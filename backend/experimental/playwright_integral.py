"""One bounded full traversal through the visible Instagram UI only."""
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from backend.experimental.playwright_browser import (
    OBSERVER, ROOT, TARGET, compare_usernames_experimental, extract_visible_people,
    normalized_username, observer_visible, page_control_or_title_matches,
    profile_control, refuse_if_gate, safe_profile_path, scroll_geometry,
    scroll_is_stably_terminal, scroll_metrics_and_advance,
    validate_username_reference,
)
from backend.experimental.response_probe import PageResponseProbe

MAX_RUNTIME_SECONDS = 720
WORK_BUDGET_SECONDS = 710  # leave time to save the final report and close Chromium
MAX_CYCLES_PER_LIST = 300
SCROLL_PAUSE_MS = 1100


def parse_visible_count(control):
    numbers = re.findall(r"(?<!\w)\d{1,9}(?!\w)",
                         control.inner_text(timeout=3000).replace(",", ""))
    return int(numbers[0]) if len(numbers) == 1 else None


def save_checkpoint(path: Path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**result, "checkpointed_at": datetime.now(timezone.utc).isoformat()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class NetworkSummary:
    """Aggregate already completed browser traffic; stop on explicit HTTP access limits."""

    def __init__(self, page):
        self.phase = None
        self.counts = {direction: {"finished_fetch_xhr": 0, "failed_fetch_xhr": 0,
                                   "http_401": 0, "http_403": 0, "http_429": 0}
                       for direction in ("followers", "following")}
        self.restriction = None
        page.on("requestfinished", self.finished)
        page.on("requestfailed", self.failed)

    def set_phase(self, direction):
        self.phase = direction

    def eligible(self, request):
        host = (urlsplit(request.url).hostname or "").lower()
        return (self.phase in self.counts and request.resource_type in ("fetch", "xhr")
                and (host == "instagram.com" or host.endswith(".instagram.com")))

    def finished(self, request):
        if not self.eligible(request):
            return
        bucket = self.counts[self.phase]
        bucket["finished_fetch_xhr"] += 1
        try:
            response = request.response()
            status = response.status if response else None
        except Exception:
            status = None
        if status in (401, 403, 429):
            bucket[f"http_{status}"] += 1
            self.restriction = ("network_authentication_required" if status == 401
                                else "network_access_limited")

    def failed(self, request):
        if self.eligible(request):
            self.counts[self.phase]["failed_fetch_xhr"] += 1

    def check(self):
        if self.restriction:
            raise RuntimeError(self.restriction)


def collect_list(page, direction, deadline, network, probe, checkpoint):
    started = time.monotonic()
    result = {"direction": direction, "reported_count": None,
              "reported_count_after": None, "counts_stable": None,
              "rows": [], "recovered_count": 0, "numeric_ids_seen": 0,
              "duplicate_anchors_seen": 0, "overlap_observations": 0,
              "cycles": [], "terminal_observed": False,
              "state": "unavailable", "reason": "not_attempted",
              "error": None, "elapsed_seconds": 0}
    known = {}
    try:
        if time.monotonic() >= deadline - 3:
            result.update(state="incomplete", reason="global_time_limit")
            return result
        refuse_if_gate(page)
        network.check()
        control = profile_control(page, direction)
        if control is None:
            result["reason"] = "list_control_unavailable"
            return result
        result["reported_count"] = parse_visible_count(control)
        network.set_phase(direction)
        probe.set_phase(direction)
        control.click(timeout=min(5000, max(1000, int((deadline - time.monotonic()) * 1000))))
        page.wait_for_timeout(1200)
        refuse_if_gate(page)
        network.check()
        dialogs = page.get_by_role("dialog")
        if not dialogs.count() or not dialogs.first.is_visible():
            result["reason"] = "list_dialog_unavailable"
            return result
        dialog = dialogs.first
        initial = extract_visible_people(dialog)
        result["duplicate_anchors_seen"] += initial["duplicate_anchor_count"]
        for row in initial["rows"]:
            known[normalized_username(row["username"])] = row
        result["rows"] = list(known.values())
        result["recovered_count"] = len(known)
        checkpoint(result)
        if not known and result["reported_count"] and result["reported_count"] > 0:
            result.update(state="unavailable", reason="empty_dialog_with_nonzero_count")
            return result
        bottom_stable = 0
        stalled = 0
        for index in range(MAX_CYCLES_PER_LIST):
            cycle_started = time.monotonic()
            if cycle_started >= deadline - 3:
                result.update(state="incomplete", reason="global_time_limit")
                break
            refuse_if_gate(page)
            network.check()
            before = scroll_geometry(dialog)
            if before is None:
                result.update(state="incomplete", reason="scroll_container_missing")
                break
            scroll_started = time.monotonic()
            advanced = scroll_metrics_and_advance(dialog)
            scroll_seconds = time.monotonic() - scroll_started
            if advanced is None:
                result.update(state="incomplete", reason="scroll_container_disappeared")
                break
            wait_started = time.monotonic()
            page.wait_for_timeout(SCROLL_PAUSE_MS)
            wait_seconds = time.monotonic() - wait_started
            refuse_if_gate(page)
            network.check()
            read_started = time.monotonic()
            snapshot = extract_visible_people(dialog)
            read_seconds = time.monotonic() - read_started
            new_count = 0
            overlap = 0
            for row in snapshot["rows"]:
                key = normalized_username(row["username"])
                if key not in known:
                    known[key] = row
                    new_count += 1
                else:
                    overlap += 1
                    if known[key]["id"] is None and row["id"] is not None:
                        known[key] = row
            after = scroll_geometry(dialog)
            result["duplicate_anchors_seen"] += snapshot["duplicate_anchor_count"]
            result["overlap_observations"] += overlap
            result["rows"] = list(known.values())
            result["recovered_count"] = len(known)
            result["numeric_ids_seen"] = sum(row["id"] is not None for row in known.values())
            position_advanced = bool(after and after["top"] > before["top"] + 1)
            content_grew = bool(after and after["height"] > before["height"] + 1)
            result["cycles"].append({
                "cycle": index + 1, "elapsed_seconds": round(time.monotonic() - started, 3),
                "total_seconds": round(time.monotonic() - cycle_started, 3),
                "dom_read_seconds": round(read_seconds, 3),
                "wait_seconds": round(wait_seconds, 3),
                "scroll_seconds": round(scroll_seconds, 3),
                "new_usernames": new_count, "total_usernames": len(known),
                "duplicate_anchors": snapshot["duplicate_anchor_count"],
                "scroll_before_px": round(before["top"], 1),
                "scroll_after_px": round(after["top"], 1) if after else None,
                "scroll_height_before_px": round(before["height"], 1),
                "scroll_height_after_px": round(after["height"], 1) if after else None,
                "position_advanced": position_advanced,
                "content_grew": content_grew,
            })
            checkpoint(result)
            if scroll_is_stably_terminal(advanced, after, new_count):
                bottom_stable += 1
            else:
                bottom_stable = 0
            if not (new_count or position_advanced or content_grew):
                stalled += 1
            else:
                stalled = 0
            if bottom_stable >= 3:
                result["terminal_observed"] = True
                if result["reported_count"] == len(known):
                    result.update(state="terminal_observed_unverified",
                                  reason="ui_end_and_count_match_no_independent_reference")
                else:
                    result.update(state="incomplete", reason="ui_end_count_mismatch")
                break
            if stalled >= 3:
                result.update(state="incomplete", reason="three_cycles_without_progress")
                break
        else:
            result.update(state="incomplete", reason="local_cycle_limit")
        return result
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit", "network_authentication_required",
            "network_access_limited"} else "ui_error_or_timeout"
        result.update(state="interrupted", reason=code, error=code)
        return result
    finally:
        if network is not None:
            network.set_phase(None)
        if probe is not None:
            probe.set_phase(None)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        checkpoint(result)


