import copy
import unittest

from backend.services.relationships import analyze_snapshot, analyze_history
from tests.test_hybrid_collector import report_fixture, list_fixture


def fixture(run='first', day=1):
    report = report_fixture(run)
    report['started_at'] = f'2026-10-{day:02d}T12:00:00+00:00'
    return report


def reference():
    return {'source':'independent_owner_reference','username':'example_target',
            'followers_ids':['1','2'],'following_ids':['2','3']}


class RelationshipTests(unittest.TestCase):
    def test_observations_partition_both_lists_without_definitive_claim(self):
        report=fixture()
        snapshot=copy.deepcopy(report)
        result=analyze_snapshot(report)
        self.assertEqual([u['id'] for u in result['observed_mutual']],['2'])
        self.assertEqual([u['id'] for u in result['following_not_observed_followers']],['3'])
        self.assertEqual([u['id'] for u in result['followers_not_observed_following']],['1'])
        self.assertIsNone(result['non_followers'])
        self.assertEqual(result['history']['status'],'previous_snapshot_required')
        self.assertEqual(report,snapshot)

    def test_missing_follower_may_make_a_candidate_false(self):
        report=fixture()
        report['followers']['reported_count']=report['followers']['reported_count_after']=3
        result=analyze_snapshot(report)
        self.assertEqual(result['following_not_observed_followers'][0]['id'],'3')
        self.assertIsNone(result['non_followers'])

    def test_matching_id_with_changed_username_stays_mutual(self):
        report=fixture()
        report['following']['structured_rows'][0]['username']='bob_renamed'
        report['following']['ui_rows'][0]['username']='bob_renamed'
        self.assertEqual(analyze_snapshot(report)['observed_mutual'][0]['id'],'2')

    def test_cross_list_same_username_different_id_is_rejected(self):
        report=fixture()
        report['following']['structured_rows'][0]['id']='4'
        with self.assertRaisesRegex(ValueError,'cross_list'):
            analyze_snapshot(report)

    def test_duplicates_and_ui_mismatch_rejected(self):
        for change in ('duplicate','ui'):
            report=fixture()
            if change=='duplicate':
                report['followers']['structured_rows'].append(report['followers']['structured_rows'][0])
            else:
                report['followers']['ui_rows']=[]
            with self.subTest(change=change),self.assertRaises(ValueError):
                analyze_snapshot(report)

    def test_scope_errors_and_naive_time_rejected(self):
        for changes in ({'target':'someone_else'},{'observer':'someone_else'},
                        {'error':'access_restricted'},{'started_at':'2026-10-01T12:00:00'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                analyze_snapshot({**fixture(),**changes})

    def test_stored_verification_flags_and_count_equality_are_not_proof(self):
        report=fixture()
        report.update(integrity_verified=True,comparison_status='verified',non_followers=[])
        self.assertIsNone(analyze_snapshot(report)['non_followers'])

    def test_existing_validator_only_unlocks_with_matching_independent_repeat(self):
        result=analyze_snapshot(fixture(),repeat=fixture('repeat',2),reference=reference())
        self.assertEqual(result['non_followers'],[{'id':'3','username':'carol'}])
        wrong={**reference(),'followers_ids':['1','2','3']}
        self.assertIsNone(analyze_snapshot(fixture(),repeat=fixture('repeat',2),reference=wrong)['non_followers'])

    def test_partial_end_and_unstable_counts_cannot_be_promoted(self):
        for changes in ({'terminal_observed':False},{'reported_count_after':3}):
            report=fixture()
            report['followers'].update(changes)
            result=analyze_snapshot(report,repeat=fixture('repeat',2),reference=reference())
            self.assertIsNone(result['non_followers'])

    def test_current_nonfollowers_are_not_historical_unfollowers(self):
        result=analyze_history(fixture(),fixture('later',3))
        self.assertEqual(result['history']['no_longer_observed_followers'],[])
        self.assertEqual(len(result['following_not_observed_followers']),1)
        self.assertFalse(result['history']['unfollow_action_confirmed'])

    def test_historical_absence_is_not_promoted_when_partial(self):
        later=fixture('later',3)
        later['followers']=list_fixture('followers',[{'id':'2','username':'bob'}])
        result=analyze_history(fixture(),later)
        self.assertEqual(result['history']['no_longer_observed_followers'],[{'id':'1','username':'alice'}])
        self.assertIsNone(result['history']['no_longer_present_verified'])
        self.assertEqual(result['history']['no_longer_observed_followers_still_followed'],[])

    def test_verified_history_proves_set_difference_not_voluntary_action(self):
        later=fixture('later',3)
        later['followers']=list_fixture('followers',[{'id':'2','username':'bob'}])
        repeat=copy.deepcopy(later)
        repeat.update(run_id='later-repeat',started_at='2026-10-04T12:00:00+00:00')
        result=analyze_history(fixture(),later,previous_repeat=fixture('old-repeat',2),
            previous_reference=reference(),current_repeat=repeat,
            current_reference={**reference(),'followers_ids':['2']})
        self.assertEqual(result['history']['no_longer_present_verified'],[{'id':'1','username':'alice'}])
        self.assertFalse(result['history']['unfollow_action_confirmed'])

    def test_history_requires_ordered_distinct_runs(self):
        for later in (fixture(),fixture('later',1)):
            with self.assertRaises(ValueError):
                analyze_history(fixture(),later)

    def test_independently_verified_zero_followers_is_not_a_load_failure(self):
        later=fixture('later',3)
        later['followers']=list_fixture('followers',[])
        repeat=copy.deepcopy(later)
        repeat.update(run_id='zero-repeat',started_at='2026-10-04T12:00:00+00:00')
        result=analyze_history(fixture(),later,previous_repeat=fixture('old-repeat',2),
            previous_reference=reference(),current_repeat=repeat,
            current_reference={**reference(),'followers_ids':[]})
        self.assertEqual(len(result['history']['no_longer_present_verified']),2)
        self.assertEqual(result['history']['no_longer_observed_followers_still_followed'],[{'id':'2','username':'bob'}])
