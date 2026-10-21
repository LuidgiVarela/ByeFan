"""Offline Stage 8 safety and checkpoint fixtures; never visit Instagram."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.experimental import playwright_integral as integral


class FakePage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback


class FakeRequest:
    url = "https://www.instagram.com/fixture"
    resource_type = "fetch"

    def response(self):
        return type("Response", (), {"status": 429})()


class IntegralTests(unittest.TestCase):
    def test_checkpoint_is_local_json_and_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            integral.save_checkpoint(path, {"target": integral.TARGET, "followers": None})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["target"], integral.TARGET)
            self.assertIn("checkpointed_at", saved)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_global_deadline_stops_before_browser_access(self):
        result = integral.collect_list(None, "followers", time.monotonic() - 1,
                                       None, None, lambda current: None)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["reason"], "global_time_limit")
        self.assertEqual(result["cycles"], [])

    def test_network_limit_stops_without_replay(self):
        page = FakePage()
        summary = integral.NetworkSummary(page)
        summary.set_phase("followers")
        page.handlers["requestfinished"](FakeRequest())
        with self.assertRaisesRegex(RuntimeError, "network_access_limited"):
            summary.check()
        self.assertEqual(summary.counts["followers"]["http_429"], 1)

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_terminal_observation_still_unverified_and_checkpoints(self):
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
                network = integral.NetworkSummary(page)
                class Probe:
                    phase = None

                    def set_phase(self, value):
                        self.phase = value
                probe = Probe()
                checkpoints = []
                result = integral.collect_list(page, "followers", time.monotonic() + 30,
                                               network, probe,
                                               lambda current: checkpoints.append(current["recovered_count"]))
                self.assertEqual(result["state"], "terminal_observed_unverified")
                self.assertEqual(result["recovered_count"], 12)
                self.assertTrue(result["terminal_observed"])
                self.assertGreater(len(checkpoints), 1)
                self.assertIsNone(probe.phase)
            finally:
                instance.close()
