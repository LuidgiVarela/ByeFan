"""Bounded anonymous inspection of normal browser traffic, starting before goto.

No constructed Instagram requests, cookies, storage state, raw bodies or URLs
are persisted. Profile objects are schema evidence, never proof of membership.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from backend.experimental.passive_list_audit import _candidate
from backend.experimental.playwright_browser import (
    ROOT, TARGET, extract_visible_people, page_control_or_title_matches,
    profile_control, refuse_if_gate_batched, scroll_metrics_and_advance,
)

MAX_USERS = 10
MAX_SCROLLS = 3
MAX_SECONDS = 90
MAX_RESPONSES = 100
MAX_BODY = 12_000_000
MAX_TOTAL_BODY = 32_000_000
MAX_NODES = 30_000
BROWSER_CHANNELS = {"chromium": None, "chrome": "chrome", "msedge": "msedge"}
VIEWPORTS = {"narrow": {"width": 390, "height": 800},
             "desktop": {"width": 1365, "height": 900}}
PHASES = ("initial", "followers", "following", "between")
HINTS = {"users", "edge_followed_by", "edge_follow", "next_max_id", "page_info",
         "has_more", "has_next_page", "end_cursor",
         "xdt_api__v1__friendships__followers", "xdt_api__v1__friendships__following"}
BLOCK_CODES = {"login_required", "challenge_required", "checkpoint_required",
               "feedback_required", "consent_required", "rate_limit_error"}
SAFE_ERRORS = {"authentication_or_verification_required", "authentication_required",
               "verification_or_access_limit", "network_access_restricted",
               "structured_access_restricted", "work_budget_reached",
               "target_profile_unavailable", "unrecognized_dialog_present",
               "login_prompt_not_dismissible", "login_prompt_reappeared",
               "login_prompt_not_closed", "list_dialog_unconfirmed"}


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.buffer = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = not dict(attrs).get("src") and len(self.scripts) < 300
            self.buffer = []

    def handle_data(self, text):
        if self.active:
            self.buffer.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.buffer))
            self.active = False
            self.buffer = []


def decode_json(text):
    """JSON, known anti-XSSI prefixes or NDJSON; never evaluate JavaScript."""
    text = text.lstrip()
    for prefix in ("for (;;);", ")]}'"):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
    try:
        return [json.loads(text)]
    except (ValueError, RecursionError):
        documents = []
        for line in text.splitlines()[:200]:
            try:
                documents.append(json.loads(line))
            except (ValueError, RecursionError):
                continue
        return documents


def inspect_document(document):
    stack = [document]
    nodes = count = 0
    hints = set()
    target_edges = []
    candidates = {}
    restricted = False
    while stack and nodes < MAX_NODES:
        item = stack.pop()
        nodes += 1
        if isinstance(item, dict):
            for key in ("message", "error_type", "error"):
                if isinstance(item.get(key), str) and item[key].lower() in BLOCK_CODES:
                    restricted = True
            candidate = _candidate(item)
            if candidate:
                count += 1
                name, identifier = candidate
                if len(candidates) < 2000 or name in candidates:
                    candidates.setdefault(name, set()).add(identifier)
            hints.update(HINTS.intersection(item))
            if str(item.get("username", "")).casefold() == TARGET:
                for key in ("edge_followed_by", "edge_follow"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        edges = value.get("edges")
                        target_edges.append({"direction": "followers" if key == "edge_followed_by" else "following",
                                             "edge_entries": len(edges) if isinstance(edges, list) else None,
                                             "reported_count": value.get("count") if type(value.get("count")) is int else None,
                                             "has_next_page": (value.get("page_info") or {}).get("has_next_page")
                                             if isinstance(value.get("page_info"), dict) and type(value["page_info"].get("has_next_page")) is bool else None})
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return {"candidate_objects": count, "schema_hints": sorted(hints),
            "target_edges": target_edges[:20], "node_limit_hit": bool(stack),
            "restricted": restricted}, candidates


class AnonymousProbe:
    def __init__(self, page):
        self.phase = "initial"
        self.starts = {}
        self.items = []
        self.identities = {}
        self.restriction = None
        self.restriction_evidence = None
        self.started = time.monotonic()
        self.finished = self.failed = self.over_limit = self.bytes_read = 0
        self.header_checks = 0
        self.script_responses = 0
        page.on("request", self.begin)
        page.on("response", self.headers)
        page.on("requestfinished", self.finish)
        page.on("requestfailed", self.fail)

    @staticmethod
    def relevant(request):
        host = (urlsplit(request.url).hostname or "").lower()
        return (host == "instagram.com" or host.endswith(".instagram.com")) and request.resource_type in ("document", "fetch", "xhr", "script")

    def begin(self, request):
        if self.relevant(request):
            if len(self.starts) < 500:
                self.starts[request] = self.phase
            else:
                self.over_limit += 1

    def headers(self, response):
        if self.relevant(response.request):
            self.header_checks += 1
            if response.status in (401, 403, 429):
                self.restriction = "network_access_restricted"
                self.restriction_evidence = {"status": response.status,
                                             "start_phase": self.starts.get(response.request),
                                             "resource_type": response.request.resource_type}

    def fail(self, request):
        if request in self.starts:
            self.starts.pop(request)
            self.failed += 1

    def scan(self, text, kind):
        scripts = []
        if kind == "html":
            parser = InlineScripts()
            parser.feed(text)
            scripts = parser.scripts
        else:
            scripts = [text]
        summaries = []
        parsed = 0
        for script in scripts:
            for document in decode_json(script):
                parsed += 1
                item, candidates = inspect_document(document)
                if item["restricted"]:
                    self.restriction = "structured_access_restricted"
                for name, ids in candidates.items():
                    if len(self.identities) < 2000 or name in self.identities:
                        self.identities.setdefault(name, set()).update(ids)
                summaries.append(item)
        return {"inline_scripts": len(scripts) if kind == "html" else 0,
                "parsed_documents": parsed,
                "candidate_objects": sum(x["candidate_objects"] for x in summaries),
                "schema_hints": sorted({hint for x in summaries for hint in x["schema_hints"]}),
                "target_edges": [edge for x in summaries for edge in x["target_edges"]][:20],
                "node_limit_hit": any(x["node_limit_hit"] for x in summaries)}

    def finish(self, request):
        phase = self.starts.pop(request, None)
        if phase is None:
            return
        self.finished += 1
        # Bundle inventory must not exhaust the budget for actual data responses.
        if request.resource_type == "script":
            self.script_responses += 1
            return
        if len(self.items) >= MAX_RESPONSES:
            self.over_limit += 1
            return
        record = {"start_phase": phase, "finish_phase": self.phase,
                  "resource_type": request.resource_type,
                  "elapsed_seconds": round(time.monotonic() - self.started, 3)}
        self.items.append(record)
        try:
            response = request.response()
            if response is None:
                record["body_status"] = "missing_response"
                return
            record["status"] = response.status
            content_type = response.header_value("content-type") or ""
            kind = "html" if "html" in content_type else "json" if "json" in content_type else "other"
            record["content_kind"] = kind
            if response.status != 200 or self.bytes_read >= MAX_TOTAL_BODY:
                record["body_status"] = "status_or_total_budget"
                return
            length = response.header_value("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY:
                record["body_status"] = "oversized_header"
                return
            # Ordinary JS bundles are inventoried, not executed or deobfuscated.
            if request.resource_type == "script" and kind == "other":
                record["body_status"] = "javascript_not_parsed"
                return
            body = response.body()
            self.bytes_read += len(body)
            record["body_bytes"] = len(body)
            if len(body) > MAX_BODY or self.bytes_read > MAX_TOTAL_BODY:
                record["body_status"] = "oversized_body"
                return
            record.update(self.scan(body.decode("utf-8", errors="replace"), kind))
            record["body_status"] = "inspected"
        except Exception:
            record["body_status"] = "unreadable"

    def check(self):
        if self.restriction:
            raise RuntimeError(self.restriction)

    def report(self):
        return {"finished": self.finished, "failed": self.failed,
                "script_responses_not_parsed": self.script_responses,
                "response_headers_checked": self.header_checks,
                "pending": len(self.starts), "over_limit": self.over_limit,
                "body_bytes_read": self.bytes_read, "restriction": self.restriction,
                "restriction_evidence": self.restriction_evidence,
                "responses": self.items,
                "limits": {"responses": MAX_RESPONSES, "body_bytes": MAX_BODY,
                           "total_body_bytes": MAX_TOTAL_BODY, "nodes_per_document": MAX_NODES},
                "candidate_counts_are_occurrences_not_unique_members": True}


def check(page, probe, deadline):
    probe.check()
    if time.monotonic() >= deadline:
        raise RuntimeError("work_budget_reached")
    refuse_if_gate_batched(page)
    # Stop on explicit visible restrictions not covered by the shared phrases.
    extra = page.evaluate("""() => {
      const text = (document.body?.innerText || '').toLowerCase();
      return ['we limit certain things you can see', 'confirme que é você',
        'tente novamente mais tarde', 'limitamos a frequência', 'verifique sua identidade']
        .some(x => text.includes(x)) || [...document.querySelectorAll('iframe')]
        .some(el => /captcha|challenge/i.test(el.src) && el.getClientRects().length);
    }""")
    if extra:
        raise RuntimeError("verification_or_access_limit")


def wait_checked(page, probe, deadline, milliseconds):
    until = min(deadline, time.monotonic() + milliseconds / 1000)
    while time.monotonic() < until:
        check(page, probe, deadline)
        page.wait_for_timeout(min(100, max(1, (until - time.monotonic()) * 1000)))
    check(page, probe, deadline)


def click_failure_hints(exc):
    """Reduce Playwright's potentially sensitive call log to fixed categories."""
    message = str(exc).lower()
    patterns = {"pointer_intercepted": "intercepts pointer events",
                "disabled": "element is not enabled", "unstable": "element is not stable",
                "not_visible": "element is not visible", "outside_viewport": "outside of the viewport",
                "detached": "not attached", "strict_locator": "strict mode violation"}
    return [key for key, phrase in patterns.items() if phrase in message]


