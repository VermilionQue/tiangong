"""Accounts, debt facts, wishes and budgets in the existing private ledger.

No seed data, no inferred repayments and no second authoritative database.
Every management mutation and its ledger entries share one SQLite transaction.
"""
import calendar
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo

import finance

ConflictError = finance.ConflictError
MAX_CENTS = 99_999_999_999
ACCOUNT_KINDS = ('cash', 'bank', 'e_wallet', 'investment', 'other')
BUDGET_GROUPS = {'餐饮': ('买菜做饭', '下馆子', '咖啡饮料'), '日用': ('日用百货',), '娱乐': ('娱乐',)}
BUDGET_GROUP_KEYS = {'餐饮':'dining', '日用':'daily', '娱乐':'entertainment'}
DEBT_KINDS = ('mortgage', 'interest_only', 'short_term', 'credit_card', 'personal')
ACTIONS = ('account_save', 'debt_save', 'debt_payment', 'payment_void',
           'wish_save', 'budget_save', 'budget_suggest', 'settings_save', 'card_statement_save')


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def now():
    return datetime.now().astimezone().isoformat()


def day(value, nullable=False):
    if nullable and value in (None, ''):
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('日期应为YYYY-MM-DD')
    date.fromisoformat(value)
    return value


def month_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', value):
        raise ValueError('月份应为YYYY-MM')
    day(value+'-01')
    return value


def integer(value, label, nullable=False, minimum=0, maximum=MAX_CENTS):
    if nullable and value is None:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label}需为有效整数，金额使用分；未知请留空')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,100}', value):
        raise ValueError('记录编号无效')
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f'{label}需明确选择')
    return value


def source_note(data, old):
    return finance.raw_text(data.get('source_note', (old or {}).get('source_note', '')), 50000, '来源原文')


@contextmanager
def connection():
    with finance.connection() as con:
        con.executescript('''
          CREATE TABLE IF NOT EXISTS hub_entities (
            kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
            revision INTEGER NOT NULL, PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS hub_history (
            kind TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
            data TEXT NOT NULL,action TEXT NOT NULL,changed_at TEXT NOT NULL,
            PRIMARY KEY(kind,id,revision));
          CREATE TABLE IF NOT EXISTS hub_requests (
            request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
            response TEXT NOT NULL,created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS hub_reviews (
            id TEXT PRIMARY KEY,source_digest TEXT NOT NULL,
            metadata TEXT NOT NULL,response TEXT NOT NULL);
        ''')
        con.commit()
        yield con


def get(con, kind, key):
    row = con.execute('SELECT data FROM hub_entities WHERE kind=? AND id=?', (kind, key)).fetchone()
    return json.loads(row[0]) if row else None


def entities(con, kind):
    return [json.loads(row[0]) for row in con.execute('SELECT data FROM hub_entities WHERE kind=? ORDER BY id', (kind,))]


def put(con, kind, key, values, old, action):
    stamp = now()
    row = {**values, 'id': key, 'revision': (old['revision'] if old else 0)+1,
           'createdAt': old['createdAt'] if old else stamp, 'updatedAt': stamp}
    if old:
        con.execute('INSERT INTO hub_history VALUES(?,?,?,?,?,?)',
                    (kind, key, old['revision'], json.dumps(old, ensure_ascii=False), action, stamp))
    con.execute('INSERT OR REPLACE INTO hub_entities VALUES(?,?,?,?)',
                (kind, key, json.dumps(row, ensure_ascii=False), row['revision']))
    return row


def history(kind, key):
    if kind not in ('account', 'debt', 'payment', 'wish', 'budget', 'settings', 'card_statement'):
        raise ValueError('记录类型无效')
    with connection() as con:
        current = get(con, kind, identifier(key))
        if current is None:
            raise ValueError('记录不存在')
        past = [json.loads(row[0]) for row in con.execute(
            'SELECT data FROM hub_history WHERE kind=? AND id=? ORDER BY revision DESC', (kind, key))]
    return {'entity': current, 'history': past}


def revoked_snapshot(as_of, amount_field):
    return {'as_of':as_of, amount_field:None, 'confirmed':False, 'confirmation_revoked':True}


def update_snapshots(snapshots, as_of, values, amount_field):
    snapshots = list(snapshots)
    if as_of:
        if values['confirmed']:
            # Explicitly rechecking an earlier date supersedes subsequent
            # unknown markers, but preserves unknown periods before that date.
            snapshots = [row for row in snapshots if not row.get('confirmation_revoked') or row['as_of'] < as_of]
        snapshots = [row for row in snapshots if row.get('as_of') != as_of]
        snapshots.append({'as_of':as_of, **values})
    elif snapshots:
        snapshots = [row for row in snapshots if row.get('as_of') != today()]
        snapshots.append(revoked_snapshot(today(), amount_field))
    return sorted(snapshots, key=lambda row:row.get('as_of') or '')


def account_fields(data, old):
    old = old or {}
    kind = data.get('kind', old.get('kind', 'bank'))
    if kind not in ACCOUNT_KINDS:
        raise ValueError('账户类型无效')
    balance = integer(data.get('balance_cents', old.get('balance_cents')), '日终余额', True, minimum=-MAX_CENTS)
    as_of = day(data.get('as_of', old.get('as_of')), True)
    if as_of and as_of > today():
        raise ValueError('余额快照不能填写未来日期')
    confirmed = boolean(data.get('confirmed', old.get('confirmed', False)), '余额是否已核对')
    if confirmed and (as_of is None or balance is None):
        raise ValueError('确认余额前需填写余额金额和核对日期')
    snapshots = list(old.get('snapshots', []))
    if old.get('confirmation_cleared_as_of'):
        snapshots.append(revoked_snapshot(old['confirmation_cleared_as_of'], 'balance_cents'))
    snapshots = update_snapshots(snapshots, as_of, {'balance_cents':balance,'confirmed':confirmed}, 'balance_cents')
    return {'name': finance.text(data.get('name', old.get('name')), 200, '账户名称', True),
            'kind': kind, 'balance_cents': balance, 'as_of': as_of, 'snapshots': snapshots, 'confirmed': confirmed,
            'confirmation_cleared_as_of': None,
            'source_note': source_note(data, old)}


