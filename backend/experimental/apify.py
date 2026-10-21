"""Small, explicitly authorized Apify runs with conservative budget reservations."""
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from backend.collector.instagram import NoRedirect
from backend.identity import TARGET

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "reports" / "apify-budget.sqlite3"
ACTORS = {
    "steadyfetch": ("steadyfetch~instagram-followers-scraper", "1.0.54"),
    "apify": ("apify~instagram-followers-following-scraper", "0.0.168"),
}
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


class ExperimentError(ValueError):
    """Only locally generated, non-sensitive error codes are exposed."""


def money(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ExperimentError("invalid_budget") from None
    if not amount.is_finite() or not Decimal("0") < amount <= Decimal("100"):
        raise ExperimentError("budget_must_be_positive_and_at_most_100_usd")
    if amount * 1_000_000 != (amount * 1_000_000).to_integral_value():
        raise ExperimentError("budget_precision_exceeds_microdollar")
    return amount


@dataclass(frozen=True)
class Plan:
    provider: str
    direction: str
    cap_usd: str
    limit: int = 5
    username: str = TARGET
    expanded: bool = False

    def __post_init__(self):
        if self.provider not in ACTORS or self.direction not in ("followers", "following"):
            raise ExperimentError("invalid_provider_or_direction")
        if self.username != TARGET:
            raise ExperimentError("unauthorized_target")
        if type(self.limit) is not int or not 1 <= self.limit <= (1000 if self.expanded else 10):
            raise ExperimentError("sample_limit_1_to_10_or_expanded_1_to_1000")
        money(self.cap_usd)

    def preview(self):
        actor, build = ACTORS[self.provider]
        payload = {"usernames": [TARGET], "resultsLimit": self.limit}
        if self.provider == "steadyfetch":
            payload.update(mode=self.direction, includeSeen=True, maxRunSeconds=150)
        else:
            payload["dataToScrape"] = self.direction
        return {"provider": self.provider, "actor": actor, "username": TARGET,
                "direction": self.direction, "sample": not self.expanded,
                "input": payload, "query": {"build": build, "timeout": 180,
                "maxTotalChargeUsd": str(money(self.cap_usd)), "restartOnError": "false"}}


class Budget:
    """Reserve the WHOLE run ceiling, atomically. Never refund automatically.

    This deliberately sacrifices available budget after cheap/failed/unknown runs.
    It avoids overspending through concurrency, missing cost data or POST timeouts.
    """
    def __init__(self, path=LEDGER):
        self.path = Path(path)

    def initialize(self, total):
        micro = int(money(total) * 1_000_000)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids silently resetting an existing budget.
        with self.path.open("xb"):
            pass
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE budget (total INTEGER NOT NULL, suspended INTEGER NOT NULL)")
            db.execute("INSERT INTO budget VALUES (?, 0)", (micro,))
            db.execute("CREATE TABLE reservations (id TEXT PRIMARY KEY, amount INTEGER NOT NULL)")

    def reserve(self, cap):
        if not self.path.is_file():
            raise ExperimentError("initialize_budget_first")
        micro = int(money(cap) * 1_000_000)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            total, suspended = db.execute("SELECT total, suspended FROM budget").fetchone()
            if suspended:
                raise ExperimentError("budget_suspended_review_billing")
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM reservations").fetchone()[0]
            if used + micro > total:
                raise ExperimentError("cumulative_budget_exceeded")
            reservation = uuid.uuid4().hex
            db.execute("INSERT INTO reservations VALUES (?, ?)", (reservation, micro))
        return reservation

    def freeze(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("UPDATE budget SET suspended = 1")


class API:
    def __init__(self, token):
        if not token or any(c.isspace() for c in token):
            raise ExperimentError("missing_or_invalid_APIFY_TOKEN")
        self._token = token
        self._opener = build_opener(NoRedirect())

    def request(self, method, path, params=None, body=None):
        url = "https://api.apify.com/v2/" + path
        if params:
            url += "?" + urlencode(params)
        req = Request(url, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + self._token,
                               "Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=20) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ExperimentError("response_too_large")
                return json.loads(raw)
        except HTTPError as exc:
            # Never log server bodies or exception text (could echo the token).
            raise ExperimentError("apify_http_" + str(exc.code)) from None
        except (URLError, TimeoutError, OSError):
            raise ExperimentError("network_error_no_automatic_retry") from None
        except (ValueError, UnicodeError):
            raise ExperimentError("invalid_api_response") from None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9]+", value):
        raise ExperimentError("invalid_remote_identifier")
    return value


def effective_pricing(metadata, now=None):
    now = now or datetime.now(timezone.utc)
    eligible = []
    for entry in metadata.get("pricingInfos", []):
        start = datetime.fromisoformat(entry["startedAt"].replace("Z", "+00:00"))
        if start <= now:
            eligible.append((start, entry))
    if not eligible:
        raise ExperimentError("pricing_unknown")
    entry = max(eligible, key=lambda pair: pair[0])[1]
    if entry.get("pricingModel") != "PAY_PER_EVENT":
        raise ExperimentError("pricing_model_changed_review_required")
    return entry


def normalize(provider, direction, rows):
    users, issues, seen = [], [], set()
    for index, row in enumerate(rows):
        def reject(code):
            issues.append({"row": index, "code": code})
        if not isinstance(row, dict):
            reject("invalid_row")
            continue
        if row.get("error") or (provider == "steadyfetch" and row.get("status") != "delivered"):
            # Status/end-of-list/resume rows are NOT people or proof of completeness.
            code = row.get("status") or row.get("error")
            reject(code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,50}", code)
                   else "provider_status_or_error")
            continue
        source = row.get("seedUsername" if provider == "steadyfetch" else "sourceUsername")
        kind = row.get("direction") if provider == "steadyfetch" else {
            "FOLLOWER": "followers", "FOLLOWING": "following"}.get(row.get("type"))
        if source != TARGET or kind != direction:
            reject("source_or_direction_mismatch")
            continue
        if provider == "steadyfetch":
            relation = "followerOf" if direction == "followers" else "followingOf"
            if row.get(relation) != TARGET:
                reject("relationship_mismatch")
                continue
        uid, name = row.get("userId"), row.get("username")
        if type(uid) is int and uid > 0:
            uid = str(uid)
        if (not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid)
                or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name)):
            reject("invalid_identity")
            continue
        if uid in seen:
            reject("duplicate_identity")
            continue
        seen.add(uid)
        users.append({"id": uid, "username": name})
    return {"users": users, "recovered_count": len(users), "issues": issues,
            "identity_format_valid": not issues, "real_identity_verified": False,
            "state": "incomplete" if users else "unavailable", "integrity_verified": False,
            "reason": "independent_reference_and_repeat_required"}


