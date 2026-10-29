"""Pinned native configuration drift: readonly availability must not authorize execution."""
import copy,json,threading,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from test_workbench_runtime import fixture
from orchestration.common import save,digest,tree_hash
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_server import create_server

class OpenTargetSecurityFieldsTests(unittest.TestCase):
 def setUp(self):
  self.before=dict(model='SYNTHETIC-explicit',model_reasoning_effort='high',permissions={'default':'read'},model_provider='SYNTHETIC-relay')
  self.target={'open-in-target-preferences':{'global':'vscodeInsiders'}}
 def identity(self,value,detail=True):
  from studio.preference_baseline import security_fields,security_identity
  return security_identity(security_fields(value,desktop_detail=detail))
 def test_observed_target_is_ignored_only_in_explicit_desktop_semantics(self):
  after=dict(self.before,desktop=copy.deepcopy(self.target));snapshot=copy.deepcopy(after)
  self.assertEqual(self.identity(self.before),self.identity(after))
  self.assertNotEqual(self.identity(self.before,False),self.identity(after,False))
  self.assertEqual(after,snapshot)
 def test_unknown_target_values_and_types_remain_identity_sensitive(self):
  for value in ('vscode','terminal','/bin/true','file:///tmp/editor','https://example.invalid','code --wait',True,1,1.0,None,[],{},['vscodeInsiders'],{'command':'vscodeInsiders'}):
   with self.subTest(value=value):
    after=dict(self.before,desktop={'open-in-target-preferences':{'global':value}})
    self.assertNotEqual(self.identity(self.before),self.identity(after))
  for table in ('vscodeInsiders',True,1,[],['vscodeInsiders']):
   with self.subTest(table=table):
    self.assertNotEqual(self.identity(self.before),self.identity(dict(self.before,desktop={'open-in-target-preferences':table})))
 def test_unknown_nested_and_sibling_fields_are_retained(self):
  from studio.preference_baseline import security_fields
  for extra in ({'command':'editor'},{'path':'/tmp/editor'},{'uri':'file:///tmp/editor'},{'nested':{'global':'vscodeInsiders'}},{'unknown':True}):
   with self.subTest(extra=extra):
    after=dict(self.before,desktop={'open-in-target-preferences':{'global':'vscodeInsiders',**extra},'other':{'enabled':True}})
    fields=security_fields(after,desktop_detail=True)
    self.assertEqual(fields['desktop'],{'open-in-target-preferences':extra,'other':{'enabled':True}})
    self.assertNotEqual(self.identity(self.before),self.identity(after))
 def test_security_fields_and_nested_types_stay_bound(self):
  from studio.preference_baseline import security_identity
  for key,value in (('permissions',{'default':'write'}),('model_provider','other'),('approval_policy','never'),('auth',{'method':'other'}),('model_providers',{'relay':{'base_url':'https://other.invalid'}}),('hooks',{'command':'unsafe'}),('tools',{'write':True})):
   with self.subTest(key=key):
    after=dict(self.before,desktop=copy.deepcopy(self.target));after[key]=value
    self.assertNotEqual(self.identity(self.before),self.identity(after))
  identities=[self.identity(dict(self.before,desktop={'open-in-target-preferences':{'global':'vscodeInsiders','unknown':value}})) for value in (True,1,1.0)]
  self.assertEqual(len(set(identities)),3)
  self.assertEqual(len({security_identity({'x':v}) for v in (True,1,1.0)}),3)
 def test_conversation_detail_enum_and_bad_desktop_types_remain_checked(self):
  for mode in ('STEPS_PROSE','STEPS_COMMANDS','STEPS_EXECUTION'):
   after=dict(self.before,desktop={**self.target,'conversationDetailMode':mode})
   self.assertEqual(self.identity(self.before),self.identity(after))
  for value in ('UNKNOWN',True,1,[],{}):
   with self.subTest(mode=value),self.assertRaises(ValueError):
    self.identity(dict(self.before,desktop={**self.target,'conversationDetailMode':value}))
  for value in (True,1,'vscodeInsiders',[]):
   with self.subTest(desktop=value),self.assertRaises(ValueError):self.identity(dict(self.before,desktop=value))
 def test_direct_v1_v2_and_raw_config_pins_do_not_upgrade(self):
  import tempfile
  from studio.model_registration import direct_environment_identity
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);root=home/'.codex';root.mkdir();config=root/'config.toml'
   config.write_text('model="SYNTHETIC-default"\nmodel_reasoning_effort="high"\n')
   binding=dict(provider='codex',execution_backend='native-local',home=str(home),config_root=str(root),model='SYNTHETIC-explicit',effort='high',native_interaction='ordinary',execution_mode='Ask',direct_environment_scope='native-codex-ordinary',readonly_files=[])
   versions=[dict(binding,direct_environment_version=v) for v in (1,2,3)]
   before=[direct_environment_identity(b) for b in versions]
   raw=dict(versions[2],readonly_files=[[str(config),'.codex/config.toml']]);pin=direct_environment_identity(raw)
   config.write_text(config.read_text()+'[desktop.open-in-target-preferences]\nglobal="vscodeInsiders"\n')
   after=[direct_environment_identity(b) for b in versions]
   self.assertNotEqual(before[0],after[0]);self.assertNotEqual(before[1],after[1]);self.assertEqual(before[2],after[2])
   self.assertNotEqual(direct_environment_identity(raw),pin)
 def test_original_pin_worker_fd_and_missing_baseline_remain_authentic(self):
  import os,tempfile
  from types import SimpleNamespace
  from unittest.mock import patch
  from studio.preference_baseline import ENV,original,preference_only,worker_baseline
  raw=b'model="SYNTHETIC-original"\nmodel_reasoning_effort="high"\n[permissions]\ndefault="read"\n'
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);config=root/'config.toml';config.write_bytes(raw)
   operator=dict(native_local={'codex':{'config_root':folder}},dependencies={str(config):digest(raw)})
   with patch.dict(os.environ,{ENV:''}):baseline=original(operator)
   self.assertEqual(baseline['raw'],raw)
   config.write_bytes(raw+b'[desktop.open-in-target-preferences]\nglobal="vscodeInsiders"\n')
   self.assertTrue(preference_only(baseline,str(config)))
   self.assertEqual(operator['dependencies'][str(config)],digest(raw))
   with patch.dict(os.environ,{ENV:''}):self.assertIsNone(original(operator))
   self.assertFalse(preference_only(None,str(config)))
   env={};runtime=SimpleNamespace(preference_baseline=baseline,assistant_settings=None)
   with worker_baseline(runtime,env) as fds:
    self.assertEqual(len(fds),1);self.assertEqual(os.pread(fds[0],len(raw),0),raw)
    with patch.dict(os.environ,{ENV:str(os.dup(fds[0]))}):restored=original(operator)
   self.assertEqual(restored['raw'],raw);self.assertTrue(preference_only(restored,str(config)))
   self.assertNotIn(ENV,env);self.assertEqual(list(root.iterdir()),[config])
   config.write_bytes(raw.replace(b'default="read"',b'default="write"')+b'[desktop.open-in-target-preferences]\nglobal="vscodeInsiders"\n')
   self.assertFalse(preference_only(restored,str(config)))

