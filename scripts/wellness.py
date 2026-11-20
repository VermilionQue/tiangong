import runtime_config
# -*- coding: utf-8 -*-
import base64, calendar, hashlib, json, math, os, re, sqlite3, tempfile, uuid
from datetime import date, datetime
from pathlib import Path
import tenant_storage
from zoneinfo import ZoneInfo
BASE=tenant_storage.path('')
SOURCE=Path(os.environ.get('TIANGONG_HEALTH_IMPORT_DIR', str(runtime_config.STATE_DIR / 'imports/apple-health')))
KINDS=('体测','体重记录','体检','就诊','健康报告')
# Units are part of the API contract; values are transcribed, never diagnosed.
METRICS={
    'bodyFatPercent':('体脂率','%',0,100),
    'skeletalMuscleKg':('骨骼肌','kg',0,300),
    'bmi':('BMI','',1,150),
    'fatMassKg':('脂肪量','kg',0,500),
    'fatFreeMassKg':('去脂体重','kg',0,500),
    'muscleMassKg':('肌肉量','kg',0,500),
    'bodyWaterKg':('身体水分','kg',0,500),
    'bodyWaterPercent':('水分率','%',0,100),
    'proteinKg':('蛋白质','kg',0,100),
    'proteinPercent':('蛋白质率','%',0,100),
    'mineralsKg':('无机盐','kg',0,100),
    'boneMassKg':('骨量','kg',0,100),
    'visceralFatLevel':('内脏脂肪等级','',0,100),
    'waistHipRatio':('腰臀比','',0,5),
    'waistHipFatRatio':('腰臀脂肪比','',0,5),
    'robustnessIndex':('健壮指数','',0,100),
    'basalMetabolismKcal':('基础代谢','kcal',0,10000),
    'totalEnergyKcal':('报告总能量消耗','kcal',0,20000),
    'musclePercent':('肌肉率','%',0,100),
    'subcutaneousFatPercent':('皮下脂肪率','%',0,100),
}
MAX_ATTACHMENT_BYTES=12*1024*1024
MAX_ATTACHMENTS=8
ASSET_KEY=re.compile(r'[0-9a-f]{64}\.(?:pdf|png|jpg|jpeg)')
def connection():
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    c=sqlite3.connect(str(BASE/'wellness.sqlite3'),timeout=15);c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, data TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS reminder(id TEXT PRIMARY KEY, sent TEXT)')
    c.commit();(BASE/'wellness.sqlite3').chmod(0o600);return c

def report_attachments(row):
    """Read both versions without rewriting the original legacy JSON."""
    return row.get('attachments') or ([row['attachment']] if row.get('attachment') else [])

def public_report(row):
    row=dict(row)
    row['attachments']=report_attachments(row)
    row['attachment']=row['attachments'][0] if row['attachments'] else None
    row.setdefault('metrics',{})
    row.setdefault('source','')
    row.setdefault('provenance',{})
    return row

def reports():
    with connection() as c:
        return sorted([public_report(json.loads(r['data'])) for r in c.execute('SELECT data FROM reports')],key=lambda r:(r['date'],r.get('createdAt','')),reverse=True)

def report_asset(report_id, key=None):
    row=next((r for r in reports() if r['id']==report_id),None)
    attachments=report_attachments(row) if row else []
    item=next((a for a in attachments if a['key']==key),None) if key else next(iter(attachments),None)
    if not item or not ASSET_KEY.fullmatch(item.get('key','')):raise ValueError('附件不存在')
    folder=(BASE/'reports').resolve()
    path=folder/item['key']
    if path.is_symlink() or path.resolve().parent!=folder or not path.is_file():raise ValueError('附件不存在')
    return path
def next_month(d):
    d=date.fromisoformat(d);y=d.year+(d.month==12);m=d.month%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1])).isoformat()
def assessment_reminder_id(day):
    """A month's reminder survives report imports and old report-ID receipts."""
    return 'body-assessment:'+day.strftime('%Y-%m')
