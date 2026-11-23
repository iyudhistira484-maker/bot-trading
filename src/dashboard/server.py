"""Loopback-only authenticated HTTP dashboard for the paper simulator."""
from __future__ import annotations

import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from src.dashboard.auth import Auth
from src.dashboard.store import DashboardError, PaperStore

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 8192


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, auth: Auth, store: PaperStore):
        self.auth = auth
        self.store = store
        # No bind-host option: even a misconfiguration cannot expose public interfaces.
        super().__init__(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = 'PaperDashboard'
    sys_version = ''

    def log_message(self, format, *args):
        # URLs, headers and errors can contain secrets; never print requests.
        return

    def _valid_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get('Host', '') in {f'127.0.0.1:{port}', f'localhost:{port}'}

    def _valid_origin(self) -> bool:
        return self.headers.get('Origin', '') == 'http://' + self.headers.get('Host', '')

    def _token(self) -> str:
        try:
            jar = cookies.SimpleCookie()
            jar.load(self.headers.get('Cookie', ''))
            return jar['paper_session'].value if 'paper_session' in jar else ''
        except cookies.CookieError:
            return ''

    def _headers(self, code: int, content_type: str, length: int, extra: dict | None = None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _send(self, code: int, payload: bytes, content_type: str, extra: dict | None = None):
        self._headers(code, content_type, len(payload), extra)
        self.wfile.write(payload)

    def _json(self, code: int, data: dict, extra: dict | None = None):
        payload = json.dumps(data, allow_nan=False, separators=(',', ':')).encode('utf-8')
        self._send(code, payload, 'application/json; charset=utf-8', extra)

    def _body(self) -> dict:
        if self.headers.get('Transfer-Encoding') or not self.headers.get('Content-Type', '').lower().startswith('application/json'):
            raise DashboardError('Permintaan tidak valid.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('bad length')
            data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('bad number')))
            if not isinstance(data, dict):
                raise ValueError('bad body')
            return data
        except (ValueError, UnicodeDecodeError):
            self.close_connection = True
            raise DashboardError('Permintaan tidak valid.') from None

    def _static(self, name: str, content_type: str):
        self._send(200, (STATIC / name).read_bytes(), content_type)

    def do_GET(self):
        if not self._valid_host():
            self._json(400, {'error': 'Host tidak diizinkan.'}); return
        path = urlsplit(self.path).path
        csrf = self.server.auth.session(self._token())
        if path == '/':
            self._static('app.html' if csrf else 'login.html', 'text/html; charset=utf-8'); return
        static = {'/static/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/static/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/static/login.js': ('login.js', 'text/javascript; charset=utf-8'),
                  '/static/icon.svg': ('icon.svg', 'image/svg+xml')}
        if path in static:
            self._static(*static[path]); return
        if not csrf:
            self._json(401, {'error': 'Silakan login.'}); return
        try:
            if path == '/api/session': self._json(200, {'csrf': csrf})
            elif path == '/api/status': self._json(200, self.server.store.status())
            elif path == '/api/history': self._json(200, {'orders': self.server.store.history()})
            elif path == '/api/settings': self._json(200, self.server.store.settings())
            else: self._json(404, {'error': 'Halaman tidak ditemukan.'})
        except (DashboardError, sqlite3.Error):
            self._json(503, {'error': 'Data paper belum tersedia atau sedang terkunci.'})
        except Exception:
            self._json(500, {'error': 'Gagal membaca data paper.'})

    def do_POST(self):
        if not self._valid_host() or not self._valid_origin():
            self._json(403, {'error': 'Asal permintaan tidak diizinkan.'}); return
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path == '/api/login':
                if set(body) != {'password'}:
                    raise DashboardError('Permintaan tidak valid.')
                result = self.server.auth.login(body['password'], self.client_address[0])
                if not result:
                    self._json(401, {'error': 'Password salah atau percobaan terlalu banyak. Tunggu lima menit.'}); return
                token, _ = result
                self._json(200, {'ok': True}, {'Set-Cookie': f'paper_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600'})
                return
            token = self._token()
            csrf = self.server.auth.session(token)
            if not csrf:
                self._json(401, {'error': 'Silakan login.'}); return
            if not hmac.compare_digest(csrf, self.headers.get('X-CSRF-Token', '')):
                self._json(403, {'error': 'Permintaan tidak diizinkan.'}); return
            if path == '/api/logout':
                self.server.auth.logout(token)
                self._json(200, {'ok': True}, {'Set-Cookie': 'paper_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
            elif path == '/api/control':
                if set(body) != {'action'} or not isinstance(body['action'], str):
                    raise DashboardError('Permintaan tidak valid.')
                self.server.store.control(body['action'])
                print(json.dumps({'event': 'dashboard_control', 'action': body['action']}), flush=True)
                self._json(200, {'ok': True})
            elif path == '/api/settings':
                self.server.store.save_settings(body)
                print(json.dumps({'event': 'dashboard_settings_updated'}), flush=True)
                self._json(200, {'ok': True})
            else:
                self._json(404, {'error': 'Halaman tidak ditemukan.'})
        except DashboardError as exc:
            self._json(409, {'error': str(exc)})
        except sqlite3.Error:
            self._json(503, {'error': 'Database tidak tersedia atau terkunci.'})
        except Exception:
            self._json(500, {'error': 'Operasi gagal; periksa status service.'})
