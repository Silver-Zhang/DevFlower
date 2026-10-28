"""Direct Codex configuration identity regressions; all transports are synthetic."""
import json
import tempfile
import tomllib
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orchestration.common import Blocked
from studio.model_registration import ModelRegistration, direct_environment_identity, profile_identity
from studio.local_codex_adapter import LocalCodex
from studio.native_local import key as session_key
from studio.server import Conflict

SID='00000000-0000-4000-8000-000000000038'
RAW=b'model="default-a"\nmodel_reasoning_effort="high"\nsandbox_mode="read-only"\n[model_providers.relay]\nbase_url="https://example.invalid"\nmodel="nested"\n'

class DirectPreferenceIdentityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.config_root=self.root/'.codex';self.config_root.mkdir()
  self.config=self.config_root/'config.toml';self.config.write_bytes(RAW)
  self.exe=self.root/'fake-executable';self.exe.write_text('synthetic executable');self.exe.chmod(0o700)
  self.auth=self.root/'auth';self.auth.write_text('synthetic-auth')
  self.code=self.root/'policy';self.code.write_text('synthetic-policy')
  self.artifacts=self.root/'artifacts';self.artifacts.mkdir()
  self.protected=self.root/'protected';self.protected.mkdir()
  self.b=dict(executable=str(self.exe),home=str(self.root),config_root=str(self.config_root),provider='codex',execution_backend='native-local',model='explicit-model',effort='high',role='reviewer',project='project',session_id=SID,conversation_format='text',native_interaction='ordinary',native_protected=[str(self.protected)],readonly_files=[[str(self.auth),'.codex/auth.json'],[str(self.code),'policy']],subagents='disabled',timeout_seconds=10,execution_mode='Ask',direct_environment_version=2,direct_environment_scope='native-codex-ordinary')
  self.b['direct_environment_sha256']=direct_environment_identity(self.b)
 def mutate_preferences(self):self.config.write_bytes(RAW.replace(b'default-a',b'default-b').replace(b'effort="high"',b'effort="low"'))
 def check(self,b=None):ModelRegistration(SimpleNamespace()).require_direct(b or self.b)
 def transport(self,during=None,b=None):
  calls=[];sent=[];outer=self
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,method,params):
    calls.append(method)
    if during:during()
    return {'config':tomllib.loads(outer.config.read_text())}
  events=[dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic answer')),dict(type='turn.completed',usage={})]
  raw=('\n'.join(json.dumps(x) for x in events)+'\n').encode()
  def execute(argv,*a,**kw):
   sent.append(argv);kw['on_stdout'](raw)
   return dict(exit=0,timeout=False,cancelled=False,overflow=False),raw,b''
  with ExitStack() as stack:
   stack.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC))
   for name in ('require_exec_capability','require_sandbox_capability','require_native_write_capability'):
    stack.enter_context(patch('studio.native_codex_exec_probe.'+name))
   stack.enter_context(patch('studio.native_codex_exec.check_protected_hardlinks'))
   stack.enter_context(patch('orchestration.process.execute',side_effect=execute))
   result=LocalCodex(b or self.b,dict(step_timeout_seconds=10,max_log_bytes=100000)).start('synthetic task',str(self.root),self.artifacts)
  return result,calls,sent
 def assert_explicit(self,sent):
  self.assertEqual(len(sent),1);argv=sent[0]
  self.assertEqual(argv[argv.index('--model')+1],'explicit-model')
  self.assertIn('model_reasoning_effort="high"',argv)
 def test_preferences_before_start_and_preflight_use_explicit_values(self):
  self.mutate_preferences();self.check();_,_,sent=self.transport();self.assert_explicit(sent)
  self.config.write_bytes(RAW);self.check();_,_,sent=self.transport(self.mutate_preferences);self.assert_explicit(sent)
 def test_legacy_untagged_and_version_one_reject_preferences(self):
  for version in (None,1):
   with self.subTest(version=version):
    self.config.write_bytes(RAW);b=dict(self.b);b.pop('direct_environment_scope');b.pop('direct_environment_version')
    if version is not None:b['direct_environment_version']=version
    b['direct_environment_sha256']=profile_identity(b);self.mutate_preferences()
    with self.assertRaises(Conflict):self.check(b)
    with self.assertRaises(Blocked):self.transport(b=b)
    self.config.write_bytes(RAW)
    with self.assertRaises(Blocked):self.transport(during=self.mutate_preferences,b=b)
 def test_unsafe_binding_and_bad_version_rejected(self):
  for change in (dict(direct_environment_version=3),dict(direct_environment_version=True),dict(provider='claude'),dict(provider='copilot'),dict(execution_backend='isolated'),dict(native_interaction='legacy'),dict(model=''),dict(model='unknown'),dict(effort=' '),dict(direct_environment_scope='other'),dict(config_root=str(self.root/'other'))):
   with self.subTest(change=change),self.assertRaises((Blocked,ValueError)):direct_environment_identity(dict(self.b,**change))
  with self.assertRaises(Blocked):self.transport(b=dict(self.b,native_interaction=None))
 def test_invalid_config_rejected(self):
  for raw in (b'model = 1',b'model_reasoning_effort = " "',b'model = ',b'\xff'):
   with self.subTest(raw=raw):
    self.config.write_bytes(raw)
    with self.assertRaises(Blocked):self.check()
 def test_sensitive_changes_are_rejected(self):
  changes=[lambda:self.config.write_bytes(RAW.replace(b'example.invalid',b'other.invalid')),lambda:self.config.write_bytes(RAW.replace(b'read-only',b'workspace-write')),lambda:self.config.write_bytes(RAW.replace(b'"nested"',b'"changed"')),lambda:self.auth.write_text('changed auth'),lambda:self.code.write_text('changed code'),lambda:self.exe.write_text('changed exe')]
  for change in changes:
   with self.subTest(change=change):
    self.config.write_bytes(RAW);self.auth.write_text('synthetic-auth');self.code.write_text('synthetic-policy');self.exe.write_text('synthetic executable')
    self.b['direct_environment_sha256']=direct_environment_identity(self.b);change()
    with self.assertRaises(Conflict):self.check()
    with self.assertRaises(Blocked):self.transport()
 def test_config_also_readonly_remains_raw(self):
  self.b['readonly_files'].append([str(self.config),'.codex/config.toml'])
  self.b['direct_environment_sha256']=direct_environment_identity(self.b);self.mutate_preferences()
  with self.assertRaises(Conflict):self.check()
 def test_comments_and_format_are_semantic_but_nested_model_is_bound(self):
  self.config.write_bytes(b'# comment\n'+RAW.replace(b'model="default-a"',b'model = "another"'))
  self.check()
  self.config.write_bytes(RAW.replace(b'"nested"',b'"different"'))
  with self.assertRaises(Conflict):self.check()
 def test_terminal_rebinding_versions_keep_session_identity(self):
  from devflower_native_terminal import native_cli_argv
  before=session_key(self.b);raw=profile_identity(self.b);v2=direct_environment_identity(self.b)
  self.mutate_preferences();self.assertEqual(session_key(self.b),before)
  self.assertEqual(direct_environment_identity(self.b),v2)
  legacy=dict(self.b);legacy.pop('direct_environment_version');legacy.pop('direct_environment_scope')
  self.assertNotEqual(direct_environment_identity(legacy),raw)
  self.assertEqual(session_key(legacy),before)
  for binding in (self.b,legacy):
   argv=native_cli_argv(dict(binding,native_interactive=True),SID)
   self.assertEqual(argv[argv.index('--model')+1],'explicit-model')
   self.assertIn('model_reasoning_effort="high"',argv)
 def test_runtime_signs_new_version_and_legacy_rebinding_does_not_migrate(self):
  from studio.workbench_runtime import WorkbenchRuntime
  from studio.graph import Invalid
  profile=dict(provider='codex',model='explicit-model',effort='high',executable=str(self.exe),readonly_files=self.b['readonly_files'])
  class Flow:
   def get(self,*args):raise Invalid('no synthetic record')
  rt=SimpleNamespace(session_center=SimpleNamespace(workspace=lambda w:dict(path=str(self.root))),
    operator=dict(profiles={'p':profile},native_local={'codex':True}),flow=Flow(),native=self.root,
    policy=dict(models={}),policy_sha='policy',policy_file=self.root/'policy',validate_source=lambda *a,**k:None)
  native={k:v for k,v in self.b.items() if k not in ('direct_environment_version','direct_environment_scope','direct_environment_sha256','native_interaction')}
  node=dict(profile='p',model='explicit-model',effort='high',mode='Ask',direct_environment_version=999)
  with patch('studio.model_registration.ModelRegistration.choices',return_value={}),patch('studio.native_local.bind',return_value=native),patch('studio.agent_extensions.AgentExtensions.bind'):
   new=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True)['binding']
   old=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True,direct_environment_version=1)['binding']
   self.assertEqual(new['direct_environment_version'],2);self.assertNotIn('direct_environment_version',old)
   new_pin=new['direct_environment_sha256'];old_pin=old['direct_environment_sha256'];self.mutate_preferences()
   new_again=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True)['binding']
   old_again=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True,direct_environment_version=1)['binding']
   self.assertEqual(new_again['direct_environment_sha256'],new_pin)
   self.assertNotEqual(old_again['direct_environment_sha256'],old_pin)
   self.assertEqual(session_key(new),session_key(new_again))
   with self.assertRaises(Blocked):WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True,direct_environment_version=999)
 def test_sensitive_change_during_preflight_is_refused(self):
  with self.assertRaises(Blocked):self.transport(lambda:self.config.write_bytes(RAW.replace(b'example.invalid',b'changed.invalid')))
 def test_symlink_and_oversized_config_are_rejected(self):
  self.config.write_bytes(b' ' * 200001)
  with self.assertRaises(Blocked):self.check()
  self.config.unlink();self.config.symlink_to(self.code)
  with self.assertRaises(ValueError):self.check()
