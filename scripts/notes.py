# -*- coding: utf-8 -*-
import argparse, base64, binascii, hashlib, html, json, os, re, shutil, sqlite3, struct, uuid, zlib
from html.parser import HTMLParser
from pathlib import Path
import tenant_storage
from datetime import datetime
from urllib.parse import unquote
BASE=tenant_storage.path('notes')
MAX_NEW_IMAGES=8
MAX_IMAGE_BYTES=12*1024*1024
MAX_ATTACHMENTS=24
VOID={'img','br','hr','meta','input','source','link'}
class Node:
    def __init__(self,tag='',attrs=None):self.tag=tag;self.attrs=dict(attrs or []);self.children=[]
    def find(self,cls):
        result=[]
        if cls in self.attrs.get('class','').split():result.append(self)
        for ch in self.children:
            if isinstance(ch,Node):result+=ch.find(cls)
        return result
    def text(self):
        return ''.join((c.text()+('\n' if c.tag in ('p','div','li','br') else '')) if isinstance(c,Node) else c for c in self.children)
    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c,Node):yield from c.walk()
class Parser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root]
    def handle_starttag(self,t,a):
        n=Node(t,a);self.stack[-1].children.append(n)
        if t not in VOID:self.stack.append(n)
    def handle_startendtag(self,t,a):self.handle_starttag(t,a);self.handle_endtag(t)
    def handle_endtag(self,t):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==t:self.stack=self.stack[:i];break
    def handle_data(self,d):self.stack[-1].children.append(d)
def parse(s):p=Parser();p.feed(s);return p.root
ALLOW={'p','br','b','strong','i','em','u','s','ul','ol','li','blockquote','code','pre','a','h1','h2','h3'}
def safe(n):
    if isinstance(n,str):return html.escape(n)
    if n.tag in ('script','style','iframe','object'):return ''
    inner=''.join(safe(c) for c in n.children)
    if n.tag not in ALLOW:return inner
    attr=''
    if n.tag=='a':
        href=n.attrs.get('href','')
        if href.startswith(('https://','http://','mailto:')):attr=' href="'+html.escape(href,quote=True)+'" target="_blank" rel="noopener noreferrer"'
    return '<'+n.tag+attr+'>'+inner+('' if n.tag=='br' else '</'+n.tag+'>')
