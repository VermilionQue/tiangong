"""Idempotent fictional examples, written only in the isolated demo workspace."""
from datetime import date, timedelta
import tenant_storage
import todos
import notes
import finance
import receipts


def seed(today=None):
    today = today or date.today()
    with tenant_storage.scope('demo'):
        marker = tenant_storage.path('demo-seeded-v33')
        if marker.exists():
            return
        # No source account is queried or copied. All labels and amounts below
        # are intentionally fictional and the HTTP demo account cannot write.
        existing = {r['id']: r for r in todos.listing()}
        for i, (title, offset, moment) in enumerate([
            ('给生活留一点空白', 0, '09:00'), ('读完一章喜欢的书', 0, '11:00'),
            ('出门散步，看看秋天', 0, '17:00'), ('整理本周的小目标', 1, ''),
            ('周末去逛公园', 2, ''), ('想学的一道新菜', None, '')]):
            key = 'demo-todo-' + str(i)
            todos.save(dict(id=key, revision=existing.get(key, {}).get('revision', 0), title=title,
                            due=(today + timedelta(days=offset)).isoformat() if offset is not None else '',
                            due_time=moment, note='这是一条体验用的示例记录。', priority='normal'))
        if not notes.all_notes(True):
            notes.save(dict(id='demo-note', html='<p>#日常灵感 今天发现了一条安静的小路。把想法记下来，慢慢长成自己的生活。</p>'))
        if not finance.state()['records']:
            for i, (name, kind, cents, category) in enumerate([
                ('示例月度收入', 'income', 1200000, '工资收入'),
                ('买一束花', 'expense', 3800, '生活'),
                ('午餐', 'expense', 2800, '餐饮'),
                ('一本新书', 'expense', 5900, '学习')]):
                finance.save(dict(id='demo-finance-' + str(i), date=today.isoformat(), name=name,
                                  type=kind, cents=cents, category=category, note='虚构示例，不是真实账目'))
        if not receipts.state()['records']:
            receipts.save(dict(id='demo-receipt', request_id='demo-receipt-v33', received_on=today.isoformat(),
                               project='示例灯光项目', owner='示例业主', amount_cents=680000,
                               address='示例地址', photos=[], note='虚构的收款示例'))
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker.write_text(today.isoformat())
        marker.chmod(0o600)


if __name__ == '__main__':
    seed()
    print('独立体验资料已就绪。')
