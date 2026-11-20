"""Import a privately exported Feishu workbook. Dry-run by default; no money writes."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import crm
import tenant_storage


def prepare(source,sha):
    rows=[];units={};members={};unmatched=[]
    def sheet(name):
        values=source[name]
        return [(i,dict(zip(values[0],v))) for i,v in enumerate(values[1:],2)]
    def base(kind,name,sheetname,index,legacy):
        key='feishu-'+hashlib.sha256(f'{sheetname}\0{index}\0{name}'.encode()).hexdigest()[:32]
        return dict(id=key,kind=kind,name=name,revision=1,createdAt=crm.now(),updatedAt=crm.now(),deletedAt='',phone='',address='',note='',source_sha256=sha,source_sheet=sheetname,source_row=index,legacy=legacy)
    for i,d in sheet('渠道单位管理'):
        name=str(d.get('单位名称') or '').strip()
        r=base('unit',name or f'原表未命名渠道 · 第{i}行','渠道单位管理',i,d);r['contact']=''
        if name:units[name]=r['id']
        else:r['deletedAt']=crm.now();r['note']='飞书原表未填写单位名称，保留原始资料，待需要时恢复补充。'
        rows.append(r)
    for i,d in sheet('渠道成员管理'):
        name=str(d.get('推单人全名') or '').strip()
        if not name:raise ValueError('渠道成员缺少名称')
        r=base('member',name,'渠道成员管理',i,d);r.update(unit_id=units.get(str(d.get('所在单位') or '').strip(),''),contact='')
        if d.get('所在单位') and not r['unit_id']:unmatched.append({'sheet':'渠道成员管理','row':i,'field':'所在单位'})
        rows.append(r);members[name]=r
    customer_sheet=next(k for k in source if k.startswith('客户管理'))
    def text(v):return str(v or '').strip()
    def date(v):return str(v)[:10] if v else ''
    for i,d in sheet(customer_sheet):
        name=text(d.get('姓名-项目'))
        if not name:raise ValueError('客户缺少名称')
        r=base('customer',name,customer_sheet,i,d);source_name=text(d.get('线索来源'));member=members.get(source_name)
        budget=d.get('预算预估');budget=None if budget is None or budget=='' else int((Decimal(str(budget))*100).quantize(Decimal('1')))
        stage=text(d.get('把控')) or '首触'
        if stage not in crm.STAGES: raise ValueError(f'第{i}行进度需要确认')
        r.update(source=source_name,member_id=member['id'] if member else '',unit_id=member['unit_id'] if member else units.get(source_name,''),stage=stage,
                 contact_on=date(d.get('首触时间')),next_follow_on=date(d.get('待跟进日')),budget_cents=budget,control_points=text(d.get('跟进动作（客观）')),
                 home_type=text(d.get('户型')),area=text(d.get('面积')),community='',address=text(d.get('项目地址（XX区-XX路XX号-XX小区-XX号）')),
                 phone=text(d.get('线索电话') or d.get('合同电话')),delivery_stage=text(d.get('交付阶段')) if stage=='成交' else '',attachments=[],followups=[])
        subjective=text(d.get('把控要点（主观）'))
        if subjective:r['note']='原表主观判断（保留原话）：\n'+subjective
        if source_name and not member and not r['unit_id']:unmatched.append({'sheet':customer_sheet,'row':i,'field':'线索来源'})
        rows.append(r)
    return rows,unmatched


def import_file(path,apply=False):
    import openpyxl
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    for sheet in book: sheet.reset_dimensions()
    source={s.title:[[v.isoformat(sep=' ') if hasattr(v,'isoformat') else v for v in row] for row in s.iter_rows(values_only=True)] for s in book};book.close()
    rows,unmatched=prepare(source,sha)
    customers=[r for r in rows if r['kind']=='customer'];counts=Counter(r['stage'] for r in customers)
    summary={'source_sha256':sha,'customers':len(customers),'stages':dict(counts),'units':sum(r['kind']=='unit' for r in rows),'named_units':sum(r['kind']=='unit' and not r['deletedAt'] for r in rows),'members':sum(r['kind']=='member' for r in rows),'unmatched':unmatched,'applied':False}
    if not apply:return summary
    if unmatched:raise ValueError('存在未确认关联，停止导入')
    with crm.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        done=con.execute('SELECT data FROM imports WHERE sha=?',(sha,)).fetchone()
        if done:return {**json.loads(done[0]),'already_imported':True}
        for r in rows:
            existing=con.execute('SELECT data FROM records WHERE id=?',(r['id'],)).fetchone()
            if existing:raise ValueError('此来源记录已导入过其他版本，请先核对差异')
        for r in rows:con.execute('INSERT INTO records VALUES(?,?,?)',(r['id'],r['kind'],crm.dumps(r)))
        folder=Path(tenant_storage.path('crm-sources'));folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        dest=folder/(sha+'.xlsx')
        if not dest.exists():dest.write_bytes(raw);dest.chmod(0o600)
        summary['applied']=True
        con.execute('INSERT INTO imports VALUES(?,?)',(sha,crm.dumps(summary)))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('file',type=Path);p.add_argument('--apply',action='store_true');args=p.parse_args()
    print(json.dumps(import_file(args.file,args.apply),ensure_ascii=False,indent=2))
