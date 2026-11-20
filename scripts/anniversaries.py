"""Annual private dates; HKO lunar tables and the existing authoritative todo API.

No scheduler or notification transport is created here. OpenClaw runs sync and
preview at its existing reminder jobs. Personal dates live outside the repository.
"""

import runtime_config
import argparse
import calendar
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
import urllib.request

BASE = runtime_config.STATE_DIR / 'anniversaries'
REGISTRY = BASE / 'events.json'
ZONE = timezone(timedelta(hours=8))
MONTHS = ['正', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二']
DAYS = ['初一','初二','初三','初四','初五','初六','初七','初八','初九','初十',
        '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十',
        '廿一','廿二','廿三','廿四','廿五','廿六','廿七','廿八','廿九','三十']


def today():
    return datetime.now(ZONE).date()


def parse_table(text, year):
    raw = []
    for line in text.splitlines():
        match = re.match(r'(\d+)年(\d+)月(\d+)日\s+(\S+)', line)
        if match:
            raw.append((date(*map(int, match.group(1, 2, 3))), match[4]))
    expected = 366 if calendar.isleap(year) else 365
    if len(raw) != expected or any(d != date(year, 1, 1) + timedelta(days=i)
                                   for i, (d, _) in enumerate(raw)):
        raise ValueError('香港天文台年历不完整，停止换算')
    first = next(token for _, token in raw if token.endswith('月'))
    month = MONTHS.index(first.replace('閏', '').replace('闰', '')[:-1]) + 1
    if not raw[0][1].endswith('月'):
        month = (month - 2) % 12 + 1
    lunar_year, leap, result = year - 1, False, []
    for solar, token in raw:
        if token.endswith('月'):
            leap = token.startswith(('閏', '闰'))
            month = MONTHS.index(token.replace('閏', '').replace('闰', '')[:-1]) + 1
            day = 1
            if month == 1 and not leap:
                lunar_year = year
        else:
            day = DAYS.index(token) + 1
        result.append((solar, lunar_year, month, day, leap))
    return result


@lru_cache(maxsize=16)
def table(year):
    if not 1901 <= year <= 2100:
        raise ValueError('年历超出支持范围，请核对香港天文台资料')
    path = BASE / 'calendar' / f'T{year}c.txt'
    if path.exists():
        return parse_table(path.read_text(encoding='utf-8-sig'), year)
    url = f'https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt'
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read(100001)
    if len(body) > 100000:
        raise ValueError('年历响应过大')
    result = parse_table(body.decode('utf-8-sig'), year)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(body)
    temporary.replace(path)
    return result


def events():
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    rows = value['events']
    if len({e['id'] for e in rows}) != len(rows):
        raise ValueError('纪念日编号重复')
    return rows


def occurrences(event, after, count=2):
    result = []
    for year in range(after.year, min(after.year + 4, 2101)):
        if event['calendar'] == 'solar':
            candidates = [(date(year, event['month'], event['day']), year)]
        elif event['calendar'] == 'lunar':
            candidates = [(d, y) for d, y, m, day, leap in table(year)
                          if (m, day, leap) == (event['month'], event['day'], False)]
        else:
            raise ValueError('不支持的历法')
        for due, recurrence_year in candidates:
            if due >= after:
                result.append((due, recurrence_year))
                if len(result) == count:
                    return result
    raise ValueError('无法找到下一次纪念日，未猜测日期')


def describe(event, due, recurrence_year):
    if event['kind'] == 'birthday':
        birth = date.fromisoformat(event['birth_solar'])
        years = due.year - birth.year - ((due.month, due.day) < (birth.month, birth.day))
        measure = f'当日{years}周岁'
    else:
        years = recurrence_year - event['origin_year']
        measure = f'{years}周年'
    reminder = due - timedelta(days=7)
    lunar_label = (f'农历{MONTHS[event["month"]-1]}月{DAYS[event["day"]-1]}'
                   if event['calendar'] == 'lunar' else '公历')
    title = f'{event["title"]}（{measure}）'
    return {'event_id': event['id'], 'title': title, 'date': due.isoformat(),
            'calendar': event['calendar'], 'calendar_label': lunar_label,
            'years': years, 'measure': measure, 'recurrence_year': recurrence_year,
            'reminder_at': reminder.isoformat() + 'T09:00:00+08:00',
            'message': f'提前7天提醒：{due.isoformat()}是{event["title"]}（{lunar_label}），{measure}。'}


def preview(event, after):
    current, following = [describe(event, *item) for item in occurrences(event, after)]
    return {**current, 'next_date': following['date'],
            'next_reminder_at': following['reminder_at']}


def sync(rows, after, store=None):
    if store is None:
        import todos as store
    existing = {r['id']: r for r in store.listing()}
    # Validate every date before making any writes. Never restore user-deleted
    # occurrences, overwrite edits/completions, or access SQLite directly.
    planned = []
    for event in rows:
        for due, year in occurrences(event, after):
            item = describe(event, due, year)
            key = f'anniversary:{event["id"]}:{year}'
            note = (f'原始日期：{event["original"]}\n年度规则：{item["calendar_label"]}；'
                    '每年续记。\n提醒：提前7天，北京时间09:00，经山君微信提醒。\n'
                    f'本次提醒：{item["reminder_at"]}\n'
                    '生日周岁按公历出生日期计算；农历生日可能早于公历生日。\n'
                    f'来源：{event["source"]}\n年度事件：{event["id"]}')
            planned.append(dict(id=key, title='全天 ' + item['title'], note=note,
                                due=item['date'], due_time='', priority='normal',
                                done=0, deleted=0))
    created = []
    for row in planned:
        if row['id'] not in existing:
            store.save(row)
            created.append(row['id'])
    actual = {r['id']: r for r in store.listing()}
    if any(key not in actual for key in created):
        raise RuntimeError('保存后回读失败，请核对原编号，不要重复新建')
    return {'as_of': after.isoformat(), 'events': len(rows), 'created': created,
            'preserved': len(planned) - len(created), 'verified': len(planned)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['sync', 'preview'])
    parser.add_argument('--event')
    parser.add_argument('--on', type=date.fromisoformat, default=today())
    args = parser.parse_args()
    rows = events()
    if args.event:
        rows = [e for e in rows if e['id'] == args.event]
        if not rows:
            parser.error('没有该事件')
    value = sync(rows, args.on) if args.command == 'sync' else [preview(e, args.on) for e in rows]
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
