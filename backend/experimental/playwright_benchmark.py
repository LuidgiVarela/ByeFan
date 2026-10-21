"""Bounded, visible-UI timing probe for the authorized Instagram profile."""
import json
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from backend.experimental.playwright_browser import (
    OBSERVER, PROFILE, ROOT, TARGET, extract_visible_people, normalized_username,
    observer_visible, page_control_or_title_matches, profile_control, refuse_if_gate,
    safe_profile_path, scroll_geometry, scroll_metrics_and_advance,
)

MAX_BENCHMARK_CYCLES = 10
MAX_INTERMEDIATE_CYCLES = 40
SCROLL_PAUSE_MS = 1100


class LoadEventCounter:
    """Counts browser-completed fetch/XHR events; stores no URLs or response bodies."""

    def __init__(self, page):
        self.phase = None
        self.finished = {"followers": 0, "following": 0}
        self.failed = {"followers": 0, "following": 0}
        page.on("requestfinished", self._finished)
        page.on("requestfailed", self._failed)

    def set_phase(self, direction):
        self.phase = direction

    def _eligible(self, request):
        host = (urlsplit(request.url).hostname or "").lower()
        return (self.phase in self.finished
                and request.resource_type in ("xhr", "fetch")
                and (host == "instagram.com" or host.endswith(".instagram.com")))

    def _finished(self, request):
        if self._eligible(request):
            self.finished[self.phase] += 1

    def _failed(self, request):
        if self._eligible(request):
            self.failed[self.phase] += 1

    def snapshot(self, direction):
        return self.finished[direction], self.failed[direction]


def benchmark_list(page, direction, max_cycles=MAX_BENCHMARK_CYCLES,
                   ceiling=MAX_BENCHMARK_CYCLES, load_events=None):
    if not 1 <= max_cycles <= ceiling <= MAX_INTERMEDIATE_CYCLES:
        raise ValueError("benchmark_cycle_limit_exceeded")
    started = time.monotonic()
    result = {"direction": direction, "reported_count": None,
              "initial_usernames": 0, "recovered_count": 0,
              "numeric_ids_seen": 0, "cycles": [], "state": "unavailable",
              "reason": "not_attempted", "error": None, "elapsed_seconds": 0}
    known = {}
    try:
        refuse_if_gate(page)
        control = profile_control(page, direction)
        if control is None:
            result["reason"] = "list_control_unavailable"
            return result
        label = control.inner_text(timeout=3000)
        numbers = re.findall(r"(?<!\w)\d{1,9}(?!\w)", label.replace(",", ""))
        if len(numbers) == 1:
            result["reported_count"] = int(numbers[0])
        if load_events is not None:
            load_events.set_phase(direction)
        control.click(timeout=5000)
        page.wait_for_timeout(1200)
        refuse_if_gate(page)
        dialogs = page.get_by_role("dialog")
        if not dialogs.count() or not dialogs.first.is_visible():
            result["reason"] = "list_dialog_unavailable"
            return result
        dialog = dialogs.first
        initial = extract_visible_people(dialog)
        for row in initial["rows"]:
            known[normalized_username(row["username"])] = row
        result["initial_usernames"] = len(known)
        result["recovered_count"] = len(known)
        if not known and result["reported_count"] and result["reported_count"] > 0:
            result["state"] = "unavailable"
            result["reason"] = "empty_dialog_with_nonzero_count"
            return result
        no_progress = 0
        for index in range(max_cycles):
            cycle_started = time.monotonic()
            event_counts_before = load_events.snapshot(direction) if load_events else (0, 0)
            refuse_if_gate(page)
            before = scroll_geometry(dialog)
            if before is None:
                result["state"] = "incomplete"
                result["reason"] = "scroll_container_missing"
                break
            scroll_started = time.monotonic()
            moved = scroll_metrics_and_advance(dialog)
            scroll_seconds = time.monotonic() - scroll_started
            if moved is None:
                result["state"] = "incomplete"
                result["reason"] = "scroll_container_disappeared"
                break
            wait_started = time.monotonic()
            page.wait_for_timeout(SCROLL_PAUSE_MS)
            wait_seconds = time.monotonic() - wait_started
            refuse_if_gate(page)
            read_started = time.monotonic()
            snapshot = extract_visible_people(dialog)
            read_seconds = time.monotonic() - read_started
            new_count = 0
            for row in snapshot["rows"]:
                key = normalized_username(row["username"])
                if key not in known:
                    known[key] = row
                    new_count += 1
                elif known[key]["id"] is None and row["id"] is not None:
                    known[key] = row
            after = scroll_geometry(dialog)
            position_advanced = bool(after and after["top"] > before["top"] + 1)
            content_grew = bool(after and after["height"] > before["height"] + 1)
            data_progress = new_count > 0
            result["recovered_count"] = len(known)
            result["numeric_ids_seen"] = sum(row["id"] is not None for row in known.values())
            result["cycles"].append({
                "cycle": index + 1,
                "total_seconds": round(time.monotonic() - cycle_started, 3),
                "dom_read_seconds": round(read_seconds, 3),
                "wait_seconds": round(wait_seconds, 3),
                "scroll_seconds": round(scroll_seconds, 3),
                "new_usernames": new_count,
                "total_usernames": len(known),
                "scroll_before_px": round(before["top"], 1),
                "scroll_after_px": round(after["top"], 1) if after else None,
                "scroll_height_before_px": round(before["height"], 1),
                "scroll_height_after_px": round(after["height"], 1) if after else None,
                "scroll_viewport_px": round(before["viewport"], 1),
                "position_advanced": position_advanced,
                "content_grew": content_grew,
                "data_progress": data_progress,
                "anchor_count": snapshot["anchor_count"],
                "completed_fetch_xhr": ((load_events.snapshot(direction)[0]
                                         - event_counts_before[0]) if load_events else 0),
                "failed_fetch_xhr": ((load_events.snapshot(direction)[1]
                                      - event_counts_before[1]) if load_events else 0),
            })
            if not (data_progress or position_advanced or content_grew):
                no_progress += 1
            else:
                no_progress = 0
            if no_progress >= 3:
                result["state"] = "incomplete"
                result["reason"] = "three_cycles_without_progress"
                break
        else:
            result["state"] = "incomplete"
            result["reason"] = "benchmark_cycle_limit"
        if result["reason"] == "not_attempted":
            result["state"] = "incomplete"
            result["reason"] = "stopped_before_cycle_limit"
        return result
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "ui_error_or_timeout"
        result["state"] = "interrupted"
        result["reason"] = code
        result["error"] = code
        return result
    finally:
        if load_events is not None:
            load_events.set_phase(None)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)


