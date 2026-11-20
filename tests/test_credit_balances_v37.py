"""Synthetic signed card balances and day-end debt snapshots; no private data."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import finance
import finance_hub as hub


class CardBalanceTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='card-v37-')
        self.addCleanup(tmp.cleanup)
        for obj,field,value in [(finance,'DB',Path(tmp.name)/'finance.sqlite3'),(hub,'today',lambda:'2026-10-24')]:
            context=patch.object(obj,field,value);context.start();self.addCleanup(context.stop)

    def save(self,action,**fields):
        return hub.manage(dict(action=action,request_id=str(uuid.uuid4()),**fields))['entity']

    def card(self,balance=2929,**fields):
        data=dict(id='card',kind='credit_card',name='合成卡',card_balance_cents=balance,
                  as_of='2026-10-23',confirmed=True,snapshot_basis='end_of_day')
        data.update(fields)
        return self.save('debt_save',**data)

    def payment(self,**fields):
        data=dict(debt_id='card',date='2026-10-24',principal_cents=100,interest_cents=0,fees_cents=0,settles_month=False)
        data.update(fields)
        return self.save('debt_payment',**data)

    def view(self):return hub.state()['debts'][0]

    def test_positive_balance_is_not_debt_cash_or_another_asset(self):
        card=self.card()
        row=self.view()
        self.assertEqual(card['principal_cents'],0)
        self.assertEqual(row['current_card_balance_cents'],2929)
        self.assertEqual(row['credit_balance_cents'],2929)
        self.assertEqual(row['remaining_principal_cents'],0)
        self.assertEqual(hub.state()['accounts'],[])
        self.assertIsNone(hub.state()['summary']['confirmed_cash_cents'])
        self.save('settings_save',debt_inventory_confirmed=True)
        self.save('wish_save',name='合成心愿',price_cents=100)
        self.assertEqual(hub.state()['summary']['remaining_debt_cents'],0)
        self.assertEqual(hub.state()['summary']['confirmed_cash_subtotal_cents'],0)
        self.assertFalse(hub.state()['wishes'][0]['unlocked'])
        self.save('debt_save',id='loan',name='合成借款',kind='interest_only',principal_cents=10000,
                  as_of='2026-10-23',confirmed=True,snapshot_basis='end_of_day')
        self.assertEqual(hub.state()['summary']['remaining_debt_cents'],10000)

    def test_negative_balance_is_exact_debt_and_ambiguous_dual_input_is_rejected(self):
        card=self.card(-87065)
        self.assertEqual(card['principal_cents'],87065)
        self.assertEqual(self.view()['current_card_balance_cents'],-87065)
        self.assertEqual(self.view()['credit_balance_cents'],0)
        for principal,balance in [(2929,2929),(0,-87065)]:
            with self.assertRaises(ValueError):
                self.save('debt_save',id='card',revision=card['revision'],principal_cents=principal,card_balance_cents=balance)
        self.assertEqual(self.view()['revision'],1)

    def test_recheck_preserves_history_and_legacy_principal_input(self):
        card=self.card(2929,as_of='2026-09-30')
        card=self.save('debt_save',id='card',revision=card['revision'],card_balance_cents=-10000,as_of='2026-10-23')
        previous=hub.state('2026-09')['debts'][0]
        self.assertEqual(previous['current_card_balance_cents'],2929)
        self.assertEqual(previous['snapshot_card_balance_cents'],2929)
        self.assertEqual(self.view()['current_card_balance_cents'],-10000)
        self.assertEqual(len(hub.history('debt','card')['history']),1)
        self.save('debt_save',id='card',revision=card['revision'],principal_cents=5000)
        self.assertEqual(self.view()['current_card_balance_cents'],-5000)

    def test_credit_offsets_future_statement_without_fabricating_payment(self):
        self.card(2929,as_of='2026-09-30')
        bill=self.save('card_statement_save',debt_id='card',consumption_month='2026-10',amount_cents=10000)
        row=self.view()
        self.assertEqual(row['current_card_balance_cents'],-7071)
        self.assertEqual(row['remaining_principal_cents'],7071)
        summary=hub.state()['card_statements'][0]
        self.assertEqual(summary['credit_applied_cents'],2929)
        self.assertEqual(summary['paid_cents'],0)
        self.assertEqual(summary['remaining_cents'],7071)
        self.assertEqual(hub.state()['payments'],[])
        self.assertEqual(finance.state()['records'],[])
        with self.assertRaises(ValueError):
            self.payment(statement_id=bill['id'],payment_month='2026-11',principal_cents=10000)
        self.payment(statement_id=bill['id'],payment_month='2026-11',principal_cents=7071)
        self.assertEqual(self.view()['current_card_balance_cents'],0)
        self.assertEqual(hub.state()['card_statements'][0]['status'],'paid')
        self.assertEqual(hub.state()['summary']['monthly_expense_cents'],0)

    def test_credit_allocation_survives_new_zero_snapshot_and_void_is_blocked(self):
        card=self.card(2929,as_of='2026-09-30')
        bill=self.save('card_statement_save',debt_id='card',consumption_month='2026-10',amount_cents=10000)
        pay=self.payment(statement_id=bill['id'],payment_month='2026-11',principal_cents=7071)
        card=self.save('debt_save',id='card',revision=card['revision'],card_balance_cents=0,as_of='2026-10-24',confirmed=True)
        self.assertEqual(self.view()['current_card_balance_cents'],0)
        self.assertEqual(hub.state()['card_statements'][0]['credit_applied_cents'],2929)
        self.assertEqual(hub.state()['card_statements'][0]['remaining_cents'],0)
        with self.assertRaises(ValueError):self.save('payment_void',id=pay['id'],revision=pay['revision'])
        self.save('debt_save',id='card',revision=card['revision'],name='仅改名字')
        self.assertEqual(self.view()['current_card_balance_cents'],0)

    def test_historical_late_bill_remains_unknown_with_credit_snapshot(self):
        card=self.card(2929)
        self.save('card_statement_save',debt_id='card',consumption_month='2026-10',amount_cents=10000)
        self.assertIsNone(self.view()['current_card_balance_cents'])
        self.save('debt_save',id='card',revision=card['revision'],name='仅改名称')
        self.assertIsNone(self.view()['remaining_principal_cents'])

    def test_day_end_snapshot_excludes_later_entered_same_day_payment(self):
        loan=self.save('debt_save',id='loan',name='合成消费贷',kind='interest_only',principal_cents=10000,
                       as_of='2026-10-23',snapshot_basis='end_of_day',confirmed=True)
        self.payment(debt_id='loan',date='2026-10-23',principal_cents=3000)
        self.assertEqual(self.view()['remaining_principal_cents'],10000)
        self.payment(debt_id='loan',date='2026-10-24',principal_cents=2000)
        self.assertEqual(self.view()['remaining_principal_cents'],8000)
        self.assertEqual(self.view()['total_principal_paid_cents'],5000)
        self.assertEqual(hub.state('2026-09')['debts'][0]['remaining_principal_cents'],None)

    def test_legacy_same_day_snapshot_behavior_is_unchanged(self):
        self.save('debt_save',id='loan',name='合成旧借款',kind='interest_only',principal_cents=10000,
                  as_of='2026-10-23',confirmed=True)
        self.payment(debt_id='loan',date='2026-10-23',principal_cents=3000)
        self.assertEqual(self.view()['remaining_principal_cents'],7000)

    def test_v36_snapshot_metadata_edit_does_not_reconfirm_old_bill(self):
        import json
        card=self.save('debt_save',id='card',name='旧格式卡',kind='credit_card',principal_cents=10000,
                       as_of='2026-09-30',confirmed=True)
        # Simulate the exact V36 stored schema, before signed balances existed.
        for key in ('card_balance_cents','snapshot_basis'):
            card.pop(key,None)
            for snapshot in card['snapshots']:snapshot.pop(key,None)
        with hub.connection() as con:
            con.execute('UPDATE hub_entities SET data=? WHERE kind=? AND id=?',(json.dumps(card),'debt','card'))
        self.save('card_statement_save',debt_id='card',consumption_month='2026-09',amount_cents=10000)
        self.assertIsNone(self.view()['current_card_balance_cents'])
        self.save('debt_save',id='card',revision=card['revision'],name='只改名称')
        self.assertIsNone(self.view()['current_card_balance_cents'])

    def test_day_end_zero_debt_accepts_backfilled_same_day_actual_payment(self):
        self.save('debt_save',id='loan',name='当日日终已还完',kind='personal',principal_cents=0,
                  as_of='2026-10-23',snapshot_basis='end_of_day',confirmed=True)
        self.payment(debt_id='loan',date='2026-10-23',principal_cents=10000)
        self.assertEqual(self.view()['remaining_principal_cents'],0)
        self.assertEqual(self.view()['total_principal_paid_cents'],10000)
        self.assertEqual(finance.state()['records'][0]['cents'],10000)

    def test_known_cash_subtotal_excludes_credit_and_does_not_unlock_incomplete_cash(self):
        self.card(2929)
        self.save('account_save',id='known',name='已核现金',kind='cash',balance_cents=53120,as_of='2026-10-23',confirmed=True)
        self.save('account_save',id='unknown',name='待核现金',kind='bank',balance_cents=None,as_of=None,confirmed=False)
        self.save('settings_save',debt_inventory_confirmed=True)
        self.save('wish_save',name='合成心愿',price_cents=100)
        summary=hub.state()['summary']
        self.assertEqual(summary['confirmed_cash_subtotal_cents'],53120)
        self.assertIsNone(summary['confirmed_cash_cents'])
        self.assertFalse(hub.state()['wishes'][0]['unlocked'])

    def test_mortgage_original_facts_do_not_infer_current_monthly_payment(self):
        self.save('debt_save',id='loan',name='合成房贷',kind='mortgage',principal_cents=80234567,
                  original_principal_cents=95000000,borrowed_on='2023-02-15',annual_rate_bp=345,
                  maturity='2053-02-15',as_of='2026-10-23',snapshot_basis='end_of_day',confirmed=True)
        row=self.view()
        self.assertEqual(row['original_principal_cents'],95000000)
        self.assertEqual(row['borrowed_on'],'2023-02-15')
        self.assertIsNone(row['monthly_payment_cents'])
        self.assertIsNone(row['estimated_payment_cents'])


if __name__=='__main__': unittest.main()
