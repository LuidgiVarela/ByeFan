"""Experimental UI traversal with passive IDs from browser-received JSON.

No endpoint is called directly. A run is explicit, single-shot, bounded, and
never promotes a result to complete without a second run and owner reference.
"""

import time
import uuid
from datetime import datetime, timezone

from backend.collector.models import ListResult, User
from backend.experimental.passive_list_audit import PassiveProbe
from backend.experimental.passive_scroll_audit import BatchTracker
from backend.experimental.playwright_browser import (
    OBSERVER, ROOT, TARGET, extract_visible_people, normalized_username,
    observer_visible, page_control_or_title_matches, profile_control,
    refuse_if_gate_batched as refuse_if_gate, safe_profile_path,
    scroll_is_stably_terminal, scroll_metrics_and_advance,
)
from backend.experimental.playwright_integral import (
    NetworkSummary, parse_visible_count, save_checkpoint,
)
from backend.services.comparator import non_followers
from backend.services.coverage import list_coverage, report_coverage, validate_target

WORK_BUDGET_SECONDS = 710
MAX_CYCLES_PER_LIST = 300
SCROLL_PAUSE_MS = 1100
SETTLE_SECONDS = 12
SETTLE_POLL_MS = 500
REPORTS = ROOT / "reports" / "hybrid-collector"
GATE_ERRORS = {
    "authentication_or_verification_required", "authentication_required",
    "verification_or_access_limit", "network_authentication_required",
    "network_access_limited", "id_username_conflict",
}


def check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise RuntimeError("owner_cancelled")


def remaining_timeout_ms(deadline, ceiling_ms=30_000):
    remaining = int((deadline - time.monotonic() - 3) * 1000)
    if remaining <= 0:
        raise RuntimeError("global_time_limit")
    return min(ceiling_ms, max(1000, remaining))


def drain_and_release(tracker, direction, visible_names, response_totals):
    """Drain finished responses and release Playwright response objects each cycle."""
    if hasattr(tracker.probe, "check"):
        tracker.probe.check()
    prior = response_totals[direction]
    events, issue = tracker.drain(direction, visible_names, ordinal_base=prior)
    # response.body() may dispatch new requestfinished callbacks. Release only
    # the snapshot inspected by drain(), never arrivals appended during it.
    consumed = tracker.offset[direction]
    response_totals[direction] += consumed
    del tracker.probe.pending[direction][:consumed]
    tracker.offset[direction] = 0
    if hasattr(tracker.probe, "check"):
        tracker.probe.check()
    return events, issue


def assess_list(item, response_overflow=0):
    """Assess evidence without declaring completeness from count equality alone."""
    if item["state"] in ("interrupted", "unavailable"):
        return item
    if not item["terminal_observed"]:
        item.update(state="incomplete", reason=item.get("reason") or "ui_end_not_observed")
        return item
    if (response_overflow or item.get("parse_limit_events")
            or item.get("deferred_overflow") or item.get("pending_responses_unprocessed")
            or item.get("inflight_requests_at_end")):
        item.update(state="incomplete", reason="structured_response_loss_possible")
        return item
    rows = item["structured_rows"]
    ids = [row.get("id") for row in rows]
    try:
        names = [normalized_username(row.get("username", "")) for row in rows]
        visible = {normalized_username(row["username"]) for row in item["ui_rows"]}
    except (ValueError, KeyError, TypeError):
        item.update(state="incomplete", reason="structured_identity_ambiguity")
        return item
    if (any(not isinstance(i, str) or not i.isascii() or not i.isdigit() for i in ids)
            or len(set(ids)) != len(ids) or len(set(names)) != len(names)
            or any(not name for name in names)):
        item.update(state="incomplete", reason="structured_identity_ambiguity")
    elif set(names) != visible:
        item.update(state="incomplete", reason="ui_structured_mismatch")
    elif (type(item["reported_count"]) is not int
          or item["reported_count"] != len(rows)):
        item.update(state="incomplete", reason="profile_count_mismatch")
    elif item.get("counts_stable") is not True:
        item.update(state="incomplete", reason="profile_count_changed_or_unavailable")
    else:
        item.update(state="terminal_observed_unverified",
                    reason="integrity_unverified_without_independent_reference_and_repeat")
    return item


def _append_events(item, events):
    item["structured_events"].extend(events)
    item["parse_limit_events"] += sum(
        event["state"] in ("body_limit", "unreadable_json", "json_node_limit")
        or event.get("node_limit_hit", False) for event in events)


