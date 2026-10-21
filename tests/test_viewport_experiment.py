import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.experimental.hybrid_collector import run_once
from scripts import hybrid_collector
from scripts.benchmark_viewport import measure


class ViewportTests(unittest.TestCase):
    def test_dry_run_supports_explicit_height_without_browser(self):
        stream = io.StringIO()
        with patch('sys.argv', ['hybrid_collector', '--viewport-height', '1200']), \
                patch('scripts.hybrid_collector.run_once', side_effect=AssertionError('no live call')), \
                contextlib.redirect_stdout(stream):
            hybrid_collector.main()
        data = json.loads(stream.getvalue())
        self.assertEqual(data['viewport'], {'width': 390, 'height': 1200})
        self.assertEqual(data['minimum_scroll_pause_ms'], 1100)

    def test_bad_height_rejected_before_browser_or_files(self):
        for height in (799, 2000, True, '1200'):
            with self.subTest(height=height), self.assertRaises(ValueError):
                run_once(viewport_height=height)

    def test_virtualized_paginated_fixture_preserves_all_ids(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.route('**/*', lambda route: route.abort())
                page = context.new_page()
                normal = measure(page, 800)
                taller = measure(page, 1200)
                self.assertTrue(normal['fixture_integrity_verified'])
                self.assertTrue(taller['fixture_integrity_verified'])
                self.assertLess(taller['cycles'], normal['cycles'])
            finally:
                browser.close()
