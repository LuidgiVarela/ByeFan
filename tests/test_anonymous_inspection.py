import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.experimental import anonymous_inspection as audit
from scripts.anonymous_inspection import main


class Request:
    def __init__(self, body, content_type="application/json", status=200,
                 resource_type="fetch", url="https://www.instagram.com/fixture?secret=never-save"):
        self.url = url
        self.resource_type = resource_type
        self.result = Response(self, body, content_type, status)

    def response(self):
        return self.result


class Response:
    def __init__(self, request, body, content_type, status):
        self.request = request
        self.payload = body.encode()
        self.content_type = content_type
        self.status = status

    def header_value(self, name):
        return self.content_type if name == "content-type" else str(len(self.payload))

    def body(self):
        return self.payload


class AnonymousInspectionTests(unittest.TestCase):
    def probe(self):
        return audit.AnonymousProbe(MagicMock())

    def emit(self, probe, request):
        probe.begin(request)
        probe.headers(request.response())
        probe.finish(request)

    def test_initial_html_json_and_target_edges_without_names_or_credentials(self):
        probe = self.probe()
        data = {"username": audit.TARGET, "id": "17", "token": "never-save-token",
                "edge_followed_by": {"count": 2, "edges": [
                    {"node": {"username": "private_fixture", "id": "18"}}],
                    "page_info": {"has_next_page": True, "end_cursor": "never-save-cursor"}}}
        html = '<script type="application/json">' + json.dumps(data) + '</script><script>window.x=1</script>'
        self.emit(probe, Request(html, "text/html", resource_type="document"))
        report = probe.report()
        item = report["responses"][0]
        self.assertEqual(item["start_phase"], "initial")
        self.assertEqual(item["parsed_documents"], 1)
        self.assertEqual(item["candidate_objects"], 2)
        self.assertEqual(item["target_edges"][0]["edge_entries"], 1)
        self.assertTrue(item["target_edges"][0]["has_next_page"])
        serialized = json.dumps(report)
        for secret in ("private_fixture", "never-save", "https://"):
            self.assertNotIn(secret, serialized)

    def test_wrong_mime_prefixed_json_and_late_response_keep_start_phase(self):
        probe = self.probe()
        request = Request('for (;;);{"users":[{"username":"fixture","pk_id":"123"}]}', "text/plain")
        probe.begin(request)
        probe.phase = "following"
        probe.finish(request)
        item = probe.report()["responses"][0]
        self.assertEqual(item["start_phase"], "initial")
        self.assertEqual(item["finish_phase"], "following")
        self.assertEqual(item["candidate_objects"], 1)
        self.assertEqual(probe.identities["fixture"], {"123"})

    def test_ndjson_and_no_javascript_execution(self):
        self.assertEqual(audit.decode_json('{"a":1}\n{"b":2}'), [{"a": 1}, {"b": 2}])
        self.assertEqual(audit.decode_json('window.x = {"username":"fixture"};'), [])
        self.assertEqual(audit.decode_json(")]}'\n{\"a\":1}"), [{"a": 1}])

    def test_schema_candidates_do_not_establish_target_membership(self):
        summary, identities = audit.inspect_document({"username": "someone_else", "pk": 1,
                                                     "edge_follow": {"count": 50, "edges": []}})
        self.assertEqual(summary["target_edges"], [])
        self.assertEqual(summary["candidate_objects"], 1)
        self.assertIn("someone_else", identities)

    def test_http_restriction_is_detected_before_body_finishes(self):
        for status in (401, 403, 429):
            probe = self.probe()
            request = Request("", status=status)
            probe.begin(request)
            probe.headers(request.response())
            with self.assertRaisesRegex(RuntimeError, "network_access_restricted"):
                probe.check()
            self.assertEqual(probe.report()["responses"], [])

    def test_structured_restriction_and_failed_request(self):
        probe = self.probe()
        self.emit(probe, Request('{"message":"challenge_required"}'))
        with self.assertRaisesRegex(RuntimeError, "structured_access_restricted"):
            probe.check()
        request = Request("{}")
        probe.begin(request)
        probe.fail(request)
        self.assertEqual(probe.report()["failed"], 1)
        self.assertEqual(probe.report()["pending"], 0)

    def test_external_origin_and_unknown_start_ignored(self):
        probe = self.probe()
        self.emit(probe, Request('{"username":"secret"}', url="https://instagram.com.example.org/"))
        probe.finish(Request("{}"))
        self.assertEqual(probe.report()["responses"], [])

    def test_budget_and_bundle_inventory_cannot_hide_restriction(self):
        probe = self.probe()
        with patch.object(audit, "MAX_RESPONSES", 1):
            for _ in range(3):
                self.emit(probe, Request("window.x=1", "text/javascript", resource_type="script"))
            self.emit(probe, Request("{}"))
            self.emit(probe, Request("{}", status=429))
        self.assertEqual(probe.report()["script_responses_not_parsed"], 3)
        self.assertEqual(probe.report()["over_limit"], 1)
        with self.assertRaises(RuntimeError):
            probe.check()

    def test_body_limit_and_traversal_limit(self):
        probe = self.probe()
        with patch.object(audit, "MAX_BODY", 2):
            self.emit(probe, Request('{"secret": "do not store"}'))
        self.assertEqual(probe.report()["responses"][0]["body_status"], "oversized_header")
        with patch.object(audit, "MAX_NODES", 1):
            summary, _ = audit.inspect_document({"nested": {"username": "fixture", "id": 1}})
        self.assertTrue(summary["node_limit_hit"])

    def test_gate_stops_before_any_list_action(self):
        page = MagicMock()
        with patch.object(audit, "wait_checked", side_effect=RuntimeError("authentication_required")):
            result = audit.collect(page)
        self.assertEqual(result["error"], "authentication_required")
        self.assertIsNone(result["followers"])
        self.assertIsNone(result["following"])
        self.assertIsNone(result["non_followers"])
        self.assertFalse(result["integrity_verified"])
        self.assertEqual(page.goto.call_count, 1)
        page.keyboard.press.assert_not_called()
        events = [call.args[0] for call in page.on.call_args_list]
        self.assertIn("request", events)
        self.assertIn("response", events)

    def test_ui_sample_cap_and_no_extra_scroll(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 1
        page.get_by_role.return_value.first.is_visible.return_value = True
        control = MagicMock()
        control.inner_text.return_value = "100 followers"
        snapshot = {"rows": [{"username": f"fixture{i}", "id": None} for i in range(20)],
                    "geometry": {"top": 0, "height": 200, "viewport": 100}, "loading": False}
        with patch.object(audit, "wait_checked"), patch.object(audit, "check"), \
                patch.object(audit, "classify_opened_dialog", return_value={"verification_text": False, "password_input": False, "login_text": False, "expected_list_heading": True}), \
                patch.object(audit, "page_control_or_title_matches", return_value=True), \
                patch.object(audit, "profile_control", return_value=control), \
                patch.object(audit, "extract_visible_people", return_value=snapshot), \
                patch.object(audit, "scroll_metrics_and_advance") as scroll:
            result = audit.collect(page)
        scroll.assert_not_called()
        for direction in ("followers", "following"):
            self.assertEqual(len(result[direction]["rows"]), 10)
            self.assertEqual(result[direction]["state"], "incomplete")

    def test_empty_dialog_never_declared_complete(self):
        page = MagicMock()
        page.get_by_role.return_value.count.return_value = 1
        control = MagicMock()
        control.inner_text.return_value = "12 followers"
        with patch.object(audit, "wait_checked"), patch.object(audit, "check"), \
                patch.object(audit, "classify_opened_dialog", return_value={"verification_text": False, "password_input": False, "login_text": False, "expected_list_heading": True}), \
                patch.object(audit, "page_control_or_title_matches", return_value=True), \
                patch.object(audit, "profile_control", return_value=control), \
                patch.object(audit, "extract_visible_people", return_value={"rows": [], "geometry": None, "loading": False}):
            result = audit.collect(page)
        self.assertEqual(result["followers"]["reason"], "empty_dialog")
        self.assertFalse(result["integrity_verified"])
        self.assertEqual(result["outcome"], "no_list_records")

    def test_cli_defaults_to_offline(self):
        output = io.StringIO()
        with patch("sys.argv", ["anonymous_inspection"]), patch(
                "scripts.anonymous_inspection.run_once", side_effect=AssertionError("live call")), \
                contextlib.redirect_stdout(output):
            main()
        self.assertFalse(json.loads(output.getvalue())["network_access"])

    def test_browser_configuration_is_explicit_isolated_and_no_fallback(self):
        launch, context = audit.browser_configuration("chrome", "desktop")
        self.assertEqual(launch, {"headless": False, "channel": "chrome"})
        self.assertEqual(context, {"locale": "en-US", "viewport": {"width": 1365, "height": 900}})
        legacy_launch, legacy_context = audit.browser_configuration()
        self.assertEqual(legacy_launch, {"headless": False})
        self.assertEqual(legacy_context["viewport"]["width"], 390)
        with self.assertRaises(ValueError):
            audit.browser_configuration("unknown", "desktop")

    def test_selected_browser_plan_stays_offline(self):
        output = io.StringIO()
        with patch("sys.argv", ["anonymous_inspection", "--browser", "chrome", "--layout", "desktop"]), \
                patch("scripts.anonymous_inspection.run_once", side_effect=AssertionError("live call")), \
                contextlib.redirect_stdout(output):
            main()
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["browser"], "chrome")
        self.assertEqual(plan["viewport"]["width"], 1365)
        self.assertFalse(plan["network_access"])

    def test_click_failure_keeps_operation_but_no_exception_details(self):
        page = MagicMock()
        control = MagicMock()
        control.inner_text.return_value = "335 followers"
        control.click.side_effect = ValueError("secret credential in raw exception")
        with patch.object(audit, "wait_checked"), patch.object(audit, "check"), \
                patch.object(audit, "page_control_or_title_matches", return_value=True), \
                patch.object(audit, "profile_control", return_value=control):
            result = audit.collect(page)
        self.assertEqual(result["failed_operation"], "click_followers_control")
        self.assertEqual(result["followers"]["reason"], "operation_interrupted")
        self.assertEqual(result["error"], "local_operation_error")
        self.assertNotIn("secret", json.dumps(result))
        self.assertIsNone(result["following"])

    def test_click_hints_never_copy_sensitive_call_log(self):
        exc = RuntimeError('https://private.example/?token=SECRET <div>SECRET</div> intercepts pointer events; element is not enabled')
        hints = audit.click_failure_hints(exc)
        self.assertEqual(hints, ["pointer_intercepted", "disabled"])
        self.assertNotIn("SECRET", json.dumps(hints))

    def test_restriction_on_error_path_overrides_generic_failure(self):
        page = MagicMock()
        page.goto.side_effect = ValueError("do not log")
        with patch.object(audit, "check", side_effect=RuntimeError("network_access_restricted")):
            result = audit.collect(page)
        self.assertEqual(result["error"], "network_access_restricted")

    def test_consumed_receipt_prevents_browser_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(audit, "ROOT", Path(tmp)):
            directory = Path(tmp) / "reports" / "anonymous-inspection"
            directory.mkdir(parents=True)
            (directory / "attempt-consumed.json").write_text("{}", encoding="utf-8")
            with patch("playwright.sync_api.sync_playwright", side_effect=AssertionError("browser")):
                with self.assertRaises(FileExistsError):
                    audit.run_once("consumed")


if __name__ == "__main__":
    unittest.main()
