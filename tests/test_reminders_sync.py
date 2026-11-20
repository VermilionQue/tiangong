import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reminders_sync as sync
import todos


class FakeNative:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.failure = None
        self.lose_create_reply = False
        self.before_apply = None

    def add(self, key, **fields):
        self.rows[key] = dict(id=key, title='苹果事项', note='', due='', due_time='',
                              done=0, priority='normal', revision='1', sync_id=None,
                              recurrence=False)
        self.rows[key].update(fields)
        if self.rows[key]['done'] and 'completed_at' not in fields:
            self.rows[key]['completed_at'] = '2026-10-19T13:00:00+08:00'
        return self.rows[key]

    def edit(self, key, **fields):
        self.rows[key].update(fields)
        if 'done' in fields and 'completed_at' not in fields:
            self.rows[key]['completed_at'] = '2026-10-19T13:00:00+08:00' if fields['done'] else None
        self.rows[key]['revision'] = str(int(self.rows[key]['revision']) + 1)

    def __call__(self, request, config):
        self.calls.append(copy.deepcopy(request))
        if self.failure:
            raise sync.NativeError(self.failure)
        if request['action'] == 'snapshot':
            return dict(ok=True, complete=True, calendar_id=config['calendar_id'],
                        reminders=copy.deepcopy(list(self.rows.values())))
        if request['action'] == 'create':
            found = next((r for r in self.rows.values() if r.get('sync_id') == request['sync_id']), None)
            if not found:
                key = 'native-' + request['sync_id']
                found = self.add(key)
                found.update(request['fields'], sync_id=request['sync_id'])
            if self.lose_create_reply:
                self.lose_create_reply = False
                raise sync.NativeError('lost_reply')
            return dict(ok=True, reminder=copy.deepcopy(found))
        if self.before_apply:
            callback, self.before_apply = self.before_apply, None
            callback(request)
        found = self.rows[request['id']]
        if request['expected_revision'] != found['revision']:
            raise sync.NativeError('conflict')
        self.edit(found['id'], **request['fields'])
        return dict(ok=True, reminder=copy.deepcopy(found))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_dir, self.old_db = sync.DIR, todos.DB
        sync.DIR, todos.DB = self.root / 'sync', self.root / 'todos.sqlite3'
        self.helper = self.root / 'helper'
        self.helper.write_text('#!/bin/sh\nexit 1\n')
        self.helper.chmod(0o700)
        self.fake = FakeNative()
        self.mock = patch.object(sync, 'native', self.fake)
        self.mock.start()
        sync.configure('list-1', str(self.helper))

    def tearDown(self):
        self.mock.stop()
        sync.DIR, todos.DB = self.old_dir, self.old_db
        self.temp.cleanup()

    def local(self, **fields):
        values = dict(title='本机事项', note='', due='', due_time='')
        values.update(fields)
        return todos.save(values)

    def item(self, key):
        return next(r for r in todos.listing() if r['id'] == key)

    def linked(self, **fields):
        row = self.local(**fields)
        self.assertEqual(sync.run_once()['state'], 'ok')
        return self.item(row['id']), 'native-' + row['id']

    def test_disabled_does_not_call_native_or_read_todos(self):
        (sync.DIR / 'config.json').unlink()
        with patch.object(todos, 'listing', side_effect=AssertionError('must not read')):
            self.assertEqual(sync.run_once()['state'], 'disabled')
        self.assertEqual(self.fake.calls, [])

    def test_export_import_complete_and_idempotent_no_revision_churn(self):
        local = self.local(done=1)
        self.fake.add('apple-1', title='苹果原生事项')
        self.assertEqual(sync.run_once()['state'], 'ok')
        rows = todos.listing()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.fake.rows), 2)
        self.assertEqual(self.fake.rows['native-' + local['id']]['done'], 1)
        for _ in range(3):
            self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(todos.listing(), rows)
        self.assertEqual(len(self.fake.rows), 2)
        self.assertEqual(len([c for c in self.fake.calls if c['action'] == 'create']), 1)

    def test_disjoint_edits_merge_without_overwriting_note_or_time(self):
        row, remote = self.linked(due='2026-10-21', due_time='09:30', note='原备注')
        todos.save({**row, 'title': '本机新标题'})
        self.fake.edit(remote, note='苹果新备注', due_time='11:00')
        self.assertEqual(sync.run_once()['state'], 'ok')
        result = self.item(row['id'])
        self.assertEqual((result['title'], result['note'], result['due_time']), ('本机新标题', '苹果新备注', '11:00'))
        calls = [c for c in self.fake.calls if c['action'] == 'apply']
        self.assertEqual(calls[-1]['fields'], {'title': '本机新标题'})

    def test_conflict_has_previews_and_resolves_only_conflicting_fields(self):
        row, remote = self.linked()
        todos.save({**row, 'title': '本机修改', 'note': '本机独有备注'})
        self.fake.edit(remote, title='苹果修改', done=1)
        result = sync.run_once()
        issue = result['issues'][0]
        self.assertEqual(issue['kind'], 'conflict')
        self.assertEqual(issue['fields'], ['title'])
        self.assertEqual(issue['local']['title'], '本机修改')
        self.assertEqual(issue['apple']['title'], '苹果修改')
        sync.resolve(row['id'], 'apple', issue['token'])
        merged = self.item(row['id'])
        self.assertEqual((merged['title'], merged['note'], merged['done']), ('苹果修改', '本机独有备注', 1))
        self.assertEqual(self.fake.rows[remote]['note'], '本机独有备注')

    def test_stale_conflict_token_cannot_overwrite_new_remote(self):
        row, remote = self.linked()
        todos.save({**row, 'title': '本机修改'})
        self.fake.edit(remote, title='苹果修改')
        issue = sync.run_once()['issues'][0]
        self.fake.edit(remote, title='苹果又修改')
        with self.assertRaisesRegex(ValueError, '刷新'):
            sync.resolve(row['id'], 'local', issue['token'])
        self.assertEqual(self.fake.rows[remote]['title'], '苹果又修改')
        self.assertNotEqual(sync.status()['issues'][0]['token'], issue['token'])

    def test_stale_conflict_token_cannot_overwrite_new_local(self):
        row, remote = self.linked()
        updated = todos.save({**row, 'title': '本机修改'})
        self.fake.edit(remote, title='苹果修改')
        issue = sync.run_once()['issues'][0]
        todos.save({**updated, 'note': '刚刚添加的备注'})
        with self.assertRaises(ValueError):
            sync.resolve(row['id'], 'apple', issue['token'])
        self.assertEqual(self.item(row['id'])['note'], '刚刚添加的备注')

    def test_create_lost_reply_and_user_edit_dedupes_on_retry(self):
        row = self.local()
        self.fake.lose_create_reply = True
        self.assertEqual(sync.run_once()['state'], 'attention')
        self.assertEqual(len(self.fake.rows), 1)
        self.fake.edit('native-' + row['id'], note='创建回执丢失后苹果加的备注')
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(len(self.fake.rows), 1)
        self.assertEqual(self.item(row['id'])['note'], '创建回执丢失后苹果加的备注')
        self.assertEqual(len([c for c in self.fake.calls if c['action'] == 'create']), 1)

    def test_import_intent_recovers_if_process_dies_before_local_save(self):
        native = self.fake.add('apple-1')
        canonical = sync._normalized(native)
        with sync._db() as db:
            sync._link(db, 'import-intent', 'apple-1', canonical, canonical, 'importing')
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(len(todos.listing()), 1)
        self.assertEqual(todos.listing()[0]['id'], 'import-intent')

    def test_deleted_local_pauses_and_restore_resumes_without_native_delete(self):
        row, remote = self.linked()
        trashed = todos.save({**row, 'deleted': 1})
        result = sync.run_once()
        self.assertEqual(result['issues'][0]['kind'], 'local_deleted')
        self.assertIn(remote, self.fake.rows)
        todos.save({**trashed, 'deleted': 0})
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(len(self.fake.rows), 1)

    def test_missing_native_or_failed_snapshot_never_deletes_or_recreates(self):
        row, remote = self.linked()
        self.fake.failure = 'not_authorized'
        self.assertEqual(sync.run_once()['state'], 'waiting_permission')
        self.assertEqual(self.item(row['id'])['deleted'], 0)
        self.fake.failure = None
        del self.fake.rows[remote]
        result = sync.run_once()
        self.assertEqual(result['issues'][0]['kind'], 'missing_apple')
        self.assertEqual(self.fake.rows, {})
        self.assertEqual(self.item(row['id'])['deleted'], 0)

    def test_recurrence_not_imported_or_overwritten(self):
        self.fake.add('repeat', recurrence=True)
        result = sync.run_once()
        self.assertEqual(todos.listing(), [])
        self.assertEqual(result['issues'][0]['kind'], 'unsupported')
        row, remote = self.local(), None
        sync.run_once()
        remote = 'native-' + row['id']
        self.fake.edit(remote, recurrence=True)
        todos.save({**self.item(row['id']), 'title': '禁止覆盖重复事项'})
        sync.run_once()
        self.assertEqual(self.fake.rows[remote]['title'], '本机事项')

    def test_date_only_midnight_and_clear_roundtrip_without_title_parsing(self):
        row, remote = self.linked(title='上午10:30 原话保留', due='2026-10-21')
        self.assertEqual(self.fake.rows[remote]['due_time'], '')
        self.fake.edit(remote, due_time='00:00')
        sync.run_once()
        self.assertEqual(self.item(row['id'])['due_time'], '00:00')
        todos.save({**self.item(row['id']), 'due': ''})
        sync.run_once()
        self.assertEqual((self.fake.rows[remote]['due'], self.fake.rows[remote]['due_time']), ('', ''))
        self.assertEqual(self.item(row['id'])['title'], '上午10:30 原话保留')

    def test_date_and_time_are_one_conflict_group(self):
        row, remote = self.linked(due='2026-10-21', due_time='09:00')
        todos.save({**row, 'due': '2026-10-22'})
        self.fake.edit(remote, due_time='10:00')
        self.assertEqual(sync.run_once()['issues'][0]['fields'], ['due', 'due_time'])

    def test_native_compare_and_swap_conflict_never_overwrites(self):
        row, remote = self.linked()
        todos.save({**row, 'title': '本机修改'})
        self.fake.before_apply = lambda request: self.fake.edit(remote, title='苹果同时修改')
        self.assertEqual(sync.run_once()['state'], 'attention')
        self.assertEqual(self.fake.rows[remote]['title'], '苹果同时修改')
        self.assertEqual(sync.run_once()['issues'][0]['kind'], 'conflict')

    def test_local_race_after_remote_write_does_not_lose_new_local_revision(self):
        row, remote = self.linked()
        local = todos.save({**row, 'title': '本机修改'})
        self.fake.edit(remote, note='苹果备注')
        self.fake.before_apply = lambda request: todos.save({**local, 'note': '本机同时新增备注'})
        self.assertEqual(sync.run_once()['state'], 'attention')
        self.assertEqual(self.item(row['id'])['note'], '本机同时新增备注')
        self.assertEqual(sync.run_once()['issues'][0]['kind'], 'conflict')

    def test_flock_prevents_second_run_and_private_permissions(self):
        self.local()
        with sync._exclusive() as acquired:
            self.assertTrue(acquired)
            sync.run_once()
            self.assertEqual(self.fake.calls, [])
        sync.run_once()
        self.assertEqual(os.stat(sync.DIR).st_mode & 0o777, 0o700)
        for name in ('config.json', 'state.sqlite3', 'status.json', 'sync.lock'):
            self.assertEqual(os.stat(sync.DIR / name).st_mode & 0o777, 0o600)

    def test_incomplete_snapshot_does_not_change_local_rows(self):
        row, remote = self.linked()
        with patch.object(sync, 'native', return_value={'ok': True, 'calendar_id': 'list-1', 'complete': False, 'reminders': []}):
            self.assertEqual(sync.run_once()['state'], 'error')
        self.assertEqual(self.item(row['id']), row)

    def test_unexpected_native_exception_reports_failure_and_next_run_recovers(self):
        self.local()
        with patch.object(sync, 'native', side_effect=ValueError('unexpected native response')):
            self.assertEqual(sync.run_once()['state'], 'error')
        self.assertEqual(sync.run_once()['state'], 'ok')

    def test_import_completed_keeps_native_historical_time(self):
        self.fake.add('completed', done=1, completed_at='2026-09-21T09:30:00Z')
        self.assertEqual(sync.run_once()['state'], 'ok')
        row = todos.listing()[0]
        self.assertEqual(row['completed_at'], '2026-09-21T09:30:00+00:00')
        self.assertNotIn('completed_at', sync._FIELDS)
        self.fake.edit('completed', note='后来更新备注', completed_at='2026-10-20T10:00:00Z')
        sync.run_once()
        self.assertEqual(self.item(row['id'])['completed_at'], '2026-09-21T09:30:00+00:00')

    def test_remote_complete_and_reopen_preserves_actual_instant(self):
        row, remote = self.linked()
        self.fake.edit(remote, done=1, completed_at='2026-10-18T23:00:00+08:00')
        sync.run_once()
        self.assertEqual(self.item(row['id'])['completed_at'], '2026-10-18T23:00:00+08:00')
        self.fake.edit(remote, done=0)
        sync.run_once()
        self.assertEqual(self.item(row['id'])['completed_at'], '')

    def test_old_save_api_pauses_completed_import_until_upgrade(self):
        self.fake.add('completed', done=1, completed_at='2026-09-21T09:30:00Z')
        real_save = todos.save
        def old_save(data):
            return real_save(data)
        with patch.object(todos, 'save', old_save):
            result = sync.run_once()
            self.assertEqual(result['state'], 'attention')
            self.assertIn('升级', result['issues'][0]['message'])
            self.assertEqual(todos.listing(), [])
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(todos.listing()[0]['completed_at'], '2026-09-21T09:30:00+00:00')

    def test_unknown_completion_does_not_invent_today(self):
        self.fake.add('completed', done=1, completed_at=None)
        result = sync.run_once()
        self.assertEqual(result['state'], 'attention')
        self.assertIn('未提供', result['issues'][0]['message'])
        self.assertEqual(todos.listing(), [])

    def test_partial_success_publishes_last_checked_for_frontend_refresh(self):
        self.fake.add('repeat', recurrence=True)
        self.fake.add('ordinary')
        result = sync.run_once()
        self.assertEqual(result['state'], 'attention')
        self.assertIsNotNone(result['last_checked'])
        self.assertEqual(sync.status()['last_checked'], result['last_checked'])
        self.assertEqual(len(todos.listing()), 1)

    def test_changed_identifier_reassociates_owned_sync_marker(self):
        local, remote_id = self.linked()
        changed = self.fake.rows.pop(remote_id)
        changed.update(id='refreshed-id', note='仍是同一项')
        self.fake.rows['refreshed-id'] = changed
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(len(todos.listing()), 1)
        self.assertEqual(len(self.fake.rows), 1)
        self.assertEqual(self.item(local['id'])['note'], '仍是同一项')

    def test_changed_apple_identifier_reassociates_unique_external_id(self):
        self.fake.add('old-id', external_id='stable-external')
        sync.run_once()
        key = todos.listing()[0]['id']
        changed = self.fake.rows.pop('old-id')
        changed.update(id='new-id', title='系统刷新标识后的修改')
        self.fake.rows['new-id'] = changed
        self.assertEqual(sync.run_once()['state'], 'ok')
        self.assertEqual(len(todos.listing()), 1)
        self.assertEqual(self.item(key)['title'], '系统刷新标识后的修改')

    def test_duplicate_external_ids_pause_instead_of_duplicate_import(self):
        self.fake.add('old-id', external_id='stable-external')
        sync.run_once()
        del self.fake.rows['old-id']
        self.fake.add('new-1', external_id='stable-external')
        self.fake.add('new-2', external_id='stable-external')
        self.assertEqual(sync.run_once()['state'], 'attention')
        self.assertEqual(len(todos.listing()), 1)

    def test_clear_time_preserves_date_and_completed_uncompleted_roundtrip(self):
        row, remote = self.linked(due='2026-10-21', due_time='09:00')
        self.fake.edit(remote, due_time='', done=1)
        sync.run_once()
        current = self.item(row['id'])
        self.assertEqual((current['due'], current['due_time'], current['done']), ('2026-10-21', '', 1))
        todos.save({**current, 'done': 0})
        sync.run_once()
        self.assertEqual(self.fake.rows[remote]['done'], 0)


