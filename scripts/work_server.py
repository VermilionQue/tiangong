# -*- coding: utf-8 -*-
"""Local work journal server and Shanjun CLI; database is outside Synology Drive."""

import runtime_config
import argparse, json, os, sqlite3, uuid, mimetypes, ipaddress
import receipts
import crm
import finance_hub
import recipes
from urllib.parse import urlsplit, parse_qs
import wellness, notes, nas_backup, daily_quote, todos, travel, finance, checkins, reminders_sync, treasures, games, weather, private_images, people
from datetime import datetime
from pathlib import Path
import tenant_storage
import quanku_shared
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]
BUSINESSES = ('个人工作', '家庭事务', '客户业务', '其他工作')
STATUSES = ('进行中', '已完成', '待跟进')
DB = tenant_storage.path('work.sqlite3')
FONT_DIR = runtime_config.STATE_DIR / 'fonts'

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(DB), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, business TEXT NOT NULL, time TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, author TEXT NOT NULL, updatedAt TEXT NOT NULL, revision INTEGER NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS history (entry_id TEXT, revision INTEGER, snapshot TEXT NOT NULL)')
    con.commit()
    os.chmod(DB, 0o600)
    return con

def listing():
    with connect() as con:
        rows = [dict(r) for r in con.execute('SELECT * FROM entries ORDER BY time DESC')]
        for row in rows:
            row['history'] = [json.loads(h[0]) for h in con.execute('SELECT snapshot FROM history WHERE entry_id=? ORDER BY revision DESC', (row['id'],))]
        return rows

