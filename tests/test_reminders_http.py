"""Sync controls use isolated state and mocked native calls, never real reminders."""
import http.client
from http.server import ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import work_server

class RemindersHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.patch=patch.object(work_server.reminders_sync,'DIR',Path(self.tmp.name));self.patch.start();self.addCleanup(self.patch.stop)
        self.backup=patch.object(work_server.nas_backup,'request_backup');self.backup_mock=self.backup.start();self.addCleanup(self.backup.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),work_server.Handler)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.server.allowed_hosts={self.origin.removeprefix('http://')}
        self.server.allowed_networks=[ipaddress.ip_network('127.0.0.0/8')];self.server.lan=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def call(self,data=None,origin=True):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=self.origin
        c.request('POST' if data is not None else 'GET','/api/todos/sync',json.dumps(data) if data is not None else None,headers)
        r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
    def test_read_does_not_request_native_access(self):
        with patch.object(work_server.reminders_sync,'native') as native:
            code,body=self.call();self.assertEqual(code,200);self.assertFalse(body['enabled']);native.assert_not_called()
    def test_same_origin_required_before_sync_or_resolution(self):
        with patch.object(work_server.reminders_sync,'wake') as wake,patch.object(work_server.reminders_sync,'resolve') as resolve:
            self.assertEqual(self.call({'action':'sync'},origin=False)[0],403)
            self.assertEqual(self.call({'action':'resolve','todo_id':'one','choice':'apple','token':'old'},origin=False)[0],403)
            wake.assert_not_called();resolve.assert_not_called()
    def test_sync_and_resolution_preserve_choices_and_tokens(self):
        with patch.object(work_server.reminders_sync,'wake') as wake,patch.object(work_server.reminders_sync,'resolve') as resolve:
            self.assertEqual(self.call({'action':'sync'})[0],200);wake.assert_called_once_with()
            self.assertEqual(self.call({'action':'resolve','todo_id':'one','choice':'apple','token':'snapshot-token'})[0],200)
            resolve.assert_called_once_with('one','apple','snapshot-token')
            self.backup_mock.assert_called_once_with()
    def test_invalid_action_and_stale_choice_return_errors(self):
        self.assertEqual(self.call({'action':'enable','helper_path':'untrusted'})[0],400)
        with patch.object(work_server.reminders_sync,'resolve',side_effect=ValueError('内容已经更新，请刷新')):
            code,body=self.call({'action':'resolve','todo_id':'one','choice':'local','token':'stale'})
            self.assertEqual(code,400);self.assertIn('刷新',body['error'])

if __name__=='__main__':unittest.main()
