"""Tenant-scoped Legrand CRM; revisions, durable import provenance, private files."""
import base64
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from PIL import Image
import tenant_storage

DB = tenant_storage.path('crm.sqlite3')
ASSETS = tenant_storage.path('crm-assets')
STAGES = ('首触', '跟进A', '跟进B', '跟进C', '跟进H', '方案中', '报价中', '成交', '战败', '无效')
KINDS = ('customer', 'unit', 'member')
FILE_KINDS = ('price', 'proposal', 'construction', 'payment', 'contract', 'other')
MAX_FILE = 12 * 1024 * 1024

class ConflictError(ValueError): pass

def now(): return datetime.now(timezone.utc).isoformat()
def dumps(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, allow_nan=False)
def ident(x):
    if not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', x): raise ValueError('记录编号无效')
    return x

def text(x, limit=500, label='内容'):
    if not isinstance(x, str) or len(x)>limit or '\x00' in x: raise ValueError(f'{label}最多{limit}字')
    return x.strip()

def day(x):
    if not x: return ''
    if not isinstance(x,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',x): raise ValueError('请填写有效日期')
    date.fromisoformat(x)
    return x

def cents(x):
    if x is None: return None
    if type(x) is not int or not 0<=x<=99_999_999_999: raise ValueError('金额应为非负整数分')
    return x

@contextmanager
def connection():
    path=Path(DB); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    con=sqlite3.connect(path,timeout=15); con.row_factory=sqlite3.Row
    try:
        con.executescript('''CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id TEXT,revision INTEGER,data TEXT,PRIMARY KEY(id,revision));
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,fingerprint TEXT,response TEXT);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(sha TEXT PRIMARY KEY,data TEXT NOT NULL);''')
        os.chmod(path,0o600)
        yield con
        con.commit()
    except Exception:
        con.rollback(); raise
    finally: con.close()

def record(con,key,kind=None):
    r=con.execute('SELECT kind,data FROM records WHERE id=?',(ident(key),)).fetchone()
    if not r or kind and r['kind']!=kind: raise ValueError('关联记录不存在，请刷新')
    return json.loads(r['data'])

def state():
    with connection() as con:
        rows=[json.loads(r[0]) for r in con.execute('SELECT data FROM records')]
    # Source exports remain available on demand, never on every page load.
    for r in rows: r.pop('legacy',None)
    return {'records': sorted(rows,key=lambda r:(r.get('contact_on',''),r['updatedAt']),reverse=True)}

def detail(key):
    with connection() as con:
        r=record(con,key)
        hist=[json.loads(x[0]) for x in con.execute('SELECT data FROM history WHERE id=? ORDER BY revision DESC LIMIT 30',(key,))]
    payments=[]
    if r['kind']=='customer':
        import receipts
        with receipts.connection() as con:
            payments=[json.loads(x[0]) for x in con.execute('SELECT data FROM receipts')]
        payments=[p for p in payments if p.get('customer_id')==key and not p.get('deletedAt')]
    return {'record':r,'history':hist,'receipts':payments}

def validate(con,data,old):
    kind=data.get('kind',old.get('kind') if old else 'customer')
    if kind not in KINDS or old and old['kind']!=kind: raise ValueError('记录类型不可更改')
    r=dict(old or {}); r['kind']=kind
    limits={'name':200,'source':200,'phone':100,'contact':200,'note':12000,'address':1000}
    if kind=='customer': limits.update(control_points=8000,home_type=100,area=100,community=200,delivery_stage=100)
    for k,n in limits.items(): r[k]=text(data.get(k,r.get(k,'')),n,k)
    if not r['name']: raise ValueError('请填写名称')
    refs={'unit_id':'unit'} if kind=='member' else {'unit_id':'unit','member_id':'member'} if kind=='customer' else {}
    for k,refkind in refs.items():
        value=text(data.get(k,r.get(k,'')),100)
        if value: record(con,value,refkind)
        r[k]=value
    if kind=='customer':
        if r.get('member_id'):
            member=record(con,r['member_id'],'member')
            if r['unit_id'] and member.get('unit_id') and r['unit_id']!=member['unit_id']: raise ValueError('渠道成员与渠道单位不一致')
            r['unit_id']=member.get('unit_id') or r['unit_id']
        for k in ('contact_on','next_follow_on','signed_on'): r[k]=day(data.get(k,r.get(k,'')))
        r['stage']=data.get('stage',r.get('stage','首触'))
        if r['stage'] not in STAGES: raise ValueError('客户进度无效')
        services=data.get('business_types',r.get('business_types',[]))
        if not isinstance(services,list) or len(services)>4 or any(not isinstance(v,str) or v not in ('全屋智能','开关面板','全屋网络','音响影院') for v in services):
            raise ValueError('请选择有效的业务类型')
        r['business_types']=list(dict.fromkeys(services))
        r['budget_cents']=cents(data.get('budget_cents',r.get('budget_cents')))
        attachments=data.get('attachments',r.get('attachments',[]))
        if not isinstance(attachments,list) or len(attachments)>100: raise ValueError('最多保存100份文件')
        checked=[]
        for item in attachments:
            if not isinstance(item,dict) or item.get('category') not in FILE_KINDS: raise ValueError('文件分类无效')
            asset=con.execute('SELECT data FROM assets WHERE id=?',(text(item.get('id'),100),)).fetchone()
            if not asset: raise ValueError('附件未上传或不属于此账号')
            a=json.loads(asset[0]); a['category']=item['category']; a['name']=text(item.get('name',a['name']),200)
            if a['id'] not in [v['id'] for v in checked]: checked.append(a)
        r['attachments']=checked
        # Timeline is append-only through a separate action.
        r.setdefault('followups',[])
    return r

def save(data):
    if not isinstance(data,dict): raise ValueError('请求格式无效')
    request_id=ident(data.get('request_id')); fingerprint=hashlib.sha256(dumps(data).encode()).hexdigest()
    key=ident(data.get('id') or str(uuid.uuid4())); action=data.get('action','save')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        cached=con.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
        if cached:
            if cached['fingerprint']!=fingerprint: raise ConflictError('请求编号已使用，请刷新后重试')
            return json.loads(cached['response'])
        existing=con.execute('SELECT data FROM records WHERE id=?',(key,)).fetchone()
        old=json.loads(existing[0]) if existing else None
        if type(data.get('revision',0)) is not int or data.get('revision',0)!=(old['revision'] if old else 0): raise ConflictError('资料已在另一处更新，请关闭后重新打开')
        if action=='save':
            r=validate(con,data,old)
            if old and old.get('deletedAt'): raise ConflictError('请先恢复这条记录')
        elif action in ('delete','restore','followup') and old:
            r=dict(old)
            if action=='delete':
                r['deletedAt']=now()
            elif action=='restore':
                r['deletedAt']=''
                if r.get('stage') in ('战败','无效'): r['stage']='首触'
            else:
                if r['kind']!='customer' or r.get('deletedAt'): raise ValueError('请先恢复客户')
                content=text(data.get('content',''),5000)
                if not content: raise ValueError('请填写跟进内容')
                if len(r['followups'])>=500: raise ValueError('跟进记录已达500条，请归档')
                r['followups']=[*r['followups'],{'id':request_id,'date':day(data.get('date')) or date.today().isoformat(),'content':content,'createdAt':now()}]
                if 'next_follow_on' in data:r['next_follow_on']=day(data['next_follow_on'])
        else: raise ValueError('操作无效')
        stamp=now();r.update(id=key,revision=(old['revision'] if old else 0)+1,updatedAt=stamp,createdAt=old['createdAt'] if old else stamp)
        r.setdefault('deletedAt','')
        if old: con.execute('INSERT INTO history VALUES(?,?,?)',(key,old['revision'],dumps(old)))
        con.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(key,r['kind'],dumps(r)))
        con.execute('INSERT INTO requests VALUES(?,?,?)',(request_id,fingerprint,dumps(r)))
    return r

