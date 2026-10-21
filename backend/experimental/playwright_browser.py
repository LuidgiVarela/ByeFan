"""Visible UI experiment. No network interception, cookies, tokens or hidden APIs."""
import json
import os
import re
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from backend.collector.models import Collection, ListResult, User
from backend.services.comparator import InsufficientData, non_followers
from backend.identity import TARGET, OBSERVER

MAX_USERS = 10
ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "reports" / "playwright-stage3"
PROFILE = (Path(os.environ.get("LOCALAPPDATA", "")) / "ByeFan" / "playwright-stage3-profile")
HANDLE = re.compile(r"^/([A-Za-z0-9_.]{1,30})/?$")


def safe_profile_path(path=PROFILE):
    resolved = Path(path).resolve()
    if not os.environ.get("LOCALAPPDATA") or resolved.is_relative_to(ROOT):
        raise RuntimeError("browser_profile_must_be_outside_repository")
    return resolved


def status_path():
    return CONTROL / "status.json"


def write_status(run_id, stage, **details):
    CONTROL.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "stage": stage,
            "updated_at": datetime.now(timezone.utc).isoformat(), **details}
    temporary = status_path().with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(status_path())


def approved(run_id):
    marker = CONTROL / ("confirm-" + run_id + ".json")
    if not marker.exists():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("run_id") == run_id
    except (ValueError, OSError):
        return False


ACCESS_GATE_TEXTS = (
    "Confirm it's you", "Help us confirm you own this account", "Enter security code",
    "Try again later", "Please wait a few minutes", "We restrict certain activity",
    "We limit how often", "Action Blocked", "Verify it's you", "Suspicious activity",
    "CAPTCHA", "I'm not a robot", "Security check", "Unusual activity",
)


def refuse_if_gate_batched(page):
    """Same main-document stop checks, using one browser read for visible text."""
    if any(fragment in page.url.lower() for fragment in
           ("/challenge", "/checkpoint", "/accounts/login")):
        raise RuntimeError("authentication_or_verification_required")
    evidence = page.evaluate("""phrases => {
      const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').toLowerCase();
      return {password: !!document.querySelector('input[type="password"]'),
        restriction: phrases.some(phrase => text.includes(phrase.toLowerCase()))};
    }""", list(ACCESS_GATE_TEXTS))
    if evidence["password"]:
        raise RuntimeError("authentication_required")
    if evidence["restriction"]:
        raise RuntimeError("verification_or_access_limit")


def refuse_if_gate(page):
    url = page.url.lower()
    if any(fragment in url for fragment in ("/challenge", "/checkpoint", "/accounts/login")):
        raise RuntimeError("authentication_or_verification_required")
    if page.locator('input[type="password"]').count() > 0:
        raise RuntimeError("authentication_required")
    for text in ACCESS_GATE_TEXTS:
        loc = page.get_by_text(text, exact=False)
        if any(loc.nth(i).is_visible() for i in range(min(loc.count(), 20))):
            raise RuntimeError("verification_or_access_limit")


def observer_visible(page):
    refuse_if_gate(page)
    links = page.locator(f'a[href="/{OBSERVER}/"], a[href="https://www.instagram.com/{OBSERVER}/"]')
    return any(links.nth(i).is_visible() for i in range(min(links.count(), 20)))


def profile_control(page, direction):
    href = f"/{TARGET}/{direction}/"
    for links in (page.locator(f'a[href*="/{TARGET}/{direction}"]'),
                  page.locator('a[href="#"]').filter(has_text=direction)):
        for i in range(min(links.count(), 20)):
            link = links.nth(i)
            if link.is_visible():
                return link
    return None


def visible_people(dialog):
    """Read visible anchors in one browser round trip; no hidden API data."""
    return extract_visible_people(dialog)["rows"]