def latest_pagination_hint(events):
    correlated = [e for e in events if e["state"] == "correlated_users_batch"]
    if not correlated:
        return None
    ordinal = max(e["response_ordinal"] for e in correlated)
    hints = {e.get("has_next_hint") for e in correlated
             if e["response_ordinal"] == ordinal}
    # Conflicting/absent metadata cannot establish a last page.
    return next(iter(hints)) if len(hints) == 1 else None


def settle_without_scrolling(page, dialog, direction, deadline, network,
                             tracker, response_totals, known_ui, item, baseline,
                             cancelled=None):
    """Give delayed DOM/network delivery time, without another scroll or request."""
    started = time.monotonic()
    end = min(deadline - 3, started + SETTLE_SECONDS)
    outcome = {"reason": "quiet_timeout", "seconds": 0, "polls": 0,
               "new_ui_usernames": 0, "new_structured_ids": 0,
               "loading": False, "geometry": baseline}
    while time.monotonic() < end:
        check_cancelled(cancelled)
        page.wait_for_timeout(min(SETTLE_POLL_MS,
                                  max(1, int((end - time.monotonic()) * 1000))))
        check_cancelled(cancelled)
        refuse_if_gate(page)
        network.check()
        snapshot = extract_visible_people(dialog, include_geometry=True)
        for row in snapshot["rows"]:
            name = normalized_username(row["username"])
            if name not in known_ui:
                outcome["new_ui_usernames"] += 1
            known_ui[name] = row
        events, issue = drain_and_release(tracker, direction, known_ui, response_totals)
        _append_events(item, events)
        if issue:
            raise RuntimeError(issue)
        outcome["new_structured_ids"] += sum(e.get("new_structured_ids", 0) for e in events)
        outcome["polls"] += 1
        outcome["loading"] = snapshot["loading"]
        outcome["geometry"] = snapshot["geometry"]
        geometry = snapshot["geometry"]
        grew = bool(geometry and baseline and geometry["height"] > baseline["height"] + 1)
        if outcome["new_ui_usernames"] or outcome["new_structured_ids"] or grew:
            outcome["reason"] = "delayed_progress"
            break
    else:
        if time.monotonic() >= deadline - 3:
            outcome["reason"] = "global_time_limit"
    item["ui_rows"] = list(known_ui.values())
    item["structured_rows"] = [{"id": uid, "username": name}
                               for uid, name in tracker.known[direction].items()]
    outcome["seconds"] = round(time.monotonic() - started, 3)
    return outcome


def classify_stop(item, settled, pending_responses):
    """A bottom with short counts or a next-page hint is truncation, not an end."""
    geometry = settled["geometry"]
    bottom = bool(geometry and geometry["at_bottom"])
    item["visual_bottom_observed"] = bottom
    if settled["reason"] == "global_time_limit":
        return "global_time_limit"
    if settled["loading"] or pending_responses:
        return "loading_without_progress"
    if latest_pagination_hint(item["structured_events"]) is True:
        return "pagination_pending_without_progress"
    if not bottom:
        return "stalled_away_from_bottom"
    if type(item["reported_count"]) is not int or item["reported_count"] != len(item["ui_rows"]):
        return "visual_end_count_mismatch"
    if len(item["structured_rows"]) != len(item["ui_rows"]):
        return "ui_structured_mismatch"
    item["terminal_observed"] = True
    return "awaiting_count_stability_check"


def stop_at_coverage(item, tracker, direction, target_percent):
    """An optional partial stopping rule, never an integrity/comparison gate."""
    validate_target(target_percent)
    if target_percent == 100:
        return False  # Full mode still checks end evidence; counts alone never end it.
    item["pending_responses_unprocessed"] = len(tracker.probe.pending[direction])
    item["inflight_requests_at_end"] = (tracker.probe.inflight_count(direction)
        if hasattr(tracker.probe, "inflight_count") else 0)
    item["deferred_overflow"] = tracker.deferred_overflow[direction]
    coverage = list_coverage(item, target_percent, require_stable=False,
                            response_overflow=getattr(tracker.probe, "overflow", {}).get(direction, 0))
    if not coverage["target_met"]:
        return False
    item.update(state="incomplete", reason="coverage_target_reached",
                terminal_observed=False, integrity_verified=False)
    return True


