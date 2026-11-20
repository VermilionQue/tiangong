"""Fresh visitor examples. Generates data locally; never reads another tenant.

All dates are relative to the China-local entry date. No source workbook, real
person, credentials, notification channel or external service is copied.
"""
import base64
from datetime import datetime, timedelta
import io
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw

import tenant_storage

NOTE = '虚构演示资料，不是真实记录。'


def art(index=0, portrait=False):
    image = Image.new('RGB', (480, 320), ['#d8e8f5', '#e9e3cf', '#dbe7e2'][index % 3])
    draw = ImageDraw.Draw(image)
    if portrait:
        draw.ellipse((180, 55, 300, 175), fill='#f2c9a7')
        draw.rounded_rectangle((125, 190, 355, 370), radius=70, fill=['#426a94','#7773aa','#4f8178'][index%3])
        draw.arc((180, 55, 300, 185), 180, 340, fill='#354255', width=22)
        draw.ellipse((211, 111, 216, 116),fill='#354255');draw.ellipse((264,111,269,116),fill='#354255')
        draw.arc((223,122,256,147),0,180,fill='#aa7367',width=3)
    else:
        draw.ellipse((350, 30, 410, 90), fill='#efc76b')
        draw.polygon([(0,260),(130,70),(300,320),(0,320)],fill='#718d9a')
        draw.polygon([(120,320),(330,100),(480,280),(480,320)],fill='#9fb9b6')
        draw.rectangle((0,270,480,320),fill='#729eb4')
    draw.text((18,18), 'FICTIONAL DEMO', fill='#354255')
    b=io.BytesIO();image.save(b,'PNG');return b.getvalue()


def pdf(title):
    content=f'BT /F1 22 Tf 50 740 Td ({title}) Tj 0 -40 Td /F1 12 Tf (Fictional demo - not a real contract or construction plan.) Tj ET\n0.3 0.5 0.7 RG 50 380 490 270 re S 50 500 m 540 500 l S 270 380 m 270 650 l S'.encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    raw=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    start=len(raw);raw+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    raw+=b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:])
    return raw+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


def workbook():
    stream=io.BytesIO()
    files={
      '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
      '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="演示报价" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>虚构演示报价，不是真实合同</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>客厅灯光套餐</t></is></c><c r="B2"><v>6800</v></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>智能控制套餐</t></is></c><c r="B3"><v>4200</v></c></row><row r="4"><c r="A4" t="inlineStr"><is><t>合计</t></is></c><c r="B4"><f>SUM(B2:B3)</f><v>11000</v></c></row></sheetData></worksheet>'}
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,body in files.items():z.writestr(name,body)
    return stream.getvalue()


def seed(identity, today=None):
    if not re.fullmatch(r'u[0-9a-f]{32}',identity):raise ValueError('游客资料只能写入新的独立空间')
    today=today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with tenant_storage.scope(identity):
        root=tenant_storage.root()
        if root.exists():raise ValueError('游客空间必须为空，不能覆盖已有资料')
        root.mkdir(parents=True,mode=0o700)
        try:
            _populate(today)
            marker=root/'guest-example-v47.json'
            marker.write_text(json.dumps({'version':47,'fictional':True,'created_on':today.isoformat()},ensure_ascii=False));marker.chmod(0o600)
        except Exception:
            # This call created the directory; a refused existing tenant is never removed.
            shutil.rmtree(root)
            raise


