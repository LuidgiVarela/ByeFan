import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.experimental.passive_scroll_audit import (
    BatchTracker, MAX_CYCLES_PER_LIST, extract_user_batches,
)
from scripts.passive_scroll_audit import main


class FakeResponse:
    status = 200

    def __init__(self, document):
        self.document = document

    def body(self):
        return json.dumps(self.document).encode()


class FakeProbe:
    max_body_bytes = 1_000_000

    def __init__(self):
        self.pending = {"followers": [], "following": []}


class PassiveScrollTests(unittest.TestCase):
    def test_extract_nested_users_and_cursor_hint(self):
        document = {"data": {"friendships": {"users": [
            {"username": "alice", "pk_id": "10"},
            {"username": "bob", "pk": 20}], "next_max_id": "next"}}}
        batches, truncated = extract_user_batches(document)
        self.assertFalse(truncated)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["rows"], [("alice", "10"), ("bob", "20")])
        self.assertTrue(batches[0]["cursor_present"])

    def test_drain_tracks_new_ids_only_from_correlated_responses(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        probe.pending["followers"].append(FakeResponse({"users": [
            {"username": "unrelated1", "pk": "1"},
            {"username": "unrelated2", "pk": "2"}]}))
        probe.pending["followers"].append(FakeResponse({"users": [
            {"username": "alice", "pk": "10"},
            {"username": "bob", "pk": "20"},
            {"username": "carol", "pk": "30"}]}))
        events, issue = tracker.drain("followers", {"alice", "bob"})
        self.assertIsNone(issue)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_structured_ids"], 3)
        self.assertEqual(len(tracker.known["followers"]), 3)
        self.assertEqual(tracker.drain("followers", {"alice", "bob"}), ([], None))

    def test_next_page_adds_ids_and_conflicts_interrupt(self):
        probe = FakeProbe()
        tracker = BatchTracker(probe)
        probe.pending["following"].append(FakeResponse({"users": [
            {"username": "alice", "pk": "10"},
            {"username": "bob", "pk": "20"}]}))
        tracker.drain("following", {"alice", "bob"})
        probe.pending["following"].append(FakeResponse({"users": [
            {"username": "bob", "pk": "20"},
            {"username": "carol", "pk": "30"}]}))
        events, issue = tracker.drain("following", {"bob", "carol"})
        self.assertIsNone(issue)
        self.assertEqual(events[0]["new_structured_ids"], 1)
        probe.pending["following"].append(FakeResponse({"users": [
            {"username": "renamed", "pk": "20"},
            {"username": "carol", "pk": "30"}]}))
        self.assertEqual(tracker.drain("following", {"renamed", "carol"})[1],
                         "id_username_conflict")

    def test_dry_run_and_fixed_limit(self):
        self.assertEqual(MAX_CYCLES_PER_LIST, 10)
        output = io.StringIO()
        with patch("sys.argv", ["passive_scroll_audit"]), patch(
                "scripts.passive_scroll_audit.run_once",
                side_effect=AssertionError("live access")), contextlib.redirect_stdout(output):
            main()
        data = json.loads(output.getvalue())
        self.assertEqual(data["explicit_private_requests"], 0)
        self.assertEqual(data["max_cycles_per_list"], 10)


if __name__ == "__main__":
    unittest.main()
