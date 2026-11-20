"""An explicitly shared family kitchen, independent of private account records."""
import contextlib
import copy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

import tenant_storage
import recipe_nutrition
import recipe_advice
import recipe_health


CATALOG = Path(__file__).resolve().parents[1] / 'data/recipes-v1.json'
CATEGORIES = ('荤菜', '素菜', '汤羹', '主食', '快手')
UNITS = ('g', 'ml', '个', '根', '片', '瓣', '勺', '颗', '块', '把')
GROUPS = {
    '海鲜': ('鱼', '虾', '蟹', '贝', '蚝', '蛤', '鱿', '海鲜'),
    '鱼': ('鱼',), '虾': ('虾', '虾皮', '虾酱'),
    '蛋': ('蛋',), '奶': ('奶', '乳', '黄油'),
    '花生': ('花生',), '大豆': ('豆腐', '豆浆', '豆干', '生抽', '老抽', '豆瓣酱', '酱油'),
    '小麦': ('面粉', '面条', '生抽', '老抽', '酱油'),
    '坚果': ('花生', '核桃', '腰果', '杏仁', '坚果'),
    '辣': ('辣', '豆瓣酱', '干椒', '花椒'),
    '猪肉': ('猪', '五花肉', '肉末', '排骨', '里脊'),
    '葱': ('葱',), '香菜': ('香菜',),
}


class ConflictError(ValueError):
    pass


