"""Pure catalog/runtime and persisted activation activity regressions."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from orchestration.common import Blocked,canonical,digest
from studio.graph import Invalid
from studio.model_catalog import ModelCatalog
from studio.model_registration import ModelRegistration
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_store import WorkbenchStore

class EffortTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.p=dict(provider='claude',model='alias[1m]',effort='high',executable='/usr/bin/true',readonly_files=[])
  self.records={}
  class Flow:
   def get(inner,k,i):
    if (k,i) not in self.records:raise Invalid('not found')
    return self.records[k,i]
   def put(inner,k,i,value):self.records[k,i]=value
  self.flow=Flow();self.rt=SimpleNamespace(session_center=SimpleNamespace(workspace=lambda w:dict(path=str(self.root))),operator=dict(profiles={'p':self.p},native_local={}),flow=self.flow,native=self.root,storage=self.root,policy={'models':{}},policy_sha='policy',operator_sha256='operator',policy_file=self.root/'policy',validate_source=lambda *a,**k:None,check_operator=lambda:None,db=SimpleNamespace(prepare=lambda data:data))
  self.records['model-discovery','p']=dict(models=[dict(id='other',efforts=[],source='synthetic')],profile_sha256=digest(json.dumps(self.p,sort_keys=True).encode()),environment_sha256='env',error=None,at=time.time(),source='synthetic')
  self.records['cli-probe','p']=dict(available=True,at=time.time(),executable_sha256=digest(Path('/usr/bin/true').resolve().read_bytes()),efforts=['low','medium','high','xhigh','max'])
  self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch('studio.model_registration.ModelRegistration.choices',return_value={}))
  self.stack.enter_context(patch('studio.model_registration.profile_identity',return_value='env'))
  self.stack.enter_context(patch('studio.native_local.bind',return_value=None));self.stack.enter_context(patch('studio.agent_extensions.AgentExtensions.bind'))
 def catalog(self):return ModelCatalog(SimpleNamespace(rt=self.rt,db=self.flow)).get('p',self.p,False,'w')
 def bind(self,model='alias[1m]',effort='max',**kw):return WorkbenchRuntime.node_binding(self.rt,'w',dict(profile='p',model=model,effort=effort,mode='Ask',**kw),admission=False,direct=True)
 def test_omitted_exact_default_matches_catalog_registration_and_binding(self):
  row=next(m for m in self.catalog()['models'] if m['id']==self.p['model']);self.assertIn('max',row['selectable_efforts']);self.assertFalse(row['discovered']);self.assertEqual(self.bind()['binding']['effort'],'max')
  self.assertEqual(ModelRegistration(self.rt).prepare(dict(workspace='w',profile='p',model=self.p['model'],effort='max'),direct=True)['effort'],'max')
  with self.assertRaises(Invalid):self.bind(model='alias')
  with self.assertRaises(Invalid):self.bind(effort='unknown')
  with self.assertRaises(Invalid):self.bind(permission='workspace-write')
 def test_stale_metadata_is_visible_and_refresh_restores_same_rule(self):
  self.records['cli-probe','p']['at']=time.time()-301;result=self.catalog();self.assertTrue(result['stale']);self.assertTrue(result['effort_metadata_stale']);self.assertTrue(result['effort_metadata_notice'])
  with self.assertRaises(Invalid):self.bind()
  self.records['cli-probe','p']['at']=time.time();self.assertFalse(self.catalog()['effort_metadata_stale']);self.assertEqual(self.bind()['binding']['effort'],'max')
 def test_exact_directory_efforts_override_cli_grammar(self):
  self.records['model-discovery','p']['models']=[dict(id=self.p['model'],efforts=['low'],effort_source='synthetic provider')]
  row=next(m for m in self.catalog()['models'] if m['id']==self.p['model']);self.assertEqual(row['selectable_efforts'],['low'])
  self.assertEqual(self.bind(effort='low')['binding']['effort'],'low')
  with self.assertRaises(Invalid):self.bind(effort='high')
  with self.assertRaises(Invalid):self.bind(effort='max')
 def test_profile_switch_requires_exact_new_model(self):
  self.p['model']='different[1m]';self.records['model-discovery','p']['profile_sha256']=digest(json.dumps(self.p,sort_keys=True).encode())
  row=next(m for m in self.catalog()['models'] if m['id']=='different[1m]');self.assertIn('max',row['selectable_efforts'])
  self.assertEqual(self.bind(model='different[1m]')['binding']['model'],'different[1m]')
  with self.assertRaises(Invalid):self.bind(model='alias[1m]')
 def test_default_profile_without_directory_uses_same_cli_options(self):
  self.records.pop(('model-discovery','p'))
  row=next(m for m in self.catalog()['models'] if m['id']==self.p['model'])
  self.assertIn('max',row['selectable_efforts']);self.assertEqual(self.bind()['binding']['effort'],'max')
 def test_existing_refresh_path_replaces_stale_cli_metadata_without_model_call(self):
  self.records['cli-probe','p']['at']=time.time()-301;self.rt.rpc_home=self.root
  manager=WorkbenchSessions(self.rt);calls=[]
  def rpc(binding,cwd,method,params):
   calls.append(method)
   return dict(available=True,machine_output=True,efforts=['low','medium','high','xhigh','max']) if method=='cli/probe' else dict(logged_in=True)
  rows=[dict(id='other',efforts=[],source='synthetic')]
  with patch.object(manager,'rpc',side_effect=rpc),patch('studio.model_catalog.proxy_models',return_value=rows),patch('studio.agent_extensions.capabilities',return_value={'skills':False,'subagents':False}):
   value=manager._capabilities(True,'p','w')[0]
  self.assertIn('cli/probe',calls);self.assertFalse(value['effort_metadata_stale'])
  self.assertIn('max',next(m for m in value['models'] if m['id']==self.p['model'])['selectable_efforts']);self.assertEqual(self.bind()['binding']['effort'],'max')

class ActivityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.flow=WorkbenchStore(self.root/'flow.sqlite');self.run='r';self.node='n';self.sid='00000000-0000-4000-8000-000000000001';self.key=digest(canonical([self.run,self.node]));self.a=digest(canonical([self.run,self.node,[]]))
  self.binding=dict(provider='codex',execution_backend='native-local',home=str(self.root),session_id=self.sid,model='m',effort='high',readonly_files=[])
  self.flow.put('run',self.run,dict(id=self.run,workspace='w',task='task'));self.flow.put('node-session',self.key,dict(id=self.key,run=self.run,node=self.node,workspace='w',cwd=str(self.root),binding=self.binding))
  self.item=dict(id=self.a,run=self.run,kind='agent',node=self.node,chat_key=self.key,state='RUNNING',iteration_path=[],observed_session=self.sid,process={'pid':1},native_tool_activity=[]);self.flow.put('activation',self.a,self.item)
  self.rt=SimpleNamespace(flow=self.flow,snapshot=lambda run:dict(state='RUNNING'),readonly_configuration_warning=lambda:None,storage=self.root);self.manager=WorkbenchSessions(self.rt)
  self.p1=patch('orchestration.privacy.credentials',return_value=['SYNTHETIC_SECRET']);self.p1.start();self.addCleanup(self.p1.stop)
  self.p2=patch('orchestration.process.group_alive',return_value=True);self.alive=self.p2.start();self.addCleanup(self.p2.stop)
 def read(self):return self.manager.node_conversation('w',self.run,self.a)
 def put(self,**values):self.item.update(values);self.flow.put('activation',self.a,self.item)
 def test_no_turn_shows_recorded_deduplicated_redacted_activity(self):
  self.put(native_tool_activity=[dict(event='item.started',id='tool1',command='echo SYNTHETIC_SECRET',output='',status='running'),dict(event='item.completed',id='tool1',command='',output='SYNTHETIC_SECRET',status='completed',exit_code=0)])
  out=self.read();self.assertEqual(out['state'],'RUNNING');self.assertEqual(len(out['activity']),1);row=out['activity'][0];self.assertEqual(row['source'],'activation-journal');self.assertEqual(row['status'],'completed');self.assertIsNone(row['started_at']);self.assertNotIn('SYNTHETIC_SECRET',str(out));self.assertIn('[REDACTED]',str(row))
 def test_unknown_process_retains_evidence_without_claiming_running(self):
  self.put(native_tool_activity=[dict(event='item.started',id='tool1',command='x',status='running')]);self.alive.return_value=False
  out=self.read();self.assertEqual(out['state'],'UNKNOWN');self.assertFalse(out['process_alive']);self.assertEqual(out['activity'][0]['activation_state'],'UNKNOWN')
 def test_finished_failed_and_cancelled_events_remain_visible(self):
  for state in ('FAILED','CANCELLED'):
   with self.subTest(state=state):
    self.put(state=state,native_tool_activity=[dict(event='item.completed',id='tool1',command='x',output='done',status='completed')])
    out=self.read();self.assertEqual(out['state'],state);self.assertEqual(out['activity'][0]['activation_state'],state)
 def test_cross_run_or_chat_never_leaks(self):
  self.put(native_tool_activity=[dict(event='item.completed',id='tool1',command='x',output='y')])
  with self.assertRaises(Blocked):self.manager.node_conversation('other','r',self.a)
  self.flow.put('run','other',dict(id='other',workspace='w',task='other'))
  with self.assertRaises(Blocked):self.manager.node_conversation('w','other',self.a)
  chat=self.flow.get('node-session',self.key);chat['node']='different';self.flow.put('node-session',self.key,chat);self.assertEqual(self.read()['activity'],[])
  chat['node']=self.node;chat['binding']['session_id']='00000000-0000-4000-8000-000000000002';self.flow.put('node-session',self.key,chat);self.assertEqual(self.read()['activity'],[])
 def test_late_event_updates_only_its_activation_and_round(self):
  self.assertEqual(self.read()['activity'],[])
  self.put(native_tool_activity=[dict(event='item.started',id='tool1',command='x',status='running')]);self.assertEqual(len(self.read()['activity']),1)
  other=digest(canonical([self.run,self.node,[1]]));self.flow.put('activation',other,dict(self.item,id=other,iteration_path=[1],native_tool_activity=[dict(event='item.completed',id='tool2',command='y',output='done')]))
  self.assertEqual(self.read()['activity'][0]['call_id'],'tool1');self.assertEqual(self.manager.node_conversation('w',self.run,other)['activity'][0]['call_id'],'tool2')
  self.put(iteration_path=[1]);self.assertEqual(self.read()['activity'],[])
 def test_legacy_history_and_journal_merge_without_duplicate(self):
  chat=self.flow.get('node-session',self.key);chat['binding']['execution_backend']='isolated';self.flow.put('node-session',self.key,chat)
  self.put(native_history_offset=0,native_tool_activity=[dict(event='item.completed',id='tool1',command='x',output='done')])
  history=dict(messages=[],summaries=[],activity=[dict(name='命令',call_id='tool1',arguments='x',result='done',events=[],association='matched')])
  self.rt.session_center=SimpleNamespace(resolve=lambda *a:({},dict(native={'path':'p'})))
  with patch.object(self.manager,'activation_session',return_value={'handle':'h'}),patch.object(self.manager,'history',return_value=history):out=self.read()
  self.assertEqual(len(out['activity']),1);self.assertEqual(out['activity'][0]['source'],'native-history+activation-journal')
 def test_completed_saved_answer_and_journal_survive_native_history(self):
  receipt=dict(session_id=self.sid,requested_model='m',requested_effort='high',native_turn_id='turn',process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
  self.put(state='COMPLETED',native_turn_id='turn',receipt=receipt,output=dict(receipt=receipt,answer='answer'),native_tool_activity=[dict(event='item.completed',id='tool1',command='x',output='done')])
  with patch.object(self.manager,'activation_session',return_value={'handle':'h'}),patch('studio.native_local.codex_history',return_value=[dict(role='assistant',text='native')]):out=self.read()
  self.assertEqual(out['saved_answer'],'answer');self.assertEqual(len(out['activity']),1);self.assertEqual(out['history_status'],'available')
