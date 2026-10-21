import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.experimental.instagram_authenticated import AuthenticatedCollector, StopCollection, make_client
from backend.experimental.instagram_session import crypt, load_session, save_session
from backend.main import report
from backend.services.validation import validate_reference


def profile(followers=2, following=1, private=False):
    return {"status": "ok", "user": {"pk": "123", "username": "example_target", "is_private": private,
                                     "follower_count": followers, "following_count": following}}


def page(ids, cursor=None, **extra):
    return {"status": "ok", "users": [{"pk": i, "username": "fictional_" + i} for i in ids],
            "next_max_id": cursor, **extra}


class FakeClient:
    rank_token = "fictional_rank_token_not_logged"
    last_response = None

    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def private_request(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class AuthenticatedTests(unittest.TestCase):
    def test_two_pages_and_both_lists_not_automatically_complete(self):
        client = FakeClient(profile(), page(["1"], "next"), page(["2"]), page(["3"]), profile())
        result = AuthenticatedCollector(client, max_pages=2).collect()
        self.assertEqual(result.followers.recovered_count, 2)
        self.assertEqual(client.calls[2][1]["max_id"], "next")
        self.assertTrue(result.following.counts_stable)
        self.assertEqual(result.followers.state, "incomplete")
        self.assertIsNone(report(result)["non_followers"])
        self.assertNotIn(client.rank_token, json.dumps(report(result)))

    def test_local_sample_limit_still_samples_second_list(self):
        client = FakeClient(profile(), page(["1"], "next"), page(["3"]), profile())
        result = AuthenticatedCollector(client).collect()
        self.assertEqual(result.followers.reason, "page_limit")
        self.assertEqual(result.following.recovered_count, 1)

    def test_challenge_and_rate_limit_stop_second_list(self):
        for name in ("ChallengeRequired", "ClientThrottledError", "LoginRequired", "ClientConnectionError"):
            error = type(name, (Exception,), {})("secret_response")
            client = FakeClient(profile(), error)
            result = AuthenticatedCollector(client).collect()
            self.assertEqual(len(client.calls), 2)
            self.assertIsNotNone(result.error)
            self.assertNotIn("secret_response", json.dumps(report(result)))

    def test_duplicate_empty_cursor_loop_and_truncation(self):
        cases = [(page(["1", "1"]), "duplicate_identity"),
                 (page([]), "count_mismatch"),
                 (page(["1"], big_list=True), "pagination_or_access_limited")]
        for response, expected in cases:
            result = AuthenticatedCollector(FakeClient(profile(), response)).collect()
            self.assertEqual(result.error, expected)
        c = FakeClient(profile(), page(["1"], "same"), page(["2"], "same"))
        self.assertEqual(AuthenticatedCollector(c, max_pages=3).collect().error, "pagination_stalled")

    def test_changed_profile_and_private_profile(self):
        c = FakeClient(profile(), page(["1", "2"]), page(["3"]), profile(followers=3))
        self.assertEqual(AuthenticatedCollector(c).collect().error, "profile_changed")
        c = FakeClient(profile(private=True))
        self.assertEqual(AuthenticatedCollector(c).collect().error, "private_or_unknown_visibility")
        self.assertEqual(len(c.calls), 1)

    def test_reference_and_repeat_enable_existing_comparator(self):
        def collect():
            return AuthenticatedCollector(FakeClient(profile(), page(["1", "2"]), page(["3"]), profile())).collect()
        first, second = collect(), collect()
        ref = {"username": "example_target", "profile_id": "123", "followers_ids": ["1", "2"], "following_ids": ["3"]}
        self.assertTrue(validate_reference(first, second, ref))
        self.assertEqual(report(first)["non_followers"], [{"id": "3", "username": "fictional_3"}])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_roundtrip_and_no_plaintext_password_on_disk(self):
        blob = crypt(b'fictional_session_cookie')
        self.assertNotIn(b'fictional_session_cookie', blob)
        self.assertEqual(crypt(blob, decrypt=True), b'fictional_session_cookie')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.dpapi"
            with patch("backend.experimental.instagram_session.session_path", return_value=path):
                save_session({"cookies": {"sessionid": "fictional"}, "password": "never_saved"}, "secondary_fixture")
                restored = load_session()
                self.assertNotIn("password", restored["settings"])
                self.assertNotIn(b'fictional', path.read_bytes())


class DependencyTests(unittest.TestCase):
    def setUp(self):
        try:
            import instagrapi  # noqa: F401
        except ImportError:
            self.skipTest("Install requirements-instagram.txt for dependency contract tests")

    def test_no_auto_retry_challenge_or_fallback_in_installed_library(self):
        import requests
        from instagrapi.exceptions import ClientRequestTimeout, ChallengeRequired
        with patch.object(requests.Session, "request", side_effect=AssertionError("Network forbidden in test")):
            client = make_client()
            self.assertEqual(client.session_retry_total, 0)
            self.assertFalse(client.with_challenge_flow)
            for error in (ClientRequestTimeout(), ChallengeRequired()):
                with patch.object(client, "_send_private_request", side_effect=error) as call:
                    with self.assertRaises(type(error)):
                        client.private_request("test/")
                    self.assertEqual(call.call_count, 1)
            for method in (client.challenge_resolve, client.login_legacy,
                           client.bloks_caa_resolve_two_step_verification, client.public_request):
                with self.assertRaises(StopCollection):
                    method()


if __name__ == "__main__":
    unittest.main()
