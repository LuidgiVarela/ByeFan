"""Authenticated collector. Importing this module does not import instagrapi or use the network."""
import logging
import re
import time
from datetime import datetime, timezone
from importlib.metadata import version

from backend.collector.models import Collection, User
from backend.identity import TARGET

class StopCollection(Exception):
    pass


def error_code(exc):
    if isinstance(exc, StopCollection):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "session_or_reference_file_missing"
    if isinstance(exc, RuntimeError) and str(exc) in {
            "windows_dpapi_required", "session_must_be_outside_repository",
            "session_decryption_failed", "session_encryption_failed",
            "secondary_account_required", "session_identity_mismatch", "login_failed"}:
        return str(exc)
    name = type(exc).__name__
    if "Challenge" in name or "Captcha" in name or "Checkpoint" in name:
        return "challenge_required_stop"
    if name in {"PleaseWaitFewMinutes", "ClientThrottledError", "RateLimitError", "FeedbackRequired"}:
        return "access_limited_stop"
    if name in {"LoginRequired", "ClientUnauthorizedError", "BadPassword", "BadCredentials"}:
        return "authentication_required"
    if name == "TwoFactorRequired":
        return "two_factor_required"
    if name == "UserNotFound":
        return "account_not_found"
    if any(part in name for part in ("Timeout", "Connection", "IncompleteRead")):
        return "connection_failed"
    return "upstream_or_schema_error"


def make_client(settings=None):
    if version("instagrapi") != "3.0.26":
        raise StopCollection("unsupported_instagrapi_version")
    from instagrapi import Client

    class ConservativeClient(Client):
        def private_request(self, endpoint, data=None, params=None, login=False,
                            with_signature=True, headers=None, extra_sig=None, domain=None):
            # Replace the retry/challenge wrapper, retaining the library transport.
            headers = dict(headers or {})
            if self.authorization:
                headers["Authorization"] = self.authorization
            self.private_requests_count += 1
            self._send_private_request(endpoint, data=data, params=params, login=login,
                                       with_signature=with_signature, headers=headers,
                                       extra_sig=extra_sig, domain=domain)
            return self.last_json

        def challenge_resolve(self, *args, **kwargs):
            raise StopCollection("challenge_required_stop")

        def bloks_caa_resolve_two_step_verification(self, *args, **kwargs):
            raise StopCollection("profile_verification_required_stop")

        def login_legacy(self, *args, **kwargs):
            raise StopCollection("alternate_login_flow_refused")

        def public_request(self, *args, **kwargs):
            raise StopCollection("public_fallback_refused")

        def login_flow(self):
            # Avoid optional post-login timeline and unrelated feed requests.
            return True

    # Library debug messages can include HTTP bodies; disable them in this CLI.
    logging.disable(logging.CRITICAL)
    client = ConservativeClient(session_retry_total=0, private_transport="requests",
                                public_transport="requests")
    if settings is not None:
        client.set_settings(settings)
    client.with_challenge_flow = False
    client.handle_exception = None
    client.set_retry_config(session_retry_total=0, session_retry_statuses=[],
                            public_request_retries_count=0, request_timeout=2,
                            private_transport="requests", public_transport="requests")
    # requests transport has no timeout by default in this dependency's call site.
    for session in (client.private, client.public):
        session.trust_env = False
        session.proxies.clear()
        original = session.request
        def bounded_request(method, url, _request=original, **kwargs):
            kwargs["timeout"] = (10, 20)
            kwargs["allow_redirects"] = False
            response = _request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                raise StopCollection("redirect_refused")
            return response
        session.request = bounded_request
    return client


def identity(node):
    uid, name = node["pk"], node["username"]
    if type(uid) is int and uid > 0:
        uid = str(uid)
    if (not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid)
            or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name)):
        raise StopCollection("invalid_identity")
    return User(uid, name)


class AuthenticatedCollector:
    def __init__(self, client, max_pages=1, page_size=10):
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be 1..100")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size must be 1..100")
        self.client, self.max_pages, self.page_size = client, max_pages, page_size

    def collect(self):
        started = time.monotonic()
        result = Collection(TARGET, datetime.now(timezone.utc).isoformat())

        def request(endpoint, label, params=None):
            start = time.monotonic()
            event = {"operation": label}
            try:
                payload = self.client.private_request(endpoint, params=params)
                if not isinstance(payload, dict) or payload.get("status") != "ok":
                    raise StopCollection("upstream_status_not_ok")
                return payload
            except Exception as exc:
                event["error"] = error_code(exc)
                raise
            finally:
                response = getattr(self.client, "last_response", None)
                event["http_status"] = getattr(response, "status_code", None)
                event["elapsed_seconds"] = round(time.monotonic() - start, 3)
                result.requests.append(event)

        def profile():
            node = request(f"users/{TARGET}/usernameinfo/", "profile")["user"]
            user = identity(node)
            if user.username.lower() != TARGET:
                raise StopCollection("profile_identity_mismatch")
            if node.get("is_private") is not False:
                raise StopCollection("private_or_unknown_visibility")
            counts = [node["follower_count"], node["following_count"]]
            if any(type(n) is not int or n < 0 for n in counts):
                raise StopCollection("invalid_counts")
            return user.id, *counts

        active = None
        try:
            initial = profile()
            result.profile_id = initial[0]
            result.followers.reported_count, result.following.reported_count = initial[1:]
            for kind in ("followers", "following"):
                active = getattr(result, kind)
                ids, cursors, cursor = set(), set(), None
                for _ in range(self.max_pages):
                    params = {"count": self.page_size, "rank_token": self.client.rank_token,
                              "search_surface": "follow_list_page", "query": "", "enable_groups": "true"}
                    if cursor:
                        params["max_id"] = cursor
                    payload = request(f"friendships/{initial[0]}/{kind}/", kind, params)
                    rows = payload["users"]
                    if not isinstance(rows, list):
                        raise StopCollection("invalid_schema")
                    active.pages += 1
                    active.state = "incomplete"
                    for row in rows:
                        user = identity(row)
                        if user.id in ids:
                            raise StopCollection("duplicate_identity")
                        ids.add(user.id)
                        active.users.append(user)
                    cursor = payload.get("next_max_id")
                    if not cursor:
                        if any(payload.get(flag) is True for flag in ("big_list", "has_more", "more_available", "is_partial")):
                            raise StopCollection("pagination_or_access_limited")
                        active.terminal_page = True
                        if len(ids) != active.reported_count:
                            raise StopCollection("count_mismatch")
                        active.reason = "integrity_unverified"
                        break
                    if not isinstance(cursor, str) or cursor in cursors or not rows:
                        raise StopCollection("pagination_stalled")
                    cursors.add(cursor)
                else:
                    # Local sampling limit is not an upstream refusal: sample both lists.
                    active.reason = "page_limit"
            active = None
            if profile() != initial:
                raise StopCollection("profile_changed")
            result.followers.counts_stable = result.following.counts_stable = True
        except Exception as exc:
            result.error = error_code(exc)
            if active is not None:
                active.reason = result.error
            for listing in (result.followers, result.following):
                if listing.reason == "not_attempted":
                    listing.reason = "stopped_after_" + result.error
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        return result
