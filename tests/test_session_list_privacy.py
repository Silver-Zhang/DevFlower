"""Display-only privacy projections; synthetic data, no native model execution."""
import copy,json,threading,unittest
from urllib.request import Request,urlopen
from test_workbench_runtime import fixture,structured_session
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_server import create_server

class SessionListPrivacyTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.path,self.native=structured_session(self.base,self.rt,'synthetic-list-privacy','codex');self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt)
 def poison(self):
  run=next(x for x in self.rt.flow.list('run') if x['id']=='synthetic-list-privacy-run');run['graph']['name']='工作流 HTTPS://private.example.invalid/workflow/WORKFLOW_MARKER'
  self.rt.flow.change('run',run['id'],dict(graph=run['graph'],task='任务 https://private.example.invalid/task/TASK_MARKER'))
  for activation in self.rt.flow.list('activation'):
   if activation.get('run')==run['id']:self.rt.flow.change('activation',activation['id'],dict(name='节点 https://private.example.invalid/node/NODE_MARKER'))
  return run['id']
 def row(self,view='active'):
  return next(x for x in self.manager.listing(self.w,view)['sessions'] if x['handle']==self.native['handle'])
 def assert_private_display(self,row):
  for key in ['title','workflow','node_name','task_summary','note']:
   with self.subTest(field=key):
    text=row.get(key) or '';self.assertNotIn('private.example.invalid',text);self.assertNotIn('WORKFLOW_MARKER',text);self.assertNotIn('NODE_MARKER',text);self.assertNotIn('TASK_MARKER',text)
 def test_native_all_list_labels_hide_urls_without_changing_source(self):
  run=self.poison();raw=self.path.read_bytes();before_run=copy.deepcopy(self.rt.flow.get('run',run));before_activations=copy.deepcopy(self.rt.flow.list('activation'));row=self.row();self.assert_private_display(row)
  self.assertEqual(self.path.read_bytes(),raw);self.assertEqual(self.rt.flow.get('run',run),before_run);self.assertEqual(self.rt.flow.list('activation'),before_activations);self.assertEqual(row['workflow_id'],run);self.assertEqual(row['handle'],self.native['handle'])
 def test_alias_is_sanitized_only_in_projection(self):
  self.poison();alias='别名 https://private.example.invalid/alias/ALIAS_MARKER';value=dict(id=self.native['handle'],workspace=self.w,collection='active',alias=alias);self.rt.flow.put('session-meta',self.native['handle'],value);row=self.row();self.assert_private_display(row);self.assertNotIn('ALIAS_MARKER',row['title']);self.assertEqual(self.rt.flow.get('session-meta',self.native['handle']),value)
 def test_all_collections_apply_same_projection(self):
  self.poison()
  for collection in ['active','archived','trash','hidden']:
   self.rt.flow.put('session-meta',self.native['handle'],dict(id=self.native['handle'],workspace=self.w,collection=collection));self.assert_private_display(self.row(collection))
 def test_saved_copy_list_labels_are_sanitized_without_rewriting_archive(self):
  self.poison();row=self.row();row.update(handle='synthetic-saved-handle',title='保存 https://private.example.invalid/title',workflow='工作流 https://private.example.invalid/workflow/WORKFLOW_MARKER',node_name='节点 https://private.example.invalid/node/NODE_MARKER',task_summary='任务 https://private.example.invalid/task/TASK_MARKER',note='备注 HTTPS://private.example.invalid/note')
  files=self.rt.project_files(self.w);files.save_archive('session','synthetic-copy',dict(session=row,messages=[]));before={p.name:p.read_bytes() for p in files.root.glob('session-*.json')};result=next(x for x in self.manager.listing(self.w)['sessions'] if x.get('saved_history'));self.assert_private_display(result);self.assertFalse(result['choices']['resume']);self.assertEqual({p.name:p.read_bytes() for p in files.root.glob('session-*.json')},before)
 def test_normal_names_identity_and_grouping_remain_intact(self):
  run=self.poison();record=self.rt.flow.get('run',run);record['graph']['name']='研究流程 · α';self.rt.flow.change('run',run,dict(graph=record['graph']));
  for a in self.rt.flow.list('activation'):
   if a.get('run')==run:self.rt.flow.change('activation',a['id'],dict(name='独立检查 · β'))
  row=self.row();self.assertEqual(row['workflow'],'研究流程 · α');self.assertEqual(row['node_name'],'独立检查 · β');self.assertEqual(row['workflow_id'],run);self.assertEqual(row['session_id'],self.native['session_id'])
 def test_url_variants_and_credential_syntax_share_the_display_edge(self):
  from studio.workbench_sessions import _list_projection
  for text in ['前 HTTPS://private.example.invalid/TOKEN 后','前 https://private.example.invalid/a?token=UNIQUE_TOKEN 后','前 https://private.example.invalid/a?x=\'UNIQUE_TOKEN\' 后','前 https://private.example.invalid/'+'x'*200+'TOKEN 后','前\nhttps://private.example.invalid/TOKEN\n后']:
   original={key:text for key in ['title','workflow','node_name','task_summary','note']};original.update(handle='opaque-exact',workflow_group_id='draft:opaque-exact',node='opaque-node')
   before=copy.deepcopy(original);shown=_list_projection(original)
   for key in ['title','workflow','node_name','task_summary','note']:
    self.assertNotIn('private.example.invalid',shown[key]);self.assertNotIn('TOKEN',shown[key]);self.assertIn('[链接已隐藏]',shown[key])
   self.assertEqual(original,before);self.assertEqual(shown['handle'],original['handle']);self.assertEqual(shown['workflow_group_id'],original['workflow_group_id']);self.assertEqual(shown['node'],original['node'])
  shown=_list_projection(dict(workflow='工作流 token=SYNTHETIC_SECRET_VALUE',node_name='节点 bearer SYNTHETIC_BEARER_VALUE'))
  self.assertNotIn('SYNTHETIC_SECRET_VALUE',shown['workflow']);self.assertNotIn('SYNTHETIC_BEARER_VALUE',shown['node_name'])
 def test_real_http_list_response_uses_display_projection(self):
  self.poison();server,token=create_server(self.rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/sessions?workspace='+self.w,headers={'Authorization':'Bearer '+token})
   with urlopen(req,timeout=10) as response:rows=json.load(response)['sessions']
   self.assert_private_display(next(x for x in rows if x['handle']==self.native['handle']))
  finally:server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=='__main__':unittest.main(verbosity=2)
