"""Invite-only QuanKu books. Identity must come from the signed gateway, never JSON."""
import contextlib
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
import tenant_storage

DB = tenant_storage.BASE / 'quanku-shared.sqlite3'
LIMIT = 9_000_000_000_000

@contextlib.contextmanager
def connect(path=None):
    if path is None:
        import cloud_authority
        cloud_authority.guard_local_storage()
    path = Path(path or DB)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=15)
    path.chmod(0o600)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS members(book TEXT,user TEXT,name TEXT,role TEXT,PRIMARY KEY(book,user));
    CREATE TABLE IF NOT EXISTS entries(book TEXT,id TEXT,data TEXT,voided INTEGER DEFAULT 0,PRIMARY KEY(book,id));
    CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY,book TEXT,role TEXT,expires INTEGER,used INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS history(book TEXT,revision INTEGER,actor TEXT,action TEXT,at INTEGER,data TEXT);
    ''')
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()

def text(value, size=100):
    if not isinstance(value, str) or not value.strip() or len(value) > size: raise ValueError('请填写有效名称或内容')
    return value.strip()

def snapshot(db, book, user):
    member = db.execute('SELECT * FROM members WHERE book=? AND user=?', (book,user)).fetchone()
    if not member: raise PermissionError('你没有这个共享账本的访问权限')
    result = dict(db.execute('SELECT * FROM books WHERE id=?',(book,)).fetchone())
    result['role'] = member['role']
    result['members'] = [dict(r) for r in db.execute('SELECT user,name,role FROM members WHERE book=?',(book,))]
    result['entries'] = [dict(json.loads(r['data']),voided=bool(r['voided'])) for r in db.execute('SELECT data,voided FROM entries WHERE book=?',(book,))]
    result['history'] = [dict(r) for r in db.execute('SELECT revision,actor,action,at,data FROM history WHERE book=? ORDER BY revision DESC LIMIT 100',(book,))]
    return result

def handle(data, user=None, path=None):
    user = user or tenant_storage.current()
    if user == 'demo': raise PermissionError('请使用正式邀请账号')
    action = data.get('action','list')
    with connect(path) as db:
        if action == 'list':
            return {'books':[dict(r) for r in db.execute('SELECT b.id,b.name,b.revision,m.role FROM books b JOIN members m ON m.book=b.id WHERE m.user=? ORDER BY b.rowid DESC',(user,))]}
        if action == 'create':
            if db.execute('SELECT count(*) FROM books WHERE owner=?',(user,)).fetchone()[0] >= 20: raise ValueError('最多创建20个共享账本')
            book = str(uuid.uuid4()); name=text(data.get('name')); nickname=text(data.get('nickname'))
            db.execute('INSERT INTO books VALUES(?,?,?,0)',(book,name,user))
            db.execute('INSERT INTO members VALUES(?,?,?,?)',(book,user,nickname,'owner'))
            return snapshot(db,book,user)
        if action == 'join':
            digest=hashlib.sha256(text(data.get('code'),100).encode()).hexdigest()
            invite=db.execute('SELECT * FROM invites WHERE hash=?',(digest,)).fetchone()
            if not invite or invite['used'] or invite['expires'] < time.time(): raise ValueError('邀请码无效、已使用或已过期')
            book=invite['book']
            if db.execute('SELECT count(*) FROM members WHERE book=?',(book,)).fetchone()[0] >= 20: raise ValueError('每个共享账本最多20人')
            if db.execute('SELECT 1 FROM members WHERE book=? AND user=?',(book,user)).fetchone(): raise ValueError('你已经是这个账本的成员')
            db.execute('INSERT INTO members VALUES(?,?,?,?)',(book,user,text(data.get('nickname')),invite['role']))
            db.execute('UPDATE invites SET used=1 WHERE hash=?',(digest,))
            audit(db,book,user,'加入账本',{})
            return snapshot(db,book,user)
        book=text(data.get('book'))
        current=snapshot(db,book,user)
        if action == 'get': return current
        if current['role'] == 'viewer': raise PermissionError('你只有查看权限')
        if type(data.get('revision')) is not int or current['revision'] != data['revision']: raise ValueError('账本已被更新，请刷新后核对再操作；本次未覆盖任何记录')
        result={}
        if action == 'invite':
            if current['role'] != 'owner': raise PermissionError('只有创建者可以邀请成员')
            role=data.get('role','editor')
            if role not in ('editor','viewer'): raise ValueError('成员角色无效')
            code=secrets.token_urlsafe(24)
            db.execute('INSERT INTO invites VALUES(?,?,?,?,0)',(hashlib.sha256(code.encode()).hexdigest(),book,role,int(time.time())+86400*7))
            result={'inviteCode':code}
        elif action == 'remove':
            if current['role'] != 'owner': raise PermissionError('只有创建者可以移除成员')
            target=text(data.get('user'))
            if target == current['owner']: raise ValueError('不能移除账本创建者')
            db.execute('DELETE FROM members WHERE book=? AND user=?',(book,target))
            db.execute('UPDATE invites SET used=1 WHERE book=?',(book,))
        elif action == 'save':
            e=data.get('entry')
            if not isinstance(e,dict): raise ValueError('账目格式无效')
            eid=str(uuid.UUID(text(e.get('id'))))
            if type(e.get('cents')) is not int or not 0<e['cents']<=LIMIT: raise ValueError('金额须为有效整数分')
            if e.get('kind') not in ('支出','收入'): raise ValueError('共享账本目前只记录收支')
            import datetime
            try: day=datetime.date.fromisoformat(e.get('date',''))
            except (ValueError,TypeError): raise ValueError('日期无效')
            if day > datetime.date.today(): raise ValueError('不能记录未来收支')
            clean={'id':eid,'cents':e['cents'],'kind':e['kind'],'date':day.isoformat(),'category':text(e.get('category')),'note':text(e.get('note') or '无备注',500),'author':user}
            if not db.execute('SELECT 1 FROM entries WHERE book=? AND id=?',(book,eid)).fetchone() and db.execute('SELECT count(*) FROM entries WHERE book=?',(book,)).fetchone()[0] >= 10000: raise ValueError('试用账本记录达到上限，请先导出')
            db.execute('INSERT INTO entries VALUES(?,?,?,0) ON CONFLICT(book,id) DO UPDATE SET data=excluded.data,voided=0',(book,eid,json.dumps(clean,ensure_ascii=False)))
            data={'entry':clean}
        elif action == 'void':
            eid=text(data.get('id'))
            if not db.execute('SELECT 1 FROM entries WHERE book=? AND id=? AND voided=0',(book,eid)).fetchone(): raise ValueError('记录不存在或已经撤销，请刷新')
            db.execute('UPDATE entries SET voided=1 WHERE book=? AND id=?',(book,eid))
            data={'id':eid}
        else: raise ValueError('操作不存在')
        audit(db,book,user,action,{k:v for k,v in data.items() if k not in ('code','password')})
        return dict(snapshot(db,book,user),**result)

def audit(db,book,user,action,data):
    db.execute('UPDATE books SET revision=revision+1 WHERE id=?',(book,))
    revision=db.execute('SELECT revision FROM books WHERE id=?',(book,)).fetchone()[0]
    db.execute('INSERT INTO history VALUES(?,?,?,?,?,?)',(book,revision,user,action,int(time.time()),json.dumps(data,ensure_ascii=False)))
