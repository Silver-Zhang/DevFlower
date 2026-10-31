"""Synthetic stdio protocol and host runner; no actual provider or socket."""
import io
import json
import os
import threading
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestration.common import Blocked,canonical
from orchestration.store import process_identity
from studio import managed_child_bridge as bridge
from studio.managed_children import OwnedChildren,OwnedEndpoint,_descendant,public_result
from studio.native_codex_exec import protected_paths
from studio.workbench_scheduler import Journal
from studio.workbench_store import WorkbenchStore
from test_managed_child_reservation import ReservationTests

class BridgeTests(unittest.TestCase):
 def test_metadata_is_stripped_before_dispatch_and_unknown_fields_rejected(self):
  seen=[];meta={'progressToken':'secret'}
  req=dict(jsonrpc='2.0',id=1,method='tools/list',params={'_meta':meta})
  self.assertEqual(bridge.handle(req,lambda x:seen.append(x))['result']['tools'][0]['name'],'delegated_readonly')
  call=dict(jsonrpc='2.0',id=2,method='tools/call',params={'_meta':meta,'name':'delegated_readonly','arguments':{'slot':1,'task':'inspect'}})
  sink=io.BytesIO();bridge.serve(io.BytesIO(canonical(call)+b'\n'),sink,lambda x:(seen.append(x) or {'ok':True,'result':'ok'}))
  self.assertEqual(seen,[{'slot':1,'task':'inspect'}]);self.assertFalse(json.loads(sink.getvalue())['result']['isError'])
  for params in ({'_meta':1},{'_meta':None},{'_meta':{},'extra':1}):
   with self.subTest(params=params),self.assertRaises(ValueError):bridge.handle(dict(req,params=params),lambda x:self.fail())
  for params in ({**call['params'],'extra':1},{**call['params'],'arguments':{'slot':1,'task':'x','model':'bad'}}):
   with self.subTest(params=params),self.assertRaises(ValueError):bridge.handle(dict(call,params=params),lambda x:self.fail())
 def test_invalid_first_json_with_diagnostic_is_bounded_error(self):
  class D:
   def record(self,*args):self.row=args
  d=D();out=io.BytesIO();bridge.serve(io.BytesIO(b'invalid\n'),out,lambda x:self.fail(),d)
  self.assertEqual(json.loads(out.getvalue())['error']['code'],-32600);self.assertEqual(d.row[2],'error')
 def test_deadline_validation(self):
  with patch('studio.managed_child_bridge.time.monotonic',return_value=100):
   self.assertEqual(bridge.remaining(501),401);self.assertEqual(bridge.remaining(101),1)
   for value in (float('nan'),float('inf'),100,None,True,700000):
    with self.subTest(value=value),self.assertRaises(Blocked):bridge.remaining(value)
 def test_descriptor_v1_compatibility_and_v2_deadlines(self):
  import tempfile
  with tempfile.TemporaryDirectory() as root,patch('studio.managed_child_bridge.time.monotonic',return_value=100):
   path=Path(root)/'endpoint.json';base=dict(version=1,socket='df-child-'+'a'*32,token='a'*43,server_pid=os.getpid())
   path.write_bytes(canonical(base));os.chmod(path,0o600);self.assertEqual(bridge.descriptor(path)['version'],1)
   for deadline in (501,101):
    path.write_bytes(canonical(dict(base,version=2,deadline=deadline)));self.assertEqual(bridge.descriptor(path)['deadline'],deadline)
   for deadline in (100,None,float('nan')):
    path.write_text(json.dumps(dict(base,version=2,deadline=deadline)))
    with self.subTest(deadline=deadline),self.assertRaises(Blocked):bridge.descriptor(path)
 def test_read_line_checks_remaining_for_each_read(self):
  class Sock:
   def __init__(self):self.timeouts=[]
   def settimeout(self,t):self.timeouts.append(t)
  sock=Sock()
  with patch('studio.managed_child_bridge.time.monotonic',side_effect=[100,100,100,100]):
   self.assertEqual(bridge.read_line(io.BytesIO(b'ok\n'),sock,501),b'ok\n')
  self.assertEqual(sock.timeouts,[401,401,401])
 def test_remote_call_uses_parent_deadline_without_retry(self):
  import struct
  class Stream:
   def __init__(self):self.data=io.BytesIO(canonical({'ok':True,'result':'done'})+b'\n')
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def write(self,data):pass
   def flush(self):pass
   def read(self,n):return self.data.read(n)
  class Sock:
   def __init__(self):self.timeouts=[];self.connects=0
   def settimeout(self,value):self.timeouts.append(value)
   def connect(self,value):self.connects+=1
   def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid(),0)
   def makefile(self,*args):return Stream()
   def close(self):pass
  sock=Sock();data=dict(version=2,deadline=501,socket='df-child-'+'a'*32,token='a'*43,server_pid=os.getpid())
  with patch('studio.managed_child_bridge.descriptor',return_value=data),patch('studio.managed_child_bridge.socket.socket',return_value=sock),patch('studio.managed_child_bridge.time.monotonic',return_value=100):
   self.assertTrue(bridge.remote_call('fixture',dict(slot=1,task='read'))['ok'])
  self.assertEqual(sock.connects,1);self.assertTrue(all(value==401 for value in sock.timeouts))
 def test_initialized_empty_params_and_invalid_notifications(self):
  self.assertIsNone(bridge.handle(dict(jsonrpc='2.0',method='notifications/initialized',params={}),lambda _:self.fail('dispatch')))
  for req in (dict(jsonrpc='2.0',method='notifications/initialized',params={'x':1}),dict(jsonrpc='2.0',id=1,method='notifications/initialized',params={}),dict(jsonrpc='2.0',method='notifications/other')):
   with self.subTest(req=req),self.assertRaises(ValueError):bridge.handle(req,lambda _:self.fail('dispatch'))
 def test_private_diagnostic_is_bounded_and_never_records_request_values(self):
  import tempfile
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'endpoint.json';diag=bridge.Diagnostic(path)
   secret='private-task-and-token';req=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='delegated_readonly',arguments=dict(slot=1,task=secret),**{secret:secret}))
   raw=canonical(req)+b'\n';sink=io.BytesIO()
   bridge.serve(io.BytesIO(raw),sink,lambda _:self.fail('extra fields must not dispatch'),diag)
   for _ in range(40):diag.record(raw,req,'error')
   diag.close();data=(Path(root)/'bridge-diagnostic.jsonl').read_text()
   self.assertNotIn(secret,data);self.assertEqual(len(data.splitlines()),32)
   self.assertEqual(os.stat(Path(root)/'bridge-diagnostic.jsonl').st_mode&0o777,0o600)
   self.assertEqual(json.loads(data.splitlines()[0])['status'],'error')
 def test_diagnostic_refuses_existing_file_and_unsafe_parent(self):
  import tempfile
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'endpoint.json';(Path(root)/'bridge-diagnostic.jsonl').symlink_to(path)
   with self.assertRaises(OSError):bridge.Diagnostic(path)
   (Path(root)/'bridge-diagnostic.jsonl').unlink();os.chmod(root,0o755)
   with self.assertRaises(Blocked):bridge.Diagnostic(path)
 def test_initialize_list_call_and_notification(self):
  events=[]
  requests=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),dict(jsonrpc='2.0',method='notifications/initialized'),dict(jsonrpc='2.0',id=2,method='tools/list'),dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='delegated_readonly',arguments=dict(slot=1,task='inspect')))]
  source=io.BytesIO(b''.join(canonical(x)+b'\n' for x in requests));sink=io.BytesIO()
  bridge.serve(source,sink,lambda args:(events.append(args) or dict(ok=True,result='synthetic')))
  rows=[json.loads(x) for x in sink.getvalue().splitlines()]
  self.assertEqual(len(rows),3);self.assertEqual(rows[1]['result']['tools'][0]['inputSchema']['additionalProperties'],False)
  self.assertEqual(events,[dict(slot=1,task='inspect')]);self.assertFalse(rows[2]['result']['isError'])
 def test_invalid_requests_never_call(self):
  cases=[dict(slot=1,task='x',model='other'),dict(slot=True,task='x'),dict(slot=3,task='x'),dict(slot=1,task='x'*8193),dict(slot=1,task='')]
  for case in cases:
   with self.subTest(case=case),self.assertRaises(ValueError):bridge.arguments(case)
  calls=[];source=io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"tools/call"}\n');sink=io.BytesIO()
  bridge.serve(source,sink,lambda args:calls.append(args));self.assertEqual(calls,[]);self.assertIn('error',json.loads(sink.getvalue()))
 def test_overlong_input_stops_without_dispatch(self):
  calls=[];sink=io.BytesIO();bridge.serve(io.BytesIO(b'x'*(bridge.MAX_LINE+1)+b'\n'),sink,lambda x:calls.append(x))
  self.assertFalse(calls);self.assertIn('error',json.loads(sink.getvalue()))
 def test_live_owned_process_chain_and_wrong_parent(self):
  import subprocess,sys
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
  try:
   parent=process_identity(os.getpid());self.assertTrue(_descendant(child.pid,parent))
   wrong=dict(parent,start='0');self.assertFalse(_descendant(child.pid,wrong))
  finally:child.terminate();child.wait(timeout=3)
 def test_two_tool_requests_run_concurrently(self):
  barrier=threading.Barrier(2);seen=[]
  def call(value):seen.append(value['slot']);barrier.wait(1);return dict(ok=True,result='done')
  requests=[dict(jsonrpc='2.0',id=i,method='tools/call',params=dict(name='delegated_readonly',arguments=dict(slot=i,task='inspect'))) for i in (1,2)]
  sink=io.BytesIO();bridge.serve(io.BytesIO(b''.join(canonical(v)+b'\n' for v in requests)),sink,call)
  rows=[json.loads(line) for line in sink.getvalue().splitlines()]
  self.assertCountEqual(seen,[1,2]);self.assertEqual(len(rows),2);self.assertTrue(all(not row['result']['isError'] for row in rows))
 def test_child_result_excludes_private_fields_and_marks_excerpt(self):
  row=dict(id='fixture',slot=1,state='COMPLETED',session_id='sid',parent_process={'secret':'private'},binding_sha256='private',result=dict(reply='中'*5000))
  result=public_result(row);self.assertNotIn('private',json.dumps(result,ensure_ascii=False));self.assertFalse(result['complete'])
  self.assertLessEqual(len(result['text'].encode()),12000);self.assertIn('节选',result['notice'])

