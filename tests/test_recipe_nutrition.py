import copy
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import recipe_advice
import recipe_health
import recipe_nutrition as nutrition
import recipes
import tenant_storage
import wellness
import test_recipes as baseline


class NutritionTests(unittest.TestCase):
    setUp = baseline.RecipeTests.setUp
    change = baseline.RecipeTests.change
    member = baseline.RecipeTests.member

    def plan(self, name='大米', grams=100):
        recipe = copy.deepcopy(recipes.initial()['recipes'][0])
        recipe.update(servings=1, ingredients=[dict(name=name, quantity=grams, unit='g')], seasonings=[])
        return dict(equivalent=1, recipes=[recipe], date=nutrition.today().isoformat(), members=[], guests=1)

    def test_reference_food_and_portion_math(self):
        plan = self.plan()
        result = nutrition.estimate(plan)
        ref = nutrition.table()[1]['大米']['per100g']
        self.assertEqual(result['per_serving'], {k: round(v, 1) for k, v in ref.items()})
        plan['equivalent'] = 2.5
        scaled = nutrition.estimate(plan)
        self.assertEqual(scaled['per_serving'], result['per_serving'])
        self.assertEqual(scaled['total']['protein'], round(ref['protein'] * 2.5, 1))

    def test_uncounted_and_unmatched_are_unknown_instead_of_zero(self):
        result = nutrition.estimate(self.plan('紫菜', None))
        self.assertIsNone(result['per_serving']['fiber'])
        self.assertTrue(result['partial'])
        plan = self.plan()
        plan['recipes'][0]['seasonings'] = [dict(name='食用油', quantity=None, unit='g')]
        result = nutrition.estimate(plan)
        self.assertTrue(result['partial'])
        self.assertIn('食用油：未填用量', result['missing'])

    def test_cooked_and_raw_grains_are_distinct(self):
        raw = nutrition.estimate(self.plan())
        cooked = nutrition.estimate(self.plan('熟米饭'))
        self.assertGreater(raw['per_serving']['carbs'], cooked['per_serving']['carbs'] * 2)
        self.assertFalse(nutrition.estimate(self.plan('糙米'))['partial'])

    def test_manual_nutrients_are_complete_validated_and_scaled(self):
        plan = self.plan('未收录食材')
        values = dict(carbs=20, protein=10, fat=5, fiber=3)
        plan['recipes'][0]['nutrition_per_serving'] = values
        plan['equivalent'] = 2
        result = nutrition.estimate(plan)
        self.assertEqual(result['total']['carbs'], 40)
        self.assertEqual(result['per_serving'], values)
        self.assertFalse(result['partial'])
        self.assertEqual(recipes.validate_recipe(plan['recipes'][0])['nutrition_per_serving'], values)
        plan['recipes'][0]['nutrition_per_serving'] = dict(carbs=20)
        with self.assertRaises(ValueError): recipes.validate_recipe(plan['recipes'][0])

    def test_targets_unset_and_inputs_are_not_a_prescription(self):
        self.assertEqual(recipes.state()['nutrition_targets'], dict.fromkeys(nutrition.KEYS))
        values = dict(carbs=248, protein=90, fat=50, fiber=25)
        self.assertEqual(self.change('nutrition-targets', targets=values)['nutrition_targets'], values)
        for value in [0, -2, 'NaN', True]:
            with self.assertRaises(ValueError): self.change('nutrition-targets', targets=dict(values, fiber=value))

    def test_only_confirmed_recent_records_drive_trends(self):
        data = recipes.initial()
        for days, consumed in [(0, True), (1, True), (2, True), (3, False), (7, True), (-1, True)]:
            meal = self.plan(); meal.update(date=(nutrition.today() - timedelta(days=days)).isoformat(), consumed=consumed)
            data['history'].append(meal)
        review = nutrition.review(data)
        self.assertEqual(review['meal_count'], 3)
        self.assertEqual(review['refined_meals'], 3)
        self.assertIn('糙米', review['observations'][0])
        data['history'][0] = dict(self.plan('糙米'), consumed=True)
        self.assertEqual(nutrition.review(data)['whole_meals'], 1)

    def test_all_unknown_daily_values_stay_unknown(self):
        data = recipes.initial(); data['history'] = [dict(self.plan('未知'), consumed=True)]
        self.assertIsNone(nutrition.review(data)['daily'][0]['per_serving']['carbs'])

    def test_confirmation_snapshots_and_future_guard(self):
        self.change('plan-generate', guests=1, recipes=['seed-13'], meal_type='lunch')
        saved = self.change('plan-save'); meal = saved['history'][0]
        self.assertEqual(saved['nutrition_review']['meal_count'], 0)
        result = self.change('history-consumed', id=meal['id'], consumed=True)
        self.assertEqual(result['nutrition_review']['meal_count'], 1)
        self.assertEqual(result['nutrition_review']['daily'][0]['slots'], ['lunch'])
        self.assertEqual(self.change('history-consumed', id=meal['id'], consumed=False)['nutrition_review']['meal_count'], 0)
        self.change('plan-generate', guests=1, date=(nutrition.today()+timedelta(days=1)).isoformat())
        future = self.change('plan-save')['history'][0]
        with self.assertRaises(ValueError): self.change('history-consumed', id=future['id'], consumed=True)

    def test_opt_in_health_reads_only_linked_account_and_latest_measurements(self):
        wellness.save_report(dict(kind='体测', date=nutrition.today().isoformat(), title='本人测量', height=175, weight=80))
        self.change('health-profile-save', conditions=['脂肪肝'], habits='常喝甜饮', life_stage='adult')
        member = self.member('我')
        self.assertIsNone(recipes.state()['members'][0]['health'])
        linked = self.change('health-link', id=member['id'])
        self.assertEqual(linked['members'][0]['health']['weight_kg']['value'], 80)
        self.assertNotIn('health_account', linked['members'][0])
        self.change('plan-generate', guests=0, members=[member['id']])
        with recipes.connect() as connection:
            facts = recipe_advice.facts(json.loads(recipes.kitchen(connection)['data']))
        self.assertEqual(facts['shared_health'][0]['profile']['conditions'], ['脂肪肝'])
        self.assertNotIn('attachments', json.dumps(facts))
        code = self.change('invite')['invite']
        with tenant_storage.scope('u'+'a'*32):
            self.change('join', code=code)
            self.assertEqual(recipes.state()['members'][0]['health']['height_cm']['value'], 175)
            self.assertEqual(recipes.state()['my_nutrition_health']['conditions'], [])
            with self.assertRaises(PermissionError): self.change('health-link', id=member['id'])
        self.change('health-unlink', id=member['id'])
        self.assertIsNone(recipes.state()['members'][0]['health'])

    def test_health_snapshot_does_not_leak_account_ids_and_unlink_clears_analysis(self):
        member = self.member()
        self.change('health-link', id=member['id'])
        self.change('plan-generate', guests=0, members=[member['id']])
        self.assertNotIn('health_account', recipes.state()['plan']['members'][0])
        self.change('plan-save')
        self.assertNotIn('health_account', recipes.state()['history'][0]['members'][0])
        with patch.object(recipe_advice.threading, 'Thread'):
            self.change('analysis-request')
        self.assertIsNotNone(recipes.state()['nutrition_advice'])
        self.change('health-unlink', id=member['id'])
        self.assertIsNone(recipes.state()['nutrition_advice'])

    def test_analysis_is_explicit_and_same_facts_are_deduplicated(self):
        self.change('plan-generate', guests=1)
        with patch.object(recipe_advice.threading, 'Thread') as thread:
            recipes.state(); self.assertEqual(thread.call_count, 0)
            first = self.change('analysis-request')
            self.assertEqual(first['nutrition_advice']['status'], 'pending')
            self.change('analysis-request'); self.assertEqual(thread.call_count, 1)
        with recipes.connect() as connection:
            row = connection.execute('SELECT * FROM kitchen_analyses').fetchone()
            kitchen, run_id = row['kitchen'], row['run_id']
        with patch.object(recipe_advice, 'call_shanjun', return_value='山君原话：可以把部分白米换成糙米。'):
            recipe_advice.worker(kitchen, run_id, 'test')
        self.assertEqual(recipes.state()['nutrition_advice']['status'], 'completed')
        self.assertEqual(recipes.state()['nutrition_advice']['result'], '山君原话：可以把部分白米换成糙米。')
        with tenant_storage.scope('u'+'b'*32): self.assertIsNone(recipes.state()['nutrition_advice'])


if __name__ == '__main__': unittest.main()
