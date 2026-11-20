"""Opt-in kitchen access to the account's minimal, private nutrition context."""
from datetime import datetime
import json

import tenant_storage
import wellness


def private():
    with wellness.connection() as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS dietary_profile(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT)')
        row = connection.execute('SELECT data FROM dietary_profile WHERE id=1').fetchone()
    profile = json.loads(row['data']) if row else {'conditions': [], 'habits': '', 'life_stage': 'adult'}
    reports = wellness.reports()
    for key, output in [('height', 'height_cm'), ('weight', 'weight_kg')]:
        found = next((r for r in reports if r.get(key) is not None), None)
        profile[output] = {'value': found[key], 'date': found['date']} if found else None
    return profile


def save(value):
    import recipes
    stage = value.get('life_stage', 'adult')
    if stage not in ('adult', 'child', 'pregnant', 'older'):
        raise ValueError('请选择有效的生命阶段')
    profile = {'conditions': recipes.strings(value.get('conditions', []), 20, 60),
               'habits': recipes.text(value.get('habits', ''), 600, True), 'life_stage': stage,
               'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
               'condition_source': '本人或照护者记录，非系统诊断'}
    with wellness.connection() as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS dietary_profile(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT)')
        connection.execute('INSERT OR REPLACE INTO dietary_profile VALUES(1,?)', (json.dumps(profile, ensure_ascii=False),))


def shared(member):
    account = member.get('health_account')
    if not account:
        return None
    with tenant_storage.scope(account):
        return private()
