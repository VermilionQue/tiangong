"""Personal ledger: integer cents, immutable import evidence and revision history."""
from contextlib import contextmanager
from datetime import date, datetime
import json
import hashlib
from pathlib import Path
import tenant_storage
import re
import sqlite3
import uuid

BASE = tenant_storage.path('')
DB = BASE / 'finance.sqlite3'
TYPES = ('income', 'expense', 'refund', 'transfer', 'financing', 'excluded', 'review')
TRACKS = ('daily', 'fudai', 'smart_home', 'zhejiang_tv', 'stocks', 'qqsg', 'unknown')
BUDGET_CATEGORIES = ('买菜做饭', '咖啡饮料', '下馆子', '知识付费', '医疗买药', '日用百货', '电子数码', '娱乐')


class ConflictError(ValueError):
    pass


@contextmanager
def connection():
    db = Path(DB)  # Resolve the account once per connection, never mutate DB.
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        db.chmod(0o600)
        con.executescript('''CREATE TABLE IF NOT EXISTS entries (
          id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS history (
          id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(id, revision));
          CREATE TABLE IF NOT EXISTS imports (
          digest TEXT PRIMARY KEY, metadata TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evidence (
          id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS budgets (
          month TEXT NOT NULL, category TEXT NOT NULL, cents INTEGER NOT NULL,
          PRIMARY KEY(month, category));
          CREATE TABLE IF NOT EXISTS ledger_requests (
          request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);''')
        con.commit()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def text(value, limit, label, required=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'{label}格式无效或超出长度')
    value = value.strip()
    if required and not value:
        raise ValueError(f'请填写{label}')
    return value


def raw_text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'{label}格式无效或超出长度')
    return value


def optional_id(value):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,100}', value):
        raise ValueError('账户编号无效')
    return value


def fields(data, old=None):
    day = data.get('date')
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise ValueError('请填写完整交易日期')
    date.fromisoformat(day)
    cents = data.get('cents')
    kind = data.get('type')
    if kind not in TYPES:
        raise ValueError('请选择有效的收支类型')
    if type(cents) is not int or not 0 <= cents <= 99999999999 or (cents == 0 and kind not in ('excluded', 'review')):
        raise ValueError('金额需为正数，最多两位小数；零额原始记录请保留为待核对')
    old = old or {}
    track = data.get('track', old.get('track', 'unknown'))
    budget_category = data.get('budget_category', old.get('budget_category', 'unknown'))
    if track not in TRACKS or budget_category not in (*BUDGET_CATEGORIES, 'unknown'):
        raise ValueError('业务方向或预算分类无效；尚不明确请保留unknown')
    confirmed = data.get('confirmed', old.get('confirmed', True))
    if type(confirmed) is not bool:
        raise ValueError('账目确认状态无效')
    account_id = optional_id(data.get('account_id', old.get('account_id')))
    to_account_id = optional_id(data.get('to_account_id', old.get('to_account_id')))
    if kind != 'transfer' and to_account_id:
        raise ValueError('只有转账可以填写转入账户')
    if kind == 'transfer' and account_id and account_id == to_account_id:
        raise ValueError('转出和转入账户不能相同')
    return dict(date=day, cents=cents, type=kind,
                name=text(data.get('name'), 300, '交易对象或名称', True),
                category=text(data.get('category'), 100, '分类', True),
                subcategory=text(data.get('subcategory', ''), 100, '二级分类'),
                account=text(data.get('account', ''), 200, '账户'),
                note=text(data.get('note', ''), 10000, '备注'),
                channel=text(data.get('channel', old.get('channel', '')), 200, '付款渠道'),
                account_id=account_id, to_account_id=to_account_id,
                track=track, budget_category=budget_category, confirmed=confirmed,
                source_text=raw_text(data.get('source_text', old.get('source_text', '')), 50000, '来源原文'))


def state():
    with connection() as con:
        records = [json.loads(r['data']) for r in con.execute('SELECT data FROM entries')]
        records = [row for row in records if not row.get('voidedAt')]
        imports = [{k: v for k, v in json.loads(r[0]).items()
                    if k in ('filename', 'version', 'start_date', 'end_date', 'sha256', 'count', 'notes')}
                   for r in con.execute('SELECT metadata FROM imports')]
        budgets = [dict(r) for r in con.execute('SELECT * FROM budgets ORDER BY month,category')]
    return {'records': sorted(records, key=lambda r: (r['date'], r['id']), reverse=True),
            'imports': imports, 'budgets': budgets}


