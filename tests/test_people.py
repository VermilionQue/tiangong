"""Private memories: isolation, evidence, reversible changes and portable export."""
import base64
from datetime import date
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import people
import tenant_storage
import private_images


class PeopleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='tiangong-people-test-')
        self.addCleanup(tmp.cleanup)
        for target, field, value in [(tenant_storage, 'BASE', Path(tmp.name))]:
            p = patch.object(target, field, value);p.start();self.addCleanup(p.stop)
        p = patch.object(people, 'today', return_value=date(2026, 10, 25))
        p.start();self.addCleanup(p.stop)

    def person(self, **kw):
        return people.save({'name':'合成人物', **kw})

    def moment(self, person, **kw):
        return people.save({'kind':'moment','title':'测试共同经历',
                            'people_ids':[person['id']], **kw})

    def test_empty_evidence_stays_unknown_and_manual_level_wins(self):
        p = self.person(met_on='2001', affinity=5, pressure=1, meet_per_month=0)
        view=people.state()['people'][0]
        self.assertIsNone(view['reference']['score'])
        self.assertIn('聊天频率',view['reference']['missing'])
        people.save({**p,'level':'知心'})
        self.assertEqual(people.state()['people'][0]['display_level'],'知心')

    def test_explicit_zero_frequency_is_evidence(self):
        self.person(met_on='2001',affinity=5,pressure=1,meet_per_month=0,chat_per_week=0)
        ref=people.state()['people'][0]['reference']
        self.assertEqual(ref['score'],60)
        self.assertEqual(ref['missing'],[])

    def test_partial_dates_preserve_precision_and_do_not_invent_contact(self):
        p=self.person(met_on='2014',reminder_days=30)
        self.moment(p,happened_on='2026-10',event_type='聊天')
        view=people.state()['people'][0]
        self.assertEqual(view['met_on'],'2014')
        self.assertEqual(view['last_contact'],'')
        self.assertEqual(view['reminder_due_on'],'2026-11-24')
        with self.assertRaises(ValueError):self.moment(p,happened_on='2026-02-30')
        with self.assertRaises(ValueError):self.person(last_contact_on='2026-10-26')

    def test_shared_moment_updates_both_contacts_and_is_reversible(self):
        a=self.person();b=self.person(name='同行者')
        m=self.moment(a,people_ids=[a['id'],b['id']],happened_on='2026-10-20',event_type='见面')
        self.assertEqual({p['last_contact'] for p in people.state()['people']},{'2026-10-20'})
        removed=people.save({**m,'action':'delete'})
        self.assertEqual({p['last_contact'] for p in people.state()['people']},{''})
        people.save({**removed,'action':'restore'})
        self.assertEqual({p['last_contact'] for p in people.state()['people']},{'2026-10-20'})

    def test_reminders_are_opt_in_snoozable_and_no_duplicate_schedule(self):
        self.person(last_contact_on='2026-01-01')
        p=self.person(name='开启提醒',last_contact_on='2026-10-01',reminder_days=20)
        self.assertEqual(len(people.state()['reminders']),1)
        p=people.save({**p,'action':'snooze','days':7})
        self.assertEqual(people.state()['reminders'],[])
        self.assertEqual(len(people.state(date(2026,11,1))['reminders']),1)
        people.save({**p,'action':'delete'})
        self.assertEqual(people.state(date(2026,11,1))['reminders'],[])

    def test_revision_conflict_does_not_overwrite_and_history_kept(self):
        p=self.person(bond='最初的记忆')
        updated=people.save({**p,'bond':'新的记忆'})
        with self.assertRaises(people.ConflictError):people.save({**p,'bond':'过时的窗口'})
        self.assertEqual(people.state()['people'][0]['bond'],'新的记忆')
        with people.connection() as con:
            original=json.loads(con.execute('SELECT data FROM history').fetchone()[0])
        self.assertEqual(original['bond'],'最初的记忆')
        self.assertEqual(updated['revision'],2)

    def test_person_and_moment_ids_cannot_cross_users(self):
        p=self.person()
        with tenant_storage.scope('u'+'b'*32):
            self.assertEqual(people.state()['people'],[])
            with self.assertRaises(ValueError):self.moment(p)
            with self.assertRaises(people.ConflictError):people.save({**p,'name':'越权覆盖'})
        self.assertEqual(people.state()['people'][0]['name'],'合成人物')

    def test_archive_includes_markdown_backlinks_and_deleted_record_images(self):
        from PIL import Image
        stream=io.BytesIO();Image.new('RGB',(8,8),'blue').save(stream,format='PNG')
        image=private_images.store('people','data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
        a=self.person(photos=[image['id']],bond='一起走过的路');b=self.person(name='同行者')
        self.moment(a,people_ids=[a['id'],b['id']],happened_on='2020',body='共同回忆')
        people.save({**b,'action':'delete'})
        with zipfile.ZipFile(io.BytesIO(people.archive())) as z:
            text=z.read('people/'+a['id']+'.md').decode()
            self.assertIn('一起走过的路',text)
            self.assertIn('[['+b['id']+'|同行者]]',text)
            self.assertIn('photos/'+image['id'],z.namelist())
            self.assertTrue(any(p['deleted'] for p in json.loads(z.read('people.json'))['people']))
        with tenant_storage.scope('u'+'b'*32):
            with self.assertRaises(ValueError):self.person(photos=[image['id']])


class PeoplePurgeTest(PeopleTest):
    def image(self, color):
        from PIL import Image
        stream=io.BytesIO();Image.new('RGB',(8,8),color).save(stream,format='PNG')
        return private_images.store('people','data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())['id']

    def test_hard_delete_erases_records_history_exclusive_photos_and_exports(self):
        unique=self.image('red');shared=self.image('blue')
        a=self.person(name='ERASE-ME-PRIVATE',photos=[unique,shared]);b=self.person(name='保留人物',photos=[shared])
        m=self.moment(a,people_ids=[a['id'],b['id']],body='ERASE-SHARED-MEMORY')
        # Even an older revision of a now-unlinked memory must not retain details.
        people.save({**m,'people_ids':[b['id']],'body':'编辑后已不再关联'})
        keep=self.moment(b,body='独立经历应该保留')
        trashed=people.save({**a,'action':'delete'})
        result=people.save({**trashed,'action':'purge'})
        self.assertTrue(result['purged']);self.assertEqual(result['removed_records'],2)
        state=people.state();self.assertEqual([p['id'] for p in state['people']],[b['id']])
        self.assertEqual([p['id'] for p in state['moments']],[keep['id']])
        with people.connection() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM history WHERE id IN (?,?)',(a['id'],m['id'])).fetchone()[0],0)
        with self.assertRaises(ValueError):private_images.resolve('people',unique)
        self.assertTrue(private_images.resolve('people',shared).is_file())
        self.assertNotIn(b'ERASE-ME-PRIVATE',Path(str(people.DB)).read_bytes())
        with zipfile.ZipFile(io.BytesIO(people.archive())) as z:
            self.assertNotIn(a['id'],z.read('people.json').decode())
            self.assertNotIn('photos/'+unique,z.namelist())

    def test_purge_requires_trash_current_revision_and_same_tenant(self):
        p=self.person()
        with self.assertRaises(ValueError):people.save({**p,'action':'purge'})
        deleted=people.save({**p,'action':'delete'})
        with self.assertRaises(people.ConflictError):people.save({**p,'action':'purge'})
        with tenant_storage.scope('u'+'c'*32):
            with self.assertRaises((ValueError,people.ConflictError)):people.save({**deleted,'action':'purge'})
        self.assertEqual(len(people.state()['people']),1)
        people.save({**deleted,'action':'purge'})
        self.assertEqual(people.state()['people'],[])

    def test_purging_one_trashed_moment_keeps_person(self):
        p=self.person();m=self.moment(p);m=people.save({**m,'action':'delete'})
        people.save({**m,'action':'purge'})
        self.assertEqual(len(people.state()['people']),1);self.assertEqual(people.state()['moments'],[])

if __name__=='__main__':unittest.main()