def extract_visible_people(dialog, *, include_geometry=False):
    return dialog.evaluate("""(root, options) => {
      const {target, includeGeometry} = options;
      const rows = [];
      const seen = new Set();
      const anchors = root.querySelectorAll('a[href]');
      let visible = 0;
      let duplicateAnchors = 0;
      for (const anchor of anchors) {
        const style = getComputedStyle(anchor);
        if (!anchor.getClientRects().length || style.visibility === 'hidden'
            || style.display === 'none') continue;
        visible++;
        const match = /^\\/([A-Za-z0-9_.]{1,30})\\/?$/.exec(anchor.getAttribute('href') || '');
        if (!match) continue;
        const username = match[1];
        const key = username.toLowerCase();
        if (key === target.toLowerCase()) continue;
        if (seen.has(key)) { duplicateAnchors++; continue; }
        seen.add(key);
        const value = anchor.getAttribute('data-user-id') || anchor.getAttribute('data-id');
        rows.push({id: value && /^[0-9]+$/.test(value) ? value : null, username});
      }
      const result = {rows, anchor_count: anchors.length, visible_anchor_count: visible,
        duplicate_anchor_count: duplicateAnchors};
      if (includeGeometry) {
        const nodes = [root, ...root.querySelectorAll('*')];
        const candidates = nodes.filter(el => el.scrollHeight > el.clientHeight + 20
          && getComputedStyle(el).overflowY !== 'hidden');
        candidates.sort((a, b) => (b.scrollHeight - b.clientHeight)
          - (a.scrollHeight - a.clientHeight));
        const el = candidates[0];
        result.geometry = el ? {top: el.scrollTop, height: el.scrollHeight,
          viewport: el.clientHeight,
          at_bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 3} : null;
        result.loading = nodes.some(el => (el.getAttribute('role') === 'progressbar'
          || el.getAttribute('aria-busy') === 'true')
          && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
      }
      return result;
    }""", {"target": TARGET, "includeGeometry": include_geometry})


def scroll_dialog_once(dialog):
    return bool(dialog.evaluate("""root => {
      const nodes = [root, ...root.querySelectorAll('*')];
      const scrollable = nodes.find(el => el.scrollHeight > el.clientHeight + 20
        && getComputedStyle(el).overflowY !== 'hidden');
      if (!scrollable) return false;
      scrollable.scrollTop = scrollable.scrollHeight;
      return true;
    }"""))


def read_list(page, direction, probe=None):
    evidence = {"direction": direction, "control_available": False, "dialog_opened": False,
                "rows": [], "scroll_attempted": False, "new_users_after_scroll": None,
                "reported_count": None, "error": None, "elapsed_seconds": 0}
    started = time.monotonic()
    try:
        refuse_if_gate(page)
        control = profile_control(page, direction)
        if control is None:
            evidence["error"] = "list_control_unavailable"
            return evidence
        evidence["control_available"] = True
        label = control.inner_text(timeout=3000)
        # Counts may be localized/abbreviated. Only exact plain integers are evidence.
        numbers = re.findall(r"(?<!\w)\d{1,9}(?!\w)", label.replace(",", ""))
        if len(numbers) == 1:
            evidence["reported_count"] = int(numbers[0])
        if probe is not None:
            probe.set_phase(direction)
        control.click(timeout=5000)
        page.wait_for_timeout(1200)
        refuse_if_gate(page)
        dialog = page.get_by_role("dialog")
        if not dialog.count() or not dialog.first.is_visible():
            evidence["error"] = "list_dialog_unavailable"
            return evidence
        dialog = dialog.first
        evidence["dialog_opened"] = True
        initial = visible_people(dialog)
        rows = initial[:MAX_USERS]
        if len(rows) < MAX_USERS:
            evidence["scroll_attempted"] = scroll_dialog_once(dialog)
            if evidence["scroll_attempted"]:
                page.wait_for_timeout(1000)
                refuse_if_gate(page)
                next_rows = visible_people(dialog)
                known = {row["username"].lower() for row in rows}
                additions = [row for row in next_rows if row["username"].lower() not in known]
                evidence["new_users_after_scroll"] = len(additions)
                rows.extend(additions[:MAX_USERS - len(rows)])
        evidence["rows"] = rows
        if not rows and evidence["reported_count"] and evidence["reported_count"] > 0:
            evidence["error"] = "empty_dialog_with_nonzero_count"
    except Exception as exc:
        # No raw Instagram response/body/exception text in reports.
        code = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "ui_error_or_timeout"
        evidence["error"] = code
    finally:
        if probe is not None:
            probe.set_phase(None)
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return evidence


