import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wellness


class TrainingScreenshotsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'training-imports').mkdir()
        self.patches=[patch.object(wellness,'SOURCE',self.root),patch.object(wellness,'BASE',self.root)]
        for p in self.patches:p.start()
        self.day='2026-02-03'
        self.record={'record_id':'elliptical-test','date':self.day,'activity':'椭圆机',
                     'start_local':self.day+' 18:10','end_local':self.day+' 18:29',
                     'duration_seconds':1148,'active_energy_kcal':170,'total_energy_kcal':207,
                     'average_heart_rate_bpm':137,'tiangong_import':{'confirmed':True}}

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def write(self,name,data):
        folder=self.root if name.startswith('健康训练日报_') else self.root/'training-imports'
        (folder/name).write_text(json.dumps(data,ensure_ascii=False))

    def test_latest_revision_and_no_heat_double_count(self):
        self.write('运动_测试_V1.json',dict(self.record,active_energy_kcal=160))
        self.write('运动_测试_V2.json',self.record)
        state=wellness.training()[self.day]
        self.assertEqual(len(state['items']),1)
        self.assertEqual(state['items'][0]['duration_seconds'],1148)
        self.assertEqual(state['items'][0]['kcal'],170)
        self.assertIn('总消耗 207',state['note'])
        self.assertEqual(state,wellness.training()[self.day])

    def test_later_health_export_is_one_session_with_evidence(self):
        self.write('运动_测试_V1.json',self.record)
        self.write('健康训练日报_'+self.day+'_V1.json',{'workouts':[
            {'id':'actual-health-id','name':'椭圆机','start':self.day+' 18:10:05 +0800',
             'end':self.day+' 18:29:13 +0800','duration_seconds':1148,'active_kcal':170.2}]})
        state=wellness.training()[self.day]
        self.assertEqual(len(state['items']),1)
        self.assertEqual(state['items'][0]['source'],'Apple 健康')
        self.assertEqual(state['items'][0]['evidence']['record_id'],'elliptical-test')

    def test_different_session_same_day_is_preserved(self):
        self.write('运动_测试_V1.json',self.record)
        self.write('健康训练日报_'+self.day+'_V1.json',{'workouts':[
            {'id':'morning','name':'椭圆机','start':self.day+' 08:10:00 +0800',
             'end':self.day+' 08:29:08 +0800','duration_seconds':1148,'active_kcal':120}]})
        self.assertEqual(len(wellness.training()[self.day]['items']),2)

    def test_merged_health_cache_keeps_one_record_and_one_summary(self):
        self.test_later_health_export_is_one_session_with_evidence()
        snapshot=wellness.training()
        (self.root/'training-cache.json').write_text(json.dumps(snapshot))
        (self.root/('健康训练日报_'+self.day+'_V1.json')).unlink()
        self.assertEqual(wellness.training(),snapshot)

    def test_strength_placeholder_is_replaced_not_added(self):
        row={'record_id':'strength-test','date':self.day,'title':'力量训练',
             'source_reported':{'duration_minutes':30,'energy_kcal':None,'volume_kg':240},
             'verified_set_count':2,'tiangong_import':{'confirmed':True},
             'exercises':[{'name':'推举','sets':[{'recorded_weight_kg':10,'repetitions':12}]*2}]}
        self.write('训记训练档案_测试_V1.json',row)
        self.write('健康训练日报_'+self.day+'_V1.json',{'workouts':[],'supplemental_training':[row]})
        state=wellness.training()[self.day]
        self.assertEqual(len(state['items']),1)
        self.assertIsNone(state['items'][0]['kcal'])
        self.assertEqual(len(state['moves']),1)
        self.assertIn('10 kg × 12',state['moves'][0])

    def test_unconfirmed_historical_records_stay_out(self):
        self.write('运动_测试_V1.json',dict(self.record,tiangong_import={}))
        self.assertEqual(wellness.training(),{})

    def test_cached_history_survives_when_documents_are_unavailable(self):
        historical={'2026-02-01':{'items':[{'name':'散步','minutes':20}], 'moves':[], 'note':'原记录'}}
        (self.root/'training-cache.json').write_text(json.dumps(historical))
        self.write('运动_测试_V1.json',self.record)
        state=wellness.training()
        self.assertEqual(state['2026-02-01'],historical['2026-02-01'])
        self.assertEqual(len(state[self.day]['items']),1)

    def test_guest_does_not_read_owner_screenshots(self):
        self.write('运动_测试_V1.json',self.record)
        with patch.object(wellness.tenant_storage,'current',return_value='guest-test'):
            self.assertEqual(wellness.training(),{})


if __name__=='__main__':unittest.main()
