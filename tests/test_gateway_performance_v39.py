"""Compression/caching must never make account data reusable or bypass login."""
import gzip
import hashlib
import re
import unittest

import test_public_gateway as fixtures


class GatewayPerformanceTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.GatewayTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.GatewayTests.tearDownClass.__func__)
    setUp = fixtures.GatewayTests.setUp
    tearDown = fixtures.GatewayTests.tearDown
    request = fixtures.GatewayTests.request
    login = fixtures.GatewayTests.login

    def test_actual_bundles_and_account_bootstrap_preserve_doctype_and_paths(self):
        web = fixtures.gateway.ROOT / 'src/web'
        raw_html = (web / 'index.html').read_bytes()
        self.upstream.resources['/'] = ('text/html; charset=utf-8', raw_html)
        cookie = self.login()
        status, headers, html = self.request('/', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(html.lower().startswith(b'<!doctype html>'))
        self.assertIn(b'<head><meta name="tiangong-account"', html)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn('ETag', headers)
        names = re.findall(rb'href="(styles-(?:shell|middle|app)\.[a-f0-9]{16}\.css)"', html)
        self.assertEqual(len(names), 3)
        for name in names:
            source = (web / name.decode()).read_bytes()
            self.assertIn(hashlib.sha256(source).hexdigest()[:16].encode(), name)
            self.assertNotIn(b'@import', source)
            urls = re.findall(rb'url\(\s*[\'\"]?([^\s\'\"\)]+)', source)
            self.assertTrue(all(url.startswith((b'/', b'data:')) for url in urls))
            self.upstream.resources['/' + name.decode()] = ('text/css', source)
            _, asset_headers, received = self.request('/' + name.decode(), cookie=cookie)
            self.assertEqual(received, source)
            self.assertIn('immutable', asset_headers['Cache-Control'])

    def test_static_revalidates_with_no_body_and_still_requires_live_session(self):
        raw = b'.fh-card { border-radius: 20px; }' * 100
        self.upstream.resources['/assets/travel-places.json'] = ('application/json', raw)
        cookie = self.login()
        status, headers, body = self.request('/assets/travel-places.json?v=39', cookie=cookie)
        self.assertEqual((status, body), (200, raw))
        self.assertEqual(headers['Cache-Control'], 'private, max-age=0, must-revalidate')
        conditions = {'If-None-Match': headers['ETag']}
        self.assertEqual(self.request('/assets/travel-places.json?v=39', cookie=cookie, **conditions)[::2], (304, b''))
        self.assertEqual(self.request('/assets/travel-places.json?v=39', **conditions)[0], 401)
        self.state.revoke(cookie.split('=', 1)[1])
        self.assertEqual(self.request('/assets/travel-places.json?v=39', cookie=cookie, **conditions)[0], 401)

    def test_static_changed_content_invalidates_old_etag(self):
        path = '/assets/travel-places.json'
        self.upstream.resources[path] = ('application/json', b'{"color":"blue"}')
        cookie = self.login()
        etag = self.request(path, cookie=cookie)[1]['ETag']
        self.upstream.resources[path] = ('application/json', b'{"color":"red"}')
        status, headers, raw = self.request(path, cookie=cookie, **{'If-None-Match': etag})
        self.assertEqual((status, raw), (200, b'{"color":"red"}'))
        self.assertNotEqual(etag, headers['ETag'])

    def test_only_valid_content_addressed_bundle_can_cache_immutable(self):
        raw = b'.card {color:blue}'
        digest = hashlib.sha256(raw).hexdigest()[:16]
        path = '/styles-app.' + digest + '.css'
        bad = '/styles-app.' + '0' * 16 + '.css'
        self.upstream.resources[path] = self.upstream.resources[bad] = ('text/css', raw)
        self.server.release_assets |= {path}
        cookie = self.login()
        self.assertEqual(self.request(path)[0], 401)
        self.assertIn('immutable', self.request(path, cookie=cookie)[1]['Cache-Control'])
        self.assertEqual(self.request(bad, cookie=cookie)[0], 404)
        self.assertEqual(self.request('/styles-private.' + digest + '.css', cookie=cookie)[0], 404)
        self.assertEqual(self.request('/styles-app.secret.css', cookie=cookie)[0], 404)

    def test_script_bundle_hash_cache_requires_auth_and_exact_namespace(self):
        raw = b'window.syntheticReleaseReady = true;'
        digest = hashlib.sha256(raw).hexdigest()[:16]
        path = '/scripts-core.' + digest + '.js'
        self.upstream.resources[path] = ('application/javascript', raw)
        self.server.release_assets |= {path}
        cookie = self.login()
        self.assertEqual(self.request(path)[0], 401)
        status, headers, body = self.request(path, cookie=cookie)
        self.assertEqual((status, body), (200, raw))
        self.assertIn('immutable', headers['Cache-Control'])
        self.assertEqual(self.request(path, cookie=cookie, **{'If-None-Match':headers['ETag']})[0], 304)
        self.assertEqual(self.request('/scripts-private.' + digest + '.js', cookie=cookie)[0], 404)
        bad = '/scripts-modules.' + '0' * 16 + '.js'
        self.upstream.resources[bad] = ('application/javascript', raw)
        self.assertEqual(self.request(bad, cookie=cookie)[0], 404)

    def test_json_compression_preserves_bytes_but_never_cache_or_etag(self):
        raw = ('{"records":[' + ','.join(['{"amount":123,"note":"合成账目"}'] * 300) + ']}').encode()
        path = '/api/finance'
        self.upstream.resources[path] = ('application/json; charset=utf-8', raw)
        status, headers, body = self.request(path, cookie=self.login(), **{'Accept-Encoding': 'br, gzip'})
        self.assertEqual(status, 200)
        self.assertEqual(gzip.decompress(body), raw)
        self.assertLess(len(body), len(raw) // 5)
        self.assertEqual(headers['Content-Encoding'], 'gzip')
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn('ETag', headers)
        self.assertEqual(headers['Vary'], 'Accept-Encoding')

    def test_compression_negotiation_and_head_are_consistent(self):
        raw = b'.card {border:1px solid blue}' * 500
        path = '/styles-app.' + hashlib.sha256(raw).hexdigest()[:16] + '.css'
        self.server.release_assets |= {path}
        self.upstream.resources[path] = ('text/css', raw)
        cookie = self.login()
        for value in ['identity', 'gzip;q=0, *;q=1', 'gzip;q=bogus']:
            status, headers, body = self.request(path, cookie=cookie, **{'Accept-Encoding': value})
            self.assertEqual((status, body), (200, raw))
            self.assertNotIn('Content-Encoding', headers)
        status, headers, compressed = self.request(path, cookie=cookie, **{'Accept-Encoding': 'gzip'})
        head = self.request(path, 'HEAD', cookie=cookie, **{'Accept-Encoding': 'gzip'})
        self.assertEqual(head[0], 200)
        self.assertEqual(head[1]['Content-Length'], str(len(compressed)))
        self.assertEqual(head[2], b'')
        self.assertEqual(gzip.decompress(compressed), raw)

    def test_omitted_fonts_are_unpublished_and_personal_attachments_never_cached(self):
        font = '/assets/fonts/pipi-diary-v39.woff2'
        self.upstream.resources[font] = ('font/woff2', b'font-test-data')
        cookie = self.login()
        self.assertEqual(self.request(font)[0], 401)
        self.assertEqual(self.request(font, cookie=cookie)[0], 404)
        private = '/receipt-assets/' + 'a' * 64 + '.webp'
        self.upstream.resources[private] = ('image/webp', b'private-user-receipt')
        headers = self.request(private, cookie=cookie)[1]
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn('ETag', headers)

    def test_html_remains_personal_and_no_store_after_compression(self):
        self.upstream.resources['/'] = ('text/html', b'<html><head></head><body>' + b'personal page ' * 300 + b'</body></html>')
        status, headers, body = self.request('/', cookie=self.login(), **{'Accept-Encoding': 'gzip'})
        self.assertEqual(status, 200)
        self.assertIn(b'tiangong-account', gzip.decompress(body))
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn('ETag', headers)

    def test_saturated_server_returns_retry_instead_of_dropping_stylesheet_socket(self):
        for _ in range(24):
            self.assertTrue(self.server.slots.acquire(blocking=False))
        try:
            status, headers, body = self.request('/style.css')
            self.assertEqual((status, body), (503, b''))
            self.assertEqual(headers['Retry-After'], '1')
            self.assertEqual(headers['Cache-Control'], 'no-store')
        finally:
            for _ in range(24):
                self.server.slots.release()


if __name__ == '__main__':
    unittest.main()
