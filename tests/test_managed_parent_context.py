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
from native_protocol_mechanics import LocalCodex
from studio.managed_children import OwnedChildren
from test_managed_child_bridge import RunnerTests
from test_native_attach_private import FakeListener
from test_native_probe_lock import RPC


class OwnedChildFailureCloseTests(unittest.TestCase):
 """Real Journal/SQLite and private owned Python children; no native model."""
 def setUp(self):
  fixture=RunnerTests('test_actual_runner_fixed_binding_and_idempotent_slot');fixture.setUp();self.addCleanup(fixture.doCleanups)
  self.fixture=fixture
  for name in ('tmp','db','journal','parent','rt','effective'):setattr(self,name,getattr(fixture,name))
  self.root=Path(self.tmp.name).resolve();self.children=[];self.addCleanup(self.reap)
 def reap(self):
  for child in self.children:
   if child.poll() is None:child.terminate()
   try:child.wait(timeout=3)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
   if child.stdin and not child.stdin.closed:child.stdin.close()
 def process(self):
  from orchestration.common import clean_env
  child=subprocess.Popen([sys.executable,'-B','-c','import sys;sys.stdin.buffer.read(1)'],cwd=self.root,start_new_session=True,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=clean_env())
  self.children.append(child);return child
 def adapter(self,mode):
  outer=self;sid='00000000-0000-4000-8000-000000000002'
  class Child:
   def __init__(self,binding,limits):self.binding=binding
   def start(self,task,cwd,artifacts,store,run):
    outer.assertTrue(Path(artifacts).resolve().is_relative_to(outer.root));outer.assertEqual(Path(cwd).resolve(),outer.root)
    if mode=='before':raise Blocked('SYNTHETIC failure before Popen')
    child=outer.process();store.process_started=True
    if mode=='no-proof':raise Blocked('SYNTHETIC no verified process publication')
    op=store.intent(run,'PROCESS',dict(context={'provider':'codex'}));store.finish(op,'RUNNING',process=process_identity(child.pid))
    req=store.intent(run,'AGENT_REQUEST',dict(requested_session=None));store.finish(req,'AGENT_SESSION',actual_session=sid)
    if mode=='after-start':raise Blocked('SYNTHETIC failure after Popen')
    child.stdin.write(b'x');child.stdin.flush();child.stdin.close();child.wait(timeout=3)
    store.finish(op,'EXITED',exit=child.returncode,timeout=False,cancelled=False,overflow=False)
    return dict(session_id=sid,report={'reply':1 if mode=='receipt' else 'SYNTHETIC child answer'},usage='unknown',requested_model=self.binding['model'],requested_effort=self.binding['effort'],process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
  return Child
 def invoke(self,mode):
  import sqlite3
  from studio.managed_child_reservation import transition
  manager=OwnedChildren(self.journal,self.effective,str(self.root),self.parent,time.monotonic()+20)
  def publish(db,row,state):
   if mode=='publish' and state=='COMPLETED':raise sqlite3.OperationalError('SYNTHETIC completion publication failed')
   return transition(db,row,state)
  def event(value):
   if mode=='event' and value['event']=='item.completed':raise OSError('SYNTHETIC display event failed after commit')
   return original_event(value)
  original_event=self.journal.native_tool_event
  with patch('studio.managed_children.LocalCodex',self.adapter(mode)),patch('studio.managed_child_reservation.transition',side_effect=publish),patch.object(self.journal,'native_tool_event',side_effect=event):
   if mode=='success':manager.call(1,'inspect private synthetic fixture')
   else:
    with self.assertRaises((Blocked,sqlite3.OperationalError,OSError)):manager.call(1,'inspect private synthetic fixture')
  self.assertEqual(manager.active,{})
  return manager,self.db.list('managed-child')[0]
 def test_after_started_failure_stays_unresolved_and_reaps_verified_process(self):
  manager,row=self.invoke('after-start')
  self.assertEqual(row['state'],'RECOVERY_REQUIRED');self.assertEqual(len(manager.proofs),1)
  with patch('studio.managed_children.LocalCodex') as transport:
   self.assertEqual(manager.call(1,'inspect private synthetic fixture')['state'],'RECOVERY_REQUIRED');transport.assert_not_called()
  self.assertEqual(len(self.children),1);self.assertEqual(self.db.get('run','r')['calls'],2)
  self.assertEqual(manager.close()['status'],'UNRESOLVED')
  self.assertEqual(manager.close()['status'],'UNRESOLVED')
  self.children[0].wait(timeout=3)
  self.assertEqual(self.db.get('activation',row['child_activation'])['state'],'RECOVERY_REQUIRED')
  self.assertEqual(self.db.get('run','r')['actual_calls'],1)
 def test_bad_receipt_stays_unresolved_after_active_is_empty(self):
  manager,row=self.invoke('receipt')
  self.assertEqual(row['state'],'RECOVERY_REQUIRED')
  self.assertEqual(manager.close()['status'],'UNRESOLVED');self.assertEqual(manager.close()['status'],'UNRESOLVED')
  self.assertEqual(self.db.get('activation',row['child_activation'])['state'],'RECOVERY_REQUIRED')
 def test_completion_publication_failure_stays_unresolved(self):
  manager,row=self.invoke('publish')
  self.assertEqual(row['state'],'RECOVERY_REQUIRED')
  self.assertEqual(manager.close()['status'],'UNRESOLVED');self.assertEqual(manager.close()['status'],'UNRESOLVED')
  self.assertEqual(self.db.get('activation',row['child_activation'])['state'],'RECOVERY_REQUIRED')
 def test_success_and_pre_spawn_failure_have_known_outcomes(self):
  manager,row=self.invoke('before')
  self.assertEqual(self.children,[]);self.assertEqual(self.db.get('run','r').get('actual_calls',0),0)
  self.assertEqual(row['state'],'FAILED');self.assertEqual(self.db.get('activation',row['child_activation'])['state'],'FAILED')
  self.assertEqual(manager.close()['status'],'closed');self.assertEqual(manager.close()['status'],'closed')
 def test_successful_receipt_closes_without_unknown(self):
  manager,row=self.invoke('success')
  self.assertEqual(row['state'],'COMPLETED');self.assertEqual(self.db.get('activation',row['child_activation'])['state'],'COMPLETED')
  self.assertEqual(manager.close()['status'],'closed');self.assertEqual(manager.close()['status'],'closed')
 def test_display_event_failure_does_not_rewrite_committed_receipt(self):
  manager,row=self.invoke('event')
  self.assertEqual(row['state'],'COMPLETED');activation=self.db.get('activation',row['child_activation'])
  self.assertEqual(activation['state'],'COMPLETED');self.assertEqual(activation['output']['answer'],'SYNTHETIC child answer')
  self.assertEqual(manager.close()['status'],'closed');self.assertEqual(manager.close()['status'],'closed')
 def test_without_proof_or_with_changed_pid_identity_no_process_is_signalled(self):
  manager,row=self.invoke('no-proof')
  with patch('studio.managed_children.stop') as signal:
   self.assertEqual(manager.close()['status'],'UNRESOLVED');signal.assert_not_called()
  self.assertIsNone(self.children[0].poll())
 def test_changed_kernel_identity_is_not_signalled(self):
  manager,row=self.invoke('after-start');manager.proofs[row['child_activation']]['start']='0'
  with patch('studio.managed_children.stop') as signal:
   self.assertEqual(manager.close()['status'],'UNRESOLVED');signal.assert_not_called()
  self.assertIsNone(self.children[0].poll())
 def test_database_substitution_cannot_redirect_verified_process_cleanup(self):
  manager,row=self.invoke('after-start');foreign=self.process()
  self.db.change('activation',row['child_activation'],{'process':process_identity(foreign.pid)})
  self.assertEqual(manager.close()['status'],'UNRESOLVED')
  self.children[0].wait(timeout=3);self.assertIsNone(foreign.poll())
 def test_proof_survives_process_record_publication_failure(self):
  from studio.workbench_scheduler import Journal
  original=Journal.finish
  def fail(journal,key,status,**updates):
   if journal is not self.journal and status=='RUNNING':raise OSError('SYNTHETIC process publication failure')
   return original(journal,key,status,**updates)
  with patch.object(Journal,'finish',fail):manager,row=self.invoke('after-start')
  self.assertEqual(len(manager.proofs),1);self.assertNotIn('process',self.db.get('activation',row['child_activation']))
  self.assertEqual(manager.close()['status'],'UNRESOLVED');self.children[0].wait(timeout=3)

 def test_real_parent_adapter_receives_latched_unknown_from_failed_child(self):
  from studio.native_codex_exec import protected_paths
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=None,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  activation=self.db.get('activation','a');activation.pop('observed_session',None);self.db.put('activation','a',activation)
  binding=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));binding['native_protected']=protected_paths(self.rt,binding)
  statuses=[];original=self.journal.finish
  def finish(key,status,**updates):statuses.append(status);return original(key,status,**updates)
  def execute(argv,cwd,home,**kwargs):
   journal=kwargs['store'];op=journal.intent('r','PROCESS',dict(context={'provider':'codex'}));journal.finish(op,'RUNNING',process=process_identity(os.getpid()))
   kwargs['on_stdout'](RAW)
   with self.assertRaises(Blocked):journal._managed_context.call(1,'inspect controlled fixture')
   self.assertEqual(journal._managed_context.owner.active,{})
   return dict(exit=0,timeout=False,cancelled=False,overflow=False),RAW,b''
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('studio.managed_children.LocalCodex',self.adapter('after-start')),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
   with self.assertRaisesRegex(Blocked,'收尾未确认'):LocalCodex(binding,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('SYNTHETIC parent',str(self.root),self.root,store=self.journal,run='r')
  self.assertNotIn('AGENT_COMPLETED',statuses);self.assertIn('AGENT_FAILED',statuses)
  self.assertEqual(self.db.get('activation','a')['state'],'RECOVERY_REQUIRED')
  self.assertEqual(self.db.list('managed-child')[0]['state'],'RECOVERY_REQUIRED')
  self.assertEqual(self.db.get('run','r')['actual_calls'],2)
  self.children[0].wait(timeout=3)

SID='00000000-0000-4000-8000-000000000001'
RAW=('\n'.join(json.dumps(x) for x in [dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})])+'\n').encode()

