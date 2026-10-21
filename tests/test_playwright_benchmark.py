"""Offline timing probe fixtures; never access Instagram."""
import os
import unittest

from backend.experimental import playwright_benchmark as benchmark


class BenchmarkTests(unittest.TestCase):
    def test_hard_cycle_limit_rejects_expansion(self):
        with self.assertRaisesRegex(ValueError, "cycle_limit_exceeded"):
            benchmark.benchmark_list(None, "followers", max_cycles=11)
        with self.assertRaisesRegex(ValueError, "cycle_limit_exceeded"):
            benchmark.benchmark_list(None, "following", max_cycles=0)

    def test_intermediate_limit_and_load_event_counter(self):
        with self.assertRaisesRegex(ValueError, "cycle_limit_exceeded"):
            benchmark.benchmark_list(None, "followers", max_cycles=41, ceiling=40)
        with self.assertRaisesRegex(ValueError, "cycle_limit_exceeded"):
            benchmark.run_benchmark(max_cycles=41)
        class Page:
            handlers = {}

            def on(self, event, callback):
                self.handlers[event] = callback

        page = Page()
        events = benchmark.LoadEventCounter(page)
        events.set_phase("followers")
        request = type("Request", (), {"url": "https://www.instagram.com/fixture",
                                       "resource_type": "fetch"})()
        page.handlers["requestfinished"](request)
        page.handlers["requestfailed"](request)
        self.assertEqual(events.snapshot("followers"), (1, 1))
        events.set_phase(None)
        page.handlers["requestfinished"](request)
        self.assertEqual(events.snapshot("followers"), (1, 1))

    @unittest.skipUnless(os.name == "nt", "Chromium fixture available on this Windows workspace")
    def test_cycle_metrics_and_new_users(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True)
            try:
                page = instance.new_page()
                page.set_content('''
                  <a href="#" onclick="document.getElementById('list').style.display='block'">
                    12 followers</a>
                  <div id="list" role="dialog" style="display:none">
                    <div style="height:100px;overflow-y:auto"
                         onscroll="if (!this.dataset.added) {
                           this.dataset.added='yes';
                           document.getElementById('people').insertAdjacentHTML(
                             'beforeend', '<a href=&quot;/person_3/&quot;>person_3</a>');
                         }">
                      <div id="people">
                        <a href="/person_0/">person_0</a>
                        <a href="/person_1/">person_1</a>
                        <a href="/person_2/">person_2</a>
                      </div>
                      <div style="height:800px"></div>
                    </div>
                  </div>''')
                result = benchmark.benchmark_list(page, "followers", max_cycles=3)
                self.assertEqual(result["initial_usernames"], 3)
                self.assertEqual(len(result["cycles"]), 3)
                self.assertEqual(result["recovered_count"], 4)
                self.assertEqual(result["cycles"][0]["new_usernames"], 1)
                for cycle in result["cycles"]:
                    self.assertGreaterEqual(cycle["total_seconds"], cycle["wait_seconds"])
                    self.assertIn("scroll_height_after_px", cycle)
                    self.assertIn("data_progress", cycle)
            finally:
                instance.close()
