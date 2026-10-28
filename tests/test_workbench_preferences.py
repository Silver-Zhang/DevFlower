"""Pinned native configuration drift: readonly availability must not authorize execution."""
import copy,json,threading,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from test_workbench_runtime import fixture
from orchestration.common import save,digest,tree_hash
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_server import create_server

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