def save(data, actor='本人'):
    if not isinstance(data, dict): raise ValueError('日报必须是一个对象')
    if data.get('business') not in BUSINESSES: raise ValueError('请选择有效业务')
    if data.get('status') not in STATUSES: raise ValueError('请选择有效状态')
    content = data.get('content')
    if not isinstance(content, str) or not content.strip() or len(content)>5000: raise ValueError('工作内容需为1至5000字')
    value = data.get('time', '')
    if not isinstance(value, str) or len(value) not in (10,16): raise ValueError('时间格式应为 YYYY-MM-DD 或 YYYY-MM-DDTHH:mm')
    datetime.strptime(value, '%Y-%m-%d' if len(value)==10 else '%Y-%m-%dT%H:%M')
    key = data.get('id') or str(uuid.uuid4())
    if not isinstance(key, str) or len(key)>100: raise ValueError('无效记录编号')
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        old = con.execute('SELECT * FROM entries WHERE id=?', (key,)).fetchone()
        if old:
            if all(old[k] == data.get(k) for k in ('business','time','content','status')) and old['author'] == actor:
                return dict(old)
            if old['business'] != data['business']: raise ValueError('不可移动到其他业务')
            if data.get('revision') != old['revision']: raise ValueError('记录已更新，请刷新后重新编辑，避免覆盖其他人的修改')
            con.execute('INSERT INTO history VALUES (?,?,?)', (key,old['revision'],json.dumps(dict(old),ensure_ascii=False)))
        elif data.get('revision') not in (None, 0): raise ValueError('原记录不存在，请刷新')
        row = dict(id=key,business=data['business'],time=value,content=content.strip(),status=data['status'],author=actor,updatedAt=datetime.now().astimezone().isoformat(),revision=(old['revision']+1 if old else 1))
        con.execute('INSERT OR REPLACE INTO entries VALUES (:id,:business,:time,:content,:status,:author,:updatedAt,:revision)',row)
    return row

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT/'src/web'),**kwargs)
    def log_message(self,*args): pass
    def end_headers(self):
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control','no-cache')
        super().end_headers()
    def allowed(self):
        host=self.headers.get('Host','').lower()
        peer=ipaddress.ip_address(self.client_address[0])
        return host in self.server.allowed_hosts and any(peer in network for network in self.server.allowed_networks)
    def reply(self,status,body):
        raw=json.dumps(body,ensure_ascii=False).encode('utf-8'); self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def _get(self):
        if not self.allowed(): return self.reply(403,{'error':'该地址未获准访问'})
        if self.path=='/assets/fonts/pipi-diary.ttf': return self.serve_asset(FONT_DIR/'pipi-diary.ttf')
        if self.path=='/api/todos/sync': return self.reply(200,reminders_sync.status() if tenant_storage.current()=='owner' else reminders_sync._default_status())
        if self.path=='/api/todos': return self.reply(200,{'tasks':todos.listing()})
        if self.path=='/api/crm': return self.reply(200,crm.state())
        if self.path=='/api/recipes': return self.reply(200,recipes.state())
        if urlsplit(self.path).path=='/api/crm/detail':
            try:
                query=parse_qs(urlsplit(self.path).query,max_num_fields=1)
                return self.reply(200,crm.detail(query.get('id',[''])[0]))
            except ValueError as e:return self.reply(400,{'error':str(e)})
        if self.path.startswith('/crm-assets/'):
            try:
                from urllib.parse import quote
                p,meta=crm.asset(self.path[len('/crm-assets/'):]);raw=p.read_bytes()
                self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(meta['name']))
                self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','private, no-store')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
            except (ValueError,OSError):return self.reply(404,{'error':'文件不存在'})
        if self.path=='/api/people': return self.reply(200,people.state())
        if self.path=='/api/people/reminders': return self.reply(200,{'reminders':people.state()['reminders']})
        if self.path=='/api/people/export':
            raw=people.archive();self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename=tiangong-people.zip');self.send_header('Cache-Control','private, no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.path=='/api/treasures': return self.reply(200,treasures.state())
        if self.path=='/api/games': return self.reply(200,games.overview())
        if self.path=='/api/games/connection': return self.reply(200,games.connection_status())
        for prefix, kind in (('/travel-assets/','travel'),('/treasure-assets/','treasures'),('/receipt-assets/','receipts'),('/people-assets/','people')):
            if self.path.startswith(prefix):
                try: return self.serve_asset(private_images.resolve(kind,self.path[len(prefix):]))
                except ValueError: return self.reply(404,{'error':'图片不存在'})
        if self.path.startswith('/api/games/steam-covers/'):
            try: return self.serve_asset(games.steam_cover_file(self.path.rsplit('/',1)[-1]))
            except (ValueError,OSError): return self.reply(404,{'error':'封面暂不可用'})
        if self.path.startswith('/api/games/covers/'):
            try: return self.serve_asset(games.cover_file(self.path.rsplit('/',1)[-1]))
            except ValueError: return self.reply(404,{'error':'封面不存在'})
        if self.path=='/api/travel': return self.reply(200,travel.state())
        if self.path=='/api/finance': return self.reply(200,finance.state())
        if urlsplit(self.path).path=='/api/finance/hub':
            try:
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=1)
                if query and (set(query)!={'month'} or len(query['month'])!=1):raise ValueError('查询条件无效')
                return self.reply(200,finance_hub.state(query.get('month',[None])[0]))
            except ValueError as e:return self.reply(400,{'error':str(e)})
        if self.path=='/api/checkins': return self.reply(200,checkins.state())
        if self.path=='/api/daily-quote': return self.reply(200,daily_quote.today())
        if self.path=='/api/status': return self.reply(200,{'service':'tiangong','mode':'lan' if self.server.lan else 'local','version':34})
        if self.path=='/api/wellness': return self.reply(200,wellness.state())
        if self.path=='/api/notes': return self.reply(200,{'notes':notes.all_notes(include_deleted=True)})
        if self.path.startswith('/api/notes/asset/'):
            try: return self.serve_asset(notes.asset(self.path[len('/api/notes/asset/'):]))
            except ValueError: return self.reply(404,{'error':'附件不存在'})
        if self.path.startswith('/api/report/'):
            parts=self.path[len('/api/report/'):].split('/')
            if len(parts) not in (1,2) or not all(parts):return self.reply(404,{'error':'附件不存在'})
            try:return self.serve_asset(wellness.report_asset(*parts))
            except ValueError:return self.reply(404,{'error':'附件不存在'})
        if urlsplit(self.path).path in ('/api/receipts','/api/receipts/history'):
            try:
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=1)
                result=receipts.history(query.get('id',[''])[0]) if urlsplit(self.path).path.endswith('/history') else receipts.state(query.get('month',[None])[0])
                return self.reply(200,result)
            except ValueError as e:return self.reply(400,{'error':str(e)})
        if self.path=='/api/work': return self.reply(200,{'mode':'local-service','entries':listing()})
        if self.path.startswith('/api/'): return self.reply(404,{'error':'接口不存在'})
        super().do_GET()
    def tenant_request(self, action):
        # Only the loopback gateway may select a tenant. An ordinary LAN request
        # must sign in; the old household trust boundary cannot serve two users.
        secret=getattr(self.server,'ingress_secret',None)
        if secret:
            try:
                if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                    raise ValueError('请登录天工录')
                identity=tenant_storage.verified(self.headers,secret,self.command,self.path)
            except ValueError:
                if self.command=='GET' and urlsplit(self.path).path in ('/','/index.html'):
                    self.send_response(303);self.send_header('Location',self.server.public_origin+'/login');self.send_header('Cache-Control','no-store');self.end_headers();return
                return self.reply(401,{'error':'请通过天工录登录入口访问'})
        else:
            # A deliberately standalone development server, never production.
            identity='owner'
        if identity=='demo' and self.command=='POST' and self.path!='/api/weather':
            return self.reply(403,{'error':'体验空间不保存修改'})
        with tenant_storage.scope(identity):
            return action()
    def do_GET(self):return self.tenant_request(self._get)
    def do_HEAD(self):
        # Do not inherit SimpleHTTPRequestHandler's unauthenticated HEAD path.
        return self.reply(405,{'error':'请使用 GET'})
    def do_POST(self):return self.tenant_request(self._post)

    def serve_asset(self,path):
        if not path.is_file():return self.reply(404,{'error':'附件不存在'})
        self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream');self.send_header('Content-Length',str(path.stat().st_size));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','private, no-store');self.end_headers()
        with path.open('rb') as f:
            import shutil
            shutil.copyfileobj(f,self.wfile)
    def _post(self):
        origin=self.headers.get('Origin'); expected='http://'+self.headers.get('Host','')
        if not self.allowed() or origin != expected: return self.reply(403,{'error':'仅允许已配置的同源页面写入'})
        if self.path not in ('/api/recipes','/api/quanku/shared','/api/crm','/api/crm/upload','/api/work','/api/wellness/report','/api/notes','/api/todos','/api/todos/sync','/api/travel','/api/finance','/api/finance/manage','/api/checkins','/api/travel/upload','/api/treasures','/api/treasures/use','/api/treasures/upload','/api/games','/api/games/connection','/api/games/sync','/api/weather','/api/receipts','/api/receipts/upload','/api/people','/api/people/upload'): return self.reply(404,{'error':'接口不存在'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=(18*1024*1024 if self.path in ('/api/crm/upload','/api/wellness/report','/api/notes','/api/travel/upload','/api/treasures/upload','/api/receipts/upload','/api/people/upload','/api/games') else 200000): raise ValueError('请求过大或为空')
            data=json.loads(self.rfile.read(length))
            if self.path=='/api/todos/sync':
                if tenant_storage.current()!='owner':return self.reply(403,{'error':'此账号尚未连接苹果提醒事项'})
                if not isinstance(data,dict):raise ValueError('同步请求无效')
                if data.get('action')=='sync':reminders_sync.wake()
                elif data.get('action')=='resolve':
                    reminders_sync.resolve(data.get('todo_id'),data.get('choice'),data.get('token'))
                    nas_backup.request_backup()
                else:raise ValueError('同步操作无效')
                return self.reply(200,reminders_sync.status())
            if not isinstance(data,dict):raise ValueError('请求格式无效')
            if self.path=='/api/quanku/shared':
                try: return self.reply(200,quanku_shared.handle(data))
                except PermissionError as e: return self.reply(403,{'error':str(e)})
            if self.path=='/api/weather':return self.reply(200,weather.handle(data))
            if self.path in ('/api/travel/upload','/api/treasures/upload','/api/receipts/upload','/api/people/upload'):
                kind={'/api/travel/upload':'travel','/api/treasures/upload':'treasures','/api/receipts/upload':'receipts','/api/people/upload':'people'}[self.path]
                asset=private_images.store(kind,data.get('data_url'));nas_backup.request_backup()
                return self.reply(200,{'asset':asset})
            if self.path=='/api/travel' and data.get('action','save') in ('save','restore') and 'photos' in data:
                private_images.validate_ids('travel',data['photos'])
            if self.path=='/api/recipes':
                result=recipes.handle(data)
            elif self.path=='/api/crm/upload':
                result={'asset':crm.upload(data)}
            elif self.path=='/api/crm':
                result={'record':crm.save(data)}
            elif self.path=='/api/people':
                result={'record':people.save(data)}
            elif self.path=='/api/finance/manage':
                result=finance_hub.manage(data)
            elif self.path=='/api/receipts':
                result={'record':receipts.save(data)}
            elif self.path=='/api/treasures':
                if data.get('action','save')=='save' and data.get('photo'):private_images.validate_ids('treasures',[data['photo']])
                result={'record':treasures.save(data)}
            elif self.path=='/api/treasures/use':result={'record':treasures.use(data)}
            elif self.path=='/api/games':result={'record':games.save(data)}
            elif self.path=='/api/games/connection':return self.reply(200,games.configure_connection(data))
            elif self.path=='/api/games/sync':result=games.sync_steam()
            else:result=None
            if result is not None:
                self.reply(200,result);nas_backup.request_backup();return
            result=checkins.save(data) if self.path=='/api/checkins' else finance.save(data) if self.path=='/api/finance' else travel.save(data) if self.path=='/api/travel' else todos.save(data) if self.path=='/api/todos' else wellness.save_report(data) if self.path=='/api/wellness/report' else notes.save(data) if self.path=='/api/notes' else save(data)
            self.reply(200,result if self.path=='/api/checkins' else {'entry':result})
            nas_backup.request_backup()
            if self.path=='/api/todos' and tenant_storage.current()=='owner':reminders_sync.wake()
        except (recipes.ConflictError,crm.ConflictError,people.ConflictError,receipts.ConflictError,travel.ConflictError,finance.ConflictError,checkins.ConflictError,treasures.ConflictError) as e: self.reply(409,{'error':str(e)})
        except PermissionError as e: self.reply(403,{'error':str(e)})
        except (ValueError,TypeError,json.JSONDecodeError) as e: self.reply(400,{'error':str(e)})
        except Exception: self.reply(500,{'error':'保存失败，数据库暂不可用，请稍后重试'})

class LocalWebServer(ThreadingHTTPServer):
    # The gateway can forward 24 concurrent requests during a cold page load.
    # Keep the listen queue from dropping their initial connections.
    request_queue_size = 128


def main():
    global DB
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,help='仅测试使用独立数据库'); sub=p.add_subparsers(dest='cmd',required=True)
    server=sub.add_parser('serve'); server.add_argument('--port',type=int,default=8765);server.add_argument('--lan',action='store_true');server.add_argument('--network',default='192.168.1.0/24');server.add_argument('--lan-ip',default='192.168.1.100');server.add_argument('--hostname',default='tiangong.local')
    sub.add_parser('list'); up=sub.add_parser('save'); up.add_argument('--file',type=Path,required=True); up.add_argument('--author',choices=['本人','山君'],default='山君')
    a=p.parse_args()
    if a.db: DB=a.db
    if a.cmd=='serve':
        connect().close()
        server=LocalWebServer(('0.0.0.0' if a.lan else '127.0.0.1',a.port),Handler)
        server.lan=a.lan
        security=runtime_config.STATE_DIR / 'security'
        secret=security/'upstream-secret'
        if a.lan and not secret.exists():
            server.server_close()
            raise RuntimeError('未找到登录网关配置，家庭网络入口保持关闭。请先配置认证网关。')
        if secret.exists():
            server.ingress_secret=secret.read_text().strip()
            import re
            if not re.fullmatch('[0-9a-f]{64}',server.ingress_secret):raise RuntimeError('服务间认证配置无效，拒绝启动')
            server.public_origin=json.loads((security/'gateway.json').read_text())['origin']
        server.allowed_networks=[ipaddress.ip_network('127.0.0.0/8')]+([ipaddress.ip_network(a.network)] if a.lan else [])
        server.allowed_hosts={h+':'+str(a.port) for h in ['127.0.0.1','localhost']+([a.lan_ip,a.hostname.lower()] if a.lan else [])}
        print('Tiangong ready on http://'+(a.lan_ip if a.lan else '127.0.0.1')+':'+str(a.port),flush=True)
        nas_backup.request_backup()
        if not a.db:reminders_sync.start(on_change=nas_backup.request_backup)
        server.serve_forever()
    elif a.cmd=='list': print(json.dumps(listing(),ensure_ascii=False))
    else:
        print(json.dumps(save(json.loads(a.file.read_text(encoding='utf-8')),a.author),ensure_ascii=False))
        if not a.db and cloud_authority.config()['mode'] == 'local':
            # Return the committed record promptly; NAS latency must not hold
            # the CLI reply open. Detached worker retains backup status/errors.
            import subprocess, sys
            log_path = DB.parent / 'cli-backup.log'
            worker = "import fcntl, pathlib, nas_backup, runtime_config; p=runtime_config.STATE_DIR / 'cli-backup.lock'; f=p.open('a'); fcntl.flock(f, fcntl.LOCK_EX); print(nas_backup.backup(), flush=True)"
            try:
                with log_path.open('ab') as log:
                    subprocess.Popen([sys.executable, '-c', worker],
                        cwd=str(Path(__file__).resolve().parent),
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        start_new_session=True, close_fds=True)
            except OSError as error:
                print('Record saved; background backup could not start: ' + str(error), file=sys.stderr)

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'work_server')

if __name__=='__main__': main()
