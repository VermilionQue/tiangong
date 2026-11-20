"""Synthetic owner/account restore drills; no mounted NAS or real data is used."""
from contextlib import closing
import errno
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nas_backup
import tenant_storage


class AllAccountBackupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='tiangong-backup-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base, self.nas = self.root / 'local', self.root / 'nas'
        self.base.mkdir()
        self.users = ['u'+'a'*32, 'u'+'b'*32]
        for name, value in [('BASE', self.base), ('NAS', self.nas)]:
            switch = patch.object(nas_backup, name, value)
            switch.start(); self.addCleanup(switch.stop)
        mounted = patch.object(nas_backup.os.path, 'ismount', return_value=True)
        mounted.start(); self.addCleanup(mounted.stop)

    def file(self, relative, content):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def database(self, relative, marker, amount=100):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('CREATE TABLE receipts(id TEXT PRIMARY KEY, amount_cents INTEGER, note TEXT)')
            connection.execute('INSERT INTO receipts VALUES(?,?,?)', ('same-record-id', amount, marker))
            connection.commit()
        return path

    def notes(self, relative, count):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('CREATE TABLE notes(id TEXT PRIMARY KEY)')
            connection.executemany('INSERT INTO notes VALUES(?)', [(str(i),) for i in range(count)])
            connection.commit()

    def backup(self):
        result = nas_backup.backup()
        self.assertEqual(result['status'], 'complete', result)
        snapshot = Path(result['snapshot'])
        manifest = json.loads((snapshot / 'manifest.json').read_text())
        return snapshot, manifest

    def all_permissions_private(self, root):
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        for path in root.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600, str(path))

    def test_all_accounts_receipts_identity_and_evidence_restore_without_mixing(self):
        for index, identity in enumerate(['owner', *self.users, 'demo']):
            prefix = '' if identity == 'owner' else f'users/{identity}/'
            self.database(prefix+'receipts.sqlite3', identity, amount=(index+1)*101)
            self.database(prefix+'work.sqlite3', identity)
            self.notes(prefix+'notes/notes.sqlite3', index+1)
            for folder in nas_backup.DATA_FOLDERS:
                self.file(prefix+folder+'/same-name.bin', f'private-evidence-{identity}-{folder}')
            self.file(prefix+'private-images/receipts/proof.webp', 'private-receipt-'+identity)
            for filename in nas_backup.DATA_FILES:
                self.file(prefix+filename, json.dumps({'identity': identity}, ensure_ascii=False))
        gateway = self.base / 'security/gateway.sqlite3'
        gateway.parent.mkdir()
        with closing(sqlite3.connect(gateway)) as connection:
            connection.execute('CREATE TABLE users(id TEXT PRIMARY KEY, password TEXT, settings TEXT)')
            for identity in ['owner', *self.users, 'demo']:
                connection.execute('INSERT INTO users VALUES(?,?,?)',
                    (identity, json.dumps({'salt': 'synthetic-salt-'+identity, 'hash': 'synthetic-hash-'+identity}), json.dumps({'theme': 'journal'})))
            connection.execute('CREATE TABLE invitations(digest TEXT,user_id TEXT)')
            connection.execute('INSERT INTO invitations VALUES(?,?)', ('synthetic-invite-hash', self.users[0]))
            connection.execute('CREATE TABLE sessions(digest TEXT,user_id TEXT)')
            connection.execute('INSERT INTO sessions VALUES(?,?)', ('synthetic-session-hash', self.users[1]))
            connection.commit()
        self.database('reminders-sync/state.sqlite3', 'paired-only-on-owner')
        self.file('reminders-sync/config.json', '{"calendar_id":"synthetic-calendar"}')
        # The request context must not reduce the global backup to this account.
        with tenant_storage.scope(self.users[0]):
            snapshot, manifest = self.backup()
        self.assertEqual(manifest['version'], 2)
        self.assertEqual(set(manifest['accounts']), {'owner', *self.users, 'demo'})
        self.assertIn('security/gateway.sqlite3', {item['path'] for item in manifest['files']})
        restored = self.root / 'restore'
        nas_backup.restore_to_empty(snapshot, restored)
        for index, identity in enumerate(['owner', *self.users, 'demo']):
            prefix = '' if identity == 'owner' else f'users/{identity}/'
            with closing(sqlite3.connect(restored / (prefix+'receipts.sqlite3'))) as connection:
                self.assertEqual(connection.execute('SELECT amount_cents,note FROM receipts').fetchall(), [((index+1)*101, identity)])
            self.assertEqual((restored / (prefix+'private-images/receipts/proof.webp')).read_text(), 'private-receipt-'+identity)
            self.assertEqual(json.loads((restored / (prefix+'training-cache.json')).read_text()), {'identity': identity})
        with closing(sqlite3.connect(restored / 'security/gateway.sqlite3')) as connection:
            self.assertEqual({row[0] for row in connection.execute('SELECT id FROM users')}, {'owner', *self.users, 'demo'})
            self.assertEqual(connection.execute('SELECT user_id FROM invitations').fetchone()[0], self.users[0])
            self.assertEqual(connection.execute('SELECT user_id FROM sessions').fetchone()[0], self.users[1])
            self.assertEqual(json.loads(connection.execute('SELECT settings FROM users WHERE id=?', (self.users[0],)).fetchone()[0]), {'theme': 'journal'})
        verified = nas_backup.restore_check(snapshot)
        self.assertTrue(verified['account_database'])
        self.assertEqual(verified['notes_by_account'], {'owner':1, self.users[0]:2, self.users[1]:3, 'demo':4})
        self.all_permissions_private(self.nas)
        self.all_permissions_private(restored)
        self.assertEqual((self.base / 'nas-backup-status.json').stat().st_mode & 0o777, 0o600)

    def test_explicit_allowlist_excludes_keys_releases_and_unknown_account_paths(self):
        forbidden = []
        for prefix in ['', f'users/{self.users[0]}/', 'users/demo/']:
            for rel in ['security/steam-games.json', 'security/gateway.json', 'security/upstream-secret',
                        'steam-games.json', 'service/release/secrets.txt', 'release-backups/old/private.txt',
                        'reminders-sync/native-jobs/job/input.json', 'reminders-sync/native-pending.json']:
                forbidden.append(prefix+rel)
                self.file(prefix+rel, 'SYNTHETIC-PLAINTEXT-SECRET-'+prefix+rel)
        self.database('receipts.sqlite3', 'owner')
        self.database(f'users/{self.users[0]}/receipts.sqlite3', 'other')
        self.database('users/not-a-legal-account/receipts.sqlite3', 'must-not-copy')
        self.database('users/owner/receipts.sqlite3', 'must-not-copy')
        self.file('daily-quotes.json', '{"quote":"safe ordinary data"}')
        snapshot, manifest = self.backup()
        names = {item['path'] for item in manifest['files']}
        self.assertTrue(names.isdisjoint(forbidden))
        self.assertFalse(any('service/' in name or 'release-backups/' in name for name in names))
        self.assertFalse(any('not-a-legal-account' in name or name.startswith('users/owner/') for name in names))
        for path in self.nas.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'SYNTHETIC-PLAINTEXT-SECRET-', path.read_bytes())
        self.assertEqual(nas_backup.restore_check(snapshot)['status'], 'restore_verified')

    def test_sqlite_online_backup_includes_committed_wal_data(self):
        source = self.base / 'security/gateway.sqlite3'
        source.parent.mkdir()
        connection = sqlite3.connect(source)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE users(id TEXT PRIMARY KEY, settings TEXT)')
        connection.commit()
        connection.execute('INSERT INTO users VALUES(?,?)', (self.users[0], '{"theme":"journal"}'))
        connection.commit()
        self.assertTrue(Path(str(source)+'-wal').exists())
        snapshot, manifest = self.backup()
        with closing(sqlite3.connect(snapshot / 'security/gateway.sqlite3')) as restored:
            self.assertEqual(restored.execute('SELECT * FROM users').fetchall(), [(self.users[0], '{"theme":"journal"}')])
        self.assertEqual([item['path'] for item in manifest['files']], ['security/gateway.sqlite3'])
        self.assertTrue(nas_backup.restore_check(snapshot)['account_database'])

    def test_selected_symlink_file_folder_account_or_database_rejects_snapshot(self):
        external = self.root / 'outside'
        external.mkdir()
        (external / 'secret.txt').write_text('outside-secret')
        for relative, directory in [('reports/link.txt', False), ('private-images/linked', True),
                                    (f'users/{self.users[0]}', True), ('receipts.sqlite3', False)]:
            with self.subTest(relative=relative):
                link = self.base / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(external if directory else external / 'secret.txt', target_is_directory=directory)
                result = nas_backup.backup()
                self.assertEqual(result['status'], 'failed', result)
                self.assertIn('符号链接', result['error'])
                self.assertEqual(list((self.nas / 'snapshots').glob('*/manifest.json')), [])
                self.assertEqual(list((self.nas / 'snapshots').glob('.pending-*')), [])
                link.unlink()
        self.assertEqual((external / 'secret.txt').read_text(), 'outside-secret')

    def test_existing_object_link_or_tampered_object_does_not_publish_new_snapshot(self):
        self.file('reports/proof.txt', 'real-evidence')
        snapshot, manifest = self.backup()
        entry = manifest['files'][0]
        obj = self.nas / 'objects' / entry['sha256']
        obj.write_text('tampered')
        result = nas_backup.backup()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(list((self.nas / 'snapshots').glob('*/manifest.json'))), 1)
        with self.assertRaises(ValueError):
            nas_backup.restore_check(snapshot)
        obj.unlink()
        external = self.root / 'secret'
        external.write_text('outside-secret')
        obj.symlink_to(external)
        self.assertEqual(nas_backup.backup()['status'], 'failed')
        with self.assertRaises(OSError):
            nas_backup.restore_check(snapshot)
        self.assertEqual(external.read_text(), 'outside-secret')

    def test_symlinked_nas_root_and_sqlite_journal_are_never_followed(self):
        source = self.database('receipts.sqlite3', 'owner')
        outside = self.root / 'outside-nas'
        outside.mkdir()
        self.nas.symlink_to(outside, target_is_directory=True)
        self.assertEqual(nas_backup.backup()['status'], 'failed')
        self.assertEqual(list(outside.iterdir()), [])
        self.nas.unlink()
        secret = outside / 'private'
        secret.write_text('outside-secret')
        Path(str(source)+'-wal').symlink_to(secret)
        result = nas_backup.backup()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('符号链接', result['error'])
        self.assertEqual(secret.read_text(), 'outside-secret')

    def test_failed_database_snapshot_keeps_existing_backup_and_cleans_staging(self):
        self.database('receipts.sqlite3', 'good')
        old, _ = self.backup()
        self.file('finance.sqlite3', 'not-a-sqlite-database')
        self.assertEqual(nas_backup.backup()['status'], 'failed')
        self.assertTrue((old / 'manifest.json').exists())
        self.assertEqual(list((self.nas / 'snapshots').glob('.pending-*')), [])
        self.assertEqual(nas_backup.restore_check(old)['status'], 'restore_verified')

    def test_restore_refuses_live_target_traversal_duplicate_path_or_bad_object_reference(self):
        self.database('receipts.sqlite3', 'good')
        snapshot, manifest = self.backup()
        for forbidden in (self.base, self.base / 'new', self.nas, self.nas / 'new', self.root):
            with self.subTest(target=forbidden), self.assertRaises(ValueError):
                nas_backup.restore_to_empty(snapshot, forbidden)
        original = manifest['files'][0]
        for files in ([{**original, 'path':'../outside.sqlite3'}], [original, original],
                      [{**original, 'location':'object', 'sha256':'../secret'}], [{**original, 'location':'unknown'}]):
            with self.subTest(files=files):
                (snapshot / 'manifest.json').write_text(json.dumps({**manifest, 'files':files}))
                with self.assertRaises(ValueError):
                    nas_backup.restore_check(snapshot)
        (snapshot / 'manifest.json').write_text(json.dumps(manifest))
        database = snapshot / original['path']
        database.unlink(); database.symlink_to(self.base / 'receipts.sqlite3')
        with self.assertRaises(OSError):
            nas_backup.restore_check(snapshot)

    def test_legacy_v1_snapshot_without_owner_notes_still_verifies(self):
        self.database(f'users/{self.users[0]}/receipts.sqlite3', 'other')
        snapshot, manifest = self.backup()
        manifest['version'] = 1
        manifest.pop('accounts')
        for entry in manifest['files']:
            entry.pop('size')
        (snapshot / 'manifest.json').write_text(json.dumps(manifest))
        verified = nas_backup.restore_check(snapshot)
        self.assertEqual(verified['notes'], 0)
        self.assertEqual(verified['notes_by_account'], {'owner':0, self.users[0]:0})

    def test_missing_mount_never_creates_fake_nas_tree(self):
        self.database('receipts.sqlite3', 'owner')
        with patch.object(nas_backup, 'BACKUP_MOUNT', str(self.nas)), patch.object(nas_backup.os.path, 'ismount', return_value=False):
            result = nas_backup.backup()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.nas.exists())
        self.assertTrue((self.base / 'receipts.sqlite3').exists())

    def test_background_network_volume_read_denial_fails_before_nas_mutation(self):
        self.database('receipts.sqlite3', 'owner')
        self.nas.mkdir()
        original_scandir, original_chmod = nas_backup.os.scandir, Path.chmod
        nas_chmod = []

        def denied_scan(path):
            if Path(path) == self.nas:
                raise PermissionError(errno.EPERM, 'Operation not permitted', str(path))
            return original_scandir(path)

        def tracked_chmod(path, mode, **kwargs):
            if path == self.nas or self.nas in path.parents:
                nas_chmod.append(path)
            return original_chmod(path, mode, **kwargs)

        with patch.object(nas_backup.os, 'scandir', side_effect=denied_scan), patch.object(Path, 'chmod', tracked_chmod):
            result = nas_backup.backup()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'nas_directory_access_denied')
        self.assertEqual(result['phase'], 'nas_read_preflight')
        self.assertEqual(result['errno'], errno.EPERM)
        self.assertIn('尚未开始备份或修改权限', result['error'])
        self.assertEqual(nas_chmod, [])
        self.assertEqual(list(self.nas.iterdir()), [])
        self.assertEqual(json.loads((self.base / 'nas-backup-status.json').read_text())['phase'], 'nas_read_preflight')
        self.assertIsNone(nas_backup.PERMISSIONS.get())

    def test_afp_successful_but_unapplied_chmod_is_recorded_without_claiming_privacy(self):
        self.database('receipts.sqlite3', 'owner')
        self.file('private-images/receipts/proof.webp', 'receipt-evidence')
        original_chmod = Path.chmod

        def afp_chmod(path, mode, **kwargs):
            if path == self.nas or self.nas in path.parents:
                # An AFP share may accept chmod yet expose synthetic 0777.
                return original_chmod(path, 0o777, **kwargs)
            return original_chmod(path, mode, **kwargs)

        with patch.object(Path, 'chmod', afp_chmod):
            snapshot, manifest = self.backup()
        self.assertFalse(manifest['permissions']['nas_posix_modes_verified'])
        self.assertFalse(manifest['permissions']['nas_share_acl_verified'])
        observations = manifest['permissions']['nas_mode_observations']
        self.assertIn({'path': '.', 'requested': '0700', 'reported': '0777'}, observations)
        self.assertLessEqual(len(observations), 8)
        state = json.loads((self.base / 'nas-backup-status.json').read_text())
        self.assertFalse(state['permissions']['nas_posix_modes_verified'])
        self.assertTrue(state['warnings'])
        restored = self.root / 'private-local-restore'
        nas_backup.restore_to_empty(snapshot, restored)
        self.all_permissions_private(restored)
        self.assertEqual((restored / 'private-images/receipts/proof.webp').read_text(), 'receipt-evidence')
        self.assertIsNone(nas_backup.PERMISSIONS.get())

    def test_chmod_errors_are_not_silently_tolerated_as_afp(self):
        self.database('receipts.sqlite3', 'owner')
        original_chmod = Path.chmod

        def denied_chmod(path, mode, **kwargs):
            if path == self.nas:
                raise PermissionError(errno.EPERM, 'Operation not permitted', str(path))
            return original_chmod(path, mode, **kwargs)

        with patch.object(Path, 'chmod', denied_chmod):
            result = nas_backup.backup()
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('nas_directory_access_denied', result.get('error_code', ''))
        self.assertEqual(list((self.nas / 'snapshots').glob('*/manifest.json')), [])
        self.assertIsNone(nas_backup.PERMISSIONS.get())

    def test_local_restore_modes_must_really_be_enforced(self):
        self.database('receipts.sqlite3', 'owner')
        snapshot, _ = self.backup()
        restored = (self.root / 'restore-with-bad-local-permissions').resolve()
        original_chmod = Path.chmod

        def unapplied_local_chmod(path, mode, **kwargs):
            return original_chmod(path, 0o777 if path == restored else mode, **kwargs)

        with patch.object(Path, 'chmod', unapplied_local_chmod):
            with self.assertRaisesRegex(PermissionError, '未兑现私有权限'):
                nas_backup.restore_to_empty(snapshot, restored)
        self.assertEqual(list(restored.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
