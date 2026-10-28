"""Native exec configuration and synthetic-transport regressions; no real model."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.native_codex_exec import provider_overrides,exec_args
from studio.native_codex_exec_probe import require_exec_capability,require_sandbox_capability
from studio.local_codex_adapter import LocalCodex

SID='00000000-0000-4000-8000-000000000001'
class ExecConfigTests(unittest.TestCase):
 def test_provider_types_safe_keys_and_quoted_values(self):
  config=dict(model_provider='relay-x',model_providers={'relay-x':dict(name='Relay',base_url='https://relay.example/v1',wire_api='responses',requires_openai_auth=True)})
  args=provider_overrides(config);self.assertIn('model_providers.relay-x.base_url="https://relay.example/v1"',args);self.assertIn('model_providers.relay-x.requires_openai_auth=true',args)
  for name in ('relay.bad','relay".other'):
   with self.subTest(name=name),self.assertRaises(Blocked):provider_overrides(dict(config,model_provider=name))
  for update in ({'env_key':'TOKEN'},{'http_headers':{'Authorization':'secret'}},{'new_security_field':True},{'requires_openai_auth':'true'},{'base_url':'https://user:pass@example.test'}):
   with self.subTest(update=update),self.assertRaises(Blocked):provider_overrides(dict(model_provider='relay-x',model_providers={'relay-x':dict(config['model_providers']['relay-x'],**update)}))
  with self.assertRaises(Blocked):provider_overrides(dict(model_provider='missing',model_providers={}))
  with self.assertRaises(Blocked):provider_overrides(dict(model_provider='openai',forced_login_method='chatgpt'))
  self.assertIn('cli_auth_credentials_store="keyring"',provider_overrides(dict(cli_auth_credentials_store='keyring')))
  with self.assertRaises(Blocked):provider_overrides(dict(cli_auth_credentials_store='unknown-store'))
 def test_argv_enforces_explicit_capabilities_and_sandbox(self):
  b=dict(executable='fixture',model='gpt-test',effort='high',role='reviewer');args=exec_args(b,provider_overrides({}))
  for value in ('--ignore-user-config','--ignore-rules','--strict-config','features.plugins=false','features.apps=false','features.multi_agent=false','features.code_mode_host=true','features.shell_tool=true','web_search="disabled"','sandbox_mode="read-only"'):
   self.assertIn(value,args)
  self.assertEqual(args[-1],'-');self.assertNotIn('danger-full-access',str(args))
  args=exec_args(dict(b,role='implementer'),provider_overrides({}));self.assertIn('sandbox_mode="workspace-write"',args);self.assertIn('sandbox_workspace_write.exclude_slash_tmp=true',args)
  args=exec_args(dict(b,no_tools=True),provider_overrides({}));self.assertIn('features.shell_tool=false',args);self.assertIn('features.code_mode_host=false',args)
 def test_real_binary_synthetic_lock_and_tool_probe(self):
  with tempfile.TemporaryDirectory() as folder:
   self.assertTrue(require_exec_capability(os.environ.get('DEVFLOWER_TEST_CODEX_SUPPORTED','/usr/lib/chatgpt/resources/codex'),folder))
   with self.assertRaises(Blocked):require_exec_capability(os.environ.get('DEVFLOWER_TEST_CODEX_LEGACY','/usr/local/bin/codex'),folder)
 def test_sandbox_probe_rejects_cross_workspace_read(self):
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,false,false,true]')):
    with self.assertRaisesRegex(Blocked,'沙箱'):require_sandbox_capability('/bin/true',folder,False)
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,true,false,true]')):
    with self.assertRaisesRegex(Blocked,'沙箱'):require_sandbox_capability('/bin/true',folder,True)
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'[true,true,false,false,true,true]')):
    with self.assertRaisesRegex(Blocked,'沙箱'):require_sandbox_capability('/bin/true',folder,True)
 def test_new_turn_uses_real_identity_and_preserves_tool_events(self):
  class RPC:
   def __init__(self,*a,**k):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,method,params):self_method.append(method);return {'config':{}}
  class Store:
   def __init__(self):self.events=[];self.calls=[]
   def get(self,run):return {'data':{'cancel_requested':False}}
   def intent(self,run,kind,data):self.calls.append((kind,data));return 'req'
   def finish(self,key,status,**data):self.calls.append((status,data))
   def native_tool_event(self,row):self.events.append(row)
   def visible_request(self,text):self.text=text
  events=[{'type':'thread.started','thread_id':SID},{'type':'item.started','item':{'id':'i','type':'command_execution','command':'cat file','status':'in_progress'}},{'type':'item.completed','item':{'id':'i','type':'command_execution','command':'cat file','aggregated_output':'value','exit_code':0,'status':'completed'}},{'type':'item.completed','item':{'type':'agent_message','text':'visible'}},{'type':'turn.completed','usage':{}}]
  raw=('\n'.join(json.dumps(x) for x in events)+'\n').encode();self_method=[];store=Store()
  def execute(argv,cwd,home,**kw):
   self.assertIn('--ignore-user-config',argv);self.assertEqual(kw['backend'],'native-local');self.assertFalse(kw['isolate']);self.assertEqual(kw['input_text'],'question');kw['on_stdout'](raw[:47]);kw['on_stdout'](raw[47:])
   return dict(exit=0,timeout=False,overflow=False,cancelled=False,process={'pid':1}),raw,b''
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='model',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[])
   client=LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=100000))
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_sandbox_capability'),patch('orchestration.process.execute',side_effect=execute):
    value=client.start('question',folder,folder,store=store,run='run')
   self.assertEqual(value['session_id'],SID);self.assertIsNone(value['native_turn_id']);self.assertEqual(value['report']['reply'],'visible');self.assertEqual(len(store.events),2);self.assertEqual(store.events[-1]['output'],'value');self.assertEqual(self_method,['config/read'])
 def test_preflight_refusal_never_sends_prompt(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[])
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_sandbox_capability',side_effect=Blocked('unverified sandbox')),patch('orchestration.process.execute') as send:
    with self.assertRaisesRegex(Blocked,'unverified sandbox'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('do not send',folder,folder)
    send.assert_not_called()
 def test_preflight_cancellation_stops_before_any_model_request(self):
  class RPC:
   def __init__(self,*a,**kw):raise AssertionError('cancelled request must not open RPC')
  class Store:
   def get(self,run):return {'data':{'cancel_requested':True}}
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[])
   with patch('studio.local_codex_adapter.CodexRPC',RPC):
    with self.assertRaisesRegex(Blocked,'取消'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('not sent',folder,folder,store=Store(),run='r')
 def test_exec_failure_has_no_successful_or_invented_receipt(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  class Store:
   def __init__(self):self.status=[]
   def get(self,run):return {'data':{}}
   def intent(self,*a):return 'request'
   def finish(self,key,status,**kwargs):self.status.append(status)
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[]);store=Store()
   def failed(argv,cwd,home,**kw):
    kw['on_stdout']((json.dumps(dict(type='thread.started',thread_id=SID))+'\n').encode())
    return dict(exit=1,timeout=False,overflow=False,cancelled=False),b'',b''
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_sandbox_capability'),patch('orchestration.process.execute',side_effect=failed):
    with self.assertRaisesRegex(Blocked,'未完成'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('question',folder,folder,store=store,run='r')
   self.assertIn('AGENT_FAILED',store.status);self.assertNotIn('AGENT_COMPLETED',store.status)
 def test_changed_provider_config_after_preflight_does_not_send(self):
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[])
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.model_registration.profile_identity',side_effect=['before','after']),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_sandbox_capability'),patch('orchestration.process.execute') as send:
    with self.assertRaisesRegex(Blocked,'预检期间变化'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('not sent',folder,folder)
    send.assert_not_called()
 def test_probe_deadline_terminates_the_local_child(self):
  import os,time
  from studio.native_codex_exec_probe import _run_command
  with tempfile.TemporaryDirectory() as folder:
   started=time.monotonic()
   with self.assertRaisesRegex(Blocked,'预算'):
    _run_command(['/usr/bin/python3','-c','import time;time.sleep(10)'],cwd=folder,env=dict(os.environ),deadline=time.monotonic()+.3,cancelled=lambda:False)
   self.assertLess(time.monotonic()-started,3)
 def test_native_tool_events_are_bounded_in_both_journals(self):
  from studio.session_center import SessionJournal
  from studio.workbench_scheduler import Journal
  class DB:
   def __init__(self):self.row={}
   def get(self,*args):return dict(self.row)
   def update(self,kind,key,data):self.row.update(data)
   def change(self,kind,key,data):self.row.update(data)
  for journal in (SessionJournal(DB(),'operation'),Journal(SimpleNamespace(db=DB()),'activation')):
   for i in range(45):journal.native_tool_event({'id':str(i),'command':'read','output':'visible'})
   db=journal.db if isinstance(journal,SessionJournal) else journal.s.db
   self.assertEqual(len(db.row['native_tool_activity']),40);self.assertEqual(db.row['native_tool_activity'][0]['id'],'5');self.assertEqual(db.row['native_tool_activity'][-1]['id'],'44')
 def test_unisolated_native_codex_terminal_is_refused_before_prepare(self):
  from studio.native_terminals import NativeTerminals
  binding=dict(provider='codex',execution_backend='native-local')
  center=SimpleNamespace(resolve=lambda *a:({'id':'workspace'},{'native':{'session_id':SID}}),_binding=lambda *a:(binding,None))
  runtime=SimpleNamespace(session_center=center,node_binding=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('must not prepare model')))
  manager=NativeTerminals(runtime,SimpleNamespace());self.addCleanup(manager.shutdown)
  with self.assertRaisesRegex(Blocked,'终端尚无法隔离'):manager.prepare(dict(workspace='workspace',handle='selected'))
 def test_model_tool_boundary_rejects_exit_zero_with_failed_or_invisible_tool(self):
  import time
  from urllib.request import Request,urlopen
  from studio.native_codex_exec_probe import _tool_boundary_probe
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);home=base/'home';work=base/'work';outside=base/'outside'
   for p in (home,work,outside):p.mkdir()
   program='print(json.dumps([True,False,False,False,False,False]))\n';expected='DFPROBE:[true, false, false, false, false, false]'
   def fake(args,*,cwd,env,input_data,deadline,cancelled):
    entry=next(x for x in args if x.startswith('model_providers.fixture.base_url='));url=json.loads(entry.split('=',1)[1])+'/responses'
    for inputs in ([],[{'type':'function_call_output','call_id':'call','output':'Process exited with code 0\n'+expected}]):
     request=Request(url,data=json.dumps({'input':inputs}).encode(),headers={'Content-Type':'application/json'},method='POST')
     with urlopen(request,timeout=3) as response:response.read()
    return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
   with patch('studio.native_codex_exec_probe._run_command',side_effect=fake):
    with self.assertRaisesRegex(ValueError,'events unavailable'):_tool_boundary_probe('/bin/true',base,home,work,outside,program,False,time.monotonic()+10,lambda:False)
 def test_unverified_tool_free_mode_is_refused_without_network(self):
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[],no_tools=True)
   with patch('studio.local_codex_adapter.CodexRPC',side_effect=AssertionError('must not open RPC')):
    with self.assertRaisesRegex(Blocked,'无工具模式'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('not sent',folder,folder)
