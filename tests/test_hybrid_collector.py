import contextlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch

from backend.experimental.hybrid_collector import (
    assess_list, drain_and_release, validate_repeated_reports, classify_stop,
    latest_pagination_hint, settle_without_scrolling,
    collect_hybrid_list,
    stop_at_coverage,
)
from backend.experimental.passive_scroll_audit import BatchTracker, extract_user_batches
from scripts.hybrid_collector import main


def list_fixture(direction, rows):
    return {"direction": direction, "reported_count": len(rows),
            "reported_count_after": len(rows), "counts_stable": True,
            "ui_rows": [{"id": None, "username": row["username"]} for row in rows],
            "structured_rows": rows, "parse_limit_events": 0,
            "terminal_observed": True, "state": "incomplete",
            "reason": "awaiting_count_stability_check", "integrity_verified": False}


def report_fixture(run_id):
    return {"run_id": run_id, "target": "example_target", "observer": "example_observer",
            "error": None, "response_overflow": {"followers": 0, "following": 0},
            "followers": list_fixture("followers", [
                {"id": "1", "username": "alice"},
                {"id": "2", "username": "bob"}]),
            "following": list_fixture("following", [
                {"id": "2", "username": "bob"},
                {"id": "3", "username": "carol"}])}


class FakeResponse:
    status = 200

    def body(self):
        return json.dumps({"users": [{"username": "alice", "pk": "1"},
                                     {"username": "bob", "pk": "2"}]}).encode()


class FakeProbe:
    max_body_bytes = 1_000_000

    def __init__(self):
        self.pending = {"followers": [], "following": []}

    def set_phase(self, direction):
        self.phase = direction


