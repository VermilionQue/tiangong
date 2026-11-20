"""Private belongings, recoverable edits and exactly-once use check-ins."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import tenant_storage
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo


DB = tenant_storage.path('treasures.sqlite3')
ICONS = ('sparkle', 'laptop', 'phone', 'camera', 'headphones', 'watch',
         'bag', 'shoes', 'home', 'book', 'bike', 'other')
MAX_CENTS = 99999999999
MAX_USES = 1000000000
PHOTO_KEY = re.compile(r'[0-9a-f]{64}\.(?:jpg|png|webp)')
EDITABLE = ('name', 'brand', 'category', 'purchased_on', 'purchase_cents', 'icon',
            'photo', 'note', 'warranty_until', 'uses', 'status', 'sold_on', 'sale_cents')


class ConflictError(ValueError):
    """The caller must refresh a revision or use a fresh logical request ID."""


def today_cn():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(DB), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        DB.chmod(0o600)
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS treasures (
            id TEXT PRIMARY KEY, data TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0))''')
        con.execute('''CREATE TABLE IF NOT EXISTS history (
            id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
            action TEXT NOT NULL, changed_at TEXT NOT NULL,
            PRIMARY KEY(id,revision), FOREIGN KEY(id) REFERENCES treasures(id))''')
        con.execute('''CREATE TABLE IF NOT EXISTS use_events (
            request_id TEXT PRIMARY KEY, treasure_id TEXT NOT NULL,
            used_on TEXT NOT NULL, uses_before INTEGER NOT NULL,
            uses_after INTEGER NOT NULL, revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, FOREIGN KEY(treasure_id) REFERENCES treasures(id))''')
        con.execute('''CREATE TABLE IF NOT EXISTS requests (
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            operation TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL)''')
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


def calendar_day(value, label, optional=True):
    if optional and value == '':
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError(f'{label}应为YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{label}无效') from None
    return value


def cents(value, label):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_CENTS:
        raise ValueError(f'{label}需为非负整数分；未知请留空')
    return value


def photo_key(value):
    if not isinstance(value, str) or (value and not PHOTO_KEY.fullmatch(value)):
        raise ValueError('请选择已上传的宝物照片，不能填写路径或外部网址')
    return value


def fields(data, old, today):
    defaults = {'name': '', 'brand': '', 'category': '', 'purchased_on': '',
                'purchase_cents': None, 'icon': 'sparkle', 'photo': '', 'note': '',
                'warranty_until': '', 'uses': 0, 'status': 'active', 'sold_on': '',
                'sale_cents': None}
    values = {key: data.get(key, old[key] if old else defaults[key]) for key in EDITABLE}
    for key, maximum, label in [('name', 120, '宝物名称'), ('brand', 100, '品牌'),
                                ('category', 80, '分类'), ('note', 3000, '备注')]:
        values[key] = text(values[key], maximum, label, required=key == 'name')
    for key, label in [('purchased_on', '购买日期'), ('sold_on', '售出日期'),
                       ('warranty_until', '保修到期日')]:
        values[key] = calendar_day(values[key], label)
    values['purchase_cents'] = cents(values['purchase_cents'], '购入金额')
    values['sale_cents'] = cents(values['sale_cents'], '卖出金额')
    values['photo'] = photo_key(values['photo'])
    if values['icon'] not in ICONS:
        raise ValueError('请选择有效图标')
    if type(values['uses']) is not int or not 0 <= values['uses'] <= MAX_USES:
        raise ValueError('使用次数需为0至1000000000的整数')
    if values['status'] not in ('active', 'sold'):
        raise ValueError('宝物状态无效')
    purchased, sold, warranty = (values[key] for key in ('purchased_on', 'sold_on', 'warranty_until'))
    if purchased and purchased > today.isoformat():
        raise ValueError('购买日期不能晚于今天')
    if warranty and purchased and warranty < purchased:
        raise ValueError('保修到期日不能早于购买日期')
    if values['status'] == 'sold':
        if not sold:
            raise ValueError('已售出的宝物请填写售出日期')
        if sold > today.isoformat() or (purchased and sold < purchased):
            raise ValueError('售出日期不能早于购买日期或晚于今天')
    elif sold or values['sale_cents'] is not None:
        raise ValueError('持有中的宝物不能填写售出资料；恢复持有请清空售出日期和卖出金额')
    return values


def metrics(row, today=None):
    """Amounts stay in cents; the two decimal strings are display-only rates."""
    if today is None:
        today = today_cn()
    elif isinstance(today, str):
        today = date.fromisoformat(calendar_day(today, '计算日期', optional=False))
    if type(today) is not date:
        raise ValueError('计算日期需为日期，不含时刻')
    purchase, sale = row.get('purchase_cents'), row.get('sale_cents')
    sold = row.get('status') == 'sold'
    net = None if purchase is None or (sold and sale is None) else purchase - (sale if sold else 0)
    purchased = row.get('purchased_on')
    end = date.fromisoformat(row['sold_on']) if sold else today
    days = (end - date.fromisoformat(purchased)).days + 1 if purchased else None
    if days is not None and days < 1:
        days = None
    def average(divisor):
        if net is None or divisor is None or divisor == 0:
            return None
        amount = (Decimal(net) / Decimal(divisor)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
        return '0' if not amount else format(amount, 'f').rstrip('0').rstrip('.')
    return {'owned_days': days, 'net_cents': net,
            'daily_cost_cents': average(days), 'per_use_cost_cents': average(row.get('uses', 0))}


def expose(row, today=None):
    return {**row, 'metrics': metrics(row, today)}


def state():
    today = today_cn()
    with connection() as con:
        records = [json.loads(row['data']) for row in con.execute('SELECT data FROM treasures')]
    records.sort(key=lambda row: (row['updatedAt'], row['id']), reverse=True)
    return {'records': [expose(row, today) for row in records]}


def request_identity(data, operation):
    if not isinstance(data, dict):
        raise ValueError('宝物请求格式无效')
    request_id = data.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', request_id):
        raise ValueError('请提供唯一请求编号；网络重试应沿用同一编号')
    try:
        raw = json.dumps({key: value for key, value in data.items() if key != 'request_id'},
                         ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('宝物请求包含无效数据') from None
    fingerprint = hashlib.sha256((operation+'\n'+raw).encode('utf-8')).hexdigest()
    return request_id, fingerprint


def previous_request(con, request_id, fingerprint):
    previous = con.execute('SELECT fingerprint,response FROM requests WHERE request_id=?', (request_id,)).fetchone()
    if not previous:
        return None
    if previous['fingerprint'] != fingerprint:
        raise ConflictError('此请求编号已用于另一项修改，请使用新的请求编号')
    return json.loads(previous['response'])


def existing(con, data, required=False):
    key = data.get('id')
    if key is None and not required:
        key = str(uuid.uuid4())
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
        raise ValueError('宝物编号无效')
    revision = data.get('revision', 0)
    if type(revision) is not int or revision < 0:
        raise ValueError('记录版本无效')
    previous = con.execute('SELECT data FROM treasures WHERE id=?', (key,)).fetchone()
    old = json.loads(previous['data']) if previous else None
    if required and not old:
        raise ConflictError('宝物记录不存在，请重新读取')
    if revision != (old['revision'] if old else 0):
        raise ConflictError('这件宝物已在另一处修改，请重新读取后再保存')
    return key, old


def commit_record(con, row, old, action, request_id, fingerprint):
    if old and row != old:
        con.execute('INSERT INTO history VALUES (?,?,?,?,?)',
                    (old['id'], old['revision'], json.dumps(old, ensure_ascii=False), action, row['updatedAt']))
    if row != old:
        con.execute('''INSERT INTO treasures VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET
            data=excluded.data,revision=excluded.revision''',
                    (row['id'], json.dumps(row, ensure_ascii=False), row['revision']))
    con.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                (request_id, fingerprint, action, json.dumps(row, ensure_ascii=False), now()))


def save(data):
    request_id, fingerprint = request_identity(data, 'save')
    action = data.get('action', 'save')
    if action not in ('save', 'delete', 'restore'):
        raise ValueError('宝物操作无效')
    today = today_cn()
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        cached = previous_request(con, request_id, fingerprint)
        if cached is not None:
            return expose(cached, today)
        key, old = existing(con, data, required=action != 'save')
        if action == 'save':
            if old and old['deletedAt']:
                raise ConflictError('这件宝物在回收站，请先恢复再编辑')
            values = fields(data, old, today)
            unchanged = old and all(old[field] == values[field] for field in EDITABLE)
            if unchanged:
                row = old
            else:
                stamp = now()
                row = {**values, 'id': key, 'revision': (old['revision'] if old else 0)+1,
                       'createdAt': old['createdAt'] if old else stamp,
                       'updatedAt': stamp, 'deletedAt': ''}
        else:
            # Trash actions preserve every descriptive field, including photos
            # and sale details; caller-supplied editable fields are ignored.
            deleting = action == 'delete'
            if bool(old['deletedAt']) == deleting:
                row = old
            else:
                stamp = now()
                row = {**old, 'revision': old['revision']+1, 'updatedAt': stamp,
                       'deletedAt': stamp if deleting else ''}
        commit_record(con, row, old, action, request_id, fingerprint)
        return expose(row, today)


def use(data):
    request_id, fingerprint = request_identity(data, 'use')
    if data.get('action', 'use') != 'use':
        raise ValueError('使用打卡操作无效')
    today = today_cn()
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        # Check the original request before resolving an omitted date. A retry
        # after midnight must not produce another use event or change its date.
        cached = previous_request(con, request_id, fingerprint)
        if cached is not None:
            return expose(cached, today)
        key, old = existing(con, data, required=True)
        if old['deletedAt'] or old['status'] != 'active':
            raise ConflictError('已售出或在回收站的宝物不能新增使用打卡')
        used_on = calendar_day(data.get('date', today.isoformat()), '使用日期', optional=False)
        if used_on > today.isoformat() or (old['purchased_on'] and used_on < old['purchased_on']):
            raise ValueError('使用日期不能早于购买日期或晚于今天')
        if old['uses'] >= MAX_USES:
            raise ValueError('使用次数已达到可记录上限')
        stamp = now()
        row = {**old, 'uses': old['uses']+1, 'revision': old['revision']+1, 'updatedAt': stamp}
        con.execute('INSERT INTO use_events VALUES (?,?,?,?,?,?,?)',
                    (request_id, key, used_on, old['uses'], row['uses'], row['revision'], stamp))
        commit_record(con, row, old, 'use', request_id, fingerprint)
        return expose(row, today)

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'treasures')