def collect_integral(page, result, deadline, checkpoint, reference=None):
    network = NetworkSummary(page)
    probe = PageResponseProbe(page, max_responses_per_list=20, max_body_bytes=500_000)
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded",
                  timeout=min(30000, max(1000, int((deadline - time.monotonic()) * 1000))))
        page.wait_for_timeout(1500)
        refuse_if_gate(page)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        result["observer_control_visible"] = True
        page.goto(f"https://www.instagram.com/{TARGET}/", wait_until="domcontentloaded",
                  timeout=min(30000, max(1000, int((deadline - time.monotonic()) * 1000))))
        page.wait_for_timeout(5000)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        result["profile_available"] = True
        checkpoint()
        for direction in ("followers", "following"):
            if time.monotonic() >= deadline - 8:
                result["error"] = "global_time_limit"
                break
            def on_list_checkpoint(current, kind=direction):
                result[kind] = current
                checkpoint()
            list_result = collect_list(page, direction, deadline, network, probe,
                                       on_list_checkpoint)
            result[direction] = list_result
            checkpoint()
            if list_result["state"] == "interrupted":
                result["error"] = list_result["error"]
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            refuse_if_gate(page)
            network.check()
        if result["followers"] and result["following"] and time.monotonic() < deadline - 5:
            for direction in ("followers", "following"):
                control = profile_control(page, direction)
                count_after = parse_visible_count(control) if control else None
                item = result[direction]
                item["reported_count_after"] = count_after
                item["counts_stable"] = (count_after is not None
                                          and count_after == item["reported_count"])
                if item["state"] == "terminal_observed_unverified" and not item["counts_stable"]:
                    item.update(state="incomplete", reason="profile_count_changed")
        if all(result[d] and result[d]["state"] == "terminal_observed_unverified"
               and result[d]["counts_stable"] for d in ("followers", "following")):
            result["non_followers_experimental"] = compare_usernames_experimental(
                result["followers"]["rows"], result["following"]["rows"])
            result["comparison_status"] = "provisional_username_only"
            if reference is not None:
                result["reference_matched"] = validate_username_reference(result, reference)
                if result["reference_matched"]:
                    result["comparison_status"] = "reference_matched_username_only"
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit", "network_authentication_required",
            "network_access_limited"} else "navigation_error_or_timeout"
    finally:
        result["network_events"] = network.counts
        if time.monotonic() < deadline - 5:
            result["structured_response_diagnostics"] = probe.summary()
        else:
            result["structured_response_diagnostics"] = "skipped_due_time_limit"
        checkpoint()
    return result


