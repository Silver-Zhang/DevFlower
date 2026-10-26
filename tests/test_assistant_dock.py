"""Synthetic UX contracts and identity-cache mutation counterexamples."""
import json,os,stat,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_canvas_assistant as ca
SyntheticBackend=ca.SyntheticBackend
import test_session_configuration as sc
from studio.identity_cache import IdentityCache
from studio.session_sources import Refused
from orchestration.common import Blocked,tree_hash,digest
from studio.server import Conflict

class AssistantHistoryTests(unittest.TestCase):
 setUp=ca.CanvasAssistantTests.setUp
 data=ca.CanvasAssistantTests.data
 wait=ca.CanvasAssistantTests.wait
 run_turn=ca.CanvasAssistantTests.run_turn
 def row(self):return self.ai.listing(self.w)['conversations'][0]
 def manage(self,op,row=None,**kwargs):
  row=row or self.row();return self.ai.manage(dict(workspace=self.w,id=row['id'],operation=op,expected=row['metadata_revision'],**kwargs))
 def test_list_title_history_and_workspace_identity(self):
  v=self.run_turn();row=self.row();self.assertEqual(row['id'],v['id']);self.assertEqual(row['title'],'创建只读分析');self.assertEqual(row['message_count'],2)
  with patch.object(self.rt.session_center,'workspace',return_value={}):self.assertEqual(self.ai.listing('another')['conversations'],[])
 def test_rename_cas_does_not_change_conversation_revision(self):
  v=self.run_turn();row=self.row();self.manage('rename',row,title='我的分析');self.assertEqual(self.ai.conversation(self.w,v['id'])['revision'],v['revision']);self.assertEqual(self.row()['title'],'我的分析')
  with self.assertRaises(Conflict):self.manage('rename',row,title='stale')
 def test_archive_and_restore_keep_messages_and_do_not_infer(self):
  v=self.run_turn();self.manage('archive');self.assertEqual(self.ai.listing(self.w)['conversations'],[]);row=self.ai.listing(self.w,'archived')['conversations'][0]
  with self.assertRaises(Blocked):self.ai.start(self.data(conversation=v['id'],expected=v['revision']))
  self.manage('restore',row);self.assertEqual(self.ai.conversation(self.w,v['id'])['messages'],v['messages']);self.assertEqual(len(SyntheticBackend.calls),1)
 def test_trash_is_recoverable_not_native_deletion(self):
  v=self.run_turn();self.manage('trash');row=self.ai.listing(self.w,'trash')['conversations'][0];self.manage('restore',row);self.assertEqual(len(self.ai.conversation(self.w,v['id'])['messages']),2);self.assertEqual(self.rt.flow.list('run'),[])
 def test_running_request_cannot_be_archived_or_trashed(self):
  SyntheticBackend.delay=.3;v=self.ai.start(self.data())
  for op in ('archive','trash'):
   with self.assertRaises(Blocked):self.manage(op)
  self.wait(v['id'])
 def test_rename_during_generation_is_not_lost(self):
  SyntheticBackend.delay=.3;v=self.ai.start(self.data());self.manage('rename',title='生成中的新名字');self.wait(v['id']);self.assertEqual(self.row()['title'],'生成中的新名字')
 def test_search_and_refresh_do_not_call_model(self):
  self.run_turn();self.assertEqual(len(self.ai.listing(self.w,query='创建')['conversations']),1);self.assertEqual(self.ai.listing(self.w,query='missing')['conversations'],[]);self.assertEqual(len(SyntheticBackend.calls),1)
 def test_foreign_workspace_cannot_manage_even_with_known_id(self):
  self.run_turn();row=self.row()
  with patch.object(self.rt.session_center,'workspace',return_value={}),self.assertRaises(Blocked):self.ai.manage(dict(workspace='another',id=row['id'],operation='trash',expected=0))
 def test_dead_owner_is_unknown_not_fake_running_in_history(self):
  v=self.run_turn();self.rt.flow.change('canvas-turn',v['turn']['id'],dict(state='RUNNING',owner=None));self.assertEqual(self.row()['state'],'RECOVERY_REQUIRED')
 def test_catalog_reopening_reuses_cache_explicit_refresh_queries(self):
  with patch.object(SyntheticBackend,'catalog',autospec=True,return_value={'models':[]}) as call:
   self.ai.catalog();self.ai.catalog();self.assertEqual(call.call_count,1);self.ai.catalog(refresh=True);self.assertEqual(call.call_count,2)

class IdentityCacheTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.file=self.root/'data';self.file.write_bytes(b'original');self.cache=IdentityCache()
 def test_cache_reuses_hash_but_not_old_file_bytes_after_edit(self):
  first=self.cache.file(self.file)
  with patch('studio.identity_cache.os.read',side_effect=AssertionError('unchanged bytes should not be reread')):self.assertEqual(self.cache.file(self.file),first)
  self.file.write_bytes(b'modified');self.assertNotEqual(self.cache.file(self.file),first)
 def test_same_size_and_restored_mtime_cannot_hide_change(self):
  before=self.file.stat();first=self.cache.file(self.file);self.file.write_bytes(b'changed!');os.utime(self.file,ns=(before.st_atime_ns,before.st_mtime_ns));self.assertNotEqual(self.cache.file(self.file),first)
 def test_replaced_inode_with_old_timestamp_is_rehashed(self):
  first=self.cache.file(self.file);before=self.file.stat();other=self.root/'replacement';other.write_bytes(b'changed!');os.utime(other,ns=(before.st_atime_ns,before.st_mtime_ns));other.replace(self.file);self.assertNotEqual(self.cache.file(self.file),first)
 def test_symlink_and_hardlink_do_not_reuse_prior_certificate(self):
  self.cache.file(self.file);target=self.root/'target';target.write_bytes(b'original');self.file.unlink();self.file.symlink_to(target)
  with self.assertRaises((Refused,OSError)):self.cache.file(self.file)
  self.file.unlink();os.link(target,self.file)
  with self.assertRaises(Refused):self.cache.file(self.file)
 def test_tree_hash_matches_uncached_and_sees_added_deleted_files(self):
  self.assertEqual(self.cache.tree(self.root),tree_hash(self.root));first=self.cache.tree(self.root);p=self.root/'new';p.write_text('new');self.assertNotEqual(self.cache.tree(self.root),first);p.unlink();self.assertEqual(self.cache.tree(self.root),first)
 def test_execute_bit_and_link_target_are_part_of_tree_identity(self):
  first=self.cache.tree(self.root);self.file.chmod(0o755);self.assertNotEqual(self.cache.tree(self.root),first);p=self.root/'link';p.symlink_to('data');self.assertEqual(self.cache.tree(self.root),tree_hash(self.root));second=self.cache.tree(self.root);p.unlink();p.symlink_to('other');self.assertNotEqual(self.cache.tree(self.root),second)
 def test_change_while_hashing_is_rejected(self):
  original=os.read;once=[False]
  def read(fd,n):
   data=original(fd,n)
   if data and not once[0]:once[0]=True;self.file.write_bytes(b'changed!')
   return data
  with patch('studio.identity_cache.os.read',side_effect=read),self.assertRaises(Blocked):self.cache.file(self.file)
 def test_missing_tree_root_is_not_an_empty_trusted_tree(self):
  with self.assertRaises(OSError):self.cache.tree(self.root/'gone')

class DirectModelSelectionTests(unittest.TestCase):
 setUp=sc.RegistrationTests.setUp
 catalog=sc.RegistrationTests.catalog
 def select(self,**changes):return self.reg.select(dict(dict(workspace=self.w,profile=self.name,model='discovered-model',effort='high'),**changes))
 def test_direct_selection_does_not_require_second_form_or_grant_execution(self):
  result=self.select();self.assertTrue(result['configured']);self.assertFalse(result['execution_allowed']);binding=self.rt.node_binding(self.w,dict(profile=self.name,model='discovered-model',effort='high',mode='Ask'),admission=False)
  from studio.model_admission import ModelAdmission
  self.assertFalse(ModelAdmission(self.rt).status(binding['binding'],binding['cwd'])['execution_allowed'])
 def test_stale_catalog_and_changed_profile_are_not_faked_as_valid(self):
  self.catalog(at=time.time()-301)
  with self.assertRaises(Blocked):self.select()
  self.catalog(environment_sha256='changed')
  with self.assertRaises(Blocked):self.select()
 def test_unknown_model_and_effort_are_rejected(self):
  for changes in ({'model':'unknown'},{'effort':'made-up'}):
   with self.subTest(changes=changes),self.assertRaises((Blocked,ValueError)):self.select(**changes)