class WorkerCallbackTests(unittest.TestCase):
    def test_worker_callback_only_new_successful_pass_and_exception_survival(self):
        class StopWorker(BaseException):
            pass
        callback = Mock()
        fake_event = Mock()
        fake_event.wait.side_effect = [None, None, None, StopWorker()]
        results = [RuntimeError('transient'),
                   {'last_checked': 'first', 'counts': {'imported': 1}},
                   {'last_checked': 'first', 'counts': {'imported': 1}},
                   {'last_checked': 'second', 'counts': {'updated': 1}}]
        previous = sync._WORKER, sync._ON_CHANGE
        sync._WORKER = None
        try:
            with patch.object(sync, 'status', return_value={'last_checked': None}), patch.object(sync, '_WAKE', fake_event), patch.object(sync, 'run_once', side_effect=results), patch.object(sync.threading, 'Thread') as thread:
                sync.start(callback)
                target = thread.call_args.kwargs['target']
                with self.assertRaises(StopWorker):
                    target()
            self.assertEqual(callback.call_count, 2)
        finally:
            sync._WORKER, sync._ON_CHANGE = previous



class LaunchServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_dir = sync.DIR
        sync.DIR = self.root / 'private'
        self.config = {'helper_path': str(self.root / 'Tiangong.app/Contents/MacOS/RemindersBridge'), 'calendar_id': 'list-1'}
        self.snapshot = {'ok': True, 'calendar': {'id': 'list-1', 'writable': True}, 'complete': True, 'reminders': []}

    def tearDown(self):
        sync.DIR = self.old_dir
        self.temp.cleanup()

    def produce(self, argv, reply):
        output = Path(argv[argv.index('--output') + 1])
        output.write_text(json.dumps(reply))
        return subprocess.CompletedProcess(argv, 0, '', '')

    def test_app_uses_launchservices_private_input_and_atomic_output(self):
        def launch(argv, **kwargs):
            self.assertEqual(argv[:5], ['/usr/bin/open', '-n', '-g', '-a', str(self.root / 'Tiangong.app')])
            self.assertEqual(argv[5:7], ['--args', 'snapshot'])
            self.assertNotIn('input', kwargs)
            input_path = Path(argv[argv.index('--input') + 1])
            self.assertEqual(json.loads(input_path.read_text()), {'calendar_id': 'list-1'})
            self.assertEqual(input_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(input_path.parent.stat().st_mode & 0o777, 0o700)
            return self.produce(argv, self.snapshot)
        with patch.object(subprocess, 'run', side_effect=launch) as called:
            result = sync.native({'action': 'snapshot'}, self.config)
        self.assertTrue(result['complete'])
        self.assertEqual(called.call_count, 1)
        self.assertEqual(sync._native_pending(), {})
        self.assertEqual(len(list((sync.DIR / 'native-jobs').glob('*/input.json'))), 1)

    def test_timeout_preserves_write_and_does_not_resend_same_uncertain_request(self):
        payload = {'calendar_id': 'list-1', 'operations': [{'action': 'update', 'id': 'x', 'expected_revision': 'old', 'fields': {'title': 'new'}}]}
        app = sync._application_path(self.config['helper_path'])
        with patch.object(sync, 'NATIVE_TIMEOUT', 0), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            for _ in range(2):
                with self.assertRaisesRegex(sync.NativeError, 'native_uncertain'):
                    sync._launch_services('apply', payload, app)
            self.assertEqual(run.call_count, 1)
        jobs = list((sync.DIR / 'native-jobs').iterdir())
        self.assertEqual(len(jobs), 1)
        self.assertTrue((jobs[0] / 'input.json').is_file())
        reply = {'ok': True, 'results': [{'status': 'applied'}]}
        (jobs[0] / 'output.json').write_text(json.dumps(reply))
        with patch.object(subprocess, 'run', side_effect=AssertionError('must not resend')):
            self.assertEqual(sync._launch_services('apply', payload, app), reply)
        self.assertTrue((jobs[0] / 'input.json').is_file())
        self.assertTrue((jobs[0] / 'output.json').is_file())
        self.assertEqual(sync._native_pending(), {})

    def test_late_write_result_is_reaped_before_fresh_snapshot(self):
        app = sync._application_path(self.config['helper_path'])
        with patch.object(sync, 'NATIVE_TIMEOUT', 0), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(sync.NativeError):
                sync._launch_services('apply', {'operations': []}, app)
        old_job = next((sync.DIR / 'native-jobs').iterdir())
        (old_job / 'output.json').write_text(json.dumps({'ok': True, 'results': []}))
        with patch.object(subprocess, 'run', side_effect=lambda argv, **kwargs: self.produce(argv, self.snapshot)) as run:
            result = sync.native({'action': 'snapshot'}, self.config)
        self.assertTrue(result['complete'])
        self.assertEqual(run.call_count, 1)
        self.assertTrue((old_job / 'input.json').exists())
        self.assertTrue((old_job / 'output.json').exists())

    def test_superseded_read_late_output_enters_bounded_finished_retention(self):
        app = sync._application_path(self.config['helper_path'])
        with patch.object(sync, 'NATIVE_TIMEOUT', 0), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            for _ in range(2):
                with self.assertRaises(sync.NativeError):
                    sync._launch_services('snapshot', {'calendar_id': 'list-1'}, app)
        jobs = list((sync.DIR / 'native-jobs').iterdir())
        active = set(sync._native_pending().values())
        superseded = next(p for p in jobs if p.name not in active)
        (superseded / 'output.json').write_text(json.dumps(self.snapshot))
        with patch.object(subprocess, 'run', side_effect=lambda argv, **kwargs: self.produce(argv, self.snapshot)):
            sync.native({'action': 'snapshot'}, self.config)
        self.assertTrue((superseded / 'result-state.json').is_file())

    def test_cleanup_retains_every_uncertain_job(self):
        app = sync._application_path(self.config['helper_path'])
        with patch.object(sync, 'NATIVE_TIMEOUT', 0), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(sync.NativeError):
                sync._launch_services('apply', {'operations': [{'unknown': 'pending'}]}, app)
        uncertain = next((sync.DIR / 'native-jobs').iterdir())
        with patch.object(subprocess, 'run', side_effect=lambda argv, **kwargs: self.produce(argv, self.snapshot)):
            for _ in range(35):
                sync.native({'action': 'snapshot'}, self.config)
        self.assertTrue((uncertain / 'input.json').is_file())
        self.assertEqual(len(list((sync.DIR / 'native-jobs').iterdir())), 21)
        self.assertIn(uncertain.name, sync._native_pending().values())

    def test_permission_error_does_not_launch_request_access(self):
        denied = {'ok': False, 'error': 'permission_required'}
        with patch.object(subprocess, 'run', side_effect=lambda argv, **kwargs: self.produce(argv, denied)) as run:
            with self.assertRaisesRegex(sync.NativeError, 'permission_required'):
                sync.native({'action': 'snapshot'}, self.config)
        self.assertNotIn('request-access', run.call_args.args[0])
        self.assertEqual(sync._native_pending(), {})



class NativeProtocolTests(unittest.TestCase):
    def test_native_field_mapping_preserves_priority_without_explicit_change(self):
        row = dict(id='apple-1', title='测试', notes='备注', due_date='2026-10-21', due_time=None,
                   completed=False, completed_at='2026-10-19T10:00:00Z', priority=5, revision='fingerprint', protected={'recurrence': False})
        translated = sync._from_native(row)
        self.assertEqual(translated['completed_at'], '2026-10-19T10:00:00Z')
        self.assertEqual((translated['note'], translated['due_time'], translated['priority']), ('备注', '', 'normal'))
        reply = {'ok': True, 'results': [{'status': 'applied', 'reminder': row}]}
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(reply), '')) as run:
            sync.native({'action': 'apply', 'id': 'apple-1', 'expected_revision': 'old', 'fields': {'note': '备注'}}, {'helper_path': '/helper', 'calendar_id': 'list-1'})
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/helper', 'apply'])
        self.assertEqual(json.loads(kwargs['input'])['operations'][0]['fields'], {'notes': '备注'})
        self.assertEqual(sync._to_native({'due': '', 'due_time': ''}), {'due_date': None, 'due_time': None})

    def test_native_failure_hides_helper_stderr(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'secret error body')):
            with self.assertRaises(sync.NativeError) as caught:
                sync.native({'action': 'snapshot'}, {'helper_path': '/helper', 'calendar_id': 'list-1'})
        self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
