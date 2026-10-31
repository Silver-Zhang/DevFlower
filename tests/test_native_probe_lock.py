"""Actual metadata lock, synthetic probes and transport; no real CLI/model."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.native_codex_rpc import metadata_guard

SID='00000000-0000-4000-8000-000000000001'
RAW=('\n'.join(json.dumps(x) for x in [dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})])+'\n').encode()
class RPC:
 def __init__(self,*a):pass
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def request(self,*a):return {'config':{}}

class ProbeLockTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.art=self.root/'artifacts';self.art.mkdir()
  self.b=dict(executable='synthetic',home=str(self.root),config_root=str(self.root),provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[str(self.root)],_metadata_guard=str(self.root))
  self.stack=ExitStack();self.addCleanup(self.stack.close);self.stack.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC))
 def patch_probe(self,**kw):
  for name in ('require_exec_capability','require_native_write_capability','require_sandbox_capability'):
   self.stack.enter_context(patch('studio.native_codex_exec_probe.'+name,side_effect=kw.get(name)))
 def run_client(self,b=None,store=None,timeout=5):
  return LocalCodex(b or self.b,dict(step_timeout_seconds=timeout,max_log_bytes=100000)).start('synthetic',str(self.root),self.art,store=store,run='r')
 def test_two_readonly_executes_overlap_after_serialized_probe_and_scan(self):
  barrier=threading.Barrier(2);errors=[];sent=[];self.patch_probe()
  def send(*a,**kw):
   sent.append(threading.current_thread().name);barrier.wait(3);kw['on_stdout'](RAW)
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  self.stack.enter_context(patch('orchestration.process.execute',side_effect=send))
  def run():
   try:self.run_client()
   except Exception as exc:errors.append(exc)
  threads=[threading.Thread(target=run,name='client-'+str(i)) for i in range(2)]
  for t in threads:t.start()
  for t in threads:t.join(5)
  self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[]);self.assertEqual(len(sent),2)
 def test_waiting_for_probe_lock_cancels_or_times_out_before_send(self):
  for cancel in (True,False):
   with self.subTest(cancel=cancel):
    ready=threading.Event();resume=threading.Event();cancelled=threading.Event();errors=[]
    class Store:
     def get(self,run):return {'data':{'cancel_requested':cancelled.is_set()}}
    def provider(config):ready.set();resume.wait(2);return []
    def run():
     try:self.run_client(store=Store(),timeout=.4)
     except Exception as exc:errors.append(exc)
    with patch('studio.native_codex_exec.provider_overrides',side_effect=provider),patch('studio.native_codex_exec_probe.require_exec_capability') as probe,patch('orchestration.process.execute') as send:
     t=threading.Thread(target=run);t.start();self.assertTrue(ready.wait(1))
     with metadata_guard(str(self.root),1):
      resume.set()
      if cancel:cancelled.set()
      t.join(2);self.assertFalse(t.is_alive())
     probe.assert_not_called();send.assert_not_called()
    self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],Blocked)
    self.assertIn('取消' if cancel else '超时',str(errors[0]))
 def test_probe_exception_releases_lock_without_dispatch(self):
  self.patch_probe(require_exec_capability=Blocked('synthetic probe error'))
  with patch('orchestration.process.execute') as send:
   with self.assertRaisesRegex(Blocked,'synthetic probe error'):self.run_client()
   send.assert_not_called()
  with metadata_guard(str(self.root),.2):pass
 def test_both_probe_calls_hold_lock_and_use_fresh_step_budget(self):
  observed=[]
  def probe(*args,**kw):
   with self.assertRaises(Blocked):
    with metadata_guard(str(self.root),.03):pass
   observed.append(kw['timeout'])
  self.patch_probe(require_exec_capability=probe,require_native_write_capability=probe)
  def send(*a,**kw):kw['on_stdout'](RAW);return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  with patch('orchestration.process.execute',side_effect=send):self.run_client()
  self.assertEqual(len(observed),2);self.assertGreater(observed[0],observed[1]);self.assertGreater(observed[1],0)
 def test_nonordinary_without_gate_still_uses_sandbox_probe(self):
  b=dict(self.b);b.pop('_metadata_guard');b.pop('native_interaction');b.pop('native_protected');self.patch_probe()
  def send(*a,**kw):kw['on_stdout'](RAW);return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  with patch('studio.native_codex_exec_probe.require_sandbox_capability') as probe,patch('orchestration.process.execute',side_effect=send):
   result=self.run_client(b);probe.assert_called_once();self.assertEqual(result['session_id'],SID)
 def test_coordination_receives_step_budget_while_rpc_stays_bounded(self):
  from contextlib import contextmanager
  waits=[];rpc_timeouts=[]
  @contextmanager
  def gate(path,timeout,cancelled):
   waits.append(timeout);yield timeout
  class BoundedRPC(RPC):
   def __init__(self,*args):rpc_timeouts.append(args[4])
  self.patch_probe()
  def send(*a,**kw):kw['on_stdout'](RAW);return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  with patch('studio.native_codex_rpc.metadata_guard',gate),patch('studio.local_codex_adapter.CodexRPC',BoundedRPC),patch('orchestration.process.execute',side_effect=send):
   self.run_client(timeout=180)
  self.assertEqual(len(waits),3);self.assertTrue(all(x>30 for x in waits));self.assertLessEqual(rpc_timeouts[0],30)