class ParentContextTests(RunnerTests):
 def context_fixture(self,baseline_sid=None):
  from studio.managed_children import ManagedContext
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding']['session_id']=baseline_sid;self.db.put('node-session','chat',chat)
  activation=self.db.get('activation','a');activation.pop('observed_session',None);self.db.put('activation','a',activation)
  c=ManagedContext.__new__(ManagedContext);c.journal=self.journal;c.rt=self.rt;c.binding=dict(self.effective,session_id=baseline_sid);c.baseline=dict(chat['binding']);c.cwd=chat['cwd'];c.chat_key='chat';c.node='node';c.workspace='w';c.deadline=time.monotonic()+5;c.parent=self.parent;c.owner=None;c.closed=threading.Event();c.lock=threading.Lock()
  return c
 def test_delayed_real_session_publication_unblocks_one_call(self):
  c=self.context_fixture();results=[];owners=[]
  class Owner:
   def __init__(self,*args):owners.append(1)
   def call(self,slot,task):return self_journal.reserve_managed_child(slot,task,c.parent)
  self_journal=self.journal
  def run():
   try:results.append(c.call(1,'inspect'))
   except Exception as e:results.append(e)
  with patch('studio.managed_children.OwnedChildren',Owner):
   t=threading.Thread(target=run);t.start();time.sleep(.1)
   op=self.journal.intent('r','AGENT_REQUEST',{});self.journal.finish(op,'AGENT_SESSION',actual_session=SID)
   t.join(4)
  self.assertFalse(t.is_alive());self.assertEqual(len(owners),1);self.assertEqual(results[0]['state'],'RESERVED');self.assertEqual(self.db.get('run','r')['calls'],2)
 def test_resume_mismatched_paired_identity_never_reserves(self):
  c=self.context_fixture(SID);other='00000000-0000-4000-8000-000000000002'
  self.db.change('activation','a',{'observed_session':other});chat=self.db.get('node-session','chat');chat['binding']['session_id']=other;self.db.put('node-session','chat',chat)
  with patch('studio.managed_children.OwnedChildren') as owner:
   with self.assertRaises(Blocked):c.call(1,'inspect')
   owner.assert_not_called()
  self.assertEqual(self.db.get('run','r')['calls'],1)
 def test_two_calls_wait_for_paired_journal_publication_and_share_owner(self):
  c=self.context_fixture();results=[];owners=[];entered=threading.Event();release=threading.Event();original=self.journal.bind_session
  class Owner:
   def __init__(self,*args):owners.append(1)
   def call(self,slot,task):return journal.reserve_managed_child(slot,task,c.parent)
  journal=self.journal
  def bind(sid):entered.set();release.wait(3);original(sid)
  def call():
   try:results.append(c.call(1,'same'))
   except Exception as e:results.append(e)
  op=journal.intent('r','AGENT_REQUEST',{})
  with patch.object(journal,'bind_session',side_effect=bind),patch('studio.managed_children.OwnedChildren',Owner):
   publisher=threading.Thread(target=lambda:journal.finish(op,'AGENT_SESSION',actual_session=SID));publisher.start();self.assertTrue(entered.wait(3))
   threads=[threading.Thread(target=call) for _ in range(2)]
   for thread in threads:thread.start()
   time.sleep(.1);self.assertEqual(owners,[]);release.set();publisher.join(4)
   for thread in threads:thread.join(4)
  self.assertFalse(publisher.is_alive());self.assertTrue(all(not t.is_alive() for t in threads));self.assertEqual(owners,[1]);self.assertEqual(len(results),2);self.assertEqual(results[0],results[1]);self.assertEqual(self.db.get('run','r')['calls'],2)
 def test_wait_refuses_timeout_cancel_close_dead_parent_and_binding_tamper(self):
  for change in ('timeout','cancel','close','dead','model','role','home','runtime_profile'):
   with self.subTest(change=change):
    c=self.context_fixture();run=self.db.get('run','r');run.pop('cancel_requested',None);self.db.put('run','r',run)
    if change=='timeout':c.deadline=time.monotonic()+.12
    elif change=='cancel':self.db.change('run','r',{'cancel_requested':True})
    elif change=='close':c.closed.set()
    elif change=='dead':c.parent=dict(self.parent,start='0')
    else:
     chat=self.db.get('node-session','chat');chat['binding'][change]='tampered';self.db.put('node-session','chat',chat)
    with patch('studio.managed_children.OwnedChildren') as owner:
     with self.assertRaises(Blocked):c.call(1,'inspect')
     owner.assert_not_called()
    self.assertEqual(self.db.get('run','r')['calls'],1)
 def test_resume_wrong_thread_event_is_not_published(self):
  chat=self.db.get('node-session','chat');chat['id']='chat';chat['binding'].update(conversation_format='text',session_id=SID,executable='synthetic',project='w');self.db.put('node-session','chat',chat)
  b=dict(chat['binding'],native_interaction='ordinary',_metadata_guard=str(self.rt.storage));from studio.native_codex_exec import protected_paths
  b['native_protected']=protected_paths(self.rt,b);statuses=[]
  def execute(argv,cwd,home,**kw):
   op=kw['store'].intent('r','PROCESS',dict(context={'provider':'codex'}));kw['store'].finish(op,'RUNNING',process=process_identity(os.getpid()))
   kw['on_stdout']((json.dumps(dict(type='thread.started',thread_id='00000000-0000-4000-8000-000000000002'))+'\n').encode())
   raise AssertionError('wrong session accepted')
  original=self.journal.finish
  def finish(key,status,**updates):statuses.append(status);return original(key,status,**updates)
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
   with self.assertRaises(Blocked):LocalCodex(b,dict(step_timeout_seconds=20,max_log_bytes=100000)).start('synthetic',str(Path(self.tmp.name)),Path(self.tmp.name),store=self.journal,run='r')
  self.assertNotIn('AGENT_SESSION',statuses);self.assertEqual(self.db.get('node-session','chat')['binding']['session_id'],SID);self.assertEqual(self.db.list('managed-child'),[])
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
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('orchestration.process.execute',side_effect=execute):
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
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('studio.managed_children.OwnedChildren',Owner),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
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
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('studio.managed_children.OwnedChildren',Owner),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
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
  with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('studio.native_codex_exec.check_protected_hardlinks_isolated'),patch('studio.managed_children.socket.socket',FakeListener),patch('orchestration.process.execute',side_effect=execute),patch.object(self.journal,'finish',side_effect=finish):
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
