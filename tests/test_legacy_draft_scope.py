import json,tempfile,threading,unittest
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from studio.server import Conflict
from studio.usable_store import Storage
from studio.usable_graph import template
from studio.usable_server import handler

class HTTPFixture(unittest.TestCase):
 def start_server(self,server):
  self.server=server;self.thread=threading.Thread(target=server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
 def api(self,path,data=None):
  req=Request('http://127.0.0.1:'+str(self.server.server_port)+path,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
  try:
   with urlopen(req,timeout=15) as r:return r.status,json.load(r)
  except HTTPError as e:return e.code,json.load(e)

class ScopedLegacyDraftTests(HTTPFixture):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Storage(self.tmp.name)
  def workspace(path):
   w=dict(id=path.rsplit('/',1)[-1],path=path);self.db.put('workspace',w['id'],w);return w
  self.rt=SimpleNamespace(session_only=True,db=self.db,candidate_sha256='synthetic',operator={},profiles=lambda:[],check_operator=lambda:None,open_workspace=workspace)
  self.token='SYNTHETIC_TOKEN';self.start_server(ThreadingHTTPServer(('127.0.0.1',0),handler(self.rt,self.token)))
  self.a=self.api('/api/workspace',{'path':'/SYNTHETIC/A'})[1];self.b=self.api('/api/workspace',{'path':'/SYNTHETIC/B'})[1]
 def test_http_scope_filters_task_and_refuses_cross_workspace_cas_or_omission(self):
  task=dict(title='SYNTHETIC',body='A_ONLY_MARKER',scope=[],acceptance='',issue_url='')
  request=dict(workspace=self.a['id'],document=template('blank'),id=None,expected=None,task=task)
  code,saved=self.api('/api/draft',request);self.assertEqual(code,200);self.assertEqual(saved['workspace'],'A')
  self.assertIn('A_ONLY_MARKER',json.dumps(self.api('/api/status?workspace=A')[1]))
  for url in ['/api/status?workspace=B','/api/status']:
   code,v=self.api(url);self.assertEqual(code,200);self.assertNotIn('A_ONLY_MARKER',json.dumps(v));self.assertEqual(v['drafts'],[])
  code,_=self.api('/api/draft',dict(request,workspace='B',id=saved['id'],expected=saved['revision']));self.assertEqual(code,409)
  request.pop('workspace');self.assertNotEqual(self.api('/api/draft',request)[0],200)
  self.assertEqual(self.db.get('draft',saved['id']),saved)
 def test_unassigned_legacy_draft_is_not_silently_adopted_or_exposed(self):
  legacy=self.db.save_draft(template('blank'),task=dict(title='LEGACY',body='UNASSIGNED',scope=[],acceptance='',issue_url=''))
  self.assertEqual(self.api('/api/status?workspace=A')[1]['drafts'],[])
  with self.assertRaises(Conflict):self.db.save_draft(template('blank'),legacy['id'],legacy['revision'],workspace='A')

 def test_null_empty_and_nonstring_workspace_cannot_mutate_legacy_record(self):
  legacy=self.db.save_draft(template('blank'),task=dict(title='LEGACY',body='UNCHANGED',scope=[],acceptance='',issue_url=''))
  for workspace in [None,'','  ',False,0,[],{}]:
   with self.subTest(workspace=workspace):
    code,_=self.api('/api/draft',dict(workspace=workspace,document=legacy['document'],id=legacy['id'],expected=legacy['revision'],task=dict(legacy['task'],body='MUST_NOT_SAVE')))
    self.assertEqual(code,400);self.assertEqual(self.db.get('draft',legacy['id']),legacy)