def text(value, maximum=100, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError('请检查名称或内容的长度')
    return value.strip()


def number(value, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError('请填写有效数量')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('请填写有效数量') from None
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError('数量超出允许范围')
    return result


def integer(value, minimum, maximum):
    result = number(value, minimum, maximum)
    if result != result.to_integral_value():
        raise ValueError('请填写整数')
    return int(result)


def strings(value, limit=30, maximum=40):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('列表内容过多或格式无效')
    return list(dict.fromkeys(text(item, maximum) for item in value))


def identifier():
    return uuid.uuid4().hex


def initial():
    return {'recipes': json.loads(CATALOG.read_text()), 'members': [], 'wishes': [],
            'pantry': [], 'plan': None, 'history': []}


@contextlib.contextmanager
def connect():
    import cloud_authority
    cloud_authority.guard_local_storage()
    database = tenant_storage.BASE / 'recipes.sqlite3'
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(database, timeout=15)
    connection.row_factory = sqlite3.Row
    database.chmod(0o600)
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS kitchens(id TEXT PRIMARY KEY, owner TEXT, revision INTEGER, data TEXT);
        CREATE TABLE IF NOT EXISTS kitchen_accounts(user TEXT PRIMARY KEY, kitchen TEXT);
        CREATE TABLE IF NOT EXISTS kitchen_invites(hash TEXT PRIMARY KEY, kitchen TEXT, expires INTEGER, used INTEGER);
    ''')
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def kitchen(connection):
    user = tenant_storage.current()
    row = connection.execute('SELECT k.* FROM kitchens k JOIN kitchen_accounts a ON a.kitchen=k.id WHERE a.user=?', (user,)).fetchone()
    if row:
        return dict(row)
    key = identifier()
    row = {'id': key, 'owner': user, 'revision': 0, 'data': json.dumps(initial(), ensure_ascii=False)}
    connection.execute('INSERT INTO kitchens VALUES(:id,:owner,:revision,:data)', row)
    connection.execute('INSERT INTO kitchen_accounts VALUES(?,?)', (user, key))
    return row


def exposed(connection, row, data):
    result = copy.deepcopy(data)
    for wish in result['wishes']:
        wish['mine'] = wish.pop('author', '') == tenant_storage.current()
    for member in result['members']:
        member['health'] = recipe_health.shared(member)
        member['health_mine'] = member.pop('health_account', None) == tenant_storage.current()
    for meal in result['history'] + ([result['plan']] if result['plan'] else []):
        for member in meal['members']:
            member.pop('health_account', None)
    result.update(revision=row['revision'], owner=row['owner'] == tenant_storage.current(),
                  account_count=connection.execute('SELECT count(*) FROM kitchen_accounts WHERE kitchen=?', (row['id'],)).fetchone()[0])
    if result['plan']:
        result['plan']['shopping'] = grocery(result['plan'], data['pantry'])
        result['plan']['nutrition'] = recipe_nutrition.estimate(result['plan'])
    result['nutrition_targets'] = recipe_nutrition.targets(data)
    result['nutrition_review'] = recipe_nutrition.review(data)
    result['nutrition_advice'] = recipe_advice.status(connection, row['id'])
    result['my_nutrition_health'] = recipe_health.private()
    return result


def state():
    with connect() as connection:
        row = kitchen(connection)
        return exposed(connection, row, json.loads(row['data']))


def validate_recipe(value):
    if not isinstance(value, dict):
        raise ValueError('菜谱格式无效')
    category = value.get('category')
    if category not in CATEGORIES:
        raise ValueError('请选择菜谱分类')
    result = {'id': text(value.get('id') or identifier()), 'name': text(value.get('name'), 60),
              'category': category, 'minutes': integer(value.get('minutes'), 1, 600),
              'servings': float(number(value.get('servings'), Decimal('.25'), 30)),
              'tags': strings(value.get('tags', []), 15), 'source': '家庭自建',
              'steps': strings(value.get('steps'), 40, 600)}
    if not result['steps']:
        raise ValueError('至少填写一个制作步骤')
    for field in ('ingredients', 'seasonings'):
        values = value.get(field, [])
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError('食材数量过多或格式无效')
        result[field] = []
        for item in values:
            if not isinstance(item, dict):
                raise ValueError('食材格式无效')
            unit = text(item.get('unit', 'g'), 10)
            if unit not in UNITS:
                raise ValueError('请使用支持的计量单位')
            quantity = item.get('quantity')
            result[field].append({'name': text(item.get('name'), 40), 'unit': unit,
                                  'quantity': None if quantity is None else float(number(quantity, Decimal('.01'), 100000)),
                                  'note': text(item.get('note', ''), 60, True)})
    if not result['ingredients']:
        raise ValueError('至少填写一种食材')
    manual = value.get('nutrition_per_serving')
    if manual is not None:
        if not isinstance(manual, dict) or set(manual) != set(recipe_nutrition.KEYS):
            raise ValueError('请完整填写每份的四项营养值，或全部留空')
        result['nutrition_per_serving'] = {key: float(number(manual[key], 0, 2000)) for key in recipe_nutrition.KEYS}
    return result


def conflicts(recipe, members):
    haystack = ' '.join([recipe['name']] + recipe.get('tags', []) + [item['name'] for field in ('ingredients', 'seasonings') for item in recipe[field]])
    result = []
    for member in members:
        for term in member['avoid']:
            if any(word in haystack for word in GROUPS.get(term, (term,))):
                result.append(member['name'] + '忌口：' + term)
    return result


def diners(data, selected):
    keys = strings(selected, 30, 100)
    members = [member for member in data['members'] if member['id'] in keys]
    if len(members) != len(keys):
        raise ValueError('用餐成员已变化，请重新选择')
    return members


def make_plan(data, request):
    members = diners(data, request.get('members', []))
    guests = integer(request.get('guests', 0), 0, 30)
    count = len(members) + guests
    if not count:
        raise ValueError('请选择用餐成员，或填写临时用餐人数')
    target = integer(request.get('count', 2 if count == 1 else 4 if count == 2 else 6), 1, 8)
    day = text(request.get('date', date.today().isoformat()), 10)
    date.fromisoformat(day)
    meal_type = request.get('meal_type', 'dinner')
    if meal_type not in recipe_nutrition.MEALS:
        raise ValueError('请选择有效餐次')
    quick = request.get('quick', False) is True
    available = [recipe for recipe in data['recipes'] if not conflicts(recipe, members) and (not quick or recipe['minutes'] <= 20)]
    selected_ids = request.get('recipes')
    if selected_ids is not None:
        keys = strings(selected_ids, 8, 100)
        recipes = [next((recipe for recipe in available if recipe['id'] == key), None) for key in keys]
        if any(recipe is None for recipe in recipes):
            raise ValueError('选中的菜有忌口冲突、超时或已被删除，请调整后再保存')
    else:
        secrets.SystemRandom().shuffle(available)
        wanted = [wish['name'] for wish in data['wishes'] if not wish['done']]
        previous = set(recipe['id'] for recipe in (data.get('plan') or {}).get('recipes', []))
        available.sort(key=lambda recipe: (recipe['id'] in previous, not any(name in recipe['name'] or recipe['name'] in name for name in wanted)))
        recipes = []
        for group in ('蛋白', '蔬菜', '主食', '汤羹')[:target]:
            match = next((recipe for recipe in available if recipe not in recipes and (group in recipe['tags'] or recipe['category'] == group)), None)
            if match:
                recipes.append(match)
        recipes += [recipe for recipe in available if recipe not in recipes][:max(0, target - len(recipes))]
    if not recipes:
        raise ValueError('没有符合条件的菜谱。可以补充菜谱或调整快手筛选；忌口仍会保留')
    return {'id': identifier(), 'date': day, 'meal_type': meal_type, 'members': copy.deepcopy(members), 'guests': guests,
            'equivalent': float(sum((Decimal(str(member['portion'])) for member in members), Decimal(guests))),
            'count': target, 'quick': quick, 'recipes': copy.deepcopy(recipes), 'checked': [],
            'notice': '' if selected_ids is not None or len(recipes) >= target else f'符合条件的菜只有 {len(recipes)} 道，未强行补足。',
            'saved': False}


def grocery(plan, pantry):
    totals = {}
    for recipe in plan['recipes']:
        factor = Decimal(str(plan['equivalent'])) / Decimal(str(recipe['servings']))
        for field in ('ingredients', 'seasonings'):
            for item in recipe[field]:
                key = (item['name'], item['unit'])
                total = totals.setdefault(key, {'name': key[0], 'unit': key[1], 'quantity': Decimal(0), 'unmeasured': False, 'seasoning': True})
                total['seasoning'] &= field == 'seasonings'
                if item['quantity'] is None:
                    total['unmeasured'] = True
                else:
                    total['quantity'] += Decimal(str(item['quantity'])) * factor
    result = []
    for (name, unit), item in totals.items():
        stock = sum((Decimal(str(entry['quantity'])) for entry in pantry if entry['name'] == name and entry['unit'] == unit), Decimal(0))
        need = max(Decimal(0), item['quantity'] - stock)
        increment = Decimal(10 if unit == 'g' else 5 if unit == 'ml' else 1)
        purchase = (need / increment).quantize(Decimal(1), rounding=ROUND_CEILING) * increment
        key = name + '|' + unit
        result.append(dict(item, id=key, quantity=float(item['quantity']), stock=float(stock), purchase=float(purchase), checked=key in plan['checked']))
    return sorted(result, key=lambda item: (item['seasoning'], item['name']))


def handle(request):
    if not isinstance(request, dict):
        raise ValueError('请求格式无效')
    action = request.get('action')
    user = tenant_storage.current()
    if user == 'demo':
        raise PermissionError('请登录自己的账号保存菜谱')
    if action == 'analysis-request':
        return recipe_advice.request(request)
    if action == 'health-profile-save':
        recipe_health.save(request)
        with connect() as connection:
            row = kitchen(connection)
            recipe_advice.setup(connection)
            connection.execute('DELETE FROM kitchen_analyses WHERE kitchen=?', (row['id'],))
        return state()
    with connect() as connection:
        row = kitchen(connection)
        data = json.loads(row['data'])
        if action == 'join':
            digest = hashlib.sha256(text(request.get('code'), 100).encode()).hexdigest()
            invite = connection.execute('SELECT * FROM kitchen_invites WHERE hash=?', (digest,)).fetchone()
            if not invite or invite['used'] or invite['expires'] < time.time():
                raise ValueError('邀请码无效、已使用或已过期')
            if invite['kitchen'] == row['id']:
                raise ValueError('你已经在这个家庭厨房中')
            if row['owner'] == user and connection.execute('SELECT count(*) FROM kitchen_accounts WHERE kitchen=?', (row['id'],)).fetchone()[0] > 1:
                raise ValueError('你创建的厨房已有家人加入，暂时不能切换到其他厨房')
            if connection.execute('SELECT count(*) FROM kitchen_accounts WHERE kitchen=?', (invite['kitchen'],)).fetchone()[0] >= 30:
                raise ValueError('家庭厨房最多加入30个账号')
            if any(m.get('health_account') == user for m in data['members']):
                for member in data['members']:
                    if member.get('health_account') == user:
                        member.pop('health_account')
                connection.execute('UPDATE kitchens SET data=?,revision=revision+1 WHERE id=?', (json.dumps(data, ensure_ascii=False), row['id']))
                recipe_advice.setup(connection)
                connection.execute('DELETE FROM kitchen_analyses WHERE kitchen=?', (row['id'],))
            connection.execute('UPDATE kitchen_accounts SET kitchen=? WHERE user=?', (invite['kitchen'], user))
            connection.execute('UPDATE kitchen_invites SET used=1 WHERE hash=?', (digest,))
            row = dict(connection.execute('SELECT * FROM kitchens WHERE id=?', (invite['kitchen'],)).fetchone())
            return exposed(connection, row, json.loads(row['data']))
        if request.get('revision') != row['revision']:
            raise ConflictError('家人刚更新了内容，请刷新后再操作；本次修改未保存')
        if action == 'invite':
            if row['owner'] != user:
                raise PermissionError('只有创建这个厨房的账号可以邀请家人')
            code = secrets.token_urlsafe(18)
            connection.execute('DELETE FROM kitchen_invites WHERE kitchen=?', (row['id'],))
            connection.execute('INSERT INTO kitchen_invites VALUES(?,?,?,0)', (hashlib.sha256(code.encode()).hexdigest(), row['id'], int(time.time()) + 86400))
            return dict(exposed(connection, row, data), invite=code)
        if action == 'recipe-save':
            recipe = validate_recipe(request.get('recipe'))
            old = next((item for item in data['recipes'] if item['id'] == recipe['id']), None)
            if old:
                data['recipes'][data['recipes'].index(old)] = recipe
            else:
                if len(data['recipes']) >= 1000:
                    raise ValueError('当前厨房最多保存1000道菜谱')
                data['recipes'].append(recipe)
        elif action == 'recipe-delete':
            key = text(request.get('id'))
            data['recipes'] = [recipe for recipe in data['recipes'] if recipe['id'] != key]
        elif action == 'member-save':
            member = {'id': text(request.get('id') or identifier()), 'name': text(request.get('name'), 30),
                      'avoid': strings(request.get('avoid', [])),
                      'portion': float(number(request.get('portion', 1), Decimal('.25'), 2))}
            old = next((item for item in data['members'] if item['id'] == member['id']), None)
            if old:
                if old.get('health_account'):
                    member['health_account'] = old['health_account']
                data['members'][data['members'].index(old)] = member
            else:
                if len(data['members']) >= 30:
                    raise ValueError('最多记录30位用餐成员')
                data['members'].append(member)
            if data['plan'] and any(item['id'] == member['id'] for item in data['plan']['members']):
                data['plan'] = None
        elif action == 'member-delete':
            key = text(request.get('id'))
            data['members'] = [member for member in data['members'] if member['id'] != key]
            if data['plan'] and any(item['id'] == key for item in data['plan']['members']):
                data['plan'] = None
            recipe_advice.setup(connection)
            connection.execute('DELETE FROM kitchen_analyses WHERE kitchen=?', (row['id'],))
        elif action in ('health-link', 'health-unlink'):
            member = next((m for m in data['members'] if m['id'] == request.get('id')), None)
            if not member:
                raise ValueError('请先添加家人')
            linked = member.get('health_account')
            if linked and linked != user and not (action == 'health-unlink' and row['owner'] == user):
                raise PermissionError('请由该健康档案的账号本人更改关联')
            if action == 'health-link':
                if any(m.get('health_account') == user and m['id'] != member['id'] for m in data['members']):
                    raise ValueError('你的健康档案已经关联其他成员，请先解除原关联')
                member['health_account'] = user
            else:
                member.pop('health_account', None)
            recipe_advice.setup(connection)
            connection.execute('DELETE FROM kitchen_analyses WHERE kitchen=?', (row['id'],))
        elif action == 'wish-add':
            member = text(request.get('member', '家人'), 30)
            if len(data['wishes']) >= 200:
                raise ValueError('请先清理已安排的点菜记录')
            data['wishes'].insert(0, {'id': identifier(), 'member': member, 'name': text(request.get('name'), 60),
                                     'author': user, 'done': False, 'at': date.today().isoformat()})
        elif action in ('wish-done', 'wish-delete'):
            wish = next((item for item in data['wishes'] if item['id'] == request.get('id')), None)
            if not wish:
                raise ValueError('这条点菜记录已不存在')
            if action == 'wish-delete':
                if wish['author'] != user and row['owner'] != user:
                    raise PermissionError('只能删除自己的点菜记录')
                data['wishes'].remove(wish)
            else:
                wish['done'] = not wish['done']
        elif action == 'pantry-save':
            items = request.get('items')
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError('库存最多100项')
            data['pantry'] = []
            seen = set()
            for item in items:
                if not isinstance(item, dict) or item.get('unit') not in UNITS:
                    raise ValueError('库存单位无效')
                key = (text(item.get('name'), 40), item['unit'])
                if key in seen:
                    raise ValueError('同名同单位库存请合并填写')
                seen.add(key)
                data['pantry'].append({'name': key[0], 'unit': key[1], 'quantity': float(number(item.get('quantity'), 0, 100000))})
        elif action == 'plan-generate':
            data['plan'] = make_plan(data, request)
        elif action == 'shopping-check':
            if not data['plan']:
                raise ValueError('请先安排一餐')
            key = text(request.get('id'))
            if key not in [item['id'] for item in grocery(data['plan'], data['pantry'])]:
                raise ValueError('采购项目已变化')
            checked = data['plan']['checked']
            checked.remove(key) if key in checked else checked.append(key)
        elif action == 'plan-save':
            if not data['plan']:
                raise ValueError('请先安排一餐')
            if not data['plan']['saved']:
                snapshot = copy.deepcopy(data['plan'])
                snapshot['shopping'] = grocery(snapshot, data['pantry'])
                snapshot['saved_at'] = datetime.now().isoformat(timespec='seconds')
                snapshot['feedback'] = ''
                snapshot['nutrition'] = recipe_nutrition.estimate(snapshot)
                snapshot['consumed'] = False
                data['history'].insert(0, snapshot)
                data['history'] = data['history'][:366]
                data['plan']['saved'] = True
        elif action == 'nutrition-targets':
            values = request.get('targets')
            if not isinstance(values, dict) or set(values) != set(recipe_nutrition.KEYS):
                raise ValueError('请填写四项每日参考目标；没有目标可留空')
            data['nutrition_targets'] = {key: None if values[key] is None else float(number(values[key], Decimal('.1'), 2000)) for key in recipe_nutrition.KEYS}
        elif action == 'history-consumed':
            meal = next((item for item in data['history'] if item['id'] == request.get('id')), None)
            if not meal or not isinstance(request.get('consumed'), bool):
                raise ValueError('找不到用餐记录或确认状态无效')
            if request['consumed'] and meal['date'] > recipe_nutrition.today().isoformat():
                raise ValueError('未来的餐单还不能确认吃过')
            meal['consumed'] = request['consumed']
            meal['nutrition'] = meal.get('nutrition') or recipe_nutrition.estimate(meal)
        elif action == 'history-feedback':
            meal = next((item for item in data['history'] if item['id'] == request.get('id')), None)
            if not meal:
                raise ValueError('找不到这餐记录')
            meal['feedback'] = text(request.get('feedback', ''), 300, True)
        else:
            raise ValueError('不支持的菜谱操作')
        row['revision'] += 1
        connection.execute('UPDATE kitchens SET data=?,revision=? WHERE id=?', (json.dumps(data, ensure_ascii=False), row['revision'], row['id']))
        return exposed(connection, row, data)
