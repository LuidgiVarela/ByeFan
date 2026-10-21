"""Read-only identity comparisons. Observed absence is never verified absence.

No network, session loading, snapshot merging or inference of user actions.
Definitive current-set differences use the project's existing strict validator.
"""
from datetime import datetime
import re

from backend.services.coverage import report_coverage
from backend.identity import TARGET, OBSERVER


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('missing_snapshot_timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid_snapshot_timestamp') from None
    if parsed.tzinfo is None:
        raise ValueError('snapshot_timezone_required')
    return parsed


def identities(item):
    if not isinstance(item, dict) or not isinstance(item.get('structured_rows'), list):
        raise ValueError('structured_list_missing')
    if item.get('state') in (None, 'unavailable', 'interrupted') or item.get('error'):
        raise ValueError('list_unavailable_or_interrupted')
    result, names = {}, {}
    for row in item['structured_rows']:
        if not isinstance(row, dict):
            raise ValueError('invalid_identity')
        uid, name = row.get('id'), row.get('username')
        if (not isinstance(uid, str) or not re.fullmatch(r'[0-9]+', uid)
                or not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,30}', name)):
            raise ValueError('invalid_identity')
        name = name.casefold()
        if uid in result or name in names:
            raise ValueError('duplicate_or_conflicting_identity')
        result[uid] = {'id': uid, 'username': name}
        names[name] = uid
    visible = item.get('ui_rows')
    if not isinstance(visible, list) or any(not isinstance(row, dict)
            or not isinstance(row.get('username'), str) for row in visible):
        raise ValueError('ui_evidence_missing')
    if {row['username'].casefold() for row in visible} != set(names):
        raise ValueError('ui_structured_mismatch')
    return result


def ordered(rows):
    return sorted(rows, key=lambda row: (row['username'], row['id']))


def analyze_snapshot(report, *, repeat=None, reference=None):
    if (not isinstance(report, dict) or report.get('target') != TARGET
            or report.get('observer') != OBSERVER):
        raise ValueError('snapshot_outside_authorized_scope')
    if report.get('error'):
        raise ValueError('snapshot_interrupted')
    timestamp(report.get('started_at'))
    if not isinstance(report.get('run_id'), str) or not report['run_id']:
        raise ValueError('snapshot_identity_missing')
    followers = identities(report.get('followers'))
    following = identities(report.get('following'))
    # A renamed account keeps its ID. A username pointing to two IDs during
    # one collection is ambiguous and must not produce a classification.
    name_ids = {}
    for row in [*followers.values(), *following.values()]:
        if row['username'] in name_ids and name_ids[row['username']] != row['id']:
            raise ValueError('cross_list_identity_conflict')
        name_ids[row['username']] = row['id']
    coverage = report_coverage(report)
    current = {'comparison_status': 'insufficient_data', 'non_followers': None}
    if repeat is not None and reference is not None:
        from backend.experimental.hybrid_collector import validate_repeated_reports
        # Validate the second input's shape as well; stored "verified" flags
        # alone are never accepted as proof.
        repeated = analyze_snapshot(repeat)
        if repeat.get('started_at') == report.get('started_at'):
            raise ValueError('independent_repeat_timestamp_required')
        sound = all(not snapshot['coverage']['lists'][direction]['issues']
                    for snapshot in ({'coverage': coverage}, repeated)
                    for direction in ('followers', 'following'))
        if sound:
            current = validate_repeated_reports(report, repeat, reference)
    fid, gid = set(followers), set(following)
    return {
        'mode': 'saved_observation', 'simulation': False, 'target': TARGET,
        'run_id': report['run_id'], 'observed_at': report['started_at'],
        'live_query_performed': False, 'coverage': coverage,
        'comparison_status': current['comparison_status'],
        'non_followers': current['non_followers'],
        'observed_mutual': ordered(following[i] for i in fid & gid),
        'following_not_observed_followers': ordered(following[i] for i in gid - fid),
        'followers_not_observed_following': ordered(followers[i] for i in fid - gid),
        'counts': {'observed_followers': len(fid), 'observed_following': len(gid),
                   'observed_mutual': len(fid & gid),
                   'following_not_observed_followers': len(gid - fid),
                   'followers_not_observed_following': len(fid - gid)},
        'history': {'status': 'previous_snapshot_required',
                    'no_longer_observed_followers': None,
                    'no_longer_observed_followers_still_followed': None,
                    'no_longer_present_verified': None, 'unfollow_action_confirmed': False},
    }


def analyze_history(previous, current, *, previous_repeat=None, previous_reference=None,
                    current_repeat=None, current_reference=None):
    before = analyze_snapshot(previous, repeat=previous_repeat, reference=previous_reference)
    after = analyze_snapshot(current, repeat=current_repeat, reference=current_reference)
    if (before['run_id'] == after['run_id']
            or timestamp(before['observed_at']) >= timestamp(after['observed_at'])):
        raise ValueError('distinct_chronologically_ordered_snapshots_required')
    old, new = identities(previous['followers']), identities(current['followers'])
    verified = before['non_followers'] is not None and after['non_followers'] is not None
    if (not old or not new) and not verified:
        after['history']['status'] = 'nonempty_follower_observations_required'
        return after
    gone = ordered(old[i] for i in set(old) - set(new))
    following = identities(current['following'])
    after['history'] = {
        'status': 'verified_snapshot_difference' if verified else 'partial_observation_difference',
        'previous_observed_at': before['observed_at'],
        'no_longer_observed_followers': gone,
        'no_longer_observed_followers_still_followed': [row for row in gone if row['id'] in following],
        'no_longer_present_verified': gone if verified else None,
        # Even a verified disappearance can be account deletion, a block or
        # removal by the owner. Do not infer a voluntary unfollow action.
        'unfollow_action_confirmed': False,
    }
    return after