def debt_fields(data, old):
    old = old or {}
    kind = data.get('kind', old.get('kind'))
    if kind not in DEBT_KINDS:
        raise ValueError('负债类型无效')
    card_balance = None
    if kind == 'credit_card':
        if 'card_balance_cents' in data:
            card_balance = integer(data['card_balance_cents'], '信用卡余额', True, minimum=-MAX_CENTS)
            derived = None if card_balance is None else max(0, -card_balance)
            if 'principal_cents' in data and data['principal_cents'] != derived:
                raise ValueError('信用卡余额与欠款本金不一致：正余额是溢缴款，负余额才是欠款')
            principal = derived
        elif 'principal_cents' in data:
            principal = integer(data['principal_cents'], '基准未偿本金', True)
            card_balance = None if principal is None else -principal
        else:
            card_balance = old.get('card_balance_cents')
            if 'card_balance_cents' not in old and old.get('principal_cents') is not None:
                card_balance = -old['principal_cents']
            principal = None if card_balance is None else max(0, -card_balance)
    else:
        principal = integer(data.get('principal_cents', old.get('principal_cents')), '基准未偿本金', True)
    basis = data.get('snapshot_basis', old.get('snapshot_basis', 'after_recorded_payments'))
    if basis not in ('end_of_day', 'after_recorded_payments'):
        raise ValueError('本金核对时点无效')
    as_of = day(data.get('as_of', old.get('as_of')), True)
    confirmed = boolean(data.get('confirmed', old.get('confirmed', False)), '未偿本金是否已核对')
    if confirmed and (principal is None or as_of is None):
        raise ValueError('确认未偿本金时需填写金额和实际基准日期')
    if as_of and as_of > today():
        raise ValueError('未偿本金基准不能是未来日期')
    maturity = day(data.get('maturity', old.get('maturity')), True)
    if old and maturity != old.get('maturity') and maturity is not None:
        if data.get('maturity_confirmed') is not True:
            raise ValueError('修改到期日需确认这是已获确认的新到期日，不能把申请展期当作批准')
    values = {'name': finance.text(data.get('name', old.get('name')), 200, '负债名称', True),
            'kind': kind, 'principal_cents': principal, 'card_balance_cents':card_balance,
            'snapshot_basis':basis, 'as_of': as_of, 'confirmed': confirmed,
            'annual_rate_bp': integer(data.get('annual_rate_bp', old.get('annual_rate_bp')), '年利率基点', True, maximum=100000),
            'monthly_payment_cents': integer(data.get('monthly_payment_cents', old.get('monthly_payment_cents')), '约定月供', True),
            'term_months': integer(data.get('term_months', old.get('term_months')), '剩余期数', True, minimum=1, maximum=1200),
            'due_day': integer(data.get('due_day', old.get('due_day')), '每月还款日', True, minimum=1, maximum=31),
            'maturity': maturity,
            'maturity_confirmed': boolean(data.get('maturity_confirmed', old.get('maturity_confirmed', False)), '到期日是否已获确认'),
            'source_note': source_note(data, old)}
    values['borrowed_on'] = day(data.get('borrowed_on', old.get('borrowed_on')), True) if kind in ('short_term', 'mortgage', 'interest_only') else None
    values['original_principal_cents'] = integer(data.get('original_principal_cents', old.get('original_principal_cents')), '实际借入本金', True, minimum=1) if kind in ('short_term', 'mortgage', 'interest_only') else None
    if values['borrowed_on'] and values['borrowed_on'] > today():
        raise ValueError('借款日期不能填写未来日期')
    if values['borrowed_on'] and as_of and values['borrowed_on'] > as_of:
        raise ValueError('剩余本金核对日不能早于借款日期')
    if values['original_principal_cents'] is not None and principal is not None and principal > values['original_principal_cents']:
        raise ValueError('剩余本金不能大于这笔实际借入本金')
    if kind in ('personal', 'short_term', 'credit_card'):
        values.update(monthly_payment_cents=None, term_months=None)
    if kind in ('personal', 'short_term'):
        values['due_day'] = None
    if kind == 'personal':
        values['annual_rate_bp'] = 0
    status = data.get('status', old.get('status', 'active'))
    if status not in ('active', 'closed') or (status == 'closed' and kind != 'credit_card'):
        raise ValueError('只有信用卡可以标记注销，借款结清需有实际本金记录')
    closed_at = day(data.get('closed_at', old.get('closed_at')), True) if kind == 'credit_card' else None
    archived_from = data.get('archived_from', old.get('archived_from')) if kind == 'credit_card' else None
    archived_from = month_value(archived_from) if archived_from else None
    if closed_at and closed_at > today():
        raise ValueError('实际注销日期不能在未来')
    if archived_from and archived_from > today()[:7]:
        raise ValueError('注销确认月份不能在未来')
    if status == 'closed' and not archived_from:
        archived_from = closed_at[:7] if closed_at else today()[:7]
    if status == 'active' and (closed_at or archived_from):
        raise ValueError('恢复使用信用卡前请清除注销与隐藏日期')
    values['verified_payment_refs'] = data.get('verified_payment_refs', old.get('verified_payment_refs', []))
    values.update(status=status, closed_at=closed_at, archived_from=archived_from)
    return values


def next_month(month):
    year, number = map(int, month_value(month).split('-'))
    return f'{year+1:04d}-01' if number == 12 else f'{year:04d}-{number+1:02d}'


def statement_summary(statement, payments, through):
    paid = sum(p['principal_cents'] for p in payments if p.get('statement_id') == statement['id']
               and not p.get('voidedAt') and p['date'] <= through)
    amount = statement['amount_cents']
    return {**statement, 'paid_cents': paid, 'remaining_cents': amount-paid,
            'status': 'paid' if paid == amount else 'partial' if paid else 'unpaid'}


def card_statement_fields(con, data, old):
    old = old or {}
    debt_id = identifier(data.get('debt_id', old.get('debt_id')))
    debt = get(con, 'debt', debt_id)
    if not debt or debt['kind'] != 'credit_card':
        raise ValueError('账单需关联信用卡')
    month = month_value(data.get('consumption_month', old.get('consumption_month')))
    if month > today()[:7]:
        raise ValueError('信用卡账单不能记录尚未发生的未来消费月')
    if old and (month != old['consumption_month'] or debt_id != old['debt_id']):
        raise ValueError('账单的信用卡与消费月份不可改动，请核对原账单')
    if not old and debt.get('status') == 'closed' and month >= debt['archived_from']:
        raise ValueError('已注销月份不再新增消费账单；历史账单仍可补录还款')
    amount = integer(data.get('amount_cents', old.get('amount_cents')), '消费账单金额')
    return {'debt_id': debt_id, 'consumption_month': month, 'due_month': next_month(month),
            'amount_cents': amount, 'source_note': source_note(data, old)}


def debt_snapshots(debt):
    """Keep pre-snapshot entities readable without a migration or production write."""
    snapshots = list(debt['snapshots']) if 'snapshots' in debt else [
        {field: debt.get(field) for field in ('as_of', 'principal_cents', 'confirmed', 'baseline_payment_ids')}]
    if debt.get('confirmation_cleared_as_of'):
        snapshots.append(revoked_snapshot(debt['confirmation_cleared_as_of'], 'principal_cents'))
    return snapshots


def debt_snapshot(debt, through=None):
    through = through or today()
    if debt.get('confirmation_cleared_as_of') and debt['confirmation_cleared_as_of'] <= through:
        return None
    eligible = [row for row in debt_snapshots(debt) if row.get('as_of') and row['as_of'] <= through]
    return max(eligible, key=lambda row: row['as_of']) if eligible else None


def raw_outstanding(debt, payments, through=None, exclude=None, statements=None):
    snapshot = debt_snapshot(debt, through)
    if not snapshot or snapshot.get('principal_cents') is None or not snapshot.get('confirmed', False):
        return None
    payments = [p for p in payments if p['id'] != exclude]
    paid = sum(row['principal_cents'] for row in payments
               if row['debt_id'] == debt['id'] and not row.get('voidedAt') and row['id'] != exclude
               and (row['date'] > snapshot['as_of'] if snapshot.get('snapshot_basis') == 'end_of_day'
                    else row['date'] >= snapshot['as_of'])
               and not (row['date'] == snapshot['as_of'] and row['id'] in (snapshot.get('baseline_payment_ids') or []))
               and (through is None or row['date'] <= through))
    extra = 0
    if debt.get('kind') == 'credit_card':
        for statement in statements or []:
            if statement['debt_id'] != debt['id'] or statement['consumption_month'] > (through or today())[:7]:
                continue
            if statement['consumption_month'] <= snapshot['as_of'][:7]:
                # A subsequently recorded/corrected historical statement may already
                # be inside this checked balance. Never silently ignore or add it.
                checked = snapshot.get('baseline_statement_amounts', {})
                if checked.get(statement['id']) != statement['amount_cents']:
                    return None
            else:
                extra += statement['amount_cents']
    card_balance = snapshot.get('card_balance_cents')
    if card_balance is None:
        card_balance = -snapshot['principal_cents']
    baseline = -card_balance if debt.get('kind') == 'credit_card' else snapshot['principal_cents']
    remaining = baseline + extra - paid
    if debt.get('kind') == 'credit_card':
        known_due = sum(max(0, statement_summary(b, payments, through or today())['remaining_cents']
                              -snapshot.get('baseline_statement_credits', {}).get(b['id'], 0))
                        for b in statements or [] if b['debt_id'] == debt['id']
                        and b['consumption_month'] <= (through or today())[:7])
        if remaining < known_due - max(0, card_balance):
            return None
    return remaining


def outstanding(debt, payments, through=None, exclude=None, statements=None):
    value = raw_outstanding(debt, payments, through, exclude, statements)
    return max(0, value) if value is not None and debt.get('kind') == 'credit_card' else value


def card_statement_summaries(debts, statements, payments, through):
    result = {b['id']:statement_summary(b, payments, through) for b in statements}
    for item in result.values():
        item['credit_applied_cents'] = 0
        item['gross_remaining_cents'] = item['remaining_cents']
    for debt in debts:
        if debt['kind'] != 'credit_card':
            continue
        snapshot = debt_snapshot(debt, through)
        if not snapshot or not snapshot.get('confirmed') or raw_outstanding(debt, payments, through, statements=statements) is None:
            continue
        pool = max(0, snapshot.get('card_balance_cents') or 0)
        bills = sorted((b for b in statements if b['debt_id'] == debt['id'] and b['consumption_month'] <= through[:7]),
                       key=lambda b:(b['consumption_month'], b['id']))
        for bill in bills:
            row = result[bill['id']]
            checked_credit = snapshot.get('baseline_statement_credits', {}).get(bill['id'], 0)
            applied = min(row['remaining_cents'], checked_credit)
            if bill['consumption_month'] > snapshot['as_of'][:7]:
                extra = min(row['remaining_cents']-applied, pool)
                applied += extra
                pool -= extra
            row['credit_applied_cents'] = applied
            row['remaining_cents'] -= applied
            row['status'] = 'paid' if row['remaining_cents'] == 0 else 'partial' if row['paid_cents'] or applied else 'unpaid'
    return list(result.values())