def collect_hybrid_list(page, direction, deadline, network, tracker,
                        response_totals, checkpoint, coverage_target=100, cancelled=None):
    validate_target(coverage_target)
    started = time.monotonic()
    item = {"direction": direction, "reported_count": None,
            "reported_count_after": None, "counts_stable": False,
            "ui_rows": [], "structured_rows": [], "structured_events": [],
            "parse_limit_events": 0, "deferred_unmatched_batches": 0,
            "deferred_overflow": 0, "cycles": [], "terminal_observed": False,
            "visual_bottom_observed": False, "settlement_events": [],
            "coverage_target_percent": coverage_target,
            "state": "unavailable", "reason": "not_attempted", "error": None,
            "elapsed_seconds": 0, "integrity_verified": False}
    known_ui = {}
    try:
        check_cancelled(cancelled)
        if time.monotonic() >= deadline - 3:
            item.update(state="incomplete", reason="global_time_limit")
            return item
        refuse_if_gate(page)
        network.check()
        control = profile_control(page, direction)
        if control is None:
            item["reason"] = "list_control_unavailable"
            return item
        item["reported_count"] = parse_visible_count(control)
        network.set_phase(direction)
        tracker.probe.set_phase(direction)
        control.click(timeout=remaining_timeout_ms(deadline, 5000))
        page.wait_for_timeout(1200)
        check_cancelled(cancelled)
        refuse_if_gate(page)
        network.check()
        dialogs = page.get_by_role("dialog")
        if not dialogs.count() or not dialogs.first.is_visible():
            item["reason"] = "list_dialog_unavailable"
            return item
        dialog = dialogs.first
        initial = extract_visible_people(dialog)
        for row in initial["rows"]:
            known_ui[normalized_username(row["username"])] = row
        item["ui_rows"] = list(known_ui.values())
        events, issue = drain_and_release(tracker, direction, known_ui, response_totals)
        _append_events(item, events)
        if issue:
            raise RuntimeError(issue)
        item["structured_rows"] = [{"id": uid, "username": name}
                                   for uid, name in tracker.known[direction].items()]
        checkpoint(item)
        if not known_ui and item["reported_count"]:
            settled = settle_without_scrolling(
                page, dialog, direction, deadline, network, tracker,
                response_totals, known_ui, item, None, cancelled)
            item["settlement_events"].append({**settled, "phase": "initial_load"})
            checkpoint(item)
            if not known_ui:
                item["reason"] = ("global_time_limit" if settled["reason"] == "global_time_limit"
                                  else "initial_load_without_users")
                return item
        item.update(state="collecting", reason="in_progress")
        if stop_at_coverage(item, tracker, direction, coverage_target):
            return item
        bottom_stable = stalled = 0
        for index in range(MAX_CYCLES_PER_LIST):
            check_cancelled(cancelled)
            cycle_started = time.monotonic()
            if cycle_started >= deadline - 3:
                item.update(state="incomplete", reason="global_time_limit")
                break
            refuse_if_gate(page)
            network.check()
            scroll_started = time.monotonic()
            advanced = scroll_metrics_and_advance(dialog)
            scroll_seconds = time.monotonic() - scroll_started
            if advanced is None:
                item.update(state="incomplete", reason="scroll_container_disappeared")
                break
            before = {"top": advanced["before"], "height": advanced["height"]}
            wait_started = time.monotonic()
            page.wait_for_timeout(SCROLL_PAUSE_MS)
            check_cancelled(cancelled)
            wait_seconds = time.monotonic() - wait_started
            refuse_if_gate(page)
            network.check()
            read_started = time.monotonic()
            snapshot = extract_visible_people(dialog, include_geometry=True)
            read_seconds = time.monotonic() - read_started
            new_ui = 0
            for row in snapshot["rows"]:
                name = normalized_username(row["username"])
                if name not in known_ui:
                    new_ui += 1
                known_ui[name] = row
            item["ui_rows"] = list(known_ui.values())
            events, issue = drain_and_release(tracker, direction, known_ui,
                                              response_totals)
            _append_events(item, events)
            if issue:
                raise RuntimeError(issue)
            after = snapshot["geometry"]
            new_ids = sum(event.get("new_structured_ids", 0) for event in events)
            position_advanced = bool(after and after["top"] > before["top"] + 1)
            content_grew = bool(after and after["height"] > before["height"] + 1)
            item["cycles"].append({
                "cycle": index + 1,
                "total_seconds": round(time.monotonic() - cycle_started, 3),
                "dom_read_seconds": round(read_seconds, 3),
                "wait_seconds": round(wait_seconds, 3),
                "scroll_seconds": round(scroll_seconds, 3),
                "new_ui_usernames": new_ui, "total_ui_usernames": len(known_ui),
                "new_structured_ids": new_ids,
                "total_structured_ids": len(tracker.known[direction]),
                "scroll_top_px": round(after["top"], 1) if after else None,
                "scroll_height_px": round(after["height"], 1) if after else None,
                "position_advanced": position_advanced,
                "content_grew": content_grew,
                "loading": snapshot["loading"],
                "correlated_batches": sum(e["state"] == "correlated_users_batch"
                                          for e in events),
            })
            item["structured_rows"] = [
                {"id": uid, "username": name}
                for uid, name in tracker.known[direction].items()]
            checkpoint(item)
            if stop_at_coverage(item, tracker, direction, coverage_target):
                break
            if scroll_is_stably_terminal(advanced, after, new_ui + new_ids):
                bottom_stable += 1
            else:
                bottom_stable = 0
            stalled = stalled + 1 if not (new_ui or new_ids or position_advanced
                                          or content_grew) else 0
            if bottom_stable >= 3 or stalled >= 3:
                settled = settle_without_scrolling(
                    page, dialog, direction, deadline, network, tracker,
                    response_totals, known_ui, item, after, cancelled)
                item["settlement_events"].append(settled)
                checkpoint(item)
                if settled["reason"] == "delayed_progress":
                    bottom_stable = stalled = 0
                    continue
                reason = classify_stop(item, settled, len(tracker.probe.pending[direction]))
                item.update(state="incomplete", reason=reason)
                break
        else:
            item.update(state="incomplete", reason="local_cycle_limit")
        return item
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in GATE_ERRORS | {"global_time_limit", "owner_cancelled"} \
            else "ui_or_response_error"
        item.update(state="interrupted", reason=code, error=code)
        return item
    finally:
        tracker.probe.set_phase(None)
        network.set_phase(None)
        item["structured_rows"] = [
            {"id": uid, "username": name}
            for uid, name in tracker.known[direction].items()]
        item["pending_responses_unprocessed"] = len(tracker.probe.pending[direction])
        item["deferred_unmatched_batches"] = len(tracker.deferred[direction])
        item["deferred_overflow"] = tracker.deferred_overflow[direction]
        item["elapsed_seconds"] = round(time.monotonic() - started, 3)
        checkpoint(item)