def compare_reference(result, direction, reference):
    """Offline exact-ID AND username comparison, never verifies reference origin."""
    if (reference.get("username") != TARGET or reference.get("complete") is not True
            or not reference.get("observed_at") or not reference.get("source")):
        raise ExperimentError("reference_requires_target_complete_date_and_source")
    entries = reference.get(direction)
    if not isinstance(entries, list):
        raise ExperimentError("invalid_reference_list")
    expected = {}
    for entry in entries:
        uid, name = entry["id"], entry["username"]
        if (not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid)
                or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name)
                or uid in expected):
            raise ExperimentError("invalid_reference_identity")
        expected[uid] = name.casefold()
    actual = {u["id"]: u["username"].casefold() for u in result["users"]}
    return {"reference_count": len(expected), "recovered_count": len(actual),
            "missing_ids": sorted(expected.keys() - actual.keys()),
            "unexpected_ids": sorted(actual.keys() - expected.keys()),
            "username_mismatches": sorted(i for i in actual.keys() & expected.keys()
                                          if actual[i] != expected[i]),
            "exact_match": actual == expected and not result["issues"],
            "repeatability_verified": False}


def execute(plan, api, budget, *, authorized=False, sleep=time.sleep, clock=time.monotonic,
            checkpoint=lambda report: None):
    if authorized is not True:
        raise ExperimentError("paid_execution_not_authorized")
    preview = plan.preview()
    report = {"plan": preview, "started_at": datetime.now(timezone.utc).isoformat(),
              "run_id": None, "dataset_id": None, "run_status": None,
              "error": None, "cost_usd": None, "non_followers": None,
              "dataset_fully_downloaded": False, "instagram_pagination_verified": False}
    start = clock()
    run = {}
    rows = []
    # Reserve before even preflight: no ambiguous failure can create free retries.
    report["reservation_id"] = budget.reserve(plan.cap_usd)
    report["reserved_usd"] = str(money(plan.cap_usd))
    checkpoint(report)
    try:
        metadata = api.request("GET", "acts/" + preview["actor"])["data"]
        pricing = effective_pricing(metadata)
        minimum = Decimal(str(pricing.get("minimalMaxTotalChargeUsd") or 0))
        if not minimum.is_finite() or minimum > money(plan.cap_usd):
            raise ExperimentError("provider_minimum_exceeds_cap")
        report["pricing_model"] = pricing["pricingModel"]
        run = api.request("POST", "acts/" + preview["actor"] + "/runs",
                          preview["query"], preview["input"])["data"]
        report["run_id"] = identifier(run["id"])
        checkpoint(report)
        accepted = Decimal(str(run.get("options", {}).get("maxTotalChargeUsd")))
        if not accepted.is_finite() or accepted < 0 or accepted > money(plan.cap_usd):
            raise ExperimentError("server_budget_not_confirmed")
        while run.get("status") not in TERMINAL:
            if clock() - start > 210:
                raise ExperimentError("poll_deadline")
            sleep(2)
            run = api.request("GET", "actor-runs/" + report["run_id"])["data"]
        report["run_status"] = run["status"]
        report["build_id"] = run.get("buildId")
        report["remote_started_at"] = run.get("startedAt")
        report["remote_finished_at"] = run.get("finishedAt")
        report["charged_event_counts"] = run.get("chargedEventCounts")
        # Preserve pricing actually attached to this run, not today's store headline.
        report["run_pricing_info"] = run.get("pricingInfo")
        if run["status"] != "SUCCEEDED":
            report["error"] = "actor_" + run["status"].lower()
        report["dataset_id"] = identifier(run["defaultDatasetId"])
        # Post-run dataset reads can incur fees OUTSIDE maxTotalChargeUsd.
        # Fail closed: no automatic download can meet the strict total cap yet.
        report["dataset_download"] = "blocked_post_run_storage_cost_not_capped"
    except (ExperimentError, KeyError, TypeError, InvalidOperation, ValueError) as exc:
        report["error"] = str(exc) if isinstance(exc, ExperimentError) else "unexpected_api_schema"
    except KeyboardInterrupt:
        report["error"] = "interrupted_abort_requested"
    finally:
        if report["run_id"] and run.get("status") not in TERMINAL:
            try:
                abort = api.request("POST", "actor-runs/" + report["run_id"] + "/abort")["data"]
                report["abort_status"] = abort.get("status")
            except Exception:
                report["abort_status"] = "unconfirmed_check_console"
        value = run.get("usageTotalUsd")
        if type(value) in (int, float) and Decimal(str(value)).is_finite() and value >= 0:
            report["cost_usd"] = value
            report["cost_status"] = ("reported_by_apify_not_independent_invoice_audit"
                                     if run.get("status") in TERMINAL else "provisional_run_not_terminal")
            if Decimal(str(value)) > money(plan.cap_usd):
                report["error"] = "reported_cost_exceeds_cap_investigate_billing"
        else:
            report["cost_status"] = "unknown_reservation_retained"
        report["run_status"] = run.get("status")
        report["elapsed_seconds"] = round(clock() - start, 3)
        report["result"] = normalize(plan.provider, plan.direction, rows)
        report["result"]["reason"] = "dataset_not_downloaded"
        if report["error"] in ("server_budget_not_confirmed", "reported_cost_exceeds_cap_investigate_billing"):
            budget.freeze()
        checkpoint(report)
    return report