class HybridCollectorTests(unittest.TestCase):
    def test_partial_target_stops_without_claiming_an_end(self):
        from tests.test_coverage import observed
        item = observed(range(1, 99))
        item.update(state="collecting", terminal_observed=False, integrity_verified=False)
        tracker = BatchTracker(FakeProbe())
        self.assertFalse(stop_at_coverage(item, tracker, "followers", 100))
        self.assertTrue(stop_at_coverage(item, tracker, "followers", 98))
        self.assertEqual(item["reason"], "coverage_target_reached")
        self.assertEqual(item["state"], "incomplete")
        self.assertFalse(item["terminal_observed"])
        self.assertFalse(item["integrity_verified"])

    def test_terminal_and_matching_counts_remain_unverified(self):
        item = assess_list(list_fixture("followers", [
            {"id": "1", "username": "alice"}]))
        self.assertEqual(item["state"], "terminal_observed_unverified")
        self.assertFalse(item["integrity_verified"])

    def test_missing_structured_row_rejects_equal_count_claim(self):
        item = list_fixture("followers", [{"id": "1", "username": "alice"}])
        item["ui_rows"].append({"id": None, "username": "bob"})
        item["reported_count"] = 1
        self.assertEqual(assess_list(item)["reason"], "ui_structured_mismatch")

    def test_overflow_or_parse_loss_rejects_structural_consistency(self):
        item = list_fixture("followers", [{"id": "1", "username": "alice"}])
        self.assertEqual(assess_list(item, response_overflow=1)["reason"],
                         "structured_response_loss_possible")
        item = list_fixture("followers", [{"id": "1", "username": "alice"}])
        item["parse_limit_events"] = 1
        self.assertEqual(assess_list(item)["reason"],
                         "structured_response_loss_possible")

    def test_two_reports_and_owner_reference_enable_id_comparator(self):
        first, second = report_fixture("a"), report_fixture("b")
        reference = {"source": "independent_owner_reference",
                     "username": "example_target",
                     "followers_ids": ["1", "2"], "following_ids": ["2", "3"]}
        result = validate_repeated_reports(first, second, reference)
        self.assertEqual(result["comparison_status"],
                         "verified_by_independent_reference_and_repeat")
        self.assertEqual(result["non_followers"], [{"id": "3", "username": "carol"}])
        second["following"]["structured_rows"][1]["id"] = "4"
        self.assertIsNone(validate_repeated_reports(first, second, reference)["non_followers"])

    def test_no_reference_or_same_run_never_compares(self):
        first, second = report_fixture("a"), report_fixture("a")
        reference = {"source": "independent_owner_reference",
                     "username": "example_target", "followers_ids": ["1", "2"],
                     "following_ids": ["2", "3"]}
        self.assertIsNone(validate_repeated_reports(first, second, reference)["non_followers"])
        second["run_id"] = "b"
        reference["source"] = "collected_report"
        self.assertIsNone(validate_repeated_reports(first, second, reference)["non_followers"])

    def test_response_handles_are_released_after_each_cycle(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        totals = {"followers": 0, "following": 0}
        for expected in (1, 2):
            probe.pending["followers"].append(FakeResponse())
            events, issue = drain_and_release(tracker, "followers", {"alice", "bob"},
                                              totals)
            self.assertIsNone(issue)
            self.assertEqual(events[0]["response_ordinal"], expected)
            self.assertEqual(probe.pending["followers"], [])
            self.assertEqual(totals["followers"], expected)
        self.assertEqual(len(tracker.known["followers"]), 2)

    def test_page_received_ahead_of_ui_is_deferred_then_correlated(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe, defer_unmatched=True)
        totals = {"followers": 0, "following": 0}
        probe.pending["followers"].append(FakeResponse())
        events, issue = drain_and_release(tracker, "followers", {"unrelated"}, totals)
        self.assertEqual((events, issue), ([], None))
        self.assertEqual(len(tracker.deferred["followers"]), 1)
        events, issue = drain_and_release(tracker, "followers", {"alice", "bob"}, totals)
        self.assertIsNone(issue)
        self.assertEqual(events[0]["response_ordinal"], 1)
        self.assertEqual(events[0]["new_structured_ids"], 2)
        self.assertEqual(len(tracker.deferred["followers"]), 0)
        self.assertEqual(totals["followers"], 1)

    def test_response_arriving_during_body_read_is_not_deleted(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        totals = {"followers": 0, "following": 0}
        class ArrivingResponse(FakeResponse):
            def body(self):
                probe.pending["followers"].append(type("Denied", (), {"status": 429})())
                return super().body()
        probe.pending["followers"].append(ArrivingResponse())
        events, issue = drain_and_release(tracker, "followers", {"alice", "bob"}, totals)
        self.assertIsNone(issue)
        self.assertEqual(totals["followers"], 1)
        self.assertEqual(len(probe.pending["followers"]), 1)
        _, issue = drain_and_release(tracker, "followers", {"alice", "bob"}, totals)
        self.assertEqual(issue, "network_access_limited")

    def test_one_user_page_and_missing_cursor_are_not_discarded_or_called_terminal(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        response = type("Single", (), {"status": 200, "body": lambda self:
            json.dumps({"users": [{"pk": "1", "username": "alice"}]}).encode()})()
        probe.pending["followers"].append(response)
        events, issue = tracker.drain("followers", {"alice"})
        self.assertIsNone(issue)
        self.assertEqual(events[0]["new_structured_ids"], 1)
        self.assertIsNone(events[0]["has_next_hint"])
        batch = extract_user_batches({"users": [{"pk": "1", "username": "alice"}],
                                      "next_max_id": ""})[0][0]
        self.assertFalse(batch["has_next_hint"])

    def test_bottom_with_short_count_or_next_page_is_not_terminal(self):
        item = list_fixture("following", [{"id": "1", "username": "alice"}])
        item.update(terminal_observed=False, reported_count=511, structured_events=[])
        settled = {"reason": "quiet_timeout", "loading": False,
                   "geometry": {"at_bottom": True}}
        self.assertEqual(classify_stop(item, settled, 0), "visual_end_count_mismatch")
        self.assertFalse(item["terminal_observed"])
        item["structured_events"] = [{"state": "correlated_users_batch",
                                       "response_ordinal": 2, "has_next_hint": True}]
        self.assertEqual(classify_stop(item, settled, 0), "pagination_pending_without_progress")
        self.assertFalse(item["terminal_observed"])

    def test_late_correlation_cannot_replace_latest_cursor_hint(self):
        self.assertFalse(latest_pagination_hint([
            {"state": "correlated_users_batch", "response_ordinal": 5, "has_next_hint": False},
            {"state": "correlated_users_batch", "response_ordinal": 1, "has_next_hint": True}]))

    def test_passive_settlement_recovers_late_rows_without_scrolling(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe, defer_unmatched=True)
        totals = {"followers": 0, "following": 0}
        now = [0.0]
        class Page:
            def wait_for_timeout(self, ms):
                now[0] += ms / 1000
        class Network:
            def check(self):
                pass
        baseline = {"top": 200, "height": 300, "viewport": 100, "at_bottom": True}
        def snapshot(*args, **kwargs):
            if now[0] >= 2:
                probe.pending["followers"].append(FakeResponse())
                return {"rows": [{"username": "alice"}, {"username": "bob"}],
                        "loading": False, "geometry": baseline}
            return {"rows": [], "loading": True, "geometry": baseline}
        item = {"structured_events": [], "parse_limit_events": 0}
        with patch("backend.experimental.hybrid_collector.time.monotonic", side_effect=lambda: now[0]), \
                patch("backend.experimental.hybrid_collector.refuse_if_gate"), \
                patch("backend.experimental.hybrid_collector.extract_visible_people", side_effect=snapshot):
            result = settle_without_scrolling(Page(), object(), "followers", 30,
                                               Network(), tracker, totals, {}, item, baseline)
        self.assertEqual(result["reason"], "delayed_progress")
        self.assertEqual(result["new_structured_ids"], 2)
        self.assertEqual(len(item["ui_rows"]), 2)
        self.assertEqual(result["seconds"], 2)

    @unittest.skipUnless(os.name == "nt", "Local Chromium fixture")
    def test_local_browser_traversal_keeps_end_unverified(self):
        from playwright.sync_api import sync_playwright
        from backend.experimental.playwright_integral import NetworkSummary
        probe = FakeProbe()
        users = [{"username": f"fixture_{i}", "pk": str(i + 1)} for i in range(12)]
        probe.pending["followers"].append(type("Response", (), {
            "status": 200, "body": lambda self: json.dumps({"users": users,
                                                             "next_max_id": ""}).encode()})())
        tracker = BatchTracker(probe, defer_unmatched=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                rows = "".join(f'<a href="/fixture_{i}/" style="display:block;height:30px">'
                               f'fixture_{i}</a>' for i in range(12))
                page.set_content('<a href="#">12 followers</a><div role="dialog">'
                                 '<div id="people" style="height:100px;overflow-y:auto"></div></div>')
                page.evaluate("rows => setTimeout(() => document.getElementById('people').innerHTML = rows, 2000)", rows)
                checkpoints = []
                with patch("backend.experimental.hybrid_collector.SCROLL_PAUSE_MS", 10), \
                        patch("backend.experimental.hybrid_collector.SETTLE_SECONDS", 2.5), \
                        patch("backend.experimental.hybrid_collector.SETTLE_POLL_MS", 20):
                    result = collect_hybrid_list(page, "followers", time.monotonic() + 30,
                        NetworkSummary(page), tracker, {"followers": 0, "following": 0},
                        lambda item: checkpoints.append(len(item["ui_rows"])))
                self.assertIsNone(result["error"])
                self.assertTrue(result["terminal_observed"])
                self.assertTrue(result["visual_bottom_observed"])
                self.assertEqual(len(result["structured_rows"]), 12)
                self.assertFalse(result["integrity_verified"])
                self.assertEqual(result["state"], "incomplete")
                self.assertEqual(result["settlement_events"][0]["phase"], "initial_load")
                self.assertEqual(result["settlement_events"][0]["reason"], "delayed_progress")
                self.assertEqual(result["settlement_events"][-1]["reason"], "quiet_timeout")
                self.assertGreater(len(checkpoints), 2)
            finally:
                browser.close()

    def test_same_username_two_ids_interrupts(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        probe.pending["followers"].append(FakeResponse())
        tracker.drain("followers", {"alice", "bob"})
        probe.pending["followers"].append(type("ChangedResponse", (), {
            "status": 200,
            "body": lambda self: json.dumps({"users": [
                {"username": "alice", "pk": "3"},
                {"username": "bob", "pk": "2"}]}).encode(),
        })())
        self.assertEqual(tracker.drain("followers", {"alice", "bob"})[1],
                         "id_username_conflict")

    def test_cli_defaults_to_no_network(self):
        output = io.StringIO()
        with patch("sys.argv", ["hybrid_collector"]), patch(
                "scripts.hybrid_collector.run_once",
                side_effect=AssertionError("live access")), contextlib.redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())["mode"], "dry_run")


if __name__ == "__main__":
    unittest.main()