def validate_debt_balances(debt, payments, statements=None):
    """A corrected older payment must also fit the baseline used by that month."""
    active = [p for p in payments if p['debt_id'] == debt['id'] and not p.get('voidedAt')]
    if debt.get('kind') == 'personal' and any(p['interest_cents'] or p['fees_cents'] for p in active):
        raise ValueError('亲友借款无利息和费用，请先核对原还款记录')
    if debt.get('kind') == 'short_term':
        borrowed = debt.get('borrowed_on')
        if borrowed and any(p['date'] < borrowed for p in active):
            raise ValueError('实际还款日期不能早于借款日期')
        original = debt.get('original_principal_cents')
        if original is not None and sum(p['principal_cents'] for p in active) > original:
            raise ValueError('累计还本超过这笔实际借入本金')
    snapshots = sorted((row for row in debt_snapshots(debt) if row.get('as_of')),
                       key=lambda row: row['as_of'])
    for index, snapshot in enumerate(snapshots):
        if not snapshot.get('confirmed') or snapshot.get('principal_cents') is None:
            continue
        end = today()
        if index+1 < len(snapshots):
            end = min(end, (date.fromisoformat(snapshots[index+1]['as_of'])-timedelta(days=1)).isoformat())
        if debt.get('confirmation_cleared_as_of'):
            end = min(end, (date.fromisoformat(debt['confirmation_cleared_as_of'])-timedelta(days=1)).isoformat())
        if end < snapshot['as_of']:
            continue
        balance = outstanding({**debt, 'snapshots':[snapshot]}, payments, end, statements=statements)
        if balance is not None and balance < 0:
            raise ValueError('实还本金超过该日期已知剩余本金，请先核对对应历史基准和还款金额')


def validated_payment_refs(con, debt_id, refs):
    if not isinstance(refs, list):
        raise ValueError('实际扣款关联格式无效')
    used, months = set(), set()
    result = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref)-{'month','entry_ids','entry_hashes','total_cents','settles_month'}:
            raise ValueError('实际扣款关联字段无效')
        month = month_value(ref.get('month'))
        if month in months:
            raise ValueError('同一月份请合并在一组扣款关联中')
        months.add(month)
        if any(p['debt_id'] == debt_id and p['payment_month'] == month and p.get('settles_month')
               and not p.get('voidedAt') for p in entities(con, 'payment')):
            raise ConflictError('该月已有实际还款记录，请先核对重复后再关联银行扣款')
        ids = ref.get('entry_ids')
        if not isinstance(ids, list) or not ids:
            raise ValueError('请选择已发生的扣款流水')
        amount, hashes = 0, {}
        for entry_id in ids:
            identifier(entry_id)
            if entry_id in used:
                raise ValueError('同一扣款不能重复关联')
            used.add(entry_id)
            found = con.execute('SELECT data FROM entries WHERE id=?', (entry_id,)).fetchone()
            row = json.loads(found[0]) if found else None
            if (not row or row.get('voidedAt') or not row.get('confirmed', True)
                    or row['type'] not in ('financing', 'transfer', 'expense')
                    or row.get('signed_cents') != -row['cents'] or row['cents'] <= 0
                    or row['date'][:7] != month or row['date'] > today()
                    or row.get('managed_by') == 'debt_payment'):
                raise ValueError('关联记录必须是当月已确认的真实流出，不能重复关联已有还款')
            amount += row['cents']
            hashes[entry_id] = entry_hash(row)
        result.append({'month':month, 'entry_ids':ids, 'entry_hashes':hashes, 'total_cents':amount,
                       'settles_month':boolean(ref.get('settles_month', True), '扣款是否完成当月还款')})
    for debt in entities(con, 'debt'):
        if debt['id'] == debt_id:
            continue
        other_ids = {i for r in debt.get('verified_payment_refs', []) for i in r['entry_ids']}
        if used & other_ids:
            raise ConflictError('这笔实际扣款已关联其他负债')
    return result


def active_payment_refs(con, debt, through):
    result = []
    for ref in debt.get('verified_payment_refs', []):
        rows = []
        for key in ref['entry_ids']:
            found = con.execute('SELECT data FROM entries WHERE id=?', (key,)).fetchone()
            row = json.loads(found[0]) if found else None
            if (not row or row.get('voidedAt') or row['date'] > through
                    or entry_hash(row) != ref.get('entry_hashes', {}).get(key)):
                break
            rows.append(row)
        else:
            if rows:
                result.append({**ref, 'total_cents':sum(r['cents'] for r in rows), 'dates':sorted({r['date'] for r in rows})})
    return result


def payment_fields(con, data, old, debt):
    old = old or {}
    payment_day = day(data.get('date', old.get('date', today())))
    if payment_day > today():
        raise ValueError('还款记录只记录已经发生的付款，不能填未来日期')
    amounts = {key: integer(data.get(key, old.get(key)), label) for key, label in
               [('principal_cents', '实还本金'), ('interest_cents', '实付利息'), ('fees_cents', '实付费用')]}
    if not 0 < sum(amounts.values()) <= MAX_CENTS:
        raise ValueError('请填写实际还款金额，本金、利息和费用总和必须大于零')
    account_id = finance.optional_id(data.get('account_id', old.get('account_id')))
    if account_id and get(con, 'account', account_id) is None:
        raise ValueError('扣款账户不存在，请选择已记录账户或留空待核')
    payment_month = month_value(data.get('payment_month', old.get('payment_month', payment_day[:7])))
    settles = boolean(data.get('settles_month', old.get('settles_month', False)), '本月还款是否已确认完成')
    if settles and any(r['month'] == payment_month and r['settles_month'] for r in active_payment_refs(con, debt, today())):
        raise ConflictError('该月已关联实际扣款，不要重复记录月供；额外还本请单独记录')
    if debt['kind'] in ('personal', 'short_term', 'credit_card') and settles:
        raise ValueError('此类借款按实际还款记录，不使用本月已还标记')
    if debt['kind'] == 'personal' and (amounts['interest_cents'] or amounts['fees_cents']):
        raise ValueError('亲友借款无利息和费用，只记录实际归还本金')
    statement_id = None
    if debt['kind'] == 'credit_card':
        statement_id = identifier(data.get('statement_id', old.get('statement_id')))
        statement = get(con, 'card_statement', statement_id)
        if not statement or statement['debt_id'] != debt['id']:
            raise ValueError('请选择这张信用卡对应的消费账单')
        if old and statement_id != old.get('statement_id'):
            raise ValueError('更换还款账单请先撤销原记录再新增')
        if payment_month != statement['due_month']:
            raise ValueError('信用卡应还月份为消费月份的下一个月')
        if payment_day < statement['consumption_month']+'-01':
            raise ValueError('实际还款日期不能早于账单消费月份')
        other_payments = [p for p in entities(con, 'payment') if p['id'] != old.get('id')]
        bill_summary = next(b for b in card_statement_summaries([debt], entities(con, 'card_statement'), other_payments, today())
                            if b['id'] == statement_id)
        if amounts['principal_cents'] > bill_summary['remaining_cents']:
            raise ValueError('还款本金超过该账单剩余待还金额')
    return {**amounts, 'debt_id': debt['id'], 'date': payment_day,
            'payment_month': payment_month, 'statement_id': statement_id,
            'account_id': account_id,
            'channel': finance.text(data.get('channel', old.get('channel', '')), 200, '付款渠道'),
            'settles_month': settles,
            'total_cents': sum(amounts.values()), 'voidedAt': '', 'source_note': source_note(data, old)}


