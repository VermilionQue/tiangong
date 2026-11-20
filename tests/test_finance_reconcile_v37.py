"""Reviewed version upgrades keep one ledger and fail atomically on conflicts."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finance
import reconcile_finance as reconcile


class ReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = patch.object(finance, 'DB', Path(self.temp.name)/'ledger.sqlite3')
        self.override.start(); self.addCleanup(self.override.stop)
        self.row = dict(id='old:1', date='2026-01-02', name='测试消费', cents=1200,
                        signed_cents=-1200, direction='out', type='expense', category='生活',
                        evidence={'original_id':'original:1','original_amount':1200})
        finance.import_ledger({'metadata':{'sha256':'a'*64},'records':[self.row]})
        self.old = finance.state()['records'][0]
        self.delta = {'metadata':{'sha256':'b'*64,'filename':'合成修订.xlsx'},
                      'updates':[{'id':self.old['id'],'source_hash':reconcile.digest(self.old),
                                  'changes':{'track':'daily','note':'来源核实'},
                                  'evidence':{'sheet':'总表','row':2}}],
                      'additions':[{**self.row,'id':'new:2','date':'2026-10-23'}]}

    def test_update_append_preserve_history_evidence_and_exact_retry(self):
        payload = copy.deepcopy(self.delta)
        result = reconcile.apply_delta(payload)
        self.assertEqual((result['updated'],result['added']), (1,1))
        self.assertEqual(payload, self.delta)
        self.assertTrue(reconcile.apply_delta(payload)['already_applied'])
        self.assertEqual(len(finance.state()['records']), 2)
        with finance.connection() as c:
            self.assertEqual(json.loads(c.execute('select data from history').fetchone()[0]), self.old)
            self.assertEqual(json.loads(c.execute("select data from evidence where id='old:1'").fetchone()[0]), self.row['evidence'])
            self.assertEqual(c.execute('select count(*) from reconciliation_evidence').fetchone()[0], 2)

    def test_stale_row_rejects_entire_batch_without_append(self):
        finance.save({**self.old,'note':'另一台设备刚刚修改'})
        before = finance.state()
        with self.assertRaises(finance.ConflictError): reconcile.apply_delta(self.delta)
        self.assertEqual(finance.state(), before)

    def test_invalid_new_amount_does_not_partially_update(self):
        before = finance.state()
        self.delta['additions'][0]['signed_cents'] = 1200
        with self.assertRaises(ValueError): reconcile.apply_delta(self.delta)
        self.assertEqual(finance.state(), before)

    def test_same_source_different_review_cannot_reapply(self):
        reconcile.apply_delta(self.delta)
        self.delta['updates'][0]['changes']['note'] = '意外不同的核对'
        with self.assertRaises(finance.ConflictError): reconcile.apply_delta(self.delta)
        self.assertEqual(len(finance.state()['records']), 2)

    def test_no_removal_of_absent_history_and_no_duplicate_id(self):
        self.delta['updates'] = []
        reconcile.apply_delta(self.delta)
        self.assertIn(self.old, finance.state()['records'])
        self.delta['metadata']['sha256'] = 'c'*64
        with self.assertRaises(finance.ConflictError): reconcile.apply_delta(self.delta)
        self.assertEqual(len(finance.state()['records']), 2)

    def test_protected_fields_cannot_erase_history(self):
        for changes in ({'id':'overwrite'}, {'revision':42}, {'source':'another'}, {'voidedAt':'now'}):
            self.delta['updates'][0]['changes'] = changes
            with self.assertRaises(ValueError): reconcile.apply_delta(self.delta)
        self.assertEqual(finance.state()['records'], [self.old])

    def test_failed_stale_review_can_be_rechecked_without_overwriting_its_archive(self):
        base=Path(self.temp.name)/'private'
        source=Path(self.temp.name)/'synthetic.xlsx'
        source.write_bytes(b'only synthetic workbook bytes, not a real workbook')
        source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        self.delta['metadata']['sha256']=source_hash
        stale=copy.deepcopy(self.delta)
        finance.save({**self.old,'note':'并发更新，需重新核对'})
        prepared=Path(self.temp.name)/'prepared.json'
        prepared.write_text(json.dumps(stale),encoding='utf-8')
        with patch.object(finance,'BASE',base):
            with self.assertRaises(finance.ConflictError):reconcile.import_reviewed(source,prepared)
            first_archive=base/'finance/sources'/f'{source_hash}.{reconcile.digest(stale)}.review.json'
            self.assertEqual(json.loads(first_archive.read_text()),stale)
            self.assertEqual(len(finance.state()['records']),1)
            self.delta['updates'][0]['source_hash']=reconcile.digest(finance.state()['records'][0])
            prepared.write_text(json.dumps(self.delta),encoding='utf-8')
            result=reconcile.import_reviewed(source,prepared)
            self.assertEqual(result['added'],1)
            second_archive=base/'finance/sources'/f'{source_hash}.{reconcile.digest(self.delta)}.review.json'
            self.assertEqual(json.loads(second_archive.read_text()),self.delta)
            self.assertNotEqual(first_archive,second_archive)
            self.assertEqual(json.loads(first_archive.read_text()),stale)
            self.assertTrue(reconcile.import_reviewed(source,prepared)['already_applied'])
            self.assertEqual(len(list((base/'finance/sources').glob('*.xlsx'))),1)
            self.assertEqual(second_archive.stat().st_mode & 0o777,0o600)

    def test_additions_cannot_inject_hidden_or_managed_runtime_fields(self):
        before=finance.state()
        for field,value in [('managed_by','debt_payment'),('voidedAt','now'),('payment_id','payment'),
                            ('revision',23),('updatedAt','now'),('source','manual')]:
            delta=copy.deepcopy(self.delta)
            delta['additions'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):reconcile.apply_delta(delta)
            self.assertEqual(finance.state(),before)

    def test_late_database_failure_rolls_back_history_entries_and_evidence(self):
        with finance.connection() as con:
            con.execute("CREATE TRIGGER fail_new_evidence BEFORE INSERT ON evidence BEGIN SELECT RAISE(ABORT, 'synthetic disk write failure'); END")
        before=finance.state()
        with self.assertRaisesRegex(Exception,'synthetic disk write failure'):reconcile.apply_delta(self.delta)
        self.assertEqual(finance.state(),before)
        with finance.connection() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM history').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM reconciliation_evidence').fetchone()[0],0)
            con.execute('DROP TRIGGER fail_new_evidence')
        self.assertEqual(reconcile.apply_delta(self.delta)['added'],1)

    def test_pending_count_reports_review_rows_instead_of_claiming_zero(self):
        self.delta['additions'][0].update(type='review',confirmed=False)
        result=reconcile.apply_delta(self.delta)
        self.assertEqual(result['pending'],1)


if __name__ == '__main__': unittest.main()
