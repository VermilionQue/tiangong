"""Durable handoff between the cloud UI and the existing Mac Reminders worker."""
import json
import sqlite3
import time
import uuid
import tenant_storage


def connection():
    if tenant_storage.current() != 'owner':
        raise ValueError('提醒事项仅供当前已连接的主账号使用')
    path = tenant_storage.path('reminders-cloud.sqlite3')
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS worker(id INTEGER PRIMARY KEY, data TEXT, updated REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY, data TEXT, created REAL)')
    return db


def state():
    with connection() as db:
        row = db.execute('SELECT * FROM worker WHERE id=1').fetchone()
    value = json.loads(row['data']) if row else {'enabled': True, 'state': 'waiting', 'conflicts': []}
    if not row or time.time() - row['updated'] > 120:
        value.update(state='waiting', message='网页记录已保存在云端；苹果提醒事项将在 iMac 联机后继续同步')
    return value


def publish(value):
    if not isinstance(value, dict):
        raise ValueError('同步状态无效')
    with connection() as db:
        db.execute('INSERT OR REPLACE INTO worker VALUES(1,?,?)', (json.dumps(value, ensure_ascii=False), time.time()))
    return True


def queue(data):
    if not isinstance(data, dict) or data.get('action') not in ('sync', 'resolve'):
        raise ValueError('同步操作无效')
    with connection() as db:
        # One identical pending request is enough; repeated clicks never multiply work.
        payload = json.dumps(data, sort_keys=True)
        if not db.execute('SELECT 1 FROM queue WHERE data=?', (payload,)).fetchone():
            db.execute('INSERT INTO queue VALUES(?,?,?)', (str(uuid.uuid4()), payload, time.time()))
    return state()


def pending():
    with connection() as db:
        return [{'id': r['id'], 'data': json.loads(r['data'])} for r in db.execute('SELECT * FROM queue ORDER BY created')]


def acknowledge(key):
    with connection() as db:
        db.execute('DELETE FROM queue WHERE id=?', (key,))
    return True


def local_cycle():
    import reminders_sync
    for request in pending():
        data = request['data']
        try:
            if data['action'] == 'resolve':
                reminders_sync.resolve(data.get('todo_id'), data.get('choice'), data.get('token'))
            else:
                reminders_sync.run_once()
        except ValueError:
            # A changed conflict token is already safe to reject; refresh current conflicts.
            pass
        acknowledge(request['id'])
    return publish(reminders_sync.run_once())


import cloud_authority
cloud_authority.install(globals(), 'cloud_reminders')

if __name__ == '__main__':
    while cloud_authority.config()['mode'] == 'cloud':
        try:
            local_cycle()
        except Exception:
            # Keep the existing local status/log and retry; never fall back to old data.
            pass
        time.sleep(30)
