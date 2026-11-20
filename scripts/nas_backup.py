# -*- coding: utf-8 -*-
"""All-account SQLite snapshots and private data on the mounted NAS.

BASE is a fixed owner root, never a request-scoped DeferredPath. Only explicit
locations are selected; configured keys and release caches are not backed up.
"""

import runtime_config
import argparse
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time

BASE = runtime_config.STATE_DIR
NAS = Path(os.environ.get('TIANGONG_BACKUP_DIR', str(BASE / 'backups'))).expanduser()
BACKUP_MOUNT = os.environ.get('TIANGONG_BACKUP_MOUNT', '')
USER_ID = re.compile(r'(?:u[0-9a-f]{32}|demo)')
SHA256 = re.compile(r'[0-9a-f]{64}')
DATABASES = (
    'work.sqlite3', 'wellness.sqlite3', 'notes/notes.sqlite3', 'todos.sqlite3',
    'travel.sqlite3', 'finance.sqlite3', 'checkins.sqlite3', 'treasures.sqlite3',
    'games.sqlite3', 'crm.sqlite3', 'receipts.sqlite3', 'people.sqlite3',
)
DATA_FOLDERS = ('crm-assets', 'crm-sources', 'notes/sources', 'reports', 'finance/sources', 'private-images', 'games-covers')
DATA_FILES = ('training-cache.json', 'daily-quotes.json', 'notes/import_latest.json')
GLOBAL_DATABASES = ('security/gateway.sqlite3', 'reminders-sync/state.sqlite3', 'recipes.sqlite3')
GLOBAL_FILES = ('reminders-sync/config.json',)
LOCK = threading.Lock()
QUEUE_LOCK = threading.Lock()
PENDING = False
WORKER = False
PERMISSIONS = ContextVar('nas_backup_permissions', default=None)


class NASAccessError(PermissionError):
    """A read check failed before any backup or chmod was attempted."""


def nas_access_preflight():
    no_symlinks(NAS)
    existing = Path(NAS)
    while not existing.exists():
        existing = existing.parent
    try:
        with os.scandir(existing) as entries:
            next(entries, None)
    except PermissionError as error:
        raise NASAccessError(
            error.errno,
            'NAS目录读取被系统拒绝，尚未开始备份或修改权限；请核查后台进程的网络卷访问授权和NAS共享权限',
            str(existing),
        ) from error


def private_mode(path, mode):
    """Request a private mode and verify what the filesystem actually reports.

    AFP can report success but keep synthetic 0777 modes. Record this limitation
    for NAS destinations, never assert that chmod enforced privacy. A failed
    chmod, local permission mismatch, or denied file operation still fails.
    """
    path = Path(path)
    path.chmod(mode)
    reported = stat.S_IMODE(path.stat().st_mode)
    if reported == mode:
        return
    report = PERMISSIONS.get()
    if report is None or not (path == NAS or NAS in path.parents):
        raise PermissionError(f'文件系统未兑现私有权限：{path}')
    report['nas_posix_modes_verified'] = False
    observation = {'path': path.relative_to(NAS).as_posix(),
                   'requested': f'{mode:04o}', 'reported': f'{reported:04o}'}
    observations = report['nas_mode_observations']
    if len(observations) < 8 and observation not in observations:
        observations.append(observation)


def stamp():
    return datetime.now().astimezone().isoformat()


def no_symlinks(path, root=None):
    """Reject links at/below a data root, including a dangling final link.

    System ancestors may be aliases (macOS /var -> /private/var), so traversal
    starts at the selected data root instead of rejecting operating-system paths.
    """
    path = Path(path).absolute()
    if root is None:
        root = next((candidate for candidate in (Path(BASE).absolute(), Path(NAS).absolute())
                     if path == candidate or candidate in path.parents), path)
    root = Path(root).absolute()
    relative = path.relative_to(root)
    parts = [root]
    for name in relative.parts:
        parts.append(parts[-1] / name)
    for part in parts:
        try:
            details = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode):
            raise OSError(f'备份路径不允许符号链接：{part}')


