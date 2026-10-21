"""Serve demo assets, synthetic jobs and optional saved observations on loopback.

The operator API credential never enters the browser. No real target can be
submitted for collection, and no report/session directory is served.
"""
from pathlib import Path
import re
import secrets

from backend.pilot.api import Handler, PilotServer
from backend.pilot.queue import QueueError
from backend.identity import TARGET, OBSERVER

ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    '/icon.svg': ('icon.svg', 'image/svg+xml'),
}


class SitePreviewServer(PilotServer):
    def __init__(self, address, queue, asset_directory, *, saved_analysis=None, live_collection=None):
        self.asset_directory = Path(asset_directory).resolve()
        self.saved_analysis = saved_analysis
        self.live_collection = live_collection
        super().__init__(address, queue, secrets.token_urlsafe(32))
        self.RequestHandlerClass = SiteHandler


class SiteHandler(Handler):
    def write_allowed(self):
        origin = self.headers.get('Origin')
        expected = f'http://127.0.0.1:{self.server.server_port}'
        if origin not in (None, expected) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise QueueError('origin_not_allowed', 403)
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
            raise QueueError('body_not_allowed', 400)

    def dispatch(self):
        if self.command == 'GET' and self.path == '/demo/config':
            return 200, {'mode': 'local_queue', 'simulation': True,
                         'target_username': TARGET, 'observer_username': OBSERVER,
                         'saved_analysis_available': self.server.saved_analysis is not None,
                         'live_collection_configured': self.server.live_collection is not None,
                         'live_collection_available': bool(self.server.live_collection
                             and self.server.live_collection.available())}
        if self.path == '/local/collection' and self.command == 'POST':
            self.require_local_origin()
            if self.server.live_collection is None:
                raise QueueError('live_collection_not_enabled', 403)
            return 202, self.server.live_collection.start()
        live_match = re.fullmatch(r'/local/collection/([a-f0-9]{32})', self.path)
        if live_match and self.command in ('GET', 'DELETE'):
            if self.server.live_collection is None:
                raise QueueError('not_found', 404)
            if self.command == 'DELETE':
                self.require_local_origin()
                self.server.live_collection.cancel(live_match[1], self.token())
            return 200, self.server.live_collection.status(live_match[1], self.token())
        if self.command == 'POST' and self.path == '/local/analysis':
            self.require_local_origin()
            if self.server.saved_analysis is None:
                raise QueueError('saved_analysis_not_configured', 404)
            return 200, self.server.saved_analysis
        if self.command == 'POST' and self.path == '/demo/jobs':
            self.write_allowed()
            if self.headers.get('X-ByeFan-Demo') != '1':
                raise QueueError('demo_header_required', 403)
            return 202, self.server.queue.submit('fixture_0')
        match = re.fullmatch(r'/demo/jobs/([a-f0-9]{32})', self.path)
        if match and self.command in ('GET', 'DELETE'):
            if self.command == 'DELETE':
                self.write_allowed()
                self.server.queue.cancel(match[1], self.token())
            return 200, self.server.queue.status(match[1], self.token())
        raise QueueError('not_found', 404)

    def require_local_origin(self):
        self.write_allowed()
        if (self.headers.get('Origin') != f'http://127.0.0.1:{self.server.server_port}'
                or self.headers.get('X-ByeFan-Local') != '1'):
            raise QueueError('local_origin_required', 403)

    def do_GET(self):
        if self.path in ('/local-analysis.js', '/local-analysis.css'):
            suffix = self.path.rsplit('.', 1)[1]
            payload = (Path(__file__).parent / 'assets' / ('analysis.' + suffix)).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8' if suffix == 'js' else 'text/css; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)
            return
        asset = ASSETS.get(self.path)
        if not asset:
            return self.handle_request()
        path, mime = asset
        try:
            payload = (self.server.asset_directory / path).read_bytes()
            if path == 'index.html':
                payload = payload.replace(b'</head>', b'<link rel="stylesheet" href="/local-analysis.css"><script src="/local-analysis.js" defer></script></head>')
        except OSError:
            return self.send_json(404, {'error': 'asset_missing'})
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)