def con():
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    c=sqlite3.connect(str(BASE/'notes.sqlite3'));c.row_factory=sqlite3.Row
    # Serialize the additive migration, including simultaneous first requests.
    c.execute('BEGIN IMMEDIATE')
    c.execute('CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, created TEXT, updated TEXT, html TEXT, text TEXT, tags TEXT, attachments TEXT, source TEXT, revision INTEGER, deleted INTEGER NOT NULL DEFAULT 0, deleted_at TEXT)')
    columns={r['name'] for r in c.execute('PRAGMA table_info(notes)')}
    if 'deleted' not in columns:c.execute('ALTER TABLE notes ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
    if 'deleted_at' not in columns:c.execute('ALTER TABLE notes ADD COLUMN deleted_at TEXT')
    c.execute('CREATE TABLE IF NOT EXISTS history(id TEXT, revision INTEGER, data TEXT)');c.commit();(BASE/'notes.sqlite3').chmod(0o600);return c
def tags(text):return sorted(set(re.findall(r'(?<!\w)#([^\s#<>，。！？,;；：:]+)',text)))
def decoded(row):
    result=dict(row)
    for k in ('tags','attachments'):result[k]=json.loads(result[k])
    return result
def all_notes(include_deleted=False):
    with con() as c:
        return [decoded(r) for r in c.execute('SELECT * FROM notes '+('' if include_deleted else 'WHERE deleted=0 ')+'ORDER BY created DESC')]
def import_export(folder):
    folder=Path(folder).resolve();files=list(folder.glob('*.html'))
    if len(files)!=1:raise ValueError('需包含一份flomo导出HTML')
    raw=files[0].read_text(encoding='utf-8');root=parse(raw);memos=root.find('memo');digest=hashlib.sha256(raw.encode()).hexdigest()[:16]
    archive=BASE/'sources'/digest
    if not archive.exists():archive.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copytree(folder,archive)
    added=0;skipped=0;missing=[];attachment_count=0
    with con() as c:
        for memo in memos:
            stamp=memo.find('time')[0].text().strip();datetime.strptime(stamp,'%Y-%m-%d %H:%M:%S');content=memo.find('content')[0];body=safe(content);text=content.text().strip();assets=[]
            for n in memo.walk():
                for attr in ('src','href'):
                    ref=unquote(n.attrs.get(attr,''))
                    if not ref.startswith('file/'):continue
                    target=(archive/ref).resolve()
                    if archive.resolve() not in target.parents:raise ValueError('附件路径越界')
                    if not target.is_file():missing.append(ref);continue
                    key=digest+'/'+ref
                    if any(x['key']==key for x in assets):continue
                    assets.append({'key':key,'name':target.name,'kind':target.suffix.lower()})
            attachment_count+=len(assets)
            key='flomo-'+hashlib.sha256((stamp+'\n'+body+'\n'+json.dumps([a['key'].split('/',1)[1] for a in assets])).encode()).hexdigest()
            if c.execute('SELECT 1 FROM notes WHERE id=?',(key,)).fetchone():skipped+=1;continue
            c.execute('INSERT INTO notes (id,created,updated,html,text,tags,attachments,source,revision) VALUES (?,?,?,?,?,?,?,?,?)',(key,stamp,stamp,body,text,json.dumps(tags(text),ensure_ascii=False),json.dumps(assets,ensure_ascii=False),'flomo',1));added+=1
    result={'source_count':len(memos),'added':added,'skipped':skipped,'attachment_references':attachment_count,'missing':missing,'total':len(all_notes()),'tags':len(set(t for r in all_notes() for t in r['tags']))}
    (BASE/'import_latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result

def _image_kind(raw):
    """Check raster signatures and their container structure without decoding pixels."""
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        pos=8;image=False;first=True
        while pos+12<=len(raw):
            size=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8];end=pos+12+size
            if end>len(raw):break
            payload=raw[pos+8:pos+8+size]
            if zlib.crc32(kind+payload)&0xffffffff!=struct.unpack('>I',raw[end-4:end])[0]:break
            if first:
                if kind!=b'IHDR' or size!=13 or not all(struct.unpack('>II',payload[:8])):break
                first=False
            elif kind==b'IHDR':break
            if kind==b'IDAT' and size:image=True
            if kind==b'IEND':
                if size==0 and image and end==len(raw):return '.png'
                break
            pos=end
    elif raw.startswith(b'\xff\xd8'):
        pos=2;frame=False;scan=False;in_scan=False
        while pos<len(raw):
            if in_scan:
                pos=raw.find(b'\xff',pos)
                if pos<0:break
            elif raw[pos]!=255:break
            while pos<len(raw) and raw[pos]==255:pos+=1
            if pos>=len(raw):break
            marker=raw[pos];pos+=1
            if in_scan and (marker==0 or 0xd0<=marker<=0xd7):continue
            in_scan=False
            if marker==0xd9:
                if frame and scan and pos==len(raw):return '.jpg'
                break
            if marker in (0,0xd8) or pos+2>len(raw):break
            size=int.from_bytes(raw[pos:pos+2],'big')
            if size<2 or pos+size>len(raw):break
            payload=raw[pos+2:pos+size]
            if marker in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
                if len(payload)<9 or len(payload)!=6+3*payload[5] or not all(struct.unpack('>HH',payload[1:5])):break
                frame=True
            if marker==0xda:
                if not frame or len(payload)<6 or len(payload)!=4+2*payload[0]:break
                scan=True;in_scan=True
            pos+=size
    elif raw.startswith((b'GIF87a',b'GIF89a')) and len(raw)>=14:
        if not all(struct.unpack('<HH',raw[6:10])):raise ValueError('图片内容无效或已损坏')
        pos=13+(3*(2**((raw[10]&7)+1)) if raw[10]&128 else 0);image=False
        while pos<len(raw):
            marker=raw[pos];pos+=1
            if marker==0x3b:
                if image and pos==len(raw):return '.gif'
                break
            if marker==0x21:
                pos+=1
            elif marker==0x2c:
                if pos+9>=len(raw) or not all(struct.unpack('<HH',raw[pos+4:pos+8])):break
                packed=raw[pos+8];pos+=9+(3*(2**((packed&7)+1)) if packed&128 else 0)
                if pos>=len(raw) or not 2<=raw[pos]<=8:break
                pos+=1;image=True
            else:break
            while pos<len(raw) and raw[pos]:pos+=raw[pos]+1
            if pos>=len(raw):break
            pos+=1
    elif raw.startswith(b'RIFF') and len(raw)>=20 and raw[8:12]==b'WEBP' and int.from_bytes(raw[4:8],'little')==len(raw)-8:
        def chunks_valid(data):
            pos=0;image=False
            while pos+8<=len(data):
                kind=data[pos:pos+4];size=int.from_bytes(data[pos+4:pos+8],'little');end=pos+8+size
                if end+(size%2)>len(data):return False
                payload=data[pos+8:end]
                if kind==b'VP8 ':
                    if len(payload)<10 or payload[3:6]!=b'\x9d\x01\x2a' or not all(n&0x3fff for n in struct.unpack('<HH',payload[6:10])):return False
                    image=True
                elif kind==b'VP8L':
                    if len(payload)<5 or payload[0]!=0x2f:return False
                    image=True
                elif kind==b'ANMF':
                    if len(payload)<16 or not chunks_valid(payload[16:]):return False
                    image=True
                elif kind==b'VP8X' and len(payload)!=10:return False
                pos=end+(size%2)
            return image and pos==len(data)
        if chunks_valid(raw[12:]):return '.webp'
    raise ValueError('图片内容无效或已损坏，只支持 JPG、PNG、GIF、WebP')

def _prepare_images(value):
    if not isinstance(value,list):raise ValueError('附件列表无效')
    prepared=[];total=0;new_count=0
    for item in value:
        if not isinstance(item,dict):raise ValueError('附件格式无效')
        if set(item)=={'key'}:
            if not isinstance(item['key'],str) or not item['key'] or len(item['key'])>2000:raise ValueError('附件编号无效')
            prepared.append((item['key'],None,None));continue
        if set(item)!={'name','data'}:raise ValueError('附件格式无效')
        new_count+=1
        if new_count>MAX_NEW_IMAGES:raise ValueError('一次最多新增 8 张图片')
        name=item['name'];encoded=item['data']
        if not isinstance(name,str) or not name.strip() or len(name)>255 or any(ch in unquote(name) for ch in ('/','\\')) or any(ord(ch)<32 or ord(ch)==127 for ch in unquote(name)):raise ValueError('图片文件名无效')
        suffix=Path(name).suffix.lower()
        if suffix not in ('.jpg','.jpeg','.png','.gif','.webp'):raise ValueError('只支持 JPG、PNG、GIF、WebP 图片')
        if not isinstance(encoded,str) or len(encoded)>4*((MAX_IMAGE_BYTES+2)//3):raise ValueError('图片过大，单张及本次新增图片合计不能超过 12 MB')
        try:raw=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error):raise ValueError('图片数据无效') from None
        total+=len(raw)
        if not raw or total>MAX_IMAGE_BYTES:raise ValueError('图片过大或为空，单张及本次新增图片合计不能超过 12 MB')
        kind=_image_kind(raw)
        if ('.jpg' if suffix=='.jpeg' else suffix)!=kind:raise ValueError('图片扩展名与内容不一致')
        key='uploads/'+hashlib.sha256(raw).hexdigest()+kind
        prepared.append((key,{'key':key,'name':name,'kind':kind},raw))
    return prepared

def _attachments(prepared,old):
    previous=json.loads(old['attachments']) if old else []
    if prepared is None:return previous,{}
    available={item['key']:item for item in previous};result=[];uploads={};seen=set()
    for key,item,raw in prepared:
        if item is None and key not in available:raise ValueError('只能保留当前笔记原有的附件，请刷新后重试')
        if key in seen:continue
        seen.add(key);result.append(available.get(key) or item)
        if raw is not None:uploads[key]=raw
    # Imported notes may already exceed the new-upload limit; retain/edit them intact.
    if len(result)>MAX_ATTACHMENTS and (len(result)>len(previous) or any(item['key'] not in available for item in result)):raise ValueError('每篇笔记最多保留 24 张附件')
    return result,uploads

def _write_images(uploads,created):
    if not uploads:return
    sources=BASE/'sources';folder=sources/'uploads'
    if sources.is_symlink() or folder.is_symlink():raise ValueError('图片保存目录无效')
    sources.mkdir(mode=0o700,parents=True,exist_ok=True);folder.mkdir(mode=0o700,exist_ok=True)
    sources.chmod(0o700);folder.chmod(0o700)
    for key,raw in uploads.items():
        path=sources/key
        try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=len(raw) or hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(raw).digest():raise ValueError('已有图片校验失败，请稍后重试')
            path.chmod(0o600);continue
        created.append(path)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())

