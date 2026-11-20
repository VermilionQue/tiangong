"""Private relationship memories, explicit contact reminders and portable archives."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import io
import json
import re
import sqlite3
import uuid
import zipfile
import tenant_storage
import private_images

DB = tenant_storage.path('people.sqlite3')
KEY = re.compile(r'[0-9a-f]{32}')
STAGES = ('常联系', '重逢中', '某段同行', '暂时少联系', '珍藏')
KINDS = ('见面', '聊天', '共同经历', '重要时刻', '随记')
LEVELS = ('相识', '熟悉', '亲近', '知心')

class ConflictError(ValueError):
    pass

def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()

def stamp():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')

@contextmanager
def connection():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(str(DB), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA secure_delete=ON')
    try:
        DB.chmod(0o600)
        con.executescript('''CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL,
          deleted INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS history (
          id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(id,revision));''')
        con.commit()
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def text(value, limit, name, required=False):
    if not isinstance(value, str) or len(value) > limit or '\0' in value:
        raise ValueError(f'{name}最多 {limit} 字')
    value = value.strip()
    if required and not value:
        raise ValueError('请填写' + name)
    return value

def day(value, name, partial=False, future=False):
    value = text(value, 10, name)
    if not value:
        return ''
    pattern = r'\d{4}(?:-\d{2}(?:-\d{2})?)?' if partial else r'\d{4}-\d{2}-\d{2}'
    if not re.fullmatch(pattern, value):
        raise ValueError(name + ('请填年份、年月或完整日期' if partial else '请填完整日期'))
    parts = list(map(int, value.split('-')))
    try:
        date(*(parts + [1] * (3-len(parts))))
    except ValueError:
        raise ValueError(name + '无效') from None
    if not future and tuple(parts) > (today().year, today().month, today().day)[:len(parts)]:
        raise ValueError(name + '不能晚于今天')
    return value

def number(value, maximum, name, minimum=0):
    if value is None:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name}请填 {minimum}～{maximum} 的整数，不清楚可以留空')
    return value

def ids(value, maximum=20):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(x,str) or not KEY.fullmatch(x) for x in value):
        raise ValueError('人物关联无效')
    return list(dict.fromkeys(value))

def photos(value):
    if not isinstance(value,list) or len(value)>8:
        raise ValueError('每条记录最多 8 张照片')
    return private_images.validate_ids('people', value)

def rows(con):
    return [json.loads(r['data']) for r in con.execute('SELECT data FROM records ORDER BY id')]

def save(payload):
    if not isinstance(payload,dict):
        raise ValueError('请求格式无效')
    kind = payload.get('kind','person')
    action = payload.get('action','save')
    if kind not in ('person','moment') or action not in ('save','delete','restore','snooze','purge'):
        raise ValueError('操作无效')
    key = payload.get('id') or uuid.uuid4().hex
    if not isinstance(key,str) or not KEY.fullmatch(key):
        raise ValueError('记录编号无效')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT data FROM records WHERE id=?',(key,)).fetchone()
        old = json.loads(row['data']) if row else None
        revision = payload.get('revision',0)
        if type(revision) is not int or revision != (old['revision'] if old else 0):
            raise ConflictError('这条记录已更新，请刷新后再保存；你的输入仍保留在表单中')
        if old and old['kind'] != kind:
            raise ValueError('记录类型不一致')
        if action != 'save' and not old:
            raise ValueError('记录不存在')
        if action == 'purge':
            return purge_record(con, old)
        result = dict(old or {'id':key,'kind':kind,'created_at':stamp(),'deleted':False})
        if action == 'save':
            if old and old['deleted']:
                raise ValueError('请先恢复记录再编辑')
            def get(k, default=''):
                return payload.get(k,result.get(k,default))
            if kind == 'person':
                for k,limit,label in [('name',80,'姓名或昵称'),('alias',100,'备注称呼'),('met_context',1000,'怎样认识'),('city',100,'所在城市'),('bio',3000,'这个人是谁'),('bond',3000,'我们的羁绊'),('preferences',2000,'喜好与小事'),('boundaries',2000,'相处感受与边界')]:
                    result[k]=text(get(k),limit,label,k=='name')
                result['met_on']=day(get('met_on'),'认识时间',partial=True)
                result['last_contact_on']=day(get('last_contact_on'),'最近联系日期')
                result['stage']=get('stage','常联系')
                result['level']=get('level','auto')
                if result['stage'] not in STAGES or result['level'] not in ('auto',)+LEVELS:
                    raise ValueError('请选择有效的关系阶段和关系层次')
                tags=get('tags',[])
                if not isinstance(tags,list) or len(tags)>12:raise ValueError('圈子标签最多 12 个')
                result['tags']=list(dict.fromkeys(text(t,30,'圈子标签',True) for t in tags))
                for k,maximum,label,minimum in [('affinity',5,'喜欢程度',1),('pressure',5,'相处压力',1),('meet_per_month',60,'每月见面次数',0),('chat_per_week',100,'每周聊天次数',0)]:
                    result[k]=number(get(k,None),maximum,label,minimum)
                result['reminder_days']=number(get('reminder_days',0),3650,'提醒间隔')
                if result['reminder_days'] is None:result['reminder_days']=0
                result['reminder_since']=result.get('reminder_since') or today().isoformat()
                if result['reminder_days'] and old and not old.get('reminder_days'):
                    result['reminder_since']=today().isoformat()
                result['snooze_until']=result.get('snooze_until','')
                result['photos']=photos(get('photos',[]))
            else:
                result['title']=text(get('title'),160,'经历标题',True)
                result['body']=text(get('body'),12000,'经历内容')
                result['happened_on']=day(get('happened_on'),'发生时间',partial=True)
                result['location']=text(get('location'),150,'地点')
                result['event_type']=get('event_type','共同经历')
                if result['event_type'] not in KINDS:raise ValueError('请选择经历类型')
                result['people_ids']=ids(get('people_ids',[]))
                if not result['people_ids']:raise ValueError('请选择至少一位朋友')
                for person_id in result['people_ids']:
                    person=con.execute("SELECT kind,deleted FROM records WHERE id=?",(person_id,)).fetchone()
                    if not person or person['kind']!='person' or person['deleted']:raise ValueError('关联人物不存在或已移入回收站')
                result['photos']=photos(get('photos',[]))
        elif action == 'snooze':
            if kind!='person':raise ValueError('只能暂缓人物联系提醒')
            days=number(payload.get('days'),365,'暂缓天数',1)
            if days is None:raise ValueError('请填写暂缓天数')
            result['snooze_until']=(today()+timedelta(days=days)).isoformat()
        else:
            result['deleted']=action=='delete'
        if old:
            con.execute('INSERT INTO history VALUES (?,?,?)',(key,old['revision'],json.dumps(old,ensure_ascii=False)))
        result.update(revision=revision+1,updated_at=stamp())
        con.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)',(key,kind,result['revision'],int(result['deleted']),json.dumps(result,ensure_ascii=False)))
        return result

def purge_record(con, old):
    """Hard-delete a trashed record and its dependent memories in this tenant only.

    Old revisions count as references too: a previously unlinked memory must
    not retain the erased person's details. Shared photo bytes remain only if
    another independent record or revision still uses them.
    """
    if not old['deleted']:
        raise ValueError('请先将记录移到回收站，再彻底删除')
    current = rows(con)
    historic = [json.loads(r['data']) for r in con.execute('SELECT data FROM history')]
    erase = {old['id']}
    if old['kind'] == 'person':
        erase.update(r['id'] for r in current + historic
                     if r.get('kind') == 'moment' and old['id'] in r.get('people_ids', []))
    removed_photos = {k for r in current + historic if r['id'] in erase for k in r.get('photos', [])}
    retained_photos = {k for r in current + historic if r['id'] not in erase for k in r.get('photos', [])}
    paths = []
    for key in removed_photos - retained_photos:
        try:
            paths.append(private_images.resolve('people', key))
        except ValueError:
            # Already-missing images do not block erasing the owning record.
            continue
    for key in erase:
        con.execute('DELETE FROM history WHERE id=?', (key,))
        con.execute('DELETE FROM records WHERE id=?', (key,))
    con.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    return {'id':old['id'], 'kind':old['kind'], 'purged':True,
            'removed_records':len(erase), 'removed_photos':len(paths)}

def reference(person, moments, at):
    """Explicit inputs and recorded contacts only; missing evidence is not zero."""
    missing=[]
    for key,label in [('met_on','认识时间'),('affinity','喜欢程度'),('pressure','相处压力')]:
        if person.get(key) in (None,''):missing.append(label)
    contact=[m for m in moments if not m['deleted'] and m['event_type'] in ('聊天','见面') and len(m['happened_on'])==10 and at-timedelta(days=89)<=date.fromisoformat(m['happened_on'])<=at]
    meeting=person.get('meet_per_month');chat=person.get('chat_per_week')
    if meeting is None and not any(m['event_type']=='见面' for m in contact):missing.append('见面频率')
    if chat is None and not any(m['event_type']=='聊天' for m in contact):missing.append('聊天频率')
    if missing:return {'level':None,'score':None,'missing':missing,'reasons':[],'manual':person.get('level')!='auto'}
    year=int(person['met_on'][:4]);years=max(0,at.year-year)
    points=min(20,years*4)
    meeting_score=min(20,(meeting if meeting is not None else sum(m['event_type']=='见面' for m in contact)/3)*5)
    chat_score=min(20,(chat if chat is not None else sum(m['event_type']=='聊天' for m in contact)/13)*5)
    affinity=(person['affinity']-1)*6.25
    comfort=(5-person['pressure'])*3.75
    score=round(points+meeting_score+chat_score+affinity+comfort)
    level=LEVELS[3 if score>=80 else 2 if score>=55 else 1 if score>=30 else 0]
    return {'level':level,'score':score,'missing':[], 'manual':person.get('level')!='auto','reasons':[
        f'相识年数参考 {points}/20',f'见面频率 {round(meeting_score,1)}/20',f'聊天频率 {round(chat_score,1)}/20',f'喜欢程度 {round(affinity,1)}/25',f'相处轻松程度 {round(comfort,1)}/15'],
        'basis':'频率优先采用你填写的近况；留空时使用近 90 天已记录的见面或聊天。相识年数按年份粗略计算。'}

def state(at=None):
    at=at or today()
    with connection() as con:all_rows=rows(con)
    people=[r for r in all_rows if r['kind']=='person']
    moments=sorted((r for r in all_rows if r['kind']=='moment'),key=lambda r:(r['happened_on'],r['created_at']),reverse=True)
    by_person={p['id']:[] for p in people}
    for m in moments:
        for key in m['people_ids']:
            if key in by_person:by_person[key].append(m)
    due=[]
    for p in people:
        related=by_person[p['id']]
        dates=[m['happened_on'] for m in related if not m['deleted'] and m['event_type'] in ('聊天','见面') and len(m['happened_on'])==10]
        if p.get('last_contact_on'):dates.append(p['last_contact_on'])
        last=max(dates,default='')
        p['last_contact']=last
        p['reference']=reference(p,related,at)
        p['display_level']=p['level'] if p['level']!='auto' else (p['reference']['level'] or '尚未判断')
        p['moments_count']=sum(not m['deleted'] for m in related)
        p['reminder_due_on']=''
        if p['reminder_days'] and not p['deleted']:
            base=date.fromisoformat(last or p['reminder_since'])
            due_on=(base+timedelta(days=p['reminder_days'])).isoformat()
            if p.get('snooze_until'):due_on=max(due_on,p['snooze_until'])
            p['reminder_due_on']=due_on
            if due_on<=at.isoformat():
                due.append({'id':p['id'],'name':p['name'],'last_contact':last,'due_on':due_on,'days_since':(at-base).days,'revision':p['revision']})
    return {'people':people,'moments':moments,'reminders':sorted(due,key=lambda r:r['due_on']),'as_of':at.isoformat()}

def archive():
    """User-authored Markdown, original precision and private photos; no vendor code."""
    data=state(); stream=io.BytesIO()
    assets={photo for r in data['people']+data['moments'] for photo in r['photos']}
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as out:
        out.writestr('people.json',json.dumps(data,ensure_ascii=False,indent=2))
        names={p['id']:p['name'] for p in data['people']}
        for p in data['people']:
            if p['deleted']:continue
            lines=[f"# {p['name']}",'',f"认识时间：{p['met_on'] or '尚未记录'}",f"圈子：{'、'.join(p['tags'])}",f"阶段：{p['stage']}",f"关系层次：{p['display_level']}",'']
            for label,key in [('这个人是谁','bio'),('怎样认识','met_context'),('我们的羁绊','bond'),('喜好与小事','preferences'),('相处感受与边界','boundaries')]:lines.extend(['## '+label,p[key] or '尚未记录',''])
            for photo in p['photos']:assets.add(photo);lines.append(f'![照片](../photos/{photo})')
            lines+=['','## 共同经历']
            for m in reversed(data['moments']):
                if m['deleted'] or p['id'] not in m['people_ids']:continue
                lines.extend(['',f"### {m['happened_on'] or '日期未详'} · {m['title']}",f"{m['event_type']}　{m['location']}",m['body'],'同行的人：'+ '、'.join(f"[[{pid}|{names.get(pid,'已移除的人物')}]]" for pid in m['people_ids'])])
                for photo in m['photos']:assets.add(photo);lines.append(f'![照片](../photos/{photo})')
            out.writestr(f"people/{p['id']}.md",'\n'.join(lines))
        for photo in sorted(assets):out.write(private_images.resolve('people',photo),'photos/'+photo)
        out.writestr('README.md','# 天工录 · 人际\n\npeople/ 是人物 Markdown，可在 Obsidian 中打开。照片位于 photos/；people.json 保留全部记录、原始日期精度及回收状态。关系建议仅供本人整理记忆。\n')
    return stream.getvalue()


# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'people')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['reminders']);args=parser.parse_args()
    print(json.dumps({'reminders':state()['reminders']},ensure_ascii=False))
