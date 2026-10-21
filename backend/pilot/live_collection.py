"""One locally authorized attempt; no automatic retries, login or arbitrary targets.

The synthetic queue deliberately remains separate. Admission is persisted before
starting the worker so a crash/restart cannot reuse an attempt identifier.
"""
import copy
import json
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

from backend.experimental.hybrid_collector import GATE_ERRORS, run_once
from backend.pilot.queue import QueueError
from backend.services.relationships import analyze_snapshot, TARGET, OBSERVER


class LocalCollection:
    def __init__(self, directory, attempt, *, runner=run_once):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', attempt):
            raise ValueError('invalid_attempt_identifier')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.receipt = self.directory / (attempt + '.json')
        self.runner = runner
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.closed = False
        self.ticket = uuid.uuid4().hex
        self.secret = secrets.token_urlsafe(32)
        self.state = 'already_used' if self.receipt.exists() else 'ready'
        self.progress = {'elapsed_seconds': 0, 'phase': 'starting', 'followers': 0, 'following': 0}
        self.result = None
        self.error = None

    def available(self):
        with self.lock:
            return self.state == 'ready' and not self.closed and not self.receipt.exists()

    def start(self):
        with self.lock:
            if self.state != 'ready' or self.closed:
                raise QueueError('attempt_already_used', 409)
            try:
                # Exclusive creation also protects two server processes using
                # the same named authorization. Never persist the ticket secret.
                with self.receipt.open('x', encoding='utf-8') as stream:
                    json.dump({'target': TARGET, 'observer': OBSERVER,
                        'admitted_at': time.time(), 'attempt_consumed': True,
                        'coverage_target': 98, 'work_budget_seconds': 710,
                        'max_cycles_per_list': 300}, stream)
                    stream.flush()
                    import os
                    os.fsync(stream.fileno())
            except FileExistsError:
                self.state = 'already_used'
                raise QueueError('attempt_already_used', 409) from None
            self.state = 'running'
            self.thread = threading.Thread(target=self._run, name='byefan-single-live-attempt')
            try:
                self.thread.start()
            except Exception:
                self.state, self.error = 'interrupted', 'worker_start_failed'
                raise QueueError('worker_start_failed', 503) from None
            return {'ticket_id': self.ticket, 'access_token': self.secret, 'simulation': False}

    def _progress(self, report):
        summary = {'elapsed_seconds': report.get('elapsed_seconds', 0), 'phase': 'starting'}
        for direction in ('followers', 'following'):
            item = report.get(direction) or {}
            summary[direction] = len(item.get('structured_rows', []))
            if item:
                summary['phase'] = direction
        with self.lock:
            self.progress = summary

    def _run(self):
        state, error, result = 'interrupted', 'collector_failed', None
        try:
            _, _, report = self.runner(98, viewport_height=800,
                cancelled=self.stop.is_set, on_progress=self._progress)
            code = report.get('error')
            if code in GATE_ERRORS - {'id_username_conflict'} or code == 'observer_session_not_confirmed_in_ui':
                state, error = 'restricted', 'access_or_session_restricted'
            elif code == 'id_username_conflict':
                error = 'insufficient_or_inconsistent_data'
            elif self.stop.is_set() or code == 'owner_cancelled':
                state, error = 'cancelled', 'owner_cancelled'
            elif code:
                error = code if code in {'existing_browser_profile_missing', 'worker_already_active',
                    'global_time_limit', 'target_profile_unavailable'} else 'collector_failed'
            else:
                # Revalidate raw evidence; never trust a stored definitive label.
                result = analyze_snapshot(report)
                result.update(mode='live_observation', live_query_performed=True)
                state, error = 'completed', None
        except ValueError:
            error = 'insufficient_or_inconsistent_data'
        except Exception:
            pass  # Never expose browser exception text or sensitive response data.
        with self.lock:
            if self.stop.is_set() and state != 'restricted':
                state, error, result = 'cancelled', 'owner_cancelled', None
            self.state, self.error, self.result = state, error, result

    def _authorize(self, ticket, token):
        if ticket != self.ticket or not secrets.compare_digest(self.secret, token):
            raise QueueError('ticket_not_found', 404)

    def status(self, ticket, token):
        with self.lock:
            self._authorize(ticket, token)
            return copy.deepcopy({'state': self.state, 'target': TARGET, 'simulation': False,
                'cancel_requested': self.stop.is_set(), 'progress': self.progress,
                'error': self.error, 'result': self.result if self.state == 'completed' else None})

    def cancel(self, ticket, token):
        with self.lock:
            self._authorize(ticket, token)
            if self.state == 'running':
                self.stop.set()

    def close(self):
        with self.lock:
            self.closed = True
            self.stop.set()
            thread = self.thread
        if thread:
            thread.join()  # Cooperative shutdown; no Playwright calls from HTTP threads.
