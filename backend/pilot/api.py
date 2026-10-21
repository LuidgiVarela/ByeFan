"""Loopback-only HTTP prototype. Not a public production server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import sqlite3
import threading

from backend.pilot.queue import QueueError


class PilotServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True

    def __init__(self, address, queue, admission_token):
        if address[0] != '127.0.0.1':
            raise ValueError('loopback_only')
        if len(admission_token) < 32:
            raise ValueError('admission_token_minimum_32_characters')
        self.queue, self.admission_token = queue, admission_token
        self.slots = threading.BoundedSemaphore(64)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        self.slots.acquire()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ByeFanLocalPilot'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # No tokens, paths, usernames or request bodies in access logs.

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if status in (429, 503):
            self.send_header('Retry-After', '5')
        self.end_headers()
        self.wfile.write(payload)

    def token(self):
        value = self.headers.get('Authorization', '')
        return value[7:] if value.startswith('Bearer ') else ''

    def admission(self):
        if not secrets.compare_digest(self.token(), self.server.admission_token):
            raise QueueError('unauthorized', 401)

    def dispatch(self):
        if self.path == '/health' and self.command == 'GET':
            self.admission()
            return 200, {'simulation': True, 'live_collection_enabled': False}
        if self.path == '/jobs' and self.command == 'POST':
            self.admission()
            if self.headers.get('Transfer-Encoding'):
                raise QueueError('unsupported_transfer_encoding', 400)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise QueueError('json_required', 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise QueueError('invalid_content_length', 400) from None
            if not 0 < length <= 4096:
                raise QueueError('body_size_invalid', 413)
            try:
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise QueueError('invalid_json', 400) from None
            if not isinstance(body, dict) or set(body) - {'username', 'coverage_target'}:
                raise QueueError('invalid_fields', 400)
            return 202, self.server.queue.submit(body.get('username'), body.get('coverage_target', 98))
        match = re.fullmatch(r'/jobs/([a-f0-9]{32})', self.path)
        if match and self.command in ('GET', 'DELETE'):
            ticket = match.group(1)
            if self.command == 'DELETE':
                self.server.queue.cancel(ticket, self.token())
            return 200, self.server.queue.status(ticket, self.token())
        raise QueueError('not_found', 404)

    def handle_request(self):
        try:
            status, body = self.dispatch()
        except QueueError as exc:
            status, body = exc.status, {'error': exc.code}
        except sqlite3.OperationalError:
            status, body = 503, {'error': 'storage_busy_or_unavailable'}
        except Exception:
            status, body = 500, {'error': 'internal_error'}
        try:
            self.send_json(status, body)
        except (OSError, TimeoutError):
            pass

    do_GET = handle_request
    do_POST = handle_request
    do_DELETE = handle_request
