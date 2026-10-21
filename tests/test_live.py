"""Opt-in network test; never runs implicitly for arbitrary profiles."""
import json
import os
import unittest
from pathlib import Path

from backend.collector.instagram import InstagramCollector
from backend.main import report
from backend.services.validation import structurally_consistent, validate_reference


@unittest.skipUnless(os.getenv("BYEFAN_AUTHORIZED_USERNAME"), "No authorized live profile configured")
class LiveTests(unittest.TestCase):
    def test_authorized_profile(self):
        username = os.environ["BYEFAN_AUTHORIZED_USERNAME"]
        first = InstagramCollector().collect(username)
        output = {"runs": [report(first)], "integrity_verified": False}
        if structurally_consistent(first):
            second = InstagramCollector().collect(username)
            reference_path = os.getenv("BYEFAN_REFERENCE")
            if reference_path:
                reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
                output["integrity_verified"] = validate_reference(first, second, reference)
            output["runs"] = [report(first), report(second)]
        Path("reports").mkdir(exist_ok=True)
        Path("reports/live-test.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        self.assertTrue(output["integrity_verified"], "Live acceptance failed; see reports/live-test.json")
