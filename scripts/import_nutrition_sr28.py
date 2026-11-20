"""Build a small, auditable ingredient table from the official USDA SR28 archive.

Usage: python3 scripts/import_nutrition_sr28.py /path/to/sr28asc.zip
The source archive is not committed. All weights are edible raw weights unless
explicitly marked; household units and yield factors are approximate.
"""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

SOURCE = 'https://www.ars.usda.gov/ARSUserFiles/80400525/Data/SR/SR28/dnload/sr28asc.zip'
# Chinese aliases must describe the actual food, not a fuzzy translation.
FOODS = [
    ('10005', ['五花肉'], 'other', {}, 1),
    ('10219', ['猪肉末', '肉末'], 'other', {}, 1),
    ('10088', ['排骨'], 'other', {}, .7),
    ('05062', ['鸡胸肉'], 'other', {}, 1),
    ('05100', ['鸡翅中'], 'other', {'个': 50}, .7),
    ('15003', ['鲈鱼'], 'other', {}, .5),
    ('01123', ['鸡蛋'], 'other', {'个': 50}, 1),
    ('11529', ['番茄', '西红柿'], 'vegetable', {'个': 180}, 1),
    ('11090', ['西兰花'], 'vegetable', {'颗': 350}, 1),
    ('11352', ['土豆', '马铃薯'], 'tuber', {'个': 180}, 1),
    ('11052', ['四季豆', '菜豆'], 'vegetable', {}, 1),
    ('11333', ['青椒'], 'vegetable', {'个': 100}, 1),
    ('11205', ['黄瓜'], 'vegetable', {'根': 200}, 1),
    ('11116', ['小白菜', '青菜', '油菜'], 'vegetable', {}, 1),
    ('11119', ['大白菜'], 'vegetable', {}, 1),
    ('11457', ['菠菜'], 'vegetable', {}, 1),
    ('11124', ['胡萝卜'], 'vegetable', {'根': 100}, 1),
    ('11291', ['葱', '小葱', '葱花'], 'vegetable', {'根': 10}, 1),
    ('11216', ['姜', '生姜'], 'other', {'片': 2}, 1),
    ('11215', ['蒜', '大蒜', '蒜瓣'], 'other', {'瓣': 3}, 1),
    ('16127', ['嫩豆腐'], 'other', {'块': 300}, 1),
    ('16087', ['花生', '花生米'], 'other', {}, 1),
    ('20444', ['大米', '白米', '生大米'], 'refined', {}, 1),
    ('20036', ['糙米'], 'whole', {}, 1),
    ('20038', ['燕麦米', '燕麦粒'], 'whole', {}, 1),
    ('08120', ['原味燕麦片', '纯燕麦片'], 'whole', {}, 1),
    ('20031', ['小米'], 'whole', {}, 1),
    ('20080', ['全麦面粉'], 'whole', {}, 1),
    ('20081', ['白面粉', '小麦面粉', '面粉'], 'refined', {}, 1),
    ('16032', ['红芸豆'], 'pulse', {}, 1),
    ('16080', ['绿豆'], 'pulse', {}, 1),
    ('11167', ['鲜玉米粒'], 'whole', {}, 1),
    ('20045', ['熟米饭', '白米饭'], 'refined', {}, 1),
    ('01077', ['全脂牛奶', '牛奶'], 'other', {'ml': 1.03}, 1),
    ('16123', ['生抽', '老抽', '酱油'], 'other', {'ml': 1.1, '勺': 16.5}, 1),
    ('06176', ['蚝油'], 'other', {'ml': 1.2, '勺': 18}, 1),
    ('02053', ['醋', '白醋'], 'other', {'ml': 1, '勺': 15}, 1),
    ('19335', ['白糖', '冰糖', '白砂糖'], 'other', {'勺': 12}, 1),
    ('04044', ['食用油', '大豆油'], 'other', {'ml': .92, '勺': 13.8}, 1),
    ('04058', ['芝麻油', '香油'], 'other', {'ml': .92, '勺': 13.8}, 1),
    ('14148', ['可乐'], 'other', {'ml': 1.04}, 1),
    ('20027', ['淀粉', '玉米淀粉'], 'refined', {'勺': 8}, 1),
    ('02047', ['盐', '食盐'], 'other', {'勺': 18}, 1),
    ('14411', ['水', '清水'], 'other', {'ml': 1}, 1),
]


def rows(archive, name):
    return [[part.strip('~') for part in line.split('^')]
            for line in archive.read(name).decode('latin1').splitlines()]


def build(path):
    archive = zipfile.ZipFile(path)
    names = {r[0]: r[2] for r in rows(archive, 'FOOD_DES.txt')}
    wanted = {item[0] for item in FOODS}
    nutrients = {}
    for row in rows(archive, 'NUT_DATA.txt'):
        if row[0] in wanted and row[1] in ('203', '204', '205', '291'):
            nutrients.setdefault(row[0], {})[row[1]] = float(row[2])
    foods = []
    for ndb, aliases, group, units, edible in FOODS:
        values = nutrients[ndb]
        if set(values) != {'203', '204', '205', '291'}:
            raise ValueError('Incomplete reference food: ' + ndb)
        foods.append({'ndb': ndb, 'description': names[ndb], 'aliases': aliases,
                      'group': group, 'grams_per_unit': units, 'edible_fraction': edible,
                      'per100g': {'carbs': round(max(0, values['205'] - values['291']), 3),
                                  'protein': values['203'], 'fat': values['204'], 'fiber': values['291']}})
    return {'version': 1, 'source': {'name': 'USDA National Nutrient Database SR28 (2015)',
            'url': SOURCE, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'basis': '每100g可食部；碳水=USDA总碳水减膳食纤维；原料生重，熟米饭除外',
            'approximations': '家用单位重量及带骨食材可食比例为常见用量估算；不识别的食材不自动类推'},
            'foods': foods}


if __name__ == '__main__':
    output = Path(__file__).resolve().parents[1] / 'data/food-nutrients-v1.json'
    output.write_text(json.dumps(build(sys.argv[1]), ensure_ascii=False, indent=2) + '\n')
    print('Wrote', len(FOODS), 'reference foods')
