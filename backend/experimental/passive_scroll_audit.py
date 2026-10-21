"""Bounded UI scrolling plus passive analysis of already received JSON pages."""

import json
import re
import time
import uuid
from datetime import datetime, timezone

from backend.experimental.passive_list_audit import PassiveProbe, _candidate
from backend.experimental.playwright_browser import (
    OBSERVER, ROOT, TARGET, extract_visible_people, normalized_username,
    observer_visible, page_control_or_title_matches, profile_control,
    refuse_if_gate, safe_profile_path, scroll_geometry, scroll_metrics_and_advance,
)
from backend.experimental.playwright_integral import NetworkSummary

MAX_CYCLES_PER_LIST = 10
MAX_BATCH_USERS = 100
SCROLL_PAUSE_MS = 1100
REPORTS = ROOT / "reports" / "passive-scroll-audit"


def extract_user_batches(document, max_nodes=30_000):
    """Find bounded `users` arrays with numeric IDs; no endpoint assumptions."""
    stack = [document]
    visited = 0
    batches = []
    while stack and visited < max_nodes:
        value = stack.pop()
        visited += 1
        if isinstance(value, dict):
            users = value.get("users")
            if isinstance(users, list) and 0 < len(users) <= MAX_BATCH_USERS:
                rows = [_candidate(user) for user in users]
                if all(rows):
                    cursor = value.get("next_max_id")
                    batches.append({"rows": rows, "cursor_present": bool(cursor),
                                    "has_next_hint": (bool(cursor)
                                                      if "next_max_id" in value else None),
                                    "row_count": len(rows)})
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return batches, bool(stack)


class BatchTracker:
    def __init__(self, probe, *, defer_unmatched=False, max_deferred=500):
        self.probe = probe
        self.offset = {"followers": 0, "following": 0}
        self.known = {"followers": {}, "following": {}}
        self.name_ids = {"followers": {}, "following": {}}
        self.defer_unmatched = defer_unmatched
        self.max_deferred = max_deferred
        self.deferred = {"followers": [], "following": []}
        self.deferred_overflow = {"followers": 0, "following": 0}

    def drain(self, direction, visible_names, *, ordinal_base=0):
        """Consume only newly finished responses; keep correlated list candidates."""
        responses = self.probe.pending[direction]
        start = self.offset[direction]
        self.offset[direction] = len(responses)
        visible = {normalized_username(name) for name in visible_names}
        events = []

        def accept(batch, response_ordinal):
            overlap = sum(name in visible for name, _ in batch["rows"])
            if overlap < min(2, batch["row_count"]):
                return False, None
            fresh = 0
            conflict = False
            for name, identifier in batch["rows"]:
                old = self.known[direction].get(identifier)
                old_id = self.name_ids[direction].get(name)
                if old is None:
                    self.known[direction][identifier] = name
                    fresh += 1
                elif old != name:
                    conflict = True
                if old_id is not None and old_id != identifier:
                    conflict = True
                self.name_ids[direction][name] = identifier
            events.append({"response_ordinal": response_ordinal,
                           "state": "correlated_users_batch",
                           "batch_rows": batch["row_count"],
                           "matched_visible": overlap,
                           "new_structured_ids": fresh,
                           "total_structured_ids": len(self.known[direction]),
                           "cursor_present": batch["cursor_present"],
                           "has_next_hint": batch["has_next_hint"],
                           "correlation_basis": ("single_visible_row" if batch["row_count"] == 1
                                                 else "multiple_visible_rows"),
                           "node_limit_hit": False,
                           "id_username_conflict": conflict})
            return True, "id_username_conflict" if conflict else None

        if self.defer_unmatched:
            remaining = []
            for entry in self.deferred[direction]:
                accepted, issue = accept(entry["batch"], entry["response_ordinal"])
                if not accepted:
                    remaining.append(entry)
                if issue:
                    self.deferred[direction] = remaining
                    return events, issue
            self.deferred[direction] = remaining
        for ordinal in range(start, len(responses)):
            response = responses[ordinal]
            if response.status in (401, 403, 429):
                return events, "network_access_limited"
            if response.status != 200:
                continue
            try:
                body = response.body()
                if len(body) > self.probe.max_body_bytes:
                    events.append({"response_ordinal": ordinal_base + ordinal + 1,
                                   "state": "body_limit"})
                    continue
                batches, truncated = extract_user_batches(json.loads(body))
            except Exception:
                events.append({"response_ordinal": ordinal_base + ordinal + 1,
                               "state": "unreadable_json"})
                continue
            if truncated:
                events.append({"response_ordinal": ordinal_base + ordinal + 1,
                               "state": "json_node_limit"})
            for batch in batches:
                accepted, issue = accept(batch, ordinal_base + ordinal + 1)
                if not accepted and self.defer_unmatched:
                    self.deferred[direction].append({"batch": batch,
                                                     "response_ordinal": ordinal_base + ordinal + 1})
                if issue:
                    return events, issue
        if len(self.deferred[direction]) > self.max_deferred:
            excess = len(self.deferred[direction]) - self.max_deferred
            self.deferred_overflow[direction] += excess
            del self.deferred[direction][:excess]
        return events, None


