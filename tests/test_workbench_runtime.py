"""Real filesystem/HTTP runtime boundaries; synthetic native data is not model acceptance."""
from pathlib import Path
from http.server import ThreadingHTTPServer
import copy,json,os,sys,threading,time,unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parent)]
from orchestration.common import Blocked,canonical,digest,save,tree_hash,workspace_hash
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_server import create_server
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_examples import examples
from studio.workbench_scheduler import source_lease
from studio.workbench_external import validate_profile,validate_receipt
import test_agent_sessions


def fixture(owner, admission=False):
    base=test_agent_sessions.CenterTests();base.setUp();owner.addCleanup(base.doCleanups)
    old=base.development_runtime();policy=base.root/'workbench-policy.json'
    save(policy,dict(version=1,models={},external_steps={}))
    rt=WorkbenchRuntime(old.operator_file,old.operator_sha256,tree_hash(Path(__file__).resolve().parents[1]),policy,digest(policy.read_bytes()))
    base.runtime=rt;base.project=rt.open_workspace(str(base.source))
    # Reuse the old filesystem-fixture builder without adding a production API.
    rt.sessions=rt.session_center.discover
    if admission:
        # Synthetic certificates only for unrelated runtime/browser fixtures.
        # Admission adversarial tests pass admission=False and exercise the real
        # verifier. These records never constitute real Provider acceptance.
        from studio.model_admission import ModelAdmission
        gate=ModelAdmission(rt)
        for name,profile in rt.operator['profiles'].items():
            for mode in ('Ask','Plan','Agent'):
                selected=rt.node_binding(base.project['id'],dict(profile=name,mode=mode),admission=False)
                scope=gate.scope(selected['binding'],selected['cwd']);key=gate.key(selected['binding'],selected['cwd'])
                rt.db.put('model-admission',key,dict(state='VERIFIED',verified_at=time.time(),scope=scope,
                    evidence='SYNTHETIC fixture certificate, not a real model result'))
    return base,rt