def reminder_date(today=None):
    return date.fromisoformat(today) if today else datetime.now(ZoneInfo('Asia/Shanghai')).date()
def reminder_sent(key):
    with connection() as c:return c.execute('SELECT 1 FROM reminder WHERE id=?',(key,)).fetchone() is not None
def next_assessment(today=None):
    day=reminder_date(today)
    if day.day==1 and not reminder_sent(assessment_reminder_id(day)):return day.isoformat()
    return next_month(day.replace(day=1).isoformat())
def due_reminder(today=None):
    """The existing health scheduler delivers at 11:00 on the first of a month.

    Keep this query read-only; only a confirmed delivery calls mark_reminded.
    There is no daily catch-up or per-report reminder after the monthly date.
    """
    day=reminder_date(today);key=assessment_reminder_id(day)
    if day.day!=1 or reminder_sent(key):return None
    return {'id':key,'due':day.isoformat(),'message':'新的一月，记得安排一次体测，并把报告发给山君或阿策。'}

def mark_reminded(key):
    with connection() as c:c.execute('INSERT OR IGNORE INTO reminder (id,sent) VALUES (?,?)',(key,datetime.now().astimezone().isoformat()))
def number(value,lo,hi,label):
    if isinstance(value,bool):raise ValueError('请核对'+label)
    try:value=float(value)
    except (ValueError,TypeError):raise ValueError('请核对'+label) from None
    if not math.isfinite(value) or not lo<=value<=hi:raise ValueError('请核对'+label)
    return value