def save(data):
    if not isinstance(data,dict):raise ValueError('无效笔记')
    action=data.get('action','save')
    if action not in ('save','delete','restore'):raise ValueError('无效笔记操作')
    key=data.get('id')
    if key is not None and (not isinstance(key,str) or not key or len(key)>100):raise ValueError('无效笔记编号')
    key=key or str(uuid.uuid4())
    if action!='save':return change_deleted(key,data.get('revision'),action=='delete')
    raw=data.get('html','')
    if not isinstance(raw,str) or len(raw)>100000:raise ValueError('笔记过长')
    root=parse(raw);body=safe(root);text=parse(body).text().strip()
    prepared=_prepare_images(data['attachments']) if 'attachments' in data else None
    now=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')
    with con() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM notes WHERE id=?',(key,)).fetchone()
        attachments,uploads=_attachments(prepared,old)
        if not text and not attachments:raise ValueError('先写下一点想法或添加图片吧')
        if old:
            if old['deleted']:raise ValueError('笔记已移入回收站，请先恢复再编辑')
            if old['html']==body and json.loads(old['attachments'])==attachments:return decoded(old)
            if type(data.get('revision')) is not int or old['revision']!=data['revision']:raise ValueError('笔记已有更新，请刷新后再编辑')
        elif data.get('revision') is not None and (type(data['revision']) is not int or data['revision']!=0):raise ValueError('原笔记不存在，请刷新后重试')
        created=[]
        try:
            _write_images(uploads,created)
            if old:c.execute('INSERT INTO history VALUES (?,?,?)',(key,old['revision'],json.dumps(dict(old),ensure_ascii=False)))
            row=(key,old['created'] if old else now,now,body,text,json.dumps(tags(text),ensure_ascii=False),json.dumps(attachments,ensure_ascii=False),old['source'] if old else '天工录',old['revision']+1 if old else 1)
            c.execute('INSERT OR REPLACE INTO notes (id,created,updated,html,text,tags,attachments,source,revision) VALUES (?,?,?,?,?,?,?,?,?)',row)
            result=decoded(c.execute('SELECT * FROM notes WHERE id=?',(key,)).fetchone())
            c.commit()
            return result
        except Exception:
            # Remove only newly created unreferenced files, while the DB write lock is held.
            for path in created:
                try:path.unlink()
                except OSError:pass
            raise