class PreferenceDriftTests(unittest.TestCase):
 def setUp(self):
  self.base,old=fixture(self);home=self.base.root/'native-preferences';home.mkdir();root=home/'.codex';root.mkdir();self.config=root/'config.toml';self.config.write_text('model="one"\nmodel_reasoning_effort="high"\n[permissions]\ndefault="read"\n')
  op=copy.deepcopy(old.operator);op['native_local']={'codex':dict(home=str(home),config_root=str(root))};op['dependencies'][str(self.config)]=digest(self.config.read_bytes());op['profiles']['default-codex']['read_paths'].append(str(self.config));save(old.operator_file,op)
  self.rt=WorkbenchRuntime(old.operator_file,digest(old.operator_file.read_bytes()),tree_hash(Path(__file__).resolve().parents[1]),old.policy_file,old.policy_sha)
  self.rt.open_workspace(str(self.base.source));self.server,self.token=create_server(self.rt);thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start();self.addCleanup(thread.join);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
  self.url='http://127.0.0.1:'+str(self.server.server_port)
 def get(self):
  with urlopen(Request(self.url+'/api/status',headers={'Authorization':'Bearer '+self.token})) as response:return json.load(response)
 def test_changed_preference_keeps_status_readable(self):
  self.config.write_text('model="two"\nmodel_reasoning_effort="medium"\n[permissions]\ndefault="read"\n')
  status=self.get();self.assertEqual(status['version'],3);self.assertIsNone(status['configuration_warning'])
 def test_unsafe_permission_change_is_readable_but_cannot_launch(self):
  self.config.write_text('model="one"\n[permissions]\ndefault="write"\n')
  self.assertEqual(self.get()['version'],3)
  from orchestration.common import Blocked
  with self.assertRaises(Blocked):self.rt.check_operator()

 def test_changed_executable_remains_rejected_for_status(self):
  exe=self.rt.operator['profiles']['default-codex']['executable'];old=self.rt.operator['dependencies'][exe];self.rt.operator['dependencies'][exe]='0'*64
  try:
   with self.assertRaises(HTTPError) as error:self.get()
   self.assertEqual(error.exception.code,400)
  finally:self.rt.operator['dependencies'][exe]=old
 def test_drifted_read_view_does_not_start_native_metadata(self):
  from unittest.mock import patch
  from studio.native_local import profiles
  from studio.workbench_runtime import READONLY_VIEW
  self.config.write_text('model="two"\n')
  token=READONLY_VIEW.set(True)
  try:
   with patch('studio.native_codex_rpc.metadata',side_effect=AssertionError('native process must not start')):
    profiles(self.rt,self.base.project,[])
  finally:READONLY_VIEW.reset(token)
 def test_preference_change_allows_new_request_but_not_changed_security(self):
  from studio.workbench_examples import examples
  from orchestration.common import Blocked
  from unittest.mock import patch
  self.config.write_text('model="two"\nmodel_reasoning_effort="medium"\n[permissions]\ndefault="read"\n')
  request=dict(workspace=self.base.project['id'],graph=examples(self.rt.operator['profiles'])[0],task='synthetic task',interaction='click')
  permit=self.rt.prepare_flow(request)
  with patch.object(self.rt,'spawn') as spawn:self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
  self.assertEqual(spawn.call_count,1)
  self.config.write_text('model="two"\n[permissions]\ndefault="write"\n')
  with self.assertRaises(Blocked):self.rt.check_operator()

 def test_worker_reconstructs_original_pin_without_persisting_config(self):
  import subprocess,sys
  from studio.preference_baseline import worker_baseline
  from orchestration.common import clean_env
  self.config.write_text('model="two"\nmodel_reasoning_effort="medium"\n[permissions]\ndefault="read"\n')
  env=clean_env()
  script="from studio.workbench_runtime import WorkbenchRuntime; import sys; r=WorkbenchRuntime(*sys.argv[1:]); r.check_operator(); print('worker accepted pinned baseline')"
  args=[sys.executable,'-B','-c',script,str(self.rt.operator_file),self.rt.operator_sha256,self.rt.candidate_sha256,str(self.rt.policy_file),self.rt.policy_sha]
  env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'tools')
  with worker_baseline(self.rt,env) as fds:
   result=subprocess.run(args,env=env,pass_fds=fds,text=True,capture_output=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr);self.assertIn('worker accepted',result.stdout)
  self.assertFalse(any('baseline' in p.name for p in self.rt.storage.iterdir()))
 def test_missing_original_baseline_cannot_be_forged_after_restart(self):
  from orchestration.common import Blocked
  self.config.write_text('model="two"\n[permissions]\ndefault="write"\n')
  with self.assertRaises(Blocked):WorkbenchRuntime(self.rt.operator_file,self.rt.operator_sha256,self.rt.candidate_sha256,self.rt.policy_file,self.rt.policy_sha)
 def test_model_preference_changes_after_prepare_reject_old_snapshot(self):
  from studio.workbench_examples import examples
  from studio.server import Conflict
  from unittest.mock import patch
  graph=examples(self.rt.operator['profiles'])[0];p=self.rt.operator['profiles']['default-codex']
  for n in graph['nodes']:
   if n['kind']=='agent':n.update(profile='default-codex',provider='codex',model=p['model'],effort=p['effort'])
  # Preserve the original rejection contract for already-issued raw v1 permits.
  original=self.rt.node_binding
  with patch.object(self.rt,'node_binding',side_effect=lambda *a,**kw:original(*a,**kw,direct_environment_version=1)):
   permit=self.rt.prepare_flow(dict(workspace=self.base.project['id'],graph=graph,task='synthetic',interaction='click'))
  self.config.write_text('model="two"\nmodel_reasoning_effort="medium"\n[permissions]\ndefault="read"\n')
  with self.assertRaises(Conflict):self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
  self.assertEqual(self.rt.flow.list('run'),[])
 def test_new_snapshot_keeps_explicit_selection_after_preference_change(self):
  from studio.workbench_examples import examples
  from unittest.mock import patch
  graph=examples(self.rt.operator['profiles'])[0];p=self.rt.operator['profiles']['default-codex']
  for n in graph['nodes']:
   if n['kind']=='agent':n.update(profile='default-codex',provider='codex',model=p['model'],effort=p['effort'])
  original=self.rt.node_binding
  with patch.object(self.rt,'node_binding',side_effect=lambda *a,**kw:original(*a,**kw,direct_environment_version=2)):
   permit=self.rt.prepare_flow(dict(workspace=self.base.project['id'],graph=graph,task='synthetic',interaction='click'))
  self.config.write_text('model="two"\nmodel_reasoning_effort="medium"\n[permissions]\ndefault="read"\n')
  with patch.object(self.rt,'spawn') as spawn:result=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
  self.assertEqual(spawn.call_count,1)
  for selected in result['run']['bindings'].values():
   self.assertEqual(selected['binding']['direct_environment_version'],2)
   self.assertEqual((selected['binding']['model'],selected['binding']['effort']),(p['model'],p['effort']))
 def test_provider_hooks_and_tools_are_not_preferences(self):
  from orchestration.common import Blocked
  for suffix in ('model_provider="other"\n','[hooks]\ncommand="unsafe"\n','[tools]\nwrite=true\n'):
   with self.subTest(config=suffix):
    self.config.write_text('model="two"\n[permissions]\ndefault="read"\n'+suffix)
    self.assertTrue(self.get()['configuration_warning'])
    with self.assertRaises(Blocked):self.rt.check_operator()
 def test_new_v3_snapshot_accepts_bounded_desktop_detail(self):
  from studio.workbench_examples import examples
  from unittest.mock import patch
  graph=examples(self.rt.operator['profiles'])[0];p=self.rt.operator['profiles']['default-codex']
  for n in graph['nodes']:
   if n['kind']=='agent':n.update(profile='default-codex',provider='codex',model=p['model'],effort=p['effort'])
  permit=self.rt.prepare_flow(dict(workspace=self.base.project['id'],graph=graph,task='synthetic',interaction='click'))
  self.config.write_text('model="one"\nmodel_reasoning_effort="high"\n[permissions]\ndefault="read"\n[desktop]\nconversationDetailMode="STEPS_PROSE"\n')
  with patch.object(self.rt,'spawn') as spawn:result=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
  self.assertEqual(spawn.call_count,1)
  for selected in result['run']['bindings'].values():
   self.assertEqual(selected['binding']['direct_environment_version'],3)
   self.assertEqual((selected['binding']['model'],selected['binding']['effort']),(p['model'],p['effort']))

 def test_http_frozen_v3_selection_starts_once_after_observed_open_target_change(self):
  from studio.workbench_examples import examples
  from unittest.mock import patch
  graph=examples(self.rt.operator['profiles'])[0];p=self.rt.operator['profiles']['default-codex']
  expected=(p['model'],p['effort']);pin=self.rt.operator['dependencies'][str(self.config)]
  for node in graph['nodes']:
   if node['kind']=='agent':node.update(profile='default-codex',provider='codex',model=expected[0],effort=expected[1])
  def post(path,value):
   request=Request(self.url+path,data=json.dumps(value).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Origin':self.url})
   with urlopen(request) as response:return json.load(response)
  permit=post('/api/prepare',dict(workspace=self.base.project['id'],graph=graph,task='SYNTHETIC frozen model after display preference',interaction='click'))
  self.config.write_text(self.config.read_text()+'[desktop.open-in-target-preferences]\nglobal="vscodeInsiders"\n')
  self.assertIsNone(self.get()['configuration_warning'])
  with patch.object(self.rt,'spawn') as spawn:result=post('/api/start',dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'start'}))
  self.assertEqual(spawn.call_count,1)
  self.assertEqual(self.rt.operator['dependencies'][str(self.config)],pin)
  for selected in result['run']['bindings'].values():
   self.assertEqual(selected['binding']['direct_environment_version'],3)
   self.assertEqual((selected['binding']['model'],selected['binding']['effort']),expected)
  from orchestration.common import Blocked
  with self.assertRaises(Blocked):WorkbenchRuntime(self.rt.operator_file,self.rt.operator_sha256,self.rt.candidate_sha256,self.rt.policy_file,self.rt.policy_sha)


