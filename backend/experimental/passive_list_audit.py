"""Passive audit of responses received during a bounded visible-UI sample.

No request is constructed by this module. It never persists response bodies,
URLs, cookies, tokens, or usernames outside the 10 visible rows per list.
"""

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from backend.experimental.playwright_browser import (
    OBSERVER, ROOT, TARGET, observer_visible, page_control_or_title_matches,
    read_list, refuse_if_gate, safe_profile_path,
)

REPORTS = ROOT / "reports" / "passive-list-audit"
MAX_RESPONSES_PER_LIST = 60
MAX_BODY_BYTES = 1_000_000
MAX_NODES = 30_000
MAX_VISIBLE_USERS = 10
HANDLE = re.compile(r"[a-z0-9_.]{1,30}")
IDS = re.compile(r"[0-9]+")
RESTRICTION_CODES = {
    "authentication_or_verification_required", "authentication_required",
    "verification_or_access_limit", "observer_session_not_confirmed_in_ui",
    "target_profile_unavailable",
}


def _candidate(node):
    if not isinstance(node, dict):
        return None
    username = node.get("username")
    if not isinstance(username, str):
        return None
    username = username.casefold()
    if not HANDLE.fullmatch(username):
        return None
    identifier = node.get("pk_id") or node.get("pk") or node.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        return None
    identifier = str(identifier)
    return (username, identifier) if IDS.fullmatch(identifier) else None


def summarize_document(document, visible_usernames, *, max_nodes=MAX_NODES):
    """Match candidate identities only to usernames already visible in the UI."""
    visible = {name.casefold() for name in visible_usernames if isinstance(name, str)}
    stack = [document]
    visited = candidates = 0
    matched = {}
    hints = set()
    while stack and visited < max_nodes:
        item = stack.pop()
        visited += 1
        if isinstance(item, dict):
            candidate = _candidate(item)
            if candidate:
                candidates += 1
                name, identifier = candidate
                if name in visible:
                    matched.setdefault(name, set()).add(identifier)
            for key in item:
                if key in ("users", "edge_followed_by", "edge_follow",
                           "xdt_api__v1__friendships__followers",
                           "xdt_api__v1__friendships__following"):
                    hints.add(key)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return {"candidate_objects": candidates,
            "matched_visible": {name: sorted(ids) for name, ids in matched.items()},
            "schema_hints": sorted(hints), "node_limit_hit": bool(stack)}


