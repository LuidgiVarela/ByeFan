"""Local HTML fixtures in Chromium; never contact Instagram."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.experimental import playwright_browser as browser


class BrowserSafetyTests(unittest.TestCase):
    def test_profile_must_be_outside_repository(self):
        with self.assertRaisesRegex(RuntimeError, "outside_repository"):
            browser.safe_profile_path(browser.ROOT / "browser-profile")

    def test_owner_confirmation_requires_matching_marker(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(browser, "CONTROL", Path(directory)):
            self.assertFalse(browser.approved("fictionalrun"))
            (Path(directory) / "confirm-fictionalrun.json").write_text('{"run_id":"other"}', encoding="utf-8")
            self.assertFalse(browser.approved("fictionalrun"))
            (Path(directory) / "confirm-fictionalrun.json").write_text('{"run_id":"fictionalrun"}', encoding="utf-8")
            self.assertTrue(browser.approved("fictionalrun"))

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_visible_ui_sample_capped_and_ids_unknown(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                links = "".join(f'<a href="/fixture_{i}/">fixture_{i}</a>' for i in range(12))
                page.set_content('''<a href="/example_target/followers/" onclick="event.preventDefault();
                    document.getElementById('people').style.display='block'">301 followers</a>
                    <div id="people" role="dialog" style="display:none">''' + links + '</div>')
                self.assertIsNotNone(browser.profile_control(page, "followers"))
                self.assertIsNone(browser.profile_control(page, "following"))
                result = browser.read_list(page, "followers")
                self.assertTrue(result["control_available"])
                self.assertTrue(result["dialog_opened"])
                self.assertEqual(result["reported_count"], 301)
                self.assertEqual(len(result["rows"]), 10)
                self.assertTrue(all(row["id"] is None for row in result["rows"]))
                self.assertFalse(result["scroll_attempted"])
            finally:
                instance.close()

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_visible_challenge_stops(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('<div>Confirm it\'s you</div>')
                with self.assertRaisesRegex(RuntimeError, "verification_or_access_limit"):
                    browser.refuse_if_gate(page)
            finally:
                instance.close()

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_profile_controls_with_hash_links(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('<a href="#">338 followers</a><a href="#">511 following</a>')
                self.assertEqual(browser.profile_control(page, "followers").inner_text(), "338 followers")
                self.assertEqual(browser.profile_control(page, "following").inner_text(), "511 following")
            finally:
                instance.close()

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_nonzero_counter_with_empty_dialog_is_not_success(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('<a href="#" onclick="document.getElementById(\'list\').style.display=\'block\'">'
                                 '338 followers</a><div id="list" role="dialog" style="display:none">'
                                 'List temporarily empty</div>')
                result = browser.read_list(page, "followers")
                self.assertTrue(result["dialog_opened"])
                self.assertEqual(result["rows"], [])
                self.assertEqual(result["error"], "empty_dialog_with_nonzero_count")
            finally:
                instance.close()


if __name__ == "__main__":
    unittest.main()