class OwnedTerminalControlRouteTests(unittest.TestCase):
 """Real guard/handler/store/OS PTY; memory HTTP transport, no native CLI."""
 use_http=False
 def setUp(self):
  import os,pty,subprocess,sys,tempfile,time
  from unittest.mock import patch
  from studio import session_runtime,workbench_runtime,workbench_server
  from studio.terminal_text import TerminalText
  from orchestration.common import clean_env
  from orchestration.store import process_identity
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  source=self.root/'source';source.mkdir();self.source_file=source/'public.txt';self.source_file.write_text('SYNTHETIC source')
  home=self.root/'home';home.mkdir();native_config=home/'.codex';native_config.mkdir()
  self.config=native_config/'config.toml';self.raw=b'model="SYNTHETIC"\n[permissions]\ndefault="read"\n';self.config.write_bytes(self.raw)
  self.exe=self.root/'synthetic-cli';self.exe.write_text('SYNTHETIC pinned executable; never invoked')
  project=self.root/'project';project.mkdir();self.workspace='SYNTHETIC-workspace';self.key='SYNTHETIC-owned-pty'
  self.operator=self.root/'operator.json';self.policy=self.root/'policy.json'
  profile=dict(provider='codex',executable=str(self.exe),model='SYNTHETIC',effort='high',read_paths=[],readonly_files=[])
  save(self.operator,dict(version=1,kind='development-sessions',storage=str(self.root/'storage'),native=str(self.root/'native-cache'),allowed_roots=[str(project)],profiles={'default-codex':profile},native_local={'codex':dict(home=str(home),config_root=str(native_config))},dependencies={str(self.config):digest(self.raw),str(self.exe):digest(self.exe.read_bytes())}))
  save(self.policy,dict(version=1,models={},external_steps={}))
  for module in (session_runtime,workbench_runtime):
   item=patch.object(module,'ROOT',source);item.start();self.addCleanup(item.stop)
  self.rt=WorkbenchRuntime(self.operator,digest(self.operator.read_bytes()),tree_hash(source),self.policy,digest(self.policy.read_bytes()),assistant_settings=self.root/'assistant-settings')
  class MemoryServer:
   def __init__(self,address,handler):self.server_port=18080;self.RequestHandlerClass=handler
   def server_close(self):pass
  if self.use_http:
   self.server,self.token=create_server(self.rt)
   thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
   self.addCleanup(thread.join);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
  else:
   with patch.object(workbench_server,'ThreadingHTTPServer',MemoryServer):self.server,self.token=create_server(self.rt)
   self.addCleanup(self.server.server_close)
  self.manager=self.server.native_terminals
  master,slave=pty.openpty()
  script="import os,tty;tty.setraw(0);os.write(1,b'READY\\n')\nwhile True:\n b=os.read(0,1024)\n if not b:break\n os.write(1,b'Got:'+b)\n"
  try:self.process=subprocess.Popen([sys.executable,'-B','-c',script],stdin=slave,stdout=slave,stderr=slave,start_new_session=True,close_fds=True,env=clean_env(home))
  finally:os.close(slave)
  os.set_blocking(master,False);identity=process_identity(self.process.pid)
  entry=dict(proc=self.process,master=master,process=identity,home=str(home),session_id='SYNTHETIC-session',text='',first=0,next=0,total=0,filter=TerminalText(),inputs={},owner='workbench',generation=0,deadline=time.monotonic()+30,state='RUNNING',execution_binding={},cwd=str(project),admission_verification=None)
  self.rt.flow.put('terminal',self.key,dict(id=self.key,workspace=self.workspace,provider='codex',session_id='SYNTHETIC-session',state='RUNNING',process=identity,input_sequence=0,input_generation=0,input_owner='workbench'))
  self.manager.entries[self.key]=entry
  entry['thread']=threading.Thread(target=self.manager._read,args=(self.key,entry),daemon=True);entry['thread'].start()
  self.wait(lambda:'READY' in self.manager.snapshot(self.workspace,self.key)['output'])
 def wait(self,predicate):
  import time
  for _ in range(100):
   if predicate():return
   time.sleep(.02)
  self.fail('controlled OS PTY deadline')
 def post(self,path,data,authorized=True):
  import io
  from studio.workbench_runtime import READONLY_VIEW
  raw=json.dumps(data).encode();headers={'Host':'127.0.0.1:'+str(self.server.server_port),'Content-Type':'application/json','Content-Length':str(len(raw)),'Authorization':'Bearer '+(self.token if authorized else 'wrong')}
  if self.use_http:
   request=Request('http://'+headers['Host']+path,data=raw,headers=headers)
   try:
    with urlopen(request) as response:return response.status,json.load(response)
   except HTTPError as error:return error.code,json.load(error)
  handler=self.server.RequestHandlerClass.__new__(self.server.RequestHandlerClass)
  handler.server=self.server;handler.headers=headers;handler.path=path;handler.rfile=io.BytesIO(raw)
  responses=[];handler.respond=lambda status,value,mime=None:responses.append((status,value))
  before=READONLY_VIEW.get();handler.route(True);self.assertEqual(READONLY_VIEW.get(),before)
  self.assertEqual(len(responses),1);return responses[0]
 def control(self,action,**extra):return self.post('/api/terminal/'+action,dict(workspace=self.workspace,id=self.key,**extra))
 def drift(self,kind='trust'):
  if kind=='permission':
   self.config.write_bytes(self.raw.replace(b'default="read"',b'default="write"'));return
  suffix={'trust':b'[projects."/SYNTHETIC/qa"]\ntrust_level="trusted"\n','provider':b'model_provider="other"\n'}[kind]
  self.config.write_bytes(suffix+self.raw if kind=='provider' else self.raw+suffix)
 def test_existing_input_resize_and_close_survive_security_config_drift(self):
  from orchestration.common import Blocked
  from unittest.mock import patch
  for kind in ('trust','permission','provider'):
   with self.subTest(kind=kind):
    self.drift(kind)
    with self.assertRaises(Blocked):self.rt.check_operator()
    sequence=self.rt.flow.get('terminal',self.key)['input_sequence']+1
    with patch('studio.native_terminals.subprocess.Popen',side_effect=AssertionError('must not spawn')):
     code,value=self.control('input',sequence=sequence,text='SYNTHETIC-'+kind+'\n',generation=0)
     self.assertEqual(code,200,value);self.assertTrue(value['accepted'])
     self.assertEqual(self.control('resize',cols=90,rows=25,generation=0)[0],200)
    self.wait(lambda:'Got:SYNTHETIC-'+kind in self.manager.snapshot(self.workspace,self.key)['output'])
  code,value=self.control('close');self.assertEqual(code,200,value);self.assertTrue(value['stopped'])
  self.assertIsNotNone(self.process.poll())
 def test_drift_does_not_bypass_session_generation_or_replay_guards(self):
  self.drift()
  self.assertEqual(self.post('/api/terminal/input',dict(workspace='foreign',id=self.key,sequence=1,text='bad',generation=0))[0],400)
  self.assertEqual(self.post('/api/terminal/close',dict(workspace=self.workspace,id='1234'))[0],400)
  code,value=self.control('claim',generation=0);self.assertEqual(code,200,value)
  self.assertEqual(self.control('input',sequence=1,text='old',generation=0)[0],409)
  self.assertEqual(self.control('resize',cols=80,rows=24,generation=0)[0],409)
  self.assertEqual(self.control('claim',generation=0)[0],409)
  self.assertEqual(self.control('input',sequence=1,text='once\n',generation=1)[0],200)
  code,value=self.control('input',sequence=1,text='once\n',generation=1);self.assertEqual(code,200);self.assertTrue(value['duplicate'])
  self.assertEqual(self.control('input',sequence=1,text='different\n',generation=1)[0],409)
  self.assertEqual(self.control('input',sequence=3,text='gap\n',generation=1)[0],409)
  self.wait(lambda:'Got:once' in self.manager.snapshot(self.workspace,self.key)['output'])
  self.assertEqual(self.manager.snapshot(self.workspace,self.key)['output'].count('Got:once'),1)
  self.rt.flow.change('terminal',self.key,{'input_status':'UNKNOWN'})
  self.assertEqual(self.control('input',sequence=1,text='once\n',generation=1)[0],400)
  self.assertEqual(self.control('claim',generation=1)[0],400)
  self.assertEqual(self.control('close')[0],200)
 def test_share_only_connects_owned_pty_and_release_is_not_an_http_route(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  self.drift()
  if self.use_http:code,value=self.control('share')
  else:
   with patch('studio.native_attach.NativeAttach',return_value=SimpleNamespace(path=self.root/'SYNTHETIC-attach.json',close=lambda:None)):
    code,value=self.control('share')
  self.assertEqual(code,200,value);self.assertEqual(value['mechanism'],'same-supervised-pty')
  self.assertEqual(self.post('/api/terminal/share',dict(workspace='foreign',id=self.key))[0],400)
  self.assertIn('Approved native',self.control('release',generation=0)[1]['error'])
 def test_new_process_and_unknown_posts_stay_strict_after_drift(self):
  from unittest.mock import patch
  self.drift()
  with patch('studio.native_terminals.subprocess.Popen',side_effect=AssertionError('must not spawn')):
   for path in ('/api/terminal/prepare','/api/terminal/start','/api/prepare','/api/start','/api/chat/prepare','/api/chat/start','/api/terminal/input/unknown','/api/unknown'):
    with self.subTest(path=path):
     code,value=self.post(path,{});self.assertEqual(code,400);self.assertIn('Approved native',value['error'])
  self.assertEqual(self.post('/api/terminal/close',dict(workspace=self.workspace,id=self.key),authorized=False)[0],403)
 def test_controls_still_reject_source_policy_operator_and_executable_changes(self):
  self.drift()
  for path in (self.source_file,self.policy,self.operator,self.exe):
   original=path.read_bytes()
   try:
    path.write_bytes(original+b'\nSYNTHETIC changed identity')
    for action,data in (('input',dict(sequence=1,text='no\n',generation=0)),('resize',dict(cols=80,rows=24,generation=0)),('claim',dict(generation=0)),('share',{}),('close',{})):
     with self.subTest(path=path.name,action=action):self.assertEqual(self.control(action,**data)[0],400)
   finally:path.write_bytes(original)
  self.assertEqual(self.rt.flow.get('terminal',self.key)['input_sequence'],0)


class OwnedTerminalControlHTTPTests(OwnedTerminalControlRouteTests):
 """Run in the outer host: real loopback HTTP and the same controlled OS PTY."""
 use_http=True