def change_deleted(key,revision,deleted):
    now=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')
    with con() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM notes WHERE id=?',(key,)).fetchone()
        if not old:raise ValueError('原笔记不存在，请刷新后重试')
        if type(revision) is not int or revision!=old['revision']:raise ValueError('笔记已有更新，请刷新后再操作')
        if bool(old['deleted'])==deleted:return decoded(old)
        c.execute('INSERT INTO history VALUES (?,?,?)',(key,old['revision'],json.dumps(dict(old),ensure_ascii=False)))
        c.execute('UPDATE notes SET deleted=?,deleted_at=?,updated=?,revision=revision+1 WHERE id=?',(int(deleted),now if deleted else None,now,key))
        return decoded(c.execute('SELECT * FROM notes WHERE id=?',(key,)).fetchone())
def asset(key):
    path=(BASE/'sources'/unquote(key)).resolve();root=(BASE/'sources').resolve()
    if root not in path.parents or path.suffix.lower() not in ('.jpg','.jpeg','.png','.gif','.webp','.mov','.mp4','.m4a','.mp3','.wav','.pdf'):raise ValueError('无效附件')
    if not path.is_file():raise ValueError('附件不存在')
    return path

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'notes')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder');a=p.parse_args();print(json.dumps(import_export(a.folder),ensure_ascii=False))