def control_diagnostic(control):
    """Read-only actionability evidence: no text, attributes, URLs or screenshot."""
    try:
        result = control.evaluate("""el => {
          const r = el.getBoundingClientRect();
          const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left)) / 2;
          const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top)) / 2;
          const intersects = r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
          const hit = intersects ? document.elementFromPoint(x, y) : null;
          const dialogs = [...document.querySelectorAll('[role="dialog"]')]
            .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
          const dialogHints = dialogs.slice(0, 5).map(node => {
            const text = (node.innerText || '').toLowerCase();
            const labels = [...node.querySelectorAll('button,[role="button"],a')]
              .map(button => (button.innerText || '').trim().toLowerCase());
            return {
              cookie_text: /cookie|biscoito/.test(text),
              login_text: /log in|login|sign in|entrar no instagram|iniciar sess/.test(text),
              mandatory_login_text: /you must log in|login required|log in to continue|sign in to continue|faça login para continuar/.test(text),
              verification_text: /captcha|verify|verification|confirm it's you|verifique|confirme que/.test(text),
              password_input: !!node.querySelector('input[type="password"]'),
              optional_cookie_rejection_control: labels.some(label => [
                'decline optional cookies', 'only allow essential cookies', 'reject optional cookies',
                'recusar cookies opcionais', 'permitir apenas cookies essenciais'
              ].includes(label))
            };
          });
          return {available: true, connected: el.isConnected,
            disabled: el.matches(':disabled') || el.closest('[aria-disabled="true"]') !== null,
            pointer_events_none: getComputedStyle(el).pointerEvents === 'none',
            in_viewport: intersects, has_area: r.width > 0 && r.height > 0,
            center_hits_control: !!hit && (el === hit || el.contains(hit)),
            hit_inside_dialog: !!hit && !!hit.closest('[role="dialog"]'),
            visible_dialog_count: dialogs.length, dialog_hints: dialogHints};
        }""", timeout=500)
        return result if isinstance(result, dict) else {"available": False}
    except Exception:
        return {"available": False}


