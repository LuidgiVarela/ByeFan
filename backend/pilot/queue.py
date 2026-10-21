"""SQLite work coalescing, capability-protected tickets and bounded admission.

One active job per database. Never automatically retry an interrupted job.
This pilot accepts only explicit allowlisted synthetic targets.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import threading
import uuid


class QueueError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class JobQueue:
    def __init__(self, path, allowed_targets, *, capacity=100, cache_seconds=60,
                 clock=time.time):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.allowed_targets = frozenset(allowed_targets)
        if not self.allowed_targets or any(not name.startswith('fixture_')
                                           for name in self.allowed_targets):
            raise ValueError('synthetic_targets_only')
        if capacity < 1 or cache_seconds < 0:
            raise ValueError('invalid_queue_configuration')
        self.capacity, self.cache_seconds, self.clock = capacity, cache_seconds, clock
        # SQLite has a single writer. Queue threads locally instead of making
        # them compete through SQLite's busy-timeout backoff. Cross-process
        # correctness still comes from BEGIN IMMEDIATE, not this optimization.
        self._write_lock = threading.Lock()
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id=1), paused TEXT);
                INSERT OR IGNORE INTO control VALUES (1, NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, target TEXT NOT NULL, work_key TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
                    result TEXT, error TEXT, progress INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS jobs_lookup ON jobs(work_key,state,finished);
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                    secret_hash TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS tickets_job ON tickets(job_id,cancelled);
            ''')

    @contextmanager
    def connection(self, write=False):
        with self._write_lock if write else nullcontext():
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('PRAGMA synchronous=FULL')
                if write:
                    db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def submit(self, target, coverage_target=98):
        if not isinstance(target, str):
            raise QueueError('invalid_target', 400)
        target = target.strip().lower().removeprefix('@')
        if target not in self.allowed_targets:
            raise QueueError('synthetic_target_not_allowlisted', 403)
        if type(coverage_target) is not int or coverage_target not in (98, 99, 100):
            raise QueueError('invalid_coverage_target', 400)
        # Version and quality policy are part of cache identity. No snapshot merging.
        key = f'simulation-v1:{target}:{coverage_target}'
        now, ticket, token = self.clock(), uuid.uuid4().hex, secrets.token_urlsafe(32)
        with self.connection(write=True) as db:
            if db.execute('SELECT paused FROM control').fetchone()[0]:
                raise QueueError('queue_paused', 503)
            job = db.execute('''SELECT * FROM jobs WHERE work_key=? AND cancel_requested=0
                AND (state IN ('queued','running') OR (state='completed' AND finished>?))
                ORDER BY created DESC LIMIT 1''', (key, now-self.cache_seconds)).fetchone()
            reused = 'inflight' if job else 'new'
            if job:
                if db.execute('SELECT count(*) FROM tickets WHERE job_id=?',
                              (job['id'],)).fetchone()[0] >= 1000:
                    raise QueueError('subscriber_limit', 429)
                if job['state'] == 'completed':
                    reused = 'cache'
                jid = job['id']
            else:
                count = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
                if count >= self.capacity:
                    raise QueueError('queue_full', 429)
                jid = uuid.uuid4().hex
                db.execute('INSERT INTO jobs(id,target,work_key,state,created) VALUES(?,?,?,?,?)',
                           (jid, target, key, 'queued', now))
            db.execute('INSERT INTO tickets(id,job_id,secret_hash) VALUES(?,?,?)',
                       (ticket, jid, digest(token)))
        return {'ticket_id': ticket, 'access_token': token, 'reused': reused,
                'simulation': True}

    def _ticket(self, db, ticket, token):
        row = db.execute('''SELECT t.cancelled, t.secret_hash, j.rowid AS sequence, j.* FROM tickets t
            JOIN jobs j ON j.id=t.job_id WHERE t.id=?''', (ticket,)).fetchone()
        if not row or not secrets.compare_digest(row['secret_hash'], digest(token)):
            raise QueueError('ticket_not_found', 404)
        return row

    def status(self, ticket, token):
        with self.connection() as db:
            row = self._ticket(db, ticket, token)
            paused = db.execute('SELECT paused FROM control').fetchone()[0]
            state = 'cancelled' if row['cancelled'] else row['state']
            ahead = db.execute("""SELECT count(*) FROM jobs WHERE state IN ('queued','running')
                AND (created<? OR (created=? AND rowid<?))""",
                (row['created'], row['created'], row['sequence'])).fetchone()[0]
            return {'ticket_id': ticket, 'target': row['target'], 'state': state,
                    'simulation': True, 'queue_paused': bool(paused),
                    'jobs_ahead': ahead if state == 'queued' else None,
                    'progress_percent': row['progress'] if state != 'cancelled' else None,
                    'created_at': row['created'], 'started_at': row['started'],
                    'finished_at': row['finished'], 'error': row['error'],
                    'result': json.loads(row['result']) if row['result'] and state == 'completed' else None}

    def cancel(self, ticket, token):
        with self.connection(write=True) as db:
            row = self._ticket(db, ticket, token)
            db.execute('UPDATE tickets SET cancelled=1 WHERE id=?', (ticket,))
            remaining = db.execute('SELECT count(*) FROM tickets WHERE job_id=? AND cancelled=0',
                                   (row['id'],)).fetchone()[0]
            if not remaining:
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (row['id'],))
                db.execute("UPDATE jobs SET state='cancelled', finished=? WHERE id=? AND state='queued'",
                           (self.clock(), row['id']))

    def claim(self):
        with self.connection(write=True) as db:
            if db.execute('SELECT paused FROM control').fetchone()[0]:
                return None
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET state='running', started=? WHERE id=?",
                       (self.clock(), row['id']))
            return dict(row)

    def cancelled(self, jid):
        with self.connection() as db:
            row = db.execute('SELECT state,cancel_requested FROM jobs WHERE id=?', (jid,)).fetchone()
            return not row or row['state'] != 'running' or bool(row['cancel_requested'])

    def progress(self, jid, value):
        with self.connection(write=True) as db:
            db.execute("UPDATE jobs SET progress=max(progress,?) WHERE id=? AND state='running'",
                       (max(0, min(99, int(value))), jid))

    def finish(self, jid, result=None, *, error=None, pause=False):
        # No arbitrary exception text, credentials or upstream response bodies in storage.
        if error not in (None, 'collector_failed', 'access_restricted', 'worker_stopped'):
            raise ValueError('unapproved_error_code')
        if result is not None and (result.get('simulation') is not True
                                   or result.get('non_followers') is not None
                                   or result.get('comparison_status') != 'insufficient_data'):
            raise ValueError('simulation_result_required')
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row or row['state'] != 'running':
                raise QueueError('job_not_running')
            if row['cancel_requested']:
                state, result = 'cancelled', None
            elif error:
                state, result = 'interrupted', None
            elif result is not None:
                state = 'completed'
            else:
                raise ValueError('result_or_error_required')
            db.execute('UPDATE jobs SET state=?,finished=?,result=?,error=?,progress=? WHERE id=?',
                       (state, self.clock(), json.dumps(result) if result is not None else None,
                        error, 100 if state == 'completed' else row['progress'], jid))
            if pause:
                db.execute('UPDATE control SET paused=?', (error or 'manual_review',))

    def recover_after_worker_exit(self):
        """Only call while holding WorkerLock; never requeue an uncertain execution."""
        with self.connection(write=True) as db:
            count = db.execute("""UPDATE jobs SET state='interrupted', error='worker_exit',
                finished=? WHERE state='running'""", (self.clock(),)).rowcount
            if count:
                db.execute("UPDATE control SET paused='worker_exit'")
            return count

    def resume(self):
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                raise QueueError('worker_still_running')
            db.execute('UPDATE control SET paused=NULL')

    def purge(self, older_than_seconds=86400):
        """Remove terminal jobs and all associated tickets after local retention period."""
        if older_than_seconds < 0:
            raise ValueError('invalid_retention')
        with self.connection(write=True) as db:
            cutoff = self.clock() - older_than_seconds
            db.execute('DELETE FROM tickets WHERE job_id IN (SELECT id FROM jobs WHERE finished<?)', (cutoff,))
            return db.execute('DELETE FROM jobs WHERE finished<?', (cutoff,)).rowcount
