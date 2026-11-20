"""Belonging data is synthetic and always isolated from the personal database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import treasures


class TreasuresTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, value in [('DB', Path(self.temp.name) / 'private/treasures.sqlite3'),
                              ('today_cn', lambda: date(2026, 10, 21))]:
            patcher = patch.object(treasures, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sequence = 0

    def request(self, **values):
        self.sequence += 1
        return {'request_id': f'request-{self.sequence:04d}', **values}

    def create(self, **values):
        return treasures.save(self.request(name='合成相机', **values))

    def mutate(self, row, **values):
        return self.request(id=row['id'], revision=row['revision'], **values)

    def rows(self, table):
        self.assertIn(table, ('history', 'use_events', 'requests'))
        with treasures.connection() as con:
            return [dict(row) for row in con.execute(f'SELECT * FROM {table} ORDER BY rowid')]

    def assert_counts(self, records, history, events, requests):
        self.assertEqual(len(treasures.state()['records']), records)
        self.assertEqual(len(self.rows('history')), history)
        self.assertEqual(len(self.rows('use_events')), events)
        self.assertEqual(len(self.rows('requests')), requests)

    def test_empty_private_storage_contains_no_invented_records(self):
        self.assertEqual(treasures.state(), {'records': []})
        self.assertEqual(treasures.DB.stat().st_mode & 0o777, 0o600)
        self.assertEqual(treasures.DB.parent.stat().st_mode & 0o777, 0o700)

    def test_create_retry_partial_edit_and_prior_evidence_are_preserved(self):
        payload = self.request(name='合成相机', brand='合成品牌', category='数码',
                               purchased_on='2026-10-01', purchase_cents=123456,
                               photo='a'*64+'.jpg', note='原始说明', uses=5,
                               warranty_until='2028-10-01', icon='camera')
        first = treasures.save(payload)
        self.assertEqual(treasures.save(payload), first)
        self.assertEqual(first['revision'], 1)
        self.assert_counts(1, 0, 0, 1)
        second = treasures.save(self.mutate(first, note='修订说明',
                                            createdAt='fake', source='fake', metrics={'net_cents': 0}))
        self.assertEqual(second['revision'], 2)
        self.assertEqual(second['createdAt'], first['createdAt'])
        for key in treasures.EDITABLE:
            if key != 'note':
                self.assertEqual(second[key], first[key])
        self.assertNotIn('source', second)
        self.assertEqual(second['metrics']['net_cents'], 123456)
        historical = json.loads(self.rows('history')[0]['data'])
        self.assertEqual(historical, {k: v for k, v in first.items() if k != 'metrics'})
        # Replaying a successful create must not resurrect its older values.
        self.assertEqual(treasures.save(payload), first)
        self.assertEqual(treasures.state()['records'], [second])
        unchanged = treasures.save(self.mutate(second, note='修订说明'))
        self.assertEqual(unchanged, second)
        self.assert_counts(1, 1, 0, 3)

    def test_request_ids_bind_payload_and_endpoint_while_revisions_guard_updates(self):
        payload = self.request(name='合成物品', id='synthetic-1')
        first = treasures.save(payload)
        with self.assertRaises(treasures.ConflictError):
            treasures.save({**payload, 'name': '不同内容'})
        with self.assertRaises(treasures.ConflictError):
            treasures.use(payload)
        second = treasures.save(self.mutate(first, note='新版'))
        for revision in (0, 1, 3):
            with self.subTest(revision=revision), self.assertRaises(treasures.ConflictError):
                treasures.save(self.request(id=first['id'], revision=revision, note='不能覆盖'))
        with self.assertRaises(treasures.ConflictError):
            treasures.save(self.request(id='missing', revision=1, name='缺失'))
        self.assertEqual(treasures.state()['records'], [second])
        self.assert_counts(1, 1, 0, 2)

    def test_unknown_and_zero_amounts_and_dates_remain_distinct(self):
        unknown = self.create()
        self.assertEqual(unknown['metrics'], {'owned_days': None, 'net_cents': None,
                         'daily_cost_cents': None, 'per_use_cost_cents': None})
        zero = self.create(purchased_on='2026-10-21', purchase_cents=0, uses=2)
        self.assertEqual(zero['metrics'], {'owned_days': 1, 'net_cents': 0,
                         'daily_cost_cents': '0', 'per_use_cost_cents': '0'})
        missing_date = self.create(purchase_cents=10000, uses=3)
        self.assertEqual(missing_date['metrics']['owned_days'], None)
        self.assertEqual(missing_date['metrics']['daily_cost_cents'], None)
        self.assertEqual(missing_date['metrics']['per_use_cost_cents'], '3333.333333')
        never_used = self.create(purchased_on='2026-10-20', purchase_cents=100)
        self.assertEqual(never_used['metrics']['daily_cost_cents'], '50')
        self.assertIsNone(never_used['metrics']['per_use_cost_cents'])

    def test_calendar_days_include_purchase_day_and_sold_metrics_freeze(self):
        active = self.create(purchased_on='2024-02-28', purchase_cents=30000, uses=3)
        self.assertEqual(treasures.metrics(active, '2024-03-01')['owned_days'], 3)
        self.assertEqual(treasures.metrics(active, date(2024, 3, 1))['daily_cost_cents'], '10000')
        sold = treasures.save(self.mutate(active, status='sold', sold_on='2024-03-01', sale_cents=33000))
        expected = {'owned_days': 3, 'net_cents': -3000,
                    'daily_cost_cents': '-1000', 'per_use_cost_cents': '-1000'}
        self.assertEqual(sold['metrics'], expected)
        self.assertEqual(treasures.metrics(sold, '2030-12-31'), expected)
        unknown_sale = treasures.save(self.mutate(sold, sale_cents=None))
        self.assertEqual(unknown_sale['metrics']['owned_days'], 3)
        for key in ('net_cents', 'daily_cost_cents', 'per_use_cost_cents'):
            self.assertIsNone(unknown_sale['metrics'][key])
        zero_sale = treasures.save(self.mutate(unknown_sale, sale_cents=0))
        self.assertEqual(zero_sale['metrics']['net_cents'], 30000)
        with self.assertRaises(ValueError):
            treasures.metrics(active, datetime(2026, 10, 21, 1, 0))

    def test_invalid_fields_never_create_or_modify_records(self):
        cases = [
            {'name': ''}, {'name': ' '}, {'name': 'x'*121}, {'name': None},
            {'brand': 'x'*101}, {'category': 'x'*81}, {'note': 'x'*3001}, {'note': 'a\0b'},
            *[{'purchase_cents': v} for v in (True, False, 1.0, '10', -1, treasures.MAX_CENTS+1)],
            *[{'sale_cents': v} for v in (True, 1.0, '10', -1)],
            *[{'uses': v} for v in (True, 1.5, '2', -1, treasures.MAX_USES+1)],
            *[{'purchased_on': v} for v in ('2026-10-22', '2025-02-29', '2026-1-01',
                                          '２０２６-01-01', None, '../data')],
            {'purchased_on': '2026-10-20', 'warranty_until': '2026-10-19'},
            {'warranty_until': '2026-02-30'}, {'status': 'missing'},
            {'status': 'sold'}, {'status': 'sold', 'sold_on': '2026-10-22'},
            {'purchased_on': '2026-10-20', 'status': 'sold', 'sold_on': '2026-10-19'},
            {'sold_on': '2026-10-20'}, {'sale_cents': 0}, {'icon': 'unsafe'}, {'icon': {}},
            *[{'photo': v} for v in ('../file.jpg', '/private/file.jpg', 'https://example.org/a.jpg',
                                    'a'*64+'.svg', 'a'*64+'.gif', 'a'*64+'.jpeg',
                                    'a'*64+'.jpg/../secret', 'A'*64+'.jpg',
                                    '%2e%2e%2ffile.jpg', '..\\private.png', 'a'*63+'.jpg', None)],
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                treasures.save(self.request(**{'name': '合成物品', **changes}))
        self.assert_counts(0, 0, 0, 0)
        first = self.create(purchase_cents=100)
        with self.assertRaises(ValueError):
            treasures.save(self.mutate(first, purchase_cents=1.25, note='不得部分写入'))
        self.assertEqual(treasures.state()['records'], [first])
        self.assert_counts(1, 0, 0, 1)

    def test_invalid_envelopes_and_readonly_field_injection(self):
        for data in (None, [], {}, {'request_id': 'short'}, {'request_id': '../bad/key'},
                     {'request_id': True}, {'request_id': 'a'*101}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                treasures.save(data)
        for key, values in [('id', ('../id', '', 1)), ('revision', (-1, True, 1.0, '0', None)),
                            ('action', ('use', 'purge', None))]:
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    treasures.save(self.request(name='合成物品', **{key: value}))
        first = self.create(deletedAt='fake', createdAt='fake', updatedAt='fake')
        self.assertEqual(first['deletedAt'], '')
        self.assertNotEqual(first['createdAt'], 'fake')
        self.assertNotEqual(first['updatedAt'], 'fake')
        self.assert_counts(1, 0, 0, 1)

    def test_use_retries_once_across_midnight_and_keeps_event_dates(self):
        first = self.create(purchased_on='2026-10-01', uses=4)
        payload = self.mutate(first)
        second = treasures.use(payload)
        self.assertEqual((second['uses'], second['revision']), (5, 2))
        with patch.object(treasures, 'today_cn', return_value=date(2026, 10, 22)):
            retry = treasures.use(payload)
            self.assertEqual((retry['id'], retry['uses'], retry['revision']), (first['id'], 5, 2))
        event = self.rows('use_events')[0]
        self.assertEqual((event['used_on'], event['uses_before'], event['uses_after']), ('2026-10-21', 4, 5))
        third = treasures.use(self.mutate(second, date='2026-10-15'))
        self.assertEqual(third['uses'], 6)
        self.assertEqual(self.rows('use_events')[1]['used_on'], '2026-10-15')
        revised = treasures.save(self.mutate(third, uses=10))
        self.assertEqual(revised['uses'], 10)
        self.assertEqual(len(self.rows('use_events')), 2)
        self.assertEqual([entry['action'] for entry in self.rows('history')], ['use', 'use', 'save'])

    def test_use_rejects_invalid_dates_sold_trash_and_overflow(self):
        first = self.create(purchased_on='2026-10-01')
        for value in ('2026-09-30', '2026-10-22', '2026-02-30', '', None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                treasures.use(self.mutate(first, date=value))
        sold = treasures.save(self.mutate(first, status='sold', sold_on='2026-10-20'))
        with self.assertRaises(treasures.ConflictError):
            treasures.use(self.mutate(sold))
        with self.assertRaises(ValueError):
            treasures.save(self.mutate(sold, status='active'))
        active = treasures.save(self.mutate(sold, status='active', sold_on='', sale_cents=None))
        deleted = treasures.save(self.mutate(active, action='delete'))
        with self.assertRaises(treasures.ConflictError):
            treasures.use(self.mutate(deleted))
        full = self.create(uses=treasures.MAX_USES)
        with self.assertRaises(ValueError):
            treasures.use(self.mutate(full))
        self.assertEqual(self.rows('use_events'), [])

    def test_trash_restore_and_replayed_requests_preserve_every_original_field(self):
        first = self.create(purchased_on='2026-10-01', purchase_cents=1000,
                            status='sold', sold_on='2026-10-10', sale_cents=500,
                            photo='b'*64+'.png', note='原始资料')
        payload = self.mutate(first, action='delete', name='忽略此字段', photo='../bad')
        deleted = treasures.save(payload)
        self.assertTrue(deleted['deletedAt'])
        self.assertEqual(deleted['revision'], 2)
        for key in treasures.EDITABLE:
            self.assertEqual(deleted[key], first[key])
        self.assertEqual(treasures.save(payload), deleted)
        self.assertEqual(treasures.state()['records'], [deleted])
        with self.assertRaises(treasures.ConflictError):
            treasures.save(self.mutate(deleted, note='不允许在回收站中编辑'))
        restored = treasures.save(self.mutate(deleted, action='restore'))
        self.assertFalse(restored['deletedAt'])
        self.assertEqual(restored['revision'], 3)
        for key in treasures.EDITABLE:
            self.assertEqual(restored[key], first[key])
        # Old delete response can be replayed but does not undo the restore.
        treasures.save(payload)
        self.assertEqual(treasures.state()['records'], [restored])
        self.assert_counts(1, 2, 0, 3)

    def test_concurrent_same_request_creates_one_event_and_one_revision(self):
        first = self.create(uses=3)
        payload = self.mutate(first)
        barrier = threading.Barrier(2)
        def apply():
            barrier.wait()
            return treasures.use(payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['uses'], 4)
        self.assert_counts(1, 1, 1, 2)

    def test_concurrent_different_requests_never_overwrite_same_revision(self):
        first = self.create(uses=3)
        payloads = [self.mutate(first), self.mutate(first, note='另一处编辑')]
        barrier = threading.Barrier(2)
        def apply(index):
            barrier.wait()
            try:
                return ('ok', (treasures.use if index == 0 else treasures.save)(payloads[index]))
            except treasures.ConflictError:
                return ('conflict', None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, range(2)))
        self.assertEqual(sorted(result[0] for result in results), ['conflict', 'ok'])
        final = next(row for status, row in results if status == 'ok')
        self.assertEqual(treasures.state()['records'], [final])
        self.assert_counts(1, 1, int(final['uses'] == 4), 2)

    def test_failure_after_event_or_history_write_rolls_back_every_change(self):
        first = self.create(uses=3)
        payload = self.mutate(first)
        # The failure occurs after use_events/history and row writes, before
        # recording the retry token; all four tables must roll back together.
        with treasures.connection() as con:
            con.execute("CREATE TRIGGER fail_request BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            treasures.use(payload)
        self.assertEqual(treasures.state()['records'], [first])
        self.assert_counts(1, 0, 0, 1)
        with treasures.connection() as con:
            con.execute('DROP TRIGGER fail_request')
        retried = treasures.use(payload)
        self.assertEqual(retried['uses'], 4)
        self.assert_counts(1, 1, 1, 2)


if __name__ == '__main__':
    unittest.main()