class RunnerTests(ReservationTests):
 def setUp(self):
  super().setUp();root=Path(self.tmp.name);home=root/'home';home.mkdir();config=home/'.codex';config.mkdir()
  chat=self.db.get('node-session','chat');chat['cwd']=str(root)
  chat['binding'].update(home=str(home),config_root=str(config),local_lock_root=str(root/'locks'))
  self.db.put('node-session','chat',chat);self.binding=chat['binding'];self.rt=SimpleNamespace(storage=root,native=root/'native',operator_file=root/'operator',policy_file=root/'policy',protected=[],operator={'profiles':{},'native_local':{}},policy={},db=WorkbenchStore(root/'sessions.sqlite'),source_lock=lambda cwd:root/'source.lock')
  self.rt.source_view_hash=lambda cwd,view:'fixture-source';run=self.db.get('run','r');run['source_view']=dict(root=str(root));self.db.put('run','r',run)
  effective=dict(self.binding,native_interaction='ordinary',_metadata_guard=str(root));effective['native_protected']=protected_paths(self.rt,effective)
  self.journal.s.rt=self.rt;self.effective=effective
 def test_actual_runner_fixed_binding_and_idempotent_slot(self):
  child_sid='00000000-0000-4000-8000-000000000002';calls=[]
  class SyntheticCodex:
   def __init__(self,binding,limits):self.binding=binding;self.limits=limits
   def start(self,task,cwd,artifacts,store,run):
    calls.append((self.binding,task,cwd));store.process_started=True
    op=store.intent(run,'PROCESS',dict(context={'provider':'codex'}));store.finish(op,'RUNNING',process=process_identity(os.getpid()))
    request=store.intent(run,'AGENT_REQUEST',dict(requested_session=None));store.finish(request,'AGENT_SESSION',actual_session=child_sid)
    return dict(session_id=child_sid,report={'reply':'synthetic'},usage='unknown',requested_model=self.binding['model'],requested_effort=self.binding['effort'],process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+10)
  with patch('studio.managed_children.LocalCodex',SyntheticCodex):
   result=manager.call(1,'inspect');again=manager.call(1,'inspect')
  self.assertEqual(result['state'],'COMPLETED');self.assertEqual(again['session_id'],child_sid);self.assertEqual(len(calls),1)
  self.assertEqual(self.db.get('native-step',result['id'])['status'],'COMPLETED')
  childrow=self.db.get('activation',result['child_activation']);childchat=self.db.get('node-session',result['child_chat'])
  self.assertEqual(childrow['name'],'node · 子任务1');self.assertEqual(childchat['parent_activation'],'a')
  self.assertEqual(childrow['observed_session'],child_sid)
  parent=self.db.get('activation','a');self.assertEqual([e['event'] for e in parent['native_tool_activity']],['item.started','item.completed'])
  from studio.tool_activity import journal_tools
  activity=journal_tools(parent['native_tool_activity'],set(),'RUNNING');self.assertEqual(len(activity),1);self.assertEqual(activity[0]['name'],'只读子任务')
  from studio.workbench_sessions import WorkbenchSessions
  self.rt.flow=self.db;self.rt.snapshot=lambda run:dict(state='RUNNING');run=self.db.get('run','r');run['task']='fixture';self.db.put('run','r',run)
  conversation=WorkbenchSessions(self.rt).node_conversation('w','r',result['child_activation'])
  self.assertEqual(conversation['saved_answer'],'synthetic');self.assertEqual(conversation['node'],childrow['node'])
  binding=calls[0][0];self.assertEqual((binding['model'],binding['effort']),('m','high'))
  self.assertEqual(binding['subagents'],'disabled');self.assertEqual(binding['skills'],[]);self.assertIsNone(binding['session_id'])
  self.assertEqual(self.db.get('run','r')['actual_calls'],1);self.assertEqual(manager.close()['status'],'closed')
 def test_preexisting_reservation_is_not_redispatched(self):
  self.journal.reserve_managed_child(1,'inspect',self.parent)
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+10)
  with patch('studio.managed_children.LocalCodex') as transport:
   self.assertEqual(manager.call(1,'inspect')['state'],'RESERVED');transport.assert_not_called()
 def test_changed_child_source_refuses_transport(self):
  self.rt.source_view_hash=lambda cwd,view:'changed-source'
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+10)
  with patch('studio.managed_children.LocalCodex') as transport:
   with self.assertRaisesRegex(Blocked,'源码已变化'):manager.call(1,'inspect')
   transport.assert_not_called()
  row=self.db.list('managed-child')[0];self.assertEqual(row['state'],'FAILED')
  self.assertEqual(self.db.get('native-step',row['id'])['status'],'FAILED')
 def test_two_slots_run_concurrently_with_distinct_sessions(self):
  barrier=threading.Barrier(2);results=[];errors=[]
  class SyntheticCodex:
   def __init__(self,binding,limits):self.binding=binding
   def start(self,task,cwd,artifacts,store,run):
    store.process_started=True;op=store.intent(run,'PROCESS',dict(context={'provider':'codex'}));store.finish(op,'RUNNING',process=process_identity(os.getpid()))
    sid='00000000-0000-4000-8000-00000000000'+str(int(task)+2)
    req=store.intent(run,'AGENT_REQUEST',dict(requested_session=None));store.finish(req,'AGENT_SESSION',actual_session=sid)
    barrier.wait(10);return dict(session_id=sid,report={'reply':task},usage='unknown',requested_model=self.binding['model'],requested_effort=self.binding['effort'],process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+30)
  def run(slot):
   try:results.append(manager.call(slot,str(slot)))
   except Exception as exc:errors.append(exc)
  with patch('studio.managed_children.LocalCodex',SyntheticCodex):
   workers=[threading.Thread(target=run,args=(i,)) for i in (1,2)]
   for worker in workers:worker.start()
   for worker in workers:worker.join(25)
  self.assertFalse(any(worker.is_alive() for worker in workers));self.assertEqual(errors,[])
  self.assertEqual(len(results),2);self.assertEqual(len({x['session_id'] for x in results}),2)
  self.assertEqual(self.db.get('run','r')['actual_calls'],2)
  names={self.db.get('activation',x['child_activation'])['name'] for x in results}
  self.assertEqual(names,{'node · 子任务1','node · 子任务2'})
  from studio.tool_activity import journal_tools
  activity=journal_tools(self.db.get('activation','a')['native_tool_activity'],set(),'RUNNING')
  self.assertEqual(len(activity),2);self.assertEqual(len({item['call_id'] for item in activity}),2)
 def test_endpoint_private_descriptor_and_wrong_peer_or_nonce(self):
  import struct
  from test_native_attach_private import FakeListener
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+10)
  with patch('studio.managed_children.socket.socket',FakeListener):endpoint=OwnedEndpoint(manager)
  self.addCleanup(endpoint.close);endpoint.thread.join(2)
  data=bridge.descriptor(endpoint.path);self.assertEqual(data['version'],2);self.assertEqual(data['deadline'],manager.deadline)
  self.assertEqual(endpoint.path.stat().st_mode&0o777,0o600)
  output=[]
  class Stream:
   def __init__(self,request):self.request=request;self.data=io.BytesIO(canonical(request)+b'\n')
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def readline(self,n):return canonical(self.request)+b'\n'
   def read(self,n):return self.data.read(n)
   def write(self,data):output.append(data)
   def flush(self):pass
  class Client:
   def __init__(self,request):self.stream=Stream(request)
   def getsockopt(self,*a):return struct.pack('3i',os.getpid(),os.getuid(),0)
   def settimeout(self,*a):pass
   def makefile(self,*a):return self.stream
   def close(self):pass
  for request,desc in [(dict(version=1,token='wrong',arguments=dict(slot=1,task='x')),True),(dict(version=2,token=endpoint.secret,arguments=dict(slot=1,task='x')),True),(dict(version=1,token=endpoint.secret,arguments=dict(slot=1,task='x')),False)]:
   client=Client(request);endpoint.clients.add(client)
   with patch('studio.managed_children._descendant',return_value=desc),patch.object(manager,'call') as call:
    endpoint._serve(client);call.assert_not_called()
  self.assertEqual(output,[])
  moved=endpoint.root.with_name('moved-endpoint');endpoint.root.rename(moved);endpoint.root.mkdir(mode=0o700)
  marker=endpoint.root/'foreign';marker.write_text('untouched');result=endpoint.close()
  self.assertEqual(result['cleanup']['status'],'identity_changed');self.assertEqual(marker.read_text(),'untouched');self.assertEqual((moved/'endpoint.json').read_bytes(),b'')
