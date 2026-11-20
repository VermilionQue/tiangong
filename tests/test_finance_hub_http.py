"""Real V34 HTTP routes with synthetic tenants in a temporary directory.

Also serves the same isolated backend to test_finance_hub_browser.cjs. No
production database, private document, backup scheduler or external API is used.
"""
import http.client
import ipaddress
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finance
import finance_hub as hub
import tenant_storage
import work_server as web


class SyntheticFinanceServer:
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='tiangong-finance-v34-http-')
        self.root = Path(self.directory.name)
        self.patches = [patch.object(tenant_storage, 'BASE', self.root),
                        patch.object(web.nas_backup, 'request_backup', return_value=None),
                        patch.object(hub, 'today', lambda: '2026-10-23')]
        for switch in self.patches:
            switch.start()
        assert Path(finance.DB).is_relative_to(self.root), 'Refusing to use a non-temporary ledger'

        class Handler(web.Handler):
            def _get(self):
                route = urlsplit(self.path).path
                if route in ('/api/finance', '/api/finance/hub'):
                    return super()._get()
                if route == '/api/daily-quote':
                    return self.reply(200, {'text': '仅用于自动化测试', 'date': '2026-10-23', 'source': 'test'})
                if route.startswith('/api/') or route.startswith('/auth/'):
                    return self.reply(404, {'error': 'Synthetic test only enables finance APIs'})
                if route in ('/', '/index.html'):
                    html = (web.ROOT / 'src/web/index.html').read_text()
                    allowed = {'theme.js', 'app.js', 'finance-hub-v34.js', 'navigation.js', 'scrollbars-v34.js'}
                    def filter_script(match):
                        tag = match[0]
                        source = re.search(r'(?<![\w-])src="([^"]+)"', tag)
                        if source:
                            return tag if urlsplit(source[1]).path in allowed else ''
                        deferred = re.search(r'data-app-src="([^"]+)"', tag)
                        if not deferred:
                            return tag
                        path = urlsplit(deferred[1]).path
                        if '--all-modules' in sys.argv or path.startswith('scripts-core.'):
                            return tag
                        if path.startswith('scripts-modules.'):
                            return ''.join(f'<script type="application/x-tiangong" data-app-src="{name}"></script>'
                                           for name in ('navigation.js', 'scrollbars-v34.js'))
                        return ''
                    html = re.sub(r'<script\b[^>]*>\s*</script>', filter_script, html)
                    body = html.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                    return
                if route == '/assets/fonts/pipi-diary.ttf':
                    return self.reply(404, {'error': 'Private font is not read by tests'})
                return super()._get()

            def _post(self):
                if urlsplit(self.path).path not in ('/api/finance', '/api/finance/manage'):
                    return self.reply(404, {'error': 'Synthetic test only enables finance writes'})
                return super()._post()

        self.server = web.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.origin = 'http://' + self.host
        self.server.allowed_hosts = {self.host}
        self.server.allowed_networks = [ipaddress.ip_network('127.0.0.0/8')]
        self.server.lan = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)
        for switch in reversed(self.patches):
            switch.stop()
        self.directory.cleanup()

    def request(self, route, payload=None, origin=True):
        connection = http.client.HTTPConnection(self.host, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = self.origin
        connection.request('GET' if payload is None else 'POST', route,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    def manage(self, action, **values):
        status, body = self.request('/api/finance/manage', {
            'action': action, 'request_id': str(uuid.uuid4()), **values})
        assert status == 200, (status, body)
        return body['entity']

    def entry(self, **values):
        data = {'date': '2026-10-23', 'name': '合成账目', 'cents': 3000,
                'type': 'expense', 'category': '测试', 'track': 'daily',
                'budget_category': '咖啡饮料', 'account_id': 'test-bank',
                'request_id': str(uuid.uuid4()), **values}
        status, body = self.request('/api/finance', data)
        assert status == 200, (status, body)
        return body['entry']

    def state(self):
        status, body = self.request('/api/finance/hub?month=2026-10')
        assert status == 200, body
        return body

    def seed(self):
        self.manage('account_save', id='test-bank', name='合成招商银行卡', kind='bank',
                    balance_cents=1000000, as_of='2026-09-30', confirmed=True)
        self.manage('account_save', id='test-wallet', name='合成微信零钱', kind='e_wallet',
                    balance_cents=20000, as_of='2026-09-30', confirmed=True)
        self.manage('debt_save', id='test-loan', name='合成消费贷', kind='interest_only',
                    principal_cents=50000, as_of='2026-09-30', confirmed=True,
                    monthly_payment_cents=11000, annual_rate_bp=600)
        self.manage('wish_save', id='test-wish', name='合成心愿', price_cents=20000)
        self.manage('budget_save', month='2026-10', category='咖啡饮料', cents=10000)


class FinanceHubHTTPTest(unittest.TestCase):
    def setUp(self):
        self.service = SyntheticFinanceServer().__enter__()
        self.addCleanup(self.service.__exit__, None, None, None)

    def test_origin_validation_and_confirmation_require_a_real_date(self):
        self.assertEqual(self.service.state()['accounts'], [])
        payload = {'action': 'account_save', 'request_id': 'http-confirm-1', 'id': 'test-bank',
                   'name': '合成招商卡', 'kind': 'bank', 'balance_cents': 100000,
                   'as_of': None, 'confirmed': True}
        self.assertEqual(self.service.request('/api/finance/manage', payload, origin=False)[0], 403)
        self.assertEqual(self.service.request('/api/finance/manage', payload)[0], 400)
        self.assertEqual(self.service.state()['accounts'], [])
        payload['as_of'] = '2026-09-30'
        self.assertEqual(self.service.request('/api/finance/manage', payload)[0], 200)
        self.assertEqual(self.service.state()['accounts'][0]['current_balance_cents'], 100000)
        self.assertEqual(self.service.request('/api/finance/hub?month=2026-13')[0], 400)

    def test_channel_is_not_a_second_account_and_transfer_is_symmetric(self):
        self.service.seed()
        self.service.entry(channel='微信支付', cents=3000)
        first = self.service.state()
        balances = {row['id']: row['current_balance_cents'] for row in first['accounts']}
        self.assertEqual(balances, {'test-bank': 997000, 'test-wallet': 20000})
        self.service.entry(type='transfer', direction='out', cents=5000,
                           to_account_id='test-wallet', channel='微信支付')
        state = self.service.state()
        balances = {row['id']: row['current_balance_cents'] for row in state['accounts']}
        self.assertEqual(balances, {'test-bank': 992000, 'test-wallet': 25000})
        self.assertEqual(sum(balances.values()), 1017000)
        self.assertEqual(state['summary']['monthly_expense_cents'], 3000)
        self.assertEqual(state['summary']['monthly_income_cents'], 0)

    def test_extra_principal_does_not_mark_the_month_paid_and_retries_are_once(self):
        self.service.seed()
        self.service.manage('debt_payment', id='extra', debt_id='test-loan', date='2026-10-23',
                            payment_month='2026-10', principal_cents=3000, interest_cents=0,
                            fees_cents=0, account_id='test-bank', settles_month=False)
        state = self.service.state()
        self.assertEqual(state['debts'][0]['monthly_status'], 'recorded')
        self.assertEqual(state['summary']['monthly_expense_cents'], 0)
        payload = {'action': 'debt_payment', 'request_id': 'synthetic-http-payment-retry',
                   'id': 'monthly', 'debt_id': 'test-loan', 'date': '2026-10-23',
                   'payment_month': '2026-10', 'principal_cents': 10000, 'interest_cents': 1000,
                   'fees_cents': 0, 'account_id': 'test-bank', 'settles_month': True}
        first = self.service.request('/api/finance/manage', payload)
        second = self.service.request('/api/finance/manage', payload)
        self.assertEqual(first[0], 200); self.assertEqual(second[0], 200)
        self.assertTrue(second[1]['duplicate'])
        state = self.service.state()
        self.assertEqual(len(state['payments']), 2)
        self.assertEqual(state['debts'][0]['monthly_status'], 'paid')
        self.assertEqual(state['debts'][0]['remaining_principal_cents'], 37000)
        self.assertEqual(state['summary']['monthly_expense_cents'], 1000)
        self.assertEqual(next(a for a in state['accounts'] if a['id']=='test-bank')['current_balance_cents'], 986000)

    def test_card_statement_and_repayment_round_trip_without_duplicate_consumption(self):
        self.service.manage('debt_save',id='card',name='合成信用卡',kind='credit_card',
                            principal_cents=None,as_of=None,confirmed=False)
        bill=self.service.manage('card_statement_save',debt_id='card',consumption_month='2026-09',amount_cents=12000)
        self.assertEqual(self.service.state()['debts'][0]['card_statement']['due_month'],'2026-10')
        payment=self.service.manage('debt_payment',debt_id='card',statement_id=bill['id'],date='2026-10-20',
                                    payment_month='2026-10',principal_cents=12000,interest_cents=0,fees_cents=0,settles_month=False)
        state=self.service.state()
        self.assertEqual(state['debts'][0]['card_statement']['status'],'paid')
        self.assertEqual(state['summary']['monthly_expense_cents'],0)
        bad={'action':'card_statement_save','id':bill['id'],'revision':bill['revision'],
             'amount_cents':11000,'request_id':'synthetic-http-bill-too-small'}
        self.assertEqual(self.service.request('/api/finance/manage',bad)[0],400)
        self.service.manage('payment_void',id=payment['id'],revision=payment['revision'])
        self.assertEqual(self.service.state()['debts'][0]['card_statement']['remaining_cents'],12000)

    def test_archived_unknown_card_and_personal_interest_constraints_over_http(self):
        self.service.manage('debt_save',id='closed',name='已注销合成卡',kind='credit_card',status='closed',
                            archived_from='2026-10',closed_at=None,principal_cents=None,as_of=None,confirmed=False)
        self.service.manage('debt_save',id='family',name='合成亲友借款',kind='personal',
                            principal_cents=50000,as_of='2026-10-01',confirmed=True)
        state=self.service.state()
        self.assertEqual([d['id'] for d in state['debts']],['family'])
        self.assertEqual([d['id'] for d in state['archived_debts']],['closed'])
        self.assertIsNone(state['debts'][0]['month_paid_cents'])
        bad={'action':'debt_payment','debt_id':'family','date':'2026-10-20','principal_cents':100,
             'interest_cents':1,'fees_cents':0,'request_id':'synthetic-http-personal-interest'}
        self.assertEqual(self.service.request('/api/finance/manage',bad)[0],400)
        self.assertEqual(self.service.state()['payments'],[])

    def test_budget_refunds_restore_capacity_and_business_spending_is_excluded(self):
        self.service.seed()
        self.service.entry(cents=7000)
        budget = next(row for row in self.service.state()['budgets'] if row['category']=='餐饮')
        self.assertEqual(budget['remaining_cents'], 3000)
        self.assertEqual(budget['remainder_ratio'], .3)
        self.service.entry(type='refund', cents=2000)
        self.service.entry(cents=99999, track='fudai')
        budget = next(row for row in self.service.state()['budgets'] if row['category']=='餐饮')
        self.assertEqual(budget['consumed_cents'], 5000)
        self.assertEqual(budget['remaining_cents'], 5000)
        self.assertEqual(budget['remainder_ratio'], .5)

    def test_unverified_debt_inventory_and_unknown_principal_never_unlock_a_wish(self):
        self.service.seed()
        state = self.service.state()
        self.assertEqual(state['wishes'][0]['unlock_status'], 'unknown_inputs')
        self.assertFalse(state['wishes'][0]['unlocked'])
        self.service.manage('settings_save', debt_inventory_confirmed=True)
        self.assertEqual(self.service.state()['wishes'][0]['unlock_status'], 'debt_outstanding')
        self.service.manage('debt_save', id='unknown-debt', name='合成待核借款', kind='short_term',
                            principal_cents=None, as_of=None, confirmed=False)
        self.assertEqual(self.service.state()['wishes'][0]['unlock_status'], 'unknown_inputs')

    def test_ledger_idempotency_does_not_duplicate_a_committed_retry(self):
        self.service.seed()
        payload = {'id': 'retry-expense', 'request_id': 'synthetic-http-ledger-retry',
                   'date': '2026-10-23', 'name': '重试合成记录', 'cents': 3000, 'type': 'expense',
                   'category': '咖啡', 'track': 'daily', 'budget_category': '咖啡饮料',
                   'account_id': 'test-bank', 'channel': '微信支付'}
        first = self.service.request('/api/finance', payload)
        second = self.service.request('/api/finance', payload)
        self.assertEqual(first, second)
        self.assertEqual(len(self.service.request('/api/finance')[1]['records']), 1)
        self.assertEqual(self.service.request('/api/finance', {**payload, 'cents': 4000})[0], 409)
        self.assertEqual(self.service.state()['summary']['monthly_expense_cents'], 3000)


if __name__ == '__main__':
    if '--serve-ui' in sys.argv:
        with SyntheticFinanceServer() as service:
            service.seed()
            print(json.dumps({'origin': service.origin}), flush=True)
            sys.stdin.readline()
    else:
        unittest.main()