def payment_ledger(con, payment, debt, void=False):
    ledger_ids = []
    components = [('principal_cents', 'financing', '贷款本金'), ('interest_cents', 'expense', '贷款利息'),
                  ('fees_cents', 'expense', '还款费用')]
    for component, kind, category in components:
        key = 'debtpay:'+hashlib.sha256(payment['id'].encode()).hexdigest()[:32]+':'+component
        found = con.execute('SELECT data FROM entries WHERE id=?', (key,)).fetchone()
        old = json.loads(found[0]) if found else None
        cents = payment[component]
        if not cents and old is None:
            continue
        ledger_ids.append(key)
        if old:
            con.execute('INSERT INTO history VALUES(?,?,?)', (key, old['revision'], json.dumps(old, ensure_ascii=False)))
        stamp = now()
        if not cents or void:
            row = {**old, 'voidedAt': stamp, 'updatedAt': stamp, 'revision': old['revision']+1}
        else:
            row = {**finance.fields({'date': payment['date'], 'name': debt['name'], 'type': kind,
                                    'cents': cents, 'category': category, 'track': 'daily', 'budget_category': 'unknown',
                                    'account_id': payment['account_id'], 'channel': payment['channel'],
                                    'source_text': payment['source_note']}),
                   'id': key, 'revision': (old['revision'] if old else 0)+1, 'updatedAt': stamp,
                   'direction': 'out', 'signed_cents': -cents, 'source': 'debt_payment',
                   'groups': [], 'source_row': None, 'reason': '本人确认的实际还款',
                   'managed_by': 'debt_payment', 'payment_id': payment['id'], 'voidedAt': ''}
        con.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?)', (key, json.dumps(row, ensure_ascii=False), row['revision']))
    return ledger_ids


def request_identity(data):
    if not isinstance(data, dict):
        raise ValueError('泉库操作格式无效')
    request_id = identifier(data.get('request_id'))
    if len(request_id) < 8:
        raise ValueError('请提供唯一请求编号，网络重试沿用同一编号')
    raw = json.dumps({key:value for key,value in data.items() if key != 'request_id'},
                     ensure_ascii=False, sort_keys=True, allow_nan=False)
    return request_id, hashlib.sha256(raw.encode()).hexdigest()


def manage(data):
    if not isinstance(data, dict) or data.get('action') not in ACTIONS:
        raise ValueError('不支持的泉库操作')
    action = data['action']
    if action == 'budget_suggest':
        return budget_suggest(month_value(data.get('month', today()[:7])))
    request_id, fingerprint = request_identity(data)
    kind = {'account_save': 'account', 'debt_save': 'debt', 'debt_payment': 'payment',
            'payment_void': 'payment', 'wish_save': 'wish', 'budget_save': 'budget', 'settings_save': 'settings',
            'card_statement_save':'card_statement'}[action]
    key = identifier(data.get('id') or str(uuid.uuid4()))
    if action == 'card_statement_save' and not data.get('id'):
        unique = identifier(data.get('debt_id'))+':'+month_value(data.get('consumption_month'))
        key = 'cardbill:'+hashlib.sha256(unique.encode()).hexdigest()[:32]
    if action == 'settings_save':
        key = 'profile'
    if action == 'budget_save':
        category = data.get('category')
        if category not in (*finance.BUDGET_CATEGORIES, *BUDGET_GROUPS):
            raise ValueError('请选择吃、喝或日用预算')
        suffix = 'group:'+BUDGET_GROUP_KEYS[category] if category in BUDGET_GROUPS else str(finance.BUDGET_CATEGORIES.index(category))
        key = month_value(data.get('month'))+':'+suffix
    revision = integer(data.get('revision', 0), '记录版本')
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        cached = con.execute('SELECT fingerprint,response FROM hub_requests WHERE request_id=?', (request_id,)).fetchone()
        if cached:
            if cached['fingerprint'] != fingerprint:
                raise ConflictError('请求编号已用于其他修改，请重新读取后再操作')
            return {**json.loads(cached['response']), 'duplicate': True}
        old = get(con, kind, key)
        if revision != (old['revision'] if old else 0):
            raise ConflictError('记录已在另一处更新，请刷新后再保存')
        if old and old.get('voidedAt'):
            raise ConflictError('此还款已撤销；需要重新记录时请新增一笔')
        ledger_ids = None
        if action == 'settings_save':
            values = {'debt_inventory_confirmed': boolean(data.get('debt_inventory_confirmed'), '债务清单是否完整'),
                      'source_note': source_note(data, old)}
        elif action == 'account_save':
            values = account_fields(data, old)
        elif action == 'card_statement_save':
            values = card_statement_fields(con, data, old)
            for other in entities(con, 'card_statement'):
                if other['id'] != key and other['debt_id'] == values['debt_id'] and other['consumption_month'] == values['consumption_month']:
                    raise ConflictError('这张信用卡此消费月已有账单，请编辑已有账单')
            paid = sum(p['principal_cents'] for p in entities(con, 'payment')
                       if p.get('statement_id') == key and not p.get('voidedAt'))
            if values['amount_cents'] < paid:
                raise ValueError('账单金额不能低于实际已还本金，请先核对或撤销错误还款')
        elif action == 'debt_save':
            values = debt_fields(data, old)
            if values['verified_payment_refs'] and values['kind'] not in ('mortgage', 'interest_only'):
                raise ValueError('修改借款类型前请先核对并解除实际月扣款关联')
            if 'verified_payment_refs' in data:
                values['verified_payment_refs'] = validated_payment_refs(con, key, values['verified_payment_refs'])
            payments = entities(con, 'payment')
            statements = entities(con, 'card_statement')
            if old and old['kind'] == 'credit_card' and values['kind'] != 'credit_card' and any(b['debt_id'] == key for b in statements):
                raise ValueError('信用卡已有账单，不能直接改成其他借款类型')
            prior_basis = {**(old or {}), 'snapshot_basis':(old or {}).get('snapshot_basis', 'after_recorded_payments')}
            if old and old['kind'] == 'credit_card' and 'card_balance_cents' not in old:
                prior_basis['card_balance_cents'] = -old['principal_cents'] if old.get('principal_cents') is not None else None
            rechecked = (not old or any(values[field] != prior_basis.get(field) for field in ('principal_cents','as_of','confirmed','card_balance_cents','snapshot_basis'))
                         or boolean(data.get('reconfirm_balance', False), '是否重新核对本金'))
            if rechecked:
                values['baseline_payment_ids'] = [payment['id'] for payment in payments
                    if payment['debt_id'] == key and not payment.get('voidedAt') and values['as_of']
                    and payment['date'] == values['as_of']]
            else:
                values['baseline_payment_ids'] = old.get('baseline_payment_ids', [])
            snapshots = debt_snapshots(old) if old else []
            snapshot_values = {field:values[field] for field in ('principal_cents','confirmed','baseline_payment_ids','card_balance_cents','snapshot_basis')}
            if values['kind'] == 'credit_card':
                relevant = [b for b in statements if b['debt_id'] == key and values['as_of'] and b['consumption_month'] <= values['as_of'][:7]]
                if values['confirmed'] and rechecked:
                    checked_bills = card_statement_summaries([old], statements, payments, values['as_of']) if old else []
                    prior_credits = {b['id']:b['credit_applied_cents'] for b in checked_bills}
                    required = sum(max(0, statement_summary(b, payments, values['as_of'])['remaining_cents']
                                       -prior_credits.get(b['id'], 0)) for b in relevant)
                    if values['principal_cents'] < required:
                        raise ValueError('核对总欠款不能少于该日期已知账单待还金额，请先核对账单或实际还款')
                prior = next((s for s in snapshots if s.get('as_of') == values['as_of']), {})
                snapshot_values['baseline_statement_amounts'] = ({b['id']:b['amount_cents'] for b in relevant}
                    if rechecked else prior.get('baseline_statement_amounts', {}))
                snapshot_values['baseline_statement_credits'] = (prior_credits if values['confirmed'] and rechecked
                    else prior.get('baseline_statement_credits', {}))
            values['snapshots'] = update_snapshots(snapshots, values['as_of'], snapshot_values, 'principal_cents')
            values['confirmation_cleared_as_of'] = None
            validate_debt_balances({**values, 'id':key}, payments, statements)
        elif action == 'debt_payment':
            debt_id = identifier(data.get('debt_id', (old or {}).get('debt_id')))
            debt = get(con, 'debt', debt_id)
            if debt is None:
                raise ValueError('负债记录不存在')
            if old and debt_id != old['debt_id']:
                raise ValueError('更换还款所属负债请先撤销原记录再新增')
            if 'debt_revision' in data:
                if integer(data['debt_revision'], '负债版本') != debt['revision']:
                    raise ConflictError('负债信息已更新，请重新读取后记录还款')
            values = payment_fields(con, data, old, debt)
            snapshot = debt_snapshot(debt)
            included_in_checked_day = any(checked.get('confirmed')
                and key in (checked.get('baseline_payment_ids') or [])
                and old and old['date'] == checked['as_of']
                for checked in debt_snapshots(debt))
            if (old and included_in_checked_day and (values['principal_cents'] != old['principal_cents'] or values['date'] != old['date'])):
                raise ValueError('这笔还本已包含在同日核对本金中，请先取消该基准核对，修正还款后重新核对本金')
            remaining = outstanding(debt, entities(con, 'payment'), exclude=key, statements=entities(con, 'card_statement'))
            included = bool(snapshot and values['date'] == snapshot['as_of']
                            and (snapshot.get('snapshot_basis') == 'end_of_day' or key in (snapshot.get('baseline_payment_ids') or [])))
            if (remaining is not None and values['date'] >= snapshot['as_of']
                    and not included and values['principal_cents'] > remaining):
                raise ValueError('实还本金超过已知剩余本金，请先核对负债基准')
            candidate = [payment for payment in entities(con, 'payment') if payment['id'] != key]
            validate_debt_balances(debt, candidate+[{**values, 'id':key}], entities(con, 'card_statement'))
        elif action == 'payment_void':
            if old is None:
                raise ValueError('还款记录不存在')
            values = {**old, 'voidedAt': now(), 'void_reason': finance.text(data.get('reason', ''), 1000, '撤销原因')}
            debt = get(con, 'debt', old['debt_id'])
            if old['principal_cents'] and any(s.get('confirmed') and key in (s.get('baseline_payment_ids') or [])
                    and old['date'] == s.get('as_of') for s in debt_snapshots(debt)):
                raise ValueError('这笔还本已包含在同日核对本金中，请先取消该基准核对，撤销还款后重新核对本金')
            candidate = [payment for payment in entities(con, 'payment') if payment['id'] != key]
            validate_debt_balances(debt, candidate+[{**values, 'id':key}], entities(con, 'card_statement'))
        elif action == 'wish_save':
            values = {'name': finance.text(data.get('name', (old or {}).get('name')), 200, '心愿名称', True),
                      'price_cents': integer(data.get('price_cents', (old or {}).get('price_cents')), '心愿价格', minimum=1),
                      'rule': 'debt_free_and_price_at_most_10_percent_cash', 'source_note': source_note(data, old)}
        else:
            month = month_value(data['month'])
            category = data['category']
            if category in BUDGET_GROUPS and old is None:
                inherited = legacy_budget_rows(con, month, category)
                versions = {r['id']:r['revision'] for r in inherited}
                if versions and data.get('legacy_revisions') != versions:
                    raise ConflictError('旧预算已变化，请刷新合并后的金额再保存')
            elif category not in BUDGET_GROUPS:
                group = next((g for g,categories in BUDGET_GROUPS.items() if category in categories), None)
                if group and get(con, 'budget', month+':group:'+BUDGET_GROUP_KEYS[group]):
                    raise ConflictError('预算已合并，请在餐饮、日用或娱乐中调整总额')
            values = {'month': month_value(data['month']), 'category': data['category'],
                      'cents': integer(data.get('cents'), '预算金额'), 'source_note': source_note(data, old)}
            con.execute('INSERT OR REPLACE INTO budgets VALUES(?,?,?)', (values['month'], values['category'], values['cents']))
        row = put(con, kind, key, values, old, action)
        if kind == 'payment':
            ledger_ids = payment_ledger(con, row, debt, void=action == 'payment_void')
        response = {'action': action, 'entity': row, 'duplicate': False}
        if ledger_ids is not None:
            response['ledger_ids'] = ledger_ids
        con.execute('INSERT INTO hub_requests VALUES(?,?,?,?)',
                    (request_id, fingerprint, json.dumps(response, ensure_ascii=False), now()))
    return response


