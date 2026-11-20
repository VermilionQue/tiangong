"""Legrand receipts: exact cents, private evidence, revision-safe edits and trash."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo

import private_images
import tenant_storage


DB = tenant_storage.path('receipts.sqlite3')
BUSINESS = '客户业务'
MAX_CENTS = 99_999_999_999
EDITABLE = ('received_on', 'project', 'owner', 'amount_cents', 'address', 'photos', 'note', 'customer_id')


class ConflictError(ValueError):
    """The request or revision no longer describes the current receipt."""


def today_cn():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    # Resolve the tenant path once for the lifetime of this connection.
    db = Path(DB)
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(db), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        db.chmod(0o600)
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY, received_on TEXT NOT NULL,
            data TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0))''')
        con.execute('CREATE INDEX IF NOT EXISTS receipts_date ON receipts(received_on)')
        con.execute('''CREATE TABLE IF NOT EXISTS history (
            id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
            action TEXT NOT NULL, changed_at TEXT NOT NULL,
            PRIMARY KEY(id,revision), FOREIGN KEY(id) REFERENCES receipts(id))''')
        con.execute('''CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            response TEXT NOT NULL, created_at TEXT NOT NULL)''')
        con.commit()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def text(value, maximum, label, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label}最多{maximum}字')
    value = value.strip()
    if required and not value:
        raise ValueError(f'请填写{label}')
    return value


def calendar_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('收款日期应为YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError('收款日期无效') from None
    return value


def calendar_month(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', value):
        raise ValueError('月份应为YYYY-MM')
    calendar_day(value+'-01')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('收款编号无效')
    return value


def state(month=None, day=None):
    """An explicit day narrows the month. Trash is returned but excluded from totals."""
    if day is not None:
        day = calendar_day(day)
    month = calendar_month(month if month is not None else (day or today_cn())[:7])
    if day and not day.startswith(month+'-'):
        raise ValueError('收款日期与查询月份不一致')
    with connection() as con:
        if day:
            query = con.execute('SELECT data FROM receipts WHERE received_on=?', (day,))
        else:
            query = con.execute('SELECT data FROM receipts WHERE received_on>=? AND received_on<=?',
                                (month+'-01', month+'-31'))
        records = [json.loads(row['data']) for row in query]
    records.sort(key=lambda row: (row['received_on'], row['createdAt'], row['id']), reverse=True)
    active = [row for row in records if not row['deletedAt']]
    return {'business': BUSINESS, 'month': month, 'date': day or '', 'records': records,
            'total_cents': sum(row['amount_cents'] for row in active),
            'count': len(active), 'deleted_count': len(records)-len(active)}


def history(key):
    key = identifier(key)
    with connection() as con:
        current = con.execute('SELECT data FROM receipts WHERE id=?', (key,)).fetchone()
        if not current:
            raise ValueError('收款记录不存在')
        previous = [{'record': json.loads(row['data']), 'action': row['action'],
                     'changed_at': row['changed_at']}
                    for row in con.execute('SELECT * FROM history WHERE id=? ORDER BY revision DESC', (key,))]
    return {'record': json.loads(current['data']), 'history': previous}


def fields(data, old):
    defaults = {'received_on': today_cn(), 'project': '', 'owner': '', 'amount_cents': None,
                'address': '', 'photos': [], 'note': '', 'customer_id': ''}
    values = {key: data.get(key, old.get(key,defaults[key]) if old else defaults[key]) for key in EDITABLE}
    values['customer_id']=text(values['customer_id'],100,'关联客户')
    if values['customer_id']:
        import crm
        with crm.connection() as con: crm.record(con,values['customer_id'],'customer')
    values['received_on'] = calendar_day(values['received_on'])
    # This module records actual receipts, not future receivables.
    if values['received_on'] > today_cn():
        raise ValueError('收款日期不能晚于今天')
    for key, maximum, label in [('project', 200, '项目名称'), ('owner', 120, '业主'),
                                ('address', 500, '地址'), ('note', 5000, '备注')]:
        values[key] = text(values[key], maximum, label, required=key in ('project', 'owner'))
    amount = values['amount_cents']
    if type(amount) is not int or not 1 <= amount <= MAX_CENTS:
        raise ValueError('收款金额需为正整数分，最多999999999.99元')
    values['photos'] = private_images.validate_ids('receipts', values['photos'])
    return values


def request_identity(data):
    if not isinstance(data, dict):
        raise ValueError('收款请求格式无效')
    request_id = data.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
        raise ValueError('请提供唯一请求编号；网络重试应沿用同一编号')
    try:
        raw = json.dumps({key: value for key, value in data.items() if key != 'request_id'},
                         ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('收款请求包含无效数据') from None
    return request_id, hashlib.sha256(raw.encode('utf-8')).hexdigest()


def save(data, author='本人'):
    request_id, fingerprint = request_identity(data)
    if data.get('business', BUSINESS) != BUSINESS:
        raise ValueError('当月收款仅用于客户业务业务')
    action = data.get('action', 'save')
    if action not in ('save', 'delete', 'restore'):
        raise ValueError('收款操作无效')
    author = text(author, 100, '记录人', required=True)
    key = identifier(data['id'] if 'id' in data else str(uuid.uuid4()))
    revision = data.get('revision', 0)
    if type(revision) is not int or revision < 0:
        raise ValueError('记录版本无效')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        cached = con.execute('SELECT fingerprint,response FROM requests WHERE request_id=?', (request_id,)).fetchone()
        if cached:
            if cached['fingerprint'] != fingerprint:
                raise ConflictError('此请求编号已用于另一项修改，请使用新的请求编号')
            return json.loads(cached['response'])
        previous = con.execute('SELECT data FROM receipts WHERE id=?', (key,)).fetchone()
        old = json.loads(previous['data']) if previous else None
        if action != 'save' and not old:
            raise ConflictError('收款记录不存在，请重新读取')
        if revision != (old['revision'] if old else 0):
            raise ConflictError('这笔收款已在另一处修改，请重新读取后再保存')
        stamp = now()
        if action == 'save':
            if old and old['deletedAt']:
                raise ConflictError('这笔收款在回收站，请先恢复再编辑')
            values = fields(data, old)
            if old and all(values[field] == old.get(field, '' if field=='customer_id' else None) for field in EDITABLE):
                row = old
            else:
                row = {**values, 'id': key, 'business': BUSINESS,
                       'revision': (old['revision'] if old else 0)+1,
                       'createdAt': old['createdAt'] if old else stamp,
                       'updatedAt': stamp, 'author': author, 'deletedAt': ''}
        else:
            deleting = action == 'delete'
            row = old if bool(old['deletedAt']) == deleting else {
                **old, 'revision': old['revision']+1, 'updatedAt': stamp,
                'author': author, 'deletedAt': stamp if deleting else ''}
        if row != old:
            if old:
                con.execute('INSERT INTO history VALUES (?,?,?,?,?)',
                            (key, old['revision'], json.dumps(old, ensure_ascii=False), action, stamp))
            con.execute('''INSERT INTO receipts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                received_on=excluded.received_on,data=excluded.data,revision=excluded.revision''',
                        (key, row['received_on'], json.dumps(row, ensure_ascii=False), row['revision']))
        con.execute('INSERT INTO requests VALUES (?,?,?,?)',
                    (request_id, fingerprint, json.dumps(row, ensure_ascii=False), stamp))
    return row

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'receipts')
