import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recipes
import tenant_storage


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = patch.object(tenant_storage, 'BASE', Path(self.directory.name))
        self.guard = patch('cloud_authority.guard_local_storage')
        self.base.start()
        self.guard.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.base.stop)
        self.addCleanup(self.guard.stop)

    def change(self, action, **values):
        return recipes.handle(dict(action=action, revision=recipes.state()['revision'], **values))

    def member(self, name='家人', avoid=None, portion=1):
        return self.change('member-save', name=name, avoid=avoid or [], portion=portion)['members'][-1]

    def test_open_catalog_crud_and_persistence(self):
        state = recipes.state()
        self.assertEqual(len(state['recipes']), 15)
        recipe = copy.deepcopy(state['recipes'][0])
        recipe.update(id='', name='自己的拿手菜')
        saved = self.change('recipe-save', recipe=recipe)['recipes'][-1]
        self.assertEqual(recipes.state()['recipes'][-1]['name'], '自己的拿手菜')
        self.change('recipe-delete', id=saved['id'])
        self.assertEqual(len(recipes.state()['recipes']), 15)

    def test_dietary_groups_and_seasonings_block_unsafe_selection(self):
        member = self.member(avoid=['海鲜', '花生', '大豆'])
        state = self.change('plan-generate', members=[member['id']], guests=0, count=6)
        self.assertTrue(state['plan']['recipes'])
        for recipe in state['plan']['recipes']:
            self.assertEqual(recipes.conflicts(recipe, [member]), [])
        for key in ['seed-9', 'seed-15']:
            with self.assertRaisesRegex(ValueError, '忌口'):
                self.change('plan-generate', members=[member['id']], guests=0, recipes=[key])

    def test_portion_scaling_pantry_and_unit_safe_aggregation(self):
        adult = self.member('成人')
        child = self.member('孩子', portion=.5)
        self.change('pantry-save', items=[dict(name='大米', quantity=50, unit='g'), dict(name='鸡蛋', quantity=2, unit='g')])
        state = self.change('plan-generate', members=[adult['id'], child['id']], guests=1, recipes=['seed-13', 'seed-2', 'seed-12'])
        plan = state['plan']
        self.assertEqual(plan['equivalent'], 2.5)
        rice = next(item for item in plan['shopping'] if item['name'] == '大米')
        self.assertEqual(rice['quantity'], 187.5)
        self.assertEqual(rice['purchase'], 140)
        eggs = next(item for item in plan['shopping'] if item['name'] == '鸡蛋')
        self.assertEqual(eggs['stock'], 0)
        self.assertGreater(eggs['quantity'], 3)

    def test_invite_shares_only_kitchen_and_code_is_single_use(self):
        self.member('妈妈')
        code = self.change('invite')['invite']
        with tenant_storage.scope('u' + 'a' * 32):
            self.assertEqual(recipes.state()['members'], [])
            joined = self.change('join', code=code)
            self.assertEqual(joined['members'][0]['name'], '妈妈')
            self.change('wish-add', member='我', name='番茄炒蛋')
            with self.assertRaises(PermissionError):
                self.change('invite')
        self.assertEqual(recipes.state()['wishes'][0]['name'], '番茄炒蛋')
        self.assertFalse(recipes.state()['wishes'][0]['mine'])
        with tenant_storage.scope('u' + 'b' * 32):
            with self.assertRaises(ValueError):
                self.change('join', code=code)
            self.assertEqual(recipes.state()['wishes'], [])

    def test_shared_owner_cannot_abandon_family_by_switching_kitchens(self):
        code = self.change('invite')['invite']
        with tenant_storage.scope('u' + 'a' * 32):
            self.change('join', code=code)
        with tenant_storage.scope('u' + 'b' * 32):
            other = self.change('invite')['invite']
        with self.assertRaisesRegex(ValueError, '已有家人'):
            self.change('join', code=other)
        self.assertEqual(recipes.state()['account_count'], 2)

    def test_revision_conflict_does_not_lose_family_edits(self):
        old = recipes.state()['revision']
        self.member()
        with self.assertRaises(recipes.ConflictError):
            recipes.handle(dict(action='wish-add', revision=old, name='鱼', member='我'))
        self.assertEqual(recipes.state()['wishes'], [])

    def test_history_is_snapshot_and_repeated_confirmation_is_idempotent(self):
        self.change('plan-generate', members=[], guests=2, recipes=['seed-2'])
        self.change('plan-save')
        self.change('plan-save')
        state = recipes.state()
        self.assertEqual(len(state['history']), 1)
        recipe = next(item for item in state['recipes'] if item['id'] == 'seed-2')
        recipe['name'] = '新的菜名'
        self.change('recipe-save', recipe=recipe)
        self.change('recipe-delete', id='seed-2')
        self.assertEqual(recipes.state()['history'][0]['recipes'][0]['name'], '番茄炒蛋')

    def test_changing_attending_restrictions_invalidates_current_plan(self):
        member = self.member()
        self.change('plan-generate', members=[member['id']], guests=0, recipes=['seed-15'])
        self.change('member-save', id=member['id'], name=member['name'], avoid=['鱼'], portion=1)
        self.assertIsNone(recipes.state()['plan'])

    def test_quick_shortage_respects_restrictions(self):
        member = self.member(avoid=['蛋', '大豆', '猪肉', '鱼', '花生', '蒜', '土豆', '时蔬'])
        try:
            state = self.change('plan-generate', members=[member['id']], guests=0, count=8, quick=True)
        except ValueError as error:
            self.assertIn('没有符合条件', str(error))
        else:
            self.assertLess(len(state['plan']['recipes']), 8)
            self.assertTrue(state['plan']['notice'])
            self.assertTrue(all(recipe['minutes'] <= 20 for recipe in state['plan']['recipes']))

    def test_invalid_inputs_leave_storage_unchanged(self):
        old = recipes.state()
        for payload in [dict(action='member-save', name='孩子', portion=float('nan'), avoid=[]),
                        dict(action='pantry-save', items=[dict(name='大米', quantity=-1, unit='g')]),
                        dict(action='plan-generate', guests=0, members=[]),
                        dict(action='plan-generate', guests=1.5, members=[])]:
            with self.assertRaises(ValueError):
                recipes.handle(dict(revision=old['revision'], **payload))
            self.assertEqual(recipes.state(), old)

    def test_checked_and_feedback_survive_new_connection(self):
        plan = self.change('plan-generate', guests=2, members=[], recipes=['seed-13'])['plan']
        self.change('shopping-check', id=plan['shopping'][0]['id'])
        self.assertTrue(recipes.state()['plan']['shopping'][0]['checked'])
        meal = self.change('plan-save')['history'][0]
        self.change('history-feedback', id=meal['id'], feedback='米饭下次少一点')
        self.assertEqual(recipes.state()['history'][0]['feedback'], '米饭下次少一点')


if __name__ == '__main__':
    unittest.main()
