"""UI-only diagnostic. Never imports an authenticated browser profile."""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.collector.instagram import normalize_username


def main():
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    parser = argparse.ArgumentParser()
    parser.add_argument("username", type=normalize_username)
    parser.add_argument("--output", type=Path, default=Path("reports/browser.json"))
    args = parser.parse_args()
    result = {"username": args.username, "started_at": datetime.now(timezone.utc).isoformat(),
              "method": "Playwright, fresh anonymous context", "followers_recovered": 0,
              "following_recovered": 0, "integrity_verified": False, "events": []}
    start = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="en-US")
        page = context.new_page()
        try:
            response = page.goto(f"https://www.instagram.com/{args.username}/", wait_until="domcontentloaded", timeout=30000)
            result["profile_http_status"] = response.status if response else None
            page.wait_for_timeout(3000)
            result["profile_title"] = page.title()
            result["profile_description"] = page.locator('meta[property="og:description"]').get_attribute("content")
            result["login_form_visible"] = any(
                loc.is_visible() for loc in page.locator('input[name="username"]').all())
            result["login_button_visible"] = any(
                loc.is_visible() for loc in page.get_by_role("button", name=re.compile(r"log in", re.I)).all())
            result["list_controls"] = {
                kind: page.get_by_role("link", name=re.compile(kind, re.I)).count()
                for kind in ("followers", "following")}
            for kind in ("followers", "following"):
                if any(s in page.url for s in ("/accounts/login", "/challenge", "/checkpoint")):
                    result["stop_reason"] = "authentication_or_challenge_required"
                    break
                links = page.locator(f'a[href$="/{args.username}/{kind}/"]')
                event = {"list": kind, "visible_link": links.count() > 0 and links.first.is_visible()}
                result["events"].append(event)
                if not event["visible_link"]:
                    result["stop_reason"] = "list_link_unavailable"
                    continue
                links.first.click(timeout=5000)
                page.wait_for_timeout(2000)
                event["login_redirect"] = "/accounts/login" in page.url
                event["login_form"] = page.locator('input[name="username"]').count() > 0
                event["dialog_visible"] = page.get_by_role("dialog").count() > 0
                # Diagnostic only: do not scroll, solve challenges or dismiss login gates.
                result["stop_reason"] = ("authentication_required" if event["login_redirect"] or event["login_form"]
                                         else "list_integrity_unverified")
                break
        except PlaywrightTimeout:
            result["stop_reason"] = "browser_timeout"
        finally:
            browser.close()
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
