"""Exercise the actual HTTP handler with synthetic places and temporary stores."""
import http.client
from http.server import ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import travel
import work_server


class TravelHTTPTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tiangong-travel-http-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        catalog = self.base / 'places.json'
        catalog.write_text(json.dumps({'version': 1, 'places': [
            {'id': 'cn-test', 'scope': 'domestic', 'name': '测试省'},
            {'id': 'world-test', 'scope': 'overseas', 'name': '测试国'}]}))
        # Protect every potentially reachable write store, not just this route.
        for module, field, value in [
                (travel, 'DB', self.base/'travel.sqlite3'),
                (travel, 'CATALOG', catalog),
                (work_server, 'DB', self.base/'work.sqlite3'),
                (work_server.todos, 'DB', self.base/'todos.sqlite3'),
                (work_server.notes, 'BASE', self.base/'notes'),
                (work_server.wellness, 'BASE', self.base/'wellness')]:
            p = patch.object(module, field, value)
            p.start();self.addCleanup(p.stop)
        backup = patch.object(work_server.nas_backup, 'request_backup', return_value=None)
        self.backup = backup.start();self.addCleanup(backup.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), work_server.Handler)
        self.server.allowed_hosts = {'127.0.0.1:' + str(self.server.server_port)}
        self.server.allowed_networks = [ipaddress.ip_network('127.0.0.0/8')]
        self.server.lan = False
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.worker.join(timeout=3)

    def request(self, method='GET', payload=None, headers=None, raw=None):
        request_headers = {'Origin': self.origin, 'Content-Type': 'application/json'}
        request_headers.update(headers or {})
        request_headers = {key: value for key, value in request_headers.items() if value is not None}
        body = raw if raw is not None else json.dumps(payload).encode('utf-8') if payload is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, '/api/travel', body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def payload(self, **updates):
        return {'scope': 'domestic', 'place_id': 'cn-test', 'visited_on': '2025-06-15',
                'city': '测试城市', 'note': '合成旅行小记', **updates}

    def saved(self, payload):
        code, headers, body = self.request('POST', payload)
        self.assertEqual(code, 200, body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        return body['entry']

    def history(self):
        with travel.connection() as con:
            return [json.loads(row['data']) for row in con.execute('SELECT data FROM history ORDER BY revision')]

    def test_get_and_post_share_the_same_temporary_database(self):
        code, headers, empty = self.request()
        self.assertEqual(code, 200);self.assertEqual(empty['visits'], [])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        saved = self.saved(self.payload())
        code, _, state = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(state['visits'], [saved])
        self.assertEqual(state['counts'], {'domestic': 1, 'overseas': 0})
        self.assertEqual(travel.DB.parent, self.base)
        self.assertTrue(travel.DB.is_file())

    def test_retried_create_and_update_do_not_duplicate_history(self):
        original = self.saved(self.payload(revision=0))
        self.assertEqual(self.saved(self.payload(revision=0)), original)
        changed = self.saved({**original, 'note': '更新后的合成小记'})
        self.assertEqual(self.saved({**original, 'note': '更新后的合成小记'}), changed)
        self.assertEqual(self.request()[2]['visits'], [changed])
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(self.history(), [original])

    def test_partial_dates_round_trip_and_edits_preserve_exact_precision(self):
        original = self.saved(self.payload(visited_on='2024'))
        self.assertEqual(self.request()[2]['visits'][0]['visited_on'], '2024')
        current = original
        for value in ('2024-02', '2024-02-29', ''):
            current = self.saved({**current, 'visited_on': value})
            self.assertEqual(self.request()[2]['visits'][0]['visited_on'], value)
        self.assertEqual(len(self.request()[2]['visits']), 1)
        self.assertEqual([row['visited_on'] for row in self.history()], ['2024', '2024-02', '2024-02-29'])

    def test_missing_null_or_cross_origin_is_rejected_without_writes_or_backup(self):
        for origin in (None, 'null', 'https://untrusted.example', self.origin + '/extra'):
            with self.subTest(origin=origin):
                code, _, _ = self.request('POST', self.payload(), {'Origin': origin})
                self.assertEqual(code, 403)
        self.assertFalse(travel.DB.exists())
        self.backup.assert_not_called()

    def test_wrong_host_is_rejected_for_both_reads_and_writes(self):
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                code, _, _ = self.request(method, self.payload() if method == 'POST' else None,
                                           {'Host': 'untrusted.example', 'Origin': 'http://untrusted.example'})
                self.assertEqual(code, 403)
        self.assertFalse(travel.DB.exists())
        self.backup.assert_not_called()

    def test_stale_revision_returns_409_and_preserves_the_latest_visit(self):
        original = self.saved(self.payload())
        latest = self.saved({**original, 'city': '已更新城市'})
        for changes in ({'city': '旧页面覆盖'}, {'action': 'delete'}):
            with self.subTest(changes=changes):
                code, _, body = self.request('POST', {**original, **changes})
                self.assertEqual(code, 409);self.assertIn('刷新', body['error'])
        self.assertEqual(self.request()[2]['visits'], [latest])
        self.assertEqual(self.history(), [original])

    def test_soft_delete_and_explicit_restore_keep_details_and_counts(self):
        original = self.saved(self.payload())
        deleted = self.saved({**original, 'action': 'delete'})
        state = self.request()[2]
        self.assertEqual(state['counts']['domestic'], 0)
        self.assertEqual(state['visits'], [deleted])
        self.assertEqual(deleted['note'], original['note'])
        self.assertEqual(self.request('POST', original)[0], 409)
        restored = self.saved({**deleted, 'action': 'restore'})
        self.assertEqual(restored['deleted'], 0)
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(self.request()[2]['counts']['domestic'], 1)
        self.assertEqual(self.history(), [original, deleted])

    def test_invalid_payloads_return_400_and_never_create_visits(self):
        for payload in [[], self.payload(place_id='not-in-catalog'), self.payload(note='x'*2001),
                        *[self.payload(visited_on=value) for value in
                          ('2025-02-30', '9999-01-01', '9999', '9999-01', '2025-13', '0000', '2025-2')]]:
            with self.subTest(payload=payload):
                self.assertEqual(self.request('POST', payload)[0], 400)
        self.assertEqual(self.request('POST', raw=b'{bad json')[0], 400)
        # An oversized declared length is rejected before the body is read.
        # Sending the body as well races the server's deliberate early close.
        self.assertEqual(self.request('POST', raw=b'', headers={'Content-Length': '200001'})[0], 400)
        self.assertEqual(self.request()[2]['visits'], [])
        self.backup.assert_not_called()


if __name__ == '__main__':unittest.main()
