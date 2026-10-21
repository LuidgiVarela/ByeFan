"""Fresh unauthenticated browser-context sample for the authorized profile only."""
import json
import time
import uuid
from datetime import datetime, timezone

from backend.experimental.playwright_browser import (
    MAX_USERS, ROOT, TARGET, page_control_or_title_matches, read_list, refuse_if_gate,
)
from backend.experimental.response_probe import PageResponseProbe


def collect_anonymous_ui(page):
    started = time.monotonic()
    probe = PageResponseProbe(page)
    result = {"target": TARGET, "authenticated": False,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "profile_available": False, "followers": None, "following": None,
              "structured_response_diagnostics": None,
              "integrity_verified": False, "non_followers": None,
              "comparison_status": "insufficient_data", "error": None,
              "elapsed_seconds": 0}
    try:
        page.goto(f"https://www.instagram.com/{TARGET}/",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        result["profile_available"] = True
        for direction in ("followers", "following"):
            evidence = read_list(page, direction, probe=probe)
            result[direction] = evidence
            if evidence["error"] in ("authentication_or_verification_required",
                                     "authentication_required", "verification_or_access_limit",
                                     "ui_error_or_timeout"):
                result["error"] = evidence["error"]
                break
            if direction == "followers" and evidence["dialog_opened"]:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                refuse_if_gate(page)
        if any(result[direction] and result[direction]["error"] == "empty_dialog_with_nonzero_count"
               for direction in ("followers", "following")):
            result["error"] = "anonymous_list_rows_unavailable"
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "target_profile_unavailable", "authentication_or_verification_required",
            "authentication_required", "verification_or_access_limit"
        } else "navigation_error_or_timeout"
    result["structured_response_diagnostics"] = probe.summary()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def run_anonymous_sample():
    """Do not call without the owner's explicit Stage 5 live-test authorization."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            # new_context is isolated and starts without imported storage state.
            context = browser.new_context(locale="en-US", viewport={"width": 390, "height": 800})
            try:
                page = context.new_page()
                result = collect_anonymous_ui(page)
            finally:
                context.close()
        finally:
            browser.close()
    result["sample_limit_per_list"] = MAX_USERS
    result["run_id"] = uuid.uuid4().hex
    output_dir = ROOT / "reports" / "playwright-stage5-anonymous"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / ("result-" + result["run_id"] + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, result