def collect_ui(page):
    started = time.monotonic()
    result = {"target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "login_manual_confirmed": True, "observer_control_visible": False,
              "profile_available": False, "followers": None, "following": None,
              "ids_available": False, "instagram_pagination_verified": False,
              "integrity_verified": False, "non_followers": None,
              "comparison_status": "insufficient_data", "error": None,
              "error_type": None, "page_closed_at_error": None,
              "target_path_reached": False, "target_handle_visible": False,
              "elapsed_seconds": 0}
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        result["observer_control_visible"] = True
        page.goto(f"https://www.instagram.com/{TARGET}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        refuse_if_gate(page)
        result["target_path_reached"] = page.url.split("?", 1)[0].rstrip("/").endswith("/" + TARGET)
        target_labels = page.get_by_text(TARGET, exact=True)
        result["target_handle_visible"] = any(target_labels.nth(i).is_visible()
                                                   for i in range(min(target_labels.count(), 20)))
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        result["profile_available"] = True
        for direction in ("followers", "following"):
            evidence = read_list(page, direction)
            result[direction] = evidence
            if evidence["error"] in ("authentication_or_verification_required", "authentication_required",
                                     "verification_or_access_limit", "ui_error_or_timeout"):
                result["error"] = evidence["error"]
                break
            if direction == "followers" and evidence["dialog_opened"]:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        result["ids_available"] = all(row["id"] is not None for direction in ("followers", "following")
                                      for row in (result[direction] or {}).get("rows", [])) and any(
            (result[direction] or {}).get("rows") for direction in ("followers", "following"))
        # Apply the existing comparator's gate to the observed sample. It must reject it.
        left = ListResult(users=[User(row["id"] or "", row["username"])
                                 for row in (result["followers"] or {}).get("rows", [])],
                          state="incomplete", reason="visible_sample_only")
        right = ListResult(users=[User(row["id"] or "", row["username"])
                                  for row in (result["following"] or {}).get("rows", [])],
                           state="incomplete", reason="visible_sample_only")
        try:
            result["non_followers"] = [asdict(u) for u in non_followers(left, right)]
            result["comparison_status"] = "verified"
        except InsufficientData:
            pass
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "navigation_error_or_timeout"
        result["error_type"] = type(exc).__name__
        result["page_closed_at_error"] = page.is_closed()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def page_control_or_title_matches(page):
    title = page.title().lower()
    labels = page.get_by_text(TARGET, exact=True)
    return TARGET in title or any(labels.nth(i).is_visible() for i in range(min(labels.count(), 20))) \
        or profile_control(page, "followers") is not None or profile_control(page, "following") is not None


def normalized_username(value):
    """Instagram handles are case-insensitive; this is not a stable user ID."""
    if not isinstance(value, str):
        raise ValueError("invalid_username")
    handle = value.strip().removeprefix("@").casefold()
    if not re.fullmatch(r"[a-z0-9_.]{1,30}", handle):
        raise ValueError("invalid_username")
    return handle


def compare_usernames_experimental(followers, following):
    """Only call for two terminal UI observations; never a verified ID result."""
    followers_by_name = {normalized_username(row["username"]) for row in followers}
    following_by_name = {normalized_username(row["username"]) for row in following}
    return sorted(following_by_name - followers_by_name)


def validate_username_reference(result, reference):
    """Owner-provided independent reference; matching names remains weaker than IDs."""
    if not all(result[direction]["state"] == "terminal_observed_unverified"
               for direction in ("followers", "following")):
        return False
    if reference.get("username") != TARGET:
        return False
    for direction in ("followers", "following"):
        provided = reference.get(direction + "_usernames")
        if not isinstance(provided, list):
            return False
        try:
            names = [normalized_username(name) for name in provided]
        except ValueError:
            return False
        if len(names) != len(set(names)):
            return False
        observed = {normalized_username(row["username"])
                    for row in result[direction]["rows"]}
        if observed != set(names):
            return False
    return True


def scroll_metrics_and_advance(dialog):
    """One progressive UI scroll; return geometry or None if no scroll container."""
    return dialog.evaluate("""root => {
      const nodes = [root, ...root.querySelectorAll('*')];
      const candidates = nodes.filter(el => el.scrollHeight > el.clientHeight + 20
        && getComputedStyle(el).overflowY !== 'hidden');
      candidates.sort((a, b) => (b.scrollHeight - b.clientHeight)
        - (a.scrollHeight - a.clientHeight));
      const el = candidates[0];
      if (!el) return null;
      const before = el.scrollTop;
      el.scrollTop = Math.min(el.scrollHeight, before + Math.max(1, el.clientHeight * .75));
      return {before, after: el.scrollTop, height: el.scrollHeight,
        viewport: el.clientHeight,
        at_bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 3};
    }""")


def scroll_geometry(dialog):
    return dialog.evaluate("""root => {
      const nodes = [root, ...root.querySelectorAll('*')];
      const candidates = nodes.filter(el => el.scrollHeight > el.clientHeight + 20
        && getComputedStyle(el).overflowY !== 'hidden');
      candidates.sort((a, b) => (b.scrollHeight - b.clientHeight)
        - (a.scrollHeight - a.clientHeight));
      const el = candidates[0];
      if (!el) return null;
      return {top: el.scrollTop, height: el.scrollHeight,
        viewport: el.clientHeight,
        at_bottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 3};
    }""")


def scroll_is_stably_terminal(before, after, new_count):
    """A momentary pause or matching counter alone is never terminal evidence."""
    return bool(before and after and before["at_bottom"] and after["at_bottom"]
                and before["height"] == after["height"]
                and before["viewport"] == after["viewport"]
                and new_count == 0)


def read_full_list(page, direction, max_cycles=300, max_seconds=900):
    started = time.monotonic()
    evidence = {"direction": direction, "reported_count": None, "rows": [],
                "scroll_cycles": 0, "terminal_observed": False,
                "state": "unavailable", "reason": "not_attempted",
                "error": None, "elapsed_seconds": 0, "cycles": [],
                "dom_read_seconds": 0, "fixed_wait_seconds": 0}
    known = {}
    try:
        refuse_if_gate(page)
        control = profile_control(page, direction)
        if control is None:
            evidence["reason"] = "list_control_unavailable"
            return evidence
        label = control.inner_text(timeout=3000)
        numbers = re.findall(r"(?<!\w)\d{1,9}(?!\w)", label.replace(",", ""))
        if len(numbers) == 1:
            evidence["reported_count"] = int(numbers[0])
        control.click(timeout=5000)
        page.wait_for_timeout(1200)
        refuse_if_gate(page)
        dialog = page.get_by_role("dialog")
        if not dialog.count() or not dialog.first.is_visible():
            evidence["reason"] = "list_dialog_unavailable"
            return evidence
        dialog = dialog.first
        bottom_stable = 0
        stalled = 0
        for cycle in range(max_cycles + 1):
            refuse_if_gate(page)
            read_started = time.monotonic()
            snapshot = extract_visible_people(dialog)
            read_seconds = time.monotonic() - read_started
            evidence["dom_read_seconds"] += read_seconds
            new_count = 0
            for row in snapshot["rows"]:
                key = normalized_username(row["username"])
                if key not in known:
                    known[key] = row
                    new_count += 1
                elif known[key]["id"] is None and row["id"] is not None:
                    known[key] = row
            evidence["rows"] = list(known.values())
            if cycle == max_cycles or time.monotonic() - started >= max_seconds:
                evidence["state"] = "incomplete"
                evidence["reason"] = "local_cycle_or_time_limit"
                return evidence
            geometry = scroll_metrics_and_advance(dialog)
            if geometry is None:
                evidence["state"] = "incomplete"
                evidence["reason"] = "scroll_container_missing"
                return evidence
            evidence["scroll_cycles"] += 1
            wait_started = time.monotonic()
            page.wait_for_timeout(1100)
            evidence["fixed_wait_seconds"] += time.monotonic() - wait_started
            refuse_if_gate(page)
            after = scroll_geometry(dialog)
            evidence["cycles"].append({
                "cycle": cycle + 1, "elapsed_seconds": round(time.monotonic() - started, 3),
                "new_usernames": new_count, "total_usernames": len(known),
                "dom_read_seconds": round(read_seconds, 3),
                "anchor_count": snapshot["anchor_count"],
                "visible_anchor_count": snapshot["visible_anchor_count"],
                "scroll_before_px": round(geometry["before"], 1),
                "scroll_after_px": round(geometry["after"], 1),
                "scroll_height_px": round(geometry["height"], 1),
                "scroll_viewport_px": round(geometry["viewport"], 1),
                "at_bottom_after_wait": after["at_bottom"] if after else False,
                "height_after_wait_px": round(after["height"], 1) if after else None,
            })
            if scroll_is_stably_terminal(geometry, after, new_count):
                bottom_stable += 1
            else:
                bottom_stable = 0
            if geometry["after"] <= geometry["before"] + 1 and not geometry["at_bottom"] \
                    and new_count == 0:
                stalled += 1
            else:
                stalled = 0
            if stalled >= 3:
                evidence["state"] = "incomplete"
                evidence["reason"] = "scroll_stalled_before_bottom"
                return evidence
            if bottom_stable >= 3:
                # Read once more after the last wait before classifying the UI end.
                for row in extract_visible_people(dialog)["rows"]:
                    known.setdefault(normalized_username(row["username"]), row)
                evidence["rows"] = list(known.values())
                evidence["terminal_observed"] = True
                if evidence["reported_count"] == len(known):
                    evidence["state"] = "terminal_observed_unverified"
                    evidence["reason"] = "ui_end_and_count_match_no_independent_reference"
                else:
                    evidence["state"] = "incomplete"
                    evidence["reason"] = "ui_end_count_mismatch"
                return evidence
        return evidence
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "ui_error_or_timeout"
        evidence["state"] = "interrupted"
        evidence["reason"] = code
        evidence["error"] = code
        return evidence
    finally:
        evidence["dom_read_seconds"] = round(evidence["dom_read_seconds"], 3)
        evidence["fixed_wait_seconds"] = round(evidence["fixed_wait_seconds"], 3)
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)


