"""Offline fixtures for batched DOM reading and response metadata."""
import json
import os
import unittest

from backend.experimental import playwright_browser as browser
from backend.experimental.response_probe import PageResponseProbe, candidate_profile_count


class FakePage:
    def on(self, event, callback):
        self.event = event
        self.callback = callback


class FakeResponse:
    def __init__(self, data, url="https://www.instagram.com/fixture"):
        self.url = url
        self.request = type("Request", (), {"resource_type": "fetch"})()
        self.headers = {"content-type": "application/json"}
        self.status = 200
        self.data = json.dumps(data).encode("utf-8")

    def body(self):
        return self.data


class DiagnosticsTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Local Chromium fixture")
    def test_batched_guard_preserves_visible_restrictions_and_password_checks(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                for phrase in browser.ACCESS_GATE_TEXTS:
                    page.set_content('<div>' + phrase + '</div>')
                    with self.assertRaisesRegex(RuntimeError, "verification_or_access_limit"):
                        browser.refuse_if_gate_batched(page)
                page.set_content('<div style="display:none">Try again later</div><p>Normal</p>')
                browser.refuse_if_gate_batched(page)
                page.set_content('<input type="password">')
                with self.assertRaisesRegex(RuntimeError, "authentication_required"):
                    browser.refuse_if_gate_batched(page)
            finally:
                instance.close()

    def test_terminal_requires_stable_bottom_and_no_new_users(self):
        before = {"at_bottom": True, "height": 400, "viewport": 100}
        after = {"at_bottom": True, "height": 400, "viewport": 100}
        self.assertTrue(browser.scroll_is_stably_terminal(before, after, 0))
        self.assertFalse(browser.scroll_is_stably_terminal(before, after, 1))
        self.assertFalse(browser.scroll_is_stably_terminal(before, {**after, "height": 500}, 0))
        self.assertFalse(browser.scroll_is_stably_terminal({**before, "at_bottom": False}, after, 0))

    def test_response_probe_keeps_only_bounded_schema_counts(self):
        page = FakePage()
        probe = PageResponseProbe(page, max_responses_per_list=1)
        self.assertEqual(page.event, "requestfinished")
        probe.set_phase("followers")
        first = FakeResponse({"users": [{"username": "alice", "id": "123"},
                                        {"username": "bob"}]})
        finished = type("FinishedRequest", (), {"response": lambda self: first})()
        page.callback(finished)
        probe.observe(FakeResponse({"username": "extra", "id": "456"}))
        probe.observe(FakeResponse({"username": "outside", "id": "789"},
                                   url="https://example.com/api"))
        summary = probe.summary()["followers"]
        self.assertEqual(summary["json_responses"], 1)
        self.assertEqual(summary["overflow"], 1)
        self.assertEqual(summary["candidate_profile_objects"], 1)
        self.assertNotIn("alice", json.dumps(summary))
        self.assertEqual(candidate_profile_count({"username": "alice", "pk": 123})[0], 1)

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_batched_dom_read_deduplicates_and_ignores_hidden(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('<div role="dialog">'
                                 '<a href="/Alice/" data-user-id="123">Alice</a>'
                                 '<a href="/alice/">alice duplicate</a>'
                                 '<a href="/Bob/" data-id="not-an-id">Bob</a>'
                                 '<a href="/hidden/" style="display:none">hidden</a>'
                                 '<a href="/example_target/">target</a></div>')
                snapshot = browser.extract_visible_people(page.get_by_role("dialog"))
                self.assertEqual(snapshot["anchor_count"], 5)
                self.assertEqual([row["username"] for row in snapshot["rows"]], ["Alice", "Bob"])
                self.assertEqual([row["id"] for row in snapshot["rows"]], ["123", None])
                page.set_content('<div role="dialog"><div style="height:80px;overflow-y:auto">'
                                 '<div style="height:500px"><a href="/alice/">alice</a>'
                                 '<span role="progressbar">loading</span></div></div></div>')
                snapshot = browser.extract_visible_people(page.get_by_role("dialog"),
                                                           include_geometry=True)
                self.assertEqual(snapshot["geometry"]["height"], 500)
                self.assertTrue(snapshot["loading"])
            finally:
                instance.close()

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_scroll_step_keeps_viewport_overlap(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('<div role="dialog"><div style="height:80px;overflow-y:auto">'
                                 '<div style="height:500px"></div></div></div>')
                geometry = browser.scroll_metrics_and_advance(page.get_by_role("dialog"))
                self.assertIsNotNone(geometry)
                self.assertLessEqual(geometry["after"] - geometry["before"],
                                     geometry["viewport"] * 0.75 + 1)
            finally:
                instance.close()
