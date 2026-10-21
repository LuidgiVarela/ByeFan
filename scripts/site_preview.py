"""Local demo, saved analysis, or explicitly armed single live collection."""
import argparse
import json
from pathlib import Path
import threading

from backend.pilot.queue import JobQueue
from backend.pilot.site_preview import SitePreviewServer
from backend.pilot.worker import SyntheticCollector, Worker, WorkerLock
from backend.services.relationships import analyze_snapshot, analyze_history

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--saved-report', type=Path, help='Read a saved authorized report; no live collection')
    parser.add_argument('--previous-report', type=Path, help='Optional earlier saved observation for inconclusive history')
    parser.add_argument('--repeat-report', type=Path, help='Saved independent repeat of the current report')
    parser.add_argument('--reference', type=Path, help='Owner-supplied independent reference JSON')
    parser.add_argument('--previous-repeat', type=Path)
    parser.add_argument('--previous-reference', type=Path)
    parser.add_argument('--live-attempt', help='Explicitly authorize one UI-triggered attempt; unique local receipt name')
    args = parser.parse_args(argv)
    if args.previous_report and not args.saved_report:
        parser.error('--previous-report requires --saved-report')
    if bool(args.repeat_report) != bool(args.reference):
        parser.error('--repeat-report and --reference must be provided together')
    if args.repeat_report and not args.saved_report:
        parser.error('verification requires --saved-report')
    if bool(args.previous_repeat) != bool(args.previous_reference):
        parser.error('--previous-repeat and --previous-reference must be provided together')
    if args.previous_repeat and not args.previous_report:
        parser.error('previous verification requires --previous-report')
    return args


def load_saved_analysis(args):
    def read(path):
        return json.loads(path.read_text(encoding='utf-8')) if path else None

    if not args.saved_report:
        return None
    current = read(args.saved_report)
    if args.previous_report:
        return analyze_history(read(args.previous_report), current,
            previous_repeat=read(args.previous_repeat), previous_reference=read(args.previous_reference),
            current_repeat=read(args.repeat_report), current_reference=read(args.reference))
    return analyze_snapshot(current, repeat=read(args.repeat_report), reference=read(args.reference))


def main():
    args = parse_args()
    analysis = load_saved_analysis(args)
    live = None
    if args.live_attempt:
        from backend.pilot.live_collection import LocalCollection
        live = LocalCollection(ROOT / 'reports/live-pilot', args.live_attempt)
    # Keep an already-open saved-report/demo server usable while the live pilot
    # runs on another port. Both queues still accept synthetic work only.
    queue_path = 'reports/live-pilot/demo-queue.sqlite3' if live else 'reports/site-preview/queue.sqlite3'
    queue = JobQueue(ROOT / queue_path, ['fixture_0'])
    stop = threading.Event()
    with WorkerLock(queue.path):
        queue.recover_after_worker_exit()
        if args.resume:
            queue.resume()
        queue.purge()
        server = SitePreviewServer(('127.0.0.1', args.port), queue, ROOT / 'site/dist', saved_analysis=analysis, live_collection=live)
        worker = Worker(queue, SyntheticCollector(3))

        def consume():
            try:
                while not stop.is_set():
                    if not worker.step(stop):
                        stop.wait(.1)
            except Exception:
                stop.set()
                server.shutdown()

        thread = threading.Thread(target=consume, name='website-synthetic-worker')
        thread.start()
        print(f'Local: http://127.0.0.1:{server.server_port} — saved analysis: {analysis is not None}; live attempt armed: {bool(live and live.available())}', flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            if live:
                live.close()
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
