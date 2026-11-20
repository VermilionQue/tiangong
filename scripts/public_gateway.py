#!/usr/bin/env python3
"""Authenticated, loopback-only ingress for a TLS tunnel. Never publish port 8765.

Configuration, salted password hash, session hashes and rate counters live outside
the synced checkout. Client headers are untrusted by default. The optional
cloudflare-tunnel mode is ONLY for an owned tunnel forwarding to this loopback
listener, with no other ingress or same-zone Workers rewriting client identity.
Cloudflare documents CF-Connecting-IP and Tunnel's origin guarantee at:
https://developers.cloudflare.com/fundamentals/reference/http-headers/
"""

import runtime_config
import argparse
import getpass
import gzip
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs

import tenant_storage
import lan_dns
from user_accounts import Accounts

from public_documents import PublicDocuments, PublicationError, byte_range, valid_name
from release_assets import published_assets, STYLE_BUNDLE, SCRIPT_BUNDLE

ROOT = Path(__file__).resolve().parents[1]
SECURITY = runtime_config.STATE_DIR / 'security'
COOKIE = '__Host-tiangong_session'
USERNAME = runtime_config.ADMIN_USERNAME
ITERATIONS = 600_000  # OWASP PBKDF2-HMAC-SHA256 minimum, October 2026.
SESSION_SECONDS = 12 * 3600
IDLE_SECONDS = 3600
REMEMBER_SECONDS = 30 * 24 * 3600
STATIC = {'/', '/index.html', '/app.webmanifest', '/assets/travel-places.json', '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png', '/icons/apple-touch-icon.png', '/icons/apple-touch-icon-167.png', '/icons/apple-touch-icon-152.png'}
# Source files remain local. Only the verified current release exposes code.
STATIC = {path for path in STATIC if Path(path).suffix.lower() not in ('.js', '.css')}
BUFFER_LIMIT = 16 * 1024 * 1024


def static_resource(path, content_type):
    """Only shipped, account-independent assets may be retained by a browser."""
    if path not in STATIC and not STYLE_BUNDLE.fullmatch(path) and not SCRIPT_BUNDLE.fullmatch(path):
        return False
    media_type = content_type.split(';')[0].strip().lower()
    expected = {'.css': {'text/css'}, '.js': {'application/javascript', 'text/javascript'},
                '.json': {'application/json'}, '.ttf': {'font/ttf', 'application/x-font-ttf'},
                '.woff2': {'font/woff2', 'application/font-woff2'},
                '.webp': {'image/webp'}, '.svg': {'image/svg+xml'}}
    return media_type in expected.get(Path(path).suffix.lower(), set())


def accepts_gzip(value):
    encodings = {}
    for item in (value or '').split(','):
        parts = item.strip().lower().split(';')
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.strip().startswith('q='):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0.0
        encodings[parts[0]] = quality
    return 0 < encodings.get('gzip', encodings.get('*', 0)) <= 1
API_GET = {'/api/recipes','/api/crm','/api/crm/detail','/api/finance/hub','/api/receipts','/api/receipts/history','/api/todos', '/api/todos/sync', '/api/daily-quote', '/api/status', '/api/wellness',
           '/api/notes', '/api/work', '/api/travel', '/api/finance', '/api/checkins', '/api/treasures', '/api/games', '/api/games/connection', '/api/people', '/api/people/reminders', '/api/people/export'}
API_POST = {'/api/recipes','/api/quanku/shared','/api/crm','/api/crm/upload','/api/finance/manage','/api/receipts','/api/receipts/upload','/api/work', '/api/wellness/report', '/api/notes', '/api/todos', '/api/todos/sync', '/api/travel', '/api/finance', '/api/checkins', '/api/travel/upload', '/api/treasures', '/api/treasures/use', '/api/treasures/upload', '/api/games', '/api/games/connection', '/api/games/sync', '/api/weather', '/api/people', '/api/people/upload'}
PUBLIC_APP_ASSETS = {'/app.webmanifest':'application/manifest+json', '/offline.html':'text/html; charset=utf-8',
                     '/icons/icon.svg':'image/svg+xml', '/icons/icon-192.png':'image/png',
                     '/icons/icon-512.png':'image/png', '/icons/icon-maskable-512.png':'image/png',
                     '/icons/apple-touch-icon.png':'image/png', '/icons/apple-touch-icon-167.png':'image/png',
                     '/icons/apple-touch-icon-152.png':'image/png'}
ASSET_TYPES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf', '.m4a', '.mp3', '.wav'}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self'; font-src 'self'; connect-src 'self'; "
       "form-action 'self'; manifest-src 'self'; base-uri 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'")


def canonical_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError('公网入口必须是 HTTPS 域名，例如 https://journal.example.com')
    name = parsed.hostname.lower()
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise ValueError('请配置公网域名，不能使用 IP 地址')
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', name):
        raise ValueError('域名格式无效；国际域名请使用 ASCII/Punycode')
    return 'https://' + name


