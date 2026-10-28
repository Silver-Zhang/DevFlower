"""Partial-view preflight coordination with real locks and filesystem scans."""
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from orchestration.common import Blocked
from studio.native_codex_rpc import metadata_guard
from studio.native_codex_exec import check_protected_hardlinks
from studio.workspace_access import native_workspace_view,native_workspace_hash

class WorkspaceCoordinationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'workspace';self.root.mkdir()
  self.storage=self.root/'private';self.storage.mkdir();(self.storage/'state').write_text('private')
  (self.root/'public.txt').write_text('public')
  self.rt=SimpleNamespace(storage=self.storage,native=self.storage/'native',operator_file=self.storage/'operator',policy_file=self.storage/'policy',protected=[self.storage],operator={'profiles':{},'native_local':{}},policy={})
  self.view=native_workspace_view(self.rt,str(self.root))
 def test_protected_scan_waits_for_probe_lock_and_public_hash_stays_correct(self):
  entered=threading.Event();start=threading.Event();results=[];errors=[]
  def scan(paths):entered.set();return check_protected_hardlinks(paths)
  def run():
   start.set()
   try:results.append(native_workspace_hash(self.rt,self.view))
   except Exception as exc:errors.append(exc)
  with patch('studio.native_codex_exec.check_protected_hardlinks',side_effect=scan):
   worker=threading.Thread(target=run)
   try:
    with metadata_guard(self.storage):
     worker.start();self.assertTrue(start.wait(1));self.assertFalse(entered.wait(.15),'scan entered while probe lock held')
   finally:
    if worker.ident:worker.join(5)
   self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertTrue(entered.is_set())
  first=results[0];(self.storage/'state').write_text('changed private')
  self.assertEqual(native_workspace_hash(self.rt,self.view),first)
  (self.root/'public.txt').write_text('changed public');self.assertNotEqual(native_workspace_hash(self.rt,self.view),first)
 def test_public_hash_is_outside_coordinator_and_repeated_calls_work(self):
  from studio.workspace_access import digest
  observed=[]
  def checked(value):
   with metadata_guard(self.storage,.2):observed.append(True)
   return digest(value)
  with patch('studio.workspace_access.digest',side_effect=checked):
   one=native_workspace_hash(self.rt,self.view);two=native_workspace_hash(self.rt,self.view)
  self.assertEqual(one,two);self.assertGreaterEqual(len(observed),2)
 def test_unknown_external_hardlink_still_refused(self):
  os.link(self.storage/'state',self.root/'alias')
  with self.assertRaises(Blocked):native_workspace_hash(self.rt,self.view)
 def test_new_symlink_and_root_replacement_during_scan_still_refused(self):
  for change in ('symlink','root'):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as extra:
    root=Path(extra)/'workspace';root.mkdir();storage=root/'private';storage.mkdir();(storage/'state').write_text('private')
    rt=SimpleNamespace(storage=storage,native=storage/'native',operator_file=storage/'operator',policy_file=storage/'policy',protected=[storage],operator={'profiles':{},'native_local':{}},policy={})
    view=native_workspace_view(rt,str(root));real=os.fwalk;count=0
    def walk(top,*args,**kwargs):
     nonlocal count
     if str(top)==str(storage):
      count+=1
      if count==2:
       if change=='symlink':(storage/'unexpected').symlink_to('/synthetic/target')
       else:storage.rename(root/'moved');storage.mkdir()
     yield from real(top,*args,**kwargs)
    with patch('studio.native_codex_exec.os.fwalk',side_effect=walk):
     with self.assertRaises(Blocked):native_workspace_hash(rt,view)
    self.assertGreaterEqual(count,2)