def private_directory(path, root):
    path, root = Path(path), Path(root)
    relative = path.relative_to(root)
    no_symlinks(path, root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_mode(root, 0o700)
    current = root
    for name in relative.parts:
        current = current / name
        current.mkdir(exist_ok=True, mode=0o700)
        if not current.is_dir():
            raise OSError('备份目录无效')
        private_mode(current, 0o700)


def regular_file(path):
    no_symlinks(path)
    try:
        details = Path(path).lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(details.st_mode):
        raise OSError(f'备份只接受普通文件：{path}')
    return True


def open_regular(path):
    no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError('备份只接受普通文件')
    return os.fdopen(fd, 'rb')


def digest(path):
    value = hashlib.sha256()
    with open_regular(path) as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def copy_private(source, target):
    """Copy a stable ordinary file, without following a source or target link."""
    no_symlinks(target)
    value = hashlib.sha256()
    with open_regular(source) as incoming:
        before = os.fstat(incoming.fileno())
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as outgoing:
            for block in iter(lambda: incoming.read(1024*1024), b''):
                value.update(block)
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OSError('资料在复制期间发生修改，请稍后重新备份')
    private_mode(target, 0o600)
    return value.hexdigest(), after.st_size


def atomic_json(path, data, root):
    private_directory(path.parent, root)
    no_symlinks(path)
    if path.exists():
        regular_file(path)
    fd, name = tempfile.mkstemp(prefix='.json-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            private_mode(Path(name), 0o600)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        private_mode(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def account_roots():
    """Enumerate every legal root, independent of the caller's current account."""
    no_symlinks(BASE)
    roots = [('owner', BASE)]
    users = BASE / 'users'
    no_symlinks(users)
    if not users.exists():
        return roots
    if not users.is_dir():
        raise OSError('账号资料目录无效')
    for child in sorted(users.iterdir()):
        if not USER_ID.fullmatch(child.name):
            continue
        no_symlinks(child)
        if not child.is_dir():
            raise OSError('账号资料目录无效')
        roots.append((child.name, child))
    return roots


def ordinary_files(folder):
    no_symlinks(folder)
    if not folder.exists():
        return
    if not folder.is_dir():
        raise OSError('附件目录无效')
    def fail(error):
        raise error
    for directory, directories, files in os.walk(folder, followlinks=False, onerror=fail):
        directories.sort(); files.sort()
        # A skipped link must not yield a misleadingly complete snapshot.
        for name in directories:
            no_symlinks(Path(directory) / name)
        for name in files:
            source = Path(directory) / name
            if regular_file(source):
                yield source


def snapshot_database(source, target):
    if not regular_file(source):
        return None
    for suffix in ('-wal', '-shm', '-journal'):
        regular_file(Path(str(source)+suffix))
    before = source.stat()
    no_symlinks(target)
    with closing(sqlite3.connect(source.absolute().as_uri()+'?mode=ro', uri=True, timeout=15)) as incoming, closing(sqlite3.connect(str(target), timeout=15)) as outgoing:
        private_mode(target, 0o600)
        incoming.backup(outgoing)
        # The snapshot is a standalone file even when the live database uses
        # WAL. No source journal, shared-memory file or pending write is copied.
        outgoing.execute('PRAGMA journal_mode=DELETE')
        if outgoing.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise OSError('数据库备份校验失败')
    no_symlinks(source)
    after = source.stat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise OSError('数据库文件在备份期间被替换，请稍后重新备份')
    private_mode(target, 0o600)
    return digest(target), target.stat().st_size


def store_object(source):
    objects = NAS / 'objects'
    private_directory(objects, NAS)
    temp = objects / ('.copy-'+os.urandom(12).hex())
    try:
        sha, size = copy_private(source, temp)
        target = objects / sha
        no_symlinks(target)
        if target.exists():
            if not regular_file(target) or digest(target) != sha:
                raise OSError('已有NAS附件校验失败')
            private_mode(target, 0o600)
        else:
            if digest(temp) != sha:
                raise OSError('附件复制校验失败')
            temp.replace(target)
        return sha, size
    finally:
        if temp.exists():
            temp.unlink()


def backup():
    if not LOCK.acquire(False):
        return {'status': 'busy'}
    staging = None
    permissions = {'requested_directory_mode': '0700', 'requested_file_mode': '0600',
                   'nas_posix_modes_verified': True, 'nas_share_acl_verified': False,
                   'nas_mode_observations': []}
    permission_token = PERMISSIONS.set(permissions)
    try:
        if BACKUP_MOUNT and not os.path.ismount(BACKUP_MOUNT):
            raise OSError('NAS未挂载，保留本机数据，未创建假备份目录')
        nas_access_preflight()
        roots = account_roots()
        private_directory(NAS / 'snapshots', NAS)
        ident = datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+os.urandom(3).hex()
        destination = NAS / 'snapshots' / ident
        staging = NAS / 'snapshots' / ('.pending-'+ident)
        private_directory(staging, NAS)
        manifest = {'version': 2, 'created': stamp(), 'accounts': [identity for identity, _ in roots],
                    'consistency': 'sqlite-online-backup-per-database', 'files': [],
                    'permissions': permissions}

        def database(source):
            if not regular_file(source):
                return
            rel = source.relative_to(BASE).as_posix()
            target = staging / rel
            private_directory(target.parent, staging)
            sha, size = snapshot_database(source, target)
            manifest['files'].append({'path': rel, 'location': 'snapshot', 'sha256': sha, 'size': size})

        def ordinary(source):
            if not regular_file(source):
                return
            sha, size = store_object(source)
            manifest['files'].append({'path': source.relative_to(BASE).as_posix(),
                                      'location': 'object', 'sha256': sha, 'size': size})

        # Only this account database is selected from security, never its keys.
        for rel in GLOBAL_DATABASES:
            database(BASE / rel)
        for identity, folder in roots:
            for rel in DATABASES:
                database(folder / rel)
            for rel in DATA_FOLDERS:
                for source in ordinary_files(folder / rel):
                    ordinary(source)
            for rel in DATA_FILES:
                ordinary(folder / rel)
        for rel in GLOBAL_FILES:
            ordinary(BASE / rel)
        manifest['completed'] = stamp()
        atomic_json(staging / 'manifest.json', manifest, staging)
        staging.rename(destination)
        staging = None
        state = {'status': 'complete', 'snapshot': str(destination),
                 'files': len(manifest['files']), 'accounts': len(roots), 'at': manifest['completed'],
                 'permissions': permissions}
        if not permissions['nas_posix_modes_verified']:
            state['warnings'] = ['NAS挂载未按请求显示私有文件权限；共享ACL尚未核验，不代表目录已设为0700、文件已设为0600。']
        atomic_json(BASE / 'nas-backup-status.json', state, BASE)
        return state
    except Exception as error:
        state = {'status': 'failed', 'error': str(error), 'at': stamp()}
        if isinstance(error, NASAccessError):
            state.update(error_code='nas_directory_access_denied', phase='nas_read_preflight',
                         errno=error.errno)
        try:
            atomic_json(BASE / 'nas-backup-status.json', state, BASE)
        except OSError:
            pass
        return state
    finally:
        try:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
        except OSError:
            # A disconnected mount may also prevent cleanup. The hidden
            # pending directory is never advertised as a completed snapshot.
            pass
        finally:
            PERMISSIONS.reset(permission_token)
            LOCK.release()


def request_backup():
    import os, cloud_authority
    if os.environ.get("TIANGONG_CLOUD") == "1" or cloud_authority.config()["mode"] != "local":
        return
    global PENDING, WORKER
    with QUEUE_LOCK:
        PENDING = True
        if WORKER:
            return
        WORKER = True

    def run():
        global PENDING, WORKER
        while True:
            time.sleep(2)
            with QUEUE_LOCK:
                PENDING = False
            backup()
            with QUEUE_LOCK:
                if not PENDING:
                    WORKER = False
                    return
    threading.Thread(target=run, daemon=True).start()


def manifest_entries(snapshot):
    no_symlinks(snapshot / 'manifest.json')
    with open_regular(snapshot / 'manifest.json') as stream:
        manifest = json.load(stream)
    if manifest.get('version') not in (1, 2) or not isinstance(manifest.get('files'), list):
        raise ValueError('无效备份清单')
    names = set()
    for entry in manifest['files']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('无效恢复路径')
        rel = Path(entry['path'])
        if (not rel.parts or rel.as_posix() != entry['path'] or rel.is_absolute()
                or '..' in rel.parts or '\\' in entry['path'] or entry['path'] in names):
            raise ValueError('无效恢复路径')
        if entry.get('location') not in ('snapshot', 'object') or not isinstance(entry.get('sha256'), str) or not SHA256.fullmatch(entry['sha256']):
            raise ValueError('无效备份对象')
        names.add(entry['path'])
    return manifest


def restore_to_empty(snapshot, root):
    """Restore into an empty inspection directory, never over live data."""
    snapshot, root = Path(snapshot).absolute(), Path(root).absolute()
    no_symlinks(snapshot); no_symlinks(root)
    snapshot, root = snapshot.resolve(), root.resolve()
    for protected in (Path(BASE).resolve(), Path(NAS).resolve()):
        if root == protected or protected in root.parents or root in protected.parents:
            raise ValueError('恢复验证目录不能覆盖本机资料或NAS备份')
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError('恢复验证需要空目录')
    private_directory(root, root)
    manifest = manifest_entries(snapshot)
    for entry in manifest['files']:
        rel = Path(entry['path'])
        source = snapshot / rel if entry['location'] == 'snapshot' else NAS / 'objects' / entry['sha256']
        no_symlinks(source, snapshot if entry['location'] == 'snapshot' else NAS)
        target = root / rel
        private_directory(target.parent, root)
        sha, size = copy_private(source, target)
        if sha != entry['sha256'] or ('size' in entry and size != entry['size']):
            raise ValueError('恢复文件校验不一致')
        if target.suffix == '.sqlite3':
            with closing(sqlite3.connect(target.absolute().as_uri()+'?mode=ro&immutable=1', uri=True)) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('恢复数据库校验失败')
    return manifest


def restore_check(snapshot):
    with tempfile.TemporaryDirectory(prefix='tiangong-restore-') as temporary:
        root = Path(temporary)
        manifest = restore_to_empty(snapshot, root)
        note_counts = {}
        accounts = set(manifest.get('accounts', ['owner']))
        for entry in manifest['files']:
            parts = Path(entry['path']).parts
            if parts[0] == 'users' and len(parts) > 2 and USER_ID.fullmatch(parts[1]):
                accounts.add(parts[1])
        for identity in sorted(accounts):
            if identity != 'owner' and (not isinstance(identity, str) or not USER_ID.fullmatch(identity)):
                raise ValueError('备份账号编号无效')
            notes = root / ('notes/notes.sqlite3' if identity == 'owner' else f'users/{identity}/notes/notes.sqlite3')
            count = 0
            if notes.is_file():
                with closing(sqlite3.connect(notes.absolute().as_uri()+'?mode=ro&immutable=1', uri=True)) as connection:
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes'").fetchone():
                        count = connection.execute('SELECT count(*) FROM notes').fetchone()[0]
            note_counts[identity] = count
    return {'status': 'restore_verified', 'files': len(manifest['files']),
            'notes': note_counts.get('owner', 0), 'notes_by_account': note_counts,
            'account_database': any(entry['path'] == 'security/gateway.sqlite3' for entry in manifest['files'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify')
    args = parser.parse_args()
    print(json.dumps(restore_check(args.verify) if args.verify else backup(), ensure_ascii=False))
