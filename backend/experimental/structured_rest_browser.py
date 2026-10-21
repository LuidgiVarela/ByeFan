"""One-page REST v1 experiment in the owner's existing Instagram browser session.

No cookie extraction, login, retries, pagination, or alternate accounts.
Importing this module and dry-run do not access Instagram.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.experimental.playwright_browser import (
    OBSERVER, ROOT, TARGET, observer_visible, page_control_or_title_matches,
    refuse_if_gate, safe_profile_path,
)
from backend.experimental.structured_lists import assess_pages

MAX_SAMPLE = 10
REPORTS = ROOT / "reports" / "structured-rest-stage11"
PLAYWRIGHT_REPORTS = ROOT / "reports" / "playwright-stage8-integral"

# Executed only inside an authenticated instagram.com tab. The browser supplies
# its own first-party session; no cookie or token value crosses into Python.
FETCH_ONE = """async ({kind, targetId, target}) => {
  if (location.origin !== 'https://www.instagram.com')
    return {error: 'unexpected_origin'};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  let path;
  if (kind === 'profile') {
    path = `/api/v1/users/web_profile_info/?username=${encodeURIComponent(target)}`;
  } else if (kind === 'followers' || kind === 'following') {
    if (!/^[0-9]+$/.test(targetId || '')) return {error: 'invalid_target_id'};
    path = `/api/v1/friendships/${targetId}/${kind}/?count=10`;
  } else return {error: 'invalid_kind'};
  try {
    const response = await fetch(path, {
      method: 'GET', credentials: 'include', redirect: 'manual',
      headers: {'x-ig-app-id': '936619743392459'}, signal: controller.signal
    });
    if (response.type === 'opaqueredirect' || response.status >= 300 && response.status < 400)
      return {error: 'redirect_or_authentication_required', http_status: response.status};
    if ([401, 403, 429].includes(response.status))
      return {error: 'access_restricted', http_status: response.status};
    if (!response.ok) return {error: 'http_error', http_status: response.status};
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('json')) return {error: 'non_json_response', http_status: response.status};
    const data = await response.json();
    if (data?.status !== 'ok') return {error: 'status_not_ok', http_status: response.status};
    const safeId = value => typeof value === 'string' ? value :
      Number.isSafeInteger(value) ? String(value) : null;
    if (kind === 'profile') {
      const user = data?.data?.user;
      return {http_status: response.status, profile: user ? {
        id: safeId(user.pk_id ?? user.pk ?? user.id), username: user.username,
        is_private: user.is_private, follower_count: user.edge_followed_by?.count ?? user.follower_count,
        following_count: user.edge_follow?.count ?? user.following_count
      } : null};
    }
    if (!Array.isArray(data?.users)) return {error: 'users_missing', http_status: response.status};
    return {http_status: response.status, server_rows: data.users.length,
      status: data.status, next_max_id: data.next_max_id ?? null,
      users: data.users.slice(0, 10).map(user => ({
        pk_id: safeId(user.pk_id ?? user.pk), username: user.username ?? null
      }))};
  } catch (error) {
    return {error: error?.name === 'AbortError' ? 'request_timeout' : 'request_failed'};
  } finally {clearTimeout(timer);}
}"""


def _safe_error(exc):
    if isinstance(exc, RuntimeError) and str(exc) in {
        "authentication_or_verification_required", "authentication_required",
        "verification_or_access_limit", "browser_profile_must_be_outside_repository",
        "existing_browser_profile_missing", "observer_session_not_confirmed_in_ui",
        "target_profile_unavailable", "redirect_or_authentication_required",
        "access_restricted", "http_error", "non_json_response",
        "status_not_ok", "request_timeout", "request_failed",
    }:
        return str(exc)
    return "browser_or_schema_error"


def prior_playwright_usernames(direction: str) -> set[str]:
    """Local comparison only; previous incomplete list is not a reference."""
    files = sorted(PLAYWRIGHT_REPORTS.glob("result-*.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("target") != TARGET:
                continue
            return {row["username"].lower() for row in data[direction]["rows"]}
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return set()


def _check_profile(reply):
    if reply.get("error"):
        raise RuntimeError(reply["error"])
    profile = reply.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("profile_missing")
    profile_id = profile.get("id")
    if (isinstance(profile_id, bool) or not isinstance(profile_id, (str, int))
            or not str(profile_id).isdigit() or profile.get("username", "").lower() != TARGET):
        raise ValueError("profile_identity_mismatch")
    if profile.get("is_private") is not False:
        raise ValueError("private_or_unknown_visibility")
    return str(profile_id), profile


def _sanitize_list(reply, direction):
    if reply.get("error"):
        return {"state": "inaccessible", "reason": reply["error"],
                "http_status": reply.get("http_status"), "rows": []}
    server_rows = reply.get("server_rows")
    if type(server_rows) is not int or server_rows < 0:
        return {"state": "invalid", "reason": "invalid_server_row_count", "rows": []}
    if server_rows > MAX_SAMPLE:
        # The server ignored our count=10 request. Do not retain any rows.
        return {"state": "oversized_response", "reason": "server_ignored_sample_limit",
                "http_status": reply.get("http_status"), "server_rows": server_rows,
                "rows": []}
    assessment = assess_pages([reply], "private_rest_v1", direction,
                              stopped_early=True)
    if assessment.state == "invalid":
        return {"state": "invalid", "reason": assessment.reason, "rows": []}
    if assessment.duplicate_ids:
        return {"state": "invalid", "reason": "duplicate_id_in_page", "rows": []}
    prior = prior_playwright_usernames(direction)
    return {"state": "sample_only", "reason": "single_page_sample",
            "http_status": reply.get("http_status"), "server_rows": server_rows,
            "next_cursor": reply.get("next_max_id"),
            "rows": [{"id": row.id, "username": row.username,
                      "seen_in_prior_playwright": row.username in prior}
                     for row in assessment.rows],
            "prior_playwright_available": bool(prior),
            "prior_playwright_overlap": sum(row.username in prior for row in assessment.rows)}


def run_once():
    """Explicit live operation: at most profile + one request per direction."""
    from playwright.sync_api import sync_playwright

    result = {"target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "requests": [], "followers": None, "following": None,
              "error": None, "elapsed_seconds": None, "integrity_verified": False}
    started = time.monotonic()

    def request(page, kind, target_id=None):
        began = time.monotonic()
        reply = None
        try:
            reply = page.evaluate(FETCH_ONE, {"kind": kind, "targetId": target_id,
                                              "target": TARGET})
            return reply
        finally:
            event = {"kind": kind,
                     "elapsed_seconds": round(time.monotonic() - began, 3)}
            if isinstance(reply, dict):
                event["http_status"] = reply.get("http_status")
                event["error"] = reply.get("error")
            result["requests"].append(event)

    try:
        profile_path = safe_profile_path()
        if not profile_path.is_dir():
            raise RuntimeError("existing_browser_profile_missing")
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_path), headless=False, locale="en-US",
                viewport={"width": 390, "height": 800})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                refuse_if_gate(page)
                if not observer_visible(page):
                    raise RuntimeError("observer_session_not_confirmed_in_ui")
                page.goto(f"https://www.instagram.com/{TARGET}/",
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                refuse_if_gate(page)
                if not page_control_or_title_matches(page):
                    raise RuntimeError("target_profile_unavailable")
                target_id, profile = _check_profile(request(page, "profile"))
                result["profile"] = {"id": target_id,
                    "followers_count": profile.get("follower_count"),
                    "following_count": profile.get("following_count")}
                for direction in ("followers", "following"):
                    refuse_if_gate(page)
                    reply = request(page, direction, target_id)
                    result[direction] = _sanitize_list(reply, direction)
                    if result[direction]["state"] != "sample_only":
                        break
            finally:
                context.close()
    except Exception as exc:
        result["error"] = _safe_error(exc)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"result-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, result
