"""Apply an explicitly reviewed workbook delta; never infer matching by amount.

This is a local import tool, without an HTTP mutation route. Old entry IDs and
original evidence survive, and every changed row retains its previous revision.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import finance
from import_finance import archive_source


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,100}', value):
        raise ValueError('核对来源编号无效')
    return value


def checked_row(row):
    values = finance.fields(row)
    signed = row.get('signed_cents')
    if type(signed) is not int or abs(signed) != values['cents']:
        raise ValueError('核对金额与资金方向不一致')
    direction = 'in' if signed > 0 else 'out' if signed < 0 else 'zero'
    if (row.get('direction') != direction
            or (values['type'] in ('income', 'refund') and direction != 'in')
            or (values['type'] == 'expense' and direction != 'out')):
        raise ValueError('核对收支类型与原金额方向不一致')
    groups = row.get('groups', [])
    if not isinstance(groups, list) or any(not isinstance(g, str) or len(g) > 100 for g in groups):
        raise ValueError('核对分组无效')
    return {**row, **values, 'id': key(row['id'])}


def apply_delta(payload):
    metadata = payload['metadata']
    source = metadata['sha256']
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{64}', source):
        raise ValueError('核对文件摘要无效')
    fingerprint = digest(payload)
    updates, additions = payload.get('updates', []), payload.get('additions', [])
    ids = [key(row['id']) for row in updates + additions]
    if len(set(ids)) != len(ids):
        raise ValueError('核对清单中来源编号重复')
    stamp = datetime.now().astimezone().isoformat()
    with finance.connection() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS reconciliation_evidence (
          digest TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,
          PRIMARY KEY(digest,id))''')
        con.commit()
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT metadata FROM imports WHERE digest=?', (source,)).fetchone()
        if existing:
            saved = json.loads(existing[0])
            if saved.get('delta_fingerprint') != fingerprint:
                raise finance.ConflictError('同一原表已有另一份核对结果，未覆盖')
            return {**saved['delta_result'], 'already_applied': True}
        prepared = []
        allowed = set(finance.fields({'date':'2000-01-01','cents':1,'type':'expense',
                                     'name':'schema','category':'schema'})) | {'signed_cents','direction','groups','reason'}
        for update in updates:
            found = con.execute('SELECT data FROM entries WHERE id=?', (update['id'],)).fetchone()
            old = json.loads(found[0]) if found else None
            if old is None or digest(old) != update.get('source_hash'):
                raise finance.ConflictError('账目已变化，需要重新核对：'+update['id'])
            if old.get('voidedAt') or old.get('managed_by') == 'debt_payment':
                raise finance.ConflictError('不能覆盖撤销或还款关联账目')
            changes = update['changes']
            if not isinstance(changes, dict) or set(changes)-allowed:
                raise ValueError('核对更改包含不可修改字段')
            new = checked_row({**old, **changes})
            if new != old:
                new.update(revision=old['revision']+1, updatedAt=stamp,
                           reconciliation_source=source)
            prepared.append((old, new, update.get('evidence', {})))
        for addition in additions:
            addition_allowed = allowed | {'id', 'source_row', 'evidence'}
            if set(addition)-addition_allowed:
                raise ValueError('新增核对账目包含不可导入字段')
            if con.execute('SELECT 1 FROM entries WHERE id=?', (addition['id'],)).fetchone():
                raise finance.ConflictError('新增来源编号已经存在')
            evidence = addition.get('evidence', {})
            new = checked_row({k:v for k,v in addition.items() if k != 'evidence'})
            new.update(source=source, revision=1, updatedAt=stamp)
            prepared.append((None, new, evidence))
        updated = unchanged = 0
        for old, new, evidence in prepared:
            if old and old != new:
                con.execute('INSERT INTO history VALUES(?,?,?)',
                            (old['id'], old['revision'], json.dumps(old, ensure_ascii=False)))
                updated += 1
            elif old:
                unchanged += 1
            if old != new:
                con.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?)',
                            (new['id'], json.dumps(new, ensure_ascii=False), new['revision']))
            if old is None:
                con.execute('INSERT INTO evidence VALUES(?,?)', (new['id'], json.dumps(evidence, ensure_ascii=False)))
            con.execute('INSERT INTO reconciliation_evidence VALUES(?,?,?)',
                        (source, new['id'], json.dumps(evidence, ensure_ascii=False)))
        result = {'updated':updated, 'added':len(additions),
                  'pending':sum(new['type'] == 'review' or not new.get('confirmed', True) for _,new,_ in prepared),
                  'unchanged':unchanged, 'already_applied':False}
        con.execute('INSERT INTO imports VALUES(?,?)',
                    (source, json.dumps({**metadata, 'delta_fingerprint':fingerprint,
                                         'delta_result':result}, ensure_ascii=False)))
    return result


def import_reviewed(source, prepared):
    payload = json.loads(Path(prepared).read_text(encoding='utf-8'))
    # A conflicting/stale delta leaves its reviewed evidence intact, while a
    # subsequently rechecked delta receives a separate immutable archive.
    archive_source(source, payload, review_id=digest(payload))
    return apply_delta(payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_reviewed(args.source, args.prepared), ensure_ascii=False))
