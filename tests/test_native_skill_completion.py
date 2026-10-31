"""Skill completion boundaries; synthetic transport only."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.native_skill_pack import NativeSkillPack
import test_native_skill_pack as fixtures

class SkillCompletionTests(unittest.TestCase):
 setUp=fixtures.NativeSkillTests.setUp
 bind=fixtures.NativeSkillTests.bind
 def run_with(self,*,on_finish=None,on_send=None,preflight=None):
  b=self.bind();events=[];sent=[]
  class RPC:
   def __init__(self,*a):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  class Store:
   def get(self,run):return {'data':{'cancel_requested':False}}
   def intent(self,*a):return 'op'
   def visible_request(self,*a):pass
   def finish(self,key,status,**kw):
    events.append((status,kw))
    if on_finish:on_finish(status)
  def send(*a,**kw):
   sent.append(kw['input_text']);kw['on_stdout'](fixtures.RAW)
   if on_send:on_send(kw['input_text'])
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),fixtures.RAW,b''
  with ExitStack() as s:
   s.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC))
   s.enter_context(patch('studio.native_codex_exec_probe.require_exec_capability',side_effect=preflight))
   s.enter_context(patch('studio.native_codex_exec_probe.require_native_write_capability'))
   s.enter_context(patch('studio.native_codex_exec.check_protected_hardlinks'))
   s.enter_context(patch('orchestration.process.execute',side_effect=send))
   result=None;error=None
   try:result=LocalCodex(b,dict(step_timeout_seconds=5,max_log_bytes=100000)).start('task',str(self.work),self.art,store=Store(),run='r')
   except Exception as exc:error=exc
  return result,error,events,sent,b
 def test_completion_callback_source_change_returns_original_receipt(self):
  def change(status):
   if status=='AGENT_COMPLETED':(self.source/'references/a.txt').write_text('after completion')
  result,error,events,sent,b=self.run_with(on_finish=change)
  self.assertIsNone(error);self.assertEqual(len(sent),1);self.assertEqual([e[0] for e in events],['AGENT_SESSION','AGENT_COMPLETED'])
  self.assertEqual(result['skills'][0]['sha256'],b['skills'][0]['sha256']);self.assertEqual(result['session_id'],fixtures.SID)
 def test_post_send_source_and_pack_drift_record_failed_with_process(self):
  for which in ('source','pack'):
   with self.subTest(which=which):
    def change(prompt):
     if which=='source':(self.source/'references/a.txt').write_text('changed')
     else:
      p=Path(prompt.split('资源根: ')[1].split('\n')[0])/'references/a.txt';p.chmod(0o600);p.write_text('changed')
    result,error,events,sent,b=self.run_with(on_send=change)
    self.assertIsNone(result);self.assertIsInstance(error,Blocked);self.assertIn('调用已经发生',str(error));self.assertIn('未批准且未重发',str(error));self.assertNotIn('未发送',str(error))
    self.assertEqual(len(sent),1);self.assertEqual([e[0] for e in events],['AGENT_SESSION','AGENT_FAILED'])
    self.assertEqual(events[-1][1]['error_category'],'skill-pack-validation');self.assertEqual(events[-1][1]['process']['exit'],0);self.assertEqual(events[-1][1]['native_session'],fixtures.SID);self.assertIn('调用已经发生',events[-1][1]['visible_error'])
    (self.source/'references/a.txt').write_text('fixture reference')
 def test_pre_send_source_drift_blocks_without_transport(self):
  def drift(*a,**kw):(self.source/'references/a.txt').write_text('changed')
  result,error,events,sent,b=self.run_with(preflight=drift)
  self.assertIsNone(result);self.assertIsInstance(error,Blocked);self.assertIn('未发送',str(error));self.assertEqual(sent,[]);self.assertEqual(events,[])
 def test_cleanup_oserror_preserves_completed_receipt_with_warning(self):
  roots=[]
  def denied(root,*,dir_fd,onerror):roots.append(Path(__import__('os').readlink('/proc/self/fd/'+str(dir_fd))));raise OSError('synthetic cleanup denied')
  with patch('studio.native_skill_pack.shutil.rmtree',side_effect=denied):
   result,error,events,sent,b=self.run_with()
  self.assertIsNone(error);self.assertEqual(len(sent),1);self.assertEqual(events[-1][0],'AGENT_COMPLETED')
  warning=result['skill_cleanup'];self.assertEqual(warning['status'],'cleanup_failed');self.assertEqual(events[-1][1]['skill_cleanup'],warning)
  self.assertEqual(result['skills'][0]['cleanup_warning'],warning);self.assertTrue(roots[0].exists())
  import shutil;shutil.rmtree(roots[0])
 def test_cleanup_replaced_root_preserves_replacement_and_warns(self):
  original=NativeSkillPack.cleanup;paths=[]
  def replace(pack):
   if not pack.cleanup_attempted:
    moved=pack.root.with_name(pack.root.name+'-moved');pack.root.rename(moved);pack.root.mkdir(mode=0o700);(pack.root/'do-not-remove').write_text('replacement');paths.extend([pack.root,moved])
   return original(pack)
  with patch.object(NativeSkillPack,'cleanup',replace):result,error,events,sent,b=self.run_with()
  self.assertIsNone(error);self.assertEqual(len(sent),1);self.assertEqual(events[-1][0],'AGENT_COMPLETED');self.assertEqual(result['skill_cleanup']['status'],'identity_changed')
  self.assertTrue((paths[0]/'do-not-remove').exists());self.assertTrue(paths[1].exists())
  import shutil
  for path in paths:shutil.rmtree(path)
 def test_root_replaced_during_cleanup_never_traverses_replacement(self):
  import os,shutil
  original=shutil.rmtree;paths=[]
  def replace(path,*,dir_fd,onerror):
   root=Path(os.readlink('/proc/self/fd/'+str(dir_fd)));moved=root.with_name(root.name+'-moved')
   root.rename(moved);root.mkdir(mode=0o700);(root/'do-not-remove').write_text('replacement');paths.extend([root,moved])
   return original(path,dir_fd=dir_fd,onerror=onerror)
  with patch('studio.native_skill_pack.shutil.rmtree',side_effect=replace):result,error,events,sent,b=self.run_with()
  self.assertIsNone(error);self.assertEqual(result['skill_cleanup']['status'],'identity_changed');self.assertEqual(events[-1][0],'AGENT_COMPLETED')
  self.assertTrue((paths[0]/'do-not-remove').exists());self.assertTrue(paths[1].exists())
  for path in paths:shutil.rmtree(path)
 def test_replacement_after_final_stat_is_not_removed(self):
  import os,shutil
  from studio.native_skill_pack import _cleanup
  root=self.root/'owned-pack';root.mkdir(mode=0o700);(root/'resource').write_text('synthetic');st=root.stat();moved=self.root/'moved-pack';real=os.stat;trigger=[]
  def race(path,*args,**kwargs):
   result=real(path,*args,**kwargs)
   if path==root.name and kwargs.get('dir_fd') is not None and not trigger:
    trigger.append(True);root.rename(moved);root.mkdir(mode=0o700)
   return result
  with patch('studio.native_skill_pack.os.stat',side_effect=race):result=_cleanup(root,(st.st_dev,st.st_ino))
  self.assertTrue(trigger);self.assertTrue(root.is_dir());self.assertTrue(moved.is_dir());self.assertEqual(list(moved.iterdir()),[]);self.assertEqual(result['status'],'identity_changed')
  self.assertEqual((moved.stat().st_dev,moved.stat().st_ino),(st.st_dev,st.st_ino));self.assertEqual(moved.stat().st_mode&0o777,0o700)
 def test_cleanup_fd_failure_is_visible_and_does_not_remove_root(self):
  from studio.native_skill_pack import _cleanup
  root=self.root/'owned';root.mkdir(mode=0o700);(root/'data').write_text('synthetic');st=root.stat()
  with patch('studio.native_skill_pack.os.open',side_effect=OSError('synthetic fd exhaustion')):
   result=_cleanup(root,(st.st_dev,st.st_ino))
  self.assertEqual(result['status'],'cleanup_failed');self.assertTrue((root/'data').exists())
 def test_cleanup_rejects_changed_root_permissions(self):
  from studio.native_skill_pack import _cleanup
  root=self.root/'owned';root.mkdir(mode=0o700);(root/'data').write_text('synthetic');st=root.stat();root.chmod(0o755)
  result=_cleanup(root,(st.st_dev,st.st_ino))
  self.assertEqual(result['status'],'cleanup_failed');self.assertTrue((root/'data').exists())
 def test_cleanup_error_does_not_hide_validation_error(self):
  roots=[]
  def denied(root,*,dir_fd,onerror):roots.append(Path(__import__('os').readlink('/proc/self/fd/'+str(dir_fd))));raise OSError('synthetic cleanup denied')
  def change(prompt):(self.source/'references/a.txt').write_text('changed')
  with patch('studio.native_skill_pack.shutil.rmtree',side_effect=denied):result,error,events,sent,b=self.run_with(on_send=change)
  self.assertIsNone(result);self.assertIsInstance(error,Blocked);self.assertIn('调用已经发生',str(error));self.assertIn('清理失败',str(error));self.assertEqual(events[-1][0],'AGENT_FAILED');self.assertEqual(len(sent),1)
  import shutil;shutil.rmtree(roots[0])
 def test_actual_journal_preserves_failed_validation_session_and_exit(self):
  from types import SimpleNamespace
  from studio.usable_store import Storage
  from studio.workbench_store import WorkbenchStore
  from studio.workbench_scheduler import Journal
  b=self.bind();flow=WorkbenchStore(self.root/'flow.sqlite');host=Storage(self.root/'journal-storage')
  flow.put('run','r',dict(id='r',state='RUNNING'));flow.put('activation','a',dict(id='a',run='r',state='RUNNING',chat_key='c'))
  flow.put('node-session','c',dict(id='c',workspace='w',binding=b));journal=Journal(SimpleNamespace(db=flow,rt=SimpleNamespace(db=host),run='r'),'a');sent=[]
  class RPC:
   def __init__(self,*a):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  def execute(*a,**kw):
   sent.append(True);kw['on_stdout'](fixtures.RAW);(self.source/'references/a.txt').write_text('changed')
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),fixtures.RAW,b''
  with ExitStack() as s:
   s.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC));s.enter_context(patch('studio.native_codex_exec_probe.require_exec_capability'));s.enter_context(patch('studio.native_codex_exec_probe.require_native_write_capability'));s.enter_context(patch('studio.native_codex_exec.check_protected_hardlinks'));s.enter_context(patch('orchestration.process.execute',side_effect=execute))
   with self.assertRaisesRegex(Blocked,'调用已经发生'):LocalCodex(b,dict(step_timeout_seconds=5,max_log_bytes=100000)).start('task',str(self.work),self.art,store=journal,run='r')
  step=flow.list('native-step')[0];activation=flow.get('activation','a')
  self.assertEqual(step['status'],'AGENT_FAILED');self.assertEqual(step['data']['process']['exit'],0);self.assertEqual(step['data']['native_session'],fixtures.SID)
  self.assertEqual(activation['observed_session'],fixtures.SID);self.assertIn('调用已经发生',activation['visible_error']);self.assertEqual(len(sent),1)
