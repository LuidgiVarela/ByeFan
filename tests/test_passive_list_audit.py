import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.experimental.passive_list_audit import PassiveProbe, summarize_document
from scripts.passive_list_audit import main


class FakePage:
    def on(self, event, callback):
        self.event = event
        self.callback = callback


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.payload = json.dumps(payload).encode()

    def body(self):
        return self.payload


class FakeRequest:
    def __init__(self, response, url="https://www.instagram.com/fixture"):
        self.resource_type = "fetch"
        self.url = url
        self._response = response

    def response(self):
        return self._response


class PassiveListAuditTests(unittest.TestCase):
    def test_response_is_bound_to_start_phase_even_when_it_arrives_late(self):
        probe = PassiveProbe(FakePage(), bind_start_phase=True)
        request = FakeRequest(FakeResponse({"users": []}))
        probe.set_phase("followers")
        probe.begin_request(request)
        probe.set_phase("following")
        probe.observe_request(request)
        self.assertEqual(len(probe.pending["followers"]), 1)
        self.assertEqual(probe.pending["following"], [])
        self.assertEqual(probe.inflight_count("followers"), 0)

    def test_unknown_start_is_discarded_and_late_restriction_still_stops(self):
        probe = PassiveProbe(FakePage(), bind_start_phase=True)
        probe.set_phase("followers")
        probe.observe_request(FakeRequest(FakeResponse({"users": []})))
        self.assertEqual(probe.pending["followers"], [])
        request = FakeRequest(FakeResponse({}, status=429))
        probe.begin_request(request)
        probe.set_phase(None)
        probe.observe_request(request)
        with self.assertRaisesRegex(RuntimeError, "network_access_limited"):
            probe.check()

    def test_failed_request_releases_start_phase(self):
        probe = PassiveProbe(FakePage(), bind_start_phase=True)
        probe.set_phase("followers")
        request = FakeRequest(FakeResponse({}))
        probe.begin_request(request)
        probe.failed_request(request)
        self.assertEqual(probe.inflight_count("followers"), 0)

    def test_candidate_matches_only_visible_handles_and_keeps_ids(self):
        document = {"data": {"edge_followed_by": {"users": [
            {"username": "Alice", "pk_id": "123"},
            {"username": "other", "pk": 456},
            {"username": "bob", "id": "789"},
        ]}}}
        item = summarize_document(document, {"alice", "bob"})
        self.assertEqual(item["candidate_objects"], 3)
        self.assertEqual(item["matched_visible"], {"alice": ["123"], "bob": ["789"]})
        self.assertIn("edge_followed_by", item["schema_hints"])

    def test_probe_filters_origin_and_does_not_persist_unknown_users(self):
        page = FakePage()
        probe = PassiveProbe(page, max_responses=1)
        self.assertEqual(page.event, "requestfinished")
        probe.set_phase("followers")
        first = FakeRequest(FakeResponse({"users": [
            {"username": "alice", "pk": "1"},
            {"username": "secret_other", "pk": "2"}]}))
        page.callback(first)
        page.callback(FakeRequest(FakeResponse({"username": "outside", "pk": "3"}),
                                  url="https://example.com/test"))
        page.callback(FakeRequest(FakeResponse({"username": "alice", "pk": "1"})))
        report = probe.summarize("followers", [{"username": "Alice"}])
        self.assertEqual(report["responses_observed"], 1)
        self.assertEqual(report["responses_over_limit"], 1)
        self.assertEqual(report["matched_visible"], [{"username": "alice", "ids": ["1"]}])
        self.assertNotIn("secret_other", json.dumps(report))
        self.assertNotIn("https://", json.dumps(report))

    def test_ambiguous_ids_and_node_budget(self):
        page = FakePage()
        probe = PassiveProbe(page)
        probe.set_phase("following")
        page.callback(FakeRequest(FakeResponse({"users": [
            {"username": "alice", "pk": "1"},
            {"username": "alice", "pk": "2"}]})))
        report = probe.summarize("following", [{"username": "alice"}])
        self.assertEqual(report["ambiguous_id_count"], 1)
        self.assertTrue(summarize_document({"users": [{"username": "alice", "pk": "1"}]},
                                            {"alice"}, max_nodes=1)["node_limit_hit"])

    def test_dry_run_never_starts_browser(self):
        output = io.StringIO()
        with patch("sys.argv", ["passive_list_audit"]), patch(
                "scripts.passive_list_audit.run_once",
                side_effect=AssertionError("live access")), contextlib.redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())["additional_requests"], 0)


if __name__ == "__main__":
    unittest.main()
