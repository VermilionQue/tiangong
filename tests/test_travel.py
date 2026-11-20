"""Synthetic places and temporary SQLite files; never touches personal visits."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import travel


class TravelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        catalog = self.base / 'places.json'
        catalog.write_text(json.dumps({'version': 1, 'places': [
            {'id': 'CN-TEST', 'scope': 'domestic', 'name': '测试省'},
            {'id': 'XY', 'scope': 'overseas', 'name': '测试国'}]}))
        for name, value in [('DB', self.base/'private/travel.sqlite3'), ('CATALOG', catalog)]:
            p = patch.object(travel, name, value)
            p.start();self.addCleanup(p.stop)

    def payload(self, **updates):
        return {'scope': 'domestic', 'place_id': 'CN-TEST',
                'visited_on': '2025-06-15', 'city': '合成城市', 'note': '测试小记', **updates}

    def history(self):
        with travel.connection() as con:
            return [json.loads(row['data']) for row in con.execute('SELECT * FROM history ORDER BY revision')]

    def test_initial_state_does_not_invent_visits(self):
        self.assertEqual(travel.state(), {'visits': [], 'catalog_version': 1,
                                         'counts': {'domestic': 0, 'overseas': 0}})

    def test_create_and_exact_retries_keep_one_row_and_authoritative_name(self):
        data = self.payload(place_name='客户端不能改地名')
        row = travel.save(data)
        self.assertEqual(row['id'], 'domestic:CN-TEST')
        self.assertEqual(row['place_name'], '测试省')
        self.assertEqual(travel.save(data), row)
        self.assertEqual(travel.save(row), row)
        self.assertEqual(travel.listing(), [row]);self.assertEqual(self.history(), [])
        self.assertEqual(travel.DB.stat().st_mode & 0o777, 0o600)
        self.assertEqual(travel.DB.parent.stat().st_mode & 0o777, 0o700)

    def test_optional_fields_and_older_partial_updates_preserve_details(self):
        minimal = travel.save({'scope': 'overseas', 'place_id': 'XY'})
        self.assertEqual((minimal['visited_on'], minimal['city'], minimal['note']), ('', '', ''))
        original = travel.save(self.payload())
        changed = travel.save({'scope': 'domestic', 'place_id': 'CN-TEST',
                               'revision': original['revision'], 'note': '更新小记'})
        self.assertEqual(changed['visited_on'], original['visited_on'])
        self.assertEqual(changed['city'], original['city'])
        self.assertEqual(changed['created_at'], original['created_at'])
        self.assertEqual(changed['revision'], 2)
        cleared = travel.save({**changed, 'visited_on': '', 'city': '', 'note': ''})
        self.assertEqual((cleared['visited_on'], cleared['city'], cleared['note']), ('', '', ''))
        self.assertEqual(self.history(), [original, changed])

    def test_validation_rejects_invalid_dates_types_ids_and_unbounded_fields(self):
        tomorrow = (datetime.now(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat()
        changes = [{'scope': 'unknown'}, {'scope': []}, {'place_id': 'missing'},
                   {'place_id': '../XY'}, {'place_id': []}, {'scope': 'overseas'},
                   {'id': 'overseas:XY'}, {'visited_on': '2025-02-29'},
                   {'visited_on': '20250615'}, {'visited_on': '2025-6-15'},
                   *[{'visited_on': value} for value in ('0000', '0000-01', '2025-00',
                       '2025-13', '2025-02-30', '2025-04-31', '25', '2025-',
                       '2025-6', '2025-06-', '2025-06-1', '2025年', ' 2025', '2025 ')],
                   {'visited_on': '２０２５-０６-１５'}, {'visited_on': tomorrow},
                   {'visited_on': None}, {'city': None}, {'city': 'x'*81},
                   {'note': []}, {'note': 'x'*2001}, {'note': 'bad\x00value'},
                   {'action': 'erase'}, {'revision': True}, {'revision': '1'},
                   {'revision': -1}, {'revision': 1.0}]
        for data in [None, [], *[self.payload(**change) for change in changes]]:
            with self.subTest(data=data), self.assertRaises(ValueError):travel.save(data)
        self.assertEqual(travel.listing(), [])

    def test_today_leap_date_and_maximum_lengths_are_valid(self):
        row = travel.save(self.payload(visited_on='2024-02-29', city='城'*80, note='记'*2000))
        changed = travel.save({**row, 'visited_on': datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()})
        self.assertEqual(changed['revision'], 2)

    def test_year_and_month_creates_keep_exact_precision(self):
        year = travel.save(self.payload(visited_on='2024'))
        month = travel.save(self.payload(scope='overseas', place_id='XY', visited_on='2025-06'))
        self.assertEqual(year['visited_on'], '2024')
        self.assertEqual(month['visited_on'], '2025-06')
        self.assertEqual({v['visited_on'] for v in travel.state()['visits']}, {'2024', '2025-06'})
        self.assertEqual(travel.save(self.payload(visited_on='2024')), year)
        self.assertEqual(self.history(), [])

    def test_full_date_can_change_precision_without_losing_history_or_other_fields(self):
        current = travel.save(self.payload())
        history = []
        for value in ('2025', '2025-06', '2024-02-29', ''):
            with self.subTest(visited_on=value):
                before = current
                current = travel.save({**before, 'visited_on': value})
                history.append(before)
                self.assertEqual(current['visited_on'], value)
                self.assertEqual(current['city'], before['city'])
                self.assertEqual(travel.save({**before, 'visited_on': value}), current)
                self.assertEqual(travel.listing(), [current])
        self.assertEqual(self.history(), history)

    def test_partial_date_is_preserved_by_old_update_delete_and_restore_calls(self):
        original = travel.save(self.payload(visited_on='2025-06'))
        current = travel.save({'scope': original['scope'], 'place_id': original['place_id'],
                               'revision': original['revision'], 'note': '仅修改小记'})
        deleted = travel.save({**current, 'action': 'delete'})
        restored = travel.save({'scope': original['scope'], 'place_id': original['place_id'],
                                'revision': deleted['revision'], 'action': 'restore'})
        self.assertEqual([v['visited_on'] for v in [current, deleted, restored]], ['2025-06'] * 3)
        self.assertEqual(self.history(), [original, current, deleted])

    def test_future_comparison_uses_only_supplied_precision(self):
        fixed_now = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch.object(travel, 'datetime') as clock:
            clock.now.return_value = fixed_now
            for value in ('0001', '2026', '2026-10', '2026-10-20'):
                with self.subTest(valid=value):
                    self.assertEqual(travel.validate_visit_date(value), value)
            for value in ('2027', '2026-11', '2026-10-21'):
                with self.subTest(future=value), self.assertRaisesRegex(ValueError, '不能在未来'):
                    travel.save(self.payload(visited_on=value))
        self.assertEqual(travel.listing(), [])

    def test_soft_delete_restore_and_retry_preserve_all_details_and_history(self):
        original = travel.save(self.payload())
        deleted = travel.save({**original, 'action': 'delete', 'note': '不应覆盖'})
        self.assertEqual(deleted['note'], original['note'])
        self.assertEqual(travel.save({**original, 'action': 'delete'}), deleted)
        self.assertEqual(travel.state()['counts']['domestic'], 0)
        self.assertEqual(travel.state()['visits'], [deleted])
        with self.assertRaises(travel.ConflictError):travel.save(original)
        restored = travel.save({**deleted, 'action': 'restore'})
        self.assertEqual((restored['deleted'], restored['revision']), (0, 3))
        self.assertEqual(restored['created_at'], original['created_at'])
        self.assertEqual(travel.save({**deleted, 'action': 'restore'}), restored)
        self.assertEqual(travel.state()['counts']['domestic'], 1)
        self.assertEqual(self.history(), [original, deleted])

    def test_restore_can_save_edited_details_without_changing_place(self):
        original = travel.save(self.payload())
        deleted = travel.save({**original, 'action': 'delete'})
        restored = travel.save({**deleted, 'action': 'restore', 'city': '补充城市'})
        self.assertEqual(restored['city'], '补充城市')
        self.assertEqual(restored['visited_on'], original['visited_on'])
        self.assertEqual(restored['deleted'], 0)

    def test_missing_record_actions_and_stale_edits_do_not_create_or_overwrite(self):
        for updates in ({'action': 'delete'}, {'action': 'restore'}, {'revision': 2}):
            with self.assertRaises(travel.ConflictError):travel.save(self.payload(**updates))
        old = travel.save(self.payload())
        current = travel.save({**old, 'note': '新版本'})
        self.assertEqual(travel.save({**old, 'note': '新版本'}), current)
        for updates in ({'note': '旧页面覆盖'}, {'action': 'delete'}, {'revision': None, 'note': '无版本覆盖'}):
            with self.assertRaises(travel.ConflictError):travel.save({**old, **updates})
        self.assertEqual(travel.listing(), [current]);self.assertEqual(self.history(), [old])

    def test_parallel_first_saves_are_idempotent(self):
        barrier = threading.Barrier(2)
        def save(_):
            barrier.wait();return travel.save(self.payload())
        with ThreadPoolExecutor(max_workers=2) as pool:rows = list(pool.map(save, range(2)))
        self.assertEqual(rows[0], rows[1]);self.assertEqual(len(travel.listing()), 1)
        self.assertEqual(self.history(), [])

    def test_concurrent_updates_have_one_winner_and_keep_previous_snapshot(self):
        old = travel.save(self.payload());barrier = threading.Barrier(2)
        def save(note):
            barrier.wait()
            try:return travel.save({**old, 'note': note})
            except travel.ConflictError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes = list(pool.map(save, ['版本甲', '版本乙']))
        winner = next(row for row in outcomes if row is not None)
        self.assertEqual(sum(row is None for row in outcomes), 1)
        self.assertEqual(travel.listing(), [winner]);self.assertEqual(self.history(), [old])

    def test_catalogue_failure_cannot_create_visits(self):
        for contents in ['invalid-json', '[]', '{"places":[]}',
                         json.dumps({'places': [{'id': 'XY', 'scope': 'overseas', 'name': '甲'}, {'id': 'XY', 'scope': 'overseas', 'name': '乙'}]})]:
            travel.CATALOG.write_text(contents)
            with self.assertRaises(ValueError):travel.save(self.payload())
        travel.CATALOG.unlink()
        with self.assertRaises(ValueError):travel.state()
        self.assertFalse(travel.DB.exists())

    def test_photos_round_trip_retry_partial_edit_remove_and_restore(self):
        photos = ['a'*64+'.jpg', 'b'*64+'.webp']
        original = travel.save(self.payload(photos=photos))
        self.assertEqual(original['photos'], photos)
        self.assertEqual(travel.save(self.payload(photos=photos)), original)
        self.assertEqual(travel.state()['visits'], [original])
        edited = travel.save({'scope': 'domestic', 'place_id': 'CN-TEST',
                              'revision': 1, 'note': '旧客户端只更新文字'})
        self.assertEqual(edited['photos'], photos)
        removed = travel.save({**edited, 'photos': [photos[1]]})
        self.assertEqual(removed['photos'], [photos[1]])
        self.assertEqual(travel.save({**edited, 'photos': [photos[1]]}), removed)
        deleted = travel.save({**removed, 'action': 'delete', 'photos': []})
        self.assertEqual(deleted['photos'], [photos[1]])
        restored = travel.save({'scope': 'domestic', 'place_id': 'CN-TEST',
                                'revision': deleted['revision'], 'action': 'restore'})
        self.assertEqual(restored['photos'], [photos[1]])
        cleared = travel.save({**restored, 'photos': []})
        self.assertEqual(cleared['photos'], [])
        self.assertEqual(self.history(), [original, edited, removed, deleted, restored])
        self.assertEqual(travel.listing(), [cleared])

    def test_photos_validate_count_ids_types_and_duplicates_without_partial_write(self):
        valid = [f'{n:064x}.png' for n in range(8)]
        original = travel.save(self.payload(photos=valid))
        self.assertEqual(original['photos'], valid)
        invalid = [None, {}, '', ['a'*64+'.png']*2,
                   [f'{n:064x}.png' for n in range(9)], [None], [1],
                   *[[value] for value in ('../x.jpg', '/private/image.png',
                     'https://example.org/img.jpg', 'A'*64+'.jpg', 'a'*64+'.svg',
                     'a'*64+'.jpeg', 'a'*64+'.gif', 'a'*63+'.jpg',
                     'a'*64+'.jpg/../private', '%2e%2e%2fimage.png', '..\\secret.png')]]
        for photos in invalid:
            with self.subTest(photos=photos), self.assertRaises(ValueError):
                travel.save({**original, 'photos': photos, 'note': '不得部分保存'})
        self.assertEqual(travel.listing(), [original])
        self.assertEqual(self.history(), [])

    def test_photos_and_text_from_two_devices_have_one_winner(self):
        original = travel.save(self.payload(photos=['a'*64+'.jpg']))
        updated = travel.save({**original, 'photos': ['b'*64+'.png']})
        for payload in ({**original, 'note': '另一个设备仍带旧图'},
                        {'scope': original['scope'], 'place_id': original['place_id'],
                         'revision': original['revision'], 'note': '旧接口的另一处文字修改'}):
            with self.assertRaises(travel.ConflictError):
                travel.save(payload)
        self.assertEqual(travel.listing(), [updated])
        self.assertEqual(self.history(), [original])

    def test_existing_database_migration_preserves_old_rows_and_history(self):
        travel.DB.parent.mkdir(parents=True)
        old = {'id': 'domestic:CN-TEST', 'scope': 'domestic', 'place_id': 'CN-TEST',
               'place_name': '测试省', 'visited_on': '2025', 'city': '已有城市',
               'note': '原始小记', 'deleted': 0, 'created_at': '2025-01-01T00:00:00+08:00',
               'updated_at': '2025-01-01T00:00:00+08:00', 'revision': 1}
        with sqlite3.connect(travel.DB) as con:
            con.execute('''CREATE TABLE visits(id TEXT PRIMARY KEY,scope TEXT NOT NULL,
                place_id TEXT NOT NULL,place_name TEXT NOT NULL,visited_on TEXT NOT NULL,
                city TEXT NOT NULL,note TEXT NOT NULL,deleted INTEGER NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL,
                UNIQUE(scope,place_id))''')
            con.execute('INSERT INTO visits VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(old.values()))
        migrated = travel.listing()[0]
        self.assertEqual(migrated, {**old, 'photos': []})
        updated = travel.save({**migrated, 'photos': ['a'*64+'.webp']})
        self.assertEqual(updated['created_at'], old['created_at'])
        self.assertEqual(updated['note'], old['note'])
        self.assertEqual(self.history(), [migrated])
        self.assertEqual(travel.listing(), [updated])

    def test_photo_update_failure_rolls_back_history_and_record_and_can_retry(self):
        original = travel.save(self.payload(photos=['a'*64+'.jpg']))
        payload = {**original, 'photos': ['b'*64+'.png']}
        with travel.connection() as con:
            con.execute("CREATE TRIGGER fail_visit BEFORE UPDATE ON visits BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            travel.save(payload)
        self.assertEqual(travel.listing(), [original])
        self.assertEqual(self.history(), [])
        with travel.connection() as con:
            con.execute('DROP TRIGGER fail_visit')
        updated = travel.save(payload)
        self.assertEqual(updated['photos'], ['b'*64+'.png'])
        self.assertEqual(self.history(), [original])


if __name__ == '__main__':unittest.main()