class PassiveProbe:
    def __init__(self, page, max_responses=MAX_RESPONSES_PER_LIST,
                 max_body_bytes=MAX_BODY_BYTES, *, bind_start_phase=False):
        self.phase = None
        self.pending = {"followers": [], "following": []}
        self.overflow = {"followers": 0, "following": 0}
        self.max_responses = max_responses
        self.max_body_bytes = max_body_bytes
        self.bind_start_phase = bind_start_phase
        self.started_phases = {}
        self.restriction = None
        if bind_start_phase:
            page.on("request", self.begin_request)
            page.on("requestfailed", self.failed_request)
        page.on("requestfinished", self.observe_request)

    def set_phase(self, phase):
        self.phase = phase

    def begin_request(self, request):
        if self.phase not in self.pending or request.resource_type not in ("fetch", "xhr"):
            return
        host = (urlsplit(request.url).hostname or "").lower()
        if host != "instagram.com" and not host.endswith(".instagram.com"):
            return
        if len(self.started_phases) >= 500:
            self.overflow[self.phase] += 1
            return
        self.started_phases[request] = self.phase

    def failed_request(self, request):
        self.started_phases.pop(request, None)

    def check(self):
        if self.restriction:
            raise RuntimeError(self.restriction)

    def inflight_count(self, phase):
        return sum(value == phase for value in self.started_phases.values())

    def observe_request(self, request):
        phase = self.started_phases.pop(request, None) if self.bind_start_phase else self.phase
        if phase not in self.pending or request.resource_type not in ("fetch", "xhr"):
            return
        host = (urlsplit(request.url).hostname or "").lower()
        if host != "instagram.com" and not host.endswith(".instagram.com"):
            return
        try:
            response = request.response()
        except Exception:
            return
        if response is not None and response.status in (401, 403, 429):
            self.restriction = ("network_authentication_required" if response.status == 401
                                else "network_access_limited")
        if response is None or "json" not in response.headers.get("content-type", "").lower():
            return
        if len(self.pending[phase]) >= self.max_responses:
            self.overflow[phase] += 1
        else:
            self.pending[phase].append(response)

    def summarize(self, phase, visible_rows):
        visible = {row["username"].casefold() for row in visible_rows[:MAX_VISIBLE_USERS]}
        matched = {}
        responses = []
        skipped = 0
        for ordinal, response in enumerate(self.pending[phase], 1):
            if response.status != 200:
                responses.append({"ordinal": ordinal, "http_status": response.status,
                                  "candidate_objects": 0, "matched_visible_count": 0})
                continue
            try:
                body = response.body()
                if len(body) > self.max_body_bytes:
                    skipped += 1
                    continue
                item = summarize_document(json.loads(body), visible)
            except Exception:
                skipped += 1
                continue
            for username, ids in item["matched_visible"].items():
                matched.setdefault(username, set()).update(ids)
            responses.append({"ordinal": ordinal, "http_status": response.status,
                              "candidate_objects": item["candidate_objects"],
                              "matched_visible_count": len(item["matched_visible"]),
                              "schema_hints": item["schema_hints"],
                              "node_limit_hit": item["node_limit_hit"]})
        return {"responses_observed": len(self.pending[phase]),
                "responses_over_limit": self.overflow[phase],
                "responses_skipped": skipped,
                "matched_visible_count": len(matched),
                "matched_visible": [{"username": name, "ids": sorted(ids)}
                                    for name, ids in sorted(matched.items())],
                "ambiguous_id_count": sum(len(ids) != 1 for ids in matched.values()),
                "response_summaries": responses}


def audit_ui(page):
    started = time.monotonic()
    result = {"observer": OBSERVER, "target": TARGET,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "followers": None, "following": None, "error": None,
              "elapsed_seconds": None, "integrity_verified": False}
    probe = PassiveProbe(page)
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        page.goto(f"https://www.instagram.com/{TARGET}/",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        for phase in ("followers", "following"):
            evidence = read_list(page, phase, probe=probe)
            visible_rows = evidence.get("rows", [])[:MAX_VISIBLE_USERS]
            if evidence["error"] is None:
                # Catch responses completing just after the initial dialog read.
                # This waits without sending a request or advancing the list.
                probe.set_phase(phase)
                page.wait_for_timeout(400)
                probe.set_phase(None)
                refuse_if_gate(page)
            result[phase] = {"ui": {"control_available": evidence["control_available"],
                                    "dialog_opened": evidence["dialog_opened"],
                                    "reported_count": evidence["reported_count"],
                                    "rows": visible_rows, "error": evidence["error"],
                                    "elapsed_seconds": evidence["elapsed_seconds"]},
                             "passive": probe.summarize(phase, visible_rows)}
            if evidence["error"]:
                result["error"] = evidence["error"]
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            refuse_if_gate(page)
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in RESTRICTION_CODES \
            else "navigation_or_probe_error"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def run_once():
    """Explicit live operation; caller must obtain authorization beforehand."""
    from playwright.sync_api import sync_playwright

    profile = safe_profile_path()
    if not profile.is_dir():
        raise RuntimeError("existing_browser_profile_missing")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=False, locale="en-US", viewport={"width": 390, "height": 800})
        try:
            page = context.pages[0] if context.pages else context.new_page()
            outcome = audit_ui(page)
        finally:
            context.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    output = REPORTS / f"result-{uuid.uuid4().hex}.json"
    output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, outcome