def collect_hybrid(page, report, deadline, checkpoint, coverage_target=100, cancelled=None):
    probe = PassiveProbe(page, max_responses=100, bind_start_phase=True)
    tracker = BatchTracker(probe, defer_unmatched=True)
    network = NetworkSummary(page)
    response_totals = {"followers": 0, "following": 0}
    try:
        check_cancelled(cancelled)
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded",
                  timeout=remaining_timeout_ms(deadline))
        page.wait_for_timeout(1500)
        check_cancelled(cancelled)
        refuse_if_gate(page)
        if not observer_visible(page):
            raise RuntimeError("observer_session_not_confirmed_in_ui")
        page.goto(f"https://www.instagram.com/{TARGET}/",
                  wait_until="domcontentloaded", timeout=remaining_timeout_ms(deadline))
        page.wait_for_timeout(3000)
        check_cancelled(cancelled)
        refuse_if_gate(page)
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        for direction in ("followers", "following"):
            check_cancelled(cancelled)
            probe.check()
            if time.monotonic() >= deadline - 8:
                report["error"] = "global_time_limit"
                break

            def on_checkpoint(item, key=direction):
                report[key] = item
                checkpoint()

            item = collect_hybrid_list(page, direction, deadline, network,
                                       tracker, response_totals, on_checkpoint, coverage_target, cancelled)
            report[direction] = item
            checkpoint()
            if item["state"] == "interrupted":
                report["error"] = item["error"]
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            refuse_if_gate(page)
            network.check()
        check_cancelled(cancelled)
        if not report["error"] and report["followers"] and report["following"] and time.monotonic() < deadline - 5:
            for direction in ("followers", "following"):
                control = profile_control(page, direction)
                item = report[direction]
                item["reported_count_after"] = parse_visible_count(control) if control else None
                item["counts_stable"] = (item["reported_count_after"] is not None
                                         and item["reported_count_after"] == item["reported_count"])
                assess_list(item, probe.overflow[direction])
                checkpoint()
    except Exception as exc:
        code = str(exc)
        report["error"] = code if type(exc) is RuntimeError and code in GATE_ERRORS | {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "global_time_limit", "owner_cancelled"} else "navigation_error_or_timeout"
    finally:
        if probe.restriction:
            report["error"] = probe.restriction
        for direction in ("followers", "following"):
            if report.get(direction):
                report[direction]["pending_responses_unprocessed"] = len(probe.pending[direction])
                report[direction]["inflight_requests_at_end"] = probe.inflight_count(direction)
        report["network_events"] = network.counts
        report["responses_processed"] = response_totals
        report["response_overflow"] = probe.overflow
        report["coverage"] = report_coverage(report, coverage_target)
        checkpoint()
    return report


