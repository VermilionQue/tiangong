"""Shared personal tasks with optimistic revisions and recoverable trash."""
import json, sqlite3, uuid, argparse, re
from pathlib import Path
import tenant_storage
from datetime import datetime, date
DB=tenant_storage.path('todos.sqlite3')
def completion_time(value):
    """Validate trusted completion metadata; all stored instants include an offset."""
    if not isinstance(value,str):raise ValueError('完成时间无效')
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError()
    except ValueError:raise ValueError('完成时间应包含时区')
    return stamp.isoformat()

def historical_completion(c,row):
    # Walk back through this completed run. A later note edit is not completion.
    first=dict(row)
    for snapshot in c.execute('SELECT revision,data FROM history WHERE id=? ORDER BY revision DESC',(row['id'],)):
        if snapshot['revision']!=first['revision']-1:return ''
        try:previous=json.loads(snapshot['data'])
        except (ValueError,TypeError):return ''
        if not isinstance(previous,dict) or previous.get('done') not in (0,1) or previous.get('revision')!=snapshot['revision']:return ''
        if not previous['done']:
            try:return completion_time(first.get('updated'))
            except ValueError:return ''
        first=previous
    # A legacy record that was already complete when first seen has no proven
    # completion date. Keep it unknown rather than assigning today's date.
    return ''

def con():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
    # Serialize the schema check so concurrent requests cannot both add the column.
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,title TEXT,note TEXT,due TEXT,due_time TEXT NOT NULL DEFAULT '',priority TEXT,done INTEGER,deleted INTEGER,created TEXT,updated TEXT,revision INTEGER)")
        if 'due_time' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT NOT NULL DEFAULT ''")
        c.execute('CREATE TABLE IF NOT EXISTS history(id TEXT,revision INTEGER,data TEXT,PRIMARY KEY(id,revision))')
        if 'completed_at' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''")
            for row in c.execute('SELECT * FROM tasks WHERE done=1').fetchall():
                c.execute('UPDATE tasks SET completed_at=? WHERE id=?',(historical_completion(c,row),row['id']))
        c.commit()
    except Exception:
        c.close()
        raise
    return c
def listing():
    with con() as c:return [dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created DESC')]
def save(d,*,completed_at=None):
    if not isinstance(d,dict):raise ValueError('无效待办')
    title=d.get('title','');note=d.get('note','');due=d.get('due','');priority=d.get('priority','normal')
    if not isinstance(title,str) or not title.strip() or len(title)>200:raise ValueError('请填写200字以内的待办')
    if not isinstance(note,str) or len(note)>5000:raise ValueError('备注最多5000字')
    if not isinstance(due,str):raise ValueError('日期格式不正确')
    if due:
        try:
            if date.fromisoformat(due).isoformat()!=due:raise ValueError()
        except ValueError:raise ValueError('日期格式应为YYYY-MM-DD')
    if priority not in ['normal','important']:raise ValueError('优先级无效')
    done=d.get('done',0);deleted=d.get('deleted',0)
    if done not in (0,1) or deleted not in (0,1):raise ValueError('状态无效')
    key=d.get('id') or str(uuid.uuid4())
    if not isinstance(key,str) or len(key)>100:raise ValueError('编号无效')
    now=datetime.now().astimezone().isoformat()
    with con() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM tasks WHERE id=?',(key,)).fetchone()
        # Older clients do not know this field; their updates retain its value.
        due_time=d.get('due_time',old['due_time'] if old else '')
        if not due and old and old['due']:
            due_time=''
        if not isinstance(due_time,str) or (due_time and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',due_time)):
            raise ValueError('时间格式应为HH:mm，例如09:30')
        if due_time and not due:raise ValueError('请先设置日期，再设置时间')
        values=dict(title=title.strip(),note=note,due=due,due_time=due_time,priority=priority,done=int(done),deleted=int(deleted))
        if old:
            if all(old[k]==value for k,value in values.items()):return dict(old)
            if d.get('revision')!=old['revision']:raise ValueError('这条待办已在另一处修改，请刷新后重试')
            c.execute('INSERT INTO history(id,revision,data) VALUES(?,?,?)',(key,old['revision'],json.dumps(dict(old),ensure_ascii=False)))
        # Only native/internal importers may supply a historical completion
        # instant. Ignore a browser payload's completed_at field.
        finished=''
        if done:
            finished=old['completed_at'] if old and old['done'] else (completion_time(completed_at) if completed_at is not None else now)
        row=dict(id=key,**values,completed_at=finished,created=old['created'] if old else now,updated=now,revision=old['revision']+1 if old else 1)
        c.execute('INSERT OR REPLACE INTO tasks(id,title,note,due,due_time,priority,done,deleted,created,updated,revision,completed_at) VALUES(:id,:title,:note,:due,:due_time,:priority,:done,:deleted,:created,:updated,:revision,:completed_at)',row)
        return dict(c.execute('SELECT * FROM tasks WHERE id=?',(key,)).fetchone())

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'todos')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--file',type=Path);a=p.parse_args()
    print(json.dumps(save(json.loads(a.file.read_text())) if a.file else listing(),ensure_ascii=False))
