import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

from backend.pilot.live_collection import LocalCollection
from backend.pilot.queue import QueueError
from backend.pilot.worker import WorkerLock
from backend.experimental.hybrid_collector import run_once, collect_hybrid_list, settle_without_scrolling
from backend.experimental.passive_scroll_audit import BatchTracker
from tests.test_hybrid_collector import FakeProbe
from tests.test_relationships import fixture


class LiveCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.release = threading.Event()
        self.controllers = []

    def tearDown(self):
        self.release.set()
        for controller in self.controllers:
            controller.close()
        self.temp.cleanup()

    def controller(self, runner, attempt='owner-test'):
        controller=LocalCollection(self.root,attempt,runner=runner)
        self.controllers.append(controller)
        return controller

    def test_many_requests_admit_one_durable_attempt_and_protect_results(self):
        def runner(*args, **kwargs):
            self.release.wait(5)
            report=fixture()
            kwargs['on_progress'](report)
            return None,None,report
        mock=Mock(side_effect=runner)
        controller=self.controller(mock)
        def start():
            try:
                return controller.start()
            except QueueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            tickets=[t for t in pool.map(lambda _: start(),range(50)) if t]
        self.assertEqual(len(tickets),1)
        self.assertEqual(mock.call_count,1)
        ticket=tickets[0]
        with self.assertRaises(QueueError):
            controller.status(ticket['ticket_id'],'wrong')
        self.release.set()
        controller.thread.join(5)
        status=controller.status(ticket['ticket_id'],ticket['access_token'])
        self.assertEqual(status['state'],'completed')
        self.assertEqual(status['progress']['followers'],2)
        self.assertEqual(status['result']['mode'],'live_observation')
        self.assertTrue(status['result']['live_query_performed'])
        self.assertIsNone(status['result']['non_followers'])
        self.assertNotIn(ticket['access_token'],controller.receipt.read_text())
        recovered=self.controller(Mock(side_effect=AssertionError('must not run')))
        self.assertFalse(recovered.available())
        with self.assertRaises(QueueError):
            recovered.start()

    def test_same_receipt_cannot_be_consumed_by_two_instances(self):
        first=self.controller(lambda *a,**k:(None,None,fixture()))
        second=self.controller(Mock(side_effect=AssertionError('must not run')))
        first.start()
        with self.assertRaises(QueueError):
            second.start()

    def test_cancellation_discards_late_results_and_never_retries(self):
        cancelled=threading.Event()
        def runner(*a,**kwargs):
            while not kwargs['cancelled']():
                if self.release.wait(.01):
                    break
            cancelled.set()
            return None,None,fixture()
        controller=self.controller(runner)
        ticket=controller.start()
        controller.cancel(ticket['ticket_id'],ticket['access_token'])
        self.assertTrue(cancelled.wait(3))
        controller.thread.join(3)
        status=controller.status(ticket['ticket_id'],ticket['access_token'])
        self.assertEqual(status['state'],'cancelled')
        self.assertIsNone(status['result'])
        with self.assertRaises(QueueError):
            controller.start()

    def test_restrictions_missing_data_and_sensitive_errors_do_not_release_results(self):
        for index,code in enumerate(('network_access_limited','authentication_required',
                'observer_session_not_confirmed_in_ui','secret session string')):
            report=fixture()
            report['error']=code
            controller=self.controller(lambda *a,**k:(None,None,report),str(index))
            ticket=controller.start()
            controller.thread.join(3)
            status=controller.status(ticket['ticket_id'],ticket['access_token'])
            self.assertEqual(status['state'],'restricted' if index<3 else 'interrupted')
            self.assertIsNone(status['result'])
            self.assertNotIn('secret session',json.dumps(status))
        report=fixture()
        report['following']['ui_rows']=[]
        controller=self.controller(lambda *a,**k:(None,None,report),'inconsistent')
        ticket=controller.start()
        controller.thread.join(3)
        self.assertEqual(controller.status(ticket['ticket_id'],ticket['access_token'])['error'],
            'insufficient_or_inconsistent_data')
        report=fixture()
        report['error']='id_username_conflict'
        controller=self.controller(lambda *a,**k:(None,None,report),'identity-conflict')
        ticket=controller.start()
        controller.thread.join(3)
        status=controller.status(ticket['ticket_id'],ticket['access_token'])
        self.assertEqual(status['state'],'interrupted')
        self.assertEqual(status['error'],'insufficient_or_inconsistent_data')

    def test_closed_or_invalid_attempt_never_starts(self):
        with self.assertRaises(ValueError):
            self.controller(Mock(),'../escape')
        runner=Mock()
        controller=self.controller(runner)
        controller.close()
        with self.assertRaises(QueueError):
            controller.start()
        runner.assert_not_called()

    def test_session_lock_and_pre_cancel_stop_before_browser_launch(self):
        profile=self.root/'browser-profile'
        profile.mkdir()
        with patch('backend.experimental.hybrid_collector.REPORTS',self.root/'reports'), \
                patch('backend.experimental.hybrid_collector.safe_profile_path',return_value=profile), \
                patch('playwright.sync_api.sync_playwright') as browser:
            with WorkerLock(profile.parent/'hybrid-session'):
                _,_,report=run_once()
            self.assertEqual(report['error'],'worker_already_active')
            _,_,report=run_once(cancelled=lambda:True)
            self.assertEqual(report['error'],'owner_cancelled')
            browser.assert_not_called()

    def test_cancel_list_and_settlement_before_scroll_or_wait(self):
        page=Mock()
        tracker=BatchTracker(FakeProbe())
        checkpoints=[]
        result=collect_hybrid_list(page,'followers',time.monotonic()+30,Mock(),tracker,
            {'followers':0,'following':0},checkpoints.append,cancelled=lambda:True)
        self.assertEqual(result['error'],'owner_cancelled')
        self.assertEqual(result['state'],'interrupted')
        self.assertTrue(checkpoints)
        page.wait_for_timeout.assert_not_called()
        with self.assertRaisesRegex(RuntimeError,'owner_cancelled'):
            settle_without_scrolling(page,Mock(),'followers',time.monotonic()+30,Mock(),tracker,
                {'followers':0,'following':0},{},{},None,cancelled=lambda:True)
        page.wait_for_timeout.assert_not_called()