class SecurityState:
    def __init__(self, folder=SECURITY, clock=time.time):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.folder.chmod(0o700)
        self.config_path = self.folder / 'gateway.json'
        self.db_path = self.folder / 'gateway.sqlite3'
        self.clock = clock
        self.next_cleanup = 0
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, generation TEXT NOT NULL,
                    created REAL NOT NULL, seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS limits (
                    name TEXT PRIMARY KEY, started REAL NOT NULL, count INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS limits_started_idx ON limits(started);
            ''')
            # Preserve existing sessions and the password record on upgrades.
            # Older sessions keep their original 12-hour / 1-hour policy.
            con.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in con.execute('PRAGMA table_info(sessions)')}
            if 'remember' not in columns:
                con.execute('ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 0 CHECK (remember IN (0, 1))')

        self.accounts = Accounts(self)
        secret_file = self.folder / 'upstream-secret'
        try:
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_hex(32))
        self.upstream_secret = secret_file.read_text().strip()
        if not re.fullmatch('[0-9a-f]{64}', self.upstream_secret):
            raise ValueError('服务间认证配置无效')

    def connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=5)
        con.row_factory = sqlite3.Row
        self.db_path.chmod(0o600)
        return con

    def config(self):
        try:
            data = json.loads(self.config_path.read_text())
            data['origin'] = canonical_origin(data['origin'])
            if data.get('trusted_proxy', 'none') not in ('none', 'cloudflare-tunnel'):
                return {}
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def write_config(self, data):
        # Same-directory atomic replace avoids a partially configured live ingress.
        temp = self.folder / ('.config-' + secrets.token_hex(8))
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.config_path)

    def configure(self, origin, trusted_proxy='none'):
        if trusted_proxy not in ('none', 'cloudflare-tunnel'):
            raise ValueError('无效的可信代理模式')
        data = self.config()
        data['origin'] = canonical_origin(origin)
        data['trusted_proxy'] = trusted_proxy
        if trusted_proxy == 'cloudflare-tunnel':
            data.setdefault('client_rate_key', secrets.token_hex(32))
        self.write_config(data)
        self.revoke_all()

    @staticmethod
    def ready(data):
        password = data.get('password', {})
        return (bool(data.get('origin')) and password.get('algorithm') == 'pbkdf2-sha256'
                and password.get('iterations') == ITERATIONS
                and bool(re.fullmatch('[0-9a-f]{32}', str(password.get('salt', ''))))
                and bool(re.fullmatch('[0-9a-f]{64}', str(password.get('hash', ''))))
                and bool(data.get('generation'))
                and (data.get('trusted_proxy') != 'cloudflare-tunnel'
                     or bool(re.fullmatch('[0-9a-f]{64}', str(data.get('client_rate_key', ''))))))

    def set_password(self, value):
        data = self.config()
        if not data.get('origin'):
            raise ValueError('请先配置公网域名')
        if not isinstance(value, str) or len(value) < 14 or len(value.encode('utf-8')) > 1024:
            raise ValueError('密码至少 14 个字符，最长 1024 字节')
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, ITERATIONS)
        data.update(password={'algorithm': 'pbkdf2-sha256', 'iterations': ITERATIONS,
                              'salt': salt.hex(), 'hash': digest.hex()},
                    generation=secrets.token_hex(16))
        self.write_config(data)
        self.revoke_all()

    def verify_password(self, value, data):
        if not self.ready(data) or not isinstance(value, str) or len(value.encode('utf-8')) > 1024:
            return False
        record = data['password']
        digest = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(record['salt']), ITERATIONS)
        return hmac.compare_digest(digest.hex(), record['hash'])

    def rate(self, name, maximum, seconds):
        """Atomic global counters, independent of any claimed forwarded IP."""
        now = self.clock()
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.cleanup_limits(con, now)
            row = con.execute('SELECT * FROM limits WHERE name=?', (name,)).fetchone()
            if row and now < row['started'] + seconds and row['count'] >= maximum:
                return max(1, math.ceil(row['started'] + seconds - now))
            started = row['started'] if row and now < row['started'] + seconds else now
            count = row['count'] + 1 if row and started == row['started'] else 1
            con.execute('INSERT OR REPLACE INTO limits VALUES (?,?,?)', (name, started, count))
        return 0

    def cleanup_limits(self, con, now):
        # All windows are at most 15 minutes; retain one day for conservative
        # restart/clock handling, then prune. Index avoids scanning live rows.
        if now >= self.next_cleanup:
            con.execute('DELETE FROM limits WHERE started < ?', (now - 86400,))
            self.next_cleanup = now + 60

    def reserve_client_login(self, client_key):
        """Reserve both budgets before hashing, including concurrent requests.

        The per-client reservation becomes a failure unless refunded on success.
        A blocked client does not consume the global budget. Reservations left by
        a process crash expire with the window rather than permitting a bypass.
        """
        now = self.clock()
        name = 'cf-login:' + client_key
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.cleanup_limits(con, now)
            reservations = []
            for bucket, maximum in ((name, 5), ('cf-login-total', 60)):
                row = con.execute('SELECT * FROM limits WHERE name=?', (bucket,)).fetchone()
                active = row is not None and now < row['started'] + 900
                if active and row['count'] >= maximum:
                    return max(1, math.ceil(row['started'] + 900 - now)), None
                reservations.append((bucket, row['started'] if active else now,
                                     row['count'] + 1 if active else 1))
            con.executemany('INSERT OR REPLACE INTO limits VALUES (?,?,?)', reservations)
        return 0, reservations[0][:2]

    def refund_successful_login(self, reservation):
        name, started = reservation
        with self.connect() as con:
            con.execute('UPDATE limits SET count=count-1 WHERE name=? AND started=? AND count>0',
                        (name, started))

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def new_session(self, data, remember=False, user_id='owner'):
        user = self.accounts.record(user_id, data)
        if not user or user['status'] != 'active':
            raise ValueError('账号暂不可用')
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.connect() as con:
            con.execute('DELETE FROM sessions WHERE created <= ? OR (remember=0 AND (created <= ? OR seen <= ?))',
                        (now - REMEMBER_SECONDS, now - SESSION_SECONDS, now - IDLE_SECONDS))
            con.execute('INSERT INTO sessions (digest,generation,created,seen,remember,user_id) VALUES (?,?,?,?,?,?)',
                        (self.digest(token), user['generation'], now, now, int(remember), user_id))
        return token

    def principal(self, token, data):
        if not token or not re.fullmatch('[A-Za-z0-9_-]{43}', token):
            return None
        now = self.clock()
        with self.connect() as con:
            row = con.execute('SELECT * FROM sessions WHERE digest=?', (self.digest(token),)).fetchone()
        user = self.accounts.record(row['user_id'], data) if row else None
        lifetime = REMEMBER_SECONDS if row and row['remember'] else SESSION_SECONDS
        if (not row or not user or user['status'] != 'active' or row['generation'] != user['generation']
                or now - row['created'] >= lifetime
                or (not row['remember'] and now - row['seen'] >= IDLE_SECONDS)):
            self.revoke(token)
            return None
        with self.connect() as con:
            con.execute('UPDATE sessions SET seen=? WHERE digest=?', (now, self.digest(token)))
        return self.accounts.identity(user['id'], data)

    def authenticated(self, token, data):
        return self.principal(token, data) is not None

    def revoke(self, token):
        with self.connect() as con:
            con.execute('DELETE FROM sessions WHERE digest=?', (self.digest(token),))

    def revoke_all(self):
        with self.connect() as con:
            con.execute('DELETE FROM sessions')


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, state, upstream_port=8765, public_root=None):
        if address[0] != '127.0.0.1':
            raise ValueError('网关只能监听 127.0.0.1')
        self.state = state
        self.upstream_port = upstream_port
        self.release_assets = published_assets(ROOT / 'src/web')
        self.public_documents = PublicDocuments(public_root if public_root is not None
                                                else ROOT / 'src/public/documents')
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(address, GatewayHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(timeout=1):
            # A stylesheet burst used to be silently dropped at 24 connections.
            # Queue short bursts; if still busy, return a retriable HTTP response.
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\n'
                                b'Content-Length: 0\r\nRetry-After: 1\r\n'
                                b'Cache-Control: no-store\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class GatewayHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 + close avoids ambiguous keepalive / request-smuggling boundaries.
    server_version = 'Tiangong'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, *args):
        pass  # Never log credentials, cookies, private URLs, or notes.

    def headers_out(self, status, content_type, length=None, extra=None):
        self.response_started = True
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if length is not None:
            self.send_header('Content-Length', str(length))
        headers = {
            'Cache-Control': 'private, no-store, max-age=0',
            'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': CSP,
            'Strict-Transport-Security': 'max-age=31536000',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(self)',
            'Cross-Origin-Resource-Policy': 'same-origin',
            'Cross-Origin-Opener-Policy': 'same-origin', 'Connection': 'close',
        }
        headers.update(extra or {})
        for key, value in headers.items():
            if value is not None:
                self.send_header(key, value)
        self.end_headers()
        self.close_connection = True

    def reply(self, status, body, extra=None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.headers_out(status, 'application/json; charset=utf-8', len(raw), extra)
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': '请求无法处理'})

    def login_page(self, status=200, message='', extra=None, remember=True):
        page = (ROOT / 'src/gateway/login.html').read_text().replace(
            '<!-- gateway-message -->', html.escape(message))
        if not remember:
            page = page.replace('value="1" checked', 'value="1"')
        raw = page.encode()
        # Native form POSTs use navigate mode. Under no-referrer, browsers send
        # Origin: null, which our strict CSRF boundary correctly rejects. Keep
        # the same-origin form origin while withholding referrers off-site.
        login_headers = {**(extra or {}), 'Referrer-Policy': 'same-origin'}
        self.headers_out(status, 'text/html; charset=utf-8', len(raw), login_headers)
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def login_error(self, status, message, extra=None, remember=False):
        if self.headers.get_content_type() == 'application/x-www-form-urlencoded':
            return self.login_page(status, message, extra, remember=remember)
        return self.reply(status, {'error': message}, extra)

    def token(self):
        values = self.headers.get_all('Cookie', [])
        if len(values) != 1 or len(values[0]) > 4096:
            return ''
        cookie = SimpleCookie()
        try:
            cookie.load(values[0])
            return cookie[COOKIE].value if COOKIE in cookie else ''
        except CookieError:
            return ''

    def client_key(self, data):
        if data.get('trusted_proxy', 'none') != 'cloudflare-tunnel':
            return None
        values = self.headers.get_all('CF-Connecting-IP', [])
        if len(values) != 1 or '%' in values[0] or len(values[0]) > 64:
            raise ValueError('无效的隧道来源')
        address = ipaddress.ip_address(values[0].strip())
        # Equivalent IPv4 / IPv4-mapped IPv6 representations share one budget.
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return hmac.new(bytes.fromhex(data['client_rate_key']),
                        str(address).encode('ascii'), hashlib.sha256).hexdigest()

    def dispatch(self):
        state = self.server.state
        data = state.config()
        if not state.ready(data):
            return self.reply(503, {'error': '天工录尚未开放，请稍后再来'})
        host = urlsplit(data['origin']).netloc
        if self.headers.get_all('Host') != [host]:
            return self.reply(421, {'error': '请使用已配置的天工录域名'})
        try:
            client_key = self.client_key(data)
        except ValueError:
            return self.reply(403, {'error': '请从天工录的安全入口访问'})
        transfer = self.headers.get_all('Transfer-Encoding', [])
        lengths = self.headers.get_all('Content-Length', [])
        # The trusted tunnel sometimes frames bodyless GET/HEAD as chunked.
        # Accept only the exact empty terminator; never forward framed input.
        framing_ok = len(lengths) <= 1
        if transfer:
            framing_ok = (self.command in ('GET', 'HEAD') and not lengths
                          and [v.strip().lower() for v in transfer] == ['chunked'])
            if framing_ok:
                previous_timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(2)
                    framing_ok = self.rfile.read(5) == b'0\r\n\r\n'
                except (OSError, socket.timeout):
                    framing_ok = False
                finally:
                    self.connection.settimeout(previous_timeout)
        if not framing_ok:
            return self.reply(400, {'error': '请求格式无效'})
        raw_path = urlsplit(self.path)
        decoded = unquote(raw_path.path, errors='strict')
        request_target = self.requestline.split()[1]
        if (not self.path.startswith('/') or request_target.startswith('//') or raw_path.scheme
                or raw_path.netloc or raw_path.fragment or len(self.path) > 4096
                or '\\' in decoded or '%' in decoded or any(ord(c) < 32 for c in decoded)
                or any(part in ('.', '..') for part in decoded.split('/'))):
            return self.reply(400, {'error': '请求路径无效'})
        path = decoded
        if path.startswith(lan_dns.PREFIX):
            return lan_dns.handle(self, data, path, raw_path.query, client_key)
        if path == '/documents' or path.startswith('/documents/'):
            # Public customer documents have their own budgets. Loading many
            # images must not use up the private application's request quota.
            retry = state.rate('public-client:' + client_key, 600, 60) if client_key else 0
            if not retry:
                retry = state.rate('public-requests', 12000, 60)
            if retry:
                return self.reply(429, {'error': '访问过于频繁，请稍后重试'},
                                  {'Retry-After': str(retry)})
            if self.command not in ('GET', 'HEAD'):
                return self.reply(405, {'error': '此页面仅支持浏览'}, {'Allow': 'GET, HEAD'})
            if self.headers.get('Content-Length') not in (None, '0'):
                return self.reply(400, {'error': '请求格式无效'})
            if path == '/documents':
                return self.reply(308, {'ok': True}, {'Location': '/documents/'})
            return self.public_document(path[len('/documents/'):])
        retry = state.rate('all-requests', 600, 60)
        if retry:
            return self.reply(429, {'error': '访问过于频繁，请稍后重试'}, {'Retry-After': str(retry)})
        if self.command == 'POST':
            if (self.headers.get_all('Origin') != [data['origin']]
                    or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin')):
                return self.reply(403, {'error': '请在天工录页面内操作'})
        elif self.headers.get('Sec-Fetch-Site') == 'cross-site' and path.startswith(('/api/','/travel-assets/','/treasure-assets/','/people-assets/')):
            return self.reply(403, {'error': '请在天工录页面内访问'})
        if self.command in ('GET', 'HEAD'):
            if self.headers.get('Content-Length') not in (None, '0'):
                return self.reply(400, {'error': '请求格式无效'})
            if path == '/company':
                return self.reply(308, {'ok': True}, {'Location': '/company/'})
            if path == '/company/':
                raw = (ROOT / 'src/gateway/company.html').read_bytes()
                return self.representation(raw, 'text/html; charset=utf-8', cacheable=True)
            if path in PUBLIC_APP_ASSETS:
                asset = ROOT / 'src/web' / path.lstrip('/')
                if not asset.is_file():
                    return self.reply(404, {'error': '文件不存在'})
                raw = asset.read_bytes()
                return self.representation(raw, PUBLIC_APP_ASSETS[path], cacheable=True)
            if path == '/login':
                if state.authenticated(self.token(), data):
                    return self.reply(303, {'ok': True}, {'Location': '/'})
                return self.login_page()
            if path == '/guest':
                raw = (ROOT / 'src/gateway/guest.html').read_bytes()
                self.headers_out(200, 'text/html; charset=utf-8', len(raw), {'Referrer-Policy':'same-origin'})
                if self.command != 'HEAD': self.wfile.write(raw)
                return
            if path == '/join':
                raw = (ROOT / 'src/gateway/join.html').read_bytes()
                self.headers_out(200, 'text/html; charset=utf-8', len(raw))
                if self.command != 'HEAD': self.wfile.write(raw)
                return
            if path in ('/auth/login.css', '/auth/login.js', '/auth/account-bootstrap.js', '/auth/join.js'):
                filename = {'/auth/login.css': 'login.css',
                            '/auth/login.js': 'login.js', '/auth/account-bootstrap.js':'account-bootstrap.js', '/auth/join.js':'join.js'}[path]
                types = {'.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript'}
                raw = (ROOT / 'src/gateway' / filename).read_bytes()
                return self.representation(raw, types[Path(filename).suffix] + '; charset=utf-8', cacheable=True)
        if path == '/auth/login' and self.command == 'POST':
            reservation = None
            if client_key is None:
                retry = state.rate('login-attempts', 5, 900)
            else:
                retry, reservation = state.reserve_client_login(client_key)
            if retry:
                return self.login_error(429, '尝试次数较多，请十五分钟后再试', {'Retry-After': str(retry)})
            payload = self.read_login()
            user = state.accounts.authenticate(payload.get('username'), payload.get('password'), data)
            if not user:
                return self.login_error(401, '用户名或密码不正确，请再试一次',
                                        remember=payload.get('remember', False))
            if reservation:
                state.refund_successful_login(reservation)
            state.revoke(self.token())
            remember = payload.get('remember', False)
            token = state.new_session(data, remember=remember, user_id=user['id'])
            extra = {'Set-Cookie': self.cookie(token, REMEMBER_SECONDS if remember else SESSION_SECONDS)}
            if self.headers.get_content_type() == 'application/x-www-form-urlencoded':
                extra['Location'] = '/'
                return self.reply(303, {'ok': True}, extra)
            return self.reply(200, {'ok': True}, extra)
        if path in ('/auth/invitation', '/auth/activate', '/auth/demo', '/auth/guest') and self.command == 'POST':
            retry = state.rate('enrollment:' + (client_key or 'local'), 20, 900)
            if retry:
                return self.reply(429, {'error':'操作较频繁，请稍后重试'}, {'Retry-After':str(retry)})
            if path in ('/auth/demo', '/auth/guest'):
                if int(self.headers.get('Content-Length', '0')):
                    self.read_body(4096)
                user_id = state.accounts.create_guest() if path == '/auth/guest' else 'demo'
            else:
                payload = self.read_json(4096)
                if path == '/auth/invitation':
                    return self.reply(200, {'user': state.accounts.invitation(payload.get('token'))})
                user_id = state.accounts.activate(payload.get('token'), payload.get('password'))['id']
            state.revoke(self.token())
            token = state.new_session(data, user_id=user_id)
            extra = {'Set-Cookie':self.cookie(token)}
            if path in ('/auth/demo', '/auth/guest'):
                extra['Location'] = '/#today'
                return self.reply(303, {'ok':True}, extra)
            return self.reply(200, {'ok':True}, extra)
        token = self.token()
        self.account = state.principal(token, data)
        if not self.account:
            if path in ('/', '/index.html') and self.command in ('GET', 'HEAD'):
                return self.reply(303, {'error': '请先登录'}, {'Location': '/login'})
            return self.reply(401, {'error': '请先登录天工录'})
        if path == '/api/quanku/shared' and self.account['role'] in ('guest', 'demo'):
            return self.reply(403, {'error':'共享账本仅向受邀正式账号开放'})
        claimed = self.headers.get_all('X-Tiangong-Client-Account', [])
        if claimed and claimed != [self.account['id']]:
            return self.reply(409, {'error':'账号已切换，请刷新后继续'}, {'X-Tiangong-Account-Changed':'1'})
        if path == '/auth/logout' and self.command == 'POST':
            self.read_json(4096)
            state.revoke(token)
            return self.reply(200, {'ok': True}, {'Set-Cookie': self.cookie('', 0)})
        if path == '/auth/session' and self.command in ('GET', 'HEAD'):
            return self.reply(200, {'authenticated': True, 'username': self.account['username'], 'user':self.account})
        if path == '/auth/users':
            if self.account['role'] != 'admin':
                return self.reply(403, {'error':'仅管理员可管理账号'})
            if self.command in ('GET', 'HEAD'):
                return self.reply(200, {'users':state.accounts.list()})
            if self.command == 'POST':
                return self.reply(200, state.accounts.manage(self.read_json(4096)))
        if self.account['role'] == 'demo' and self.command == 'POST' and path != '/api/weather':
            return self.reply(403, {'error':'这是只读体验空间，修改不会保存。请登录自己的账号。'})
        if path == '/auth/profile' and self.command == 'POST':
            return self.reply(200, {'user':state.accounts.settings(self.account['id'], self.read_json(4096))})
        if path == '/auth/password' and self.command == 'POST':
            if self.account['role'] == 'guest':
                return self.reply(403, {'error':'游客无需设置密码；请从游客入口重新开始体验。'})
            retry = state.rate('password:' + self.account['id'], 5, 900)
            if retry:
                return self.reply(429, {'error':'请十五分钟后再试'}, {'Retry-After':str(retry)})
            payload = self.read_json(4096)
            state.accounts.change_password(self.account['id'], payload.get('old_password'), payload.get('new_password'))
            return self.reply(200, {'ok':True}, {'Set-Cookie':self.cookie('',0)})
        allowed = (path in STATIC or path in API_GET or path in self.server.release_assets
                   or bool(re.fullmatch(r'/(?:travel-assets|treasure-assets|receipt-assets|people-assets)/[0-9a-f]{64}\.webp',path))
                   or bool(re.fullmatch(r'/crm-assets/[0-9a-f]{64}\.(?:xlsx|xls|pdf|png|jpg|jpeg|webp|docx|doc)',path))
                   or bool(re.fullmatch(r'/api/games/covers/[0-9a-f]{64}\.(?:jpg|png|webp)',path))
                   or bool(re.fullmatch(r'/api/games/steam-covers/[1-9][0-9]{0,9}\.jpg',path))
                   or (path.startswith('/api/notes/asset/') and Path(path).suffix.lower() in ASSET_TYPES)
                   or bool(re.fullmatch(r'/api/report/[A-Za-z0-9_-]{1,100}(?:/[0-9a-f]{64}\.(?:pdf|png|jpg|jpeg))?', path)))
        body = None
        if self.command == 'POST':
            allowed = path in API_POST
            if allowed:
                payload = self.read_json(18 * 1024 * 1024 if path in ('/api/crm/upload','/api/wellness/report', '/api/notes', '/api/travel/upload', '/api/treasures/upload', '/api/receipts/upload', '/api/people/upload', '/api/games') else 200000)
                if path == '/api/recipes' and payload.get('action') in ('invite', 'join') and self.account['role'] in ('guest', 'demo'):
                    return self.reply(403, {'error':'家庭共享需要受邀正式账号；游客可体验自己的厨房'})
                if path == '/api/recipes' and payload.get('action') == 'analysis-request' and self.account['role'] in ('guest', 'demo'):
                    return self.reply(403, {'error':'山君饮食分析需要正式账号；营养估算可直接体验'})
                body = json.dumps(payload, ensure_ascii=False).encode()
        if not allowed:
            return self.reply(404, {'error': '页面不存在'})
        target = raw_path.path
        if path in ('/api/crm/detail','/api/receipts', '/api/receipts/history','/api/finance/hub') and raw_path.query:
            query = parse_qs(raw_path.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            expected = 'id' if path in ('/api/crm/detail','/api/receipts/history') else 'month'
            if set(query) != {expected} or len(query[expected]) != 1:
                raise ValueError('查询条件无效')
            target += '?' + raw_path.query
        self.proxy(target, body)

    def public_document(self, name):
        name = name or 'index.html'
        if not valid_name(name):
            return self.reply(404, {'error': '页面不存在'})
        publication = self.server.public_documents
        try:
            files = publication.manifest()
            if name not in files:
                return self.reply(404, {'error': '页面不存在'})
            stream, size, etag, content_type = publication.open_verified(name, files)
        except (OSError, PublicationError):
            return self.reply(503, {'error': '答疑资料正在更新，请稍后再试'})
        with stream:
            extra = {'Cache-Control': 'public, max-age=300, must-revalidate',
                     'Pragma': None, 'ETag': etag, 'X-Robots-Tag': 'noindex, nofollow'}
            if content_type.startswith('text/html'):
                extra['Cache-Control'] = 'public, max-age=0, must-revalidate'
            pdf = content_type == 'application/pdf'
            if pdf:
                extra['Accept-Ranges'] = 'bytes'
            conditions = ','.join(self.headers.get_all('If-None-Match', []))
            if any(item.strip().removeprefix('W/') in (etag, '*')
                   for item in conditions.split(',')):
                return self.headers_out(304, content_type, extra=extra)
            start, end, status = 0, size - 1, 200
            ranges = self.headers.get_all('Range', [])
            if (pdf and self.command == 'GET' and ranges
                    and self.headers.get('If-Range', etag) == etag):
                try:
                    if len(ranges) != 1 or len(ranges[0]) > 128:
                        raise ValueError('无效范围')
                    start, end = byte_range(ranges[0], size)
                except ValueError:
                    extra['Content-Range'] = 'bytes */' + str(size)
                    return self.headers_out(416, content_type, 0, extra)
                status = 206
                extra['Content-Range'] = f'bytes {start}-{end}/{size}'
            length = max(0, end - start + 1)
            self.headers_out(status, content_type, length, extra)
            if self.command == 'HEAD':
                return
            stream.seek(start)
            while length:
                chunk = stream.read(min(128 * 1024, length))
                if not chunk:
                    break
                self.wfile.write(chunk)
                length -= len(chunk)

    def read_body(self, limit):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= limit:
            raise ValueError('提交内容为空或超过大小限制')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError('提交内容不完整')
        return raw

    def read_login(self):
        if self.headers.get_content_type() == 'application/x-www-form-urlencoded':
            fields = parse_qs(self.read_body(4096).decode('utf-8'), keep_blank_values=True,
                              strict_parsing=True, max_num_fields=3, errors='strict')
            if any(len(values) != 1 for values in fields.values()):
                raise ValueError('登录字段不能重复')
            payload = {key: values[0] for key, values in fields.items()}
            if payload.get('remember', '') not in ('', '1'):
                raise ValueError('登录设置无效')
            payload['remember'] = payload.get('remember') == '1'
        else:
            payload = self.read_json(4096)
        if not isinstance(payload.get('remember', False), bool):
            raise ValueError('登录设置无效')
        return payload

    def read_json(self, limit):
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('请发送 JSON 数据')
        value = json.loads(self.read_body(limit))
        if not isinstance(value, dict):
            raise ValueError('提交内容格式无效')
        return value

    @staticmethod
    def cookie(value, max_age=SESSION_SECONDS):
        return f'{COOKIE}={value}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Strict'

    def representation(self, raw, content_type, cacheable=False, path=''):
        extra = {'Vary': 'Accept-Encoding'}
        if cacheable:
            digest = hashlib.sha256(raw).hexdigest()
            etag = 'W/"' + digest + '"'
            policy = 'private, max-age=0, must-revalidate'
            bundle = STYLE_BUNDLE.fullmatch(path) or SCRIPT_BUNDLE.fullmatch(path)
            if bundle and bundle.group(1) != digest[:16]:
                return self.reply(502, {'error': '页面资源正在更新，请刷新后重试'})
            if bundle:
                policy = 'private, max-age=31536000, immutable'
            elif path in ('/assets/fonts/pipi-diary.ttf', '/assets/fonts/pipi-diary-v39.woff2'):
                policy = 'private, max-age=604800, must-revalidate'
            extra.update({'Cache-Control': policy, 'Pragma': None, 'ETag': etag})
            conditions = ','.join(self.headers.get_all('If-None-Match', []))
            if any(item.strip().removeprefix('W/') in (etag[2:], '*')
                   for item in conditions.split(',')):
                return self.headers_out(304, content_type, extra=extra)
        media_type = content_type.split(';')[0].strip().lower()
        compressible = (media_type.startswith('text/') or media_type in
                        ('application/json', 'application/javascript', 'image/svg+xml',
                         'font/ttf', 'application/x-font-ttf'))
        if len(raw) >= 1024 and compressible and accepts_gzip(self.headers.get('Accept-Encoding')):
            compressed = gzip.compress(raw, compresslevel=5, mtime=0)
            if len(compressed) < len(raw):
                raw = compressed
                extra['Content-Encoding'] = 'gzip'
        self.headers_out(200, content_type, len(raw), extra)
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def proxy(self, path, body):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=40 if path=='/api/games/sync' else 20)
        upstream_host = '127.0.0.1:' + str(self.server.upstream_port)
        try:
            # Never forward user Host, cookies, Origin, proxy IP or authorization.
            headers = {'Host': upstream_host, 'Accept-Encoding': 'identity', 'Connection': 'close'}
            headers.update(tenant_storage.headers(self.server.state.upstream_secret, self.account['id'],
                                                  'POST' if body is not None else 'GET', path))
            if body is not None:
                headers.update({'Origin': 'http://' + upstream_host, 'Content-Type': 'application/json'})
            # Use GET for HEAD: existing API HEAD falls through to static serving.
            connection.request('POST' if body is not None else 'GET', path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader('Content-Type', 'application/octet-stream')
            if response.status >= 500:
                return self.reply(502, {'error': '服务暂时不可用，请稍后重试'})
            if response.status >= 400 and 'text/html' in content_type:
                return self.reply(response.status, {'error': '请求无法处理'})
            if response.status in (301, 302, 303, 307, 308):
                return self.reply(502, {'error': '页面暂时无法读取'})
            if path in ('/', '/index.html') and response.status == 200:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return self.reply(502, {'error': '页面暂时无法读取'})
                # Install the 401 interceptor before ordinary scripts at the
                # bottom of the page start their first API requests.
                bootstrap = ('<meta name="tiangong-account" content="' + html.escape(json.dumps(self.account, ensure_ascii=False), quote=True) +
                             '"><link rel="stylesheet" href="/auth/login.css"><script src="/auth/account-bootstrap.js"></script><script src="/auth/login.js"></script>').encode()
                raw = raw.replace(b'<head>', b'<head>' + bootstrap, 1)
                self.representation(raw, content_type)
            else:
                length = response.getheader('Content-Length')
                cacheable = body is None and static_resource(path, content_type)
                is_json = content_type.split(';')[0].strip().lower() == 'application/json'
                if (response.status == 200 and length and length.isdigit()
                        and int(length) <= BUFFER_LIMIT and (cacheable or is_json)):
                    raw = response.read(int(length) + 1)
                    if len(raw) != int(length):
                        return self.reply(502, {'error': '页面未完整读取，请稍后重试'})
                    return self.representation(raw, content_type, cacheable, path)
                self.headers_out(response.status, content_type, int(length) if length and length.isdigit() else None)
                if self.command != 'HEAD':
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
        except (OSError, http.client.HTTPException):
            if getattr(self, 'response_started', False):
                # A truncated upstream response must close; never append a second response.
                self.close_connection = True
            else:
                self.reply(502, {'error': '天工录暂时无法连接，请稍后重试'})
        finally:
            connection.close()

    def handle_safe(self):
        try:
            self.dispatch()
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self.reply(400, {'error': '请求内容无效，请检查后重试'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            if not getattr(self, 'response_started', False):
                self.reply(503, {'error': '天工录暂时不可用，请稍后再试'})
            self.close_connection = True

    do_GET = handle_safe
    do_HEAD = handle_safe
    do_POST = handle_safe


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='天工录安全公网入口')
    parser.add_argument('--security-dir', type=Path, default=SECURITY, help='安全配置目录（不得放入网盘同步目录）')
    commands = parser.add_subparsers(dest='command', required=True)
    configure = commands.add_parser('configure')
    configure.add_argument('--origin', required=True)
    configure.add_argument('--trusted-proxy', choices=['none', 'cloudflare-tunnel'], default='none',
                           help='仅自有 Cloudflare Tunnel 到本机回环入口时才能启用 cloudflare-tunnel')
    commands.add_parser('set-password')
    commands.add_parser('revoke-sessions')
    commands.add_parser('status')
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    state = SecurityState(args.security_dir)
    try:
        if args.command == 'configure':
            state.configure(args.origin, args.trusted_proxy)
            print('域名与入口模式已配置；未设置密码时保持关闭。')
        elif args.command == 'set-password':
            if not os.isatty(0):
                raise ValueError('请在交互式终端中设置密码；不接受命令参数、文件或管道中的密码')
            first = getpass.getpass('设置天工录登录密码（至少14个字符，不会显示）：')
            second = getpass.getpass('再次输入密码：')
            if first != second:
                raise ValueError('两次密码不同，未作修改')
            state.set_password(first)
            print('密码已设置，旧登录会话已退出。密码不会写入项目或日志。')
        elif args.command == 'revoke-sessions':
            state.revoke_all()
            print('所有公网登录会话已退出。')
        elif args.command == 'status':
            data = state.config()
            print(json.dumps({'origin': data.get('origin'), 'password_configured': state.ready(data),
                              'username': USERNAME,
                              'trusted_proxy': data.get('trusted_proxy', 'none'),
                              'listen': '127.0.0.1:8770'}, ensure_ascii=False))
        elif args.command == 'serve':
            server = GatewayServer(('127.0.0.1', args.port), state)
            print(f'天工录安全入口监听 127.0.0.1:{args.port}；未配置密码时拒绝访问。', flush=True)
            server.serve_forever()
    except (ValueError, KeyboardInterrupt) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
