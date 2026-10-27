"""Ordinary native semantics; synthetic processes and no real Provider calls."""
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
SID='00000000-0000-4000-8000-000000000001'
class OrdinaryNativeTests(unittest.TestCase):
 def binding(self,folder,**kw):
  return dict(dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=SID,project='test',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[folder+'/authority']),**kw)
 def test_ordinary_resume_uses_native_exec_and_actual_uuid(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,method,params):
    if method!='config/read':raise AssertionError('ordinary exec must not start an unisolated app-server thread')
    return {'config':{}}
  events=[dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='visible')),dict(type='turn.completed',usage={})]
  raw=('\n'.join(json.dumps(x) for x in events)+'\n').encode();calls=[]
  def execute(argv,cwd,home,**kw):
   calls.append(argv);self.assertEqual(argv[:3],['native','exec','resume']);self.assertEqual(argv[-2:],[SID,'-']);kw['on_stdout'](raw)
   return dict(exit=0,timeout=False,overflow=False,cancelled=False,process={'pid':1}),raw,b''
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder)
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability',create=True),patch('orchestration.process.execute',side_effect=execute):
    value=LocalCodex(b,dict(step_timeout_seconds=10,max_log_bytes=100000)).start('question',folder,folder)
   self.assertEqual(value['session_id'],SID);self.assertIsNone(value['native_turn_id']);self.assertEqual(len(calls),1)
 def test_explicit_strict_resume_refuses_unavailable_atomic_cas(self):
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,require_atomic_resume=True)
   with patch('studio.local_codex_adapter.CodexRPC',side_effect=AssertionError('must not spawn')):
    with self.assertRaisesRegex(Blocked,'原子'):LocalCodex(b,dict(step_timeout_seconds=10,max_log_bytes=100000)).start('question',folder,folder)
 def test_ordinary_resume_real_failure_never_replays(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  calls=[]
  def execute(*args,**kw):
   calls.append(args);return dict(exit=1,timeout=False,overflow=False,cancelled=False),b'',b''
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('orchestration.process.execute',side_effect=execute):
    with self.assertRaisesRegex(Blocked,'未完成'):LocalCodex(self.binding(folder),dict(step_timeout_seconds=10,max_log_bytes=100000)).start('question',folder,folder)
   self.assertEqual(len(calls),1)
 def test_failure_reports_redacted_stderr_and_actual_exit(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  class Store:
   def __init__(self):self.finished=[]
   def get(self,*a):return {'data':{'cancel_requested':False}}
   def intent(self,*a):return 'request'
   def finish(self,*a,**kw):self.finished.append((a,kw))
  with tempfile.TemporaryDirectory() as folder:
   auth=Path(folder)/'.codex';auth.mkdir();(auth/'auth.json').write_text(json.dumps({'access_token':'synthetic-private-value'}))
   err=b'ERROR Failed to load AGENTS.md: bwrap Read-only file system token=synthetic-private-value\n{"type":"reasoning","content":"hidden-thought"}\nERROR reasoning_content=hidden-thought\n'
   process=dict(exit=1,timeout=False,overflow=False,cancelled=False,sha256={'stderr':'abc'},bytes={'stderr':len(err)})
   store=Store()
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('orchestration.process.execute',return_value=(process,b'',err)):
    with self.assertRaises(Blocked) as caught:LocalCodex(self.binding(folder),dict(step_timeout_seconds=10,max_log_bytes=100000)).start('question',folder,folder,store=store,run='run')
   text=str(caught.exception);self.assertIn('AGENTS.md',text);self.assertIn('Read-only file system',text);self.assertIn('1',text);self.assertNotIn('synthetic-private-value',text);self.assertNotIn('hidden-thought',text)
   self.assertEqual(store.finished[-1][1]['process']['sha256']['stderr'],'abc');self.assertEqual(store.finished[-1][1]['process']['exit'],1);self.assertNotIn('hidden-thought',repr(store.finished))
 def test_stderr_error_excerpt_is_bounded_and_filters_protocol(self):
  from studio.local_codex_adapter import _stderr_diagnostic
  raw=('INFO private trace\nERROR HTTP 503 bearer sk-'+'a'*40+'\nERROR reasoning_content=hidden\nERROR HTTP 429 '+('x'*5000)+'\n').encode()
  text=_stderr_diagnostic(raw,set());self.assertIn('HTTP 503',text);self.assertIn('HTTP 429',text);self.assertNotIn('sk-'+'a'*40,text);self.assertNotIn('hidden',text);self.assertNotIn('private trace',text);self.assertLessEqual(len(text),1600)
 def test_exec_reports_only_an_explicit_native_turn_id(self):
  turn='00000000-0000-4000-8000-000000000002'
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  events=[dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='visible')),dict(type='turn.completed',turn_id=turn,usage={})]
  raw=('\n'.join(json.dumps(x) for x in events)+'\n').encode()
  def execute(*args,**kw):
   kw['on_stdout'](raw);return dict(exit=0,timeout=False,overflow=False,cancelled=False),raw,b''
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('orchestration.process.execute',side_effect=execute):
    result=LocalCodex(self.binding(folder),dict(step_timeout_seconds=10,max_log_bytes=100000)).start('question',folder,folder)
  self.assertEqual(result['native_turn_id'],turn)
 def test_native_probe_allows_reads_outside_but_rejects_outside_writes(self):
  from studio.native_codex_exec_probe import require_native_write_capability
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,true,false,true,true,true,false,false,false,false,false,false,true,true,false,false,true]')),patch('studio.native_codex_exec_probe._tool_boundary_probe'):
    self.assertTrue(require_native_write_capability('/bin/true',folder,True))
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,true,true,true,true,true,false,false,false,false,false,false,false,false]')):
    with self.assertRaises(Blocked):require_native_write_capability('/bin/true',folder,True)
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,true,false,true,true,true]')):
    from studio.native_codex_exec_probe import require_sandbox_capability
    with self.assertRaises(Blocked):require_sandbox_capability('/bin/true',folder,True)
 def test_explicit_native_terminal_does_not_claim_readonly(self):
  from devflower_native_terminal import native_cli_argv
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,execution_backend='native-local',native_interactive=True)
   argv=native_cli_argv(b,SID);self.assertEqual(argv[2:4],['resume',SID]);self.assertNotIn('--sandbox',argv);self.assertNotIn('--ask-for-approval',argv);self.assertIn('default_permissions="devflower"',argv);self.assertIn(folder+'/authority',str(argv))
   with self.assertRaises(Blocked):native_cli_argv(dict(b,native_protected=[]),SID)
   legacy=native_cli_argv(dict(b,native_interactive=False),SID);self.assertIn('read-only',legacy);self.assertIn('never',legacy)
   claude=dict(b,provider='claude');interactive=native_cli_argv(claude,SID);self.assertIn('--strict-mcp-config',interactive);self.assertIn('--mcp-config',interactive);self.assertEqual(interactive[interactive.index('--tools')+1],'');self.assertIn('plan',interactive)
   legacy_claude=native_cli_argv(dict(claude,native_interactive=False),SID);self.assertIn('plan',legacy_claude);self.assertIn('--strict-mcp-config',legacy_claude)
 def test_click_opens_native_terminal_scope_but_legacy_stays_refused(self):
  from studio.native_terminals import NativeTerminals
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,execution_backend='native-local',runtime_profile='profile')
   row=dict(native=dict(session_id=SID,cwd=folder,sha256='a'*64));project=dict(id='test',path=folder)
   center=SimpleNamespace(resolve=lambda *a:(project,row),_binding=lambda *a:(b,None))
   selected=dict(binding=b,cwd=folder,native_sha256='a'*64)
   rt=SimpleNamespace(session_center=center,node_binding=lambda *a,**kw:selected,db=SimpleNamespace(prepare=lambda value:value),candidate_sha256='candidate',operator_sha256='operator',policy_sha='policy')
   manager=NativeTerminals(rt,SimpleNamespace(busy=lambda *a:False));self.addCleanup(manager.shutdown)
   permit=manager.prepare(dict(workspace='test',handle='handle',interaction='click'))
   self.assertEqual(permit['session_id'],SID);self.assertIn('权限由 CLI 管理',permit['human_questions'][0])
   with self.assertRaisesRegex(Blocked,'终端尚无法隔离'):manager.prepare(dict(workspace='test',handle='handle'))
 def test_native_tool_free_only_allows_asking_tool(self):
  from studio.native_codex_exec import exec_args,provider_overrides
  with tempfile.TemporaryDirectory() as folder:
   args=exec_args(self.binding(folder,no_tools=True),provider_overrides({}))
   self.assertIn('features.shell_tool=false',args);self.assertIn('features.code_mode_host=false',args);self.assertNotIn('danger-full-access',str(args))
 def test_protected_native_credentials_and_authority_cannot_be_workspace(self):
  from studio.workspace_access import view_policy
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);home=root/'home';config=home/'.codex';config.mkdir(parents=True);source=root/'project';source.mkdir();authority=root/'control';authority.mkdir()
   rt=SimpleNamespace(protected=[authority],storage=root/'store',native=root/'native',operator={'native_local':{'codex':dict(home=str(home),config_root=str(config))}})
   self.assertTrue(view_policy(rt,str(root))['protected_container']);self.assertTrue(view_policy(rt,str(config))['protected_container']);self.assertTrue(view_policy(rt,str(home))['protected_container']);self.assertFalse(view_policy(rt,str(source))['protected_container'])
 def test_delivery_workflow_never_enters_ordinary_mode(self):
  from studio.workbench_scheduler import has_delivery
  self.assertFalse(has_delivery(dict(nodes=[dict(kind='agent')])));self.assertTrue(has_delivery(dict(nodes=[dict(kind='loop',body=dict(nodes=[dict(kind='delivery')]))])))
 def test_click_resume_accepts_new_native_history_but_not_identity_change(self):
  from studio.session_center import SessionCenter
  from studio.server import Conflict
  from orchestration.common import workspace_hash
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,execution_backend='native-local');source=dict(root=folder)
   runtime=SimpleNamespace(db=None,operator_sha256='pinned',project=lambda _:dict(path=folder),validate_source=lambda _:source)
   center=SessionCenter(runtime);row=dict(native=dict(sha256='changed',session_id=SID,cwd=folder),profile=dict(provider='codex',home=folder,config_root=folder))
   value=dict(operator_sha256='pinned',workspace='test',source=source,source_sha256=workspace_hash(folder),handle='handle',native_sha256='old',binding=b,cwd=folder,interaction='click')
   with patch.object(center,'resolve',return_value=({},row)):
    center._recheck(value)
    with self.assertRaises(Conflict):center._recheck(dict(value,interaction='legacy'))
    row['native']['cwd_changed']=True
    with self.assertRaises(Conflict):center._recheck(value)
    row['native']['cwd_changed']=False
    row['native']['session_id']='00000000-0000-4000-8000-000000000002'
    with self.assertRaises(Conflict):center._recheck(value)
 def test_workflow_latest_history_keeps_session_location_binding(self):
  from studio.workbench_runtime import WorkbenchRuntime
  from studio.server import Conflict
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,execution_backend='native-local');chat=dict(binding=b,workspace='test',cwd=folder,native_path=folder+'/session.jsonl',expected_native_sha256='old',handle='new')
   runtime=SimpleNamespace(native_snapshot=lambda workspace,binding:dict(native_cwd=folder,native_path=folder+'/session.jsonl',expected_native_sha256='new'))
   WorkbenchRuntime.recheck_session(runtime,chat,native_latest=True)
   runtime.native_snapshot=lambda workspace,binding:dict(native_cwd=folder,native_path=folder+'/other.jsonl',expected_native_sha256='new')
   with self.assertRaises(Conflict):WorkbenchRuntime.recheck_session(runtime,chat,native_latest=True)
 def test_click_configuration_assistant_uses_probed_ordinary_no_write_tools(self):
  import test_native_local
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  base=test_native_local.NativeLocalTests();base.setUp();self.addCleanup(base.doCleanups)
  rt=base.rt;workspace=base.base.project['id'];binding=dict(base.binding,role='reviewer',no_tools=True,session_id=None,runtime_profile='default-codex')
  folder=rt.storage/'synthetic-config-context';folder.mkdir();graph=examples(rt.operator['profiles'])[0]
  row=dict(id='config-synthetic',state='QUEUED',configuration=True,workspace=workspace,binding=binding,cwd=str(folder),interaction='click',cancel_requested=False,base_graph=graph,goal='synthetic',choices={},base_graph_sha256='a'*64)
  rt.db.put('session-operation',row['id'],row);seen=[]
  class FakeCLI:
   def __init__(self,provider,binding,limits):seen.append(binding)
   def start(self,*a,**kw):raise Blocked('synthetic stop')
  assistant=ConfigurationAssistant(rt)
  with patch.object(assistant,'_source'),patch.object(assistant,'_claim_dispatch',return_value=True),patch('studio.model_registration.ModelRegistration.require_direct'),patch('studio.configuration_assistant.CLI',FakeCLI):assistant.execute(row['id'])
  self.assertEqual(len(seen),1);self.assertTrue(seen[0]['no_tools']);self.assertEqual(seen[0]['native_interaction'],'ordinary');self.assertEqual(rt.db.get('session-operation',row['id'])['state'],'FAILED')
 def test_native_snapshot_refuses_ambiguous_or_unreadable_session(self):
  from studio.workbench_runtime import WorkbenchRuntime
  with tempfile.TemporaryDirectory() as folder:
   b=self.binding(folder,execution_backend='native-local');row=dict(native=dict(cwd=folder,cwd_changed=False,path=folder+'/x',sha256='a'*64),profile=dict(home=folder,config_root=folder,provider='codex'),ambiguous=True,authorized_read=True)
   runtime=SimpleNamespace(session_center=SimpleNamespace(discover=lambda *a:dict(discovery=dict(sessions=[dict(session_id=SID,handle='opaque')]))),db=SimpleNamespace(get=lambda *a:row))
   with self.assertRaises(Blocked):WorkbenchRuntime.native_snapshot(runtime,'test',b)
   row['ambiguous']=False;row['authorized_read']=False
   with self.assertRaises(Blocked):WorkbenchRuntime.native_snapshot(runtime,'test',b)