def resolve_pre_list_dialog(page, probe, deadline, diagnostic, history):
    """Dismiss one recognizable invitation through its official Close button.

    Never dismiss a password form, explicit access requirement, challenge or
    unknown dialog. Reappearing invitations stop the run instead of looping.
    """
    if not diagnostic.get("visible_dialog_count", 0):
        return
    check(page, probe, deadline)
    hints = diagnostic.get("dialog_hints", [])
    if any(hint.get("verification_text") for hint in hints):
        raise RuntimeError("verification_or_access_limit")
    if any(hint.get("password_input") or hint.get("mandatory_login_text") for hint in hints):
        raise RuntimeError("authentication_required")
    if diagnostic["visible_dialog_count"] != 1 or len(hints) != 1 \
            or not hints[0].get("login_text") or hints[0].get("cookie_text"):
        raise RuntimeError("unrecognized_dialog_present")
    if history:
        raise RuntimeError("login_prompt_reappeared")
    dialogs = page.get_by_role("dialog")
    visible = [dialogs.nth(i) for i in range(min(dialogs.count(), 10)) if dialogs.nth(i).is_visible()]
    if len(visible) != 1:
        raise RuntimeError("unrecognized_dialog_present")
    close = visible[0].get_by_role("button", name=re.compile(r"^(close|fechar|dismiss)$", re.I))
    choices = [close.nth(i) for i in range(min(close.count(), 10))
               if close.nth(i).is_visible() and close.nth(i).is_enabled()]
    if len(choices) != 1:
        raise RuntimeError("login_prompt_not_dismissible")
    event = {"kind": "login_invitation", "action": "official_close_button",
             "attempted": True, "closed": False}
    history.append(event)
    check(page, probe, deadline)
    choices[0].click(timeout=3000)
    wait_checked(page, probe, deadline, 600)
    if any(dialogs.nth(i).is_visible() for i in range(min(dialogs.count(), 10))):
        raise RuntimeError("login_prompt_not_closed")
    event["closed"] = True


