"""Exclusive local worker; only a synthetic collector is provided."""
import os
import time


class WorkerLock:
    def __init__(self, database_path):
        self.path = str(database_path) + '.worker.lock'
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, 'a+b')
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError('worker_already_active') from None
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()  # OS releases the lock, including on process exit.
            self.handle = None


class AccessRestricted(Exception):
    pass


class WorkCancelled(Exception):
    pass


class SyntheticCollector:
    """Exercise scheduling, never invent Instagram records or access the network."""
    def __init__(self, duration=0.05):
        if not 0 <= duration <= 60:
            raise ValueError('invalid_synthetic_duration')
        self.duration = duration

    def __call__(self, job, cancelled, progress):
        started = time.monotonic()
        while time.monotonic() - started < self.duration:
            if cancelled():
                raise WorkCancelled()
            elapsed = time.monotonic() - started
            progress(100 * elapsed / self.duration)
            time.sleep(max(0, min(0.02, self.duration - elapsed)))
        return {'simulation': True, 'target': job['target'],
                'comparison_status': 'insufficient_data', 'non_followers': None,
                'integrity_verified': False, 'records_collected': 0}


class Worker:
    def __init__(self, queue, collector):
        self.queue, self.collector = queue, collector

    def step(self, stop=None):
        job = self.queue.claim()
        if job is None:
            return False
        try:
            result = self.collector(job,
                lambda: self.queue.cancelled(job['id']) or bool(stop and stop.is_set()),
                lambda value: self.queue.progress(job['id'], value))
            self.queue.finish(job['id'], result)
        except AccessRestricted:
            self.queue.finish(job['id'], error='access_restricted', pause=True)
        except WorkCancelled:
            self.queue.finish(job['id'], error='worker_stopped', pause=bool(stop and stop.is_set()))
        except Exception:
            self.queue.finish(job['id'], error='collector_failed', pause=True)
        return True

    def run(self, stop):
        with WorkerLock(self.queue.path):
            self.queue.recover_after_worker_exit()
            while not stop.is_set():
                if not self.step(stop):
                    stop.wait(0.05)