def run_integral(reference=None):
    """One attempt, with a global work deadline and an ignored local checkpoint."""
    from playwright.sync_api import sync_playwright
    started = time.monotonic()
    deadline = started + WORK_BUDGET_SECONDS
    run_id = uuid.uuid4().hex
    output_dir = ROOT / "reports" / "playwright-stage8-integral"
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / ("checkpoint-" + run_id + ".json")
    result = {"run_id": run_id, "target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "max_runtime_seconds": MAX_RUNTIME_SECONDS,
              "observer_control_visible": False, "profile_available": False,
              "followers": None, "following": None,
              "network_events": None, "structured_response_diagnostics": None,
              "reference_checked": reference is not None, "reference_matched": False,
              "integrity_verified": False, "non_followers": None,
              "non_followers_experimental": None,
              "comparison_status": "insufficient_data", "error": None,
              "elapsed_seconds": 0}

    def checkpoint():
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_checkpoint(checkpoint_path, result)

    checkpoint()
    try:
        profile = safe_profile_path()
        if not profile.is_dir():
            raise RuntimeError("existing_browser_profile_missing")
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=False, locale="en-US",
                viewport={"width": 390, "height": 800})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                collect_integral(page, result, deadline, checkpoint, reference)
            finally:
                context.close()
    except Exception as exc:
        result["error"] = (str(exc) if type(exc) is RuntimeError
                           and str(exc) == "existing_browser_profile_missing"
                           else "browser_start_or_close_failed")
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    checkpoint()
    output = output_dir / ("result-" + run_id + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, checkpoint_path, result