def collect_benchmark(page, max_cycles=MAX_BENCHMARK_CYCLES):
    if not 1 <= max_cycles <= MAX_INTERMEDIATE_CYCLES:
        raise ValueError("benchmark_cycle_limit_exceeded")
    started = time.monotonic()
    load_events = LoadEventCounter(page)
    result = {"target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "observer_control_visible": False, "profile_available": False,
              "max_cycles_per_list": max_cycles,
              "followers": None, "following": None,
              "integrity_verified": False, "non_followers": None,
              "error": None, "elapsed_seconds": 0}
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        result["observer_control_visible"] = True
        page.goto(f"https://www.instagram.com/{TARGET}/",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        result["profile_available"] = True
        for direction in ("followers", "following"):
            list_result = benchmark_list(page, direction, max_cycles=max_cycles,
                                         ceiling=max_cycles, load_events=load_events)
            result[direction] = list_result
            if list_result["state"] == "interrupted":
                result["error"] = list_result["error"]
                break
            if direction == "followers":
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                refuse_if_gate(page)
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "navigation_error_or_timeout"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def run_benchmark(max_cycles=MAX_BENCHMARK_CYCLES):
    if max_cycles not in (MAX_BENCHMARK_CYCLES, MAX_INTERMEDIATE_CYCLES):
        raise ValueError("benchmark_cycle_limit_exceeded")
    from playwright.sync_api import sync_playwright
    profile = safe_profile_path()
    if not profile.is_dir():
        raise RuntimeError("existing_browser_profile_missing")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=False, locale="en-US", viewport={"width": 390, "height": 800})
        try:
            page = context.pages[0] if context.pages else context.new_page()
            result = collect_benchmark(page, max_cycles=max_cycles)
        finally:
            context.close()
    result["run_id"] = uuid.uuid4().hex
    output_dir = ROOT / "reports" / ("playwright-stage7-intermediate"
                                    if max_cycles == MAX_INTERMEDIATE_CYCLES
                                    else "playwright-stage6-benchmark")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / ("result-" + result["run_id"] + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, result
