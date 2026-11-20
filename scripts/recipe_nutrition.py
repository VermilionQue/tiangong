"""Local nutrient estimates and bounded facts; no model calls on reads."""
from datetime import datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
from zoneinfo import ZoneInfo

KEYS = ('carbs', 'protein', 'fat', 'fiber')
MEALS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐', 'other': '其他餐次'}
FIBER_SOURCE = 'https://www.nhc.gov.cn/wjw/jiany/202301/bd6c614391274ebd955fc9018f2032a2.shtml'
GRAIN_SOURCE = 'https://dg.cnsoc.org/article/04/70JvPbFmTlyZbjoO67LeRg.html'


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


@lru_cache(maxsize=1)
def table():
    data = json.loads((Path(__file__).resolve().parents[1] / 'data/food-nutrients-v1.json').read_text())
    return data, {name: food for food in data['foods'] for name in food['aliases']}


def estimate(plan):
    totals = dict.fromkeys(KEYS, 0.)
    groups = dict.fromkeys(('whole', 'refined', 'pulse', 'vegetable', 'tuber'), 0.)
    missing, approximate = set(), set()
    measured, entries = 0, 0
    equivalent = float(plan['equivalent'])
    for recipe in plan['recipes']:
        scale = equivalent / float(recipe['servings'])
        manual = recipe.get('nutrition_per_serving')
        if manual:
            for key in KEYS:
                totals[key] += manual[key] * equivalent
        for field in ('ingredients', 'seasonings'):
            for item in recipe[field]:
                entries += 1
                name, quantity = item['name'], item['quantity']
                food = table()[1].get(name)
                reason = '未填用量' if quantity is None else '尚无匹配食材' if not food else ''
                unit = item['unit']
                grams = None
                if not reason:
                    if unit == 'g':
                        grams = float(quantity)
                    elif unit in food['grams_per_unit']:
                        grams = float(quantity) * food['grams_per_unit'][unit]
                        approximate.add(name)
                    else:
                        reason = '请换算为克'
                if reason:
                    if not manual:
                        missing.add(name + '：' + reason)
                    continue
                grams *= scale * food['edible_fraction']
                if food['edible_fraction'] != 1:
                    approximate.add(name)
                if field == 'ingredients' and food['group'] in groups:
                    groups[food['group']] += grams
                measured += 1
                if not manual:
                    for key in KEYS:
                        totals[key] += grams / 100 * food['per100g'][key]
        if manual:
            measured += 1
            entries += 1
    valid = measured > 0
    return {'total': {key: round(value, 1) if valid else None for key, value in totals.items()},
            'per_serving': {key: round(value / equivalent, 1) if valid else None for key, value in totals.items()},
            'groups_per_serving': {key: round(value / equivalent, 1) for key, value in groups.items()},
            'missing': sorted(missing), 'approximate': sorted(approximate),
            'measured': measured, 'entries': entries, 'partial': bool(missing),
            'basis': '按已计量原料估算；每份为成人食量折算，不是每位家人的实际摄入',
            'data_version': 1, 'source_url': table()[0]['source']['url']}


def targets(data):
    return data.get('nutrition_targets', dict.fromkeys(KEYS))


def review(data, day=None):
    day = day or today()
    start = day - timedelta(days=6)
    meals = [meal for meal in data['history'] if meal.get('consumed') is True
             and start.isoformat() <= meal['date'] <= day.isoformat()]
    daily = {}
    refined, whole, partial = 0, 0, 0
    for meal in meals:
        nutrition = meal.get('nutrition') or estimate(meal)
        entry = daily.setdefault(meal['date'], {'date': meal['date'], 'meals': 0, 'slots': [],
                    'per_serving': dict.fromkeys(KEYS), 'partial': False})
        entry['meals'] += 1
        slot = meal.get('meal_type', 'other')
        if slot not in entry['slots']:
            entry['slots'].append(slot)
        for key in KEYS:
            value = nutrition['per_serving'][key]
            if value is not None:
                entry['per_serving'][key] = (entry['per_serving'][key] or 0) + value
        entry['partial'] |= nutrition['partial']
        partial += int(nutrition['partial'])
        group = nutrition['groups_per_serving']
        grains = group['refined'] + group['whole'] + group['pulse']
        if grains:
            refined += int(group['refined'] / grains >= .8)
            whole += int(group['whole'] + group['pulse'] > 0)
    observations = []
    if refined >= 3 and whole == 0:
        observations.append(f'最近确认的{len(meals)}餐里，{refined}餐主食以精制谷物为主，未记录全谷物或杂豆；下一餐可以把部分白米换成糙米、燕麦米或杂豆。')
    elif whole:
        observations.append(f'最近确认的{len(meals)}餐里，{whole}餐记录了全谷物或杂豆，可以继续轮换食材。')
    elif meals:
        observations.append('目前确认的餐次较少或主食记录不完整，暂不判断近期饮食趋势。')
    else:
        observations.append('还没有确认吃过的记录。保存餐单后，到用餐记录点“确认吃过”，再看近期饮食变化。')
    if partial:
        observations.append(f'{partial}餐含未计量或未匹配的食材，营养数值只覆盖已计量部分。补齐油、糖等用量后会更接近实际。')
    for entry in daily.values():
        entry['per_serving'] = {k: round(v, 1) if v is not None else None for k, v in entry['per_serving'].items()}
    return {'from': start.isoformat(), 'to': day.isoformat(), 'meal_count': len(meals),
            'day_count': len(daily), 'refined_meals': refined, 'whole_meals': whole,
            'daily': sorted(daily.values(), key=lambda item: item['date'], reverse=True),
            'observations': observations, 'sources': [GRAIN_SOURCE, FIBER_SOURCE]}
