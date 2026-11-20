"""Personal game library; Steam's documented, read-only Web API is optional.

No account cookies are collected. Secrets and active data live outside the
Synology source checkout. Public callers must be authenticated by the gateway.
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
import tenant_storage
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = tenant_storage.path('')
DB = BASE / 'games.sqlite3'
SECURITY = BASE / 'security'
CONNECTION = SECURITY / 'steam-games.json'
COVERS = BASE / 'games-covers'
PLATFORMS = ('Steam', 'Switch', 'PS5', '其他')
STATUSES = ('unclassified', 'wishlist', 'playing', 'completed', 'paused')
MAX_IMAGE = 4 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024
STEAM_ENDPOINT = 'https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/'
STEAM_CDN = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/'
_sync_lock = threading.Lock()
_cover_locks = {}
_cover_failures = {}


def now():
    return datetime.now().astimezone().isoformat()


def con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE IF NOT EXISTS games (
          id TEXT PRIMARY KEY, platform TEXT NOT NULL, title TEXT NOT NULL,
          steam_appid INTEGER, source TEXT NOT NULL DEFAULT 'manual',
          manual_minutes INTEGER, steam_minutes INTEGER, last_played INTEGER,
          status TEXT NOT NULL, note TEXT NOT NULL, rating INTEGER,
          cover TEXT NOT NULL DEFAULT '', deleted INTEGER NOT NULL DEFAULT 0,
          created TEXT NOT NULL, updated TEXT NOT NULL, revision INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS games_steam_appid
          ON games(steam_appid) WHERE steam_appid IS NOT NULL;
        CREATE TABLE IF NOT EXISTS games_history (
          id TEXT, revision INTEGER, data TEXT, PRIMARY KEY(id,revision)
        );
        CREATE TABLE IF NOT EXISTS games_meta (key TEXT PRIMARY KEY, value TEXT);
    ''')
    os.chmod(DB, 0o600)
    return c


def _meta(c, key, default=''):
    row = c.execute('SELECT value FROM games_meta WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def _put_meta(c, key, value):
    c.execute('INSERT OR REPLACE INTO games_meta(key,value) VALUES(?,?)', (key, value))


def _record_history(c, old):
    c.execute('INSERT INTO games_history(id,revision,data) VALUES(?,?,?)',
              (old['id'], old['revision'], json.dumps(dict(old), ensure_ascii=False)))


def _public(row):
    result = dict(row)
    result['playtime_minutes'] = result['steam_minutes'] if result['steam_minutes'] is not None else result['manual_minutes']
    result['cover_url'] = '/api/games/covers/' + result['cover'] if result['cover'] else (
        '/api/games/steam-covers/' + str(result['steam_appid']) + '.jpg' if result['steam_appid'] else '')
    return result


def _integer(value, label, maximum, nullable=False):
    if nullable and value in (None, ''):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(label + '格式不正确')
    return value


def _cover_bytes(value):
    if not isinstance(value, str) or len(value) > (MAX_IMAGE * 4 // 3 + 128):
        raise ValueError('封面请使用4MB以内的PNG、JPEG或WebP图片')
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('封面请使用PNG、JPEG或WebP图片')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except (ValueError, TypeError):
        raise ValueError('封面图片无法读取') from None
    if len(raw) > MAX_IMAGE or len(raw) < 12:
        raise ValueError('封面图片大小不正确')
    mime = match[1]
    valid = (mime == 'png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or (
        mime == 'jpeg' and raw.startswith(b'\xff\xd8\xff')) or (
        mime == 'webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP')
    if not valid:
        raise ValueError('图片内容与格式不符')
    return raw, 'jpg' if mime == 'jpeg' else mime


def _store_cover(raw, extension):
    if COVERS.is_symlink():
        raise ValueError('封面目录不可为符号链接')
    COVERS.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(COVERS, 0o700)
    name = hashlib.sha256(raw).hexdigest() + '.' + extension
    destination = COVERS / name
    if destination.is_symlink():
        raise ValueError('封面文件不可为符号链接')
    if not destination.exists():
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    return name


def cover_file(name):
    if not isinstance(name, str) or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp)', name):
        raise ValueError('封面不存在')
    target = COVERS / name
    if COVERS.is_symlink() or target.is_symlink() or not target.is_file():
        raise ValueError('封面不存在')
    return target


def steam_cover_file(name):
    """Authenticated, same-origin cover proxy; no arbitrary URL or redirects.

    The browser never connects to Steam. Failure only hides the decorative
    cover, and a short negative cache avoids repeatedly querying missing art.
    """
    if not isinstance(name, str) or not re.fullmatch(r'[1-9][0-9]{0,9}\.jpg', name):
        raise ValueError('封面不存在')
    appid = int(name[:-4])
    if appid > 2**32 - 1:
        raise ValueError('封面不存在')
    with con() as c:
        if not c.execute('SELECT 1 FROM games WHERE steam_appid=?', (appid,)).fetchone():
            raise ValueError('封面不存在')
    with _cover_locks.setdefault(appid, threading.Lock()):
        if COVERS.is_symlink():
            raise ValueError('封面目录不可为符号链接')
        COVERS.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = COVERS / ('steam-' + name)
        if destination.is_symlink():
            raise ValueError('封面文件不可为符号链接')
        if destination.is_file():
            return destination
        if _cover_failures.get(appid, 0) > time.monotonic():
            raise ValueError('Steam封面暂不可用')
        request = Request(STEAM_CDN + str(appid) + '/header.jpg',
                          headers={'Accept': 'image/jpeg', 'User-Agent': 'Tiangong/30'})
        try:
            with build_opener(_NoRedirect()).open(request, timeout=8) as response:
                raw = response.read(MAX_IMAGE + 1)
            if len(raw) > MAX_IMAGE or len(raw) < 12 or not raw.startswith(b'\xff\xd8\xff'):
                raise ValueError()
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            _cover_failures[appid] = time.monotonic() + 1800
            raise ValueError('Steam封面暂不可用') from None
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        return destination


def save(d):
    if not isinstance(d, dict):
        raise ValueError('游戏记录格式不正确')
    key = d.get('id') or str(uuid.uuid4())
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
        raise ValueError('游戏编号不正确')
    revision = d.get('revision')
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise ValueError('游戏版本号不正确')
    image = _cover_bytes(d['cover_data']) if d.get('cover_data') else None
    with con() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM games WHERE id=?', (key,)).fetchone()
        if not old and revision not in (None, 0):
            raise ValueError('原游戏记录不存在，请保留草稿并刷新核对')
        def field(name, default=None):
            return d.get(name, old[name] if old else default)
        platform, title, note = field('platform', 'Steam'), field('title', ''), field('note', '')
        if platform not in PLATFORMS:
            raise ValueError('请选择Steam、Switch、PS5或其他平台')
        if not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError('请填写300字以内的游戏名称')
        if not isinstance(note, str) or len(note) > 5000:
            raise ValueError('备注最多5000字')
        status = field('status', 'playing')
        if status not in STATUSES:
            raise ValueError('游戏状态不正确')
        appid = _integer(field('steam_appid'), 'Steam游戏编号', 2**32 - 1, True)
        if appid == 0 or (appid is not None and platform != 'Steam'):
            raise ValueError('只有Steam游戏可以填写有效的App ID')
        minutes = _integer(field('manual_minutes'), '游戏时长', 100_000_000, True)
        rating = _integer(field('rating'), '评分', 10, True)
        deleted = _integer(field('deleted', 0), '移出状态', 1)
        if old and old['source'] == 'steam' and (platform != old['platform'] or appid != old['steam_appid']):
            raise ValueError('已同步游戏的平台和App ID不能修改')
        duplicate = c.execute('SELECT id FROM games WHERE steam_appid=? AND id<>?', (appid, key)).fetchone() if appid else None
        if duplicate:
            raise ValueError('这个Steam游戏已在游戏库中，请直接编辑原记录（含已移出的游戏）')
        cover = '' if d.get('cover_remove') else (old['cover'] if old else '')
        if image:
            cover = _store_cover(*image)
        values = dict(platform=platform, title=title.strip(), steam_appid=appid,
                      manual_minutes=minutes, status=status, note=note, rating=rating,
                      deleted=deleted, cover=cover)
        if old:
            # A response can be lost after commit. The same stable ID and values
            # succeed without another insert or revision, even with revision=0.
            if all(old[k] == v for k, v in values.items()):
                return _public(old)
            if d.get('revision') != old['revision']:
                raise ValueError('这条游戏记录已在另一处更新，请刷新后重试')
            _record_history(c, old)
        row = dict(id=key, **values, source=old['source'] if old else 'manual',
                   steam_minutes=old['steam_minutes'] if old else None,
                   last_played=old['last_played'] if old else None,
                   created=old['created'] if old else now(), updated=now(),
                   revision=old['revision'] + 1 if old else 1)
        cols = ','.join(row)
        c.execute('INSERT OR REPLACE INTO games(' + cols + ') VALUES(' + ','.join(':' + x for x in row) + ')', row)
        return _public(row)


def _read_connection():
    if SECURITY.is_symlink() or CONNECTION.is_symlink():
        raise ValueError('Steam连接设置路径不安全')
    if not CONNECTION.exists():
        return None
    try:
        config = json.loads(CONNECTION.read_text())
        if not isinstance(config, dict) or not re.fullmatch(r'[0-9]{17}', config.get('steam_id', '')) or not re.fullmatch(r'[0-9a-fA-F]{32}', config.get('api_key', '')):
            raise ValueError()
        if not 0 < int(config['steam_id']) < 2**64:
            raise ValueError()
        return config
    except (ValueError, TypeError, OSError):
        raise ValueError('Steam连接设置无法读取，请重新保存连接') from None


def connection_status():
    try:
        config = _read_connection()
        problem = ''
    except ValueError as error:
        config, problem = None, str(error)
    with con() as c:
        success = _meta(c, 'last_sync')
        error = problem or _meta(c, 'last_error')
        verified = bool(config and _meta(c, 'verified_id') == config['steam_id'])
    return dict(platform='Steam', configured=bool(config), connected=verified,
                steam_id=config['steam_id'] if config else '', last_sync=success,
                last_error=error, status='已连接' if verified else ('待验证' if config else '未连接'))


def configure_connection(d):
    if not isinstance(d, dict):
        raise ValueError('连接设置格式不正确')
    with _sync_lock:
        if SECURITY.is_symlink() or CONNECTION.is_symlink():
            raise ValueError('Steam连接设置路径不安全')
        if d.get('action') == 'disconnect':
            CONNECTION.unlink(missing_ok=True)
            with con() as c:
                _put_meta(c, 'verified_id', '')
                _put_meta(c, 'last_error', '')
            return connection_status()
        steam_id = d.get('steam_id', '')
        api_key = d.get('api_key', '')
        if not isinstance(steam_id, str) or not re.fullmatch(r'[0-9]{17}', steam_id) or not 0 < int(steam_id) < 2**64:
            raise ValueError('请填写17位Steam ID，不是登录账号或昵称')
        if not isinstance(api_key, str):
            raise ValueError('Steam Web API Key格式不正确')
        if not api_key:
            old = _read_connection()
            if old and old['steam_id'] == steam_id:
                api_key = old['api_key']
        if not re.fullmatch(r'[0-9a-fA-F]{32}', api_key):
            raise ValueError('请填写Steam官方提供的32位Web API Key')
        with con() as c:
            owner = _meta(c, 'library_owner')
            if owner and owner != steam_id:
                raise ValueError('当前游戏库已关联另一个Steam账号，暂不支持混合不同账号的时长')
        SECURITY.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(SECURITY, 0o700)
        temporary = SECURITY / ('steam-games-' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(steam_id=steam_id, api_key=api_key), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CONNECTION)
        finally:
            temporary.unlink(missing_ok=True)
        with con() as c:
            _put_meta(c, 'verified_id', '')
            _put_meta(c, 'last_error', '')
        return connection_status()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a request containing the Web API Key to another host.
        return None


def _fetch_owned(config):
    args = dict(steamid=config['steam_id'], include_appinfo=True, include_played_free_games=True)
    query = urlencode(dict(key=config['api_key'], input_json=json.dumps(args, separators=(',', ':'))))
    request = Request(STEAM_ENDPOINT + '?' + query, headers={'Accept': 'application/json', 'User-Agent': 'Tiangong/30'})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('Steam游戏库响应过大，本次未写入，请稍后重试')
        return json.loads(raw)
    except HTTPError as error:
        if error.code in (401, 403):
            raise ValueError('Steam拒绝了连接，请检查API Key与账号设置') from None
        if error.code == 429:
            raise ValueError('Steam请求过于频繁，请稍后重试') from None
        raise ValueError('Steam暂时无法访问，本次未改变游戏库') from None
    except (URLError, OSError, TimeoutError, UnicodeError, json.JSONDecodeError):
        # Exceptions from urllib can contain the secret URL; never expose them.
        raise ValueError('Steam连接或响应异常，本次未改变游戏库，请稍后重试') from None


def _validated_owned(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('response'), dict):
        raise ValueError('Steam返回格式异常，本次未改变游戏库')
    response = payload['response']
    if 'game_count' not in response:
        raise ValueError('未读到Steam游戏详情，请确认Steam ID及“游戏详情”隐私设置允许读取')
    count = _integer(response['game_count'], 'Steam游戏数量', 50000)
    games = response.get('games', [] if count == 0 else None)
    if not isinstance(games, list) or len(games) != count:
        raise ValueError('Steam游戏库返回不完整，本次未改变游戏库')
    records, seen = [], set()
    for game in games:
        if not isinstance(game, dict):
            raise ValueError('Steam游戏记录格式异常')
        appid = _integer(game.get('appid'), 'Steam游戏编号', 2**32 - 1)
        title = game.get('name')
        # Missing playtime is unknown, not zero (the privacy setting can hide it).
        minutes = _integer(game.get('playtime_forever'), 'Steam游戏时长', 100_000_000, True)
        last_played = _integer(game.get('rtime_last_played'), 'Steam最近游玩时间', 2**63 - 1, True)
        if not appid or appid in seen or not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError('Steam游戏记录不完整或重复，本次未改变游戏库')
        seen.add(appid)
        records.append(dict(steam_appid=appid, title=title.strip(), steam_minutes=minutes, last_played=last_played))
    return records


def _normalized(title):
    return ' '.join(unicodedata.normalize('NFKC', title).casefold().split())


def sync_steam():
    if not _sync_lock.acquire(blocking=False):
        raise ValueError('Steam正在同步，请稍后查看')
    try:
        config = _read_connection()
        if not config:
            raise ValueError('请先连接Steam账号')
        try:
            records = _validated_owned(_fetch_owned(config))
            stamp = now()
            added, updated, merged = 0, 0, 0
            with con() as c:
                c.execute('BEGIN IMMEDIATE')
                owner = _meta(c, 'library_owner')
                if owner and owner != config['steam_id']:
                    raise ValueError('当前游戏库关联的Steam账号与连接设置不一致')
                manual = c.execute("SELECT * FROM games WHERE platform='Steam' AND steam_appid IS NULL AND deleted=0").fetchall()
                for incoming in records:
                    old = c.execute('SELECT * FROM games WHERE steam_appid=?', (incoming['steam_appid'],)).fetchone()
                    if not old:
                        candidates = [r for r in manual if _normalized(r['title']) == _normalized(incoming['title'])]
                        if len(candidates) == 1:
                            old = candidates[0]
                            manual = [r for r in manual if r['id'] != old['id']]
                            merged += 1
                    if old:
                        row = dict(old)
                        row.update(incoming)
                        # A missing API field cannot erase previously known time.
                        for optional in ('steam_minutes', 'last_played'):
                            if row[optional] is None:
                                row[optional] = old[optional]
                        row['source'] = 'steam'
                        if all(row[k] == old[k] for k in row):
                            continue
                        _record_history(c, old)
                        row.update(updated=stamp, revision=old['revision'] + 1)
                        updated += 1
                    else:
                        row = dict(id=str(uuid.uuid4()), platform='Steam', **incoming, source='steam',
                                   manual_minutes=None, status='unclassified',
                                   note='', rating=None, cover='', deleted=0, created=stamp, updated=stamp, revision=1)
                        added += 1
                    cols = ','.join(row)
                    c.execute('INSERT OR REPLACE INTO games(' + cols + ') VALUES(' + ','.join(':' + x for x in row) + ')', row)
                _put_meta(c, 'library_owner', config['steam_id'])
                _put_meta(c, 'verified_id', config['steam_id'])
                _put_meta(c, 'last_sync', stamp)
                _put_meta(c, 'last_error', '')
            return dict(added=added, updated=updated, merged=merged, received=len(records), connection=connection_status())
        except ValueError as error:
            with con() as c:
                _put_meta(c, 'last_error', str(error))
            raise
    finally:
        _sync_lock.release()


def overview():
    with con() as c:
        rows = [_public(row) for row in c.execute('SELECT * FROM games ORDER BY updated DESC')]
    active = [row for row in rows if not row['deleted']]
    return dict(games=rows, connection=connection_status(), summary=dict(
        count=len(active), playtime_minutes=sum(row['playtime_minutes'] or 0 for row in active),
        unknown_playtime=sum(row['playtime_minutes'] is None for row in active),
        playing=sum(row['status'] == 'playing' for row in active),
        platforms={p: sum(row['platform'] == p for row in active) for p in PLATFORMS}))