def classify_opened_dialog(dialog, direction):
    """A role=dialog is not proof that a relationship list opened."""
    evidence = dialog.evaluate("""(node, direction) => {
      const text = (node.innerText || '').toLowerCase();
      const expected = direction === 'followers' ? ['followers', 'seguidores'] : ['following', 'seguindo'];
      const headings = [...node.querySelectorAll('h1,h2,h3,[role="heading"]')]
        .map(el => (el.innerText || '').trim().toLowerCase());
      const label = (node.getAttribute('aria-label') || '').trim().toLowerCase();
      return {
        password_input: !!node.querySelector('input[type="password"]'),
        login_text: /log in|login|sign in|entrar no instagram|iniciar sess/.test(text),
        verification_text: /captcha|verify|verification|confirm it's you|verifique|confirme que/.test(text),
        expected_list_heading: headings.some(value => expected.includes(value)) || expected.includes(label),
        loading: !!node.querySelector('[role="progressbar"],[aria-busy="true"]')
      };
    }""", direction)
    return evidence


def collect(page):
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    probe = AnonymousProbe(page)  # before the first navigation, including document
    report = {"target": TARGET, "authenticated": False, "fresh_context": True,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "sample_limit_per_list": MAX_USERS, "max_scrolls_per_list": MAX_SCROLLS,
              "work_budget_seconds": MAX_SECONDS, "explicit_api_requests": 0,
              "followers": None, "following": None, "error": None,
              "integrity_verified": False, "non_followers": None,
              "login_prompt_actions": [],
              "comparison_status": "insufficient_data"}
    operation = "navigate_profile"
    try:
        page.goto(f"https://www.instagram.com/{TARGET}/", wait_until="domcontentloaded", timeout=30000)
        operation = "wait_initial_page"
        wait_checked(page, probe, deadline, 3000)
        operation = "confirm_target"
        if not page_control_or_title_matches(page):
            raise RuntimeError("target_profile_unavailable")
        for direction in ("followers", "following"):
            check(page, probe, deadline)
            probe.phase = direction
            item = {"rows": [], "control_available": False, "dialog_opened": False,
                    "list_context_confirmed": False,
                    "reported_count": None, "cycles": [], "state": "unavailable",
                    "reason": "control_unavailable", "terminal_verified": False}
            report[direction] = item
            operation = "locate_" + direction + "_control"
            control = profile_control(page, direction)
            if control is None:
                continue
            item["control_available"] = True
            operation = "read_" + direction + "_count"
            label = control.inner_text(timeout=2000).strip()
            count = re.match(r"^([0-9]+(?:,[0-9]{3})*)\s+(followers|following)\b", label, re.I)
            if count:
                item["reported_count"] = int(count[1].replace(",", ""))
            operation = "inspect_" + direction + "_interstitial"
            item["control_before_click"] = control_diagnostic(control)
            resolve_pre_list_dialog(page, probe, deadline, item["control_before_click"],
                                    report["login_prompt_actions"])
            if report["login_prompt_actions"]:
                item["control_after_prompt"] = control_diagnostic(control)
            check(page, probe, deadline)
            operation = "click_" + direction + "_control"
            item["reason"] = "opening_dialog"
            control.click(timeout=5000)
            operation = "wait_" + direction + "_dialog"
            wait_checked(page, probe, deadline, 1600)
            operation = "locate_" + direction + "_dialog"
            dialogs = page.get_by_role("dialog")
            if not dialogs.count() or not dialogs.first.is_visible():
                item["reason"] = "dialog_unavailable"
                continue
            item["dialog_opened"] = True
            dialog = dialogs.first
            operation = "classify_" + direction + "_dialog"
            item["dialog_evidence"] = classify_opened_dialog(dialog, direction)
            if item["dialog_evidence"]["verification_text"]:
                raise RuntimeError("verification_or_access_limit")
            if item["dialog_evidence"]["password_input"] or item["dialog_evidence"]["login_text"]:
                # A new login prompt after clicking the list is not dismissed.
                raise RuntimeError("authentication_required")
            if not item["dialog_evidence"]["expected_list_heading"]:
                raise RuntimeError("list_dialog_unconfirmed")
            item["list_context_confirmed"] = True
            seen = set()
            for cycle in range(MAX_SCROLLS + 1):
                operation = "read_" + direction + "_rows"
                check(page, probe, deadline)
                snapshot = extract_visible_people(dialog, include_geometry=True)
                new = 0
                for row in snapshot["rows"]:
                    name = row["username"].casefold()
                    if name not in seen and len(item["rows"]) < MAX_USERS:
                        seen.add(name)
                        item["rows"].append({"username": name, "id": row["id"]})
                        new += 1
                item["cycles"].append({"cycle": cycle, "new_users": new,
                                       "unique_total": len(seen), "geometry": snapshot["geometry"],
                                       "loading": snapshot["loading"]})
                if len(seen) >= MAX_USERS or cycle == MAX_SCROLLS or not snapshot["geometry"]:
                    break
                check(page, probe, deadline)
                operation = "scroll_" + direction
                scroll_metrics_and_advance(dialog)
                wait_checked(page, probe, deadline, 1200)
            item["state"] = "incomplete" if seen else "unavailable"
            item["reason"] = "sample_limit" if len(seen) >= MAX_USERS else "bounded_observation_ended"
            if not seen:
                item["reason"] = "empty_dialog"
            probe.phase = "between"
            operation = "close_" + direction + "_dialog"
            check(page, probe, deadline)
            page.keyboard.press("Escape")
            wait_checked(page, probe, deadline, 400)
    except Exception as exc:
        # Preserve safe operation/type, never the exception message (may contain URLs).
        report["failed_operation"] = operation
        report["error"] = str(exc) if type(exc) is RuntimeError and str(exc) in SAFE_ERRORS else {
            "TimeoutError": "ui_timeout", "Error": "browser_operation_error"
        }.get(type(exc).__name__, "local_operation_error")
        if operation.startswith("click_"):
            report["click_failure_hints"] = click_failure_hints(exc)
            report["control_after_failure"] = control_diagnostic(control)
        # A gate appearing during a click takes precedence over its timeout.
        try:
            check(page, probe, deadline)
        except RuntimeError as gate:
            if str(gate) in SAFE_ERRORS:
                report["error"] = str(gate)
        except Exception:
            pass
        if probe.phase in ("followers", "following") and report[probe.phase]:
            report[probe.phase]["reason"] = "operation_interrupted"
            report[probe.phase]["error"] = report["error"]
    for direction in ("followers", "following"):
        for row in (report[direction] or {}).get("rows", []):
            ids = sorted(probe.identities.get(row["username"], set()))
            row["passive_candidate_ids"] = ids[:5]
            row["identity_ambiguous"] = len(ids) > 1 or bool(row["id"] and ids and row["id"] not in ids)
            # A cross-response identity match alone does not establish list membership.
    report["network"] = probe.report()
    observed = [bool((report[direction] or {}).get("rows")) for direction in ("followers", "following")]
    report["outcome"] = ("interrupted" if report["error"] else "sample_observed" if all(observed)
                         else "partial_sample" if any(observed) else "no_list_records")
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def browser_configuration(browser_name="chromium", layout="narrow"):
    if browser_name not in BROWSER_CHANNELS or layout not in VIEWPORTS:
        raise ValueError("unsupported_browser_configuration")
    launch = {"headless": False}
    if BROWSER_CHANNELS[browser_name]:
        launch["channel"] = BROWSER_CHANNELS[browser_name]
    context = {"locale": "en-US", "viewport": dict(VIEWPORTS[layout])}
    return launch, context