def month_end(month):
    year, number = map(int, month.split('-'))
    return f'{month}-{calendar.monthrange(year, number)[1]:02d}'


def active_entries(con, include_unconfirmed=False):
    return [row for row in (json.loads(item[0]) for item in con.execute('SELECT data FROM entries'))
            if not row.get('voidedAt') and (include_unconfirmed or row.get('confirmed', True))]


def entry_hash(row):
    """Hash the complete reviewed row, including its revision and original text."""
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def apply_review(payload, strict=False):
    """Internal, verified import metadata/classification update. No public route.

    A stale row hash is skipped, never applied over a later manual correction.
    Classification changes remain in this ledger's existing history table.
    """
    if not isinstance(payload, dict):
        raise ValueError('核对资料格式无效')
    digest = payload.get('source_digest')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('核对来源摘要无效')
    raw_months = payload.get('months')
    if not isinstance(raw_months, list):
        raise ValueError('需明确已核实的完整月份')
    months = sorted({month_value(value) for value in raw_months})
    available = payload.get('available_months', [])
    partial = payload.get('partial_months', [])
    if not isinstance(available, list) or not isinstance(partial, list):
        raise ValueError('资料覆盖月份格式无效')
    available = sorted({month_value(value) for value in available})
    partial = sorted({month_value(value) for value in partial})
    reviewed_at = payload.get('reviewed_at', now())
    if not isinstance(reviewed_at, str):
        raise ValueError('核对时间无效')
    datetime.fromisoformat(reviewed_at)
    mapping = payload.get('mapping_by_id')
    if not isinstance(mapping, dict):
        raise ValueError('逐行核对映射无效')
    for key, item in mapping.items():
        identifier(key)
        if (not isinstance(item, dict) or not isinstance(item.get('source_hash'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', item['source_hash'])):
            raise ValueError('逐行来源摘要无效')
        if set(item)-{'source_hash','track','budget_category','channel','subcategory'}:
            raise ValueError('核对映射只能补充业务方向、预算分类、二级分类和付款渠道')
        if 'track' in item and item['track'] not in finance.TRACKS:
            raise ValueError('核对业务方向无效')
        if 'budget_category' in item and item['budget_category'] not in (*finance.BUDGET_CATEGORIES, 'unknown'):
            raise ValueError('核对预算分类无效')
        if 'channel' in item:
            finance.text(item['channel'], 200, '付款渠道')
        if 'subcategory' in item:
            finance.text(item['subcategory'], 100, '二级分类')
    review_id = entry_hash(payload)
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        cached = con.execute('SELECT response FROM hub_reviews WHERE id=?', (review_id,)).fetchone()
        if cached:
            previous = json.loads(cached[0])
            if strict and previous.get('conflicts'):
                raise ConflictError('此前核对存在来源冲突，请重新核验并生成完整映射')
            return {**previous, 'already_applied': True}
        if strict:
            for key, item in mapping.items():
                found = con.execute('SELECT data FROM entries WHERE id=?', (key,)).fetchone()
                if found is None or entry_hash(json.loads(found[0])) != item['source_hash']:
                    raise ConflictError('核对来源已变化，整批未应用，请重新核验')
        applied, conflicts, unchanged = [], [], []
        for key, item in mapping.items():
            found = con.execute('SELECT data FROM entries WHERE id=?', (key,)).fetchone()
            old = json.loads(found[0]) if found else None
            if old is None or entry_hash(old) != item['source_hash']:
                conflicts.append(key)
                continue
            changes = {field: value for field, value in item.items() if field != 'source_hash'}
            if all(old.get(field) == value for field, value in changes.items()):
                unchanged.append(key)
                continue
            row = {**old, **changes, 'revision': old['revision']+1, 'updatedAt': now(),
                   'classification_review': {'source_digest':digest, 'reviewed_at':reviewed_at}}
            con.execute('INSERT INTO history VALUES(?,?,?)', (key, old['revision'], json.dumps(old, ensure_ascii=False)))
            con.execute('UPDATE entries SET data=?,revision=? WHERE id=?', (json.dumps(row, ensure_ascii=False), row['revision'], key))
            applied.append(key)
        metadata = {'source_digest': digest, 'reviewed_at': reviewed_at,
                    'coverage': {'complete_months': months, 'available_months': available,
                                 'partial_months': partial, 'notes': finance.raw_text(payload.get('notes',''), 10000, '核对说明')}}
        result = {'source_digest': digest, 'applied': applied, 'conflicts': conflicts,
                  'unchanged': unchanged, 'already_applied': False, 'complete_months': months,
                  'available_months': available, 'partial_months': partial}
        con.execute('INSERT INTO hub_reviews VALUES(?,?,?,?)',
                    (review_id, digest, json.dumps(metadata, ensure_ascii=False), json.dumps(result, ensure_ascii=False)))
    return result


def account_balance(account, entries, through, known_ids):
    snapshots = [row for row in account.get('snapshots', []) if row['as_of'] <= through]
    snapshot = max(snapshots, key=lambda row: row['as_of']) if snapshots else None
    if account.get('confirmation_cleared_as_of') and account['confirmation_cleared_as_of'] <= through:
        snapshot = None
    if not snapshot or snapshot['balance_cents'] is None or not snapshot.get('confirmed', False):
        return {**account, 'current_balance_cents': None, 'balance_as_of': through,
                'snapshot_as_of': snapshot['as_of'] if snapshot else None,
                'snapshot_balance_cents': snapshot['balance_cents'] if snapshot else account.get('balance_cents'),
                'snapshot_confirmed': bool(snapshot and snapshot.get('confirmed')), 'pending_entry_ids': []}
    balance = snapshot['balance_cents']
    pending = []
    for row in entries:
        if not snapshot['as_of'] < row['date'] <= through or row['type'] == 'excluded':
            continue
        account_id, target = row.get('account_id'), row.get('to_account_id')
        if not row.get('confirmed', True):
            if (account_id not in known_ids or account_id == account['id']
                    or (row['type'] == 'transfer' and (target not in known_ids or target == account['id']))):
                pending.append(row['id'])
            continue
        if row['type'] == 'transfer':
            if account_id not in known_ids or target not in known_ids:
                pending.append(row['id'])
            if account_id == account['id']:
                balance -= row['cents']
            if target == account['id']:
                balance += row['cents']
        else:
            if account_id not in known_ids or row.get('signed_cents') is None:
                pending.append(row['id'])
            if account_id == account['id'] and type(row.get('signed_cents')) is int:
                balance += row['signed_cents']
    return {**account, 'current_balance_cents': None if pending else balance,
            'known_postings_balance_cents': balance, 'balance_as_of': through,
            'snapshot_as_of': snapshot['as_of'], 'snapshot_balance_cents': snapshot['balance_cents'],
            'snapshot_confirmed': True, 'pending_entry_ids': pending}


def estimated_payment(debt):
    """A plan only. This number is never written as a payment or ledger entry."""
    if debt['kind'] not in ('mortgage', 'interest_only'):
        return None
    if debt.get('monthly_payment_cents') is not None:
        return debt['monthly_payment_cents']
    principal, bp = debt.get('remaining_principal_cents'), debt.get('annual_rate_bp')
    if principal is None or bp is None:
        return None
    monthly_rate = Decimal(bp)/Decimal(120000)
    if debt['kind'] == 'interest_only':
        value = Decimal(principal)*monthly_rate
    elif debt['kind'] == 'mortgage' and debt.get('term_months'):
        terms = debt['term_months']
        value = Decimal(principal)/terms if not monthly_rate else Decimal(principal)*monthly_rate/(1-(1+monthly_rate)**(-terms))
    else:
        return None
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def payment_quote(debt, month):
    quote = {'month':month, 'principal_cents':None, 'interest_cents':None, 'total_cents':None,
             'estimated':True, 'status':'unknown', 'label':'需核对本金、利率与还款安排',
             'assumptions':[], 'source':'estimate', 'recorded_total_cents':debt.get('month_paid_cents'),
             'paid_dates':debt.get('month_payment_dates', []), 'scheduled_total_cents':None}
    if debt['kind'] not in ('mortgage', 'interest_only'):
        quote['label'] = '按实际发生的还款记录'
        return quote
    # The reference schedule and the unpaid draft are different amounts. Paying
    # this month must clear the latter without discarding a known rate-based
    # reference, or the UI falsely asks the owner to verify the loan again.
    principal, bp = debt.get('remaining_principal_cents'), debt.get('annual_rate_bp')
    if debt['kind'] == 'interest_only' and principal is not None and bp is not None:
        quote['scheduled_total_cents'] = int((Decimal(principal)*Decimal(bp)/Decimal(120000))
                                              .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    elif debt['kind'] == 'mortgage':
        quote['scheduled_total_cents'] = debt.get('monthly_payment_cents')
    if debt.get('monthly_status') == 'paid':
        linked = any(r['settles_month'] for r in debt.get('linked_payments', []))
        quote.update(principal_cents=0, interest_cents=0, total_cents=0, estimated=False, status='paid',
                     source='verified_ledger' if linked else 'recorded_payment',
                     label='已关联实际扣款，请勿重复记录' if linked else '本月已记录完成还款')
        return quote
    if debt.get('linked_payment_cents'):
        quote['label'] = '已有扣款的本息尚未拆分，请核对剩余应还金额'
        return quote
    if principal is None or bp is None:
        quote['label'] = '本金或年利率尚待核对，请按银行实际金额填写'
        return quote
    if principal == 0:
        quote.update(principal_cents=0, interest_cents=0, total_cents=0, status='paid', estimated=False,
                     source='checked_balance', label='已核对剩余本金为零')
        return quote
    rate = Decimal(bp)/Decimal(120000)
    paid_principal = debt.get('month_principal_cents') or 0
    paid_interest = debt.get('month_interest_cents') or 0
    quote['assumptions'].append('金额仅供核对，保存时记录你确认的实际本金与利息。')
    if debt['kind'] == 'interest_only':
        interest = int((Decimal(principal)*rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        remaining_interest = max(0, interest-paid_interest)
        quote.update(principal_cents=0, interest_cents=remaining_interest, total_cents=remaining_interest,
                     scheduled_total_cents=interest, status='ready' if remaining_interest else 'paid',
                     label='按当前本金 × 年利率 ÷ 12 估算本月利息')
        quote['assumptions'].append('按月均利息估算；银行按实际天数计息或有提前还本时，实际利息可能不同。')
        if paid_interest:
            quote['assumptions'].append('已扣除本月记录过的实际利息，避免重复填写。')
        return quote
    # Reconstruct this month's amount before recorded principal payments so a
    # partial instalment does not cause the second draft to recalculate twice.
    basis = principal+paid_principal
    interest = int((Decimal(basis)*rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    total = debt.get('monthly_payment_cents')
    if total is not None:
        quote['source'] = 'known_monthly_payment'
        quote['label'] = '按已记录月供预填，本金与利息拆分需核对'
        quote['assumptions'].append('总额取已明确的月供；本息拆分按本月还本前余额与当前利率估算。')
    else:
        terms = debt.get('term_months')
        maturity = debt.get('maturity')
        if terms is None and maturity:
            y, m = map(int, month.split('-'))
            end_y, end_m = map(int, maturity[:7].split('-'))
            terms = (end_y-y)*12+end_m-m+1
            quote['assumptions'].append('剩余期数按所选还款月到到期月（含首尾）推算，未假定展期。')
        if terms is None or terms < 1:
            quote['label'] = '尚缺明确月供或有效剩余期限，请按银行金额填写'
            return quote
        total_value = Decimal(basis)/terms if not rate else Decimal(basis)*rate/(1-(1+rate)**(-terms))
        total = int(total_value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        quote['label'] = '按等额本息估算月供，实际以银行扣款为准'
        quote['remaining_terms_estimate'] = terms
    principal_part = total-interest
    if principal_part < 0:
        quote['label'] = '月供与当前利率计算不一致，请核对银行本息拆分'
        return quote
    remaining_principal = min(principal, max(0, principal_part-paid_principal))
    remaining_interest = max(0, interest-paid_interest)
    quote.update(principal_cents=remaining_principal, interest_cents=remaining_interest,
                 total_cents=remaining_principal+remaining_interest, scheduled_total_cents=total,
                 status='ready' if remaining_principal+remaining_interest else 'paid')
    if paid_principal or paid_interest:
        quote['assumptions'].append('已扣除本月记录过的本息；提前还款可能改变银行后续计划。')
    return quote


def legacy_budget_rows(con, month, group):
    saved = {r['category']:r for r in entities(con, 'budget') if r['month'] == month}
    for row in con.execute('SELECT category,cents FROM budgets WHERE month=?', (month,)):
        category = row['category']
        if category not in saved and category in finance.BUDGET_CATEGORIES:
            saved[category] = {'id':month+':'+str(finance.BUDGET_CATEGORIES.index(category)),
                               'category':category, 'cents':row['cents'], 'revision':0}
    if group == '餐饮':
        # Prefer each old group over its component categories; never double count.
        return ([saved['吃']] if '吃' in saved else [saved[c] for c in ('买菜做饭','下馆子') if c in saved]) + ([saved['喝']] if '喝' in saved else [saved['咖啡饮料']] if '咖啡饮料' in saved else [])
    return [saved[category] for category in BUDGET_GROUPS[group] if category in saved]


def coverage_state(con):
    complete, partial, available, notes = set(), set(), set(), []
    for item in list(con.execute('SELECT metadata FROM imports'))+list(con.execute('SELECT metadata FROM hub_reviews')):
        metadata = json.loads(item[0])
        coverage = metadata.get('coverage', {})
        if not isinstance(coverage, dict):
            coverage = {}
        for key, result in [('complete_months', complete), ('partial_months', partial), ('available_months', available)]:
            values = coverage.get(key, metadata.get(key, []))
            if not isinstance(values, list):
                continue
            for value in values:
                try:
                    result.add(month_value(value))
                except ValueError:
                    continue
        note = coverage.get('notes')
        if isinstance(note, str) and note:
            notes.append(note)
    # An ongoing month is never promoted to a complete historical month.
    partial.update(month for month in complete if month >= today()[:7])
    complete = {month for month in complete if month < today()[:7]} - partial
    # Record existence proves an available month, never completeness.
    available.update(row['date'][:7] for row in active_entries(con, include_unconfirmed=True))
    available.update(complete | partial)
    partial.update(available - complete)
    return {'complete_months': sorted(complete), 'partial_months': sorted(partial),
            'available_months': sorted(available), 'notes': notes, 'has_complete_months': bool(complete)}


def in_lookback(month, target):
    year, number = map(int, month.split('-'))
    target_year, target_number = map(int, target.split('-'))
    delta = target_year*12+target_number-(year*12+number)
    return 1 <= delta <= 10


def budget_suggestions(con, target):
    coverage = coverage_state(con)
    months = [month for month in coverage['complete_months'] if in_lookback(month, target)]
    reference_months = [month for month in coverage['available_months'] if in_lookback(month, target)
                        and month < today()[:7]]
    records = [row for row in active_entries(con, include_unconfirmed=True) if row['date'][:7] in reference_months
               and row['type'] in ('expense', 'refund') and row.get('track', 'unknown') in ('daily', 'unknown')]
    unresolved = [row['id'] for row in records if not row.get('confirmed', True) or row.get('track', 'unknown') == 'unknown'
                  or row.get('budget_category', 'unknown') == 'unknown']
    suggestions = []
    complete_unresolved = [row for row in records if row['date'][:7] in months and row['id'] in unresolved]
    for category, members in BUDGET_GROUPS.items():
        totals = {month: 0 for month in months}
        reference_totals = {month: 0 for month in reference_months}
        for row in records:
            if row.get('confirmed', True) and row.get('track') == 'daily' and row.get('budget_category') in members:
                amount = row['cents']*(1 if row['type'] == 'expense' else -1)
                reference_totals[row['date'][:7]] += amount
                if row['date'][:7] in totals:
                    totals[row['date'][:7]] += amount
        cents = None
        if months and not complete_unresolved:
            cents = max(0, int((Decimal(sum(totals.values()))/len(months)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
        provisional = max(0, int((Decimal(sum(reference_totals.values()))/len(reference_months)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))) if reference_months else None
        suggestions.append({'category': category, 'categories':list(members), 'cents': cents, 'monthly_net_cents': totals,
                            'provisional_cents': provisional, 'reference_monthly_net_cents': reference_totals})
    status = ('ready' if months and not complete_unresolved else 'mapping_incomplete' if unresolved
              else 'provisional' if reference_months else 'coverage_insufficient')
    return {'action': 'budget_suggest', 'month': target, 'suggestions': suggestions,
            'based_on_months': months if status == 'ready' else reference_months,
            'complete_basis_months': months, 'reference_months': reference_months,
            'coverage': coverage, 'unmapped_entry_ids': unresolved, 'unmapped_count': len(unresolved),
            'status': status, 'lookback_months': 10,
            'reference_label': '现有记录月均，仅作参考；有记录的月份不代表流水完整',
            'method': 'mean_daily_expense_minus_refund_of_explicit_complete_months', 'saved': False}


def budget_suggest(month):
    with connection() as con:
        return budget_suggestions(con, month_value(month))


def dashboard_forecast(debts, statements, month, current_month):
    rows = []
    for debt in debts:
        if debt.get('archived_from') and debt['archived_from'] <= month:
            continue
        if debt.get('remaining_principal_cents') == 0:
            continue
        kind = debt['kind']
        if kind == 'credit_card':
            bill = next((b for b in statements if b['debt_id'] == debt['id'] and b['due_month'] == month), None)
            total = bill['remaining_cents'] if bill else None
            rows.append({'id':debt['id'], 'name':debt['name'], 'due_day':debt.get('due_day'),
                         'total_cents':total, 'principal_cents':None, 'interest_cents':None,
                         'estimated':False, 'note':'按已录账单' if bill else '消费账单尚未录入'})
        elif kind in ('mortgage', 'interest_only'):
            quote_debt = dict(debt)
            if month != current_month:
                quote_debt.update(monthly_status='unrecorded', month_principal_cents=0, month_interest_cents=0,
                                  month_paid_cents=0, month_payment_dates=[], linked_payment_cents=0, linked_payments=[])
            quote = payment_quote(quote_debt, month)
            principal = quote.get('principal_cents')
            total = quote.get('total_cents')
            maturity = kind == 'interest_only' and debt.get('maturity_confirmed') and (debt.get('maturity') or '').startswith(month+'-')
            if maturity and total is not None and debt.get('remaining_principal_cents') is not None:
                principal = debt['remaining_principal_cents']
                total += principal
            rows.append({'id':debt['id'], 'name':debt['name'], 'due_day':debt.get('due_day'),
                         'total_cents':total, 'principal_cents':principal, 'interest_cents':quote.get('interest_cents'),
                         'estimated':bool(quote.get('estimated') or maturity),
                         'note':'到期本金与利息参考' if maturity else quote['label']})
    unknown = sum(r['total_cents'] is None for r in rows)
    return {'month':month, 'rows':sorted(rows,key=lambda r:(r['due_day'] or 32,r['name'])),
            'known_total_cents':sum(r['total_cents'] or 0 for r in rows),
            'principal_cents':sum(r['principal_cents'] or 0 for r in rows),
            'interest_cents':sum(r['interest_cents'] or 0 for r in rows),
            'unknown_count':unknown, 'complete':not unknown,
            'unscheduled_count':sum(d['kind'] in ('personal','short_term') and d.get('remaining_principal_cents') != 0 and not d.get('archived') for d in debts)}


def state(month=None):
    month = month_value(month or today()[:7])
    through = min(today(), month_end(month))
    with connection() as con:
        # One consistent read transaction for all derived totals.
        con.execute('BEGIN')
        entries = active_entries(con, include_unconfirmed=True)
        raw_accounts = entities(con, 'account')
        settings = get(con, 'settings', 'profile') or {'id':'profile', 'revision':0, 'debt_inventory_confirmed':False}
        known_ids = {row['id'] for row in raw_accounts}
        accounts = [account_balance(row, entries, through, known_ids) for row in raw_accounts]
        payments = entities(con, 'payment')
        statements = entities(con, 'card_statement')
        raw_debts = entities(con, 'debt')
        card_statements = card_statement_summaries(raw_debts, statements, payments, through)
        debts = []
        for row in raw_debts:
            snapshot = debt_snapshot(row, through)
            principal = outstanding(row, payments, through, statements=statements)
            discrepancy = principal if principal is not None and principal < 0 else None
            if discrepancy is not None:
                principal = None
            linked = [r for r in active_payment_refs(con, row, through) if r['month'] == month]
            monthly = [payment for payment in payments if payment['debt_id'] == row['id']
                       and payment['payment_month'] == month and not payment.get('voidedAt') and payment['date'] <= through]
            item = {**row, 'remaining_principal_cents': principal, 'current_principal_cents': principal,
                    'snapshot_principal_cents': snapshot.get('principal_cents') if snapshot else row.get('principal_cents'),
                    'snapshot_as_of': snapshot.get('as_of') if snapshot else None,
                    'snapshot_confirmed': bool(snapshot and snapshot.get('confirmed')),
                    'principal_discrepancy_cents': discrepancy,
                    'month_paid_cents': sum(payment['total_cents'] for payment in monthly)+sum(r['total_cents'] for r in linked),
                    'linked_payment_cents':sum(r['total_cents'] for r in linked),
                    'linked_payments':linked,
                    'month_payment_dates':sorted({p['date'] for p in monthly}|{d for r in linked for d in r['dates']}),
                    'month_principal_cents': sum(payment['principal_cents'] for payment in monthly),
                    'month_interest_cents': sum(payment['interest_cents'] for payment in monthly),
                    'month_fees_cents': sum(payment['fees_cents'] for payment in monthly),
                    'monthly_status': 'paid' if any(r['settles_month'] for r in linked) or any(payment['settles_month'] for payment in monthly) else 'recorded' if monthly or linked else 'unrecorded',
                    'due_date': f'{month}-{min(row["due_day"], int(month_end(month)[-2:])):02d}' if row.get('due_day') else None}
            actual = [p for p in payments if p['debt_id'] == row['id'] and not p.get('voidedAt') and p['date'] <= through]
            item.update(total_principal_paid_cents=sum(p['principal_cents'] for p in actual),
                        total_interest_paid_cents=sum(p['interest_cents'] for p in actual),
                        total_fees_paid_cents=sum(p['fees_cents'] for p in actual), settled_on=None, duration_days=None)
            if row['kind'] in ('personal', 'short_term'):
                item.update(month_paid_cents=None, month_principal_cents=None, month_interest_cents=None,
                            month_fees_cents=None, monthly_status=None, due_date=None)
            if row['kind'] == 'short_term':
                # A checked zero balance is sufficient to establish no current debt.
                # Missing borrowing history must not invalidate that dated snapshot
                # or be turned into an invented payoff date/payment.
                item['short_term_status'] = ('unverified' if principal is None else
                                             'no_debt' if principal == 0 else 'outstanding')
            if row['kind'] == 'short_term' and row.get('borrowed_on') and row.get('original_principal_cents') is not None:
                if principal == 0 and item['total_principal_paid_cents'] == row['original_principal_cents']:
                    paid_dates = [p['date'] for p in actual if p['principal_cents'] > 0]
                    if paid_dates:
                        item['settled_on'] = max(paid_dates)
                        item['duration_days'] = (date.fromisoformat(item['settled_on'])-date.fromisoformat(row['borrowed_on'])).days
            if row['kind'] == 'credit_card':
                raw_remaining = raw_outstanding(row, payments, through, statements=statements)
                item['snapshot_card_balance_cents'] = (snapshot.get('card_balance_cents', -snapshot['principal_cents'] if snapshot.get('principal_cents') is not None else None) if snapshot else row.get('card_balance_cents'))
                item['current_card_balance_cents'] = -raw_remaining if raw_remaining is not None else None
                item['credit_balance_cents'] = max(0, -raw_remaining) if raw_remaining is not None else None
                statement = next((b for b in card_statements if b['debt_id'] == row['id'] and b['due_month'] == month), None)
                item['card_statement'] = statement
                item['monthly_status'] = statement['status'] if statement else 'unknown'
                item['statement_balance_cents'] = sum(b['remaining_cents'] for b in card_statements if b['debt_id'] == row['id'] and b['consumption_month'] <= through[:7])
            item['payment_quote'] = payment_quote(item, month)
            item['estimated_payment_cents'] = estimated_payment(item)
            item['archived'] = bool(row.get('status') == 'closed' and row.get('archived_from') and month >= row['archived_from'])
            debts.append(item)
        cash = [row for row in accounts if row['kind'] in ('cash', 'bank', 'e_wallet')]
        unknown_cash = not cash or any(row['current_balance_cents'] is None or row['pending_entry_ids'] for row in cash)
        cash_cents = None if unknown_cash else sum(row['current_balance_cents'] for row in cash)
        debt_cents = None if not settings['debt_inventory_confirmed'] or any(row['remaining_principal_cents'] is None for row in debts) else sum(row['remaining_principal_cents'] for row in debts)
        wishes = []
        for row in entities(con, 'wish'):
            target = row['price_cents']*10
            unlock = 'unknown_inputs' if debt_cents is None or debt_cents < 0 or cash_cents is None else 'debt_outstanding' if debt_cents != 0 else 'eligible' if cash_cents >= target else 'saving'
            wishes.append({**row, 'target_savings_cents': target, 'confirmed_cash_cents': cash_cents,
                           'unlock_status': unlock, 'unlocked': unlock == 'eligible',
                           'shortfall_cents': None if cash_cents is None else max(0, target-cash_cents)})
        current = [row for row in entries if row.get('confirmed', True) and row['date'].startswith(month+'-') and row['date'] <= through]
        budgets = []
        saved = {row['category']: row for row in entities(con, 'budget') if row['month'] == month}
        for category, members in BUDGET_GROUPS.items():
            row = saved.get(category)
            inherited = legacy_budget_rows(con, month, category) if row is None else []
            consumed = sum(entry['cents']*(1 if entry['type'] == 'expense' else -1) for entry in current
                           if entry.get('track') == 'daily' and entry.get('budget_category') in members
                           and entry['type'] in ('expense', 'refund'))
            cents = row['cents'] if row else sum(r['cents'] for r in inherited) if inherited else 60000 if category == '娱乐' and month >= '2026-10' else None
            remaining = None if cents is None else cents-consumed
            ratio = None if cents is None else (float(Decimal(remaining)/cents) if cents else (0.0 if consumed >= 0 else None))
            budgets.append({**(row or {'id': month+':group:'+BUDGET_GROUP_KEYS[category], 'revision': 0}),
                            'categories':list(members), 'inherited_from_legacy':bool(inherited),
                            'legacy_revisions':{r['id']:r['revision'] for r in inherited},
                            'month': month, 'category': category, 'cents': cents, 'consumed_cents': consumed,
                            'remaining_cents': remaining, 'remainder_ratio': ratio})
        coverage = coverage_state(con)
    return {'month': month, 'as_of': through, 'accounts': accounts,
            'debts': [d for d in debts if not d['archived']],
            'archived_debts': [d for d in debts if d['archived']],
            'card_statements': sorted(card_statements, key=lambda b:(b['consumption_month'],b['id']), reverse=True),
            'payments': sorted(payments, key=lambda row:(row['date'],row['id']), reverse=True),
            'wishes': wishes, 'budgets': budgets, 'coverage': coverage, 'settings': settings,
            'debt_forecasts':[dashboard_forecast(debts, card_statements, month, month),
                              dashboard_forecast(debts, card_statements, next_month(month), month)],
            'summary': {'confirmed_cash_cents': cash_cents,
                        'confirmed_cash_subtotal_cents':sum(row['current_balance_cents'] for row in cash if row['current_balance_cents'] is not None and not row['pending_entry_ids']),
                        'remaining_debt_cents': debt_cents,
                        'accounts_pending_count': sum(row['current_balance_cents'] is None or bool(row['pending_entry_ids']) for row in accounts),
                        'unknown_debt_count': sum(row['remaining_principal_cents'] is None for row in debts),
                        'monthly_income_cents': sum(row['cents'] for row in current if row['type'] == 'income'),
                        'monthly_expense_cents': sum(row['cents'] for row in current if row['type'] == 'expense'),
                        'monthly_refund_cents': sum(row['cents'] for row in current if row['type'] == 'refund'),
                        'daily_net_expense_cents': sum(row['cents']*(1 if row['type'] == 'expense' else -1) for row in current if row.get('track') == 'daily' and row['type'] in ('expense', 'refund')),
                        'daily_confirmed_count': sum(row.get('track') == 'daily' and row['type'] in ('income', 'expense', 'refund') for row in current),
                        'unmapped_daily_count': sum(row['type'] in ('expense','refund') and (row.get('track','unknown') == 'unknown' or (row.get('track') == 'daily' and row.get('budget_category','unknown') == 'unknown')) for row in current)}}

# Cloud trial: the local CLI follows the authoritative dataset.
import cloud_authority
cloud_authority.install(globals(), 'finance_hub')
