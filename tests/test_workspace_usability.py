"""Actual filesystem/HTTP boundaries; synthetic Provider replies are labelled."""
import copy,json,os,secrets,time,unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from test_workbench_runtime import fixture,structured_session
import test_canvas_assistant as assistant_harness
from test_canvas_assistant import SyntheticBackend
from orchestration.common import Blocked,canonical,digest,save
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_sessions import WorkbenchSessions
from studio.session_batch import SessionBatch
from studio.model_catalog import ModelCatalog
from studio.model_registration import ModelRegistration,profile_identity
from studio.canvas_assistant import decode_reply


class SelectedWorkspaceTests(unittest.TestCase):
    def setUp(self): self.base,self.rt=fixture(self)
    def outside(self):
        path=self.base.source.parent/'chosen-project';path.mkdir();return path
    def test_browse_does_not_grant_but_explicit_selection_allows_normal_folder(self):
        path=self.outside();v=self.rt.folder(str(path));self.assertTrue(v['openable'])
        with self.assertRaises(Blocked):self.rt.validate_source(str(path))
        w=self.rt.open_workspace(str(path));self.assertTrue(w['execution_allowed'])
        self.assertEqual(self.rt.node_binding(w['id'],dict(profile='default-codex',mode='Ask'),admission=False)['cwd'],str(path))
        self.assertTrue((path/'.devflower/project.json').exists());self.assertEqual(self.rt.flow.list('run'),[])
    def test_copied_metadata_alone_cannot_grant_execution(self):
        path=self.outside();meta=path/'.devflower';meta.mkdir();save(meta/'project.json',dict(version=1,kind='devflower-project',authority=False))
        with self.assertRaises(Blocked):self.rt.validate_source(str(path))
    def test_selected_directory_replacement_invalidates_host_scope(self):
        path=self.outside();self.rt.open_workspace(str(path));path.rename(path.with_name('old-project'));path.mkdir()
        with self.assertRaises(Blocked):self.rt.validate_source(str(path))
    def test_selection_does_not_authorize_sibling_or_child(self):
        path=self.outside();self.rt.open_workspace(str(path));child=path/'child';child.mkdir()
        with self.assertRaises(Blocked):self.rt.validate_source(str(child))
    def test_protected_and_system_folders_stay_readable_without_execution(self):
        for path in (self.rt.native,self.rt.storage,self.rt.storage.parent,Path('/etc')):
            w=self.rt.open_workspace(str(path));self.assertFalse(w['execution_allowed'])
            with self.assertRaises(Blocked):self.rt.validate_source(str(path))
    def test_selected_scope_survives_restart_but_not_operator_change(self):
        path=self.outside();self.rt.open_workspace(str(path));self.assertEqual(self.rt.validate_source(str(path))['root'],str(path))
        self.rt.operator_sha256='different'
        with self.assertRaises(Blocked):self.rt.validate_source(str(path))


class CopiedGroupTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt);self.batch=SessionBatch(self.manager)
        self.files=self.rt.project_files(self.w)
        self.saved=dict(handle='copied-native-key',title='保存的对话',provider='claude',collection='active',session_id='unknown',workflow='复制流程',workflow_group_id='draft:copied',workflow_id='old-run',updated=10)
        self.history=dict(messages=[dict(role='assistant',text='SYNTHETIC saved answer')])
        self.files.session(self.saved,self.history)
    def prepare(self):return self.batch.prepare(dict(workspace=self.w,workflow='draft:copied'))
    def confirm(self,p):return self.batch.confirm(p['id'],p['sha256'],'清理已展示的会话，保留历史')
    def test_reading_copy_group_delete_is_reproducible_and_recoverable(self):
        p=self.prepare();v=self.confirm(p);self.assertEqual(v['trashed'],1)
        self.assertFalse(v['native_modified']);self.assertFalse(self.manager.listing(self.w)['sessions'])
        row=self.manager.listing(self.w,'trash')['sessions'][0];self.assertTrue(row['saved_history'])
        self.assertEqual(self.manager.history(self.w,row['handle'])['messages'],self.history['messages'])
        self.manager.change(self.w,row['handle'],'restore');self.assertEqual(len(self.manager.listing(self.w)['sessions']),1)
    def test_archive_change_aborts_without_partial_trash(self):
        p=self.prepare();self.files.session(self.saved,dict(messages=[dict(role='assistant',text='Changed')]))
        with self.assertRaises(Conflict):self.confirm(p)
        self.assertEqual(self.rt.flow.list('project-session-meta'),[])
    def test_mixed_native_and_copied_group_is_atomic(self):
        path,row=structured_session(self.base,self.rt,'live-member');before=path.read_bytes()
        self.rt.flow.change('run','live-member-run',dict(draft_id='copied'))
        self.assertEqual(self.confirm(self.prepare())['trashed'],2);self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(self.manager.listing(self.w,'trash')['sessions']),2)
    def test_busy_native_prevents_copied_group_change(self):
        structured_session(self.base,self.rt,'busy-member');self.rt.flow.change('run','busy-member-run',dict(draft_id='copied'))
        with patch.object(self.manager,'busy',return_value=True),self.assertRaises(Blocked):self.prepare()
        self.assertEqual(self.rt.flow.list('project-session-meta'),[])
    def test_saved_handle_cannot_be_used_as_native_writer(self):
        handle=self.manager.listing(self.w)['sessions'][0]['handle']
        with self.assertRaises(Invalid):self.rt.session_center.resolve(self.w,handle)
        with self.assertRaises(Blocked):self.manager.change(self.w,handle,'native_archive',answer='Do it')
    def test_saved_metadata_race_returns_conflict_without_lost_classification(self):
        handle=self.manager.listing(self.w)['sessions'][0]['handle'];key,_,_=self.manager.saved_metadata(self.w,handle)
        original=self.rt.flow.tx
        @contextmanager
        def concurrent_change(*,read=False):
            if read:
                with original(read=True) as db:yield db
                return
            with original() as db:db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('project-session-meta',key,canonical(dict(id=handle,workspace=self.w,collection='trash')).decode()))
            with original() as db:yield db
        with patch.object(self.rt.flow,'tx',side_effect=concurrent_change),self.assertRaises(Conflict):self.manager.change(self.w,handle,'rename',name='New alias')
        self.assertEqual(self.rt.flow.get('project-session-meta',key)['collection'],'trash')


class EffortSelectionTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt)
        self.name='default-claude';self.profile=self.rt.operator['profiles'][self.name]
    def catalog(self,efforts=None):
        self.rt.flow.put('cli-probe',self.name,dict(available=True,at=time.time(),executable_sha256=digest(Path(self.profile['executable']).resolve().read_bytes()),efforts=efforts or ['low','medium','high','xhigh','max']))
        with patch('studio.model_catalog.proxy_models',return_value=[dict(id=self.profile['model'],name='Synthetic model',efforts=[],source='SYNTHETIC proxy')]),patch.object(self.manager,'rpc',return_value={}):
            return ModelCatalog(self.manager).get(self.name,self.profile,True,self.w)
    def test_profile_high_does_not_hide_cli_effort_choices(self):
        v=self.catalog();row=v['models'][0];self.assertIn('max',row['selectable_efforts']);self.assertEqual(row['native_efforts'],[])
        self.assertFalse(row['execution_allowed']);self.assertIn('model support requires execution',row['selectable_effort_source'])
    def test_nondefault_effort_selection_remains_unverified(self):
        self.catalog();v=ModelRegistration(self.rt).select(dict(workspace=self.w,profile=self.name,model=self.profile['model'],effort='max'))
        self.assertEqual(v['effort'],'max');self.assertFalse(v['execution_allowed']);self.assertEqual(self.rt.db.listing('model-admission',limit=None),[])
    def test_stale_or_different_cli_probe_never_grants_choices(self):
        self.catalog();probe=self.rt.flow.get('cli-probe',self.name);probe['at']-=400;self.rt.flow.put('cli-probe',self.name,probe)
        v=ModelCatalog(self.manager).get(self.name,self.profile,False,self.w);self.assertEqual(v['models'][0]['selectable_efforts'],[self.profile['effort']])
        with self.assertRaises(Invalid):ModelRegistration(self.rt).select(dict(workspace=self.w,profile=self.name,model=self.profile['model'],effort='invented'))


class AssistantRunTests(unittest.TestCase):
    setUp=assistant_harness.CanvasAssistantTests.setUp
    data=assistant_harness.CanvasAssistantTests.data
    wait=assistant_harness.CanvasAssistantTests.wait
    run_turn=assistant_harness.CanvasAssistantTests.run_turn
    # Reuse the established bounded synthetic direct-API harness, not a model verdict.
    def test_run_request_binds_current_canvas_without_execution_or_approval(self):
        reply=dict(reply='请确认本次运行',plan=None,run_request=dict(task='回答当前任务'))
        with patch.object(SyntheticBackend,'chat',return_value=dict(text=json.dumps(reply))):v=self.run_turn()
        self.assertEqual(v['turn']['state'],'COMPLETED');r=v['turn']['run_request']
        self.assertEqual(r['graph'],self.g);self.assertEqual(r['workspace'],self.w)
        self.assertEqual(self.rt.flow.list('run'),[]);self.assertEqual(self.rt.db.listing('permit',limit=None),[])
    def test_run_request_cannot_supply_authority_or_mutate_canvas(self):
        for request in (dict(task='x',permissions='write'),dict(task=''),dict(task='x',command='sh')):
            with self.subTest(request=request),self.assertRaises(Invalid):decode_reply(json.dumps(dict(reply='request',plan=None,run_request=request)),self.choices)
    def test_chat_only_cannot_request_execution(self):
        reply=dict(reply='run',plan=None,run_request=dict(task='x'))
        with patch.object(SyntheticBackend,'chat',return_value=dict(text=json.dumps(reply))):v=self.run_turn(self.data(intent='chat'))
        self.assertEqual(v['turn']['state'],'FAILED');self.assertEqual(self.rt.flow.list('run'),[])
