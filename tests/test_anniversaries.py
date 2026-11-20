import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import date

spec = importlib.util.spec_from_file_location('anniversaries', Path(__file__).resolve().parents[1] / 'scripts/anniversaries.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def listing(self):
        return list(self.rows.values())

    def save(self, row):
        self.rows[row['id']] = dict(row)


class AnnualDates(unittest.TestCase):
    def test_rollover_and_reminder_cross_year(self):
        event = dict(id='test', title='Example', original='2000-01-05', source='test',
                     calendar='solar', month=1, day=5, kind='anniversary', origin_year=2000)
        result = a.preview(event, date(2026, 12, 25))
        self.assertEqual(result['reminder_at'], '2026-12-29T09:00:00+08:00')
        self.assertEqual(result['years'], 27)
        self.assertEqual(result['next_date'], '2028-01-05')
        self.assertEqual(a.preview(event, date(2027, 1, 6))['date'], '2028-01-05')

    def test_lunar_birthday_uses_actual_age(self):
        event = dict(id='test', title='Example', calendar='lunar', kind='birthday',
                     birth_solar='2000-11-07', month=9, day=27)
        result = a.describe(event, date(2027, 10, 26), 2027)
        self.assertEqual(result['years'], 26)

    def test_lunar_december_in_next_gregorian_year_and_ignore_leap_month(self):
        event = dict(calendar='lunar', month=12, day=8)
        rows = {2026: [(date(2026, 1, 26), 2025, 12, 8, False)],
                2027: [(date(2027, 1, 15), 2026, 12, 8, False),
                       (date(2027, 2, 15), 2026, 12, 8, True)],
                2028: [(date(2028, 1, 4), 2027, 12, 8, False)]}
        with patch.object(a, 'table', side_effect=lambda y: rows[y]):
            self.assertEqual(a.occurrences(event, date(2026, 11, 16)),
                             [(date(2027, 1, 15), 2026), (date(2028, 1, 4), 2027)])

    def test_repeat_sync_preserves_user_edits_and_deleted_rows(self):
        event = dict(id='test', title='Example', original='2000-06-04', source='test',
                     calendar='solar', month=6, day=4, kind='anniversary', origin_year=2000)
        store = MemoryStore()
        first = a.sync([event], date(2026, 11, 16), store)
        self.assertEqual(len(first['created']), 2)
        key = first['created'][0]
        store.rows[key].update(title='User edit', deleted=1)
        before = dict(store.rows[key])
        again = a.sync([event], date(2026, 11, 16), store)
        self.assertEqual(again['created'], [])
        self.assertEqual(store.rows[key], before)

    def test_bad_calendar_fails_before_any_write(self):
        store = MemoryStore()
        event = dict(calendar='lunar', month=1, day=3)
        with patch.object(a, 'table', side_effect=ValueError('invalid')):
            with self.assertRaises(ValueError):
                a.sync([event], date(2026, 11, 16), store)
        self.assertEqual(store.rows, {})
        with self.assertRaises(ValueError):
            a.parse_table('<html>error</html>', 2027)


if __name__ == '__main__':
    unittest.main()
