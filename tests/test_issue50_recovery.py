"""Synthetic locked database and process-identity recovery tests."""
import os,sqlite3,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch
from studio.workbench_store import WorkbenchStore
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_failures import abort_unexpected,recovery_notice,reconcile_interrupted
from orchestration.common import Blocked
from orchestration.store import process_identity

class DurableRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.flow=WorkbenchStore(Path(self.tmp.name)/'workbench.sqlite');self.rt=SimpleNamespace(flow=self.flow,storage=Path(self.tmp.name))
  self.flow.put('run','run',dict(id='run',state='RUNNING',created_at=time.time()-10,worker=None))
  self.flow.put('activation','a',dict(id='a',run='run',state='RUNNING',process=None))
  self.flow.put('native-step','p',dict(id='p',run='run',kind='PROCESS',status='RUNNING',data={'process':dict(process_identity(os.getpid()),pid=999999,pgid=999999,sid=999999)}))
 def test_lock_exhaustion_durable_notice_readonly_projection_and_explicit_reconcile(self):
  tx=self.flow.tx
  @contextmanager
  def short(*,read=False,timeout=20):
   with tx(read=read,timeout=.04 if not read else timeout) as db:yield db
  self.flow.tx=short
  lock=sqlite3.connect(self.flow.path);lock.execute('BEGIN IMMEDIATE')
  try:
   with self.assertRaises(sqlite3.OperationalError):abort_unexpected(self.rt,'run',sqlite3.OperationalError('SYNTHETIC LOCK'))
   self.assertIsNotNone(recovery_notice(self.rt,'run'))
   snap=WorkbenchRuntime.snapshot(self.rt,'run');self.assertEqual(snap['state'],'RECOVERY_REQUIRED');self.assertTrue(snap['recovery_pending']);self.assertEqual(snap['nodes'][0]['state'],'RECOVERY_REQUIRED')
   self.assertEqual(self.flow.get('run','run')['state'],'RUNNING','GET must not mutate database')
   with self.assertRaises(Blocked):WorkbenchRuntime.control(self.rt,'run','resume')
   with self.assertRaises(sqlite3.OperationalError):reconcile_interrupted(self.rt,'run')
   self.assertIsNotNone(recovery_notice(self.rt,'run'))
  finally:lock.rollback();lock.close()
  with patch('studio.workbench_failures.group_alive',return_value=False):reconcile_interrupted(self.rt,'run')
  self.assertEqual(self.flow.get('run','run')['state'],'RECOVERY_REQUIRED');self.assertEqual(self.flow.get('native-step','p')['status'],'RECOVERY_REQUIRED');self.assertEqual(self.flow.get('native-step','p')['data']['outcome'],'unknown');self.assertIsNone(recovery_notice(self.rt,'run'))
 def test_live_worker_or_native_process_never_reconciled_or_signalled(self):
  from studio.workbench_failures import _persist_recovery_notice
  _persist_recovery_notice(self.rt,'run','Synthetic')
  self.flow.change('run','run',{'worker':process_identity(os.getpid())})
  with patch('studio.workbench_failures.stop') as stop:
   with self.assertRaises(Blocked):reconcile_interrupted(self.rt,'run')
   stop.assert_not_called()
  self.flow.change('run','run',{'worker':None})
  with patch('studio.workbench_failures.group_alive',return_value=True),patch('studio.workbench_failures.stop') as stop:
   with self.assertRaises(Blocked):reconcile_interrupted(self.rt,'run')
   stop.assert_not_called()
 def test_malformed_process_identity_cannot_be_treated_as_confirmed_dead(self):
  from studio.workbench_failures import _persist_recovery_notice
  self.flow.change('native-step','p',{'data':{'process':{'pid':999999}}})
  _persist_recovery_notice(self.rt,'run','Synthetic')
  with self.assertRaises(Blocked):reconcile_interrupted(self.rt,'run')
  self.assertIsNotNone(recovery_notice(self.rt,'run'))
 def test_foreign_host_process_is_never_assumed_dead(self):
  from studio.workbench_failures import _persist_recovery_notice
  process=dict(process_identity(os.getpid()),host='SYNTHETIC_OTHER_HOST',pid=999999,pgid=999999,sid=999999)
  self.flow.change('native-step','p',{'data':{'process':process}})
  _persist_recovery_notice(self.rt,'run','Synthetic')
  with self.assertRaises(Blocked):reconcile_interrupted(self.rt,'run')
  self.assertEqual(self.flow.get('native-step','p')['status'],'RUNNING')
  self.assertIsNotNone(recovery_notice(self.rt,'run'))
 def test_missing_process_identity_preserves_notice_and_unknown_occupancy(self):
  from studio.workbench_failures import _persist_recovery_notice
  for data in [{},{'process':None}]:
   with self.subTest(data=data):
    self.flow.change('native-step','p',{'data':data})
    _persist_recovery_notice(self.rt,'run','Synthetic')
    with self.assertRaises(Blocked):reconcile_interrupted(self.rt,'run')
    self.assertIsNotNone(recovery_notice(self.rt,'run'))
    self.assertEqual(self.flow.get('native-step','p')['status'],'RUNNING')
    self.assertNotIn('observed_alive',self.flow.get('native-step','p')['data'])
 def test_cleanup_of_owned_groups_precedes_failed_database_write(self):
  identity=process_identity(os.getpid());self.flow.change('activation','a',{'process':identity})
  lock=sqlite3.connect(self.flow.path);lock.execute('BEGIN IMMEDIATE');original=self.flow.change
  def change(*a,**kw):kw['timeout']=.04;return original(*a,**kw)
  try:
   with patch.object(self.flow,'change',side_effect=change),patch('studio.workbench_failures.group_alive',return_value=True),patch('studio.workbench_failures.stop') as stop:
    with self.assertRaises(sqlite3.OperationalError):abort_unexpected(self.rt,'run',RuntimeError('synthetic'))
    stop.assert_called_once_with(identity)
  finally:lock.rollback();lock.close()

