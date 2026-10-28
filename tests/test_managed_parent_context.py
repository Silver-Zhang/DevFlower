"""Actual Journal publication with fake native transport and fake socket."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestration.common import Blocked
from orchestration.store import process_identity
from studio.local_codex_adapter import LocalCodex
from studio.managed_children import OwnedChildren
from test_managed_child_bridge import RunnerTests
from test_native_attach_private import FakeListener
from test_native_probe_lock import RPC

SID='00000000-0000-4000-8000-000000000001'
RAW=('\n'.join(json.dumps(x) for x in [dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})])+'\n').encode()

class ParentContextTests(RunnerTests):
 def test_effort_error_is_visible_without_sensitive_detail_on_actual_failure_path(self):
  from studio.local_codex_adapter import _stderr_diagnostic
  secret='credential-sentinel'
  for raw in (b'Error: unsupported model_reasoning_effort high for model fixture',b'HTTP 400 invalid parameter reasoning.effort unsupported'):
   self.assertIn('推理强度',_stderr_diagnostic(raw,[]))
  for raw in (b'reasoning: private chain must never be displayed',b'{"error":"unsupported reasoning.effort", "secret":"credential-sentinel"}',b'thinking chain unsupported reasoning.effort'):
   self.assertEqual(_stderr_diagnostic(raw,[]),'')
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=None,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  b=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));from studio.native_codex_exec import protected_paths
  b['native_protected']=protected_paths(self.rt,b);calls=[]
  def execute(*args,**kwargs):
   calls.append(1);return dict(exit=1,timeout=False,overflow=False,cancelled=False),json.dumps({'type':'error','message':'unsupported model_reasoning_effort '+secret}).encode()+b'\n',b''
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks'),patch('studio.managed_children.socket.socket',FakeListener),patch('orchestration.process.execute',side_effect=execute):
   with self.assertRaises(Blocked) as error:LocalCodex(b,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('synthetic',str(Path(self.tmp.name)),Path(self.tmp.name),store=self.journal,run='r')
  self.assertIn('推理强度',str(error.exception));self.assertNotIn(secret,str(error.exception));self.assertEqual(calls,[1])
 def test_mcp_timeouts_use_remaining_parent_budget(self):
  from studio.managed_children import ManagedContext
  from studio.native_codex_exec import managed_mcp_args
  context=ManagedContext.__new__(ManagedContext);context.closed=threading.Event();context.deadline=time.monotonic()+130;context.path=Path('/private/endpoint.json')
  args=managed_mcp_args(context)
  self.assertEqual([x for x in args if 'supports_parallel_tool_calls=' in x],['mcp_servers.devflower_managed.supports_parallel_tool_calls=true'])
  self.assertEqual([x for x in args if 'approval_mode=' in x],['mcp_servers.devflower_managed.tools.delegated_readonly.approval_mode="approve"'])
  with self.assertRaises(Blocked):managed_mcp_args(SimpleNamespace(closed=threading.Event(),deadline=time.monotonic()+10,path=Path('/private/endpoint.json')))
  for key in ('startup_timeout_sec','tool_timeout_sec'):
   value=next(int(item.rsplit('=',1)[1]) for item in args if 'mcp_servers.devflower_managed.'+key+'=' in item)
   self.assertGreater(value,0);self.assertLessEqual(value,10 if key=='startup_timeout_sec' else 130)
   if key=='tool_timeout_sec':self.assertGreater(value,60)
  context.deadline=time.monotonic()-1
  with self.assertRaises(Blocked):managed_mcp_args(context)
 def test_real_journal_binds_process_then_session_and_closes_before_completed(self):
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=None,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  activation=self.db.get('activation','a');activation.pop('observed_session',None);self.db.put('activation','a',activation)
  b=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));from studio.native_codex_exec import protected_paths
  b['native_protected']=protected_paths(self.rt,b);events=[];sent=[]
  class Owner:
   def __init__(self,*args):events.append('owner-created')
   def call(self,slot,task):events.append('child-call');return dict(id='synthetic',state='RESERVED',slot=slot)
   def close(self):events.append('child-close');return dict(status='closed')
  def execute(argv,cwd,home,**kw):
   context=kw['store']._managed_context;sent.append(argv)
   self.assertNotIn(context.endpoint.secret,str(argv))
   with self.assertRaises(Blocked):context.call(1,'before process')
   op=kw['store'].intent('r','PROCESS',dict(context={'provider':'codex'}));kw['store'].finish(op,'RUNNING',process=process_identity(os.getpid()))
   with self.assertRaises(Blocked):context.bind_process(process_identity(os.getpid()))
   with self.assertRaises(Blocked):context.call(1,'before session')
   kw['on_stdout'](RAW)
   self.assertEqual(context.call(1,'after session')['state'],'RESERVED')
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  orig=self.journal.finish
  def finish(key,status,**updates):events.append(status);return orig(key,status,**updates)
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks'),patch('studio.managed_children.socket.socket',FakeListener),patch('studio.managed_children.OwnedChildren',Owner),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
   result=LocalCodex(b,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('synthetic',str(Path(self.tmp.name)),Path(self.tmp.name),store=self.journal,run='r')
  self.assertEqual(result['session_id'],SID);self.assertEqual(result['subagents'],'readonly');self.assertLess(events.index('child-close'),events.index('AGENT_COMPLETED'))
  args=sent[0];self.assertEqual(sum('mcp_servers.devflower_managed.command=' in x for x in args),1)
  self.assertIn('mcp_servers.devflower_managed.required=true',args)
  self.assertIn('mcp_servers.devflower_managed.enabled=true',args)
  self.assertEqual([x for x in args if 'supports_parallel_tool_calls=' in x],['mcp_servers.devflower_managed.supports_parallel_tool_calls=true'])
  self.assertIn('mcp_servers.devflower_managed.enabled_tools=["delegated_readonly"]',args)
  for field in ('startup_timeout_sec','tool_timeout_sec'):
   values=[int(x.rsplit('=',1)[1]) for x in args if 'mcp_servers.devflower_managed.'+field+'=' in x]
   self.assertEqual(len(values),1);self.assertGreater(values[0],0);self.assertLessEqual(values[0],20)
  self.assertIn('--ignore-user-config',args);self.assertIn('features.multi_agent=false',args)
  self.assertIn('approval_policy="never"',args)
  self.assertEqual([x for x in args if 'approval_mode=' in x],['mcp_servers.devflower_managed.tools.delegated_readonly.approval_mode="approve"'])
  self.assertIn('skills.include_instructions=false',args);self.assertIn('skills.bundled.enabled=false',args)
  self.assertFalse(any('token' in x for x in args));self.assertFalse(hasattr(self.journal,'_managed_context'))

 def test_close_never_signals_database_substituted_foreign_process(self):
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+15)
  owned=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
  foreign=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
  try:
   owned_id=process_identity(owned.pid);foreign_id=process_identity(foreign.pid)
   self.db.put('managed-child','x',dict(child_activation='c'));self.db.put('activation','c',dict(process=foreign_id))
   event=threading.Event();event.set();manager.active['x']=event;manager.proofs['c']=owned_id
   self.assertEqual(manager.close()['status'],'UNRESOLVED');self.assertIsNone(foreign.poll())
  finally:
   owned.terminate();foreign.terminate();owned.wait(timeout=3);foreign.wait(timeout=3)

 def test_outstanding_child_remains_unresolved_on_repeated_close(self):
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+15)
  event=threading.Event();event.set();manager.active['missing']=event
  self.assertEqual(manager.close()['status'],'UNRESOLVED');manager.active.clear()
  self.assertEqual(manager.close()['status'],'UNRESOLVED')

 def test_unresolved_child_cleanup_refuses_parent_completion(self):
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=None,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  activation=self.db.get('activation','a');activation.pop('observed_session',None);self.db.put('activation','a',activation)
  b=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));from studio.native_codex_exec import protected_paths
  b['native_protected']=protected_paths(self.rt,b);statuses=[]
  class Owner:
   def __init__(self,*args):pass
   def call(self,*args):return dict(id='fixture',slot=1,state='RESERVED')
   def close(self):return dict(status='UNRESOLVED')
  def execute(argv,cwd,home,**kw):
   op=kw['store'].intent('r','PROCESS',dict(context={'provider':'codex'}));kw['store'].finish(op,'RUNNING',process=process_identity(os.getpid()))
   kw['on_stdout'](RAW);kw['store']._managed_context.call(1,'inspect')
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  orig=self.journal.finish
  def finish(key,status,**updates):statuses.append(status);return orig(key,status,**updates)
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks'),patch('studio.managed_children.socket.socket',FakeListener),patch('studio.managed_children.OwnedChildren',Owner),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
   with self.assertRaisesRegex(Blocked,'收尾未确认'):LocalCodex(b,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('synthetic',str(Path(self.tmp.name)),Path(self.tmp.name),store=self.journal,run='r')
  self.assertNotIn('AGENT_COMPLETED',statuses);self.assertIn('AGENT_FAILED',statuses);self.assertEqual(self.db.get('activation','a')['state'],'RECOVERY_REQUIRED')

 def test_disconnected_bridge_requests_child_stop(self):
  import struct
  from studio.managed_children import OwnedEndpoint
  stopped=threading.Event();responses=[]
  class Owner:
   parent=self.parent
   deadline=time.monotonic()+10
   def call(self,slot,task):stopped.wait(2);raise Blocked('cancelled')
   def stop_children(self):stopped.set();return dict(status='closed')
  class Stream:
   def __init__(self):
    import io
    self.data=io.BytesIO(json.dumps(dict(version=1,token='synthetic',arguments=dict(slot=1,task='inspect'))).encode()+b'\n')
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def readline(self,n):return json.dumps(dict(version=1,token='synthetic',arguments=dict(slot=1,task='inspect'))).encode()+b'\n'
   def read(self,n):return self.data.read(n)
   def write(self,raw):responses.append(raw)
   def flush(self):pass
  class Client:
   def getsockopt(self,*args):return struct.pack('3i',os.getpid(),os.getuid(),0)
   def settimeout(self,*args):pass
   def makefile(self,*args):return Stream()
   def recv(self,*args):return b''
   def close(self):pass
  endpoint=OwnedEndpoint.__new__(OwnedEndpoint);endpoint.owner=Owner();endpoint.secret='synthetic';endpoint.lock=threading.Lock();client=Client();endpoint.clients={client}
  with patch('studio.managed_children._descendant',return_value=True),patch('studio.managed_children.select.select',return_value=([client],[],[])):
   endpoint._serve(client)
  self.assertTrue(stopped.is_set());self.assertFalse(json.loads(responses[0])['ok'])

 def test_parent_transport_exception_closes_context_without_completion(self):
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=None,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  b=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));from studio.native_codex_exec import protected_paths
  b['native_protected']=protected_paths(self.rt,b);paths=[];statuses=[]
  def execute(argv,cwd,home,**kw):paths.append(kw['store']._managed_context.path);raise OSError('synthetic failure')
  orig=self.journal.finish
  def finish(key,status,**updates):statuses.append(status);return orig(key,status,**updates)
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks'),patch('studio.managed_children.socket.socket',FakeListener),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
   with self.assertRaisesRegex(Blocked,'synthetic failure'):LocalCodex(b,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('synthetic',str(Path(self.tmp.name)),Path(self.tmp.name),store=self.journal,run='r')
  self.assertEqual(paths[0].read_bytes(),b'');self.assertIn('AGENT_FAILED',statuses);self.assertNotIn('AGENT_COMPLETED',statuses)

 def test_preflight_source_change_refuses_before_child_spawn(self):
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+15);calls=[]
  outer=self
  class SyntheticCodex:
   def __init__(self,*args):pass
   def start(self,task,cwd,artifacts,store,run):
    outer.rt.source_view_hash=lambda cwd,view:'changed';store.check_before_spawn();calls.append('spawn')
  with patch('studio.managed_children.LocalCodex',SyntheticCodex):
   with self.assertRaisesRegex(Blocked,'源码已变化'):manager.call(1,'inspect')
  self.assertEqual(calls,[]);self.assertEqual(self.db.list('managed-child')[0]['state'],'FAILED')

 def test_post_call_source_change_preserves_recovery_required(self):
  manager=OwnedChildren(self.journal,self.effective,str(Path(self.tmp.name)),self.parent,time.monotonic()+15);outer=self
  child_sid='00000000-0000-4000-8000-000000000002'
  class SyntheticCodex:
   def __init__(self,*args):pass
   def start(self,task,cwd,artifacts,store,run):
    store.check_before_spawn();store.process_started=True
    op=store.intent(run,'PROCESS',dict(context={'provider':'codex'}));store.finish(op,'RUNNING',process=process_identity(os.getpid()))
    request=store.intent(run,'AGENT_REQUEST',dict(requested_session=None));store.finish(request,'AGENT_SESSION',actual_session=child_sid)
    outer.rt.source_view_hash=lambda cwd,view:'changed'
    return dict(session_id=child_sid,report={'reply':'synthetic'})
  with patch('studio.managed_children.LocalCodex',SyntheticCodex):
   with self.assertRaisesRegex(Blocked,'调用已经发生'):manager.call(1,'inspect')
  self.assertEqual(self.db.list('managed-child')[0]['state'],'RECOVERY_REQUIRED')