def upload(data):
    name=text(data.get('name',''),200)
    ext=Path(name).suffix.lower()
    allowed={'.xlsx','.xls','.pdf','.png','.jpg','.jpeg','.webp','.docx','.doc'}
    if ext not in allowed: raise ValueError('支持Excel、PDF、Word、PNG/JPG/WebP，单份不超过12MB')
    raw=data.get('data','')
    if not isinstance(raw,str) or len(raw)>MAX_FILE*4//3+8: raise ValueError('文件不能超过12MB')
    try: body=base64.b64decode(raw,validate=True)
    except ValueError: raise ValueError('文件内容无法读取') from None
    if not 0<len(body)<=MAX_FILE: raise ValueError('文件为空或超过12MB')
    if ext in ('.xlsx','.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                entries=z.infolist();names=z.namelist()
                target='xl/workbook.xml' if ext=='.xlsx' else 'word/document.xml'
                if len(entries)>10000 or sum(e.file_size for e in entries)>120*1024*1024 or target not in names or any('vbaproject' in n.lower() for n in names): raise ValueError('Office文件格式不支持')
        except zipfile.BadZipFile: raise ValueError('Office文件损坏') from None
    elif ext in ('.xls','.doc'):
        if not body.startswith(bytes.fromhex('d0cf11e0a1b11ae1')): raise ValueError('Office文件格式不符')
    elif ext=='.pdf':
        if not body.startswith(b'%PDF-'): raise ValueError('PDF文件格式不符')
    else:
        try:
            with Image.open(io.BytesIO(body)) as im:
                if im.width*im.height>40_000_000 or im.format not in ('PNG','JPEG','WEBP'): raise ValueError('图片格式或尺寸不支持')
                im.verify()
        except (OSError,Image.DecompressionBombError): raise ValueError('图片无法读取') from None
    key=hashlib.sha256(body).hexdigest()+ext
    folder=Path(ASSETS);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    p=folder/key
    if not p.exists():
        fd,tmp=tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd,'wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            os.replace(tmp,p)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    item={'id':key,'name':name,'size':len(body),'uploadedAt':now()}
    with connection() as con:
        con.execute('INSERT OR IGNORE INTO assets VALUES(?,?)',(key,dumps(item)))
    return item

def asset(key):
    if not re.fullmatch(r'[a-f0-9]{64}\.(?:xlsx|xls|pdf|png|jpg|jpeg|webp|docx|doc)',key): raise ValueError('文件不存在')
    with connection() as con:r=con.execute('SELECT data FROM assets WHERE id=?',(key,)).fetchone()
    p=Path(ASSETS)/key
    if not r or p.is_symlink() or not p.is_file(): raise ValueError('文件不存在')
    return p,json.loads(r[0])