def save(data):
    if not isinstance(data, dict):
        raise ValueError('账目格式无效')
    if data.get('action'):
        raise ValueError('不支持的账本操作')
    key = data.get('id') or str(uuid.uuid4())
    if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,100}', key):
        raise ValueError('记录编号无效')
    revision = data.get('revision', 0)
    if type(revision) is not int or revision < 0:
        raise ValueError('记录版本无效')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        request_id = data.get('request_id')
        if request_id is not None:
            if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{8,100}', request_id):
                raise ValueError('请求编号无效')
            fingerprint = hashlib.sha256(json.dumps({k:v for k,v in data.items() if k != 'request_id'},
                                       ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
            cached = con.execute('SELECT fingerprint,response FROM ledger_requests WHERE request_id=?', (request_id,)).fetchone()
            if cached:
                if cached['fingerprint'] != fingerprint:
                    raise ConflictError('请求编号已用于另一笔操作')
                return json.loads(cached['response'])
        found = con.execute('SELECT data FROM entries WHERE id=?', (key,)).fetchone()
        old = json.loads(found[0]) if found else None
        if old and (old.get('managed_by') == 'debt_payment' or old.get('voidedAt')):
            raise ValueError('还款关联账目请在负债页修改或撤销')
        values = fields(data, old)
        direction = {'income': 'in', 'refund': 'in', 'expense': 'out'}.get(values['type'], data.get('direction', old.get('direction', 'unknown') if old else 'unknown'))
        if direction not in ('in', 'out', 'zero', 'unknown'):
            raise ValueError('资金方向无效')
        if not values['cents']:
            direction = 'zero'
        elif direction == 'zero':
            raise ValueError('非零金额需选择资金方向')
        if old and all(old.get(k, '') == v for k, v in values.items()) and direction == old.get('direction'):
            if request_id is not None:
                con.execute('INSERT INTO ledger_requests VALUES(?,?,?)',
                            (request_id, fingerprint, json.dumps(old, ensure_ascii=False)))
            return old
        if revision != (old['revision'] if old else 0):
            raise ConflictError('这笔记录已更新，请关闭后重新打开，避免覆盖其他设备的修改')
        if old:
            con.execute('INSERT INTO history VALUES (?,?,?)', (key, old['revision'], json.dumps(old, ensure_ascii=False)))
        row = {**(old or {}), **values, 'id': key, 'revision': revision+1,
               'updatedAt': datetime.now().astimezone().isoformat()}
        row['direction'] = direction
        row['signed_cents'] = values['cents'] if direction == 'in' else -values['cents'] if direction == 'out' else 0 if direction == 'zero' else None
        if not old:
            row.update(source='manual', groups=[], reason='本人记录', source_row=None)
        else:
            row['reason'] = '本人修改；原表和修改历史已保留'
        con.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?)', (key, json.dumps(row, ensure_ascii=False), row['revision']))
        if request_id is not None:
            con.execute('INSERT INTO ledger_requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(row, ensure_ascii=False)))
    return row


def import_ledger(payload):
    """Import a verified private staging file once; no remote import endpoint."""
    metadata, records = payload['metadata'], payload['records']
    digest = metadata['sha256']
    if not re.fullmatch('[0-9a-f]{64}', digest) or not records:
        raise ValueError('导入文件校验信息无效')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('来源编号重复')
    checked = []
    for row in records:
        if not isinstance(row.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,100}', row['id']):
            raise ValueError('来源编号无效')
        values = fields(row)
        signed = row.get('signed_cents')
        if type(signed) is not int or abs(signed) != values['cents']:
            raise ValueError('原始金额方向与金额不一致')
        direction = 'in' if signed > 0 else 'out' if signed < 0 else 'zero'
        if row.get('direction') != direction or (values['type'] in ('income', 'refund') and direction != 'in') or (values['type'] == 'expense' and direction != 'out'):
            raise ValueError('原始资金方向与收支类型不一致')
        checked.append({**row, **values, 'revision': 1, 'source': digest})
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM imports WHERE digest=?', (digest,)).fetchone():
            return {'imported': 0, 'already_imported': True}
        if con.execute('SELECT 1 FROM imports').fetchone():
            raise ValueError('已有历史账本，请先对照差异，避免不同版本重复入账')
        for row in checked:
            evidence = row.pop('evidence', {})
            con.execute('INSERT INTO entries VALUES (?,?,?)', (row['id'], json.dumps(row, ensure_ascii=False), 1))
            con.execute('INSERT INTO evidence VALUES (?,?)', (row['id'], json.dumps(evidence, ensure_ascii=False)))
        con.execute('INSERT INTO imports VALUES (?,?)', (digest, json.dumps(metadata, ensure_ascii=False)))
    return {'imported': len(checked), 'already_imported': False}

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'finance')
