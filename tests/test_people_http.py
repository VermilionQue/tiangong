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
import tenant_storage

class PeopleHTTPTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        for p in [patch.object(tenant_storage,'BASE',Path(tmp.name)),patch.object(work_server.nas_backup,'request_backup',return_value=None)]:p.start();self.addCleanup(p.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),work_server.Handler)
        self.server.allowed_hosts={'127.0.0.1:'+str(self.server.server_port)}
        self.server.allowed_networks=[ipaddress.ip_network('127.0.0.0/8')]
        self.server.ingress_secret='synthetic-test-secret';self.server.public_origin='https://test.invalid'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def req(self,path='/api/people',body=None,user='owner',origin=True):
        method='POST' if body is not None else 'GET'
        headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:'+str(self.server.server_port)}
        if user:headers.update(tenant_storage.headers(self.server.ingress_secret,user,method,path))
        if not origin:headers['Origin']='https://evil.invalid'
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
        r=c.getresponse();raw=r.read();result=(r.status,dict(r.headers),raw);c.close();return result
    def test_identity_is_enforced_on_lists_and_exports(self):
        self.assertEqual(self.req(user=None)[0],401)
        code,_,raw=self.req(body={'name':'合成人物'});self.assertEqual(code,200)
        entry=json.loads(raw)['record']
        code,_,raw=self.req(user='u'+'b'*32);self.assertEqual(json.loads(raw)['people'],[])
        self.assertEqual(self.req(body={**entry,'name':'越权'},user='u'+'b'*32)[0],409)
        self.assertEqual(self.req('/api/people/export',user=None)[0],401)
        code,headers,raw=self.req('/api/people/export');self.assertEqual(code,200)
        self.assertTrue(raw.startswith(b'PK'));self.assertIn('no-store',headers['Cache-Control'])
    def test_read_only_demo_and_csrf_and_conflict(self):
        self.assertEqual(self.req(body={'name':'测试'},user='demo')[0],403)
        self.assertEqual(self.req(body={'name':'测试'},origin=False)[0],403)
        entry=json.loads(self.req(body={'name':'测试'})[2])['record']
        self.assertEqual(self.req(body={**entry,'name':'新版'})[0],200)
        self.assertEqual(self.req(body={**entry,'name':'旧版'})[0],409)

if __name__=='__main__':unittest.main()
