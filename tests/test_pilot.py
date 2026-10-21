from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest

from backend.pilot.queue import JobQueue, QueueError
from backend.pilot.api import PilotServer
from backend.pilot.worker import AccessRestricted, SyntheticCollector, Worker, WorkerLock
from scripts.benchmark_pilot import request, scenario


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.path = Path(self.temp.name) / 'jobs.sqlite3'
        self.queue = JobQueue(self.path, ['fixture_0', 'fixture_1'], clock=lambda: self.now)

    def submit(self, target='fixture_0', coverage=98):
        return self.queue.submit(target, coverage)

    def status(self, ticket):
        return self.queue.status(ticket['ticket_id'], ticket['access_token'])

    def cancel(self, ticket):
        self.queue.cancel(ticket['ticket_id'], ticket['access_token'])

    def test_coalesce_independent_tickets_and_hashed_secrets(self):
        with ThreadPoolExecutor(max_workers=50) as pool:
            tickets = list(pool.map(lambda _: self.submit(), range(50)))
        self.assertEqual(sum(t['reused'] == 'new' for t in tickets), 1)
        self.assertEqual(len({t['ticket_id'] for t in tickets}), 50)
        with self.assertRaises(QueueError):
            self.queue.status(tickets[0]['ticket_id'], tickets[1]['access_token'])
        with self.queue.connection() as db:
            serialized = str(list(db.iterdump()))
        self.assertNotIn(tickets[0]['access_token'], serialized)

    def test_only_one_claim_even_across_independent_connections(self):
        self.submit()
        self.submit('fixture_1')
        second = JobQueue(self.path, ['fixture_0', 'fixture_1'])
        with ThreadPoolExecutor(max_workers=20) as pool:
            claims = list(pool.map(lambda i: (self.queue if i % 2 else second).claim(), range(20)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_cancellation_preserves_other_subscriber(self):
        first, second = self.submit(), self.submit()
        self.cancel(first)
        self.assertEqual(self.status(first)['state'], 'cancelled')
        self.assertEqual(self.status(second)['state'], 'queued')
        Worker(self.queue, SyntheticCollector(0)).step()
        self.assertIsNone(self.status(first)['result'])
        self.assertEqual(self.status(second)['state'], 'completed')

    def test_last_cancellation_skips_queued_work(self):
        ticket = self.submit()
        self.cancel(ticket)
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.submit()['reused'], 'new')

    def test_running_cancellation_is_observable_and_result_hidden(self):
        ticket = self.submit()
        job = self.queue.claim()
        self.cancel(ticket)
        self.assertTrue(self.queue.cancelled(job['id']))
        self.queue.finish(job['id'], error='worker_stopped')
        self.assertEqual(self.status(ticket)['state'], 'cancelled')

    def test_cache_expiry_quality_key_and_integrity_preserved(self):
        ticket = self.submit()
        Worker(self.queue, SyntheticCollector(0)).step()
        result = self.status(ticket)['result']
        self.assertFalse(result['integrity_verified'])
        self.assertIsNone(result['non_followers'])
        self.assertEqual(self.submit()['reused'], 'cache')
        self.assertEqual(self.submit(coverage=99)['reused'], 'new')
        self.now += 61
        self.assertEqual(self.submit()['reused'], 'new')

    def test_capacity_still_allows_coalescing(self):
        queue = JobQueue(self.path, ['fixture_0', 'fixture_1'], capacity=1)
        queue.submit('fixture_0')
        self.assertEqual(queue.submit('fixture_0')['reused'], 'inflight')
        with self.assertRaises(QueueError) as caught:
            queue.submit('fixture_1')
        self.assertEqual(caught.exception.status, 429)

    def test_restriction_pauses_all_work_and_no_automatic_retry(self):
        first, second = self.submit(), self.submit('fixture_1')
        def restricted(*args):
            raise AccessRestricted('must not store upstream secrets')
        Worker(self.queue, restricted).step()
        self.assertEqual(self.status(first)['error'], 'access_restricted')
        self.assertEqual(self.status(second)['state'], 'queued')
        self.assertTrue(self.status(second)['queue_paused'])
        self.assertIsNone(self.queue.claim())
        with self.assertRaises(QueueError):
            self.submit()
        self.queue.resume()
        self.assertEqual(self.queue.claim()['target'], 'fixture_1')

    def test_restart_never_requeues_uncertain_job(self):
        ticket = self.submit()
        self.queue.claim()
        with WorkerLock(self.path):
            restarted = JobQueue(self.path, ['fixture_0', 'fixture_1'])
            self.assertEqual(restarted.recover_after_worker_exit(), 1)
        self.assertEqual(self.status(ticket)['state'], 'interrupted')
        self.assertIsNone(restarted.claim())

    def test_worker_lock_prevents_second_owner(self):
        with WorkerLock(self.path):
            with self.assertRaises(RuntimeError):
                with WorkerLock(self.path):
                    self.fail('lock admitted a second worker')
            child = subprocess.run([sys.executable, '-c',
                'import sys\nfrom backend.pilot.worker import WorkerLock\n'
                'try:\n with WorkerLock(sys.argv[1]): pass\n'
                'except RuntimeError:\n sys.exit(23)\n', str(self.path)],
                capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 23)
        with WorkerLock(self.path):
            pass

    def test_retention_removes_ticket_and_job(self):
        ticket = self.submit()
        Worker(self.queue, SyntheticCollector(0)).step()
        self.now += 86401
        self.assertEqual(self.queue.purge(), 1)
        with self.assertRaises(QueueError):
            self.status(ticket)

    def test_real_target_rejected_and_no_fake_definitive_result(self):
        with self.assertRaises(QueueError):
            self.submit('example_target')
        self.submit()
        job = self.queue.claim()
        with self.assertRaises(ValueError):
            self.queue.finish(job['id'], {'simulation': True, 'comparison_status': 'verified'})

    def test_collector_exception_is_sanitized_and_pauses(self):
        ticket = self.submit()
        def broken(*args):
            raise RuntimeError('secret-password-token')
        Worker(self.queue, broken).step()
        self.assertEqual(self.status(ticket)['error'], 'collector_failed')
        self.assertNotIn('secret-password-token', json.dumps(self.status(ticket)))
        self.assertIsNone(self.queue.claim())


class PilotHTTPTests(unittest.TestCase):
    def test_http_authentication_validation_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / 'jobs.sqlite3', ['fixture_0'])
            token = secrets.token_urlsafe(32)
            with self.assertRaises(ValueError):
                PilotServer(('0.0.0.0', 0), queue, token)
            server = PilotServer(('127.0.0.1', 0), queue, token)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05})
            thread.start()
            try:
                port = server.server_port
                self.assertEqual(request(port, 'POST', '/jobs', 'wrong', {'username':'fixture_0'})[0], 401)
                self.assertEqual(request(port, 'POST', '/jobs', token, {'username':'example_target'})[0], 403)
                self.assertEqual(request(port, 'POST', '/jobs', token, {'username':'fixture_0','password':'x'})[0], 400)
                self.assertEqual(request(port, 'POST', '/jobs', token, {'username':'x'*5000})[0], 413)
                status, ticket = request(port, 'POST', '/jobs', token, {'username':'fixture_0'})
                self.assertEqual(status, 202)
                path = '/jobs/' + ticket['ticket_id']
                self.assertEqual(request(port, 'GET', path, token)[0], 404)
                self.assertEqual(request(port, 'DELETE', path, ticket['access_token'])[1]['state'], 'cancelled')
                self.assertEqual(request(port, 'GET', '/health', token)[1]['live_collection_enabled'], False)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_fifty_simultaneous_clients_distinct_and_identical_targets(self):
        for distinct in (True, False):
            with self.subTest(distinct=distinct):
                result = scenario(distinct)
                self.assertEqual(result['maximum_active_collectors'], 1)
                self.assertEqual(result['jobs_executed'], 50 if distinct else 1)
                self.assertEqual(result['cache_hits_second_wave'], 50)
                self.assertEqual(result['instagram_requests'], 0)


if __name__ == '__main__':
    unittest.main()