def structured_session(base,rt,label='synthetic-structured',provider='claude',native_answers=False):
    """Persist the scheduler's real record shapes; no CLI/model acceptance is claimed.

    Both sessions deliberately share a UUID. HOME/profile must keep them apart.
    Claude records reproduce StructuredOutput plus 'No response requested.'.
    """
    home=rt.native/label;sid=test_agent_sessions.SID;workspace=base.project['id']
    path=test_agent_sessions.native(home/('.'+provider),provider,base.source)
    binding=dict(rt.operator['profiles']['default-'+provider],runtime_profile='default-'+provider,home=str(home),session_id=sid)
    rt.db.put('chat-profile',label,dict(workspace=workspace,binding=binding))
    run=label+'-run';chat=label+'-chat';node='loop/analyze'
    rt.flow.put('run',run,dict(id=run,workspace=workspace,state='COMPLETED',created_at=10,
        graph=examples(rt.operator['profiles'])[0],task='Synthetic structured session '+label))
    rt.flow.put('node-session',chat,dict(id=chat,run=run,node=node,workspace=workspace,
        cwd=str(base.source),binding=binding,initialized=True))
    records=[]
    for turn in (1,2):
        answer=f'SYNTHETIC {label} final answer {turn}'
        report=dict(reply=answer,summary='Synthetic transport fixture',accepted=False,result_json='{}',artifacts=[],evidence=[],issues=[],next='')
        if provider=='claude':
            records.extend([dict(type='user',sessionId=sid,message=dict(role='user',content=f'SYNTHETIC question {turn}')),
                dict(type='assistant',sessionId=sid,message=dict(role='assistant',content=[
                    dict(type='thinking',thinking='HIDDEN_RESULT_REASONING'),
                    dict(type='tool_use',name='StructuredOutput',input=report)])),
                dict(type='assistant',sessionId=sid,message=dict(role='assistant',content='No response requested.'))])
            if native_answers:records.append(dict(type='assistant',sessionId=sid,message=dict(role='assistant',content=json.dumps(report))))
        else:
            records.extend([dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text=f'SYNTHETIC question {turn}')])),
                dict(type='response_item',payload=dict(type='reasoning',summary='HIDDEN_RESULT_REASONING'))])
            if native_answers:records.append(dict(type='response_item',payload=dict(type='message',role='assistant',phase='final_answer',content=[dict(type='output_text',text=json.dumps(report))])))
        receipt=dict(session_id=sid,requested_model=binding['model'],requested_effort=binding['effort'],
            resolved_model='unknown',resolved_effort='unknown',raw_report_sha256=digest(canonical(report)),
            process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
        output=dict(answer=answer,summary=report['summary'],result={},receipt=receipt)
        aid=label+f'-turn-{turn}'
        completion=dict(run=run,activation=aid,chat_key=chat,session_id=sid,output=output,
            source_before='synthetic-source',source_after='synthetic-source')
        completion_file=rt.storage/'flow-artifacts'/run/aid/'completion.json'
        completion_file.parent.mkdir(parents=True,exist_ok=True);save(completion_file,completion)
        rt.flow.put('activation',aid,dict(id=aid,run=run,node=node,name='Synthetic '+label,kind='agent',
            chat_key=chat,provider=provider,state='COMPLETED',iteration=turn,iteration_path=[1,turn],
            started_at=10+turn*2,finished_at=11+turn*2,output=output,receipt=receipt,
            completion_sha256=digest(canonical(completion)),actual_request=f'SYNTHETIC request {turn}'))
    with path.open('a') as stream:stream.write(''.join(json.dumps(r)+'\n' for r in records))
    rows=rt.session_center.discover(workspace,True)['discovery']['sessions']
    row=next(r for r in rows if rt.db.get('session-handle',r['handle'])['profile']['home']==str(home))
    return path,row


class SidebarIdentityTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self, admission=True);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt)
    def rows(self):return {r['handle']:r for r in self.manager.listing(self.w)['sessions']}
    def running_operation(self,label):
        chat=self.rt.flow.get('node-session',label+'-chat')
        self.rt.db.put('session-operation',label+'-operation',dict(id=label+'-operation',workspace=self.w,
            binding=chat['binding'],cwd=chat['cwd'],permission='read',state='RUNNING',created_at=time.time(),
            native_session=test_agent_sessions.SID,worker={}))
    def test_sidebar_titles_keep_same_uuid_homes_and_providers_separate(self):
        sessions=[]
        for provider in ('claude','codex'):
            for suffix in ('A','B'):
                label=provider+'-'+suffix;_,row=structured_session(self.base,self.rt,label,provider);sessions.append((row,label))
        rows=self.rows()
        for row,label in sessions:self.assertEqual(rows[row['handle']]['title'],'Synthetic '+label)
    def test_sidebar_state_and_node_belong_to_exact_native_home(self):
        _,a=structured_session(self.base,self.rt,'sidebar-A');_,b=structured_session(self.base,self.rt,'sidebar-B')
        self.rt.flow.change('node-session','sidebar-B-chat',dict(node='other/review'))
        for turn in (1,2):self.rt.flow.change('activation','sidebar-B-turn-'+str(turn),dict(node='other/review'))
        self.rt.flow.change('activation','sidebar-B-turn-2',dict(state='CANCELLED',started_at=999))
        rows=self.rows()
        self.assertEqual((rows[a['handle']]['display_state'],rows[a['handle']]['node']),('COMPLETED','loop/analyze'))
        self.assertEqual((rows[b['handle']]['display_state'],rows[b['handle']]['node']),('CANCELLED','other/review'))
    def test_legacy_derived_title_is_refreshed_without_changing_explicit_alias(self):
        _,a=structured_session(self.base,self.rt,'cache-A');_,b=structured_session(self.base,self.rt,'cache-B')
        self.rt.flow.put('session-title',a['handle'],dict(id=a['handle'],title='Synthetic cache-B'))
        self.rt.flow.put('session-meta',b['handle'],dict(id=b['handle'],workspace=self.w,collection='active',alias='My explicit alias'))
        rows=self.rows();self.assertEqual(rows[a['handle']]['title'],'Synthetic cache-A');self.assertEqual(rows[b['handle']]['title'],'My explicit alias')
    def test_active_operation_does_not_cross_provider_or_home(self):
        _,a=structured_session(self.base,self.rt,'operation-A','codex');_,b=structured_session(self.base,self.rt,'operation-B','claude')
        self.running_operation('operation-B')
        # Synthetic transport records only; no real model/process acceptance.
        with patch('studio.session_center.alive',return_value=True):rows=self.rows()
        self.assertEqual(rows[a['handle']]['display_state'],'COMPLETED');self.assertEqual(rows[b['handle']]['display_state'],'RUNNING')
    def test_current_operation_is_not_replaced_by_completed_workflow_history(self):
        _,row=structured_session(self.base,self.rt,'current-operation');self.running_operation('current-operation')
        with patch('studio.session_center.alive',return_value=True):rows=self.rows()
        self.assertEqual(rows[row['handle']]['display_state'],'RUNNING')


class StructuredHistoryTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self, admission=True);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt)
    def test_two_round_outputs_are_distinct_from_native_history_and_survive_reload(self):
        for provider in ('claude','codex'):
            with self.subTest(provider=provider):
                path,row=structured_session(self.base,self.rt,provider,provider)
                before=path.read_bytes()
                history=self.manager.history(self.w,row['handle'])
                results=history.get('execution_results',[])
                self.assertEqual([r['text'] for r in results],[f'SYNTHETIC {provider} final answer {t}' for t in (1,2)])
                self.assertEqual([r['iteration_path'] for r in results],[[1,1],[1,2]])
                self.assertTrue(all(r['source']=='structured_execution_result' and r['run']==provider+'-run' and r['node']=='loop/analyze' for r in results))
                self.assertEqual(history['messages'],self.manager.history(self.w,row['handle'])['messages'])
                self.assertEqual(results,WorkbenchSessions(self.rt).history(self.w,row['handle'])['execution_results'])
                self.assertFalse(any('final answer' in m['text'] for m in history['messages']))
                self.assertNotIn('HIDDEN_RESULT_REASONING',json.dumps(history))
                self.assertEqual(path.read_bytes(),before)
    def test_same_uuid_in_different_homes_and_reordered_switches_never_mix(self):
        _,a=structured_session(self.base,self.rt,'session-A');_,b=structured_session(self.base,self.rt,'session-B')
        for row,label in ((a,'session-A'),(b,'session-B'),(a,'session-A')):
            results=self.manager.history(self.w,row['handle']).get('execution_results',[])
            self.assertEqual([r['text'] for r in results],[f'SYNTHETIC {label} final answer {t}' for t in (1,2)])
    def test_binding_and_activation_identity_must_match_exactly(self):
        _,row=structured_session(self.base,self.rt)
        chat=self.rt.flow.get('node-session','synthetic-structured-chat')
        activation=self.rt.flow.get('activation','synthetic-structured-turn-1')
        changes=[('workspace','another-workspace'),('cwd',str(self.base.root)),('run','other-run'),('node','other/node')]
        for field,value in changes+ [('binding',dict(chat['binding'],**{k:v})) for k,v in (
            ('home',str(self.base.root/'other-home')),('provider','codex'),('session_id',test_agent_sessions.OTHER))]:
            with self.subTest(field=field,value=value):
                self.rt.flow.put('node-session',chat['id'],dict(chat,**{field:value}))
                self.assertEqual(self.manager.history(self.w,row['handle']).get('execution_results',[]),[])
        self.rt.flow.put('node-session',chat['id'],chat)
        for changes in ({'provider':'codex'},{'run':'other-run'},{'node':'other/node'}):
            with self.subTest(changes=changes):
                self.rt.flow.put('activation',activation['id'],dict(activation,**changes))
                self.assertEqual(len(self.manager.history(self.w,row['handle']).get('execution_results',[])),1)
        self.rt.flow.put('activation',activation['id'],activation)
        self.assertEqual(len(self.manager.history(self.w,row['handle']).get('execution_results',[])),2)
    def test_failed_cancelled_unconfirmed_and_bad_receipts_never_supply_answers(self):
        _,row=structured_session(self.base,self.rt)
        a=self.rt.flow.get('activation','synthetic-structured-turn-1')
        bad=[dict(a,state=s) for s in ('FAILED','CANCELLED','RUNNING','STARTING','RECOVERY_REQUIRED')]
        for change in ({'session_id':test_agent_sessions.OTHER},{'requested_model':'other-model'},
                       {'process':dict(a['receipt']['process'],exit=1)},
                       {'process':dict(a['receipt']['process'],cancelled=True)},
                       {'process':dict(a['receipt']['process'],timeout=True)},
                       {'process':dict(a['receipt']['process'],overflow=True)}):
            bad.append(dict(a,output=dict(a['output'],receipt=dict(a['receipt'],**change))))
        bad.extend([dict(a,output={'answer':'unconfirmed'}),dict(a,receipt=dict(a['receipt'],session_id=test_agent_sessions.OTHER))])
        for value in bad:
            with self.subTest(state=value['state'],output=value['output']):
                self.rt.flow.put('activation',a['id'],value)
                results=self.manager.history(self.w,row['handle']).get('execution_results',[])
                self.assertEqual([r['text'] for r in results],['SYNTHETIC synthetic-structured final answer 2'])
    def test_native_final_text_is_preserved_without_duplicate_result_body(self):
        _,row=structured_session(self.base,self.rt,'native','codex',native_answers=True)
        history=self.manager.history(self.w,row['handle'])
        self.assertEqual([m['text'] for m in history['messages'] if m['role']=='assistant'],[f'SYNTHETIC native final answer {t}' for t in (1,2)])
        self.assertEqual(history.get('execution_results'),[])
        # A query matching only a protocol/summary field must not show a different reply.
        self.assertEqual(self.manager.history(self.w,row['handle'],'Synthetic transport fixture')['messages'],[])
    def test_repeated_answers_remain_separate_and_runs_sort_by_execution_time(self):
        _,row=structured_session(self.base,self.rt)
        first=self.rt.flow.get('activation','synthetic-structured-turn-1')
        second=self.rt.flow.get('activation','synthetic-structured-turn-2')
        self.rt.flow.change('activation',second['id'],dict(output=dict(second['output'],answer=first['output']['answer'])))
        # INSERT OR REPLACE order is deliberately unrelated to execution order.
        self.rt.flow.put('activation',first['id'],first)
        history=self.manager.history(self.w,row['handle'])
        self.assertEqual([r['id'] for r in history['execution_results']],[first['id'],second['id']])
        self.assertEqual([r['text'] for r in history['execution_results']],[first['output']['answer']]*2)
        # A completed earlier round stays a real answer if a later node/run fails;
        # its displayed workflow status must not claim workflow success.
        self.rt.flow.change('run',first['run'],dict(state='FAILED'))
        self.assertTrue(all(r['run_state']=='FAILED' for r in self.manager.history(self.w,row['handle'])['execution_results']))
        chat=self.rt.flow.get('node-session','synthetic-structured-chat')
        run=self.rt.flow.get('run',first['run'])
        self.rt.flow.put('run','resumed-run',dict(run,id='resumed-run',state='COMPLETED'))
        self.rt.flow.put('node-session','resumed-chat',dict(chat,id='resumed-chat',run='resumed-run'))
        self.rt.flow.put('activation','earlier',dict(first,id='earlier',run='resumed-run',chat_key='resumed-chat',started_at=1))
        self.assertEqual([r['id'] for r in self.manager.history(self.w,row['handle'])['execution_results']],['earlier',first['id'],second['id']])
    def test_persisted_results_survive_runtime_restart_and_native_archive(self):
        path,row=structured_session(self.base,self.rt,'archive','codex')
        before=self.manager.history(self.w,row['handle'])['execution_results']
        restarted=WorkbenchRuntime(self.rt.operator_file,self.rt.operator_sha256,self.rt.candidate_sha256,self.rt.policy_file,self.rt.policy_sha)
        self.assertEqual(WorkbenchSessions(restarted).history(self.w,row['handle'])['execution_results'],before)
        home=Path(self.rt.flow.get('node-session','archive-chat')['binding']['home'])
        archived=home/'.codex/archived_sessions'/path.name;archived.parent.mkdir(parents=True)
        with patch.object(self.manager,'rpc',side_effect=lambda *args:path.rename(archived)):
            self.manager.change(self.w,row['handle'],'native_archive',answer='Synthetic owned archive only')
        self.assertEqual(self.manager.history(self.w,row['handle'])['execution_results'],before)
    def test_search_uses_only_redacted_visible_answer_and_groups_stay_advanced(self):
        _,row=structured_session(self.base,self.rt)
        secret='synthetic-secret-value-123456'
        # An explicit fixture credential, never a real model credential.
        home=Path(self.rt.flow.get('node-session','synthetic-structured-chat')['binding']['home'])
        save(home/'.claude/.credentials.json',{'token':secret})
        a=self.rt.flow.get('activation','synthetic-structured-turn-1')
        self.rt.flow.change('activation',a['id'],dict(output=dict(a['output'],answer='Visible credential '+secret+' api_key=sk-synthetic-longvalue'),actual_request='Request '+secret))
        history=self.manager.history(self.w,row['handle'],'Visible credential')
        self.assertEqual([r['text'] for r in history.get('execution_results',[])],['Visible credential [REDACTED] api_key=[REDACTED]'])
        self.assertNotIn(secret,json.dumps(history));self.assertNotIn('HIDDEN_RESULT_REASONING',json.dumps(history))
        self.assertEqual(self.manager.history(self.w,row['handle'],secret).get('execution_results'),[])
        self.assertEqual(self.manager.history(self.w,row['handle'],'Synthetic transport fixture').get('execution_results'),[])
        self.assertEqual(len(history['groups']),2);self.assertTrue(history['activity'])
    def test_decoding_native_reply_cannot_bypass_credential_redaction(self):
        path,row=structured_session(self.base,self.rt,'redact-native','codex',native_answers=True)
        home=Path(self.rt.flow.get('node-session','redact-native-chat')['binding']['home'])
        save(home/'.codex/auth.json',{'token':'synthetic-credential-123456'})
        # JSON escapes are decoded only for the visible reply, then redacted again.
        encoded='{"reply":"Visible \\u0073ynthetic-credential-123456"}'
        with path.open('a') as stream:stream.write(json.dumps(dict(type='response_item',payload=dict(type='message',role='assistant',phase='final_answer',content=[dict(type='output_text',text=encoded)])))+'\n')
        history=self.manager.history(self.w,row['handle'])
        self.assertNotIn('synthetic-credential-123456',json.dumps(history))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self, admission=True);self.w=self.base.project['id'];self.graph=examples(self.rt.operator['profiles'])[0]
    def request(self):return dict(workspace=self.w,graph=copy.deepcopy(self.graph),task='Synthetic transport boundary')
    def test_folder_is_browsable_outside_roots_but_execution_is_refused(self):
        result=self.rt.folder(str(self.base.source));self.assertEqual(result['path'],str(self.base.source))
        outside=self.rt.folder('/etc');self.assertTrue(outside['openable']);self.assertFalse(outside['execution_allowed'])
        with self.assertRaises(Blocked):self.rt.validate_source('/etc')
        link=self.base.source/'escape';link.symlink_to('/etc',target_is_directory=True)
        self.assertNotIn('escape',[x['name'] for x in self.rt.folder(str(self.base.source))['folders']])
    def test_ask_and_plan_cannot_select_write_permission(self):
        for mode in ('Ask','Plan'):
            n=copy.deepcopy(self.graph['nodes'][1]);n.update(mode=mode,permission='workspace-write')
            with self.assertRaises(Invalid):self.rt.node_binding(self.w,n)
    def test_drift_after_preview_refuses_launch(self):
        permit=self.rt.prepare_flow(self.request());(self.base.source/'late.txt').write_text('late')
        with patch.object(self.rt,'spawn') as spawn:
            with self.assertRaises(Conflict):self.rt.launch_flow(permit['id'],permit['sha256'],'approved test')
            spawn.assert_not_called()
    def test_duplicate_start_requires_same_authorization_and_never_spawns_twice(self):
        permit=self.rt.prepare_flow(self.request())
        with patch.object(self.rt,'spawn') as spawn:
            a=self.rt.launch_flow(permit['id'],permit['sha256'],'approved test');b=self.rt.launch_flow(permit['id'],permit['sha256'],'approved test')
            self.assertEqual(a['run']['id'],b['run']['id']);self.assertEqual(spawn.call_count,1)
            with self.assertRaises(Conflict):self.rt.launch_flow(permit['id'],'0'*64,'approved test')
    def test_dead_worker_is_unknown_never_running_and_missing_receipt_cannot_resume(self):
        p=self.rt.prepare_flow(self.request())
        with patch.object(self.rt,'spawn'):r=self.rt.launch_flow(p['id'],p['sha256'],'approved test')['run']['id']
        self.rt.flow.change('run',r,dict(state='RUNNING',created_at=time.time()-10))
        self.rt.flow.put('activation','lost',dict(id='lost',run=r,node='analyze',state='RUNNING'))
        self.assertEqual(self.rt.snapshot(r)['state'],'RECOVERY_REQUIRED')
        with self.assertRaises(Blocked):self.rt.control(r,'resume')
    def test_failed_first_session_name_never_null_or_protocol_scaffold(self):
        path,row=self.base.native_profile('codex',managed=True)
        # Native text scaffold is not an acceptable title; actual name remains separate.
        with path.open('a') as f:f.write(json.dumps(dict(type='response_item',payload=dict(type='message',role='user',content=[dict(type='input_text',text='## 本次运行初始化\nINIT\n\n## 每轮任务\nExplain the graph')])) )+'\n')
        rows=WorkbenchSessions(self.rt).listing(self.w)['sessions']
        self.assertTrue(all(isinstance(x['title'],str) and x['title'].strip() for x in rows))
    def test_two_native_uuids_in_one_managed_home_keep_exact_binding(self):
        path,row=self.base.native_profile('codex',managed=True)
        original=self.rt.db.get('chat-profile','test-profile');other=copy.deepcopy(original);other['binding']['session_id']='22222222-2222-4222-8222-222222222222'
        self.rt.db.put('chat-profile','second-profile',other)
        native=self.rt.db.get('session-handle',row['handle']);binding,reason=self.rt.session_center._binding(native,self.base.project)
        self.assertIsNotNone(binding,reason);self.assertEqual(binding['session_id'],row['session_id'])
    def test_native_flat_archive_history_and_metadata_restore_are_distinct(self):
        path,row=self.base.native_profile('codex',managed=True);manager=WorkbenchSessions(self.rt)
        home=Path(self.rt.db.get('chat-profile','test-profile')['binding']['home'])
        archived=home/'.codex/archived_sessions'/path.name;archived.parent.mkdir(parents=True)
        with patch.object(manager,'rpc',side_effect=lambda *args:path.rename(archived)):
            result=manager.change(self.w,row['handle'],'native_archive',answer='approved own synthetic session')
        self.assertTrue(result['native_modified'])
        history=manager.history(self.w,row['handle']);self.assertTrue(history['messages'])
        self.assertNotIn('HIDDEN_REASONING',json.dumps(history))
        with patch.object(manager,'rpc',side_effect=lambda *args:archived.rename(path)):
            self.assertTrue(manager.change(self.w,row['handle'],'restore')['native_modified'])
        manager.change(self.w,row['handle'],'trash',answer='approved own synthetic session')
        self.assertFalse(manager.change(self.w,row['handle'],'restore')['native_modified'])
    def test_attachments_cannot_grant_credential_or_outside_access(self):
        rows=self.rt.attachments(str(self.base.source),['/etc/passwd','../external','missing'])
        self.assertFalse(any(r['accessible'] for r in rows))
    def test_receiver_rechecks_attachment_hash_and_access(self):
        file=self.base.source/'artifact.txt';file.write_text('one')
        value=[{'content':{'artifacts':self.rt.attachments(str(self.base.source),['artifact.txt'])}}]
        self.assertTrue(self.rt.recipient_inputs(str(self.base.source),value)[0]['content']['artifacts'][0]['accessible'])
        file.write_text('two')
        self.assertFalse(self.rt.recipient_inputs(str(self.base.source),value)[0]['content']['artifacts'][0]['accessible'])
        self.assertFalse(self.rt.recipient_inputs(str(self.base.root),value)[0]['content']['artifacts'][0]['accessible'])
    def test_lost_native_mutation_receipt_prevents_blind_retry(self):
        _,row=self.base.native_profile('codex',managed=True);internal=self.rt.db.get('session-handle',row['handle'])
        binding,_=self.rt.session_center._binding(internal,self.base.project);manager=WorkbenchSessions(self.rt)
        with patch('studio.workbench_sessions.execute',return_value=({'exit':1,'timeout':True},b'',b'')) as execute:
            for _ in range(2):
                with self.assertRaises(Blocked):manager.rpc(binding,str(self.base.source),'thread/fork',{'threadId':row['session_id']})
            self.assertEqual(execute.call_count,1)
        self.assertEqual(self.rt.flow.list('native-mutation')[0]['state'],'RECOVERY_REQUIRED')
    def test_writer_lease_excludes_other_readers_but_two_readers_coexist(self):
        path=self.base.root/'scope.lock'
        with source_lease(path,False),source_lease(path,False):
            with self.assertRaises(Blocked):
                with source_lease(path,True):pass
        with source_lease(path,True):
            with self.assertRaises(Blocked):
                with source_lease(path,False):pass
    def test_delivery_profile_not_supplied_by_browser(self):
        g=self.request();g['graph']['nodes'][1].update(kind='delivery',profile='browser-command')
        with self.assertRaises(Invalid):self.rt.prepare_flow(g)
    def test_external_accept_with_failing_tests_or_wrong_identity_never_passes(self):
        executable=str(Path('/usr/bin/true').resolve())  # Receipt tests use a valid pinned non-interpreter fixture.
        sha=digest(Path(executable).read_bytes())
        self.rt.policy['external_steps']['fixed']=dict(kind='verify',argv=[executable],executable_sha256=sha,dependencies={executable:sha})
        self.rt.flow.put('run','r',dict(workspace=self.w,graph_sha256='g',authorization={},task='frozen task'))
        self.rt.flow.put('activation','a',dict(id='a',run='r'))
        base=dict(run='r',activation='a',candidate_sha256=workspace_hash(self.base.source),graph_sha256='g',
                  authorization_sha256=digest(canonical({})),acceptance_sha256=digest(b'frozen task'),
                  tests=2,failures=1,errors=0,skipped=0,verdict='ACCEPT',verified=True)
        for changed in ({},{'failures':0,'candidate_sha256':'0'*64},{'failures':0,'tests':0},{'failures':0,'skipped':1}):
            receipt=dict(base,**changed)
            with patch('studio.workbench_runtime.execute',return_value=({'exit':0,'timeout':False,'cancelled':False,'overflow':False},canonical(receipt),b'')):
                with self.assertRaises(Blocked):self.rt.external_step('r','a',{'kind':'verify','profile':'fixed'},[])
        with patch('studio.workbench_runtime.execute',return_value=({'exit':0,'timeout':False,'cancelled':False,'overflow':False},canonical(dict(base,failures=0)),b'')):
            self.assertTrue(self.rt.external_step('r','a',{'kind':'verify','profile':'fixed'},[])['result']['verified'])
    def test_external_delivery_needs_remote_readback_even_with_accept(self):
        executable=str(Path('/usr/bin/true').resolve())  # Receipt tests use a valid pinned non-interpreter fixture.
        sha=digest(Path(executable).read_bytes())
        self.rt.policy['external_steps']['fixed']=dict(kind='delivery',argv=[executable],executable_sha256=sha,dependencies={executable:sha})
        self.rt.flow.put('run','r',dict(workspace=self.w,graph_sha256='g',authorization={},task='frozen task'))
        self.rt.flow.put('activation','a',dict(id='a',run='r'))
        receipt=dict(run='r',activation='a',candidate_sha256=workspace_hash(self.base.source),graph_sha256='g',
                     authorization_sha256=digest(canonical({})),acceptance_sha256=digest(b'frozen task'),
                     verdict='ACCEPT',remote_verified=False,delivery_state='COMPLETED')
        with patch('studio.workbench_runtime.execute',return_value=({'exit':0,'timeout':False,'cancelled':False,'overflow':False},canonical(receipt),b'')):
            with self.assertRaises(Blocked):self.rt.external_step('r','a',{'kind':'delivery','profile':'fixed'},[])
    def test_external_interpreter_pin_does_not_cover_mutable_script(self):
        exe=str(Path(sys.executable).resolve());sha=digest(Path(exe).read_bytes());script=self.base.root/'bridge.py';script.write_text('print(1)')
        profile=dict(kind='verify',argv=[exe,'-I',str(script)],executable_sha256=sha,dependencies={exe:sha})
        with self.assertRaises(Blocked):validate_profile(profile)
        profile['dependencies'][str(script)]=digest(script.read_bytes());validate_profile(profile)
        script.write_text('print(2)')
        with self.assertRaises(Blocked):validate_profile(profile)
    def test_external_receipt_requires_contract_counters_and_real_remote_confirmation(self):
        p=dict(run='r',activation='a',candidate_sha256='c',graph_sha256='g',authorization_sha256='h',acceptance_sha256=digest(b'frozen task'))
        receipt=dict(p,tests=1,failures=0,errors=0,skipped=0,verified=True)
        validate_receipt(receipt,p,'verify')
        for change in ({'tests':True},{'tests':-1},{'skipped':1},{'graph_sha256':'stale'},{'authorization_sha256':'stale'},{'verified':False}):
            with self.assertRaises(Blocked):validate_receipt(dict(receipt,**change),p,'verify')
        delivery=dict(p,verdict='ACCEPT',remote_verified=True,delivery_state='COMPLETED');validate_receipt(delivery,p,'delivery')
        for change in ({'remote_verified':'true'},{'delivery_state':'PARTIAL_DELIVERY'},{'verdict':'ACCEPT_WITH_LIMITATIONS'}):
            with self.assertRaises(Blocked):validate_receipt(dict(delivery,**change),p,'delivery')

    def test_external_receipt_rejects_another_or_missing_task_contract(self):
        payload=dict(run='r',activation='a',candidate_sha256='c',graph_sha256='g',authorization_sha256='h',
                     acceptance_sha256=digest('冻结任务\r\n保留尾部空格  \n'.encode('utf-8')))
        for kind,extra in (('verify',dict(tests=1,failures=0,errors=0,skipped=0,verified=True)),
                           ('delivery',dict(verdict='ACCEPT',remote_verified=True,delivery_state='COMPLETED'))):
            receipt=dict(payload,**extra);validate_receipt(receipt,payload,kind)
            for changed in (digest(b'another task'),None):
                bad=dict(receipt,acceptance_sha256=changed)
                if changed is None:bad.pop('acceptance_sha256')
                with self.subTest(kind=kind,acceptance=changed):
                    with self.assertRaises(Blocked):validate_receipt(bad,payload,kind)

    def test_external_payload_hashes_frozen_task_utf8_bytes(self):
        executable=str(Path('/usr/bin/true').resolve());sha=digest(Path(executable).read_bytes())
        self.rt.policy['external_steps']['fixed']=dict(kind='verify',argv=[executable],executable_sha256=sha,dependencies={executable:sha})
        request=self.request();request['task']='  冻结任务\r\n精确 UTF-8 字节  \n'
        permit=self.rt.prepare_flow(request)
        with patch.object(self.rt,'spawn'):
            run=self.rt.launch_flow(permit['id'],permit['sha256'],'approved test')['run']['id']
        self.rt.flow.put('activation','a',dict(id='a',run=run))
        request['task']='edited draft must not replace frozen task'
        expected=digest('  冻结任务\r\n精确 UTF-8 字节  \n'.encode('utf-8'))
        observed=[];receipt_acceptance=expected
        def execute(*args,**kwargs):
            payload=json.loads(kwargs['input_text']);observed.append(payload)
            receipt={k:payload[k] for k in ('run','activation','candidate_sha256','graph_sha256','authorization_sha256')}
            receipt.update(acceptance_sha256=receipt_acceptance,
                           tests=1,failures=0,errors=0,skipped=0,verified=True)
            if receipt_acceptance is None:receipt.pop('acceptance_sha256')
            return dict(exit=0,timeout=False,overflow=False,cancelled=False),canonical(receipt),b''
        with patch('studio.workbench_runtime.execute',side_effect=execute):
            result=self.rt.external_step(run,'a',{'kind':'verify','profile':'fixed'},[])
        self.assertEqual(observed[0].get('acceptance_sha256'),expected)
        self.assertEqual(result['result']['acceptance_sha256'],expected)
        for receipt_acceptance in (digest(b'another task'),None):
            with self.subTest(receipt_acceptance=receipt_acceptance):
                with patch('studio.workbench_runtime.execute',side_effect=execute):
                    with self.assertRaises(Blocked):self.rt.external_step(run,'a',{'kind':'verify','profile':'fixed'},[])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self, admission=True);self.server,self.token=create_server(self.rt);t=threading.Thread(target=self.server.serve_forever,daemon=True);t.start()
        self.addCleanup(t.join);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def get(self,path,auth=True,extra=None):
        headers={'Authorization':'Bearer '+self.token} if auth else {};headers.update(extra or {})
        return urlopen(Request(self.url+path,headers=headers))
    def test_auth_origin_cookie_and_static_boundary(self):
        with self.assertRaises(HTTPError) as e:self.get('/api/status',False)
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(HTTPError):self.get('/api/status',extra={'Origin':'http://127.0.0.1:1'})
        with self.assertRaises(HTTPError):self.get('/api/status',False,{'Cookie':'devflower_workbench='+self.token})
        with self.get('/') as r:self.assertIn('Max-Age=0',r.headers['Set-Cookie'])
        with self.get('/api/status') as r:self.assertFalse(json.load(r)['stable_approved'])
    def test_no_shell_or_stable_approval_endpoints(self):
        for path in ('/api/shell','/api/approve-stable','/api/authorize-run'):
            with self.assertRaises(HTTPError) as e:self.get(path)
            self.assertEqual(e.exception.code,400)
    def test_structured_results_reload_search_and_session_switch_over_real_http(self):
        _,a=structured_session(self.base,self.rt,'http-A');_,b=structured_session(self.base,self.rt,'http-B')
        for row,label in ((a,'http-A'),(a,'http-A'),(b,'http-B'),(a,'http-A')):
            with self.get('/api/history?'+urlencode(dict(workspace=self.base.project['id'],handle=row['handle']))) as response:history=json.load(response)
            self.assertEqual([r['text'] for r in history.get('execution_results',[])],[f'SYNTHETIC {label} final answer {t}' for t in (1,2)])
            self.assertEqual(len({r['id'] for r in history['execution_results']}),2)
        with self.get('/api/history?'+urlencode(dict(workspace=self.base.project['id'],handle=a['handle'],query='final answer 2'))) as response:history=json.load(response)
        self.assertEqual([r['text'] for r in history['execution_results']],['SYNTHETIC http-A final answer 2'])

if __name__=='__main__':unittest.main()
