"""One recoverable personal visit per catalogue place, outside synced storage."""
from contextlib import contextmanager
from datetime import date, datetime
import json
from pathlib import Path
import tenant_storage
import re
import sqlite3
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DB = tenant_storage.path('travel.sqlite3')
CATALOG = ROOT / 'src/web/assets/travel-places.json'
SCOPES = ('domestic', 'overseas')
PHOTO_KEY = re.compile(r'[0-9a-f]{64}\.(?:jpg|png|webp)')


class ConflictError(ValueError):
    """A changed visit requires a fresh revision before it can be replaced."""


def catalogue():
    try:
        data = json.loads(CATALOG.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('places'), list):
            raise ValueError()
        places = {}
        for item in data['places']:
            if not isinstance(item, dict):
                raise ValueError()
            scope, key, name = item.get('scope'), item.get('id'), item.get('name')
            if (scope not in SCOPES or not isinstance(key, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', key)
                    or not isinstance(name, str) or not name.strip() or len(name) > 100
                    or (scope, key) in places):
                raise ValueError()
            places[scope, key] = {'id': key, 'scope': scope, 'name': name}
        if not places:
            raise ValueError()
        return data.get('version', 1), places
    except (OSError, ValueError, TypeError):
        raise ValueError('旅行地点列表暂时不可用，请稍后重试') from None


@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(DB), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        DB.chmod(0o600)
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS visits (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, place_id TEXT NOT NULL,
            place_name TEXT NOT NULL, visited_on TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision > 0),
            UNIQUE(scope, place_id))''')
        con.execute('''CREATE TABLE IF NOT EXISTS history (
            id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(id, revision))''')
        if 'photos' not in {row['name'] for row in con.execute('PRAGMA table_info(visits)')}:
            con.execute("ALTER TABLE visits ADD COLUMN photos TEXT NOT NULL DEFAULT '[]'")
        con.commit()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def listing():
    """Return cancelled visits too, so clients can restore their saved details."""
    with connection() as con:
        return [visit_row(row) for row in con.execute(
            'SELECT * FROM visits ORDER BY updated_at DESC, scope, place_id')]


def visit_row(row):
    result = dict(row)
    result['photos'] = json.loads(result.get('photos', '[]'))
    return result


def photo_ids(value):
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('每处旅行回忆最多保存8张照片')
    if any(not isinstance(key, str) or not PHOTO_KEY.fullmatch(key) for key in value):
        raise ValueError('请选择已上传的旅行照片，不能填写路径或外部网址')
    if len(value) != len(set(value)):
        raise ValueError('同一张照片不需要重复添加')
    return list(value)


def state():
    version, _ = catalogue()
    visits = listing()
    return {'visits': visits, 'catalog_version': version,
            'counts': {scope: sum(row['scope'] == scope and not row['deleted']
                                  for row in visits) for scope in SCOPES}}


def text_field(value, maximum, label):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label}最多{maximum}字')
    return value.strip()


def validate_visit_date(value):
    """Keep the recorded precision; compare only the supplied date components."""
    error = '到访日期可填YYYY、YYYY-MM或YYYY-MM-DD，也可以留空'
    if not isinstance(value, str):
        raise ValueError(error)
    if not value:
        return value
    if not re.fullmatch(r'[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?', value):
        raise ValueError(error)
    parts = tuple(int(part) for part in value.split('-'))
    try:
        if parts[0] < 1 or (len(parts) > 1 and not 1 <= parts[1] <= 12):
            raise ValueError()
        if len(parts) == 3:
            date(*parts)
    except ValueError:
        raise ValueError('到访日期无效，请检查年份、月份或日期') from None
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if parts > (today.year, today.month, today.day)[:len(parts)]:
        raise ValueError('到访日期不能在未来；暂时记不清可以留空')
    return value


def visit_fields(data, old):
    def field(key):
        return data.get(key, old[key] if old else '')
    visited_on = validate_visit_date(field('visited_on'))
    return {'visited_on': visited_on,
            'city': text_field(field('city'), 80, '城市'),
            'note': text_field(field('note'), 2000, '旅行小记'),
            'photos': photo_ids(data.get('photos', old['photos'] if old else []))}


def save(data):
    """Save/soft-delete/restore a place with optimistic, retry-safe revisions.

    Only explicit restore can reactivate a cancelled visit. A restore may also
    supply edited details; omitted details are retained for all existing rows.
    Exact retries return the existing row without adding history or revisions.
    """
    if not isinstance(data, dict):
        raise ValueError('旅行记录格式无效')
    scope, place_id = data.get('scope'), data.get('place_id')
    if scope not in SCOPES or not isinstance(place_id, str):
        raise ValueError('请选择有效地点')
    _, places = catalogue()
    place = places.get((scope, place_id))
    if not place:
        raise ValueError('请选择地点列表中的地点')
    key = scope + ':' + place_id
    if 'id' in data and data['id'] != key:
        raise ValueError('地点与记录编号不一致')
    action = data.get('action', 'save')
    if action not in ('save', 'delete', 'restore'):
        raise ValueError('旅行操作无效')
    revision = data.get('revision')
    if revision is not None and (type(revision) is not int or revision < 0):
        raise ValueError('记录版本无效，请刷新后重试')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        previous = con.execute('SELECT * FROM visits WHERE id=?', (key,)).fetchone()
        old = visit_row(previous) if previous else None
        if not old and (action != 'save' or revision not in (None, 0)):
            raise ConflictError('原旅行记录不存在，请刷新后重试')
        if old and old['deleted'] and action == 'save':
            raise ConflictError('这个地点已取消点亮，请先恢复记录')
        # Cancelling a place must not erase or replace the original description.
        fields = ({k: old[k] for k in ('visited_on', 'city', 'note', 'photos')}
                  if action == 'delete' else visit_fields(data, old))
        fields.update(deleted=int(action == 'delete'))
        if old and all(old[name] == value for name, value in fields.items()):
            return old
        if old and revision != old['revision']:
            raise ConflictError('这份旅行记录已在另一处修改，请刷新后重试')
        now = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        row = {'id': key, 'scope': scope, 'place_id': place_id,
               'place_name': place['name'], **fields,
               'created_at': old['created_at'] if old else now,
               'updated_at': now, 'revision': old['revision'] + 1 if old else 1}
        if old:
            con.execute('INSERT INTO history (id,revision,data) VALUES (?,?,?)',
                        (key, old['revision'], json.dumps(old, ensure_ascii=False)))
        con.execute('''INSERT INTO visits
            (id,scope,place_id,place_name,visited_on,city,note,deleted,created_at,updated_at,revision,photos)
            VALUES (:id,:scope,:place_id,:place_name,:visited_on,:city,:note,:deleted,:created_at,:updated_at,:revision,:photos)
            ON CONFLICT(id) DO UPDATE SET place_name=excluded.place_name,
            visited_on=excluded.visited_on,city=excluded.city,note=excluded.note,
            deleted=excluded.deleted,updated_at=excluded.updated_at,revision=excluded.revision,
            photos=excluded.photos''', {**row, 'photos': json.dumps(row['photos'])})
        return row

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'travel')
