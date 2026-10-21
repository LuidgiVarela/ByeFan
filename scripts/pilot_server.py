"""Local synthetic API + one worker. No flag enables real Instagram access."""
import argparse
import os
from pathlib import Path
import threading

from backend.pilot.api import PilotServer
from backend.pilot.queue import JobQueue
from backend.pilot.worker import SyntheticCollector, Worker, WorkerLock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic', action='store_true', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--database', type=Path, default=Path('reports/pilot/queue.sqlite3'))
    parser.add_argument('--job-seconds', type=float, default=0.1)
    parser.add_argument('--resume', action='store_true', help='Manually resume admission; never retries interrupted jobs')
    parser.add_argument('--purge', action='store_true', help='Delete terminal jobs older than 24 hours and exit')
    args = parser.parse_args()
    token = os.environ.get('BYEFAN_PILOT_TOKEN', '')
    if len(token) < 32 and not args.purge:
        parser.error('Set BYEFAN_PILOT_TOKEN locally with at least 32 characters; do not commit it.')
    queue = JobQueue(args.database, [f'fixture_{i}' for i in range(100)])
    if args.purge:
        print({'deleted_jobs': queue.purge()})
        return
    collector = SyntheticCollector(args.job_seconds)
    stop = threading.Event()
    # Hold the OS lock before recovering state or accepting HTTP requests.
    with WorkerLock(queue.path):
        queue.recover_after_worker_exit()
        if args.resume:
            queue.resume()
        server = PilotServer(('127.0.0.1', args.port), queue, token)
        worker = Worker(queue, collector)

        def consume():
            while not stop.is_set():
                if not worker.step(stop):
                    stop.wait(0.05)

        thread = threading.Thread(target=consume, name='synthetic-worker')
        thread.start()
        print(f'LOCAL SIMULATION ONLY: http://127.0.0.1:{server.server_port}; no Instagram access')
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
