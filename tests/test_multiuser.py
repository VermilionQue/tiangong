"""Real gateway/backend tenant boundaries; all state and evidence are synthetic.

Run with a Python that has Pillow installed. No live database, Apple Health
directory, backup, reminder bridge or remote provider is used by this suite.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import io
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
from typing import NamedTuple
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import public_gateway as gateway
import tenant_storage
import work_server as web

ORIGIN = 'https://multiuser.example.com'
# Synthetic test credentials; never valid for a deployed account.
PASSWORD = 'test-only-owner-password-2026!'
MEMBER_PASSWORD = 'test-only-member-password-2026!'


class Session(NamedTuple):
    cookie: str
    identity: str


class Reply(NamedTuple):
    status: int
    headers: dict
    data: object
    raw: bytes


def picture(color):
    stream = io.BytesIO()
    Image.new('RGB', (12, 9), color).save(stream, 'PNG')
    return stream.getvalue()


class MultiuserHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tiangong-multiuser-test-')
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.base = self.folder / 'private'
        self.base.mkdir()
        self.apple_source = self.folder / 'synthetic-apple-health'
        self.apple_source.mkdir()
        catalogue = self.folder / 'catalogue.json'
        catalogue.write_text(json.dumps({'version': 1, 'places': [
            {'id': 'test-province', 'name': '合成省份', 'scope': 'domestic'}]}))
        # Keep DeferredPath objects intact so real server threads resolve the
        # current identity, rather than replacing each store with a fixed path.
        replacements = [(tenant_storage, 'BASE', self.base),
                        (web.wellness, 'SOURCE', self.apple_source),
                        (web.travel, 'CATALOG', catalogue),
                        (web, 'FONT_DIR', self.folder / 'fonts'),
                        (web.weather, 'CACHE', {})]
        for module, name, value in replacements:
            item = patch.object(module, name, value)
            item.start(); self.addCleanup(item.stop)
        for module, name in [(web.nas_backup, 'request_backup'),
                             (web.reminders_sync, 'wake')]:
            item = patch.object(module, name, return_value=None)
            item.start(); self.addCleanup(item.stop)
        item = patch.object(web.reminders_sync, 'status', return_value=web.reminders_sync._default_status())
        item.start(); self.addCleanup(item.stop)
        for module, name in [(web.games, '_fetch_owned'), (web.weather, 'fetch')]:
            item = patch.object(module, name, side_effect=AssertionError('Live providers are forbidden in this suite'))
            item.start(); self.addCleanup(item.stop)
        self.now = 1_700_000_000.0
        self.security = gateway.SecurityState(self.folder / 'gateway-security', clock=lambda: self.now)
        self.security.configure(ORIGIN)
        self.security.set_password(PASSWORD)
        self.local = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.local_host = '127.0.0.1:' + str(self.local.server_port)
        self.local.allowed_hosts = {self.local_host}
        self.local.allowed_networks = [ipaddress.ip_network('127.0.0.0/8')]
        self.local.lan = False
        self.local.ingress_secret = self.security.upstream_secret
        self.local.public_origin = ORIGIN
        self.public = gateway.GatewayServer(('127.0.0.1', 0), self.security, self.local.server_port,
                                            public_root=self.folder / 'public')
        for server in (self.local, self.public):
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(self.stop, server, worker)
        token = self.security.new_session(self.security.config())
        self.owner = Session(gateway.COOKIE + '=' + token, 'owner')

    @staticmethod
    def stop(server, worker):
        server.shutdown(); server.server_close(); worker.join(timeout=3)

    def request(self, path, method='GET', data=None, session=None, *, direct=False, headers=None):
        server = self.local if direct else self.public
        values = {'Host': self.local_host if direct else 'multiuser.example.com'}
        if method == 'POST':
            values.update({'Origin': 'http://' + self.local_host if direct else ORIGIN,
                           'Content-Type': 'application/json'})
        if session:
            values.update({'Cookie': session.cookie, 'X-Tiangong-Client-Account': session.identity})
        values.update(headers or {})
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
        try:
            connection.request(method, path, body=raw, headers=values)
            response = connection.getresponse()
            raw = response.read()
            response_headers = dict(response.getheaders())
            parsed = json.loads(raw) if raw and response_headers.get('Content-Type', '').startswith('application/json') else None
            return Reply(response.status, response_headers, parsed, raw)
        finally:
            connection.close()

    def ok(self, response, status=200):
        self.assertEqual(response.status, status, response.raw[:500])
        return response.data

    def invite(self, username='member-one'):
        return self.ok(self.request('/auth/users', 'POST', {
            'action': 'create', 'username': username, 'display_name': username}, self.owner))

    def member(self, username='member-one'):
        invitation = self.invite(username)
        response = self.request('/auth/activate', 'POST', {
            'token': invitation['invitation'], 'password': MEMBER_PASSWORD})
        self.ok(response)
        return Session(response.headers['Set-Cookie'].split(';', 1)[0], invitation['user']['id'])

    def login(self, username, password):
        response = self.request('/auth/login', 'POST', {'username': username, 'password': password})
        self.ok(response)
        cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        response = self.request('/auth/session', headers={'Cookie': cookie})
        account = self.ok(response)['user']
        return Session(cookie, account['id'])

    def work(self, session, key='shared-id', content='synthetic record', revision=0):
        return self.request('/api/work', 'POST', {
            'id': key, 'revision': revision, 'business': '客户业务', 'time': '2020-01-02T11:00',
            'content': content, 'status': '进行中'}, session)

    def upload(self, session, kind='travel', color='steelblue'):
        value = 'data:image/png;base64,' + base64.b64encode(picture(color)).decode()
        return self.ok(self.request('/api/' + kind + '/upload', 'POST', {'data_url': value}, session))['asset']

    def test_legacy_owner_session_and_existing_store_survive_migration(self):
        self.ok(self.work(self.owner, content='legacy owner record'))
        legacy_folder = self.folder / 'legacy-security'
        legacy_folder.mkdir()
        config = self.security.config()
        (legacy_folder / 'gateway.json').write_text(json.dumps(config))
        token = secrets.token_urlsafe(32)
        with sqlite3.connect(legacy_folder / 'gateway.sqlite3') as connection:
            connection.execute('CREATE TABLE sessions (digest TEXT PRIMARY KEY,generation TEXT NOT NULL,created REAL NOT NULL,seen REAL NOT NULL)')
            connection.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                               (gateway.SecurityState.digest(token), config['generation'], self.now, self.now))
        migrated = gateway.SecurityState(legacy_folder, clock=lambda: self.now)
        self.public.state = migrated
        self.local.ingress_secret = migrated.upstream_secret
        session = Session(gateway.COOKIE + '=' + token, 'owner')
        account = self.ok(self.request('/auth/session', session=session))['user']
        self.assertEqual((account['id'], account['role']), ('owner', 'admin'))
        self.assertEqual(self.ok(self.request('/api/work', session=session))['entries'][0]['content'], 'legacy owner record')
        with migrated.connect() as connection:
            self.assertEqual(connection.execute('SELECT user_id FROM sessions').fetchone()[0], 'owner')
        self.assertTrue((self.base / 'work.sqlite3').is_file())

    def test_invitation_expires_without_activating_account(self):
        invitation = self.invite()
        self.ok(self.request('/auth/invitation', 'POST', {'token': invitation['invitation']}))
        self.now = invitation['expires']
        for path, payload in [('/auth/invitation', {'token': invitation['invitation']}),
                              ('/auth/activate', {'token': invitation['invitation'], 'password': MEMBER_PASSWORD})]:
            self.assertEqual(self.request(path, 'POST', payload).status, 400)
        self.assertEqual(self.security.accounts.record(invitation['user']['id'])['status'], 'invited')

    def test_invitation_can_only_be_activated_once_even_concurrently(self):
        invitation = self.invite()
        token = invitation['invitation']
        with self.security.connect() as connection:
            stored = connection.execute('SELECT digest FROM invitations').fetchone()[0]
            self.assertEqual(stored, self.security.digest(token))
            self.assertNotEqual(stored, token)
        gate = threading.Barrier(2)
        def activate(_):
            gate.wait(timeout=5)
            return self.request('/auth/activate', 'POST', {'token': token, 'password': MEMBER_PASSWORD})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(activate, range(2)))
        self.assertEqual(sorted(result.status for result in results), [200, 400])
        self.assertEqual(self.request('/auth/invitation', 'POST', {'token': token}).status, 400)

    def test_reissuing_invitation_invalidates_previous_link(self):
        invitation = self.invite()
        renewed = self.ok(self.request('/auth/users', 'POST', {'action': 'invite', 'id': invitation['user']['id']}, self.owner))
        self.assertNotEqual(invitation['invitation'], renewed['invitation'])
        self.assertEqual(self.request('/auth/activate', 'POST', {'token': invitation['invitation'], 'password': MEMBER_PASSWORD}).status, 400)
        self.ok(self.request('/auth/activate', 'POST', {'token': renewed['invitation'], 'password': MEMBER_PASSWORD}))

    def test_username_password_login_and_cookie_contract(self):
        member = self.member()
        response = self.request('/auth/login', 'POST', {'username': 'MEMBER-ONE', 'password': MEMBER_PASSWORD})
        self.ok(response)
        cookie = response.headers['Set-Cookie']
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(attribute, cookie)
        self.assertNotIn('Domain=', cookie)
        logged = Session(cookie.split(';', 1)[0], member.identity)
        self.assertEqual(self.ok(self.request('/auth/session', session=logged))['user']['id'], member.identity)
        for username, password in [('member-one', PASSWORD), ('missing-user', MEMBER_PASSWORD), ('demo', MEMBER_PASSWORD)]:
            self.assertEqual(self.request('/auth/login', 'POST', {'username': username, 'password': password}).status, 401)
        self.assertEqual(self.login('admin', PASSWORD).identity, 'owner')

    def test_member_cannot_manage_accounts_or_escalate_settings(self):
        member = self.member()
        for path, method, payload in [('/auth/users', 'GET', None),
                                      ('/auth/users', 'POST', {'action': 'disable', 'id': 'owner'}),
                                      ('/auth/users', 'POST', {'action': 'create', 'username': 'evil-admin', 'display_name': 'synthetic'})]:
            self.assertEqual(self.request(path, method, payload, member).status, 403)
        self.assertEqual(self.request('/auth/profile', 'POST', {'role': 'admin', 'id': 'owner'}, member).status, 400)
        account = self.ok(self.request('/auth/session', session=member))['user']
        self.assertEqual(account['role'], 'member')
        self.assertNotIn('password', account)
        self.assertNotIn('generation', account)
        self.assertEqual(self.security.accounts.record('owner')['status'], 'active')

    def test_disable_revokes_all_member_sessions_and_enable_does_not_restore_them(self):
        first = self.member()
        second = self.login('member-one', MEMBER_PASSWORD)
        self.ok(self.request('/auth/users', 'POST', {'action': 'disable', 'id': first.identity}, self.owner))
        for session in (first, second):
            self.assertEqual(self.request('/api/work', session=session).status, 401)
        self.assertEqual(self.request('/auth/login', 'POST', {'username': 'member-one', 'password': MEMBER_PASSWORD}).status, 401)
        self.ok(self.request('/auth/users', 'POST', {'action': 'enable', 'id': first.identity}, self.owner))
        self.assertEqual(self.request('/api/work', session=first).status, 401)
        self.assertEqual(self.login('member-one', MEMBER_PASSWORD).identity, first.identity)
        self.ok(self.request('/api/work', session=self.owner))

    def test_password_change_revokes_only_that_users_sessions(self):
        member = self.member()
        other = self.login('member-one', MEMBER_PASSWORD)
        changed = 'Replacement synthetic password 789!'
        self.ok(self.request('/auth/password', 'POST', {'old_password': MEMBER_PASSWORD, 'new_password': changed}, member))
        for session in (member, other):
            self.assertEqual(self.request('/auth/session', session=session).status, 401)
        self.ok(self.request('/auth/session', session=self.owner))
        self.assertEqual(self.request('/auth/login', 'POST', {'username': 'member-one', 'password': MEMBER_PASSWORD}).status, 401)
        self.assertEqual(self.login('member-one', changed).identity, member.identity)

    def test_profile_settings_are_private_and_persist_across_login(self):
        member = self.member()
        patches = [(self.owner, {'theme': 'classic', 'last_page': 'finance', 'navigation_open': True}),
                   (member, {'theme': 'journal', 'last_page': 'notes', 'navigation_open': False,
                             'weather_city': {'name': '合成城市', 'latitude': 30.1, 'longitude': 120.2}})]
        for session, values in patches:
            self.ok(self.request('/auth/profile', 'POST', values, session))
        for session, values in patches:
            self.assertEqual(self.ok(self.request('/auth/session', session=session))['user']['settings'], values)
        renewed = self.login('member-one', MEMBER_PASSWORD)
        self.assertEqual(self.ok(self.request('/auth/session', session=renewed))['user']['settings'], patches[1][1])

    def test_stale_client_account_cannot_read_or_write_after_account_switch(self):
        member = self.member()
        for path, method, payload in [('/api/work', 'GET', None),
                                      ('/api/work', 'POST', {'content': 'must not write'}),
                                      ('/auth/profile', 'POST', {'theme': 'journal'})]:
            response = self.request(path, method, payload, member,
                                    headers={'X-Tiangong-Client-Account': 'owner'})
            self.assertEqual(response.status, 409)
            self.assertEqual(response.headers.get('X-Tiangong-Account-Changed'), '1')
        self.assertEqual(self.ok(self.request('/api/work', session=member))['entries'], [])
        self.assertEqual(self.ok(self.request('/auth/session', session=member))['user']['settings'], {})

    def test_concurrent_two_users_keep_same_ids_and_private_images_separate(self):
        member = self.member()
        gate = threading.Barrier(2)
        def write(session, label, color):
            gate.wait(timeout=5)
            for index in range(6):
                self.ok(self.work(session, key='same-' + str(index), content=label + '-' + str(index)))
                self.ok(self.request('/api/todos', 'POST', {'id': 'same-' + str(index), 'title': label + '-' + str(index)}, session))
            asset = self.upload(session, color=color)
            self.ok(self.request('/api/travel', 'POST', {'scope': 'domestic', 'place_id': 'test-province',
                                'note': label, 'photos': [asset['id']]}, session))
            note = self.ok(self.request('/api/notes', 'POST', {'id': 'same-note', 'html': '<p>' + label + '</p>',
                'attachments': [{'name': 'synthetic.png', 'data': base64.b64encode(picture(color)).decode()}]}, session))['entry']
            return asset, note
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, self.owner, 'owner-only', 'red'),
                       pool.submit(write, member, 'member-only', 'blue')]
            owner_result, member_result = [future.result(timeout=30) for future in futures]
        for session, other, label, result, foreign in [
                (self.owner, member, 'owner-only', owner_result, member_result),
                (member, self.owner, 'member-only', member_result, owner_result)]:
            rows = self.ok(self.request('/api/work', session=session))['entries']
            self.assertEqual({row['content'] for row in rows}, {label + '-' + str(i) for i in range(6)})
            tasks = self.ok(self.request('/api/todos', session=session))['tasks']
            self.assertEqual({row['title'] for row in tasks}, {label + '-' + str(i) for i in range(6)})
            self.assertEqual(self.ok(self.request('/api/travel', session=session))['visits'][0]['note'], label)
            self.assertEqual(self.ok(self.request('/api/notes', session=session))['notes'][0]['text'], label)
            asset, note = result
            self.ok(self.request(asset['url'], session=session))
            self.assertEqual(self.request(asset['url'], session=other).status, 404)
            note_url = '/api/notes/asset/' + note['attachments'][0]['key']
            self.assertEqual(self.request(note_url, session=session).raw, picture('red' if session.identity == 'owner' else 'blue'))
            self.assertEqual(self.request(note_url, session=other).status, 404)
            self.assertEqual(self.request('/api/travel', 'POST', {'scope': 'domestic', 'place_id': 'test-province',
                             'photos': [foreign[0]['id']]}, session).status, 400)
        self.assertTrue((self.base / 'work.sqlite3').is_file())
        self.assertTrue((self.base / 'users' / member.identity / 'work.sqlite3').is_file())
        self.assertEqual(tenant_storage.current(), 'owner')

    def test_receipt_records_history_and_evidence_cannot_be_requested_by_other_user(self):
        member = self.member()
        asset = self.upload(self.owner, kind='receipts')
        payload = {'id': 'owner-receipt', 'request_id': 'receipt-create-test', 'received_on': '2020-01-02',
                   'project': 'owner project', 'owner': 'synthetic owner', 'amount_cents': 12345,
                   'photos': [asset['id']]}
        row = self.ok(self.request('/api/receipts', 'POST', payload, self.owner))['record']
        self.ok(self.request('/api/receipts', 'POST', {**payload, 'request_id': 'receipt-edit-test',
                              'revision': row['revision'], 'note': 'private history'}, self.owner))
        self.assertEqual(self.ok(self.request('/api/receipts?month=2020-01', session=member))['records'], [])
        self.assertEqual(self.request('/api/receipts/history?id=owner-receipt', session=member).status, 400)
        self.assertEqual(self.request(asset['url'], session=member).status, 404)
        self.assertEqual(self.request('/api/receipts', 'POST', {**payload, 'revision': 1,
                         'request_id': 'member-forged-edit'}, member).status, 409)
        self.assertEqual(self.request('/api/receipts', 'POST', {**payload,
                         'request_id': 'member-forged-image'}, member).status, 400)
        state = self.ok(self.request('/api/receipts?month=2020-01', session=self.owner))
        self.assertEqual((state['count'], state['total_cents']), (1, 12345))
        history = self.ok(self.request('/api/receipts/history?id=owner-receipt', session=self.owner))
        self.assertEqual(len(history['history']), 1)

    def test_finance_hub_same_account_ids_and_request_ids_are_private_per_tenant(self):
        member = self.member()
        gate = threading.Barrier(2)
        def save_account(session, label, amount):
            gate.wait(timeout=5)
            return self.ok(self.request('/api/finance/manage', 'POST', {
                'action': 'account_save', 'id': 'same-bank', 'request_id': 'same-create-request',
                'name': label, 'kind': 'bank', 'balance_cents': amount,
                'as_of': '2020-01-01', 'confirmed': True}, session))['entity']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save_account, self.owner, 'owner-private-bank', 900000),
                       pool.submit(save_account, member, 'member-private-bank', 120000)]
            for future in futures:
                self.assertEqual(future.result(timeout=15)['revision'], 1)
        self.ok(self.request('/api/finance/manage', 'POST', {
            'action': 'settings_save', 'request_id': 'owner-settings',
            'debt_inventory_confirmed': True}, self.owner))
        for session, label, amount, confirmed in [
                (self.owner, 'owner-private-bank', 900000, True),
                (member, 'member-private-bank', 120000, False)]:
            state = self.ok(self.request('/api/finance/hub?month=2020-01', session=session))
            self.assertEqual([(row['id'], row['name'], row['current_balance_cents'])
                              for row in state['accounts']], [('same-bank', label, amount)])
            self.assertEqual(state['settings']['debt_inventory_confirmed'], confirmed)
            self.assertEqual(state['summary']['confirmed_cash_cents'], amount)
        self.ok(self.request('/api/finance/manage', 'POST', {
            'action': 'account_save', 'id': 'same-bank', 'revision': 1,
            'request_id': 'member-edit-request', 'name': 'member-private-edited',
            'kind': 'bank', 'balance_cents': 110000,
            'as_of': '2020-01-01', 'confirmed': True}, member))
        owner_state = self.ok(self.request('/api/finance/hub?month=2020-01', session=self.owner))
        member_state = self.ok(self.request('/api/finance/hub?month=2020-01', session=member))
        self.assertEqual(owner_state['accounts'][0]['name'], 'owner-private-bank')
        self.assertEqual(owner_state['accounts'][0]['revision'], 1)
        self.assertEqual(member_state['accounts'][0]['current_balance_cents'], 110000)
        self.assertEqual(member_state['accounts'][0]['revision'], 2)
        self.assertTrue((self.base / 'finance.sqlite3').is_file())
        self.assertTrue((self.base / 'users' / member.identity / 'finance.sqlite3').is_file())

    def test_finance_hub_demo_reads_empty_own_state_and_cannot_manage_it(self):
        self.ok(self.request('/api/finance/manage', 'POST', {
            'action': 'account_save', 'id': 'owner-bank', 'request_id': 'owner-bank-create',
            'name': 'private-owner-finance-marker', 'kind': 'bank',
            'balance_cents': 123456, 'as_of': '2020-01-01', 'confirmed': True}, self.owner))
        self.ok(self.request('/api/finance/manage', 'POST', {
            'action': 'budget_save', 'request_id': 'owner-budget-create',
            'month': '2020-01', 'category': '咖啡饮料', 'cents': 12345}, self.owner))
        reply = self.request('/auth/demo', 'POST', {})
        self.ok(reply, 303)
        demo = Session(reply.headers['Set-Cookie'].split(';', 1)[0], 'demo')
        response = self.request('/api/finance/hub?month=2020-01', session=demo)
        state = self.ok(response)
        for collection in ('accounts', 'debts', 'payments', 'wishes'):
            self.assertEqual(state[collection], [])
        self.assertTrue(all(row['cents'] is None for row in state['budgets']))
        self.assertFalse(state['settings']['debt_inventory_confirmed'])
        self.assertIsNone(state['summary']['confirmed_cash_cents'])
        self.assertNotIn(b'private-owner-finance-marker', response.raw)
        for payload in [
                {'action': 'account_save', 'id': 'owner-bank', 'request_id': 'demo-write',
                 'name': 'must-not-save', 'kind': 'bank', 'balance_cents': 999},
                {'action': 'settings_save', 'request_id': 'demo-settings',
                 'debt_inventory_confirmed': True}]:
            self.assertEqual(self.request('/api/finance/manage', 'POST', payload, demo).status, 403)
        self.assertEqual(self.ok(self.request('/api/finance/hub', session=demo))['accounts'], [])
        owner_state = self.ok(self.request('/api/finance/hub?month=2020-01', session=self.owner))
        self.assertEqual(owner_state['accounts'][0]['current_balance_cents'], 123456)
        self.assertEqual(next(row['cents'] for row in owner_state['budgets']
                              if row['category'] == '餐饮'), 12345)

    def test_finance_hub_routes_require_session_signed_ingress_and_matching_client(self):
        member = self.member()
        for path, method, payload in [
                ('/api/finance/hub?month=2020-01', 'GET', None),
                ('/api/finance/manage', 'POST', {'action': 'account_save', 'id': 'must-not-save'})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, payload).status, 401)
                self.assertEqual(self.request(path, method, payload,
                    headers={'X-Tiangong-Account': 'owner'}).status, 401)
                self.assertEqual(self.request(path, method, payload, direct=True).status, 401)
                mismatch = self.request(path, method, payload, member,
                                        headers={'X-Tiangong-Client-Account': 'owner'})
                self.assertEqual(mismatch.status, 409)
                self.assertEqual(mismatch.headers.get('X-Tiangong-Account-Changed'), '1')
        self.assertEqual(self.ok(self.request('/api/finance/hub', session=member))['accounts'], [])
        self.assertEqual(self.ok(self.request('/api/finance/hub', session=self.owner))['accounts'], [])

    def test_grouped_budget_and_repayment_references_cannot_cross_tenants(self):
        member = self.member()
        entry = self.ok(self.request('/api/finance', 'POST', {
            'id': 'owner-deduction', 'date': '2020-01-16', 'cents': 45000,
            'name': 'private owner deduction', 'type': 'financing', 'direction': 'out',
            'category': '贷款还款'}, self.owner))['entry']
        payload = {'action': 'debt_save', 'id': 'same-loan', 'request_id': 'same-link-request',
                   'name': 'synthetic mortgage', 'kind': 'mortgage', 'principal_cents': 1000000,
                   'confirmed': True, 'as_of': '2020-01-23', 'annual_rate_bp': 320,
                   'verified_payment_refs': [{'month': '2020-01', 'entry_ids': [entry['id']]}]}
        self.ok(self.request('/api/finance/manage', 'POST', payload, self.owner))
        self.assertEqual(self.request('/api/finance/manage', 'POST', payload, member).status, 400)
        self.assertEqual(self.ok(self.request('/api/finance/hub?month=2020-01', session=member))['debts'], [])
        for session, amount in [(self.owner, 11100), (member, 22200)]:
            self.ok(self.request('/api/finance/manage', 'POST', {
                'action': 'budget_save', 'request_id': 'same-group-budget', 'month': '2020-01',
                'category': '餐饮', 'cents': amount}, session))
            state = self.ok(self.request('/api/finance/hub?month=2020-01', session=session))
            self.assertEqual(next(b['cents'] for b in state['budgets'] if b['category'] == '餐饮'), amount)
        owner = self.ok(self.request('/api/finance/hub?month=2020-01', session=self.owner))
        self.assertEqual(owner['debts'][0]['payment_quote']['recorded_total_cents'], 45000)
        self.assertEqual(owner['payments'], [])

    def test_finance_hub_invalid_month_queries_return_400_without_data(self):
        member = self.member()
        for session in (self.owner, member):
            for query in ('month=2020-13', 'month=2020-00', 'month=2020-1', 'month=invalid',
                          'month=2020-01&month=2020-02', 'month=2020-01&user=owner', 'user=owner'):
                with self.subTest(identity=session.identity, query=query):
                    response = self.request('/api/finance/hub?' + query, session=session)
                    self.assertEqual(response.status, 400, response.raw[:500])
                    self.assertNotIn('accounts', response.data)
            valid = self.ok(self.request('/api/finance/hub?month=2020-02', session=session))
            self.assertEqual(valid['month'], '2020-02')
            self.assertEqual(valid['accounts'], [])

    def test_untrusted_account_headers_cannot_impersonate_another_account(self):
        member = self.member()
        self.ok(self.work(self.owner, content='owner-only'))
        signed_owner = tenant_storage.headers(self.security.upstream_secret, 'owner', 'GET', '/api/work')
        # Even a supplied valid internal signature must be replaced by the
        # gateway's session identity, never forwarded as caller-controlled data.
        self.assertEqual(self.ok(self.request('/api/work', session=member, headers=signed_owner))['entries'], [])
        self.assertEqual(self.request('/api/work', headers=signed_owner).status, 401)
        self.assertEqual(self.request('/api/work', headers={'X-Tiangong-Account': 'owner'}).status, 401)

    def test_unsigned_forged_expired_and_retargeted_backend_requests_are_rejected(self):
        self.ok(self.work(self.owner, content='owner-only'))
        valid = tenant_storage.headers(self.security.upstream_secret, 'owner', 'GET', '/api/work')
        cases = [{}, {'X-Tiangong-Account': 'owner'},
                 tenant_storage.headers('wrong-secret', 'owner', 'GET', '/api/work'),
                 tenant_storage.headers(self.security.upstream_secret, 'owner', 'GET', '/api/work', now=1),
                 tenant_storage.headers(self.security.upstream_secret, 'owner', 'GET', '/api/notes')]
        for headers in cases:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request('/api/work', direct=True, headers=headers).status, 401)
        self.ok(self.request('/api/work', direct=True, headers=valid))
        self.assertEqual(self.request('/api/work', 'POST', {}, direct=True, headers=valid).status, 401)
        redirect = self.request('/', direct=True)
        self.assertEqual((redirect.status, redirect.headers['Location']), (303, ORIGIN + '/login'))
        self.assertEqual(self.request('/api/work', 'HEAD', direct=True).status, 405)

    def test_non_loopback_peer_cannot_use_an_internal_signature(self):
        class SimulatedLANHandler(web.Handler):
            def setup(handler):
                super().setup()
                handler.client_address = ('192.168.10.22', handler.client_address[1])
        lan = ThreadingHTTPServer(('127.0.0.1', 0), SimulatedLANHandler)
        lan.allowed_hosts = {'127.0.0.1:' + str(lan.server_port)}
        lan.allowed_networks = [ipaddress.ip_network('192.168.1.0/24')]
        lan.ingress_secret = self.security.upstream_secret
        lan.public_origin = ORIGIN
        worker = threading.Thread(target=lan.serve_forever, daemon=True)
        worker.start(); self.addCleanup(self.stop, lan, worker)
        connection = http.client.HTTPConnection('127.0.0.1', lan.server_port, timeout=5)
        try:
            connection.request('GET', '/api/work', headers=tenant_storage.headers(self.security.upstream_secret, 'owner', 'GET', '/api/work'))
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 401)
        finally:
            connection.close()

    def test_recipe_kitchens_require_auth_and_share_only_after_formal_invitation(self):
        self.assertEqual(self.request('/api/recipes').status, 401)
        owner = self.ok(self.request('/api/recipes', session=self.owner))
        owner = self.ok(self.request('/api/recipes', 'POST', dict(action='member-save',
            revision=owner['revision'], name='合成家人', avoid=['花生'], portion=1), self.owner))
        invite = self.ok(self.request('/api/recipes', 'POST', dict(action='invite',
            revision=owner['revision']), self.owner))['invite']
        member = self.member()
        self.assertEqual(self.ok(self.request('/api/recipes', session=member))['members'], [])
        self.assertEqual(self.ok(self.request('/auth/profile', 'POST', {'last_page': 'recipes'}, member))['user']['settings']['last_page'], 'recipes')
        joined = self.ok(self.request('/api/recipes', 'POST', dict(action='join', code=invite), member))
        self.assertEqual(joined['members'][0]['name'], '合成家人')
        self.assertFalse(joined['owner'])
        self.ok(self.request('/api/recipes', 'POST', dict(action='wish-add',
            revision=joined['revision'], member='我', name='番茄炒蛋'), member))
        refreshed = self.ok(self.request('/api/recipes', session=self.owner))
        self.assertEqual(refreshed['wishes'][0]['name'], '番茄炒蛋')
        self.assertFalse(refreshed['wishes'][0]['mine'])
        self.assertEqual(self.request('/api/recipes', 'POST', dict(action='wish-add',
            revision=owner['revision'], member='我', name='过期编辑'), self.owner).status, 409)
        response = self.request('/auth/guest', 'POST', {})
        self.ok(response, 303)
        cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        identity = self.ok(self.request('/auth/session', headers={'Cookie': cookie}))['user']['id']
        guest = Session(cookie, identity)
        private = self.ok(self.request('/api/recipes', session=guest))
        self.assertEqual(private['members'], [])
        self.ok(self.request('/api/recipes', 'POST', dict(action='wish-add',
            revision=private['revision'], member='我', name='游客自己的菜'), guest))
        for action in ('invite', 'join', 'analysis-request'):
            self.assertEqual(self.request('/api/recipes', 'POST', dict(action=action,
                revision=private['revision'], code=invite), guest).status, 403)

    def test_demo_is_read_only_and_cannot_read_owner_records_or_photos(self):
        self.ok(self.work(self.owner, content='private-owner-marker'))
        self.ok(self.request('/api/todos', 'POST', {'title': 'private-owner-marker'}, self.owner))
        self.ok(self.request('/api/notes', 'POST', {'html': '<p>private-owner-marker</p>'}, self.owner))
        self.ok(self.request('/api/finance', 'POST', {'date': '2020-01-02', 'cents': 12345, 'type': 'expense',
                            'name': 'private-owner-marker', 'category': 'synthetic'}, self.owner))
        asset = self.upload(self.owner)
        response = self.request('/auth/demo', 'POST', {})
        self.ok(response, 303)
        demo = Session(response.headers['Set-Cookie'].split(';', 1)[0], 'demo')
        self.assertEqual(self.ok(self.request('/auth/session', session=demo))['user']['role'], 'demo')
        for path, key in [('/api/work', 'entries'), ('/api/todos', 'tasks'), ('/api/notes', 'notes'),
                          ('/api/finance', 'records'), ('/api/receipts?month=2020-01', 'records')]:
            self.assertEqual(self.ok(self.request(path, session=demo))[key], [])
        self.assertEqual(self.request(asset['url'], session=demo).status, 404)
        for path in ('/api/work', '/api/notes', '/api/todos', '/api/travel/upload', '/api/receipts',
                     '/auth/users', '/auth/profile', '/auth/password'):
            self.assertEqual(self.request(path, 'POST', {}, demo).status, 403)
        self.assertEqual(self.ok(self.request('/api/work', session=self.owner))['entries'][0]['content'], 'private-owner-marker')

    def test_owner_apple_health_and_report_attachments_never_leak_to_member_or_demo(self):
        member = self.member()
        source = {'workouts': [{'name': 'owner-only Apple Health exercise', 'source_app': 'Apple Health',
                  'start': '2020-01-02T10:00:00', 'end': '2020-01-02T11:00:00',
                  'duration_seconds': 3600, 'active_kcal': 200}]}
        (self.apple_source / '健康训练日报_2020-01-02_V1.json').write_text(json.dumps(source))
        (self.base / 'training-cache.json').write_text(json.dumps({'owner-cache': 'never shared'}))
        report = self.ok(self.request('/api/wellness/report', 'POST', {
            'kind': '体测', 'date': '2020-01-02', 'title': 'owner-only report', 'weight': 80,
            'attachments': [{'name': 'synthetic.png', 'base64': base64.b64encode(picture('green')).decode()}]}, self.owner))['entry']
        owner_state = self.ok(self.request('/api/wellness', session=self.owner))
        self.assertEqual(owner_state['training']['2020-01-02']['items'][0]['name'], 'owner-only Apple Health exercise')
        asset = '/api/report/' + report['id'] + '/' + report['attachments'][0]['key']
        self.ok(self.request(asset, session=self.owner))
        demo_reply = self.request('/auth/demo', 'POST', {})
        self.ok(demo_reply, 303)
        demo = Session(demo_reply.headers['Set-Cookie'].split(';', 1)[0], 'demo')
        for session in (member, demo):
            state = self.ok(self.request('/api/wellness', session=session))
            self.assertEqual(state['training'], {})
            self.assertEqual(state['reports'], [])
            self.assertEqual(self.request(asset, session=session).status, 404)
            self.assertEqual(self.request('/api/report/' + report['id'], session=session).status, 404)
        with tenant_storage.scope(member.identity):
            (web.wellness.BASE / 'training-cache.json').write_text(json.dumps({'member-cache': {'items': []}}))
        self.assertEqual(self.ok(self.request('/api/wellness', session=member))['training'], {'member-cache': {'items': []}})
        self.assertEqual(self.request('/api/todos/sync', 'POST', {'action': 'sync'}, member).status, 403)


if __name__ == '__main__':
    unittest.main()