def _populate(today):
    import checkins, crm, finance, finance_hub, notes, people, private_images, receipts, todos, travel, treasures, wellness, work_server
    def day(offset=0):return (today+timedelta(days=offset)).isoformat()
    month=today.strftime('%Y-%m')
    start=today.replace(day=1)
    def save_hub(action, key, **fields):
        return finance_hub.manage(dict(action=action,id=key,request_id='guest-'+key+'-'+action,source_note=NOTE,**fields))['entity']
    def image(kind,index=0,portrait=False):
        return private_images.store(kind,'data:image/png;base64,'+base64.b64encode(art(index,portrait)).decode())['id']
    for i,(title,offset,moment) in enumerate([
        ('确认晨光小筑的灯光方案',0,'10:00'),('和设计师沟通客厅布局',0,'14:30'),('傍晚散步三十分钟',0,'18:00'),
        ('整理本周收支',1,'11:00'),('周末去湖边拍照',3,''),('读完一本喜欢的书',None,''),('试做一道新菜',None,''),('整理旅行相册',None,'')]):
        task=todos.save(dict(id=f'guest-todo-{i}',title=title,due=day(offset) if offset is not None else '',due_time=moment,priority='normal',note=NOTE))
    for i in range(3):
        task=todos.save(dict(id=f'guest-done-{i}',title=['买好周末食材','完成方案初稿','整理书桌'][i],due=day(-i),due_time='',priority='normal',note=NOTE))
        todos.save(dict(task,done=1))
    for i,(title,body) in enumerate([
        ('生活灵感','给重要的事情留时间，也给自己留一点空白。 #日常'),('方案备忘','客厅阅读区需要一束柔和的光，餐厅照顾到家人一起吃饭的氛围。 #工作'),('旅行清单','去山里走走，带上相机和一本书。 #旅行')]):
        notes.save(dict(id=f'guest-note-{i}',html=f'<p>{title}</p><p>{body}</p><p>{NOTE}</p>'))
    for i,(name,kind,cents) in enumerate([('星河银行储蓄卡','bank',2867000),('零钱钱包','e_wallet',86500),('旅行备用金','bank',520000),('投资账户','investment',1850000)]):
        save_hub('account_save',f'guest-account-{i}',name=name,kind=kind,balance_cents=cents,as_of=day(),confirmed=True)
    menu=[('菜市场买菜','买菜做饭',4200),('午间简餐','下馆子',3600),('咖啡与果汁','咖啡饮料',2400),('清洁用品','日用百货',5800),('电影票','娱乐',6800),('周末聚餐','下馆子',16800)]
    for i in range(42):
        name,category,cents=menu[i%len(menu)]
        finance.save(dict(id=f'guest-expense-{i}',date=day(-(i%max(today.day,1))),name=name,type='expense',cents=cents,category=category,budget_category=category,track='daily',confirmed=True,account_id='guest-account-0',note=NOTE))
    finance.save(dict(id='guest-income',date=start.isoformat(),name='示例月度工资',type='income',cents=1480000,category='工资收入',track='daily',account_id='guest-account-0',note=NOTE))
    for i,track in enumerate(('fudai','smart_home','zhejiang_tv','stocks','qqsg')):
        for kind,cents in [('income',180000+i*32000),('expense',38000+i*4500)]:
            finance.save(dict(id=f'guest-{track}-{kind}',date=day(-min(5,today.day-1)),name=('项目结算' if kind=='income' else '项目成本')+' · 示例',type=kind,cents=cents,category='副业与投资',track=track,account_id='guest-account-0',note=NOTE))
    for category,amount in [('餐饮',180000),('日用',45000),('娱乐',60000)]:
        save_hub('budget_save','budget-'+str(amount),month=month,category=category,cents=amount)
    for key,name,kind,principal,rate,due in [
        ('home','星河银行住房贷款','mortgage',42800000,320,16),('loan','星河银行消费贷','interest_only',2800000,360,18),
        ('card','星河信用卡','credit_card',186000,None,8),('family','亲友周转款','personal',1500000,0,None),('short','已结清的短期周转','short_term',0,None,None)]:
        fields=dict(name=name,kind=kind,principal_cents=principal,annual_rate_bp=rate,due_day=due,as_of=day(),confirmed=True)
        if kind=='mortgage':fields.update(term_months=240,monthly_payment_cents=241720)
        if kind=='short_term':fields.update(original_principal_cents=300000,borrowed_on=day(-20))
        save_hub('debt_save','guest-debt-'+key,**fields)
    save_hub('wish_save','guest-wish-camera',name='一台记录旅行的相机',price_cents=680000)
    save_hub('settings_save','settings',debt_inventory_confirmed=True)
    def save_crm(data):
        return crm.save(dict(data,request_id='guest-request-'+data['id']))
    unit=save_crm(dict(id='guest-unit',kind='unit',name='拾光设计工作室',source='示例设计师渠道',note=NOTE))
    member=save_crm(dict(id='guest-member',kind='member',name='小禾（示例）',unit_id=unit['id'],note=NOTE))
    attachments=[]
    for category,name,body in [('price','示例价格表.xlsx',workbook()),('proposal','示例方案.pdf',pdf('Lighting proposal')),('construction','示例施工图.pdf',pdf('Construction plan')),('contract','示例合同.pdf',pdf('Sample agreement')),('payment','示例收款凭证.png',art(1))]:
        a=crm.upload(dict(name=name,data=base64.b64encode(body).decode()));attachments.append(dict(id=a['id'],category=category,name=name))
    for i,(name,stage) in enumerate([('晨光小筑 · 林女士','成交'),('云栖花园 · 江先生','方案中'),('晴川公寓 · 许女士','首触'),('望山里 · 叶先生','报价中')]):
        save_crm(dict(id=f'guest-customer-{i}',kind='customer',name=name,stage=stage,unit_id=unit['id'],member_id=member['id'],contact_on=day(-12),signed_on=start.isoformat() if stage=='成交' else '',source='设计师转介绍（虚构）',budget_cents=1800000+i*300000,home_type='三室两厅',area=str(118+i*15),community=name.split(' · ')[0],business_types=['全屋智能','开关面板'],control_points='客观需求：先确认阅读区照明和家庭网络覆盖，再沟通设备配置。',attachments=attachments if i==0 else [],note=NOTE))
    photo=image('receipts',1)
    for i,(owner,amount) in enumerate([('林女士（示例）',680000),('江先生（示例）',320000) ]):
        receipts.save(dict(id=f'guest-receipt-{i}',request_id=f'guest-receipt-request-{i}',received_on=day(-min(i,today.day-1)),project='晨光小筑灯光方案' if i==0 else '云栖网络方案',owner=owner,amount_cents=amount,address='虚构演示地址',photos=[photo],note=NOTE))
    for i,business in enumerate(work_server.BUSINESSES):
        work_server.save(dict(id=f'guest-work-{i}',business=business,time=day(),content=['和示例客户沟通方案需求，准备下一轮报价。','整理项目收支，确认下周的工作安排。'][i%2]+' '+NOTE,status='已完成'))
    training={}
    for i in range(21):
        if i%3==1:continue
        training[day(-i)]={'items':[{'name':['户外步行','骑行','力量训练'][i%3],'start':'18:00','end':'18:40','minutes':40,'kcal':180+i*12,'source':'虚构演示'}],'moves':[],'note':NOTE}
        checkins.save(dict(date=day(-i),expected_revision=0,record=dict(exercise=40,notes=1,work=[],note='今天也动一动。'+NOTE)))
    (tenant_storage.root()/'training-cache.json').write_text(json.dumps(training,ensure_ascii=False))
    for i in range(3):
        wellness.save_report(dict(id=f'guest-body-{i}',kind='体测',date=day(-30*i),title='示例体测报告',height=172,weight=68.2+i*.6,metrics={'bodyFatPercent':19.4+i*.4,'skeletalMuscleKg':29.1-i*.1},note=NOTE,source='虚构示例',attachments=[dict(name='示例报告.pdf',base64=base64.b64encode(pdf('Sample body assessment')).decode())]))
    wellness.save_report(dict(id='guest-health',kind='健康报告',date=day(-10),title='健康资料留存示例',note='这里可以保存检查报告与就诊记录。本条仅展示归档方式，不包含诊断结论。'+NOTE))
    _,places=travel.catalogue()
    wanted=['浙江','云南','四川','日本','意大利']
    visited=0
    for (scope,pid),place in places.items():
        if any(w in place['name'] for w in wanted):
            travel.save(dict(scope=scope,place_id=pid,visited_on=day(-40-visited*12),city=place['name'],note='慢慢走，记下沿途的风景。'+NOTE,photos=[image('travel',visited)]));visited+=1
    for i,(name,amount,icon) in enumerate([('随身相机',580000,'sparkle'),('降噪耳机',129900,'sparkle'),('骑行头盔',39900,'sparkle')]):
        treasures.save(dict(id=f'guest-treasure-{i}',request_id=f'guest-treasure-request-{i}',name=name,brand='虚构示例',category=['数码','数码','运动'][i],purchase_cents=amount,purchased_on=day(-120-i*40),uses=30+i*20,icon=icon,photo=image('treasures',i),note=NOTE))
    for i,name in enumerate(['小林（示例）','阿遥（示例）','小川（示例）']):
        p=people.save(dict(id=hashlib.sha256(f'guest-person-{i}'.encode()).hexdigest()[:32],name=name,met_on=str(today.year-4+i),met_context='在读书会认识，后来常一起散步。',bio='喜欢摄影、旅行和分享最近读过的书。'+NOTE,bond='一起看过日落，也在忙碌的时候互相鼓励。',city='杭州',tags=['读书会','朋友'],last_contact_on=day(-7-i*20),reminder_days=30,affinity=4,pressure=1,meet_per_month=2,chat_per_week=2,photos=[image('people',i,True)]))
        people.save(dict(id=hashlib.sha256(f'guest-moment-{i}'.encode()).hexdigest()[:32],kind='moment',title='一起散步的午后',body='从湖边走到书店，聊最近的小目标。'+NOTE,happened_on=day(-7-i*20),event_type='共同经历',people_ids=[p['id']],photos=[image('people',i)]))