def audit_list(page, direction, probe, tracker, network):
    started = time.monotonic()
    result = {"direction": direction, "reported_count": None,
              "ui_rows": [], "structured_rows": [], "initial_events": [],
              "cycles": [], "state": "unavailable", "reason": "not_attempted",
              "error": None, "elapsed_seconds": None,
              "integrity_verified": False}
    known_ui = {}
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
        probe.set_phase(direction)
        network.set_phase(direction)
        control.click(timeout=5000)
        page.wait_for_timeout(1200)
        refuse_if_gate(page)
        network.check()
        dialogs = page.get_by_role("dialog")
        if not dialogs.count() or not dialogs.first.is_visible():
            result["reason"] = "list_dialog_unavailable"
            return result
        dialog = dialogs.first
        initial = extract_visible_people(dialog)
        for row in initial["rows"]:
            known_ui[normalized_username(row["username"])] = row
        result["ui_rows"] = list(known_ui.values())
        if not known_ui and result["reported_count"]:
            result["reason"] = "empty_dialog_with_nonzero_count"
            return result
        events, issue = tracker.drain(direction, known_ui)
        result["initial_events"] = events
        if issue:
            raise RuntimeError(issue)
        for index in range(MAX_CYCLES_PER_LIST):
            refuse_if_gate(page)
            network.check()
            before = scroll_geometry(dialog)
            if before is None:
                result.update(state="incomplete", reason="scroll_container_missing")
                break
            advanced = scroll_metrics_and_advance(dialog)
            if advanced is None:
                result.update(state="incomplete", reason="scroll_container_disappeared")
                break
            page.wait_for_timeout(SCROLL_PAUSE_MS)
            refuse_if_gate(page)
            network.check()
            snapshot = extract_visible_people(dialog)
            new_ui = 0
            for row in snapshot["rows"]:
                key = normalized_username(row["username"])
                if key not in known_ui:
                    known_ui[key] = row
                    new_ui += 1
            result["ui_rows"] = list(known_ui.values())
            events, issue = tracker.drain(direction, known_ui)
            if issue:
                raise RuntimeError(issue)
            after = scroll_geometry(dialog)
            result["cycles"].append({"cycle": index + 1,
                                     "new_ui_usernames": new_ui,
                                     "total_ui_usernames": len(known_ui),
                                     "new_structured_ids": sum(e.get("new_structured_ids", 0)
                                                               for e in events),
                                     "total_structured_ids": len(tracker.known[direction]),
                                     "responses_correlated": sum(
                                         e["state"] == "correlated_users_batch" for e in events),
                                     "events": events,
                                     "scroll_top_px": round(after["top"], 1) if after else None,
                                     "scroll_height_px": round(after["height"], 1) if after else None})
        else:
            result.update(state="incomplete", reason="local_cycle_limit")
        if result["reason"] == "not_attempted":
            result.update(state="incomplete", reason="stopped_before_limit")
        return result
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit", "network_authentication_required",
            "network_access_limited", "id_username_conflict"} else "ui_or_response_error"
        result.update(state="interrupted", reason=code, error=code)
        return result
    finally:
        probe.set_phase(None)
        network.set_phase(None)
        structured = tracker.known[direction]
        result["structured_rows"] = [{"id": uid, "username": name}
                                     for uid, name in structured.items()]
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)


def audit_ui(page):
    started = time.monotonic()
    result = {"target": TARGET, "observer": OBSERVER,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "max_cycles_per_list": MAX_CYCLES_PER_LIST,
              "followers": None, "following": None, "error": None,
              "elapsed_seconds": None, "integrity_verified": False}
    probe = PassiveProbe(page)
    network = NetworkSummary(page)
    tracker = BatchTracker(probe)
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
        for direction in ("followers", "following"):
            item = audit_list(page, direction, probe, tracker, network)
            result[direction] = item
            if item["state"] == "interrupted":
                result["error"] = item["error"]
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            refuse_if_gate(page)
    except Exception as exc:
        result["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in {
            "observer_session_not_confirmed_in_ui", "target_profile_unavailable",
            "authentication_or_verification_required", "authentication_required",
            "verification_or_access_limit"} else "navigation_error_or_timeout"
    result["network_events"] = network.counts
    result["response_overflow"] = probe.overflow
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def run_once():
    """One real attempt, no retry. The caller must already have authorization."""
    from playwright.sync_api import sync_playwright

    profile = safe_profile_path()
    if not profile.is_dir():
        raise RuntimeError("existing_browser_profile_missing")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=False, locale="en-US", viewport={"width": 390, "height": 800})
        try:
            page = context.pages[0] if context.pages else context.new_page()
            result = audit_ui(page)
        finally:
            context.close()
    REPORTS.mkdir(parents=True, exist_ok=True)
    output = REPORTS / f"result-{uuid.uuid4().hex}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, result
