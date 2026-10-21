"""Fifty concurrent local HTTP clients; synthetic collector; no Instagram calls."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import secrets
import statistics
import tempfile
import threading
import time

from backend.pilot.api import PilotServer
from backend.pilot.queue import JobQueue
from backend.pilot.worker import SyntheticCollector, Worker


def request(port, method, path, token, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None,
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def scenario(distinct=True, clients=50):
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as directory:
        queue = JobQueue(Path(directory) / 'queue.sqlite3', [f'fixture_{i}' for i in range(clients)])
        admission = secrets.token_urlsafe(32)
        server = PilotServer(('127.0.0.1', 0), queue, admission)
        serving = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
        serving.start()
        stop = threading.Event()
        state = {'calls': 0, 'active': 0, 'max_active': 0}
        mutex = threading.Lock()
        synthetic = SyntheticCollector(0.02)

        def collector(*args):
            with mutex:
                state['calls'] += 1
                state['active'] += 1
                state['max_active'] = max(state['max_active'], state['active'])
            try:
                return synthetic(*args)
            finally:
                with mutex:
                    state['active'] -= 1

        worker = threading.Thread(target=Worker(queue, collector).run, args=(stop,))
        barrier = threading.Barrier(clients)

        def submit(index):
            barrier.wait(timeout=20)
            began = time.perf_counter()
            status, data = request(server.server_port, 'POST', '/jobs', admission,
                                   {'username': f'fixture_{index if distinct else 0}'})
            assert status == 202, (status, data)
            return data, time.perf_counter() - began

        def wait_for_result(ticket):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status, data = request(server.server_port, 'GET', '/jobs/' + ticket['ticket_id'],
                                       ticket['access_token'])
                assert status == 200, data
                if data['state'] == 'completed':
                    assert data['simulation'] and not data['result']['integrity_verified']
                    return data
                assert data['state'] in ('queued', 'running'), data
                time.sleep(0.1)
            raise AssertionError('local_benchmark_timeout')

        try:
            with ThreadPoolExecutor(max_workers=clients) as pool:
                submissions = list(pool.map(submit, range(clients)))
                tickets = [item[0] for item in submissions]
                latencies = sorted(item[1] * 1000 for item in submissions)
                assert len({item['ticket_id'] for item in tickets}) == clients
                # A ticket from one caller must not authorize another ticket.
                status, _ = request(server.server_port, 'GET', '/jobs/' + tickets[0]['ticket_id'],
                                    tickets[1]['access_token'])
                assert status == 404
                worker.start()
                results = list(pool.map(wait_for_result, tickets))
                assert all(result['target'] == f'fixture_{index if distinct else 0}'
                           for index, result in enumerate(results))
                # A second wave within TTL reuses stored quality, never re-collects.
                cached = list(pool.map(submit, range(clients)))
                assert all(item[0]['reused'] == 'cache' for item in cached)
            assert state['calls'] == (clients if distinct else 1), state
            assert state['max_active'] == 1, state
            return {'mode': 'offline_loopback_simulation', 'clients': clients,
                    'distinct_targets': clients if distinct else 1,
                    'jobs_executed': state['calls'], 'maximum_active_collectors': state['max_active'],
                    'cache_hits_second_wave': clients, 'isolated_tickets': clients,
                    'admission_median_ms': round(statistics.median(latencies), 3),
                    'admission_p95_ms': round(latencies[int(0.95*clients+0.999)-1], 3),
                    'elapsed_seconds': round(time.perf_counter()-started, 3),
                    'synthetic_job_seconds': 0.02, 'instagram_requests': 0,
                    'real_records_collected': 0, 'instagram_capacity_validated': False}
        finally:
            stop.set()
            if worker.ident is not None:
                worker.join(timeout=20)
            server.shutdown()
            serving.join()
            server.server_close()


def main():
    result = {'distinct': scenario(True), 'same_target': scenario(False)}
    output = Path('reports/pilot/benchmark.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
