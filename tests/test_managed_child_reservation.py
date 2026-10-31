"""Real SQLite reservation transactions; no child or provider is started."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from orchestration.common import Blocked
from orchestration.store import process_identity
from studio.workbench_scheduler import Journal
from studio.workbench_store import WorkbenchStore

SID='00000000-0000-4000-8000-000000000001'
class ReservationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.db=WorkbenchStore(Path(self.tmp.name)/'flow.sqlite');self.parent=process_identity(os.getpid())
  self.run=dict(state='RUNNING',interaction='click',workspace='w',candidate_sha256='candidate',graph_sha256='graph',graph=dict(budget=dict(calls=3)),calls=1,created_at=time.time())
  self.binding=dict(provider='codex',execution_backend='native-local',subagents='readonly',role='reviewer',execution_mode='Ask',model='m',effort='high',home='/home/synthetic',config_root='/home/synthetic/config',runtime_profile='p',session_id=SID)
  self.activation=dict(id='a',run='r',node='node',kind='agent',state='RUNNING',iteration_path=[1],process=self.parent,observed_session=SID,chat_key='chat',source_at_request=dict(sha256='fixture-source'))
  self.db.put('run','r',self.run);self.db.put('activation','a',self.activation);self.db.put('node-session','chat',dict(workspace='w',run='r',node='node',binding=self.binding))
  self.journal=Journal(SimpleNamespace(db=self.db,run='r'),'a')
 def reserve(self,slot=1,task='read public code',parent=None):return self.journal.reserve_managed_child(slot,task,parent or self.parent)
 def test_reservation_and_identical_replay_are_one_budget_and_one_intent(self):
  first=self.reserve();self.assertEqual(first,self.reserve());self.assertEqual(self.db.get('run','r')['calls'],2)
  self.assertEqual(first['state'],'RESERVED');self.assertEqual(first['usage'],'unknown');self.assertEqual(len(self.db.list('native-step')),1)
  self.assertNotIn('read public code',repr(first));self.assertEqual(self.db.get('native-step',first['id'])['kind'],'MANAGED_CHILD_DISPATCH')
 def test_missing_or_contradictory_dispatch_intent_never_replays(self):
  first=self.reserve();intent=self.db.get('native-step',first['id'])
  with self.db.tx() as db:db.execute("DELETE FROM records WHERE kind='native-step' AND id=?",(first['id'],))
  with self.assertRaises(Blocked):self.reserve()
  self.assertEqual(self.db.get('run','r')['calls'],2)
  for change in (dict(status='COMPLETED'),dict(run='other'),dict(kind='PROCESS'),dict(data=dict(slot=2,task_sha256=first['task_sha256']))):
   self.db.put('native-step',first['id'],dict(intent,**change))
   with self.subTest(change=change),self.assertRaises(Blocked):self.reserve()
  self.assertEqual(self.db.get('run','r')['calls'],2)
 def test_changed_slot_task_and_frozen_binding_rejected(self):
  self.reserve()
  with self.assertRaises(Blocked):self.reserve(task='different')
  chat=self.db.get('node-session','chat');chat['binding']['effort']='max';self.db.put('node-session','chat',chat)
  with self.assertRaises(Blocked):self.reserve()
  self.assertEqual(self.db.get('run','r')['calls'],2)
 def test_bad_arguments_and_parent_identity_refused(self):
  for slot,task in ((0,'a'),(3,'a'),(True,'a'),(1,''),(1,'x'*8193)):
   with self.subTest(slot=slot),self.assertRaises(Blocked):self.reserve(slot,task)
  fake=dict(self.parent,start='0')
  with self.assertRaises(Blocked):self.reserve(parent=fake)
  self.assertEqual(self.db.get('run','r')['calls'],1);self.assertEqual(self.db.list('native-step'),[])
 def test_cross_run_session_mode_and_cancel_are_rejected(self):
  for kind,key,change in [('activation','a',dict(run='other')),('activation','a',dict(observed_session=SID[:-1]+'2')),('node-session','chat',dict(workspace='other')),('run','r',dict(cancel_requested=True))]:
   original=self.db.get(kind,key);changed=dict(original,**change);self.db.put(kind,key,changed)
   with self.subTest(kind=kind,change=change),self.assertRaises(Blocked):self.reserve()
   self.db.put(kind,key,original)
  for update in (dict(role='implementer'),dict(execution_mode='Agent'),dict(provider='claude'),dict(subagents='disabled')):
   chat=self.db.get('node-session','chat');original=dict(chat['binding']);chat['binding'].update(update);self.db.put('node-session','chat',chat)
   with self.assertRaises(Blocked):self.reserve()
   chat['binding']=original;self.db.put('node-session','chat',chat)
  self.assertEqual(self.db.get('run','r')['calls'],1)
 def test_parallel_slots_atomically_enforce_budget(self):
  self.run['graph']['budget']['calls']=2;self.db.put('run','r',self.run)
  barrier=threading.Barrier(2);out=[]
  def run(slot):
   barrier.wait()
   try:self.reserve(slot,str(slot));out.append('reserved')
   except Blocked:out.append('blocked')
  threads=[threading.Thread(target=run,args=(slot,)) for slot in (1,2)]
  for thread in threads:thread.start()
  for thread in threads:thread.join(5)
  self.assertFalse(any(t.is_alive() for t in threads));self.assertCountEqual(out,['reserved','blocked'])
  self.assertEqual(self.db.get('run','r')['calls'],2);self.assertEqual(len(self.db.list('managed-child')),1);self.assertEqual(len(self.db.list('native-step')),1)
 def test_slot_identity_binds_activation_and_round(self):
  first=self.reserve();self.assertEqual(first['iteration_path'],[1])
  activation=self.db.get('activation','a');activation['iteration_path']=[2];self.db.put('activation','a',activation)
  with self.assertRaises(Blocked):self.reserve()
 def test_source_identity_is_required_and_bound(self):
  value=self.db.get('activation','a');original=value['source_at_request'];value.pop('source_at_request');self.db.put('activation','a',value)
  with self.assertRaises(Blocked):self.reserve()
  value['source_at_request']=original;self.db.put('activation','a',value);self.reserve()
  value['source_at_request']=dict(sha256='different');self.db.put('activation','a',value)
  with self.assertRaises(Blocked):self.reserve()
 def test_expired_or_invalid_time_budget_refuses_reservation(self):
  for created in (None,time.time()-4000,float('nan')):
   changed=dict(self.run,created_at=created);self.db.put('run','r',changed)
   with self.assertRaises(Blocked):self.reserve()
  self.assertEqual(self.db.list('native-step'),[])
