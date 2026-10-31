"""Direct workflow binding and preparation; no CLI, network or Git."""
import tempfile
from pathlib import Path
from types import MethodType,SimpleNamespace
import unittest
from unittest.mock import patch
from orchestration.common import Blocked
from studio.agent_extensions import AgentExtensions,_managed_help as real_managed_help
from studio.graph import Invalid
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_store import WorkbenchStore

class ManagedWorkflowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name);self.root=root
  executable=root/'fixture-cli';executable.write_text('synthetic')
  self.profile=dict(provider='codex',model='m',effort='high',executable=str(executable),readonly_files=[])
  flow=WorkbenchStore(root/'flow.sqlite');home=root/'home';home.mkdir()
  self.rt=SimpleNamespace(session_center=SimpleNamespace(workspace=lambda w:dict(id=w,path=str(root))),operator=dict(profiles={'p':self.profile},native_local={'codex':dict(home=str(home),config_root=str(home/'.codex'))}),flow=flow,native=root,storage=root,policy={'models':{}},policy_sha='policy',operator_sha256='operator',candidate_sha256='candidate',policy_file=root/'policy',validate_source=lambda *a,**k:{'kind':'workspace'},source_view_hash=lambda *a:'source',check_operator=lambda:None,db=SimpleNamespace(prepare=lambda value:value))
  self.rt.node_binding=MethodType(WorkbenchRuntime.node_binding,self.rt)
  self.node=dict(id='agent',kind='agent',name='Agent',profile='p',provider='codex',model='m',effort='high',mode='Ask',permission='read',subagents='readonly',round_prompt='task')
  self.native=dict(self.profile,home=str(home),config_root=str(home/'.codex'),execution_backend='native-local',project='w',session_id=None,readonly_files=[])
  self.stack=__import__('contextlib').ExitStack();self.addCleanup(self.stack.close)
  self.stack.enter_context(patch('studio.model_registration.ModelRegistration.choices',return_value={}))
  self.stack.enter_context(patch('studio.model_registration.direct_environment_identity',return_value='environment'))
  self.stack.enter_context(patch('studio.native_local.bind',return_value=self.native))
  self.stack.enter_context(patch('studio.agent_extensions.capabilities',return_value=dict(skills=True,subagents=True,source='synthetic CLI')))
  self.stack.enter_context(patch('studio.agent_extensions._managed_help',return_value=True))
  self.stack.enter_context(patch('pathlib.Path.home',return_value=home))
 def test_catalog_direct_workflow_binding_and_frozen_graph(self):
  catalog=AgentExtensions(self.rt).catalog('w','p');self.assertTrue(catalog['capabilities']['subagents']);self.assertIn('DevFlower',catalog['notice'])
  value=self.rt.node_binding('w',self.node,admission=False,direct=True,managed_workflow=True)
  self.assertEqual(value['binding']['subagents'],'readonly');self.assertEqual((value['binding']['model'],value['binding']['effort']),('m','high'))
  graph=dict(version=3,name='fixture',nodes=[dict(id='start',kind='start',name='start'),self.node,dict(id='result',kind='result',name='result')],edges=[dict(id='e1',source='start',target='agent'),dict(id='e2',source='agent',target='result')])
  prepared=WorkbenchRuntime.prepare_flow(self.rt,dict(workspace='w',interaction='click',graph=graph,task='fixture'))
  self.assertEqual(prepared['bindings']['agent']['binding']['subagents'],'readonly');self.assertEqual(prepared['graph']['nodes'][1]['subagents'],'readonly')
  self.rt.flow.save('draft',prepared['graph'],None,workspace='w')
  self.assertEqual(self.rt.flow.get('draft','draft')['graph']['nodes'][1]['subagents'],'readonly')
 def test_legacy_standalone_write_and_missing_cli_capability_refused(self):
  for kw in (dict(direct=False,managed_workflow=True),dict(direct=True,managed_workflow=False)):
   with self.subTest(kw=kw),self.assertRaises(Blocked):self.rt.node_binding('w',self.node,admission=False,**kw)
  with self.assertRaises((Blocked,Invalid)):self.rt.node_binding('w',dict(self.node,mode='Agent',permission='workspace-write'),admission=False,direct=True,managed_workflow=True)
  with patch('studio.agent_extensions.capabilities',return_value=dict(skills=False,subagents=False,reason='CLI unavailable')):
   self.assertFalse(AgentExtensions(self.rt).catalog('w','p')['capabilities']['subagents'])
   with self.assertRaisesRegex(Blocked,'CLI unavailable'):self.rt.node_binding('w',self.node,admission=False,direct=True,managed_workflow=True)
  with patch('studio.agent_extensions._managed_help',return_value=False):
   self.assertFalse(AgentExtensions(self.rt).catalog('w','p')['capabilities']['subagents'])
   with self.assertRaises(Blocked):self.rt.node_binding('w',self.node,admission=False,direct=True,managed_workflow=True)
 def test_other_provider_native_is_refused(self):
  self.profile['provider']='claude';self.rt.operator['native_local']={'claude':{}}
  with patch('studio.native_local.bind',return_value=dict(self.native,provider='claude')):
   with self.assertRaises(Blocked):self.rt.node_binding('w',dict(self.node,provider='claude'),admission=False,direct=True,managed_workflow=True)
 def test_standalone_without_journal_and_delivery_graph_are_refused(self):
  from studio.local_codex_adapter import LocalCodex
  binding=dict(self.native,native_interaction='ordinary',role='reviewer',execution_mode='Ask',subagents='readonly')
  with patch('studio.local_codex_adapter.CodexRPC') as rpc:
   with self.assertRaises(Blocked):LocalCodex(binding,dict(step_timeout_seconds=20,max_log_bytes=10000)).start('task',str(self.root),self.root)
   rpc.assert_not_called()
  graph=dict(version=3,name='fixture',nodes=[dict(id='start',kind='start',name='start'),self.node,dict(id='delivery',kind='delivery',name='delivery'),dict(id='result',kind='result',name='result')],edges=[dict(id='e1',source='start',target='agent'),dict(id='e2',source='agent',target='delivery'),dict(id='e3',source='delivery',target='result')])
  with self.assertRaises(Blocked):WorkbenchRuntime.prepare_flow(self.rt,dict(workspace='w',interaction='click',graph=graph,task='fixture'))
 def test_cli_help_probe_is_bounded_and_fails_closed(self):
  _managed_help=real_managed_help
  _managed_help.cache_clear();self.addCleanup(_managed_help.cache_clear)
  with patch('studio.agent_extensions.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'--ignore-user-config --ignore-rules --strict-config --json')) as process:
   self.assertTrue(_managed_help(str(self.root/'fixture-cli'),1,2))
   self.assertEqual(process.call_args.args[0][1:],['exec','--help']);self.assertEqual(process.call_args.kwargs['timeout'],12)
  with patch('studio.agent_extensions.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'--json')):
   self.assertFalse(_managed_help(str(self.root/'fixture-cli'),2,2))
