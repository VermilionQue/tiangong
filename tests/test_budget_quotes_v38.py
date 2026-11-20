"""Grouped budgets and repayment drafts use temporary synthetic records only."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import finance
import finance_hub as hub


class BudgetQuoteTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='budget-quote-v38-');self.addCleanup(tmp.cleanup)
        for target,key,value in [(finance,'DB',Path(tmp.name)/'ledger.sqlite3'),(hub,'today',lambda:'2026-10-24')]:
            switch=patch.object(target,key,value);switch.start();self.addCleanup(switch.stop)

    def save(self,action,**fields):
        return hub.manage(dict(action=action,request_id=str(uuid.uuid4()),**fields))['entity']

    def entry(self,**fields):
        data=dict(date='2026-10-16',name='合成银行扣款',cents=453733,type='financing',direction='out',
                  category='贷款还款',track='daily',budget_category='unknown')
        data.update(fields);return finance.save(data)

    def debt(self,kind='mortgage',**fields):
        data=dict(id='loan',name='合成房贷',kind=kind,principal_cents=95631231,
                  annual_rate_bp=320,as_of='2026-10-23',confirmed=True,snapshot_basis='end_of_day',maturity='2052-08-10')
        data.update(fields);return self.save('debt_save',**data)

    def quote(self,month='2026-10'):
        return hub.state(month)['debts'][0]['payment_quote']

    def payment(self,**fields):
        data=dict(debt_id='loan',date='2026-10-24',payment_month='2026-10',principal_cents=0,
                  interest_cents=100,fees_cents=0,settles_month=False)
        data.update(fields);return self.save('debt_payment',**data)

    def test_three_groups_inherit_exact_legacy_amounts_and_keep_fine_categories(self):
        for category,cents in [('买菜做饭',199170),('下馆子',360058),('咖啡饮料',6589),('日用百货',50820),('医疗买药',777)]:
            self.save('budget_save',month='2026-10',category=category,cents=cents)
        self.entry(type='expense',cents=1000,budget_category='买菜做饭')
        self.entry(type='expense',cents=2000,budget_category='下馆子')
        self.entry(type='refund',direction='in',cents=500,budget_category='下馆子')
        self.entry(type='expense',cents=999,budget_category='医疗买药')
        rows=hub.state()['budgets']
        self.assertEqual([r['category'] for r in rows],['餐饮','日用','娱乐'])
        self.assertEqual([r['cents'] for r in rows],[565817,50820,60000])
        self.assertEqual(rows[0]['consumed_cents'],2500)
        self.assertEqual(rows[0]['categories'],['买菜做饭','下馆子','咖啡饮料'])
        self.assertTrue(rows[0]['inherited_from_legacy'])
        self.assertEqual(len(finance.state()['budgets']),5)
        self.assertEqual({r['budget_category'] for r in finance.state()['records']},{'买菜做饭','下馆子','医疗买药'})

    def test_new_group_edit_preserves_legacy_and_rejects_stale_merge(self):
        old=self.save('budget_save',month='2026-10',category='买菜做饭',cents=10000)
        inherited=hub.state()['budgets'][0]
        self.save('budget_save',month='2026-10',category='买菜做饭',cents=20000,revision=old['revision'])
        with self.assertRaises(finance.ConflictError):
            self.save('budget_save',month='2026-10',category='餐饮',cents=30000,legacy_revisions=inherited['legacy_revisions'])
        fresh=hub.state()['budgets'][0]
        group=self.save('budget_save',month='2026-10',category='餐饮',cents=30000,legacy_revisions=fresh['legacy_revisions'])
        self.assertEqual(group['id'],'2026-10:group:dining')
        self.assertEqual(hub.state()['budgets'][0]['cents'],30000)
        self.assertFalse(hub.state()['budgets'][0]['inherited_from_legacy'])
        with self.assertRaises(finance.ConflictError):
            self.save('budget_save',month='2026-10',category='买菜做饭',cents=999,revision=2)
        self.assertEqual(next(r for r in finance.state()['budgets'] if r['category']=='买菜做饭')['cents'],20000)
        self.save('budget_save',month='2026-10',category='餐饮',cents=40000,revision=group['revision'])
        self.assertEqual(hub.state()['budgets'][0]['cents'],40000)

    def test_group_budget_suggestions_sum_net_history_without_rewriting_records(self):
        self.entry(date='2026-09-10',type='expense',cents=1200,budget_category='买菜做饭')
        self.entry(date='2026-09-11',type='expense',cents=1800,budget_category='下馆子')
        self.entry(date='2026-09-12',type='refund',direction='in',cents=500,budget_category='下馆子')
        before=finance.state()
        hub.apply_review({'source_digest':'a'*64,'months':['2026-09'],'mapping_by_id':{}})
        suggestions=hub.budget_suggest('2026-10')['suggestions']
        self.assertEqual([s['category'] for s in suggestions],['餐饮','日用','娱乐'])
        self.assertEqual(suggestions[0]['cents'],2500)
        self.assertEqual(finance.state(),before)

    def test_consumer_interest_uses_current_principal_rate_and_subtracts_paid_interest(self):
        self.debt('interest_only',principal_cents=14200000,annual_rate_bp=300)
        quote=self.quote()
        self.assertEqual((quote['principal_cents'],quote['interest_cents'],quote['total_cents']),(0,35500,35500))
        self.assertTrue(quote['estimated'])
        self.payment(interest_cents=10000)
        self.assertEqual(self.quote()['total_cents'],25500)
        self.payment(principal_cents=200000,interest_cents=0)
        self.assertEqual(self.quote()['total_cents'],25000)
        self.assertEqual(self.quote('2026-11')['total_cents'],35000)
        self.debt('interest_only',id='other',principal_cents=20000000,annual_rate_bp=450)
        other=next(d for d in hub.state()['debts'] if d['id']=='other')
        self.assertEqual(other['payment_quote']['total_cents'],75000)

    def test_unknown_rate_is_never_zero_and_due_day_does_not_mark_paid(self):
        self.debt('interest_only',principal_cents=14200000,annual_rate_bp=None,due_day=18)
        self.assertEqual(self.quote()['status'],'unknown')
        self.assertIsNone(self.quote()['interest_cents'])
        self.assertEqual(hub.state()['debts'][0]['monthly_status'],'unrecorded')
        self.assertEqual(hub.state()['payments'],[])

    def test_mortgage_known_total_has_estimated_split_and_partial_draft_is_not_duplicated(self):
        self.debt(monthly_payment_cents=453733)
        first=self.quote()
        self.assertEqual(first['total_cents'],453733)
        self.assertEqual(first['interest_cents'],255017)
        self.assertEqual(first['principal_cents'],198716)
        self.assertTrue(first['estimated'])
        self.assertEqual(first['source'],'known_monthly_payment')
        self.payment(principal_cents=100000,interest_cents=100000)
        second=self.quote()
        self.assertEqual(second['total_cents'],253733)
        self.assertEqual(second['principal_cents'],98716)
        self.assertEqual(second['interest_cents'],155017)
        self.payment(principal_cents=98716,interest_cents=155017,settles_month=True)
        self.assertEqual(self.quote()['status'],'paid')
        self.assertEqual(self.quote()['total_cents'],0)
        self.assertEqual(len(hub.state()['payments']),2)

    def test_mortgage_without_total_uses_known_terms_or_maturity_and_labels_estimate(self):
        self.debt(monthly_payment_cents=None)
        q=self.quote('2026-11')
        self.assertEqual(q['remaining_terms_estimate'],310)
        self.assertEqual(q['status'],'ready')
        self.assertTrue(q['estimated'])
        self.assertIn('估算',q['label'])
        self.assertGreater(q['total_cents'],q['interest_cents'])
        self.assertEqual(hub.state()['payments'],[])
        self.assertEqual(finance.state()['records'],[])

    def test_verified_existing_payment_links_without_ledger_or_principal_duplicate(self):
        entry=self.entry()
        debt=self.debt(monthly_payment_cents=453733,verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])
        state=hub.state();q=state['debts'][0]['payment_quote']
        self.assertEqual(q['status'],'paid')
        self.assertEqual(q['source'],'verified_ledger')
        self.assertEqual(q['recorded_total_cents'],453733)
        self.assertEqual(q['paid_dates'],['2026-10-16'])
        self.assertEqual(state['debts'][0]['month_principal_cents'],0)
        self.assertEqual(state['debts'][0]['remaining_principal_cents'],95631231)
        self.assertEqual(state['payments'],[])
        self.assertEqual(len(finance.state()['records']),1)
        self.assertEqual(self.quote('2026-11')['total_cents'],453733)
        with self.assertRaises(finance.ConflictError):self.payment(principal_cents=198716,interest_cents=255017,settles_month=True)
        self.assertEqual(hub.history('debt',debt['id'])['history'],[])

    def test_payment_reference_void_or_edit_disables_paid_state_and_does_not_refresh_on_metadata_edit(self):
        entry=self.entry()
        debt=self.debt(monthly_payment_cents=453733,verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])
        finance.save({**entry,'note':'核对后原流水已修订'})
        self.save('debt_save',id=debt['id'],revision=debt['revision'],name='只改名称')
        self.assertNotEqual(self.quote()['status'],'paid')
        with finance.connection() as con:
            stored=json.loads(con.execute('SELECT data FROM entries WHERE id=?',(entry['id'],)).fetchone()[0])
            stored['voidedAt']='2026-10-24T11:00:00+08:00'
            con.execute('UPDATE entries SET data=? WHERE id=?',(json.dumps(stored),entry['id']))
        self.assertNotEqual(self.quote()['status'],'paid')
        self.assertEqual(hub.state()['debts'][0]['linked_payment_cents'],0)

    def test_bad_reference_month_inflow_duplicate_and_cross_debt_are_rejected(self):
        entry=self.entry()
        for refs in ([{'month':'2026-09','entry_ids':[entry['id']]}],
                     [{'month':'2026-10','entry_ids':[entry['id'],entry['id']]}]):
            with self.assertRaises(ValueError):self.debt(verified_payment_refs=refs)
        self.debt(verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])
        with self.assertRaises(finance.ConflictError):
            self.debt(id='other',verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])
        income=self.entry(type='income',direction='in')
        with self.assertRaises(ValueError):
            self.debt(id='income',verified_payment_refs=[{'month':'2026-10','entry_ids':[income['id']]}])

    def test_existing_manual_monthly_payment_cannot_also_link_imported_duplicate(self):
        debt=self.debt()
        self.payment(principal_cents=100,interest_cents=100,settles_month=True)
        entry=self.entry()
        with self.assertRaises(finance.ConflictError):
            self.save('debt_save',id=debt['id'],revision=debt['revision'],verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])

    def test_partial_unsplit_link_does_not_guess_remaining_components(self):
        entry=self.entry(cents=100000)
        self.debt(monthly_payment_cents=453733,verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']],'settles_month':False}])
        q=self.quote()
        self.assertEqual(q['status'],'unknown')
        self.assertIsNone(q['principal_cents'])
        self.assertIsNone(q['interest_cents'])
        self.assertEqual(hub.state()['debts'][0]['monthly_status'],'recorded')

    def test_changing_loan_kind_requires_explicitly_removing_monthly_references(self):
        entry=self.entry()
        debt=self.debt(verified_payment_refs=[{'month':'2026-10','entry_ids':[entry['id']]}])
        with self.assertRaises(ValueError):
            self.save('debt_save',id=debt['id'],revision=debt['revision'],kind='personal')
        self.assertEqual(hub.state()['debts'][0]['monthly_status'],'paid')
        updated=self.save('debt_save',id=debt['id'],revision=debt['revision'],kind='personal',verified_payment_refs=[])
        self.assertEqual(updated['kind'],'personal')
        self.assertEqual(hub.state()['debts'][0]['linked_payments'],[])

    def test_review_subcategory_keeps_money_type_and_original_revision_history(self):
        entry=self.entry(track='qqsg',subcategory='其他')
        payload={'source_digest':'b'*64,'months':[], 'mapping_by_id':{
            entry['id']:{'source_hash':hub.entry_hash(entry),'subcategory':'道具'}}}
        result=hub.apply_review(payload)
        self.assertEqual(result['applied'],[entry['id']])
        updated=finance.state()['records'][0]
        for field in ('cents','signed_cents','type','track','confirmed'):
            self.assertEqual(updated[field],entry[field])
        self.assertEqual(updated['subcategory'],'道具')
        with finance.connection() as con:
            self.assertEqual(json.loads(con.execute('SELECT data FROM history WHERE id=?',(entry['id'],)).fetchone()[0]),entry)
        self.assertTrue(hub.apply_review(payload)['already_applied'])
        payload['source_digest']='c'*64
        payload['mapping_by_id'][entry['id']]['subcategory']='x'*101
        with self.assertRaises(ValueError):hub.apply_review(payload)
        self.assertEqual(finance.state()['records'][0],updated)

    def test_strict_review_one_source_conflict_keeps_entire_batch_unchanged(self):
        first=self.entry(track='qqsg',subcategory='其他')
        second=self.entry(track='qqsg',subcategory='其他',cents=700)
        before=finance.state()
        payload={'source_digest':'d'*64,'months':[], 'mapping_by_id':{
            first['id']:{'source_hash':hub.entry_hash(first),'subcategory':'道具'},
            second['id']:{'source_hash':'0'*64,'subcategory':'其他游戏支出'}}}
        with self.assertRaises(finance.ConflictError):hub.apply_review(payload,strict=True)
        self.assertEqual(finance.state(),before)
        with finance.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM history').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM hub_reviews').fetchone()[0],0)
        payload['mapping_by_id'][second['id']]['source_hash']=hub.entry_hash(second)
        self.assertEqual(len(hub.apply_review(payload,strict=True)['applied']),2)
        self.assertTrue(hub.apply_review(payload,strict=True)['already_applied'])


if __name__=='__main__':unittest.main()
