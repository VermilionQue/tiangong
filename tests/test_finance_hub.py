"""Synthetic money, dates and accounts only; never touch the production ledger."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finance
import finance_hub as hub
import tenant_storage


class FinanceHubTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tiangong-finance-hub-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, name, value in [(finance, 'DB', self.root/'finance.sqlite3'),
                                    (hub, 'today', lambda: '2026-10-23')]:
            switch = patch.object(target, name, value)
            switch.start(); self.addCleanup(switch.stop)

    def manage(self, action, **fields):
        return hub.manage({'action':action, 'request_id':str(uuid.uuid4()), **fields})['entity']

    def account(self, key='bank', amount=100000, as_of='2026-09-30', **fields):
        return self.manage('account_save', id=key, name='合成银行', kind='bank',
                           balance_cents=amount, as_of=as_of, confirmed=True, **fields)

    def debt(self, key='loan', principal=50000, **fields):
        return self.manage('debt_save', id=key, name='合成借款', kind='interest_only',
                           principal_cents=principal, as_of='2026-09-30', annual_rate_bp=600, confirmed=True, **fields)

    def payment(self, **fields):
        data = {'debt_id':'loan', 'date':'2026-10-10', 'principal_cents':10000,
                'interest_cents':1000, 'fees_cents':100, 'account_id':'bank'}
        data.update(fields)
        return self.manage('debt_payment', **data)

    def entry(self, **fields):
        data = {'date':'2026-10-10', 'cents':500, 'name':'合成账目', 'type':'expense',
                'category':'生活', 'track':'daily', 'budget_category':'咖啡饮料', 'account_id':'bank'}
        data.update(fields)
        return finance.save(data)

    def complete_months(self, months, mapping=None):
        return hub.apply_review({'source_digest':'a'*64, 'months':months, 'mapping_by_id':mapping or {},
                                 'reviewed_at':'2026-10-23T12:00:00+08:00'})

    def test_v44_budget_merge_preserves_old_group_override_and_monthly_entertainment(self):
        with hub.connection() as con:
            for category, cents in [('吃',50000),('喝',10000),('买菜做饭',30000),('下馆子',40000),('咖啡饮料',9000),('日用百货',8000)]:
                hub.put(con,'budget','legacy-'+category,{'month':'2026-10','category':category,'cents':cents,'source_note':''},None,'budget_save')
        budgets = {r['category']:r for r in hub.state('2026-10')['budgets']}
        self.assertEqual(budgets['餐饮']['cents'],60000)
        self.assertEqual(budgets['日用']['cents'],8000)
        self.assertEqual(budgets['娱乐']['cents'],60000)
        self.entry(cents=1000,budget_category='下馆子')
        self.entry(cents=2000,budget_category='咖啡饮料')
        self.entry(cents=500,budget_category='娱乐')
        self.entry(cents=200,budget_category='娱乐',type='refund')
        budgets = {r['category']:r for r in hub.state('2026-10')['budgets']}
        self.assertEqual(budgets['餐饮']['consumed_cents'],3000)
        self.assertEqual(budgets['娱乐']['consumed_cents'],300)
        merged=self.manage('budget_save',month='2026-10',category='餐饮',cents=70000,revision=0,legacy_revisions=budgets['餐饮']['legacy_revisions'])
        self.manage('budget_save',month='2026-10',category='娱乐',cents=0,revision=0)
        budgets={r['category']:r for r in hub.state('2026-10')['budgets']}
        self.assertEqual(budgets['餐饮']['cents'],70000)
        self.assertEqual(budgets['娱乐']['cents'],0)
        self.assertEqual(next(r for r in hub.state('2026-11')['budgets'] if r['category']=='娱乐')['cents'],60000)
        with self.assertRaises(hub.ConflictError):
            self.manage('budget_save',month='2026-10',category='咖啡饮料',cents=1234,revision=0)

    def test_v44_forecast_separates_paid_month_from_next_month_and_unknown_card(self):
        self.account(amount=1000000)
        self.debt(principal=1000000,due_day=18)
        self.manage('debt_save',id='card',name='合成信用卡',kind='credit_card',principal_cents=9999,
                    as_of='2026-09-30',confirmed=True,due_day=7)
        first,next_month=hub.state('2026-10')['debt_forecasts']
        self.assertEqual(first['known_total_cents'],5000)
        self.assertEqual(first['unknown_count'],1)
        self.assertFalse(first['complete'])
        self.payment(principal_cents=0,interest_cents=5000,fees_cents=0,settles_month=True)
        first,next_month=hub.state('2026-10')['debt_forecasts']
        self.assertEqual(first['known_total_cents'],0)
        self.assertEqual(next_month['known_total_cents'],5000)
        self.assertEqual(next_month['month'],'2026-11')
        self.assertEqual(next_month['unknown_count'],1)

    def test_no_seed_unknowns_and_debt_inventory_require_explicit_confirmation(self):
        state = hub.state('2026-10')
        self.assertEqual(state['accounts'], [])
        self.assertEqual(state['debts'], [])
        self.assertIsNone(state['summary']['confirmed_cash_cents'])
        self.assertIsNone(state['summary']['remaining_debt_cents'])
        self.assertFalse(state['settings']['debt_inventory_confirmed'])
        self.assertEqual({row['category']:row['cents'] for row in state['budgets']}, {'餐饮':None,'日用':None,'娱乐':60000})
        self.assertEqual(hub.budget_suggest('2026-10')['status'], 'coverage_insufficient')
        self.assertEqual(finance.state(), {'records':[], 'imports':[], 'budgets':[]})

    def test_actual_repayment_and_ledger_are_atomic_exact_and_reversible(self):
        self.account(); self.debt()
        self.manage('settings_save', debt_inventory_confirmed=True)
        payment = self.payment(settles_month=True)
        state = hub.state('2026-10')
        self.assertEqual(state['accounts'][0]['current_balance_cents'], 88900)
        self.assertEqual(state['debts'][0]['remaining_principal_cents'], 40000)
        self.assertEqual(state['debts'][0]['monthly_status'], 'paid')
        self.assertEqual(state['summary']['monthly_expense_cents'], 1100)
        records = finance.state()['records']
        self.assertEqual(len(records), 3)
        self.assertEqual(sum(row['cents'] for row in records if row['type']=='financing'), 10000)
        with self.assertRaises(ValueError):
            finance.save({**records[0], 'cents':999})
        void = self.manage('payment_void', id=payment['id'], revision=payment['revision'], reason='合成误录')
        self.assertTrue(void['voidedAt'])
        state = hub.state('2026-10')
        self.assertEqual(state['accounts'][0]['current_balance_cents'], 100000)
        self.assertEqual(state['debts'][0]['remaining_principal_cents'], 50000)
        self.assertEqual(state['debts'][0]['monthly_status'], 'unrecorded')
        self.assertEqual(finance.state()['records'], [])
        self.assertEqual(hub.history('payment', payment['id'])['history'], [payment])
        with finance.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM entries').fetchone()[0],3)
            self.assertEqual(con.execute('SELECT count(*) FROM history').fetchone()[0],3)

    def test_payment_failure_rolls_back_entity_and_all_ledger_components(self):
        self.account(); self.debt()
        original = hub.payment_ledger
        def interrupted(con, *args, **kwargs):
            original(con, *args, **kwargs)
            raise OSError('synthetic disk failure')
        with patch.object(hub, 'payment_ledger', interrupted), self.assertRaises(OSError):
            self.payment()
        self.assertEqual(hub.state('2026-10')['payments'], [])
        self.assertEqual(finance.state()['records'], [])

    def test_requests_are_idempotent_and_revision_conflicts_preserve_first_write(self):
        payload={'action':'account_save','id':'a','request_id':'synthetic-request-1','name':'合成卡',
                 'kind':'bank','as_of':'2026-09-30','balance_cents':12345,'confirmed':True}
        first=hub.manage(payload)
        again=hub.manage(copy.deepcopy(payload))
        self.assertTrue(again['duplicate']); self.assertEqual(again['entity'],first['entity'])
        with self.assertRaises(finance.ConflictError):hub.manage({**payload,'balance_cents':999})
        with self.assertRaises(finance.ConflictError):
            self.manage('account_save', id='a', revision=0, name='过期', kind='bank')
        self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],12345)

    def test_account_day_end_snapshot_excludes_same_day_and_unknown_account_stays_unknown(self):
        account=self.account()
        self.entry(date='2026-09-30',cents=99999)
        self.entry(date='2026-10-01',cents=1200)
        self.entry(date='2026-10-02',cents=200,type='refund')
        self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],99000)
        self.entry(date='2026-10-03',cents=300,account_id=None,channel='微信')
        state=hub.state('2026-10')
        self.assertIsNone(state['accounts'][0]['current_balance_cents'])
        self.assertEqual(state['accounts'][0]['known_postings_balance_cents'],99000)
        self.assertTrue(state['accounts'][0]['pending_entry_ids'])
        self.assertIsNone(state['summary']['confirmed_cash_cents'])
        # A later confirmed snapshot resolves earlier unmapped movements.
        self.manage('account_save',id='bank',revision=account['revision'],balance_cents=98700,
                    as_of='2026-10-03',confirmed=True)
        self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],98700)

    def test_unconfirmed_historical_snapshot_never_unlocks_and_prior_snapshots_survive(self):
        account=self.manage('account_save',id='a',name='待核对卡',kind='bank',balance_cents=900000,
                            as_of='2026-09-06',confirmed=False)
        state=hub.state('2026-10')['accounts'][0]
        self.assertIsNone(state['current_balance_cents'])
        self.assertEqual(state['snapshot_balance_cents'],900000)
        self.assertFalse(state['snapshot_confirmed'])
        self.manage('account_save',id='a',revision=account['revision'],balance_cents=800000,
                    as_of='2026-10-01',confirmed=True)
        self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],800000)
        self.assertIsNone(hub.state('2026-09')['accounts'][0]['current_balance_cents'])
        self.assertEqual(len(hub.state('2026-10')['accounts'][0]['snapshots']),2)

    def test_transfer_moves_between_accounts_without_income_or_expense(self):
        self.account('bank',100000); self.account('wallet',20000)
        transfer=self.entry(type='transfer',cents=7000,account_id='bank',to_account_id='wallet',channel='支付宝')
        state=hub.state('2026-10')
        balances={row['id']:row['current_balance_cents'] for row in state['accounts']}
        self.assertEqual(balances,{'bank':93000,'wallet':27000})
        self.assertEqual(state['summary']['confirmed_cash_cents'],120000)
        self.assertEqual(state['summary']['monthly_expense_cents'],0)
        self.assertEqual(state['summary']['monthly_income_cents'],0)
        self.assertEqual(transfer['channel'],'支付宝')

    def test_monthly_status_belongs_to_confirmed_payment_month_not_formula_or_extra_principal(self):
        self.account();self.debt(monthly_payment_cents=10000)
        self.assertEqual(hub.state('2026-10')['debts'][0]['monthly_status'],'unrecorded')
        self.payment(principal_cents=1000,interest_cents=0,fees_cents=0,payment_month='2026-09',settles_month=True)
        self.assertEqual(hub.state('2026-10')['debts'][0]['monthly_status'],'unrecorded')
        extra=self.payment(principal_cents=3000,interest_cents=0,fees_cents=0)
        self.assertEqual(hub.state('2026-10')['debts'][0]['monthly_status'],'recorded')
        self.payment(id=extra['id'],revision=extra['revision'],principal_cents=3000,
                     interest_cents=500,fees_cents=0,settles_month=True)
        self.assertEqual(hub.state('2026-10')['debts'][0]['monthly_status'],'paid')
        self.assertEqual(sum(row['cents'] for row in finance.state()['records']),4500)

    def test_debt_maturity_needs_explicit_confirmation_and_estimates_never_post(self):
        debt=self.debt(maturity='2026-11-01')
        with self.assertRaisesRegex(ValueError,'确认'):
            self.manage('debt_save',id=debt['id'],revision=debt['revision'],maturity='2027-01-01')
        self.manage('debt_save',id=debt['id'],revision=debt['revision'],maturity='2027-01-01',maturity_confirmed=True)
        self.assertEqual(hub.state('2026-10')['debts'][0]['estimated_payment_cents'],250)
        self.manage('debt_save',id='mortgage',name='合成等额本息',kind='mortgage',principal_cents=120000,
                    as_of='2026-09-30',annual_rate_bp=0,term_months=12,confirmed=True)
        mortgage=next(row for row in hub.state('2026-10')['debts'] if row['id']=='mortgage')
        self.assertEqual(mortgage['estimated_payment_cents'],10000)
        self.assertEqual(finance.state()['records'],[])

    def test_wish_rule_uses_confirmed_cash_and_complete_zero_debt_not_investment_value(self):
        self.account(amount=9999)
        self.manage('account_save',id='stocks',name='合成股票',kind='investment',balance_cents=99999999,
                    as_of='2026-09-30',confirmed=True)
        self.manage('wish_save',name='合成心愿',price_cents=1000)
        self.assertEqual(hub.state('2026-10')['wishes'][0]['unlock_status'],'unknown_inputs')
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.assertEqual(hub.state('2026-10')['wishes'][0]['unlock_status'],'saving')
        account=hub.state('2026-10')['accounts'][0]
        self.manage('account_save',id=account['id'],revision=account['revision'],balance_cents=10000,
                    as_of='2026-09-30',confirmed=True)
        self.assertTrue(hub.state('2026-10')['wishes'][0]['unlocked'])
        self.debt(principal=1)
        self.assertEqual(hub.state('2026-10')['wishes'][0]['unlock_status'],'debt_outstanding')
        self.manage('debt_save',id='unknown',name='未核对借款',kind='short_term',principal_cents=None,as_of=None)
        self.assertEqual(hub.state('2026-10')['wishes'][0]['unlock_status'],'unknown_inputs')

    def test_budget_uses_only_explicit_complete_months_daily_net_and_never_autosaves(self):
        self.entry(date='2026-01-02',cents=1000)
        self.entry(date='2026-01-03',cents=200,type='refund')
        self.entry(date='2026-02-03',cents=400)
        self.entry(date='2026-03-03',cents=99999)
        self.entry(date='2026-10-03',cents=777)
        self.entry(date='2026-01-04',cents=99999,track='stocks')
        self.assertEqual(hub.budget_suggest('2026-10')['status'],'provisional')
        self.assertTrue(all(row['cents'] is None for row in hub.budget_suggest('2026-10')['suggestions']))
        self.complete_months(['2026-01','2026-02','2026-10'])
        suggested=hub.budget_suggest('2026-10')
        self.assertEqual(suggested['based_on_months'],['2026-01','2026-02'])
        self.assertEqual(suggested['status'],'ready')
        self.assertEqual(next(row for row in suggested['suggestions'] if row['category']=='餐饮')['cents'],600)
        self.assertIn('2026-10',suggested['coverage']['partial_months'])
        self.assertEqual(finance.state()['budgets'],[])
        budget=self.manage('budget_save',month='2026-10',category='咖啡饮料',cents=1000)
        row=next(row for row in hub.state('2026-10')['budgets'] if row['category']=='餐饮')
        self.assertEqual(row['consumed_cents'],777);self.assertEqual(row['remaining_cents'],223)
        self.assertEqual(row['remainder_ratio'],.223)
        with self.assertRaises(finance.ConflictError):
            self.manage('budget_save',month='2026-10',category='咖啡饮料',cents=2000,revision=0)
        self.assertEqual(hub.history('budget',budget['id'])['entity']['cents'],1000)

    def test_classification_review_preserves_money_original_text_and_skips_stale_rows(self):
        source='  原始\n来源不改写  '
        first=self.entry(track='unknown',budget_category='unknown',source_text=source)
        second=self.entry(track='unknown',budget_category='unknown')
        mapping={row['id']:{'source_hash':hub.entry_hash(row),'track':'daily','budget_category':'咖啡饮料'} for row in (first,second)}
        finance.save({**second,'note':'用户后来修订'})
        result=self.complete_months(['2026-01'],mapping)
        self.assertEqual(result['applied'],[first['id']]);self.assertEqual(result['conflicts'],[second['id']])
        self.assertTrue(self.complete_months(['2026-01'],mapping)['already_applied'])
        records={row['id']:row for row in finance.state()['records']}
        self.assertEqual(records[first['id']]['source_text'],source)
        self.assertEqual(records[first['id']]['cents'],first['cents'])
        self.assertEqual(records[second['id']]['note'],'用户后来修订')
        with finance.connection() as con:
            original=json.loads(con.execute('SELECT data FROM history WHERE id=? AND revision=1',(first['id'],)).fetchone()[0])
        self.assertEqual(original,first)

    def test_strict_amounts_dates_and_account_ids_fail_without_mutations(self):
        self.account();self.debt()
        for values in ({'principal_cents':True},{'interest_cents':1.5},{'fees_cents':-1},
                       {'principal_cents':0,'interest_cents':0,'fees_cents':0},{'date':'2026-10-24'},
                       {'account_id':'missing'},{'principal_cents':60000}):
            with self.subTest(values=values),self.assertRaises(ValueError):self.payment(**values)
        self.assertEqual(hub.state('2026-10')['payments'],[])
        self.assertEqual(finance.state()['records'],[])
        payment=self.payment(account_id=None)
        self.assertIsNone(payment['account_id'])
        self.assertIsNone(hub.state('2026-10')['summary']['confirmed_cash_cents'])

    def test_ledger_optional_idempotency_and_raw_source_survive_legacy_edit(self):
        row=self.entry(request_id='ledger-retry-key',source_text='  原文\n不裁切  ')
        repeated=self.entry(request_id='ledger-retry-key',source_text='  原文\n不裁切  ')
        self.assertEqual(row,repeated)
        with self.assertRaises(finance.ConflictError):self.entry(request_id='ledger-retry-key',cents=501)
        legacy={key:value for key,value in row.items() if key not in ('track','budget_category','source_text','channel','account_id','to_account_id')}
        legacy['note']='旧界面修改'
        saved=finance.save(legacy)
        self.assertEqual(saved['source_text'],'  原文\n不裁切  ')
        self.assertEqual(saved['account_id'],'bank')
        self.assertEqual(saved['track'],'daily')

    def test_two_tenants_share_schema_but_never_accounts_or_requests(self):
        with patch.object(tenant_storage,'BASE',self.root/'tenants'), patch.object(finance,'DB',tenant_storage.path('finance.sqlite3')):
            for identity,amount in [('owner',100),('u'+'a'*32,200),('u'+'b'*32,300)]:
                with tenant_storage.scope(identity):
                    self.assertEqual(hub.state('2026-10')['accounts'],[])
                    hub.manage({'action':'account_save','id':'same','request_id':'same-request-id','name':'合成卡',
                                'kind':'bank','balance_cents':amount,'as_of':'2026-09-30','confirmed':True})
            for identity,amount in [('owner',100),('u'+'a'*32,200),('u'+'b'*32,300)]:
                with tenant_storage.scope(identity):
                    self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],amount)

    def test_historical_amount_without_balance_date_is_visible_but_not_confirmed(self):
        self.manage('account_save',id='historical',name='旧表余额',kind='bank',balance_cents=123456,
                    as_of=None,confirmed=False,source_note='旧表核对日期不是余额日期')
        debt=self.manage('debt_save',id='friend',name='亲友借款',kind='personal',principal_cents=200000,
                         as_of=None,confirmed=False,source_note='历史本金，未核对当前余额')
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.payment(debt_id=debt['id'],principal_cents=10000,interest_cents=0,fees_cents=0,account_id=None)
        state=hub.state('2026-10')
        self.assertEqual(state['accounts'][0]['snapshot_balance_cents'],123456)
        self.assertIsNone(state['accounts'][0]['current_balance_cents'])
        self.assertEqual(state['debts'][0]['snapshot_principal_cents'],200000)
        self.assertIsNone(state['debts'][0]['current_principal_cents'])
        self.assertIsNone(state['summary']['remaining_debt_cents'])
        self.assertIsNone(state['debts'][0]['month_principal_cents'])
        self.assertEqual(state['debts'][0]['total_principal_paid_cents'],10000)
        with self.assertRaisesRegex(ValueError,'实际基准日期'):
            self.manage('debt_save',id=debt['id'],revision=debt['revision'],confirmed=True)

    def test_same_day_new_principal_repayment_and_later_baseline_do_not_double_count(self):
        self.account()
        debt=self.manage('debt_save',id='loan',name='当天基准',kind='short_term',principal_cents=50000,
                         as_of='2026-10-10',confirmed=True)
        self.payment(principal_cents=10000,interest_cents=0,fees_cents=0)
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],40000)
        # The freshly checked 40000 already includes the earlier same-day payment.
        refreshed=self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                              as_of='2026-10-10',confirmed=True)
        self.assertTrue(refreshed['baseline_payment_ids'])
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],40000)
        self.payment(principal_cents=5000,interest_cents=0,fees_cents=0)
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],35000)

    def test_moving_prior_payment_after_rechecked_baseline_cannot_bypass_overpayment(self):
        self.account(as_of='2026-08-31')
        debt=self.manage('debt_save',id='loan',name='合成借款',kind='personal',principal_cents=50000,
                         as_of='2026-08-31',confirmed=True)
        payment=self.payment(date='2026-09-10',principal_cents=10000,interest_cents=0,fees_cents=0)
        self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                    as_of='2026-09-15',confirmed=True)
        with self.assertRaisesRegex(ValueError,'超过'):
            self.payment(id=payment['id'],revision=payment['revision'],date='2026-10-10',
                         principal_cents=90000,interest_cents=0,fees_cents=0)
        state=hub.state('2026-10')
        self.assertEqual(state['payments'][0]['principal_cents'],10000)
        self.assertEqual(state['accounts'][0]['current_balance_cents'],90000)
        self.payment(id=payment['id'],revision=payment['revision'],date='2026-10-10',
                     principal_cents=15000,interest_cents=0,fees_cents=0)
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],25000)

    def test_same_day_included_payment_correction_requires_baseline_recheck(self):
        self.account()
        debt=self.debt()
        payment=self.payment(principal_cents=10000,interest_cents=0,fees_cents=0)
        updated=self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                            as_of='2026-10-10',confirmed=True)
        with self.assertRaisesRegex(ValueError,'同日核对'):
            self.payment(id=payment['id'],revision=payment['revision'],principal_cents=90000,
                         interest_cents=0,fees_cents=0)
        # Changing just the bank/channel is harmless to the known principal.
        self.payment(id=payment['id'],revision=payment['revision'],principal_cents=10000,
                     interest_cents=0,fees_cents=0,channel='微信支付')
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],40000)
        self.assertEqual(len(updated['snapshots']),2)

    def test_pending_expense_blocks_only_affected_balance_and_never_unlocks_wish(self):
        self.account('bank',100000);self.account('other',100000)
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.manage('wish_save',name='合成心愿',price_cents=9500)
        pending=self.entry(cents=90000,confirmed=False)
        state=hub.state('2026-10');accounts={a['id']:a for a in state['accounts']}
        self.assertEqual(accounts['bank']['pending_entry_ids'],[pending['id']])
        self.assertIsNone(accounts['bank']['current_balance_cents'])
        self.assertEqual(accounts['other']['current_balance_cents'],100000)
        self.assertIsNone(state['summary']['confirmed_cash_cents'])
        self.assertEqual(state['summary']['monthly_expense_cents'],0)
        self.assertEqual(state['wishes'][0]['unlock_status'],'unknown_inputs')
        finance.save({**pending,'confirmed':True})
        self.assertEqual(hub.state('2026-10')['accounts'][0]['current_balance_cents'],10000)

    def test_clear_current_confirmation_preserves_history_without_reusing_it_today(self):
        account=self.account(as_of='2026-08-31')
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.manage('wish_save',name='合成心愿',price_cents=9500)
        cleared=self.manage('account_save',id=account['id'],revision=account['revision'],
                            balance_cents=None,as_of=None,confirmed=False)
        state=hub.state('2026-10')
        self.assertIsNone(state['summary']['confirmed_cash_cents'])
        self.assertFalse(state['wishes'][0]['unlocked'])
        self.assertEqual(hub.state('2026-09')['accounts'][0]['current_balance_cents'],100000)
        self.manage('account_save',id=account['id'],revision=cleared['revision'],balance_cents=90000,
                    as_of='2026-10-22',confirmed=True)
        self.assertEqual(hub.state('2026-10')['summary']['confirmed_cash_cents'],90000)

    def test_rechecking_debt_keeps_previous_month_snapshot_and_clear_is_not_zero(self):
        self.account(as_of='2026-08-31')
        debt=self.manage('debt_save',id='loan',name='合成借款',kind='personal',principal_cents=50000,
                         as_of='2026-08-31',confirmed=True)
        self.payment(date='2026-09-10',principal_cents=10000,interest_cents=0,fees_cents=0)
        refreshed=self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                              as_of='2026-10-01',confirmed=True)
        september=hub.state('2026-09')['debts'][0]
        self.assertEqual(september['current_principal_cents'],40000)
        self.assertEqual(september['snapshot_as_of'],'2026-08-31')
        self.assertEqual(september['snapshot_principal_cents'],50000)
        self.manage('debt_save',id=debt['id'],revision=refreshed['revision'],principal_cents=None,
                    as_of=None,confirmed=False)
        self.assertIsNone(hub.state('2026-10')['debts'][0]['current_principal_cents'])
        self.assertEqual(hub.state('2026-09')['debts'][0]['current_principal_cents'],40000)

    def test_historical_payment_and_baseline_edits_validate_each_snapshot_interval(self):
        self.account(amount=1000000,as_of='2026-08-31')
        debt=self.manage('debt_save',id='loan',name='合成借款',kind='personal',principal_cents=50000,
                         as_of='2026-08-31',confirmed=True)
        refreshed=self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                              as_of='2026-10-01',confirmed=True)
        with self.assertRaisesRegex(ValueError,'超过'):
            self.payment(date='2026-09-10',principal_cents=60000,interest_cents=0,fees_cents=0)
        payment=self.payment(date='2026-09-10',principal_cents=30000,interest_cents=0,fees_cents=0)
        with self.assertRaisesRegex(ValueError,'超过'):
            self.payment(id=payment['id'],revision=payment['revision'],date='2026-09-10',
                         principal_cents=60000,interest_cents=0,fees_cents=0)
        with self.assertRaisesRegex(ValueError,'超过'):
            self.manage('debt_save',id=debt['id'],revision=refreshed['revision'],principal_cents=20000,
                        as_of='2026-08-31',confirmed=True)
        self.assertEqual(hub.state('2026-09')['debts'][0]['current_principal_cents'],20000)
        self.assertEqual(hub.state('2026-10')['debts'][0]['current_principal_cents'],40000)

    def test_older_same_day_included_payment_cannot_bypass_newer_baseline(self):
        self.account(as_of='2026-08-31')
        debt=self.manage('debt_save',id='loan',name='合成借款',kind='personal',principal_cents=50000,
                         as_of='2026-08-31',confirmed=True)
        payment=self.payment(date='2026-09-10',principal_cents=10000,interest_cents=0,fees_cents=0)
        debt=self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                         as_of='2026-09-10',confirmed=True)
        self.manage('debt_save',id=debt['id'],revision=debt['revision'],principal_cents=40000,
                    as_of='2026-10-01',confirmed=True)
        with self.assertRaisesRegex(ValueError,'同日核对'):
            self.payment(id=payment['id'],revision=payment['revision'],date='2026-09-10',
                         principal_cents=90000,interest_cents=0,fees_cents=0)
        self.assertEqual(hub.state('2026-09')['debts'][0]['current_principal_cents'],40000)

    def test_legacy_inconsistent_negative_principal_cannot_unlock_wish(self):
        self.account();debt=self.debt()
        self.payment(principal_cents=10000,interest_cents=0,fees_cents=0)
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.manage('wish_save',name='合成心愿',price_cents=1000)
        # Simulate inconsistent legacy data using only this test's private DB.
        debt['snapshots'][0]['principal_cents']=5000
        with hub.connection() as con:
            con.execute('UPDATE hub_entities SET data=? WHERE kind=? AND id=?',
                        (json.dumps(debt),'debt',debt['id']))
        state=hub.state('2026-10')
        self.assertEqual(state['debts'][0]['principal_discrepancy_cents'],-5000)
        self.assertIsNone(state['debts'][0]['current_principal_cents'])
        self.assertEqual(state['wishes'][0]['unlock_status'],'unknown_inputs')

    def test_reconfirmation_preserves_preceding_unknown_account_and_debt_period(self):
        account=self.account(as_of='2026-08-31')
        debt=self.manage('debt_save',id='loan',name='合成借款',kind='personal',principal_cents=50000,
                         as_of='2026-08-31',confirmed=True)
        with patch.object(hub,'today',lambda:'2026-09-20'):
            account=self.manage('account_save',id=account['id'],revision=account['revision'],
                                balance_cents=None,as_of=None,confirmed=False)
            debt=self.manage('debt_save',id=debt['id'],revision=debt['revision'],
                             principal_cents=None,as_of=None,confirmed=False)
        self.manage('account_save',id=account['id'],revision=account['revision'],
                    balance_cents=90000,as_of='2026-10-01',confirmed=True)
        self.manage('debt_save',id=debt['id'],revision=debt['revision'],
                    principal_cents=40000,as_of='2026-10-01',confirmed=True)
        september=hub.state('2026-09');october=hub.state('2026-10')
        self.assertIsNone(september['accounts'][0]['current_balance_cents'])
        self.assertIsNone(september['debts'][0]['current_principal_cents'])
        self.assertEqual(october['accounts'][0]['current_balance_cents'],90000)
        self.assertEqual(october['debts'][0]['current_principal_cents'],40000)

    def test_payment_wallet_is_available_cash_but_unknown_wallet_blocks_wish(self):
        self.account(amount=10000)
        wallet=self.manage('account_save',id='wallet',name='合成微信零钱',kind='e_wallet',
                           balance_cents=30000,as_of='2026-09-30',confirmed=True)
        self.manage('account_save',id='investment',name='合成理财',kind='investment',
                    balance_cents=999999,as_of='2026-09-30',confirmed=True)
        self.manage('settings_save',debt_inventory_confirmed=True)
        self.manage('wish_save',name='合成心愿',price_cents=3500)
        self.assertEqual(hub.state('2026-10')['summary']['confirmed_cash_cents'],40000)
        self.assertTrue(hub.state('2026-10')['wishes'][0]['unlocked'])
        self.manage('account_save',id=wallet['id'],revision=wallet['revision'],confirmed=False)
        self.assertIsNone(hub.state('2026-10')['summary']['confirmed_cash_cents'])
        self.assertFalse(hub.state('2026-10')['wishes'][0]['unlocked'])

    def test_pending_expense_in_complete_month_is_disclosed_and_cannot_formalize_budget(self):
        self.entry(date='2026-09-03',cents=1000)
        pending=self.entry(date='2026-09-04',cents=90000,confirmed=False)
        self.complete_months(['2026-09'])
        result=hub.budget_suggest('2026-10')
        self.assertEqual(result['status'],'mapping_incomplete')
        self.assertEqual(result['unmapped_entry_ids'],[pending['id']])
        coffee=next(row for row in result['suggestions'] if row['category']=='餐饮')
        self.assertIsNone(coffee['cents'])
        self.assertEqual(coffee['provisional_cents'],1000)

    def test_incomplete_months_offer_only_labeled_reference_with_ten_month_limit(self):
        self.entry(date='2025-11-01',cents=900000)
        self.entry(date='2025-12-01',cents=1000)
        self.entry(date='2026-01-01',cents=3000)
        self.entry(date='2026-01-02',cents=1000,type='refund')
        self.entry(date='2026-01-03',cents=888,track='unknown',budget_category='unknown')
        self.entry(date='2026-10-01',cents=999999)
        self.entry(date='2026-11-01',cents=999999)
        hub.apply_review({'source_digest':'b'*64,'months':[], 'available_months':['2025-11','2025-12','2026-01'],
                          'partial_months':['2025-11','2025-12','2026-01'],'mapping_by_id':{},
                          'reviewed_at':'2026-10-23T12:00:00+08:00','notes':'缺工资等流水，只有部分记录'})
        suggestion=hub.budget_suggest('2026-10')
        self.assertEqual(suggestion['status'],'mapping_incomplete')
        self.assertEqual(suggestion['reference_months'],['2025-12','2026-01'])
        self.assertEqual(suggestion['unmapped_count'],1)
        coffee=next(row for row in suggestion['suggestions'] if row['category']=='餐饮')
        self.assertEqual(coffee['provisional_cents'],1500)
        self.assertIsNone(coffee['cents'])
        self.assertEqual(suggestion['complete_basis_months'],[])
        self.assertFalse(suggestion['saved'])
        self.assertEqual(finance.state()['budgets'],[])


if __name__ == '__main__':
    unittest.main()
