"""Bounded anonymous probe of undocumented web endpoints, not a supported API.

No cookies, credentials, retries, proxy rotation, redirect following or fallback
after a refusal. Query hashes are documented in Instaloader's structures.py.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import Collection, ListResult, User

LOG = logging.getLogger(__name__)
BASE = "https://www.instagram.com"
QUERIES = {
    "followers": ("37479f2b8209594dde7facb0d904896a", "edge_followed_by"),
    "following": ("58712303d941c6855d4e888c5f0cd22f", "edge_follow"),
}


class CollectionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def normalize_username(value: str) -> str:
    value = value.removeprefix("@").lower()
    if not re.fullmatch(r"[a-z0-9_.]{1,30}", value):
        raise ValueError("Invalid Instagram username")
    return value


def integer(value):
    if type(value) is not int or value < 0:
        raise CollectionError("invalid_schema")
    return value


def user_from(node):
    uid, name = node["id"], node["username"]
    if (not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid)
            or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name)):
        raise CollectionError("invalid_identity")
    return User(uid, name)


def decode_response(status, headers, body):
    location = headers.get("Location", "").lower()
    lowered = body.lower()
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        payload = None
    if status == 429 or b"please wait a few minutes" in lowered:
        raise CollectionError("rate_limited")
    if any(s in location for s in ("challenge", "checkpoint")) or any(
            s in lowered for s in (b'"challenge_required"', b'"checkpoint_required"')):
        raise CollectionError("challenge_required")
    if status == 401 or "/accounts/login" in location or b'"login_required"' in lowered:
        raise CollectionError("authentication_required")
    if status == 403:
        raise CollectionError("access_denied")
    if 300 <= status < 400:
        raise CollectionError("redirect_refused")
    if isinstance(payload, dict):
        if payload.get("message") in ("User not found", "user_not_found"):
            raise CollectionError("account_not_found")
        if payload.get("message") == "useragent mismatch":
            raise CollectionError("request_client_rejected")
    # A 404 can describe an unavailable endpoint, not necessarily a missing user.
    if status == 404:
        raise CollectionError("not_found_or_endpoint_unavailable")
    if status != 200:
        raise CollectionError("http_error")
    if payload is None:
        raise CollectionError("non_json_response")
    if not isinstance(payload, dict):
        raise CollectionError("invalid_schema")
    if payload.get("status") == "fail" or payload.get("errors"):
        raise CollectionError("upstream_error")
    return payload


class AnonymousHTTP:
    def __init__(self, timeout=15, interval=1):
        self.timeout = timeout
        self.interval = interval
        self.opener = build_opener(NoRedirect())  # no CookieProcessor
        self.events = []
        self.last_request = None

    def get(self, path, params):
        if self.last_request is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        start = time.monotonic()
        event = {"endpoint": path, "status": None}
        try:
            request = Request(BASE + path + "?" + urlencode(params), headers={
                "User-Agent": "ByeFan-Feasibility/0.1", "Accept": "application/json"})
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                event["status"] = response.code
                event["content_type"] = response.headers.get("Content-Type", "")
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise CollectionError("response_too_large")
                return decode_response(response.code, response.headers, body)
        except (URLError, TimeoutError, OSError):
            event["error"] = "connection_failed"
            raise CollectionError("connection_failed") from None
        except CollectionError as exc:
            event["error"] = exc.code
            raise
        finally:
            self.last_request = time.monotonic()
            event["elapsed_seconds"] = round(self.last_request - start, 3)
            self.events.append(event)
            LOG.info("request %s", event)  # no body, query, cookies, or credentials


class InstagramCollector:
    def __init__(self, transport=None, max_pages=20):
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be between 1 and 100")
        self.http = transport or AnonymousHTTP()
        self.max_pages = max_pages

    def profile(self, username):
        data = self.http.get("/api/v1/users/web_profile_info/", {"username": username})
        try:
            node = data["data"]["user"]
            if node is None:
                raise CollectionError("profile_unavailable")
            user = user_from(node)
            if user.username.lower() != username:
                raise CollectionError("profile_identity_mismatch")
            if type(node["is_private"]) is not bool:
                raise CollectionError("invalid_schema")
            if node["is_private"]:
                raise CollectionError("private_account")
            return user.id, integer(node["edge_followed_by"]["count"]), integer(node["edge_follow"]["count"])
        except (KeyError, TypeError, IndexError):
            raise CollectionError("invalid_schema") from None

    def read_list(self, uid, kind, result):
        query, edge_key = QUERIES[kind]
        cursor, seen_cursors, seen_ids = None, set(), set()
        try:
            for _ in range(self.max_pages):
                variables = {"id": uid, "first": 24}
                if cursor:
                    variables["after"] = cursor
                payload = self.http.get("/graphql/query/", {
                    "query_hash": query, "variables": json.dumps(variables)})
                node = payload["data"]["user"]
                if node is None:
                    raise CollectionError("list_unavailable")
                if "id" in node and node["id"] != uid:
                    raise CollectionError("profile_identity_mismatch")
                edge = node[edge_key]
                if integer(edge["count"]) != result.reported_count:
                    raise CollectionError("count_changed")
                edges, page = edge["edges"], edge["page_info"]
                if not isinstance(edges, list) or type(page["has_next_page"]) is not bool:
                    raise CollectionError("invalid_schema")
                result.pages += 1
                result.state = "incomplete"
                for item in edges:
                    user = user_from(item["node"])
                    if user.id in seen_ids:
                        raise CollectionError("duplicate_identity")
                    seen_ids.add(user.id)
                    result.users.append(user)
                if not page["has_next_page"]:
                    result.terminal_page = True
                    if len(seen_ids) != result.reported_count:
                        raise CollectionError("count_mismatch")
                    result.reason = "integrity_unverified"
                    return
                cursor = page["end_cursor"]
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors or not edges:
                    raise CollectionError("pagination_stalled")
                seen_cursors.add(cursor)
            raise CollectionError("page_limit")
        except (KeyError, TypeError, IndexError):
            result.reason = "invalid_schema"
            raise CollectionError("invalid_schema") from None
        except CollectionError as exc:
            result.reason = exc.code
            raise

    def collect(self, username):
        username = normalize_username(username)
        start = time.monotonic()
        result = Collection(username, datetime.now(timezone.utc).isoformat())
        event_offset = len(self.http.events)
        try:
            initial = self.profile(username)
            result.profile_id = initial[0]
            result.followers.reported_count, result.following.reported_count = initial[1:]
            for kind in QUERIES:
                self.read_list(initial[0], kind, getattr(result, kind))
            final = self.profile(username)
            if initial != final:
                raise CollectionError("profile_changed")
            for listing in (result.followers, result.following):
                listing.counts_stable = True
        except CollectionError as exc:
            result.error = exc.code
            for listing in (result.followers, result.following):
                if listing.reason == "not_attempted":
                    listing.reason = "stopped_after_" + exc.code
        result.elapsed_seconds = round(time.monotonic() - start, 3)
        result.requests = self.http.events[event_offset:]
        return result