def run_once(attempt, *, browser_name="chromium", layout="narrow"):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", attempt):
        raise ValueError("invalid_attempt")
    launch_options, context_options = browser_configuration(browser_name, layout)
    configuration = {"browser": browser_name, "layout": layout,
                     "viewport": context_options["viewport"], "locale": "en-US",
                     "headless": False, "imported_session": False,
                     "custom_user_agent": False, "automatic_browser_fallback": False}
    directory = ROOT / "reports" / "anonymous-inspection"
    directory.mkdir(parents=True, exist_ok=True)
    # Durable receipt: the same attempt cannot start a second browser, even on error.
    receipt = directory / f"attempt-{attempt}.json"
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump({"target": TARGET, "attempt": attempt, "consumed": True,
                   "configuration": configuration,
                   "started_at": datetime.now(timezone.utc).isoformat()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    from playwright.sync_api import sync_playwright
    result = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**launch_options)
            try:
                configuration["browser_version"] = browser.version
                context = browser.new_context(**context_options)
                page = context.new_page()
                page.set_default_timeout(5000)
                result = collect(page)
            finally:
                browser.close()
    except Exception:
        if result is None:
            result = {"target": TARGET, "error": "browser_start_or_teardown_error",
                      "integrity_verified": False, "non_followers": None}
    path = directory / f"result-{attempt}.json"
    result["configuration"] = configuration
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, result