def run_once(coverage_target=98, *, viewport_height=800, cancelled=None, on_progress=None):
    """Explicit live run only. No retries, extra accounts or hidden requests."""
    from playwright.sync_api import sync_playwright
    from backend.pilot.worker import WorkerLock

    validate_target(coverage_target)
    if type(viewport_height) is not int or viewport_height not in (800, 1000, 1200):
        raise ValueError("unsupported_experimental_viewport_height")
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    REPORTS.mkdir(parents=True, exist_ok=True)
    checkpoint_path = REPORTS / f"checkpoint-{run_id}.json"
    report = {"run_id": run_id, "target": TARGET, "observer": OBSERVER,
              "collector_version": 4, "settle_seconds": SETTLE_SECONDS,
              "coverage_target_percent": coverage_target,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "max_runtime_seconds": 720, "followers": None, "following": None,
              "error": None, "integrity_verified": False,
              "non_followers": None, "comparison_status": "insufficient_data",
              "elapsed_seconds": 0}

    report["viewport"] = {"width": 390, "height": viewport_height}

    def checkpoint():
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_checkpoint(checkpoint_path, report)
        if on_progress is not None:
            on_progress(report)

    checkpoint()
    try:
        check_cancelled(cancelled)
        profile = safe_profile_path()
        if not profile.is_dir():
            raise RuntimeError("existing_browser_profile_missing")
        # Same OS lock for the CLI and local HTTP adapter. A failed acquisition
        # must stop before launching Chromium or accessing Instagram.
        with WorkerLock(profile.parent / "hybrid-session"), sync_playwright() as playwright:
            check_cancelled(cancelled)
            context = playwright.chromium.launch_persistent_context(
                str(profile), headless=False, locale="en-US",
                viewport=report["viewport"])
            try:
                page = context.pages[0] if context.pages else context.new_page()
                collect_hybrid(page, report, started + WORK_BUDGET_SECONDS, checkpoint, coverage_target, cancelled)
            finally:
                context.close()
    except Exception as exc:
        allowed = {"existing_browser_profile_missing", "owner_cancelled", "worker_already_active"}
        report["error"] = (str(exc) if type(exc) is RuntimeError and str(exc) in allowed
                           else "browser_start_or_close_failed")
    report["coverage"] = report_coverage(report, coverage_target)
    checkpoint()
    output = REPORTS / f"result-{run_id}.json"
    save_checkpoint(output, report)
    return output, checkpoint_path, report


def validate_repeated_reports(first, second, reference):
    """Allow ID comparison only with two matching runs and an independent ID set."""
    invalid = {"comparison_status": "insufficient_data", "non_followers": None}
    if not all(isinstance(obj, dict) for obj in (first, second, reference)):
        return invalid
    if any(report.get("error") or report.get("target") != TARGET
           or report.get("observer") != OBSERVER for report in (first, second)):
        return invalid
    if (first.get("run_id") == second.get("run_id")
            or reference.get("username") != TARGET
            or reference.get("source") != "independent_owner_reference"):
        return invalid
    lists = {}
    for direction in ("followers", "following"):
        records = []
        for report in (first, second):
            item = report.get(direction)
            if not isinstance(item, dict) or assess_list(dict(item), report.get(
                    "response_overflow", {}).get(direction, 0))["state"] \
                    != "terminal_observed_unverified":
                return invalid
            records.append(item["structured_rows"])
        ids = reference.get(direction + "_ids")
        if (not isinstance(ids, list) or any(not isinstance(i, str) or not i.isascii()
                                            or not i.isdigit() for i in ids)
                or len(set(ids)) != len(ids)):
            return invalid
        id_sets = [{row["id"] for row in rows} for rows in records]
        if id_sets[0] != id_sets[1] or id_sets[0] != set(ids):
            return invalid
        lists[direction] = ListResult(
            users=[User(row["id"], row["username"]) for row in records[0]],
            reported_count=first[direction]["reported_count"],
            state="complete", reason="independent_reference_and_repeat_verified",
            terminal_page=True, counts_stable=True, integrity_verified=True)
    users = non_followers(lists["followers"], lists["following"])
    return {"comparison_status": "verified_by_independent_reference_and_repeat",
            "non_followers": [{"id": user.id, "username": user.username}
                              for user in users]}
