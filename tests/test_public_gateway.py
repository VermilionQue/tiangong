"""Boundary tests use isolated security state and a disposable upstream."""
import http.client
import hashlib
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import public_gateway as gateway
import setup_public_password as setup

ORIGIN = 'https://journal.example.com'
PASSWORD = 'test-only-passphrase-with-entropy'


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, dict(self.headers)))
        raw = b'<html><head></head><body>private workspace</body></html>' if self.path == '/' else b'{"private":true}'
        content_type = 'text/html' if self.path == '/' else 'application/json'
        if self.path in self.server.resources:
            content_type, raw = self.server.resources[self.path]
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.post_requests.append((self.path, len(raw), hashlib.sha256(raw).hexdigest()))
        self.do_GET()


class GatewayTests(unittest.TestCase):
    def test_quanku_shared_requires_signed_in_non_demo_account(self):
        status, _, _ = self.request('/api/quanku/shared', 'POST', {'action':'list'})
        self.assertEqual(status, 401)
        cookie = self.login()
        status, _, _ = self.request('/api/quanku/shared', 'POST', {'action':'list'}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.post_requests[-1][0], '/api/quanku/shared')
        status, headers, _ = self.request('/auth/demo', 'POST', {})
        self.assertEqual(status, 303)
        demo = headers['Set-Cookie'].split(';')[0]
        status, _, _ = self.request('/api/quanku/shared', 'POST', {'action':'list'}, cookie=demo)
        self.assertEqual(status, 403)

    def test_company_page_is_public_without_exposing_adjacent_files(self):
        status, headers, raw = self.request('/company/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('天工录', raw.decode())
        self.assertNotIn('Set-Cookie', headers)
        head_status, head_headers, head_body = self.request('/company/', 'HEAD')
        self.assertEqual(head_status, 200)
        self.assertEqual(int(head_headers['Content-Length']), len(raw))
        self.assertEqual(head_body, b'')
        status, headers, _ = self.request('/company')
        self.assertEqual(status, 308)
        self.assertEqual(headers['Location'], '/company/')
        for path in ('/company/company.html', '/company/../../api/finance',
                     '/company/%2e%2e/api/finance', '/src/gateway/company.html',
                     '/api/finance', '/api/wellness'):
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (400, 401, 404))
        self.assertEqual(self.upstream.requests, [])

    def test_crm_routes_are_private_and_mutations_require_same_origin(self):
        paths=['/api/crm','/api/crm/detail?id=feishu-test', '/crm-assets/'+'c'*64+'.pdf']
        for path in paths:self.assertEqual(self.request(path)[0],401)
        cookie=self.login()
        for path in paths:self.assertEqual(self.request(path,cookie=cookie)[0],200)
        self.assertEqual(self.upstream.requests[-2][1],'/api/crm/detail?id=feishu-test')
        for path in ['/api/crm','/api/crm/upload']:
            self.assertEqual(self.request(path,'POST',{'name':'test'},cookie=cookie,Origin='https://evil.example.com')[0],403)
            self.assertEqual(self.request(path,'POST',{'name':'test'},cookie=cookie)[0],200)
        self.assertEqual(self.request('/crm-assets/anything.pdf',cookie=cookie)[0],404)

    def test_people_private_routes_require_login_and_same_origin(self):
        paths=['/api/people','/api/people/reminders','/api/people/export',
               '/people-assets/'+'a'*64+'.webp']
        for path in paths:self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.upstream.requests,[])
        cookie=self.login()
        for path in paths:self.assertEqual(self.request(path,cookie=cookie)[0],200)
        for path in ['/api/people','/api/people/upload']:
            self.assertEqual(self.request(path,'POST',{'name':'test'},cookie=cookie,Origin='https://evil.example.com')[0],403)
            self.assertEqual(self.request(path,'POST',{'name':'test'},cookie=cookie)[0],200)

    def test_unpublished_theme_assets_require_login_without_opening_asset_directory(self):
        paths = ['/unpublished-theme.css', '/assets/unpublished-theme.webp',
                 '/assets/unpublished-scene.webp']
        for path in paths:
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.upstream.requests, [])
        cookie = self.login()
        for path in paths:
            status, headers, _ = self.request(path + '?v=32', cookie=cookie)
            self.assertEqual(status, 404)
            self.assertIn('no-store', headers['Cache-Control'])
        self.assertEqual(self.request('/assets/unpublished-private.webp', cookie=cookie)[0], 404)

    def test_reminders_sync_controls_require_login_and_same_origin(self):
        for path in ('/api/todos/sync','/todo-sync.css','/todo-sync.js'):
            self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.upstream.requests,[])
        cookie=self.login()
        for path in ('/api/todos/sync','/todo-sync.css','/todo-sync.js'):
            self.assertEqual(self.request(path,cookie=cookie)[0],200 if path == '/api/todos/sync' else 404)
        self.assertEqual(self.request('/api/todos/sync','POST',{'action':'sync'},cookie=cookie,Origin='https://evil.example.com')[0],403)
        self.assertEqual(self.request('/api/todos/sync','POST',{'action':'sync'},cookie=cookie)[0],200)

    def test_todo_calendar_styles_require_login(self):
        self.assertEqual(self.request('/todos-v26.css?v=26')[0], 401)
        self.assertEqual(self.upstream.requests, [])
        status, headers, _ = self.request('/todos-v26.css?v=26', cookie=self.login())
        self.assertEqual(status, 404)
        self.assertIn('no-store', headers['Cache-Control'])

    def test_finance_and_checkins_require_login_and_same_origin(self):
        paths = ['/api/finance', '/api/checkins']
        for path in paths:
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.upstream.requests, [])
        cookie = self.login()
        for path in paths:
            status, headers, _ = self.request(path, cookie=cookie)
            self.assertEqual(status, 200)
            self.assertIn('no-store', headers['Cache-Control'])
        for path in paths[:2]:
            self.assertEqual(self.request(path, 'POST', {}, cookie=cookie, Origin='https://evil.example.com')[0], 403)
            self.assertEqual(self.request(path, 'POST', {}, cookie=cookie)[0], 200)
        self.assertEqual(self.request('/finance/sources/private.xlsx', cookie=cookie)[0], 404)
        self.assertEqual(self.request('/api/finance/import', 'POST', {}, cookie=cookie)[0], 404)

    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.upstream.requests = []
        cls.upstream.post_requests = []
        cls.upstream.resources = {}
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.now = 1_700_000_000
        self.state = gateway.SecurityState(self.folder.name, clock=lambda: self.now)
        self.state.configure(ORIGIN)
        self.state.set_password(PASSWORD)
        self.server = gateway.GatewayServer(('127.0.0.1', 0), self.state, self.upstream.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.upstream.requests.clear()
        self.upstream.post_requests.clear()
        self.upstream.resources.clear()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, path, method='GET', body=None, cookie=None, form=False, **headers):
        values = {'Host': 'journal.example.com'}
        if method == 'POST':
            values.update({'Origin': ORIGIN, 'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'})
            payload = body if body is not None else {}
            body = (urlencode(payload) if form else json.dumps(payload)).encode()
        if cookie:
            values['Cookie'] = cookie
        values.update(headers)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, body, values)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def login(self):
        status, headers, _ = self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def publish_customer_documents(self):
        root = Path(self.folder.name).resolve() / 'customer-documents'
        root.mkdir()
        resources = {'index.html': b'<!doctype html><title>Customer FAQ</title>',
                     'style.css': b'body { color: #222; }',
                     'plan.pdf': b'%PDF-1.4\ncustomer-plan-test-data\n%%EOF'}
        for name, raw in resources.items():
            (root / name).write_bytes(raw)
        (root / 'manifest.json').write_text(json.dumps({'files': {
            name: hashlib.sha256(raw).hexdigest() for name, raw in resources.items()}}))
        self.server.public_documents = gateway.PublicDocuments(root)
        return root, resources

    def test_public_customer_documents_allow_anonymous_get_and_head_only(self):
        _, resources = self.publish_customer_documents()
        status, headers, _ = self.request('/documents')
        self.assertEqual(status, 308)
        self.assertEqual(headers['Location'], '/documents/')
        self.assertEqual(self.request('/documents/')[2], resources['index.html'])
        for name, raw in resources.items():
            status, headers, received = self.request('/documents/' + name + '?from=customer')
            self.assertEqual((status, received), (200, raw))
            self.assertIn('public,', headers['Cache-Control'])
            self.assertNotIn('no-store', headers['Cache-Control'])
            self.assertNotIn('Pragma', headers)
            self.assertNotIn('Set-Cookie', headers)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            status, head_headers, received = self.request('/documents/' + name, 'HEAD')
            self.assertEqual((status, received), (200, b''))
            self.assertEqual(int(head_headers['Content-Length']), len(raw))
        for origin in [ORIGIN, 'https://other.example.com']:
            status, headers, _ = self.request('/documents/', 'POST', {}, Origin=origin)
            self.assertEqual(status, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
        self.assertEqual(self.upstream.requests, [])

    def test_public_namespace_never_exposes_unlisted_or_private_files(self):
        root, _ = self.publish_customer_documents()
        (root / 'unapproved.html').write_text('not approved for customers')
        for path in ['/documents/manifest.json', '/documents/unapproved.html',
                     '/documents/api/notes', '/documents/gateway.json',
                     '/documents//index.html', '/documents/.hidden', '/documents/missing.pdf']:
            self.assertEqual(self.request(path)[0], 404, path)
        for path in ['/documents/../api/notes', '/documents/%2e%2e/api/notes',
                     '/documents/%252e%252e/api/notes', '/documents/a%5cb.pdf']:
            self.assertEqual(self.request(path)[0], 400, path)
        for path in ['/api/notes', '/api/work', '/api/todos', '/api/wellness',
                     '/api/notes/asset/export/image.jpg', '/api/report/id', '/app.js', '/documents-other/']:
            self.assertEqual(self.request(path)[0], 401, path)
        self.assertEqual(self.request('/')[0], 303)
        self.assertEqual(self.request('/documents/', Host='other.example.com')[0], 421)
        self.assertEqual(self.request('/documents/', **{'Content-Length': '1'})[0], 400)
        self.assertEqual(self.upstream.requests, [])

    def test_public_pdf_ranges_and_conditional_requests(self):
        _, resources = self.publish_customer_documents()
        raw = resources['plan.pdf']
        status, headers, _ = self.request('/documents/plan.pdf')
        etag = headers['ETag']
        self.assertEqual(headers['Content-Type'], 'application/pdf')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        for condition in [etag, 'W/' + etag, '*', '"old", ' + etag]:
            status, headers, body = self.request('/documents/plan.pdf', **{'If-None-Match': condition})
            self.assertEqual((status, body), (304, b''))
            self.assertEqual(headers['ETag'], etag)
        for value, expected, span in [('bytes=2-10', raw[2:11], f'2-10/{len(raw)}'),
                                      ('bytes=-4', raw[-4:], f'{len(raw)-4}-{len(raw)-1}/{len(raw)}'),
                                      ('bytes=10-', raw[10:], f'10-{len(raw)-1}/{len(raw)}'),
                                      ('bytes=0-9999', raw, f'0-{len(raw)-1}/{len(raw)}')]:
            status, headers, body = self.request('/documents/plan.pdf', Range=value)
            self.assertEqual((status, body), (206, expected))
            self.assertEqual(headers['Content-Range'], 'bytes ' + span)
        for value in ['bytes=999-', 'bytes=-0', 'bytes=5-2', 'bytes=0-2,4-5', 'items=0-1', 'bytes=-']:
            status, headers, body = self.request('/documents/plan.pdf', Range=value)
            self.assertEqual((status, body), (416, b''))
            self.assertEqual(headers['Content-Range'], 'bytes */' + str(len(raw)))
        status, _, body = self.request('/documents/plan.pdf', Range='bytes=1-2', **{'If-Range': '"old"'})
        self.assertEqual((status, body), (200, raw))
        status, headers, body = self.request('/documents/plan.pdf', 'HEAD', Range='bytes=1-2')
        self.assertEqual((status, body, int(headers['Content-Length'])), (200, b'', len(raw)))

    def test_publication_changes_require_matching_manifest_and_reject_symlinks(self):
        root, resources = self.publish_customer_documents()
        self.assertEqual(self.request('/documents/plan.pdf')[0], 200)
        (root / 'plan.pdf').write_bytes(b'%PDF-1.4\nchanged without approval')
        self.assertEqual(self.request('/documents/plan.pdf')[0], 503)
        (root / 'plan.pdf').unlink()
        outside = root.parent / 'private.pdf'
        outside.write_bytes(resources['plan.pdf'])
        (root / 'plan.pdf').symlink_to(outside)
        self.assertEqual(self.request('/documents/plan.pdf')[0], 503)
        (root / 'manifest.json').write_text('{bad manifest')
        self.assertEqual(self.request('/documents/')[0], 503)
        self.assertEqual(self.request('/api/notes')[0], 401)
        self.assertEqual(self.upstream.requests, [])

    def test_public_request_limits_are_independent_of_private_budget(self):
        self.publish_customer_documents()
        with self.state.connect() as con:
            con.execute('INSERT INTO limits VALUES (?,?,?)', ('all-requests', self.now, 600))
        self.assertEqual(self.request('/login')[0], 429)
        self.assertEqual(self.request('/documents/')[0], 200)
        with self.state.connect() as con:
            con.execute('DELETE FROM limits WHERE name=?', ('all-requests',))
            con.execute('UPDATE limits SET count=12000 WHERE name=?', ('public-requests',))
        self.assertEqual(self.request('/documents/')[0], 429)
        self.assertEqual(self.request('/login')[0], 200)
        with self.state.connect() as con:
            self.assertEqual(con.execute('SELECT count FROM limits WHERE name=?', ('all-requests',)).fetchone()[0], 1)

    def test_public_client_budget_keeps_other_customers_and_login_available(self):
        self.publish_customer_documents()
        self.state.configure(ORIGIN, trusted_proxy='cloudflare-tunnel')
        client = {'CF-Connecting-IP': '203.0.113.10'}
        self.assertEqual(self.request('/documents/')[0], 403)
        self.assertEqual(self.request('/documents/', **client)[0], 200)
        with self.state.connect() as con:
            con.execute("UPDATE limits SET count=600 WHERE name LIKE 'public-client:%'")
        self.assertEqual(self.request('/documents/', **client)[0], 429)
        self.assertEqual(self.request('/documents/', **client, **{'X-Forwarded-For': '203.0.113.99'})[0], 429)
        self.assertEqual(self.request('/documents/', **{'CF-Connecting-IP': '203.0.113.11'})[0], 200)
        self.assertEqual(self.request('/login', **client)[0], 200)

    def test_install_files_are_readable_without_login_with_exact_mime_and_head(self):
        resources = {
            '/app.webmanifest': 'application/manifest+json',
            '/offline.html': 'text/html; charset=utf-8',
            '/icons/icon.svg': 'image/svg+xml',
            '/icons/icon-192.png': 'image/png',
            '/icons/icon-512.png': 'image/png',
            '/icons/icon-maskable-512.png': 'image/png',
            '/icons/apple-touch-icon.png': 'image/png',
            '/icons/apple-touch-icon-167.png': 'image/png',
            '/icons/apple-touch-icon-152.png': 'image/png',
        }
        for path, content_type in resources.items():
            expected = (gateway.ROOT / 'src/web' / path.lstrip('/')).read_bytes()
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    status, headers, body = self.request(path + '?source=install', method)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Type'], content_type)
                    self.assertEqual(int(headers['Content-Length']), len(expected))
                    self.assertEqual(body, expected if method == 'GET' else b'')
                    self.assertNotIn('Set-Cookie', headers)
                    self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                    self.assertIn("manifest-src 'self'", headers['Content-Security-Policy'])
        manifest = json.loads(self.request('/app.webmanifest')[2])
        self.assertTrue(manifest['icons'])
        for icon in manifest['icons']:
            self.assertIn(icon['src'], resources)
            self.assertEqual(self.request(icon['src'])[0], 200)
        self.assertEqual(self.upstream.requests, [])

    def test_install_public_namespace_does_not_open_nearby_files_or_private_travel(self):
        cookie = self.login()
        unlisted = ['/icons/', '/icons/unlisted.png', '/icons/icon-192.png.extra',
                    '/icons/icon-192.png/extra', '/icons//icon-192.png',
                    '/app.webmanifest.extra', '/app.webmanifest/index.html',
                    '/offline.html.extra', '/assets/travel-places.json.bak']
        for path in unlisted:
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(path, method)[0], 401)
                    self.assertEqual(self.request(path, method, cookie=cookie)[0], 404)
        for path in ['/icons/../api/travel', '/icons/%2e%2e/api/travel',
                     '/icons/%252e%252e/api/travel', '/icons/a%5cb.png']:
            self.assertEqual(self.request(path)[0], 400, path)
        for path in ['/travel.js', '/travel-v23.css', '/assets/travel-places.json',
                     '/api/travel', '/install.js', '/mobile-v23.css']:
            for method in ('GET', 'HEAD'):
                self.assertEqual(self.request(path, method)[0], 401, (method, path))
        self.assertEqual(self.request('/api/travel', 'POST', {'scope': 'domestic', 'place_id': 'cn-test'})[0], 401)
        self.assertEqual(self.request('/app.webmanifest', Host='other.example.com')[0], 421)
        self.assertEqual(self.upstream.requests, [])

    def test_install_files_never_become_post_endpoints(self):
        cookie = self.login()
        paths = ['/app.webmanifest', '/offline.html', '/icons/icon.svg',
                 '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png',
                 '/icons/apple-touch-icon.png', '/icons/apple-touch-icon-167.png',
                 '/icons/apple-touch-icon-152.png']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, 'POST', {})[0], 401)
                self.assertEqual(self.request(path, 'POST', {}, cookie=cookie)[0], 404)
                self.assertEqual(self.request(path, 'POST', {}, Origin='https://evil.example.com')[0], 403)
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(self.upstream.post_requests, [])

    def test_authenticated_travel_assets_and_api_forward_only_permitted_requests(self):
        cookie = self.login()
        resources = {
            '/assets/travel-places.json': ('application/json', b'{"version":1,"places":[]}'),
            '/api/travel': ('application/json', b'{"visits":[]}'),
        }
        self.upstream.resources.update(resources)
        for path, (content_type, expected) in resources.items():
            for method in ('GET', 'HEAD'):
                status, headers, body = self.request(path, method, cookie=cookie,
                                                     **{'Sec-Fetch-Site': 'same-origin'})
                self.assertEqual(status, 200, (method, path))
                self.assertEqual(body, expected if method == 'GET' else b'')
                self.assertEqual(headers['Content-Type'], content_type)
                self.assertIn('private', headers['Cache-Control'])
                self.assertIn('no-store' if path.startswith('/api/') else 'must-revalidate', headers['Cache-Control'])
        payload = {'scope': 'domestic', 'place_id': 'cn-test', 'visited_on': '',
                   'city': '', 'note': '合成网关检查', 'revision': 0}
        self.assertEqual(self.request('/api/travel', 'POST', payload, cookie=cookie,
                                      **{'Sec-Fetch-Site': 'same-origin'})[0], 200)
        expected = json.dumps(payload, ensure_ascii=False).encode()
        self.assertEqual(self.upstream.post_requests, [('/api/travel', len(expected), hashlib.sha256(expected).hexdigest())])
        method, path, forwarded = self.upstream.requests[-1]
        self.assertEqual((method, path), ('POST', '/api/travel'))
        self.assertEqual(forwarded['Origin'], 'http://127.0.0.1:' + str(self.upstream.server_port))
        self.assertNotIn('Cookie', forwarded)
        self.upstream.requests.clear();self.upstream.post_requests.clear()
        for method in ('GET', 'HEAD'):
            self.assertEqual(self.request('/api/travel', method, cookie=cookie,
                                         **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for origin in ('null', '', 'https://evil.example.com', 'http://journal.example.com'):
            self.assertEqual(self.declared_post('/api/travel', 1024, cookie, origin)[0], 403)
        self.assertEqual(self.request('/api/travel', 'POST', payload, cookie=cookie,
                                     **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.declared_post('/api/travel', 200001, cookie)[0], 400)
        self.assertEqual(self.upstream.requests, [])
        self.assertEqual(self.upstream.post_requests, [])

    def test_login_and_authenticated_document_allow_only_same_origin_manifests(self):
        cookie = self.login()
        for path, credential in [('/login', None), ('/', cookie)]:
            status, headers, _ = self.request(path, cookie=credential)
            self.assertEqual(status, 200)
            directives = {parts[0]: parts[1:] for item in headers['Content-Security-Policy'].split(';')
                          if (parts := item.strip().split())}
            self.assertEqual(directives['manifest-src'], ["'self'"])
            self.assertEqual(directives['connect-src'], ["'self'"])
            self.assertEqual(directives['frame-ancestors'], ["'none'"])

    def test_all_private_routes_require_authentication(self):
        for path in ['/api/notes', '/api/work', '/api/todos', '/api/wellness',
                     '/api/notes/asset/export/image.jpg', '/api/report/report-id', '/app.js',
                     '/api/report/report-id/' + 'a' * 64 + '.png',
                     '/theme-v15.css', '/todos-v15.css', '/notes-v15.css', '/navigation.js',
                     '/todos-v20.css', '/wellness-v20.css', '/notes-v20.css',
                     '/theme.js', '/theme-base-v21.css', '/theme-v21.css', '/wellness-v21.css', '/assets/fonts/pipi-diary.ttf']:
            for method in ['GET', 'HEAD']:
                self.assertEqual(self.request(path, method)[0], 401, (method, path))
        status, headers, raw = self.request('/')
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/login')
        self.assertEqual(len(self.upstream.requests), 0)
        self.assertEqual(self.request('/login')[0], 200)

    def test_authenticated_multi_report_assets_preserve_bytes_and_private_headers(self):
        cookie = self.login()
        examples = [('png', 'image/png', b'\x89PNG\r\n\x1a\nsynthetic-image'),
                    ('jpg', 'image/jpeg', b'\xff\xd8\xffsynthetic-image'),
                    ('jpeg', 'image/jpeg', b'\xff\xd8\xffsynthetic-image-2'),
                    ('pdf', 'application/pdf', b'%PDF-1.4\nsynthetic-report\n%%EOF')]
        paths = []
        for extension, content_type, raw in examples:
            path = '/api/report/assessment-one/' + hashlib.sha256(raw).hexdigest() + '.' + extension
            self.upstream.resources[path] = (content_type, raw)
            paths.append(path)
            for method in ['GET', 'HEAD']:
                status, headers, received = self.request(path, method, cookie=cookie)
                self.assertEqual(status, 200)
                self.assertEqual(received, raw if method == 'GET' else b'')
                self.assertEqual(int(headers['Content-Length']), len(raw))
                self.assertEqual(headers['Content-Type'], content_type)
                self.assertIn('private', headers['Cache-Control'])
                self.assertIn('no-store', headers['Cache-Control'])
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
                self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertEqual([path for _, path, _ in self.upstream.requests],
                         [path for path in paths for _ in range(2)])
        self.assertEqual(self.request('/api/report/legacy-report', cookie=cookie)[0], 200)
        for path in ['/todos-v20.css', '/wellness-v20.css', '/notes-v20.css',
                     '/theme.js', '/theme-base-v21.css', '/theme-v21.css', '/wellness-v21.css', '/assets/fonts/pipi-diary.ttf']:
            self.assertEqual(self.request(path, cookie=cookie)[0], 404, path)

    def test_report_asset_whitelist_rejects_other_files_and_traversal_before_upstream(self):
        cookie = self.login()
        prefix = '/api/report/assessment-one/'
        for suffix in ['a' * 63 + '.png', 'A' * 64 + '.png', 'g' * 64 + '.pdf',
                       'a' * 64 + '.svg', 'a' * 64 + '.html', 'a' * 64 + '.jpg/extra',
                       'gateway.json', '', 'nested/' + 'a' * 64 + '.png']:
            self.assertEqual(self.request(prefix + suffix, cookie=cookie)[0], 404, suffix)
        for suffix in ['../gateway.json', '%2e%2e/gateway.json', '%252e%252e/gateway.json',
                       '..%5cgateway.json']:
            self.assertEqual(self.request(prefix + suffix, cookie=cookie)[0], 400, suffix)
        valid = prefix + 'a' * 64 + '.png'
        self.assertEqual(self.request(valid, cookie=cookie, **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request(valid, 'POST', {}, cookie=cookie)[0], 404)
        self.assertEqual(self.upstream.requests, [])

    def declared_post(self, path, length, cookie=None, origin=ORIGIN):
        """Header-only rejection probe: never sends an oversized body or real data."""
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.putrequest('POST', path, skip_host=True)
        connection.putheader('Host', 'journal.example.com')
        connection.putheader('Origin', origin)
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', str(length))
        if cookie:
            connection.putheader('Cookie', cookie)
        connection.endheaders()
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_upload_routes_accept_18mib_but_other_routes_keep_small_limit(self):
        cookie = self.login()
        limit = 18 * 1024 * 1024
        payload = {'padding': 'x' * (limit - len(json.dumps({'padding': ''}).encode()))}
        digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        for path in ['/api/wellness/report', '/api/notes']:
            self.assertEqual(self.request(path, 'POST', payload, cookie=cookie)[0], 200)
            self.assertEqual(self.upstream.post_requests[-1], (path, limit, digest))
            self.assertEqual(self.declared_post(path, limit + 1, cookie=cookie)[0], 400)
        for path in ['/api/work', '/api/todos']:
            self.assertEqual(self.declared_post(path, 200001, cookie=cookie)[0], 400)
        self.assertEqual(len(self.upstream.post_requests), 2)

    def test_upload_auth_and_csrf_rejections_do_not_read_or_forward_body(self):
        cookie = self.login()
        for path in ['/api/wellness/report', '/api/notes']:
            self.assertEqual(self.declared_post(path, 18 * 1024 * 1024)[0], 401)
            for origin in ['', 'null', 'https://evil.example.com']:
                self.assertEqual(self.declared_post(path, 18 * 1024 * 1024, cookie, origin)[0], 403)
        self.assertEqual(self.upstream.post_requests, [])
        self.assertEqual(self.upstream.requests, [])

    def test_native_account_form_redirect_and_error_recovery(self):
        status, headers, raw = self.request('/login')
        self.assertEqual(status, 200)
        # no-referrer makes native form navigations send Origin: null even
        # when the action is same-origin; login pages must retain the origin.
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        raw = raw.decode()
        for fragment in ['action="/auth/login"', 'method="post"', 'name="username"',
                         'value="admin"', 'autocomplete="username"', 'name="password"',
                         'autocomplete="current-password"', 'name="remember"', 'value="1" checked']:
            self.assertIn(fragment, raw)
        status, headers, raw = self.request('/auth/login', 'POST',
                                           {'username': gateway.USERNAME, 'password': 'wrong'}, form=True)
        self.assertEqual(status, 401)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn('用户名或密码不正确', raw.decode())
        self.assertNotIn(b'wrong', raw)
        self.assertNotIn(b'value="1" checked', raw)
        status, headers, raw = self.request('/auth/login', 'POST',
                                           {'username': gateway.USERNAME, 'password': PASSWORD, 'remember': '1'}, form=True)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertIn('Max-Age=' + str(gateway.REMEMBER_SECONDS), headers['Set-Cookie'])
        self.assertNotIn(PASSWORD.encode(), raw)
        cookie = headers['Set-Cookie'].split(';')[0]
        status, headers, _ = self.request('/login', cookie=cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        status, _, raw = self.request('/auth/session', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['username'], gateway.USERNAME)
        self.assertEqual(self.request('/api/notes', cookie=cookie)[1]['Referrer-Policy'], 'no-referrer')
        for path in ['/theme-v15.css', '/todos-v15.css', '/notes-v15.css', '/navigation.js']:
            self.assertEqual(self.request(path, cookie=cookie)[0], 404)

    def test_rate_limited_login_page_keeps_native_form_retry_working(self):
        with self.state.connect() as con:
            con.execute('INSERT INTO limits VALUES (?,?,?)', ('login-attempts', self.now, 5))
        payload = {'username': gateway.USERNAME, 'password': PASSWORD}
        status, headers, raw = self.request('/auth/login', 'POST', payload, form=True)
        self.assertEqual(status, 429)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertEqual(headers['Retry-After'], '900')
        self.assertNotIn(PASSWORD.encode(), raw)
        self.now += 901
        self.assertEqual(self.request('/auth/login', 'POST', payload, form=True)[0], 303)

    def test_native_form_still_rejects_opaque_missing_and_foreign_origins(self):
        payload = urlencode({'username': gateway.USERNAME, 'password': PASSWORD}).encode()
        for origin in ['null', '', 'https://evil.example.com', None]:
            with self.subTest(origin=origin):
                headers = {'Host': 'journal.example.com', 'Content-Type': 'application/x-www-form-urlencoded',
                           'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate'}
                if origin is not None:
                    headers['Origin'] = origin
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
                connection.request('POST', '/auth/login', payload, headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIsNone(response.getheader('Set-Cookie'))
                response.read()
                connection.close()
        with self.state.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sessions').fetchone()[0], 0)
            self.assertIsNone(con.execute("SELECT count FROM limits WHERE name='login-attempts'").fetchone())

    def test_native_form_preserves_password_with_unicode_and_reserved_characters(self):
        password = 'test-only-中文 +&=% passphrase'
        self.state.set_password(password)
        status, headers, raw = self.request('/auth/login', 'POST',
                                           {'username': gateway.USERNAME, 'password': password}, form=True)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertIn('Set-Cookie', headers)
        self.assertNotIn(password.encode(), raw)

    def test_unchecked_native_form_keeps_short_session_policy(self):
        status, headers, _ = self.request('/auth/login', 'POST',
                                          {'username': gateway.USERNAME, 'password': PASSWORD}, form=True)
        self.assertEqual(status, 303)
        self.assertIn('Max-Age=' + str(gateway.SESSION_SECONDS), headers['Set-Cookie'])
        cookie = headers['Set-Cookie'].split(';')[0]
        self.now += gateway.IDLE_SECONDS
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)

    def test_account_validation_remember_type_and_form_origin(self):
        for username in ['wrong-user', '用户名', None]:
            status, _, raw = self.request('/auth/login', 'POST',
                                          {'username': username, 'password': PASSWORD})
            self.assertEqual(status, 401)
            self.assertIn('用户名或密码不正确', raw.decode())
        self.assertEqual(self.request('/auth/login', 'POST',
                                     {'username': gateway.USERNAME, 'password': PASSWORD, 'remember': 'false'})[0], 400)
        self.assertEqual(self.request('/auth/login', 'POST',
                                     {'username': gateway.USERNAME, 'password': PASSWORD},
                                     form=True, Origin='https://evil.example.com')[0], 403)

    def test_remembered_session_survives_idle_restart_and_has_fixed_expiry(self):
        status, headers, _ = self.request('/auth/login', 'POST',
                                          {'username': gateway.USERNAME, 'password': PASSWORD, 'remember': True})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.now += 7 * 24 * 3600
        self.server.state = gateway.SecurityState(self.folder.name, clock=lambda: self.now)
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 200)
        # Creating a short session must not clean up a remembered session.
        self.login()
        self.now += 23 * 24 * 3600 - 1
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 200)
        self.now += 1
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)

    def test_remembered_session_logout_password_rotation_and_admin_revoke(self):
        for action in ['logout', 'password', 'all']:
            token = self.state.new_session(self.state.config(), remember=True)
            cookie = gateway.COOKIE + '=' + token
            self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 200)
            if action == 'logout':
                status, headers, _ = self.request('/auth/logout', 'POST', cookie=cookie)
                self.assertEqual(status, 200)
                self.assertIn('Max-Age=0', headers['Set-Cookie'])
            elif action == 'password':
                self.state.set_password('another-test-only-long-passphrase')
            else:
                self.state.revoke_all()
            self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)

    def test_schema_upgrade_preserves_old_session_and_password(self):
        config_before = self.state.config_path.read_bytes()
        token = 'a' * 43
        with self.state.connect() as con:
            con.execute('DROP TABLE sessions')
            con.execute('CREATE TABLE sessions (digest TEXT PRIMARY KEY, generation TEXT NOT NULL, '
                        'created REAL NOT NULL, seen REAL NOT NULL)')
            con.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                        (self.state.digest(token), self.state.config()['generation'], self.now, self.now))
        migrated = gateway.SecurityState(self.folder.name, clock=lambda: self.now)
        self.assertEqual(config_before, migrated.config_path.read_bytes())
        self.assertTrue(migrated.verify_password(PASSWORD, migrated.config()))
        self.assertTrue(migrated.authenticated(token, migrated.config()))
        self.now += gateway.IDLE_SECONDS
        self.assertFalse(migrated.authenticated(token, migrated.config()))

    def test_password_never_stored_plaintext_and_cookie_policy(self):
        self.assertNotIn(PASSWORD, self.state.config_path.read_text())
        status, headers, _ = self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie']
        for flag in ['__Host-tiangong_session=', 'Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/']:
            self.assertIn(flag, cookie)
        token = cookie.split(';')[0].split('=', 1)[1]
        with self.state.connect() as con:
            self.assertNotEqual(con.execute('SELECT digest FROM sessions').fetchone()[0], token)

    def test_login_limit_persists_and_cannot_be_evaded_with_forwarded_ip(self):
        for i in range(5):
            status = self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': 'wrong'}, **{'X-Forwarded-For': f'10.0.0.{i}'})[0]
            self.assertEqual(status, 401)
        self.server.state = gateway.SecurityState(self.folder.name, clock=lambda: self.now)
        status, headers, _ = self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD}, **{'CF-Connecting-IP': '1.2.3.4'})
        self.assertEqual(status, 429)
        self.assertEqual(headers['Retry-After'], '900')
        self.now += 901
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD})[0], 200)

    def test_session_idle_absolute_expiry_logout_and_password_rotation(self):
        cookie = self.login()
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 200)
        self.now += gateway.IDLE_SECONDS
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)
        cookie = self.login()
        self.now += gateway.SESSION_SECONDS
        with self.state.connect() as con:
            con.execute('UPDATE sessions SET seen=?', (self.now,))
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)
        cookie = self.login()
        status, headers, _ = self.request('/auth/logout', 'POST', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)
        cookie = self.login()
        self.state.set_password('a-different-long-test-passphrase')
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 401)

    def test_origin_host_and_path_boundaries(self):
        cookie = self.login()
        for origin in ['http://journal.example.com', 'https://evil.example.com', 'null', '']:
            self.assertEqual(self.request('/api/work', 'POST', cookie=cookie, Origin=origin)[0], 403)
            self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD}, Origin=origin)[0], 403)
        self.assertEqual(self.request('/api/work', cookie=cookie, Host='evil.example.com')[0], 421)
        self.assertEqual(self.request('/api/work', 'POST', cookie=cookie, **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for path in ['/../scripts/public_gateway.py', '/api/notes/asset/%2e%2e/image.jpg',
                     '/api/notes/asset/%252e%252e/image.jpg', '//evil.example.com/', '/api/notes/asset/a%5cb.jpg']:
            self.assertEqual(self.request(path, cookie=cookie)[0], 400, path)
        self.assertEqual(self.request('/gateway.json', cookie=cookie)[0], 404)
        self.assertEqual(len(self.upstream.requests), 0)

    def test_proxy_normalizes_headers_and_has_security_headers(self):
        cookie = self.login()
        status, headers, _ = self.request('/api/work', 'POST', {'content': 'test'}, cookie=cookie,
                                          **{'X-Forwarded-For': '1.2.3.4', 'Authorization': 'Bearer secret'})
        self.assertEqual(status, 200)
        method, path, forwarded = self.upstream.requests[-1]
        self.assertEqual((method, path), ('POST', '/api/work'))
        self.assertEqual(forwarded['Host'], '127.0.0.1:' + str(self.upstream.server_port))
        self.assertEqual(forwarded['Origin'], 'http://' + forwarded['Host'])
        for name in ['Cookie', 'Authorization', 'X-Forwarded-For']:
            self.assertNotIn(name, forwarded)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        raw = self.request('/', cookie=cookie)[2]
        self.assertIn(b'/auth/login.js', raw)
        self.assertIn(b'/auth/login.css', raw)
        self.assertIn(b'<script src="/auth/login.js"></script></head>', raw)

    def test_total_limit_and_missing_password_fail_closed(self):
        with self.state.connect() as con:
            con.execute('INSERT INTO limits VALUES (?,?,?)', ('all-requests', self.now, 600))
        self.assertEqual(self.request('/login')[0], 429)
        self.now += 61
        self.assertEqual(self.request('/login')[0], 200)
        data = self.state.config()
        del data['password']
        self.state.write_config(data)
        for path in ['/', '/login', '/api/notes', '/api/notes/asset/export/image.jpg']:
            self.assertEqual(self.request(path)[0], 503)
        self.assertEqual(len(self.upstream.requests), 0)

    def test_empty_chunked_tunnel_reads_and_ambiguous_framing(self):
        cookie = self.login()
        def raw(method, chunks, extra=()):
            con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
            con.putrequest(method, '/', skip_host=True)
            for key, value in [('Host', 'journal.example.com'), ('Cookie', cookie),
                               ('Transfer-Encoding', 'chunked'), *extra]:
                con.putheader(key, value)
            con.endheaders(chunks)
            response = con.getresponse(); status = response.status
            response.read(); con.close()
            return status
        self.assertEqual(raw('GET', b'0\r\n\r\n'), 200)
        self.assertEqual(raw('HEAD', b'0\r\n\r\n'), 200)
        count = len(self.upstream.requests)
        for body, headers in [(b'1\r\nx\r\n0\r\n\r\n', ()),
                              (b'0;ext\r\n\r\n', ()),
                              (b'0\r\nTrailer: x\r\n\r\n', ()),
                              (b'0\r\n\r\n', (('Content-Length','0'),)),
                              (b'0\r\n\r\n', (('Transfer-Encoding','chunked'),))]:
            self.assertEqual(raw('GET', body, headers), 400)
        self.assertEqual(len(self.upstream.requests), count)

    def test_bad_json_and_duplicate_framing_rejected(self):
        cookie = self.login()
        self.assertEqual(self.request('/api/work', 'POST', [], cookie=cookie)[0], 400)
        self.assertEqual(self.request('/api/work', 'POST', {}, cookie=cookie, **{'Transfer-Encoding': 'chunked'})[0], 400)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.putrequest('POST', '/auth/login', skip_host=True)
        for key, value in [('Host', 'journal.example.com'), ('Origin', ORIGIN), ('Content-Type', 'application/json'),
                           ('Content-Length', '2'), ('Content-Length', '2')]:
            connection.putheader(key, value)
        connection.endheaders(b'{}')
        self.assertEqual(connection.getresponse().status, 400)
        connection.close()

    def test_configuration_requires_https_domain_and_loopback(self):
        for value in ['http://journal.example.com', 'https://127.0.0.1', 'https://example.com/extra', 'https://a:b@example.com']:
            with self.assertRaises(ValueError):
                gateway.canonical_origin(value)
        with self.assertRaises(ValueError):
            gateway.GatewayServer(('0.0.0.0', 0), self.state)

    def test_upstream_unavailable_fails_without_disclosing_data(self):
        cookie = self.login()
        self.server.upstream_port = 0
        self.assertEqual(self.request('/api/notes', cookie=cookie)[0], 502)

    def test_cloudflare_clients_do_not_lock_each_other_or_trust_xff(self):
        self.state.configure(ORIGIN, 'cloudflare-tunnel')
        first = {'CF-Connecting-IP': '198.51.100.1'}
        second = {'CF-Connecting-IP': '198.51.100.2'}
        for i in range(5):
            self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': 'wrong'},
                                          **first, **{'X-Forwarded-For': f'203.0.113.{i}'})[0], 401)
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD},
                                      **first, **{'X-Forwarded-For': '203.0.113.99'})[0], 429)
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD}, **second)[0], 200)
        # The same canonical address must share a bucket, including mapped IPv6.
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD},
                                      **{'CF-Connecting-IP': '::ffff:198.51.100.1'})[0], 429)
        with self.state.connect() as con:
            rows = [dict(row) for row in con.execute('SELECT * FROM limits')]
            self.assertEqual(con.execute("SELECT count FROM limits WHERE name='cf-login-total'").fetchone()[0], 6)
        self.assertNotIn('198.51.100.', json.dumps(rows))
        self.assertTrue(all(len(row['name']) == 73 for row in rows if row['name'].startswith('cf-login:')))

    def test_cloudflare_requires_exactly_one_valid_connecting_ip(self):
        self.state.configure(ORIGIN, 'cloudflare-tunnel')
        self.assertEqual(self.request('/login')[0], 403)
        for invalid in ['', 'evil', '198.51.100.1, 198.51.100.2', '198.51.100.1:443', 'fe80::1%en0']:
            self.assertEqual(self.request('/login', **{'CF-Connecting-IP': invalid})[0], 403)
        self.assertEqual(self.request('/login', **{'CF-Connecting-IP': '2001:db8::1'})[0], 200)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.putrequest('GET', '/login', skip_host=True)
        connection.putheader('Host', 'journal.example.com')
        connection.putheader('CF-Connecting-IP', '198.51.100.1')
        connection.putheader('CF-Connecting-IP', '198.51.100.2')
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 403)
        connection.close()

    def test_cloudflare_success_refunds_client_budget_and_global_cap_remains(self):
        self.state.configure(ORIGIN, 'cloudflare-tunnel')
        headers = {'CF-Connecting-IP': '198.51.100.10'}
        for _ in range(6):
            self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD}, **headers)[0], 200)
        with self.state.connect() as con:
            self.assertEqual(con.execute("SELECT count FROM limits WHERE name LIKE 'cf-login:%'").fetchone()[0], 0)
            con.execute("UPDATE limits SET count=60 WHERE name='cf-login-total'")
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD},
                                      **{'CF-Connecting-IP': '198.51.100.11'})[0], 429)
        self.now += 901
        self.assertEqual(self.request('/auth/login', 'POST', {'username': gateway.USERNAME, 'password': PASSWORD}, **headers)[0], 200)

    def test_limit_cleanup_and_concurrent_reservations_are_bounded(self):
        with self.state.connect() as con:
            con.execute('INSERT INTO limits VALUES (?,?,?)', ('cf-login:expired', self.now - 90000, 4))
            con.execute('INSERT INTO limits VALUES (?,?,?)', ('cf-login:recent', self.now - 100, 4))
        results = []
        workers = [threading.Thread(target=lambda: results.append(self.state.reserve_client_login('concurrent'))) for _ in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sum(retry == 0 for retry, _ in results), 5)
        with self.state.connect() as con:
            self.assertIsNone(con.execute("SELECT * FROM limits WHERE name='cf-login:expired'").fetchone())
            self.assertIsNotNone(con.execute("SELECT * FROM limits WHERE name='cf-login:recent'").fetchone())
            self.assertEqual(con.execute("SELECT count FROM limits WHERE name='cf-login-total'").fetchone()[0], 5)
        # Counters survive a new SecurityState instance / service restart.
        restarted = gateway.SecurityState(self.folder.name, clock=lambda: self.now)
        self.assertGreater(restarted.reserve_client_login('concurrent')[0], 0)


class FirstPasswordTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = gateway.SecurityState(self.folder.name)
        self.state.configure(ORIGIN)
        self.server = setup.SetupServer(self.state, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, path=None, method='GET', form=None, include_csrf=True, **headers):
        values = {'Host': self.server.origin[len('http://'):]}
        body = None
        if method == 'POST':
            values.update({'Origin': self.server.origin, 'Content-Type': 'application/x-www-form-urlencoded'})
            fields = dict(form if form is not None else {'password': PASSWORD, 'confirm': PASSWORD})
            if include_csrf:
                fields.setdefault('csrf_token', self.server.csrf_token)
            body = urlencode(fields)
        values.update(headers)
        values = {key: value for key, value in values.items() if value is not None}
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        con.request(method, path or self.server.route, body, values)
        response = con.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        con.close()
        return result

    def test_only_one_time_local_url_is_accepted(self):
        status, headers, raw = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("form-action 'self'", headers['Content-Security-Policy'])
        self.assertIn(b'name="csrf_token"', raw)
        self.assertIn(self.server.csrf_token.encode(), raw)
        self.assertNotIn(self.server.csrf_token, self.server.route)
        self.assertEqual(self.request('/setup/wrong-token')[0], 404)
        self.assertEqual(self.request(Host='journal.example.com')[0], 404)
        self.assertEqual(self.request(**{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.server.expires = 0
        self.assertEqual(self.request()[0], 404)

    def test_foreign_origin_cannot_set_password(self):
        for origin in ['https://evil.example.com', '']:
            self.assertEqual(self.request(method='POST', Origin=origin)[0], 403)
        self.assertEqual(self.request(method='POST', **{'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertFalse(self.state.config().get('password'))
        self.assertEqual(self.request(method='POST', form={'password': PASSWORD, 'confirm': 'different'})[0], 400)
        self.assertEqual(self.request(method='POST', form={'password': 'short', 'confirm': 'short'})[0], 400)
        self.assertFalse(self.state.config().get('password'))

    def test_set_password_closes_and_cannot_be_used_for_reset(self):
        status, headers, raw = self.request(method='POST')
        self.assertEqual(status, 200)
        self.assertNotIn(PASSWORD.encode(), raw)
        self.assertTrue(self.state.ready(self.state.config()))
        self.assertTrue(self.server.used)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
        self.assertNotIn(PASSWORD, self.state.config_path.read_text())
        with self.assertRaises(ValueError):
            setup.SetupServer(self.state, 0)

    def test_missing_or_wrong_csrf_is_rejected_for_every_origin(self):
        for origin in [self.server.origin, None, 'null']:
            self.assertEqual(self.request(method='POST', Origin=origin, include_csrf=False)[0], 403)
            self.assertEqual(self.request(method='POST', Origin=origin,
                                          form={'password': PASSWORD, 'confirm': PASSWORD, 'csrf_token': 'wrong'})[0], 403)
        self.assertFalse(self.state.config().get('password'))

    def test_embedded_browser_missing_origin_with_csrf_can_set_password(self):
        self.assertEqual(self.request(method='POST', Origin=None)[0], 200)
        self.assertTrue(self.state.ready(self.state.config()))

    def test_embedded_browser_null_origin_with_csrf_can_set_password(self):
        self.assertEqual(self.request(method='POST', Origin='null', **{'Sec-Fetch-Site': 'none'})[0], 200)
        self.assertTrue(self.state.ready(self.state.config()))

    def test_duplicate_origin_is_rejected_even_with_valid_csrf(self):
        body = urlencode({'password': PASSWORD, 'confirm': PASSWORD, 'csrf_token': self.server.csrf_token}).encode()
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        con.putrequest('POST', self.server.route, skip_host=True)
        con.putheader('Host', self.server.origin[len('http://'):])
        con.putheader('Origin', self.server.origin)
        con.putheader('Origin', 'null')
        con.putheader('Content-Type', 'application/x-www-form-urlencoded')
        con.putheader('Content-Length', str(len(body)))
        con.endheaders(body)
        self.assertEqual(con.getresponse().status, 403)
        con.close()
        self.assertFalse(self.state.config().get('password'))


if __name__ == '__main__':
    unittest.main()
