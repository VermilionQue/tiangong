"""Shared daily check-ins; browser migration only fills previously empty dates."""
from contextlib import contextmanager
from datetime import date, datetime
import json
from pathlib import Path
import tenant_storage
import re
import sqlite3
from zoneinfo import ZoneInfo


DB = tenant_storage.path('checkins.sqlite3')
FIELDS = ('exercise', 'notes', 'work', 'note')


class ConflictError(ValueError):
    """An existing day changed after the caller read it."""


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(DB), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        DB.chmod(0o600)
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS days (
            date TEXT PRIMARY KEY, record TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0))''')
        con.execute('''CREATE TABLE IF NOT EXISTS history (
            date TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL,
            PRIMARY KEY(date, revision))''')
        con.commit()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def state():
    with connection() as con:
        return {'days': {row['date']: json.loads(row['record']) for row in
                         con.execute('SELECT date,record FROM days ORDER BY date')}}


def day_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('打卡日期应为 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('打卡日期无效') from None
    if parsed > datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('请选择今天或过去的日期')
    return value


def counter(value, maximum, label, nullable=True):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'{label}需为0至{maximum}的整数')
    return value


def text(value, maximum, label):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label}最多{maximum}字')
    return value.strip()


def fields(value):
    """Each request is a complete day snapshot, with null distinct from zero."""
    if not isinstance(value, dict):
        raise ValueError('打卡内容格式无效')
    exercise = counter(value.get('exercise'), 1440, '锻炼分钟')
    notes = counter(value.get('notes'), 999, '笔记篇数')
    note = text(value.get('note', ''), 300, '当天小记')
    rows = value.get('work', [])
    if not isinstance(rows, list) or len(rows) > 32:
        raise ValueError('每天最多记录32份工作')
    work, names = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('工作记录格式无效')
        name = text(row.get('name'), 40, '工作名称')
        if not name:
            raise ValueError('请填写工作名称')
        minutes = counter(row.get('minutes'), 1440, '工作分钟', nullable=False)
        if name.casefold() in names:
            raise ValueError('同一份工作请合并分钟数，避免重复计算')
        names.add(name.casefold())
        work.append({'name': name, 'minutes': minutes})
    if exercise is None and notes is None and not work and not note:
        raise ValueError('先记一件今天做过的事吧')
    return {'exercise': exercise, 'notes': notes, 'work': work, 'note': note}


def save(data):
    if not isinstance(data, dict):
        raise ValueError('打卡请求格式无效')
    day = day_value(data.get('date'))
    record = fields(data.get('record'))
    expected = data.get('expected_revision')
    if type(expected) is not int or expected < 0:
        raise ValueError('请提供有效的打卡版本，新记录使用0')
    importing = data.get('import_if_absent', False)
    if type(importing) is not bool or (importing and expected != 0):
        raise ValueError('旧记录迁移需使用新建版本0')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT record,revision FROM days WHERE date=?', (day,)).fetchone()
        old = json.loads(row['record']) if row else None
        equal = old is not None and all(old[key] == record[key] for key in FIELDS)
        if importing and old:
            return {'date': day, 'record': old, 'changed': False,
                    'imported': False, 'skipped': True, 'different': not equal}
        # Retrying an acknowledged or uncertain identical save adds no history.
        if equal:
            return {'date': day, 'record': old, 'changed': False}
        if expected != (row['revision'] if row else 0):
            raise ConflictError('这一天已在另一台设备修改；你的填写仍保留，请先查看最新记录')
        now = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        record.update(revision=expected + 1, source='legacy-local' if importing else 'manual',
                      createdAt=old['createdAt'] if old else now, updatedAt=now)
        if old and 'legacyUpdatedAt' in old:
            record['legacyUpdatedAt'] = old['legacyUpdatedAt']
        if importing:
            original_time = data['record'].get('updatedAt')
            if isinstance(original_time, str) and len(original_time) <= 64 and '\x00' not in original_time:
                record['legacyUpdatedAt'] = original_time
        if old:
            con.execute('INSERT INTO history VALUES (?,?,?)',
                        (day, old['revision'], json.dumps(old, ensure_ascii=False)))
        con.execute('''INSERT INTO days VALUES (?,?,?) ON CONFLICT(date) DO UPDATE SET
            record=excluded.record,revision=excluded.revision''',
                    (day, json.dumps(record, ensure_ascii=False), record['revision']))
        result = {'date': day, 'record': record, 'changed': True}
        if importing:
            result.update(imported=True, skipped=False, different=False)
        return result

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'checkins')
