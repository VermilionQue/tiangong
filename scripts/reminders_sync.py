"""Local, opt-in Apple Reminders reconciliation; never sends notifications.

Runtime state belongs outside the synced source tree. The web server owns one
worker; flock also serializes a CLI run with the server. No permission prompt is
made here: the separately installed native helper owns Reminders access.
"""

import runtime_config
import argparse
from contextlib import contextmanager
from datetime import datetime, date
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

import todos

DIR = runtime_config.STATE_DIR / 'reminders-sync'
INTERVAL = 30
NATIVE_TIMEOUT = 60
_WAKE = threading.Event()
_START_LOCK = threading.Lock()
_WORKER = None
_ON_CHANGE = None
_GROUPS = (('title',), ('note',), ('due', 'due_time'), ('done',), ('priority',))
_FIELDS = tuple(k for group in _GROUPS for k in group)


def _now():
    return datetime.now().astimezone().isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _private_dir():
    DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DIR, 0o700)


def _write_json(name, value):
    _private_dir()
    temp = DIR / (name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, DIR / name)
    finally:
        temp.unlink(missing_ok=True)


def _read_json(name, fallback):
    try:
        return json.loads((DIR / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def _config():
    value = _read_json('config.json', {})
    return value if isinstance(value, dict) else {}


def _db():
    _private_dir()
    path = DIR / 'state.sqlite3'
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    os.chmod(path, 0o600)
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS links (
            todo_id TEXT PRIMARY KEY, remote_id TEXT UNIQUE,
            base_local TEXT NOT NULL, base_remote TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'linked');
        CREATE TABLE IF NOT EXISTS identities (todo_id TEXT PRIMARY KEY, external_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS issues (
            issue_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
    ''')
    return connection


@contextmanager
def _exclusive():
    _private_dir()
    fd = os.open(DIR / 'sync.lock', os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _default_status():
    return dict(enabled=False, state='disabled', last_success=None, last_checked=None,
                last_error=None, message='尚未连接苹果提醒事项', issues=[],
                pending=0, counts={})


def status():
    if os.environ.get("TIANGONG_CLOUD") == "1":
        import cloud_reminders
        return cloud_reminders.state()
    result = _default_status()
    saved = _read_json('status.json', {})
    if isinstance(saved, dict):
        result.update({k: v for k, v in saved.items() if k in result})
    config = _config()
    result['enabled'] = bool(config.get('enabled') and config.get('calendar_id') and config.get('helper_path'))
    if not result['enabled']:
        result.update(state='disabled', message='尚未连接苹果提醒事项')
    return result


def configure(calendar_id, helper_path):
    """Enable only an explicitly selected list; never asks macOS for access."""
    if not isinstance(calendar_id, str) or not calendar_id.strip() or len(calendar_id) > 1024:
        raise ValueError('请选择有效的提醒事项清单')
    helper = Path(helper_path).expanduser()
    if not helper.is_absolute() or not helper.is_file() or not os.access(helper, os.X_OK):
        raise ValueError('提醒事项连接程序尚未安装')
    with _exclusive() as acquired:
        if not acquired:
            raise ValueError('同步正在进行，请稍后重试')
        old = _config()
        if old.get('calendar_id') and old['calendar_id'] != calendar_id:
            raise ValueError('已绑定清单不可直接替换，避免重复导入')
        _write_json('config.json', dict(enabled=True, calendar_id=calendar_id, helper_path=str(helper)))
        current = status()
        current.update(enabled=True, state='attention', message='已连接，等待首次同步')
        _write_json('status.json', current)
    wake()
    return status()


class NativeError(RuntimeError):
    def __init__(self, code='native_error'):
        self.code = str(code)
        super().__init__(self.code)


def _from_native(row):
    if not isinstance(row, dict):
        raise NativeError('invalid_response')
    priority = row.get('priority', 0)
    return dict(id=row.get('id'), external_id=row.get('external_id'),
                sync_id=row.get('sync_id'), title=row.get('title'),
                note=row.get('notes') or '', due=row.get('due_date') or '',
                due_time=row.get('due_time') or '', done=int(bool(row.get('completed'))),
                priority='important' if isinstance(priority, int) and 1 <= priority <= 4 else 'normal',
                revision=row.get('revision'), completed_at=row.get('completed_at'),
                recurrence=bool(row.get('protected', {}).get('recurrence')),
                writable=row.get('writable', True), skip_reason=row.get('skip_reason'))


def _to_native(fields):
    names = {'note': 'notes', 'due': 'due_date', 'done': 'completed'}
    result = {}
    for key, value in fields.items():
        if key == 'priority':
            value = 1 if value == 'important' else 0
        elif key == 'done':
            value = bool(value)
        elif key in ('due', 'due_time') and value == '':
            value = None
        result[names.get(key, key)] = value
    return result


def _application_path(helper_path):
    """A deployed .app must run as itself, not inherit launchd Python's TCC."""
    helper = Path(helper_path)
    if len(helper.parents) >= 3 and helper.parent.name == 'MacOS' and helper.parent.parent.name == 'Contents':
        bundle = helper.parent.parent.parent
        if bundle.suffix.lower() == '.app':
            return bundle
    return None


def _native_pending():
    pending = _read_json('native-pending.json', {})
    if not isinstance(pending, dict):
        raise NativeError('invalid_transport_state')
    return pending


def _job_output(job_id):
    if not isinstance(job_id, str) or not re.fullmatch('[0-9a-f]{32}', job_id):
        raise NativeError('invalid_transport_state')
    path = DIR / 'native-jobs' / job_id / 'output.json'
    if not path.exists():
        return None
    try:
        os.chmod(path, 0o600)
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise NativeError('invalid_response') from exc
    if not isinstance(result, dict):
        raise NativeError('invalid_response')
    return result


def _finish_job(job_id, pending, fingerprint):
    # Keep request and response as private recovery evidence; never unlink a
    # possibly running app's files. Only release its active dedupe pointer.
    _write_json('native-jobs/' + job_id + '/result-state.json', dict(state='finished', at=_now()))
    pending.pop(fingerprint, None)
    _write_json('native-pending.json', pending)
    # Bound ordinary successful scan artifacts; uncertain jobs have no finished
    # marker and are never pruned while a native process may still use them.
    active = set(pending.values())
    # Superseded read jobs may finish late after their active pointer changed.
    # Recognize their atomic result before including them in bounded retention.
    for candidate in (DIR / 'native-jobs').iterdir():
        if (candidate.is_dir() and candidate.name not in active and
                re.fullmatch('[0-9a-f]{32}', candidate.name) and
                (candidate / 'output.json').is_file() and
                not (candidate / 'result-state.json').exists()):
            if _job_output(candidate.name) is not None:
                _write_json('native-jobs/' + candidate.name + '/result-state.json', dict(state='finished', at=_now()))
    finished = sorted((p for p in (DIR / 'native-jobs').iterdir()
                       if p.is_dir() and p.name not in active and (p / 'result-state.json').is_file()),
                      key=lambda p: (p / 'result-state.json').stat().st_mtime_ns, reverse=True)
    for old in finished[20:]:
        shutil.rmtree(old)


def _launch_services(command, payload, app):
    fingerprint = hashlib.sha256(_json([str(app), command, payload]).encode()).hexdigest()
    pending = _native_pending()
    job_id = pending.get(fingerprint)
    if job_id:
        output = _job_output(job_id)
        if output is not None:
            _finish_job(job_id, pending, fingerprint)
            if command == 'apply':
                return output
            job_id = None  # A later pass needs a fresh snapshot, not an old one.
        elif command == 'snapshot':
            # Reads may be superseded without cancelling the first process.
            # Retain its files; a late write can still finish there safely.
            pending.pop(fingerprint, None)
            _write_json('native-pending.json', pending)
            job_id = None
    # Retire other completed jobs while retaining their input/output artifacts.
    for old_fingerprint, old_job in list(pending.items()):
        if old_job != job_id and _job_output(old_job) is not None:
            _finish_job(old_job, pending, old_fingerprint)
    deadline = time.monotonic() + NATIVE_TIMEOUT
    if not job_id:
        _private_dir()
        jobs = DIR / 'native-jobs'
        jobs.mkdir(exist_ok=True, mode=0o700)
        os.chmod(jobs, 0o700)
        job_id = uuid.uuid4().hex
        folder = jobs / job_id
        folder.mkdir(mode=0o700)
        _write_json('native-jobs/' + job_id + '/input.json', payload)
        _write_json('native-jobs/' + job_id + '/request.json',
                    dict(command=command, fingerprint=fingerprint, created=_now(), state='dispatched_or_uncertain'))
        pending[fingerprint] = job_id
        _write_json('native-pending.json', pending)  # Durable before launching.
        argv = ['/usr/bin/open', '-n', '-g', '-a', str(app), '--args', command,
                '--input', str(folder / 'input.json'), '--output', str(folder / 'output.json')]
        try:
            launch = subprocess.run(argv, text=True, capture_output=True,
                                    timeout=max(0.01, min(10, NATIVE_TIMEOUT)), check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeError('native_uncertain') from exc
        if launch.returncode:
            raise NativeError('native_uncertain')
    while True:
        output = _job_output(job_id)
        if output is not None:
            _finish_job(job_id, pending, fingerprint)
            return output
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeError('native_uncertain')
        time.sleep(min(0.05, remaining))


def _native_transport(command, payload, config):
    app = _application_path(config['helper_path'])
    if app:
        return _launch_services(command, payload, app)
    # Standalone helpers are supported for isolated tests and explicit tools.
    try:
        completed = subprocess.run([config['helper_path'], command], input=_json(payload),
                                   text=True, capture_output=True, timeout=NATIVE_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeError('unavailable') from exc
    try:
        return json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise NativeError('invalid_response') from exc


def native(request, config):
    """Translate the native protocol to canonical fields; injectable in tests."""
    action = request['action']
    if action == 'snapshot':
        command = 'snapshot'
        payload = {'calendar_id': config['calendar_id']}
    else:
        command = 'apply'
        operation = dict(action='create' if action == 'create' else 'update',
                         fields=_to_native(request['fields']))
        for key in ('sync_id', 'id', 'expected_revision'):
            if key in request:
                operation[key] = request[key]
        payload = dict(calendar_id=config['calendar_id'], operations=[operation])
    reply = _native_transport(command, payload, config)
    if not isinstance(reply, dict) or not reply.get('ok'):
        error = reply.get('error', {}) if isinstance(reply, dict) else {}
        code = error.get('code', 'native_error') if isinstance(error, dict) else str(error)
        raise NativeError(code)
    if command == 'snapshot':
        calendar = reply.get('calendar', {})
        if calendar.get('writable') is False:
            raise NativeError('calendar_readonly')
        return dict(ok=True, calendar_id=calendar.get('id'), complete=reply.get('complete'),
                    reminders=[_from_native(row) for row in reply.get('reminders', [])])
    results = reply.get('results')
    if not isinstance(results, list) or len(results) != 1:
        raise NativeError('invalid_response')
    result = results[0]
    if result.get('status') not in ('applied', 'already_exists'):
        raise NativeError(result.get('status', 'native_error'))
    return dict(ok=True, reminder=_from_native(result.get('reminder')))


def _snapshot(config):
    reply = native({'action': 'snapshot'}, config)
    if reply.get('calendar_id') != config['calendar_id'] or reply.get('complete') is not True:
        raise NativeError('incomplete_snapshot')
    rows = reply.get('reminders')
    if not isinstance(rows, list):
        raise NativeError('invalid_response')
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in by_id:
            raise NativeError('invalid_response')
        by_id[row['id']] = row
    return by_id


def _normalized(row):
    result = {k: row.get(k, '' if k not in ('done', 'priority') else (0 if k == 'done' else 'normal')) for k in _FIELDS}
    if not isinstance(result['title'], str) or not result['title'].strip() or len(result['title']) > 200:
        raise ValueError('标题为空或超过200字，尚未同步')
    if not isinstance(result['note'], str) or len(result['note']) > 5000:
        raise ValueError('备注超过5000字或格式无法同步')
    result['title'] = result['title'].strip()
    if not isinstance(result['due'], str) or not isinstance(result['due_time'], str):
        raise ValueError('日期或时间格式无法同步')
    if result['due']:
        try:
            if date.fromisoformat(result['due']).isoformat() != result['due']:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('日期格式无法同步') from exc
    if result['due_time'] and (not result['due'] or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', result['due_time'])):
        raise ValueError('时间格式无法同步')
    if result['done'] not in (0, 1) or result['priority'] not in ('normal', 'important'):
        raise ValueError('完成状态或重要程度无法同步')
    result['done'] = int(result['done'])
    return result


def _issue(db, key, todo_id, title, message, kind, fields=None, token=None, local=None, apple=None):
    result = dict(todo_id=todo_id, title=str(title or '')[:200], message=message,
                  kind=kind, fields=fields or [])
    if token:
        result['token'] = token
    if local is not None:
        result['local'] = local
    if apple is not None:
        result['apple'] = apple
    db.execute('INSERT OR REPLACE INTO issues VALUES (?,?)', (key, _json(result)))
    db.commit()
    return result


def _clear_issue(db, key):
    db.execute('DELETE FROM issues WHERE issue_key=?', (key,))
    db.commit()


def _link(db, todo_id, remote_id, local, remote, phase='linked', external_id=None):
    db.execute('INSERT INTO links VALUES (?,?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET remote_id=excluded.remote_id,base_local=excluded.base_local,base_remote=excluded.base_remote,phase=excluded.phase',
               (todo_id, remote_id, _json(local), _json(remote), phase))
    if external_id:
        db.execute('INSERT OR REPLACE INTO identities VALUES (?,?)', (todo_id, external_id))
    db.commit()


def _save_local(data, current=None, remote=None):
    """Native completion dates are trusted metadata, never browser fields."""
    if data.get('done') and (current is None or not current.get('done')):
        completed_at = (remote or {}).get('completed_at')
        if not completed_at:
            raise NativeError('completion_time_unknown')
        if 'completed_at' not in inspect.signature(todos.save).parameters:
            raise NativeError('completion_upgrade_required')
        return todos.save(data, completed_at=completed_at)
    return todos.save(data)


def _local_rows():
    return {r['id']: r for r in todos.listing()}


def _merge(local, remote, base_local, base_remote, choice=None):
    target = dict(local)
    conflicts = []
    for group in _GROUPS:
        left = tuple(local[k] for k in group)
        right = tuple(remote[k] for k in group)
        left_changed = left != tuple(base_local[k] for k in group)
        right_changed = right != tuple(base_remote[k] for k in group)
        if left == right:
            continue
        if left_changed and right_changed:
            conflicts.extend(group)
            if choice == 'apple':
                target.update({k: remote[k] for k in group})
        elif right_changed:
            target.update({k: remote[k] for k in group})
        elif not left_changed:
            # Baselines may differ only after a recoverable partial write.
            # Neither side changed: retain the local version deterministically.
            pass
    return target, conflicts


def _conflict_token(link, local_row, remote_row):
    data = [link['todo_id'], link['base_local'], link['base_remote'],
            local_row.get('revision'), _normalized(local_row),
            remote_row.get('revision'), _normalized(remote_row)]
    return hashlib.sha256(_json(data).encode()).hexdigest()


def _sync_link(db, link, local_row, remote_row, config, resolution=None):
    key = link['todo_id']
    if not local_row:
        _issue(db, key, key, '', '本机原事项不存在，已暂停以避免自动重建', 'missing_local')
        return 'paused'
    if local_row.get('deleted'):
        _issue(db, key, key, local_row['title'], '事项在天工录回收站，暂不向苹果删除；恢复后继续同步', 'local_deleted')
        return 'paused'
    if not remote_row:
        _issue(db, key, key, local_row['title'], '苹果事项已删除或移出清单，已暂停；不会自动重建或删除本机事项', 'missing_apple')
        return 'paused'
    if remote_row.get('writable') is False:
        _issue(db, key, key, local_row['title'], '苹果事项不可写，暂时停止同步', 'unsupported')
        return 'paused'
    if remote_row.get('recurrence'):
        _issue(db, key, key, local_row['title'], '苹果事项含重复规则，暂不支持同步', 'unsupported')
        return 'paused'
    local, remote = _normalized(local_row), _normalized(remote_row)
    base_local, base_remote = json.loads(link['base_local']), json.loads(link['base_remote'])
    target, conflicts = _merge(local, remote, base_local, base_remote)
    token = _conflict_token(link, local_row, remote_row)
    if resolution:
        choice, expected_token = resolution
        if not conflicts or token != expected_token:
            if conflicts:
                _issue(db, key, key, local['title'], '两边同时修改了同一内容，请选择保留哪一边', 'conflict', conflicts, token, local, remote)
            else:
                _clear_issue(db, key)
            raise ValueError('事项已发生变化，请刷新后重新选择')
        target, _ = _merge(local, remote, base_local, base_remote, choice)
    elif conflicts:
        _issue(db, key, key, local['title'], '两边同时修改了同一内容，请选择保留哪一边', 'conflict', conflicts, token, local, remote)
        return 'conflict'
    patch = {k: target[k] for k in _FIELDS if target[k] != remote[k]}
    # Send dates together so clearing a time never clears the day accidentally.
    if any(k in patch for k in ('due', 'due_time')):
        patch.update(due=target['due'], due_time=target['due_time'])
    if patch:
        reply = native(dict(action='apply', id=remote_row['id'],
                            expected_revision=remote_row['revision'], fields=patch), config)
        applied = reply.get('reminder')
        if not isinstance(applied, dict) or applied.get('id') != remote_row['id'] or _normalized(applied) != target:
            raise NativeError('write_not_verified')
        remote_row = applied
    # Re-read the local row rather than writing the stale initial snapshot.
    current = _local_rows().get(key)
    if not current or current.get('deleted') or current['revision'] != local_row['revision']:
        raise NativeError('local_conflict')
    if _normalized(current) != target:
        current = _save_local({**current, **target}, current, remote_row)
    # Advance only after BOTH durable sides are verified. Partial writes retain
    # the old baseline so the next pass can merge or raise a genuine conflict.
    if _normalized(current) != target:
        raise NativeError('write_not_verified')
    _link(db, key, remote_row['id'], target, target, external_id=remote_row.get('external_id'))
    _clear_issue(db, key)
    _clear_issue(db, 'apple:' + remote_row['id'])
    return 'updated' if local != target or patch else 'unchanged'


def _friendly_error(error):
    code = error.code if isinstance(error, NativeError) else 'error'
    if code in ('not_authorized', 'permission_denied', 'denied', 'restricted', 'permission_required'):
        return 'waiting_permission', '请在 iMac 上允许天工录访问提醒事项'
    if code == 'completion_upgrade_required':
        return 'attention', '正在等待完成时间功能升级，已保留苹果原始完成时间，暂缓导入'
    if code == 'completion_time_unknown':
        return 'attention', '苹果未提供该事项的完成时间，暂缓导入，避免误记为今天完成'
    if code == 'native_uncertain':
        return 'attention', '正在等待苹果连接程序的结果，已保留请求，不重复发送同一项写入'
    if code in ('conflict', 'revision_conflict', 'local_conflict'):
        return 'attention', '事项刚刚被修改，下一轮将重新核对'
    if code in ('calendar_missing', 'calendar_not_found', 'calendar_readonly'):
        return 'error', '已连接清单不可用，同步已暂停'
    return 'error', '提醒事项连接暂不可用，原有资料已保留，稍后自动重试'


def _cycle(config, resolution=None):
    previous = status()
    _write_json('status.json', {**previous, 'enabled': True, 'state': 'syncing', 'message': '正在同步提醒事项'})
    remote_rows = _snapshot(config)  # Failure must never be interpreted as zero items.
    local_rows = _local_rows()
    counts = dict(created_apple=0, imported=0, updated=0, unchanged=0, paused=0, conflicts=0)
    with _db() as db:
        links = {r['todo_id']: dict(r) for r in db.execute('SELECT * FROM links')}
        sync_ids = {}
        for row in remote_rows.values():
            if row.get('sync_id'):
                sync_ids.setdefault(row['sync_id'], []).append(row)
        # Native IDs can change after an account refresh. Reassociate only by a
        # unique owned sync marker or the stored external identifier, never title.
        identities = {r['todo_id']: r['external_id'] for r in db.execute('SELECT * FROM identities')}
        linked_ids_before = {r['remote_id'] for r in links.values() if r['remote_id']}
        for key, link in links.items():
            if link['remote_id'] and link['remote_id'] not in remote_rows:
                candidates = sync_ids.get(key, [])
                if not candidates and identities.get(key):
                    candidates = [r for r in remote_rows.values() if r.get('external_id') == identities[key]]
                if len(candidates) == 1 and candidates[0]['id'] not in linked_ids_before:
                    row = candidates[0]
                    link['remote_id'] = row['id']
                    linked_ids_before.add(row['id'])
                    _link(db, key, row['id'], json.loads(link['base_local']), json.loads(link['base_remote']), external_id=row.get('external_id'))
        # Recover native writes whose result was lost before persisting the ID.
        for key, link in links.items():
            if link['phase'] == 'creating' and not link['remote_id']:
                candidates = sync_ids.get(key, [])
                if len(candidates) == 1:
                    link['remote_id'] = candidates[0]['id']
                    _link(db, key, link['remote_id'], json.loads(link['base_local']), json.loads(link['base_remote']))
                elif len(candidates) > 1:
                    _issue(db, key, key, local_rows.get(key, {}).get('title'), '苹果存在重复关联，已暂停，避免合并错误事项', 'duplicate')
        if resolution:
            todo_id, choice, token = resolution
            link = links.get(todo_id)
            if not link:
                raise ValueError('同步关联已变化，请刷新后重新选择')
            outcome = _sync_link(db, link, local_rows.get(todo_id), remote_rows.get(link['remote_id']), config, (choice, token))
            if outcome in ('paused', 'conflict'):
                raise ValueError('事项当前无法解决冲突，请先检查同步提示')
            counts[outcome] += 1
        else:
            # Create local export intents BEFORE the external side effect.
            for key, local in local_rows.items():
                if key not in links and not local.get('deleted'):
                    try:
                        canonical = _normalized(local)
                        _link(db, key, None, canonical, canonical, 'creating')
                        links[key] = dict(todo_id=key, remote_id=None, base_local=_json(canonical), base_remote=_json(canonical), phase='creating')
                    except ValueError as exc:
                        _issue(db, key, key, local.get('title'), str(exc), 'unsupported')
            for key, link in links.items():
                local = local_rows.get(key)
                try:
                    if link['phase'] == 'importing' and local is None and remote_rows.get(link['remote_id']):
                        local = _save_local(dict(id=key, **json.loads(link['base_local']), deleted=0), remote=remote_rows[link['remote_id']])
                        local_rows[key] = local
                    if link['phase'] == 'creating' and not link['remote_id'] and local and not local.get('deleted'):
                        if len(sync_ids.get(key, [])) > 1:
                            counts['paused'] += 1
                            continue
                        reply = native(dict(action='create', sync_id=key, fields=json.loads(link['base_local'])), config)
                        row = reply.get('reminder')
                        if not isinstance(row, dict) or not row.get('id') or row.get('sync_id') != key:
                            raise NativeError('write_not_verified')
                        if row['id'] in {other['remote_id'] for otherkey, other in links.items() if otherkey != key}:
                            raise NativeError('duplicate_mapping')
                        _normalized(row)
                        link['remote_id'] = row['id']
                        remote_rows[row['id']] = row
                        _link(db, key, row['id'], json.loads(link['base_local']), json.loads(link['base_remote']), external_id=row.get('external_id'))
                        counts['created_apple'] += 1
                    outcome = _sync_link(db, link, local, remote_rows.get(link['remote_id']), config)
                    counts['conflicts' if outcome == 'conflict' else outcome] += 1
                except (NativeError, ValueError, sqlite3.Error) as exc:
                    _, message = _friendly_error(exc)
                    _issue(db, key, key, (local or {}).get('title'), message, 'retry')
                    counts['paused'] += 1
            linked_ids = {r['remote_id'] for r in db.execute('SELECT remote_id FROM links') if r['remote_id']}
            # Import only this bound calendar. Never match an unrelated title.
            for remote_id, remote in remote_rows.items():
                if remote_id in linked_ids:
                    continue
                issue_key = 'apple:' + remote_id
                if remote.get('sync_id') or (remote.get('external_id') and remote['external_id'] in identities.values()):
                    _issue(db, issue_key, '', remote.get('title'), '该苹果事项带有未匹配或重复关联，已暂停自动导入', 'unmatched')
                    continue
                if remote.get('recurrence') or remote.get('writable') is False:
                    _issue(db, issue_key, '', remote.get('title'), '苹果事项含重复规则，暂不支持同步', 'unsupported')
                    continue
                try:
                    canonical = _normalized(remote)
                    key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'tiangong-reminder:' + config['calendar_id'] + ':' + remote_id))
                    existing = _local_rows().get(key)
                    _link(db, key, remote_id, canonical, canonical, 'importing', remote.get('external_id'))
                    if existing is None:
                        _save_local(dict(id=key, **canonical, deleted=0), remote=remote)
                    _clear_issue(db, issue_key)
                    counts['imported'] += 1
                except (NativeError, ValueError, sqlite3.Error) as exc:
                    _issue(db, issue_key, '', remote.get('title'), str(exc) if isinstance(exc, ValueError) else _friendly_error(exc)[1], 'unsupported' if isinstance(exc, ValueError) else 'retry')
            # Drop issues for Apple-only items no longer visible. Linked missing
            # items retain their explicit paused issue instead of being deleted.
            for row in db.execute('SELECT issue_key FROM issues').fetchall():
                if row['issue_key'].startswith('apple:') and row['issue_key'][6:] not in remote_rows:
                    _clear_issue(db, row['issue_key'])
        issues = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM issues ORDER BY issue_key')]
    result = dict(enabled=True, state='attention' if issues else 'ok',
                  last_success=_now() if not issues else previous.get('last_success'), last_checked=_now(), last_error=None,
                  message='部分事项需要处理' if issues else '已与苹果提醒事项同步',
                  issues=issues, pending=len(issues), counts=counts)
    _write_json('status.json', result)
    return result


def _run(resolution=None):
    config = _config()
    if not config.get('enabled') or not config.get('calendar_id') or not config.get('helper_path'):
        if resolution:
            raise ValueError('尚未连接苹果提醒事项')
        return status()
    with _exclusive() as acquired:
        if not acquired:
            if resolution:
                raise ValueError('同步正在进行，请稍后重试')
            return status()
        try:
            return _cycle(config, resolution)
        except ValueError:
            if resolution:
                # Republish fresh conflicts collected before rejecting a stale UI.
                current = status()
                with _db() as db:
                    issues = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM issues')]
                current.update(state='attention', message='事项已变化，请刷新同步状态', issues=issues, pending=len(issues))
                _write_json('status.json', current)
                raise
            current = status()
            current.update(state='error', last_error=_now(), message='同步状态暂不可用，原有资料已保留，稍后自动重试')
            _write_json('status.json', current)
            return current
        except Exception as exc:
            state, message = _friendly_error(exc)
            current = status()
            current.update(enabled=True, state=state, last_error=_now(), message=message)
            _write_json('status.json', current)
            if resolution:
                raise ValueError(message) from exc
            return current


def run_once():
    return _run()


def resolve(todo_id, choice, token):
    if choice not in ('local', 'apple') or not isinstance(todo_id, str) or not todo_id or not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError('无效的冲突处理请求，请刷新后重试')
    if os.environ.get('TIANGONG_CLOUD') == '1':
        import cloud_reminders
        return cloud_reminders.queue(dict(action='resolve', todo_id=todo_id, choice=choice, token=token))
    return _run((todo_id, choice, token))


def wake():
    if os.environ.get("TIANGONG_CLOUD") == "1":
        import cloud_reminders
        return cloud_reminders.queue({"action": "sync"})
    _WAKE.set()
    return status()


def start(on_change=None):
    """Call only from the production server, never test servers or import."""
    if os.environ.get("TIANGONG_CLOUD") == "1":
        return
    global _WORKER, _ON_CHANGE
    with _START_LOCK:
        _ON_CHANGE = on_change
        if _WORKER and _WORKER.is_alive():
            return
        def worker():
            last_checked = status().get('last_checked')
            while True:
                _WAKE.clear()
                try:
                    result = run_once()
                    counts = result.get('counts', {})
                    checked = result.get('last_checked')
                    changed = checked and checked != last_checked
                    last_checked = checked
                    if changed and (counts.get('imported', 0) or counts.get('updated', 0)) and _ON_CHANGE:
                        _ON_CHANGE()
                except Exception:
                    pass  # run_once publishes expected failures; retry next time.
                _WAKE.wait(INTERVAL)
        _WORKER = threading.Thread(target=worker, name='tiangong-reminders-sync', daemon=True)
        _WORKER.start()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    enable = sub.add_parser('enable')
    enable.add_argument('--calendar-id', required=True)
    enable.add_argument('--helper-path', required=True)
    sub.add_parser('status')
    sub.add_parser('run')
    args = parser.parse_args()
    result = configure(args.calendar_id, args.helper_path) if args.action == 'enable' else run_once() if args.action == 'run' else status()
    print(json.dumps(result, ensure_ascii=False))
