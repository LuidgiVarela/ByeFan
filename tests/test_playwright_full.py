"""Offline Stage 4 fixtures; never access Instagram."""
import os
import unittest

from backend.experimental import playwright_browser as browser


class FullListTests(unittest.TestCase):
    def test_username_comparison_is_experimental_and_normalized(self):
        followers = [{"username": "@Alice"}, {"username": "bob"}]
        following = [{"username": "alice"}, {"username": "Carol"}]
        self.assertEqual(browser.compare_usernames_experimental(followers, following), ["carol"])
        with self.assertRaises(ValueError):
            browser.normalized_username("not a handle")

    def test_reference_requires_exact_independent_sets(self):
        result = {
            "followers": {"state": "terminal_observed_unverified", "rows": [{"username": "Alice"}]},
            "following": {"state": "terminal_observed_unverified", "rows": [{"username": "bob"}]},
        }
        reference = {"username": browser.TARGET, "followers_usernames": ["@alice"],
                     "following_usernames": ["Bob"]}
        self.assertTrue(browser.validate_username_reference(result, reference))
        reference["following_usernames"] = ["different"]
        self.assertFalse(browser.validate_username_reference(result, reference))
        result["followers"]["state"] = "incomplete"
        self.assertFalse(browser.validate_username_reference(result, reference))

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_visible_scroll_end_and_count_mismatch(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                people = "".join(f'<a style="display:block;height:30px" href="/person_{i}/">person_{i}</a>'
                                 for i in range(12))
                page.set_content('<a href="#" onclick="document.getElementById(\'list\').style.display=\'block\'">'
                                 '12 followers</a><div id="list" role="dialog" style="display:none">'
                                 '<div style="height:100px;overflow-y:auto">' + people + '</div></div>')
                first = browser.read_full_list(page, "followers", max_cycles=20, max_seconds=30)
                self.assertEqual(first["state"], "terminal_observed_unverified")
                self.assertEqual(len(first["rows"]), 12)
                self.assertTrue(first["terminal_observed"])
                self.assertGreater(first["scroll_cycles"], 0)
                page.keyboard.press("Escape")
                page.set_content('<a href="#" onclick="document.getElementById(\'list\').style.display=\'block\'">'
                                 '13 followers</a><div id="list" role="dialog" style="display:none">'
                                 '<div style="height:100px;overflow-y:auto">' + people + '</div></div>')
                second = browser.read_full_list(page, "followers", max_cycles=20, max_seconds=30)
                self.assertEqual(second["state"], "incomplete")
                self.assertEqual(second["reason"], "ui_end_count_mismatch")
            finally:
                instance.close()
