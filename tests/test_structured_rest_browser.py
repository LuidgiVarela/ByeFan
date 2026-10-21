import contextlib
import io
import json
import unittest
from unittest.mock import patch

from backend.experimental.structured_rest_browser import (
    FETCH_ONE, _check_profile, _sanitize_list,
)
from scripts.structured_rest_sample import main


def reply(users, cursor="next"):
    return {"status": "ok", "http_status": 200, "server_rows": len(users),
            "next_max_id": cursor, "users": users}


class StructuredRestBrowserTests(unittest.TestCase):
    def test_dry_run_does_not_start_browser(self):
        output = io.StringIO()
        with patch("sys.argv", ["structured_rest_sample"]), patch(
                "scripts.structured_rest_sample.run_once",
                side_effect=AssertionError("live call")), contextlib.redirect_stdout(output):
            main()
        self.assertFalse(json.loads(output.getvalue())["live_access"])

    def test_profile_must_match_public_authorized_target(self):
        valid = {"profile": {"id": "123", "username": "example_target",
                             "is_private": False}}
        self.assertEqual(_check_profile(valid)[0], "123")
        with self.assertRaises(ValueError):
            _check_profile({"profile": {**valid["profile"], "username": "other"}})
        with self.assertRaises(ValueError):
            _check_profile({"profile": {**valid["profile"], "is_private": True}})

    def test_valid_sample_keeps_ids_cursor_and_prior_overlap(self):
        raw = reply([{"pk_id": "10", "username": "ALICE"},
                     {"pk_id": "20", "username": "bob"}])
        with patch("backend.experimental.structured_rest_browser.prior_playwright_usernames",
                   return_value={"alice"}):
            result = _sanitize_list(raw, "followers")
        self.assertEqual(result["state"], "sample_only")
        self.assertEqual(result["next_cursor"], "next")
        self.assertEqual([(row["id"], row["username"]) for row in result["rows"]],
                         [("10", "alice"), ("20", "bob")])
        self.assertEqual(result["prior_playwright_overlap"], 1)

    def test_server_oversize_discards_all_rows(self):
        raw = reply([{"pk_id": str(n), "username": f"u{n}"} for n in range(11)])
        self.assertEqual(_sanitize_list(raw, "following")["rows"], [])
        self.assertEqual(_sanitize_list(raw, "following")["state"], "oversized_response")

    def test_access_error_and_invalid_id_stop(self):
        self.assertEqual(_sanitize_list({"error": "access_restricted", "http_status": 429},
                                        "followers")["state"], "inaccessible")
        self.assertEqual(_sanitize_list(reply([{"username": "alice"}]),
                                        "followers")["state"], "invalid")

    def test_browser_supplies_own_session_without_cookie_extraction(self):
        self.assertIn("credentials: 'include'", FETCH_ONE)
        self.assertIn("count=10", FETCH_ONE)
        self.assertNotIn("document.cookie", FETCH_ONE)
        self.assertNotIn("localStorage", FETCH_ONE)


if __name__ == "__main__":
    unittest.main()
