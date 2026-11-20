"""Monthly reminders use synthetic reports and a private temporary database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wellness


class MonthlyAssessmentReminderTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=patch.object(wellness,'BASE',Path(self.temp.name)/'health')
        self.base.start();self.addCleanup(self.base.stop)

    def test_due_only_on_first_even_without_a_previous_report(self):
        self.assertIsNone(wellness.due_reminder('2026-10-31'))
        reminder=wellness.due_reminder('2026-11-01')
        self.assertEqual(reminder['id'],'body-assessment:2026-11')
        self.assertEqual(reminder['due'],'2026-11-01')
        self.assertIsNone(wellness.due_reminder('2026-11-02'))
        self.assertIsNone(wellness.due_reminder('2026-11-30'))

    def test_queries_do_not_mark_delivery_and_success_is_deduplicated(self):
        reminder=wellness.due_reminder('2026-11-01')
        self.assertEqual(wellness.due_reminder('2026-11-01'),reminder)
        wellness.mark_reminded(reminder['id'])
        with wellness.connection() as con:first=con.execute('SELECT id,sent FROM reminder').fetchall()
        wellness.mark_reminded(reminder['id'])
        self.assertIsNone(wellness.due_reminder('2026-11-01'))
        with wellness.connection() as con:self.assertEqual(con.execute('SELECT id,sent FROM reminder').fetchall(),first)
        self.assertEqual(wellness.due_reminder('2026-12-01')['id'],'body-assessment:2026-12')

    def test_next_date_handles_month_end_leap_year_and_year_rollover(self):
        cases={'2026-10-19':'2026-11-01','2026-10-31':'2026-11-01','2026-12-31':'2027-01-01','2028-02-29':'2028-03-01','2026-02-28':'2026-03-01','2026-11-01':'2026-11-01'}
        for day,expected in cases.items():
            with self.subTest(day=day):self.assertEqual(wellness.next_assessment(day),expected)
        wellness.mark_reminded('body-assessment:2026-11')
        self.assertEqual(wellness.next_assessment('2026-11-01'),'2026-12-01')

    def test_legacy_receipts_are_preserved_without_suppressing_monthly_reminders(self):
        legacy=wellness.save_report({'kind':'体测','date':'2025-01-15','title':'合成报告','weight':70})
        wellness.mark_reminded(legacy['id'])
        self.assertEqual(wellness.due_reminder('2026-11-01')['id'],'body-assessment:2026-11')
        wellness.mark_reminded('body-assessment:2026-11')
        with wellness.connection() as con:self.assertEqual({r['id'] for r in con.execute('SELECT id FROM reminder')},{legacy['id'],'body-assessment:2026-11'})

    def test_new_report_or_duplicate_import_cannot_reset_monthly_receipt(self):
        payload={'kind':'体测','date':'2025-09-02','title':'合成体测','weight':70}
        wellness.mark_reminded('body-assessment:2026-11')
        row=wellness.save_report(payload)
        self.assertEqual(wellness.save_report(payload)['id'],row['id'])
        wellness.save_report({**payload,'date':'2025-09-03','weight':71})
        self.assertIsNone(wellness.due_reminder('2026-11-01'))
        self.assertEqual(wellness.next_assessment('2026-11-01'),'2026-12-01')

    def test_parallel_acknowledgements_keep_one_receipt(self):
        wellness.connection().close()
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(wellness.mark_reminded,['body-assessment:2026-11']*8))
        with wellness.connection() as con:self.assertEqual(con.execute('SELECT count(*) FROM reminder').fetchone()[0],1)

    def test_state_uses_a_single_day_and_does_not_move_report_dates(self):
        report=wellness.save_report({'kind':'体测','date':'2025-09-02','title':'合成体测','weight':70})
        with patch.object(wellness,'training',return_value={}),patch.object(wellness,'datetime') as clock:
            clock.now.side_effect=[datetime(2026,11,1,23,59,59),datetime(2026,11,2)]
            state=wellness.state()
        self.assertEqual(state['nextAssessment'],'2026-11-01')
        self.assertEqual(state['nextAssessment'],state['reminder']['due'])
        self.assertEqual(state['reports'][0]['date'],report['date'])


if __name__=='__main__':unittest.main()
