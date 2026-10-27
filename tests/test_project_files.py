"""Project persistence, real filesystem isolation and portable history negatives.

Synthetic conversation records test persistence, not real model acceptance.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from test_workbench_runtime import fixture, structured_session
from orchestration.common import Blocked, canonical, tree_hash, workspace_hash, git
from orchestration.process import execute
from studio.graph import Invalid
from studio.server import Conflict
from studio.project_files import ProjectFiles, archive_completed
from studio.workbench_examples import examples
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_runtime import WorkbenchRuntime
from studio.workflow_library import WorkflowLibrary
from studio.canvas_assistant import CanvasAssistant


class ProjectFilesTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.w = self.base.project['id']
        self.files = self.rt.project_files(self.w)
        self.g = examples(self.rt.operator['profiles'])[0]

    def test_open_creates_private_managed_storage_and_git_ignores_it(self):
        meta = json.loads((self.files.root / 'project.json').read_text())
        self.assertIs(meta['authority'], False)
        self.assertEqual(self.files.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(git(self.base.source,'check-ignore','.devflower/project.json'),'.devflower/project.json')

    def test_save_is_project_local_and_works_after_host_design_cache_removed(self):
        row = self.files.save_draft('saved', self.g, None)
        with self.rt.flow.tx() as db: db.execute("DELETE FROM records WHERE kind='draft'")
        self.rt.open_workspace(str(self.base.source))
        drafts, errors = self.rt.project_drafts()
        self.assertFalse(errors)
        self.assertIn(row, drafts)
        with self.files.library() as store:
            self.assertEqual(store.get('draft', 'saved')['graph'], self.g)

    def test_two_readers_cas_rejects_lost_update(self):
        row = self.files.save_draft('saved', self.g, None)
        other = ProjectFiles(self.rt, self.w)
        newer = copy.deepcopy(self.g); newer['name'] = 'new version'
        other.save_draft('saved', newer, row['revision'])
        with self.assertRaises(Conflict): self.files.save_draft('saved', self.g, row['revision'])

    def test_reopen_never_overwrites_local_revision_with_old_host_cache(self):
        row = self.files.save_draft('saved', self.g, None)
        newer = copy.deepcopy(self.g); newer['name'] = 'local newest'
        self.files.save_draft('saved', newer, row['revision'])
        self.rt.flow.put('draft', 'saved', row)
        self.rt.open_workspace(str(self.base.source))
        with self.files.library() as store: self.assertEqual(store.get('draft','saved')['graph']['name'], 'local newest')

    def test_deleted_draft_is_not_resurrected_by_migration(self):
        row = self.files.save_draft('saved', self.g, None)
        self.files.delete_draft('saved', row['revision'])
        self.rt.flow.put('draft','saved',row)  # simulate an old hosted backup
        self.rt.open_workspace(str(self.base.source))
        self.assertNotIn('saved', [r['id'] for r in self.rt.project_drafts()[0]])
        with self.files.library() as store, store.tx(read=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0],1)
        with self.assertRaises(Conflict):self.files.save_draft('saved',self.g,None)

    def test_history_migration_does_not_block_open_or_duplicate_worker(self):
        import threading
        from studio.project_files import ProjectSync
        entered=threading.Event();release=threading.Event();sync=ProjectSync(self.rt)
        self.rt.flow.put('run','old',dict(id='old',workspace=self.w,state='COMPLETED'))
        def slow(files,run):entered.set();release.wait(5)
        try:
            with patch.object(ProjectFiles,'run',slow),patch.object(ProjectFiles,'capture_sessions',return_value=0):
                self.rt.open_workspace(str(self.base.source))
                self.assertFalse(entered.is_set())
                sync.start(self.w);self.assertTrue(entered.wait(2));first=sync.workers[self.w]
                sync.start(self.w);self.assertIs(sync.workers[self.w],first)
                release.set();first.join(2)
                self.assertEqual(self.rt.db.get('project-migration',self.w)['state'],'COMPLETED')
        finally:release.set();sync.close()

    def test_active_run_prevents_local_delete(self):
        row = self.files.save_draft('saved',self.g,None)
        self.rt.flow.put('run','active',dict(id='active',workspace=self.w,draft_id='saved',state='RECOVERY_REQUIRED'))
        with self.assertRaises(Conflict):self.files.delete_draft('saved',row['revision'])

    def test_legacy_templates_migrate_without_removing_host_records(self):
        row = WorkflowLibrary(self.rt).save(dict(workspace=self.w,graph=self.g,name='favorite'))
        with self.rt.flow.tx() as db:db.execute("DELETE FROM records WHERE kind LIKE 'workflow-template%'")
        self.rt.open_workspace(str(self.base.source))
        self.assertEqual(WorkflowLibrary(self.rt).listing(self.w)['templates'][0]['id'],row['id'])

    def test_unknown_dot_directory_is_preserved(self):
        folder = self.base.source.parent / 'ordinary'; folder.mkdir()
        (folder/'.devflower').mkdir(); (folder/'.devflower'/'mine.txt').write_text('user data')
        row=self.rt.open_workspace(str(folder));self.assertEqual(row['record_location'],'host')
        self.assertEqual((folder/'.devflower'/'mine.txt').read_text(),'user data')

    def test_linked_metadata_and_archive_cannot_target_credentials(self):
        target=self.base.source.parent/'target';target.mkdir()
        folder=self.base.source.parent/'linked';folder.mkdir();(folder/'.devflower').symlink_to(target,target_is_directory=True)
        row=self.rt.open_workspace(str(folder));self.assertEqual(row['record_location'],'host');self.assertEqual(list(target.iterdir()),[])
        with self.assertRaises(Blocked):self.rt.validate_source(str(folder))
        name='session-'+__import__('hashlib').sha256(b'evil').hexdigest()+'.json'
        private=self.base.source.parent/'secret.json';private.write_text('private')
        (self.files.root/name).symlink_to(private)
        with self.assertRaises((Blocked,ValueError)):self.files.save_archive('session','evil',{})
        self.assertEqual(private.read_text(),'private')

    def test_record_writes_do_not_change_source_hash_but_complete_tree_still_changes(self):
        source=workspace_hash(self.base.source);complete=tree_hash(self.base.source)
        self.files.save_draft('test',self.g,None)
        self.files.save_archive('assistant','a',dict(id='a',messages=[]))
        self.assertEqual(source,workspace_hash(self.base.source))
        self.assertNotEqual(complete,tree_hash(self.base.source))
        (self.base.source/'real.py').write_text('real source')
        self.assertNotEqual(source,workspace_hash(self.base.source))

    def test_source_snapshot_requires_explicit_private_data_exclusion(self):
        from orchestration.common import snapshot
        with self.assertRaises(Blocked):snapshot(self.base.source,self.base.source.parent/'bad-freeze')
        target=self.base.source.parent/'safe-freeze'
        snapshot(self.base.source,target,excludes=('.devflower',))
        self.assertFalse((target/'.devflower').exists())

    def test_copy_project_imports_designs_without_native_writers(self):
        g=copy.deepcopy(self.g);g['nodes'][1]['session']='old-native-handle'
        self.files.save_draft('portable',g,None)
        folder=self.base.source.parent/'copied';folder.mkdir()
        with self.files.library() as local,local.tx(read=True) as db:
            import sqlite3
            (folder/'.devflower').mkdir(mode=0o700)
            shutil.copyfile(self.files.root/'project.json',folder/'.devflower/project.json')
            target=sqlite3.connect(folder/'.devflower/library.sqlite');db.backup(target);target.close()
        row=self.rt.open_workspace(str(folder))
        with self.rt.project_files(row['id']).library() as store:
            design=store.get('draft','portable')
        self.assertEqual(design['workspace'],row['id']);self.assertEqual(design['graph']['nodes'][1]['session'],'new')
        self.assertTrue(row['execution_allowed']);self.assertEqual(self.rt.flow.list('permit'),[]);self.assertEqual(self.rt.flow.list('run'),[])

    def test_tracked_metadata_is_never_hidden_from_source_identity(self):
        git(self.base.source,'add','-f','.devflower/project.json')
        with self.assertRaises(Blocked):self.rt.validate_source(str(self.base.source))

    def test_workspace_write_agent_cannot_read_or_change_project_records(self):
        marker=self.files.root/'project.json';before=marker.read_bytes()
        home=self.base.source.parent/'isolated-agent-home'
        result,out,err=execute(['/usr/bin/python3','-B','-c',
            "from pathlib import Path; p=Path('.devflower'); assert list(p.iterdir())==[]; "
            "\ntry: (p/'tamper').write_text('bad')\nexcept OSError: pass\nelse: raise AssertionError('writable metadata')\nPath('agent-result.txt').write_text('allowed')"],
            self.base.source,home,read=[str(self.base.source)],write=[str(self.base.source)],backend='bwrap')
        self.assertEqual(result['exit'],0,err.decode());self.assertEqual(marker.read_bytes(),before)
        self.assertEqual((self.base.source/'agent-result.txt').read_text(),'allowed')
        self.assertFalse((self.files.root/'tamper').exists())

    def test_project_container_opens_but_never_becomes_execution_or_native_scan_root(self):
        container=self.base.source.parent
        value=self.rt.open_workspace(str(container))
        self.assertTrue(value['protected_container']);self.assertFalse(value['execution_allowed'])
        self.assertTrue((container/'.devflower/project.json').exists())
        with self.assertRaises(Blocked):self.rt.node_binding(value['id'],self.g['nodes'][1],admission=False)
        with patch('studio.session_center.nested_profiles',side_effect=AssertionError('must not scan private subtrees')):
            self.assertEqual(self.rt.session_center.discover(value['id'])['discovery']['sessions'],[])

    def test_explicitly_selected_project_saves_without_starting_or_granting_model_admission(self):
        folder=self.base.source.parent/'unapproved';folder.mkdir()
        value=self.rt.open_workspace(str(folder));files=self.rt.project_files(value['id'])
        files.save_draft('saved',self.g,None)
        self.assertTrue(value['execution_allowed'])
        self.assertEqual(self.rt.node_binding(value['id'],self.g['nodes'][1],admission=False)['cwd'],str(folder))
        self.assertEqual(self.rt.flow.list('run'),[])
        self.assertEqual(self.rt.db.listing('model-admission',limit=None),[])
        from studio.configuration_assistant import ConfigurationAssistant
        with patch.object(self.rt,'validate_source',side_effect=AssertionError('draft design must not validate executable source')):
            base=ConfigurationAssistant(self.rt)._base(dict(workspace=value['id'],graph=self.g),canvas_only=True)
            ConfigurationAssistant(self.rt)._source(base)
        self.assertIsNone(base['source_sha256'])

    def test_project_data_cannot_be_context_or_attachment_input(self):
        n=dict(self.g['nodes'][1],context_refs=['.devflower/project.json'])
        with self.assertRaises(Blocked):self.rt.node_binding(self.w,n,admission=False)
        self.assertFalse(self.rt.attachments(str(self.base.source),['.devflower/project.json'])[0]['accessible'])

    def test_native_history_survives_missing_native_host_as_read_only_copy(self):
        path,row=structured_session(self.base,self.rt,'portable')
        manager=WorkbenchSessions(self.rt);native=next(s for s in manager.listing(self.w)['sessions'] if s['handle']==row['handle'])
        self.files.session(native,manager.history(self.w,row['handle']))
        path.unlink()  # synthetic test-owned transcript, never a user's history
        saved=next(s for s in manager.listing(self.w)['sessions'] if s.get('saved_history'))
        self.assertFalse(saved['choices']['resume']);self.assertEqual(saved['display_state'],'UNKNOWN')
        with patch('subprocess.Popen',side_effect=AssertionError('no replay')):
            history=manager.history(self.w,saved['handle'])
        self.assertIn('final answer',json.dumps(history));self.assertTrue(history['saved_history'])
        self.assertNotIn('HIDDEN_RESULT_REASONING',json.dumps(history))

    def test_run_archive_is_never_imported_as_runtime_or_gate_authority(self):
        structured_session(self.base,self.rt,'portable-run')
        self.files.run('portable-run-run')
        with self.rt.flow.tx() as db:db.execute("DELETE FROM records WHERE kind IN ('run','activation','node-session')")
        saved=self.rt.saved_runs();self.assertEqual(len(saved),1);self.assertTrue(saved[0]['saved_history'])
        self.assertEqual(saved[0]['state'],'UNKNOWN');self.assertEqual(self.rt.flow.list('run'),[])
        self.assertEqual(self.rt.db.listing('model-admission',limit=None),[])

    def test_assistant_archive_opens_without_restoring_turn_or_proposal(self):
        key='a'*32
        self.files.assistant(dict(id=key,workspace=self.w,title='answer',messages=[dict(role='user',text='question'),dict(role='assistant',text='answer')],
            active_turn='fake-turn',receipt={'approved':True},revision=2))
        assistant=CanvasAssistant(self.rt);self.addCleanup(assistant.shutdown)
        listed=assistant.listing(self.w)['conversations'];self.assertTrue(listed[0]['saved_history'])
        row=assistant.conversation(self.w,key)
        self.assertIsNone(row['turn']);self.assertTrue(row['saved_history']);self.assertEqual(row['collection'],'reading-copy')
        self.assertNotIn('receipt',row);self.assertEqual(self.rt.flow.list('canvas-turn'),[])

    def test_archive_failure_is_visible_and_does_not_replay_or_change_runtime(self):
        with patch.object(ProjectFiles,'capture_sessions',side_effect=OSError('disk full')),patch('subprocess.Popen',side_effect=AssertionError('no retry')):
            archive_completed(self.rt,self.w)
        self.assertTrue(self.rt.db.get('project-archive-status',self.w)['error'])


if __name__=='__main__':unittest.main()
