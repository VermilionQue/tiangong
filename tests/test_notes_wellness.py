# -*- coding: utf-8 -*-
import json, sqlite3, sys, tempfile, threading, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import notes, wellness
class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.orig=(notes.BASE,wellness.BASE);notes.BASE=Path(self.temp.name)/'notes';wellness.BASE=Path(self.temp.name)/'health'
    def tearDown(self):notes.BASE,wellness.BASE=self.orig;self.temp.cleanup()
    def test_sanitize_and_revision(self):
        r=notes.save({'html':'<p>#测试 内容 <strong>强调</strong><script>alert(1)</script><a href="javascript:alert(1)">链接</a></p>'})
        n=notes.all_notes()[0];self.assertNotIn('script',n['html']);self.assertNotIn('javascript:',n['html']);self.assertIn('<strong>',n['html']);self.assertEqual(n['tags'],['测试'])
        notes.save({'id':r['id'],'revision':1,'html':'<p>更新</p>'})
        with self.assertRaises(ValueError):notes.save({'id':r['id'],'revision':1,'html':'<p>旧版覆盖</p>'})
    def test_import_dedupe(self):
        folder=Path(self.temp.name)/'export';folder.mkdir();(folder/'memo.html').write_text('<div class="memo"><div class="time">2026-09-01 09:00:00</div><div class="content"><p>#标签 中文</p></div></div>')
        self.assertEqual(notes.import_export(folder)['added'],1);self.assertEqual(notes.import_export(folder)['skipped'],1)
    def test_delete_restore_preserves_attachment_and_history(self):
        folder=Path(self.temp.name)/'export';(folder/'file').mkdir(parents=True)
        (folder/'file'/'picture.png').write_bytes(b'attachment-original')
        (folder/'memo.html').write_text('<div class="memo"><div class="time">2026-09-01 09:00:00</div><div class="content"><p>#未来的家/读书 中文</p><img src="file/picture.png"></div></div>')
        notes.import_export(folder);original=notes.all_notes()[0]
        deleted=notes.save({'id':original['id'],'revision':1,'action':'delete'})
        self.assertEqual(notes.all_notes(),[]);self.assertEqual(deleted['revision'],2)
        self.assertEqual(deleted['deleted'],1);self.assertTrue(deleted['deleted_at'])
        self.assertEqual(notes.all_notes(include_deleted=True)[0]['attachments'],original['attachments'])
        self.assertEqual(notes.asset(original['attachments'][0]['key']).read_bytes(),b'attachment-original')
        self.assertEqual(notes.import_export(folder)['skipped'],1)
        self.assertEqual(notes.all_notes(),[])  # Re-import must not resurrect a deleted note.
        with self.assertRaises(ValueError):notes.save({'id':original['id'],'revision':1,'html':'<p>stale edit</p>'})
        with self.assertRaises(ValueError):notes.save({'id':original['id'],'revision':1,'action':'restore'})
        restored=notes.save({'id':original['id'],'revision':2,'action':'restore'})
        self.assertEqual(restored['revision'],3);self.assertEqual(restored['deleted'],0);self.assertIsNone(restored['deleted_at'])
        for field in ('id','created','html','text','tags','attachments','source'):self.assertEqual(restored[field],original[field])
        with notes.con() as c:history=[json.loads(row['data']) for row in c.execute('SELECT * FROM history ORDER BY revision')]
        self.assertEqual([row['revision'] for row in history],[1,2]);self.assertEqual([row['deleted'] for row in history],[0,1])
    def test_delete_checks_revision_and_does_not_create_missing_note(self):
        original=notes.save({'html':'<p>原笔记</p>'})
        current=notes.save({'id':original['id'],'revision':1,'html':'<p>新内容</p>'})
        for revision in (1,None,True,'2'):
            with self.assertRaises(ValueError):notes.save({'id':original['id'],'revision':revision,'action':'delete'})
        self.assertEqual(notes.all_notes()[0],current)
        with self.assertRaises(ValueError):notes.save({'id':'missing','revision':1,'action':'delete'})
        with self.assertRaises(ValueError):notes.save({'id':'missing','revision':1,'html':'<p>丢失的记录不能自动重建</p>'})
        for invalid in ([],None,{'action':'erase'},{'id':{},'html':'<p>无效编号</p>'}):
            with self.assertRaises(ValueError):notes.save(invalid)
        self.assertEqual(len(notes.all_notes()),1)
    def test_repeated_delete_and_restore_never_repeat_history_or_overwrite(self):
        original=notes.save({'html':'<p>重复请求验证</p>'})
        deleted=notes.save({'id':original['id'],'revision':1,'action':'delete'})
        with self.assertRaises(ValueError):notes.save({'id':original['id'],'revision':1,'action':'delete'})
        unchanged=notes.save({'id':original['id'],'revision':2,'action':'delete'})
        self.assertEqual(unchanged,deleted)
        restored=notes.save({'id':original['id'],'revision':2,'action':'restore'})
        with self.assertRaises(ValueError):notes.save({'id':original['id'],'revision':2,'action':'delete'})
        self.assertEqual(notes.save({'id':original['id'],'revision':3,'action':'restore'}),restored)
        with notes.con() as c:self.assertEqual(c.execute('SELECT count(*) FROM history').fetchone()[0],2)
        self.assertEqual(notes.all_notes()[0],restored)
    def test_concurrent_delete_and_edit_preserve_one_winner(self):
        original=notes.save({'html':'<p>原内容</p>'});barrier=threading.Barrier(2)
        def write(change):
            barrier.wait()
            try:return notes.save({'id':original['id'],'revision':1,**change})
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(write,[{'action':'delete'},{'html':'<p>修改内容</p>'}]))
        self.assertEqual(sum(result is not None for result in outcomes),1)
        latest=notes.all_notes(include_deleted=True)[0]
        self.assertEqual(latest['revision'],2)
        self.assertEqual(latest,next(result for result in outcomes if result is not None))
        with notes.con() as c:self.assertEqual(c.execute('SELECT count(*) FROM history').fetchone()[0],1)
    def test_legacy_schema_migration_retains_data(self):
        notes.BASE.mkdir()
        with sqlite3.connect(notes.BASE/'notes.sqlite3') as c:
            c.execute('CREATE TABLE notes(id TEXT PRIMARY KEY,created TEXT,updated TEXT,html TEXT,text TEXT,tags TEXT,attachments TEXT,source TEXT,revision INTEGER)')
            c.execute('INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)',('legacy','2026-09-01 09:00:00','2026-09-01 09:00:00','<p>#旧笔记</p>','#旧笔记','["旧笔记"]','[]','flomo',4))
        migrated=notes.all_notes()[0]
        self.assertEqual(migrated['revision'],4);self.assertEqual(migrated['deleted'],0);self.assertEqual(migrated['tags'],['旧笔记'])
        notes.save({'id':'legacy','revision':4,'action':'delete'})
        self.assertEqual(notes.all_notes(),[])
        notes.save({'id':'legacy','revision':5,'action':'restore'})
        self.assertEqual(notes.all_notes()[0]['html'],'<p>#旧笔记</p>')
    def test_hierarchical_tags_and_unsafe_import_links(self):
        row=notes.save({'html':'<p>#未来的家 #未来的家/读书 <a href="javascript:alert(1)">#书摘</a></p><img src=x onerror=alert(1)>'})
        self.assertEqual(row['tags'],['书摘','未来的家','未来的家/读书'])
        self.assertNotIn('javascript:',row['html']);self.assertNotIn('onerror',row['html'])
    def test_calendar_month_and_once_reminder(self):
        self.assertEqual(wellness.next_month('2026-01-31'),'2026-02-28')
        wellness.save_report({'kind':'体测','date':'2026-09-17','title':'测试','weight':70})
        self.assertIsNone(wellness.due_reminder('2026-10-17'))
        reminder=wellness.due_reminder('2026-11-01')
        self.assertEqual(reminder['id'],'body-assessment:2026-11')
        wellness.mark_reminded(reminder['id']);self.assertIsNone(wellness.due_reminder('2026-11-01'))
    def test_future_report_and_attachment_traversal(self):
        with self.assertRaises(ValueError):wellness.save_report({'kind':'体测','date':'2099-01-01','title':'未来'})
        with self.assertRaises(ValueError):notes.asset('../../secrets.png')
if __name__=='__main__':unittest.main()