def collect_full_ui(page, reference=None):
    started = time.monotonic()
    result = {"target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "observer_control_visible": False, "profile_available": False,
              "followers": None, "following": None, "reference_checked": reference is not None,
              "reference_matched": False, "integrity_verified": False,
              "non_followers_experimental": None, "comparison_status": "insufficient_data",
              "error": None, "elapsed_seconds": 0}
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        result["observer_control_visible"] = True
        page.goto(f"https://www.instagram.com/{TARGET}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        result["profile_available"] = True
        for direction in ("followers", "following"):
            evidence = read_full_list(page, direction)
            result[direction] = evidence
            if evidence["state"] == "interrupted":
                result["error"] = evidence["error"]
                break
            if direction == "followers":
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        if all(result[direction] and result[direction]["state"] == "terminal_observed_unverified"
               for direction in ("followers", "following")):
            result["non_followers_experimental"] = compare_usernames_experimental(
                result["followers"]["rows"], result["following"]["rows"])
            result["comparison_status"] = "provisional_username_only"
            if reference is not None:
                result["reference_matched"] = validate_username_reference(result, reference)
                if result["reference_matched"]:
                    result["comparison_status"] = "reference_matched_username_only"
        elif reference is not None:
            result["reference_matched"] = False
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "navigation_error_or_timeout"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def launch():
    from playwright.sync_api import sync_playwright
    profile = safe_profile_path()
    profile.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    write_status(run_id, "browser_starting", profile_location=str(profile))
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(profile), headless=False,
                                                                 locale="en-US", viewport={"width": 390, "height": 800})
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=30000)
        write_status(run_id, "awaiting_owner_confirmation", profile_location=str(profile))
        print(f"Chromium aberto. Faça login manual em @{OBSERVER}. Confirme nesta conversa quando terminar.", flush=True)
        try:
            while not approved(run_id):
                if page.is_closed():
                    raise RuntimeError("browser_closed_before_confirmation")
                time.sleep(1)
            write_status(run_id, "collecting_limited")
            outcome = collect_ui(page)
            output = CONTROL / ("result-" + run_id + ".json")
            output.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
            write_status(run_id, "finished", result_file=str(output), error=outcome["error"])
            print("Coleta limitada encerrada. Relatório: " + str(output), flush=True)
        except Exception:
            write_status(run_id, "stopped", error="browser_closed_or_navigation_failed")
            raise
        finally:
            context.close()


def run_full(reference=None):
    """One authorized Stage 4 run using the existing local browser profile."""
    from playwright.sync_api import sync_playwright
    profile = safe_profile_path()
    if not profile.is_dir():
        raise RuntimeError("existing_browser_profile_missing")
    output_dir = ROOT / "reports" / "playwright-stage4"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc).isoformat()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=False, locale="en-US", viewport={"width": 390, "height": 800})
        try:
            page = context.pages[0] if context.pages else context.new_page()
            result = collect_full_ui(page, reference=reference)
            result["run_id"] = run_id
            result["run_started_at"] = started
            output = output_dir / ("result-" + run_id + ".json")
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return output, result
        finally:
            context.close()
