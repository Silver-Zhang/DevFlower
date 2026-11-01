"""Ordinary workspace views never read protected subtrees."""
import os,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked

class NativeWorkspaceViewTests(unittest.TestCase):
 def test_parent_view_excludes_host_paths_and_tracks_public_files(self):
  from studio.workspace_access import native_workspace_view,native_workspace_hash
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'父 项目';root.mkdir();private=root/'权威 数据';private.mkdir();(private/'state').write_text('secret');public=root/'source.txt';public.write_text('one')
   rt=SimpleNamespace(storage=private,native=private/'native',operator_file=private/'operator',policy_file=private/'policy',protected=[private],operator={'profiles':{},'native_local':{}},policy={})
   view=native_workspace_view(rt,str(root));first=native_workspace_hash(rt,view)
   (private/'state').write_text('changed');(private/'new').write_text('private');self.assertEqual(first,native_workspace_hash(rt,view))
   public.write_text('two');self.assertNotEqual(first,native_workspace_hash(rt,view))
   with self.assertRaises(Blocked):native_workspace_view(rt,str(private))
 def test_hardlinks_cannot_smuggle_private_bytes_into_view(self):
  from studio.workspace_access import native_workspace_view,native_workspace_hash
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);private=root/'private';private.mkdir();(private/'secret').write_text('secret');os.link(private/'secret',root/'alias')
   rt=SimpleNamespace(storage=private,native=private/'native',operator_file=private/'operator',policy_file=private/'policy',protected=[private],operator={'profiles':{},'native_local':{}},policy={})
   with self.assertRaises(Blocked):native_workspace_hash(rt,native_workspace_view(rt,str(root)))
 def test_symlinks_do_not_read_their_target_and_scope_cannot_be_broad(self):
  from studio.workspace_access import native_workspace_view,native_workspace_hash
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);private=root/'private';private.mkdir();secret=private/'secret';secret.write_text('initial')
   (root/'link').symlink_to(secret)
   rt=SimpleNamespace(storage=private,native=private/'native',operator_file=private/'operator',policy_file=private/'policy',protected=[private],operator={'profiles':{},'native_local':{}},policy={})
   view=native_workspace_view(rt,str(root));before=native_workspace_hash(rt,view);secret.write_text('changed');self.assertEqual(before,native_workspace_hash(rt,view))
   for path in ('/','/home'):
    with self.assertRaises(Blocked):native_workspace_view(rt,path)

 def test_http_parent_chat_prepare_launch_and_changed_source(self):
  import json,threading
  from urllib.request import Request,urlopen
  from unittest.mock import patch
  import test_native_local
  from studio.workbench_server import create_server
  fixture=test_native_local.NativeLocalTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
  rt=fixture.rt;root=fixture.base.root;public=root/'普通 代码.txt';public.write_text('one')
  workspace=rt.open_workspace(str(root));self.assertEqual(workspace.get('execution_scope'),'native-codex-only')
  server,token=create_server(rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  base='http://127.0.0.1:'+str(server.server_port)
  def post(path,data):
   req=Request(base+path,data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   with urlopen(req) as response:return json.load(response)
  profile=rt.operator['profiles']['default-codex']
  data=dict(workspace=workspace['id'],handle='new',profile='default-codex',model=profile['model'],effort=profile['effort'],mode='Ask',permission='read',prompt='synthetic',interaction='click')
  with patch.object(rt.session_center,'_spawn',side_effect=Blocked('synthetic spawn stopped')) as spawn:
   permit=post('/api/chat/prepare',data)
   (rt.storage/'new-private-state').write_text('private')
   operation=post('/api/chat/start',dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'send'}))
   self.assertEqual(operation['state'],'FAILED');self.assertIn('no automatic retry',operation['reason']);self.assertEqual(spawn.call_count,1)
  rt.db.update('session-operation',operation['id'],dict(state='QUEUED'))
  seen=[]
  class SyntheticCLI:
   def __init__(self,provider,binding,limits):seen.append(binding)
   def start(self,prompt,cwd,artifacts,schema,journal,key):
    journal.check_before_spawn();return dict(report={'reply':'synthetic'},session_id=None)
  with patch('studio.session_center.CLI',SyntheticCLI):rt.session_center.execute(operation['id'])
  self.assertEqual(rt.session_center.operation(operation['id'],workspace['id'])['state'],'COMPLETED')
  self.assertEqual(seen[0]['native_interaction'],'ordinary');self.assertIn(str(rt.storage),seen[0]['native_protected'])
  permit=rt.session_center.prepare(data);public.write_text('two')
  from studio.server import Conflict
  with self.assertRaises(Conflict):rt.session_center.launch(permit['id'],permit['sha256'],{'action':'send'})
  permit=rt.session_center.prepare(data)
  newly_protected=root/'newly protected';newly_protected.mkdir()
  rt.protected.append(newly_protected)
  with self.assertRaises(Conflict):rt.session_center.launch(permit['id'],permit['sha256'],{'action':'send'})
  with self.assertRaises(Blocked):rt.validate_source(str(root))

 def test_http_parent_workflow_launch_worker_and_scope_change(self):
  import json,threading
  from urllib.request import Request,urlopen
  from unittest.mock import patch
  import test_native_local
  from studio.workbench_server import create_server
  from studio.workbench_examples import examples
  from studio.workbench_scheduler import Scheduler
  fixture=test_native_local.NativeLocalTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
  rt=fixture.rt;root=fixture.base.root;(root/'ordinary.txt').write_text('synthetic');w=rt.open_workspace(str(root))
  hidden=rt.storage/'hidden-test';hidden.write_text('synthetic private')
  self.assertFalse(rt.attachments(str(root),[str(hidden)])[0]['accessible'])
  self.assertTrue(rt.attachments(str(root),['ordinary.txt'])[0]['accessible'])
  graph=examples(rt.operator['profiles'])[4]
  server,token=create_server(rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  base='http://127.0.0.1:'+str(server.server_port)
  def post(path,data):
   req=Request(base+path,data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   with urlopen(req) as response:return json.load(response)
  with patch.object(rt,'spawn') as spawn:
   permit=post('/api/prepare',dict(workspace=w['id'],graph=graph,task='synthetic',interaction='click'))
   (rt.storage/'private-new-state').write_text('synthetic')
   started=post('/api/start',dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'start'}));run=started['run']['id'];self.assertEqual(spawn.call_count,1)
  row=rt.flow.get('run',run);self.assertEqual(row['source_view']['kind'],'native-workspace-view-v1')
  with patch('studio.workbench_runtime.alive',return_value=True):
   rt.control(run,'pause');self.assertTrue(rt.flow.get('run',run)['paused'])
   rt.control(run,'resume');self.assertFalse(rt.flow.get('run',run)['paused'])
  from orchestration.common import canonical
  chat='synthetic-chat';activation='synthetic-activation';sid='00000000-0000-4000-8000-000000000001'
  selected=next(iter(row['bindings'].values()))
  rt.flow.put('node-session',chat,dict(id=chat,workspace=w['id'],cwd=str(root),binding=selected['binding']))
  rt.flow.put('activation',activation,dict(id=activation,run=run,state='RUNNING'))
  receipt=rt.storage/'flow-artifacts'/run/activation/'completion.json';receipt.parent.mkdir(parents=True)
  receipt.write_bytes(canonical(dict(run=run,activation=activation,chat_key=chat,output={'answer':'synthetic'},session_id=sid,source_after=rt.source_view_hash(str(root),row['source_view']))))
  with patch('studio.workbench_runtime.alive',return_value=False),patch('studio.workbench_runtime.group_alive',return_value=False),patch.object(rt,'native_snapshot',return_value={}),patch.object(rt,'spawn') as resumed:
   rt.control(run,'resume');self.assertEqual(resumed.call_count,1);self.assertEqual(rt.flow.get('activation',activation)['state'],'COMPLETED')
  def synthetic(node,key,inputs,prefix,iteration):return {'answer':'synthetic','result':{}}
  Scheduler(rt,run,executor=synthetic).run_graph();self.assertEqual(rt.flow.get('run',run)['state'],'COMPLETED')
  with self.assertRaises(Blocked):rt.prepare_flow(dict(workspace=w['id'],graph=graph,task='strict',interaction='legacy'))
  permit=rt.prepare_flow(dict(workspace=w['id'],graph=graph,task='synthetic',interaction='click'))
  (root/'ordinary.txt').write_text('changed')
  from studio.server import Conflict
  with self.assertRaises(Conflict):rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})

 def test_parent_terminal_binds_same_view_and_does_not_restart(self):
  from unittest.mock import patch
  import subprocess
  import test_native_local
  from native_protocol_mechanics import NativeTerminals
  fixture=test_native_local.NativeLocalTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
  rt=fixture.rt;root=fixture.base.root;w=rt.open_workspace(str(root));binding=dict(fixture.binding,runtime_profile='default-codex')
  sid='00000000-0000-4000-8000-000000000001';row={'native':{'session_id':sid,'cwd':str(root),'sha256':'a'*64}}
  manager=NativeTerminals(rt,SimpleNamespace(busy=lambda *a:False));self.addCleanup(manager.shutdown)
  selected=dict(binding=binding,cwd=str(root),native_sha256='a'*64)
  with patch.object(rt.session_center,'resolve',return_value=(w,row)),patch.object(rt.session_center,'_binding',return_value=(binding,None)),patch.object(rt,'node_binding',return_value=selected),patch('studio.model_registration.ModelRegistration.require_direct'):
   permit=manager.prepare(dict(workspace=w['id'],handle='synthetic',interaction='click'))
   (rt.storage/'late-state').write_text('synthetic')
   real=subprocess.Popen;terminal_calls=[]
   def spawn(argv,**kw):
    if argv[:3]==[sys.executable,'-B','-I']:return real(argv,**kw)
    terminal_calls.append(argv);return real([sys.executable,'-B','-c','import time;time.sleep(2)'],**kw)
   with patch('studio.native_terminals.subprocess.Popen',side_effect=spawn) as call:
    request=dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'open-terminal'},cols=80,rows=24)
    first=manager.start(request);second=manager.start(request)
   self.assertEqual(first['id'],second['id']);self.assertEqual(len(terminal_calls),1)
   saved=rt.flow.get('terminal',first['id']);self.assertEqual(saved['source_view']['kind'],'native-workspace-view-v1')
   manager.close(w['id'],first['id'])
