import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from backend.collector.instagram import (
    AnonymousHTTP, CollectionError, InstagramCollector, decode_response, normalize_username)
from backend.main import report


def profile(private=False, count=2):
    return {"data": {"user": {"id": "123", "username": "fixture", "is_private": private,
                             "edge_followed_by": {"count": count}, "edge_follow": {"count": 1}}}}


def page(kind, ids, count, more=False, cursor=None):
    edge = "edge_followed_by" if kind == "followers" else "edge_follow"
    return {"data": {"user": {edge: {"count": count,
        "edges": [{"node": {"id": i, "username": "user" + i}} for i in ids],
        "page_info": {"has_next_page": more, "end_cursor": cursor}}}}}


class FakeHTTP:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.events = []

    def get(self, path, params):
        self.calls.append((path, params))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class CollectorTests(unittest.TestCase):
    def test_pagination_and_counts_are_not_integrity(self):
        transport = FakeHTTP(profile(), page("followers", ["1"], 2, True, "cursor"),
                             page("followers", ["2"], 2), page("following", ["3"], 1), profile())
        result = InstagramCollector(transport).collect("@fixture")
        self.assertEqual(result.followers.recovered_count, 2)
        self.assertTrue(result.followers.terminal_page)
        self.assertTrue(result.followers.counts_stable)
        self.assertEqual(result.followers.state, "incomplete")
        self.assertIsNone(report(result)["non_followers"])
        self.assertEqual(json.loads(transport.calls[2][1]["variables"])["after"], "cursor")

    def test_refusal_stops_all_requests(self):
        for code in ("authentication_required", "rate_limited", "challenge_required", "connection_failed"):
            with self.subTest(code=code):
                t = FakeHTTP(profile(), CollectionError(code))
                result = InstagramCollector(t).collect("fixture")
                self.assertEqual(len(t.calls), 2)
                self.assertEqual(result.error, code)
                self.assertEqual(result.following.reason, "stopped_after_" + code)

    def test_private_account_stops(self):
        t = FakeHTTP(profile(True))
        self.assertEqual(InstagramCollector(t).collect("fixture").error, "private_account")
        self.assertEqual(len(t.calls), 1)

    def test_empty_is_not_complete(self):
        result = InstagramCollector(FakeHTTP(profile(), page("followers", [], 2))).collect("fixture")
        self.assertEqual(result.error, "count_mismatch")
        self.assertEqual(result.followers.state, "incomplete")

    def test_pagination_loop_duplicate_and_limit(self):
        for second, expected in [(page("followers", ["2"], 2, True, "x"), "pagination_stalled"),
                                 (page("followers", ["1"], 2), "duplicate_identity")]:
            t = FakeHTTP(profile(), page("followers", ["1"], 2, True, "x"), second)
            self.assertEqual(InstagramCollector(t).collect("fixture").error, expected)
        t = FakeHTTP(profile(), page("followers", ["1"], 2, True, "x"))
        self.assertEqual(InstagramCollector(t, max_pages=1).collect("fixture").error, "page_limit")

    def test_changed_profile_and_schema(self):
        t = FakeHTTP(profile(), page("followers", ["1", "2"], 2),
                     page("following", ["3"], 1), profile(count=3))
        result = InstagramCollector(t).collect("fixture")
        self.assertEqual(result.error, "profile_changed")
        self.assertFalse(result.followers.counts_stable)
        for payload in ({}, {"data": {"user": None}}, {"data": []}):
            self.assertIsNotNone(InstagramCollector(FakeHTTP(payload)).collect("fixture").error)

    def test_http_classification(self):
        cases = [(401, {}, b"", "authentication_required"),
                 (302, {"Location": "/accounts/login/"}, b"", "authentication_required"),
                 (429, {}, b"", "rate_limited"), (403, {}, b"", "access_denied"),
                 (404, {}, b"", "not_found_or_endpoint_unavailable"),
                 (200, {}, b'{"message":"User not found"}', "account_not_found"),
                 (200, {}, b'<html>Login</html>', "non_json_response"),
                 (200, {}, b'{"message":"challenge_required"}', "challenge_required"),
                 (200, {}, b'[]', "invalid_schema")]
        for status, headers, body, code in cases:
            with self.subTest(code=code), self.assertRaises(CollectionError) as caught:
                decode_response(status, headers, body)
            self.assertEqual(caught.exception.code, code)

    def test_connection_failure_is_sanitized(self):
        http = AnonymousHTTP(interval=0)
        with patch.object(http.opener, "open", side_effect=URLError("sensitive data")):
            with self.assertRaises(CollectionError):
                http.get("/test/", {})
        self.assertNotIn("sensitive", str(http.events))

    def test_invalid_username_never_reaches_network(self):
        for value in ("../x", "a/b", "", "@@a", "a?x=1"):
            with self.assertRaises(ValueError):
                normalize_username(value)