def attachment_uploads(data):
    raw=data.get('attachments')
    if raw is not None and data.get('attachment'):raise ValueError('请只使用一种附件格式')
    if raw is None:raw=[data['attachment']] if data.get('attachment') else []
    if not isinstance(raw,list) or len(raw)>MAX_ATTACHMENTS:raise ValueError('最多添加8份原件')
    uploads=[];total=0;seen=set()
    for item in raw:
        if not isinstance(item,dict):raise ValueError('附件格式无效')
        name=str(item.get('name','')).replace('\\','/').split('/')[-1]
        ext=Path(name).suffix.lower()
        if ext not in ('.pdf','.png','.jpg','.jpeg') or len(name)>255:raise ValueError('请上传PDF、PNG或JPEG')
        encoded=item.get('base64','')
        if not isinstance(encoded,str) or len(encoded)>((MAX_ATTACHMENT_BYTES+2)//3)*4:raise ValueError('附件合计限12MB')
        try:blob=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise ValueError('附件编码无效') from None
        total+=len(blob)
        if not blob or total>MAX_ATTACHMENT_BYTES:raise ValueError('附件合计限12MB')
        if not (blob.startswith(b'%PDF-') if ext=='.pdf' else blob.startswith(b'\x89PNG\r\n\x1a\n') if ext=='.png' else blob.startswith(b'\xff\xd8\xff')):raise ValueError('附件内容与格式不符')
        digest=hashlib.sha256(blob).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        uploads.append(({'name':name,'key':digest+ext,'sha256':digest,'size':len(blob)},blob))
    return uploads

def store_attachment(item,blob):
    folder=BASE/'reports';folder.mkdir(exist_ok=True,mode=0o700)
    path=folder/item['key']
    if path.is_symlink():raise ValueError('附件保存位置无效')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('已存原件校验失败，请先核对原件')
        return
    fd,temp=tempfile.mkstemp(prefix='.report-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(blob);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def report_signature(row):
    result={k:row.get(k) for k in ('kind','date','title','height','weight')}
    result.update(note=row.get('note') or '',metrics=row.get('metrics') or {},source=row.get('source') or '',provenance=row.get('provenance') or {})
    result['files']=sorted(a.get('sha256') or a['key'].split('.')[0] for a in report_attachments(row))
    # File names and order are presentation; provenance keeps byte-identical sources.
    result['provenance']=dict(result['provenance'])
    result['provenance'].pop('sourceFiles',None)
    return result

def save_report(data):
    if not isinstance(data,dict):raise ValueError('报告必须是一个对象')
    if data.get('kind') not in KINDS:raise ValueError('请选择报告类型')
    if not isinstance(data.get('date'),str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data['date']):raise ValueError('报告日期格式应为YYYY-MM-DD')
    dt=date.fromisoformat(data.get('date',''))
    if dt>datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError('报告日期不能在未来')
    title=str(data.get('title','')).strip()
    if not title or len(title)>120:raise ValueError('请填写120字以内的标题')
    row={k:data.get(k) for k in ('kind','date','title','height','weight','note')}
    row['note']=str(row.get('note') or '')[:3000]
    for key,lo,hi in [('height',30,250),('weight',1,500)]:
        val=row[key]
        if val not in (None,''):
            row[key]=number(val,lo,hi,'身高体重单位：厘米、公斤')
        else:row[key]=None
    metrics=data.get('metrics',{})
    if not isinstance(metrics,dict) or set(metrics)-METRICS.keys():raise ValueError('身体指标字段无效')
    row['metrics']={k:number(v,*METRICS[k][2:],METRICS[k][0]) for k,v in metrics.items() if v not in (None,'')}
    row['source']=str(data.get('source') or '').strip()
    row['sourceRef']=str(data.get('sourceRef') or '').strip()
    if len(row['source'])>120 or len(row['sourceRef'])>200:raise ValueError('来源信息过长')
    uploads=attachment_uploads(data)
    row['attachments']=[a for a,_ in uploads]
    row['attachment']=row['attachments'][0] if uploads else None
    provenance=data.get('provenance') or {}
    if not isinstance(provenance,dict) or set(provenance)-{'method','recordedBy','measuredAt','metricSources'}:raise ValueError('来源记录格式无效')
    row['provenance']={}
    for key in ('method','recordedBy','measuredAt'):
        if provenance.get(key):
            value=provenance[key]
            if not isinstance(value,str) or len(value)>120:raise ValueError('来源记录格式无效')
            row['provenance'][key]=value
    if provenance.get('method') and provenance['method'] not in ('report_transcription','self_report','manual'):raise ValueError('记录方式无效')
    if provenance.get('measuredAt'):
        try:measured=datetime.fromisoformat(provenance['measuredAt'])
        except ValueError:raise ValueError('测量时间无效') from None
        if measured.date()!=dt:raise ValueError('测量时间与报告日期不一致')
    field_sources=provenance.get('metricSources',{})
    if not isinstance(field_sources,dict) or set(field_sources)-{'height','weight',*row['metrics']}:raise ValueError('指标来源字段无效')
    hashes={a['sha256'] for a,_ in uploads}
    for key,values in field_sources.items():
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or v not in hashes for v in values):raise ValueError('指标来源须对应本次原件')
    if field_sources:row['provenance']['metricSources']={k:sorted(set(v)) for k,v in field_sources.items()}
    if uploads:row['provenance']['sourceFiles']=[{'name':a['name'],'sha256':a['sha256'],'size':a['size']} for a,_ in uploads]
    row['id']=str(uuid.uuid4());row['createdAt']=datetime.now().astimezone().isoformat()
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        signature=report_signature(row)
        for saved in c.execute('SELECT data FROM reports'):
            old=json.loads(saved['data']);old_signature=report_signature(old)
            if old_signature==signature:return public_report(old)
            same_ref=bool(row['sourceRef']) and old.get('sourceRef')==row['sourceRef']
            same_files=bool(signature['files']) and old_signature['files']==signature['files'] and old['date']==row['date'] and old['kind']==row['kind']
            if same_ref or same_files:raise ValueError('这份原件或来源已留存，内容不同，请先核对已有档案')
        for item,blob in uploads:store_attachment(item,blob)
        c.execute('INSERT INTO reports (id,data) VALUES (?,?)',(row['id'],json.dumps(row,ensure_ascii=False,allow_nan=False)))
    return row

def save_report_from_files(data,paths):
    """Trusted local import only. HTTP uploads never accept local filesystem paths."""
    payload=dict(data)
    payload['attachments']=[{'name':Path(path).name,'base64':base64.b64encode(Path(path).read_bytes()).decode('ascii')} for path in paths]
    return save_report(payload)

def training_screenshots():
    """Latest revisions of user-provided training evidence, never HealthKit writes."""
    latest={}
    for pattern in ('训记训练档案_*_V*.json','运动_*_V*.json'):
        for path in (BASE/'training-imports').glob(pattern):
            try:
                row=json.loads(path.read_text())
                if not isinstance(row,dict) or not row.get('record_id') or row.get('tiangong_import',{}).get('confirmed') is not True:continue
                date.fromisoformat(row['date'])
                version=int(re.search(r'_V(\d+)\.json$',path.name)[1])
            except (OSError,ValueError,KeyError,TypeError):continue
            key=row['record_id']
            if key not in latest or version>latest[key][0]:latest[key]=(version,row)
    return [row for _,row in latest.values()]

def training_match(record,workouts):
    """Require identity or a unique matching session; same day alone is not a match."""
    exact=[i for i,w in enumerate(workouts) if w.get('id')==record['record_id']]
    if exact:return exact[0]
    def instant(value):
        try:
            value=re.sub(r'\s*([+-]\d{2})(\d{2})$',r'\1:\2',value).replace('Z','+00:00')
            parsed=datetime.fromisoformat(value)
            return (parsed.astimezone(ZoneInfo('Asia/Shanghai')) if parsed.tzinfo else parsed).replace(tzinfo=None)
        except (TypeError,ValueError):return None
    start,end=instant(record.get('start_local')),instant(record.get('end_local'))
    matches=[]
    for i,w in enumerate(workouts):
        duration=w.get('duration_seconds')
        if start and end:
            a,b=instant(w.get('start')),instant(w.get('end'))
            if w.get('name')==record.get('activity') and a and b and abs((a-start).total_seconds())<60 and abs((b-end).total_seconds())<60:
                matches.append(i)
        elif record.get('source_reported'):
            minutes=record['source_reported'].get('duration_minutes')
            if isinstance(minutes,(int,float)) and isinstance(duration,(int,float)) and abs(duration-minutes*60)<60 and ('训记' in str(w.get('source_app','')) or w.get('name')==record.get('title')):
                matches.append(i)
    return matches[0] if len(matches)==1 else None

def merge_training_screenshots(days,observed):
    for record in training_screenshots():
        day=record['date'];strength=record.get('source_reported',{})
        seconds=strength.get('duration_minutes')
        seconds=seconds*60 if isinstance(seconds,(int,float)) else record.get('duration_seconds')
        if not isinstance(seconds,(int,float)) or seconds<=0:continue
        title=record.get('title') or record.get('activity') or '训练'
        energy=strength.get('energy_kcal') if strength else record.get('active_energy_kcal')
        item={'id':record['record_id'],'name':title,'start':record.get('start_local','')[11:16],
              'end':record.get('end_local','')[11:16],'minutes':round(seconds/60),'duration_seconds':seconds,
              'kcal':energy,'source':'训记截图' if strength else 'Apple 健康截图','evidence':record}
        entry=days.setdefault(day,{'items':[],'moves':[],'note':''})
        entry['note']=entry['note'].replace('动作明细不包含尚未核实的组数或重量。','动作明细以留存原件为准。')
        match=training_match(record,observed.get(day,[]))
        cached=next((x for x in entry['items'] if record['record_id'] in (x.get('id'),x.get('record_id'),x.get('evidence',{}).get('record_id'))),None)
        if match is not None:
            # The Health export remains one session; screenshot details are evidence.
            entry['items'][match]['evidence']=record
            entry['items'][match]['record_id']=record['record_id']
        elif cached is not None:
            if cached.get('source') in ('训记截图','Apple 健康截图'):cached.update(item)
            else:cached['evidence']=record
        else:
            placeholders=[x for x in entry['items'] if x.get('source')=='训记补充档案' and x.get('name')==title]
            if placeholders:entry['items'][entry['items'].index(placeholders[0])]=item
            elif not any(x.get('id')==record['record_id'] for x in entry['items']):entry['items'].append(item)
        details=[]
        for exercise in record.get('exercises',[]):
            name=exercise.get('name','')
            if name in entry['moves']:entry['moves'].remove(name)
            sets=exercise.get('sets',[])
            text=name+'：'+'、'.join(f"{s['recorded_weight_kg']} kg × {s['repetitions']}" for s in sets) if sets else name
            if text and text not in entry['moves']:entry['moves'].append(text)
        heart=record.get('heart_rate',{}).get('average_bpm') if strength else record.get('average_heart_rate_bpm')
        details.append(f'{title}：{int(seconds)//60}分{int(seconds)%60:02d}秒')
        if heart is not None:details.append(f'平均心率 {heart} 次/分')
        if strength.get('volume_kg') is not None:details.append(f"训练容量 {strength['volume_kg']} kg，{record.get('verified_set_count','—')} 组")
        if strength and energy is not None:details.append(f'训记显示消耗 {energy} 大卡（未区分动态/总热量）')
        if not strength:
            if energy is not None:details.append(f'动态 {energy} 大卡')
            if record.get('total_energy_kcal') is not None:details.append(f"总消耗 {record['total_energy_kcal']} 大卡（与动态不相加）")
        summary='；'.join(details)+'。'
        if summary not in entry['note']:entry['note']=(entry['note']+'\n'+summary).strip()
    return days

def training():
    if tenant_storage.current() != 'owner':
        try:return json.loads((BASE/'training-cache.json').read_text())
        except (OSError,ValueError):return {}
    latest={}
    for path in SOURCE.glob('健康训练日报_*_V*.json'):
        m=re.search(r'_(\d{4}-\d{2}-\d{2})_V(\d+)\.json$',path.name)
        if m and (m[1] not in latest or int(m[2])>latest[m[1]][0]):latest[m[1]]=(int(m[2]),path)
    days={};observed={}
    for day,(_,path) in latest.items():
        try:d=json.loads(path.read_text())
        except (ValueError,OSError):continue
        workouts=d.get('workouts',[]); selected=[]
        # Existing user rule: overlapping Xunji and Watch sessions keep Xunji only.
        for w in sorted(workouts,key=lambda w:('训记' not in str(w.get('source_app','')),w.get('start',''))):
            if any(w.get('start','')<x.get('end','') and w.get('end','')>x.get('start','') for x in selected):continue
            selected.append(w)
        observed[day]=selected
        items=[]
        for w in selected:
            items.append({'name':w.get('name','训练'),'start':w.get('start','')[11:16],'end':w.get('end','')[11:16],'minutes':round(w['duration_seconds']/60) if isinstance(w.get('duration_seconds'),(int,float)) else None,'kcal':round(w['active_kcal']) if isinstance(w.get('active_kcal'),(int,float)) else None,'source':'训记' if '训记' in str(w.get('source_app','')) else 'Apple 健康'})
        extra=d.get('supplemental_training',[])
        moves=[]
        for ex in extra:
            if not items:items.append({'name':ex.get('title','训练'),'minutes':None,'kcal':None,'source':'训记补充档案','start':'','end':''})
            moves.extend([x.get('name','') if isinstance(x,dict) else str(x) for x in ex.get('exercises',[])])
        if items:days[day]={'items':items,'moves':moves,'note':'重叠训练优先采用训记，时长和热量不叠加。动作明细不包含尚未核实的组数或重量。'}
    if not days:
        try:days=json.loads((BASE/'training-cache.json').read_text())
        except (OSError,ValueError):pass
    return merge_training_screenshots(days,observed)

def state(today=None):
    # Use one China-local day for both fields, including calls around midnight.
    today=reminder_date(today).isoformat()
    return {'reports':reports(),'training':training(),'nextAssessment':next_assessment(today),'reminder':due_reminder(today)}

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'wellness')
