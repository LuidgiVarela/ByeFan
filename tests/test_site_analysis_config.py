"""Offline verification must reach the UI without trusting saved flags."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.site_preview import parse_args, load_saved_analysis
from tests.test_relationships import fixture, reference


class AnalysisConfigTests(unittest.TestCase):
    def test_incomplete_options_fail_before_starting_server(self):
        for options in (
            ['--reference','reference.json'],
            ['--repeat-report','repeat.json'],
            ['--reference','reference.json','--repeat-report','repeat.json'],
            ['--previous-report','old.json'],
            ['--previous-repeat','old-repeat.json'],
            ['--previous-reference','old-ref.json'],
            ['--previous-repeat','old-repeat.json','--previous-reference','old-ref.json'],
        ):
            with self.subTest(options=options),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                parse_args(options)
        self.assertIsNone(load_saved_analysis(parse_args([])))

    def test_local_files_unlock_only_verified_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            paths={key:root/f'{key}.json' for key in ('saved','repeat','ref')}
            for key,data in [('saved',fixture()),('repeat',fixture('repeat',2)),('ref',reference())]:
                paths[key].write_text(json.dumps(data),encoding='utf-8')
            opts=['--saved-report',str(paths['saved'])]
            self.assertIsNone(load_saved_analysis(parse_args(opts))['non_followers'])
            opts+=['--repeat-report',str(paths['repeat']),'--reference',str(paths['ref'])]
            self.assertEqual(load_saved_analysis(parse_args(opts))['non_followers'],[{'id':'3','username':'carol'}])
            bad=reference()
            bad['followers_ids']=['1','2','3']
            paths['ref'].write_text(json.dumps(bad),encoding='utf-8')
            self.assertIsNone(load_saved_analysis(parse_args(opts))['non_followers'])
