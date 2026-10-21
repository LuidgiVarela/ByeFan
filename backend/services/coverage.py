"""Coverage against profile counters is not accuracy or verified completeness."""
import math
import re


def validate_target(percent):
    if type(percent) is not int or not 98 <= percent <= 100:
        raise ValueError("coverage_target_must_be_integer_98_to_100")
    return percent


def list_coverage(item, target_percent=98, *, require_stable=True, response_overflow=0):
    validate_target(target_percent)
    item = item or {}
    problems = []
    identities = {}
    name_ids = {}
    for row in item.get("structured_rows", []):
        uid, name = row.get("id"), row.get("username")
        if (not isinstance(uid, str) or not re.fullmatch(r"[0-9]+", uid)
                or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name)):
            problems.append("invalid_identity")
            continue
        name = name.casefold()
        if uid in identities or name in name_ids:
            problems.append("duplicate_or_conflicting_identity")
        identities[uid] = name
        name_ids[name] = uid
    visible = set()
    for row in item.get("ui_rows", []):
        name = row.get("username")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.]{1,30}", name):
            problems.append("invalid_ui_username")
        else:
            visible.add(name.casefold())
    structured_names = set(identities.values())
    matched = sum(name in visible for name in identities.values())
    if visible != structured_names:
        problems.append("ui_structured_mismatch")
    reported = item.get("reported_count")
    exact_count = type(reported) is int and reported >= 0
    stable = exact_count and type(item.get("reported_count_after")) is int \
        and item["reported_count_after"] == reported
    if not exact_count:
        problems.append("profile_count_unavailable")
    elif len(identities) > reported or len(visible) > reported:
        problems.append("more_records_than_profile_count")
    if require_stable and not stable:
        problems.append("profile_count_unstable_or_unchecked")
    if item.get("state") in (None, "unavailable", "interrupted") or item.get("error"):
        problems.append("collection_unavailable_or_interrupted")
    if response_overflow or any(item.get(field, 0) for field in (
            "parse_limit_events", "deferred_overflow", "pending_responses_unprocessed",
            "inflight_requests_at_end")):
        problems.append("unprocessed_or_lost_responses")
    required = math.ceil(reported * target_percent / 100) if exact_count else None
    percentage = None
    if exact_count:
        percentage = (round(100 * matched / reported, 3) if reported else
                      (100.0 if not identities and not visible else None))
    return {"target_percent": target_percent, "reported_count": reported if exact_count else None,
            "unique_ui_usernames": len(visible), "unique_structured_ids": len(identities),
            "ids_matched_to_ui": matched, "required_ids_for_target": required,
            "observed_coverage_percent": percentage,
            "count_gap": max(0, reported - matched) if exact_count else None,
            "counts_stable": stable,
            "target_met": required is not None and matched >= required and not problems,
            "issues": sorted(set(problems)), "precision_percent": None,
            "integrity_verified": False, "basis": "profile_counter_and_ui_correlated_ids"}


def report_coverage(report, target_percent=98):
    """Evaluate a single run. Never merge snapshots or promote comparison results."""
    lists = {direction: list_coverage(report.get(direction), target_percent,
                    response_overflow=report.get("response_overflow", {}).get(direction, 0))
             for direction in ("followers", "following")}
    percentages = [item["observed_coverage_percent"] for item in lists.values()]
    return {"target_percent": target_percent, "lists": lists,
            "both_lists_target_met": not report.get("error") and all(
                item["target_met"] for item in lists.values()),
            "minimum_list_coverage_percent": min(percentages) if all(
                value is not None for value in percentages) else None,
            "precision_percent": None, "definitive_comparison_allowed": False}
