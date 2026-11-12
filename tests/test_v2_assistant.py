"""Workspace and intent checks around the real V2 HTTP assistant API."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, Lock, Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid

import test_v2_execution as fixtures
import test_v2_execution_http as http
from v2.assistant import Assistant
from v2.assistant_proposals import PROPOSAL_INSTRUCTIONS
from v2.server import create_server


class FixtureOperations:
    """Synthetic transport, with the same public result and readback contract."""
    codex_terminal = False

    def __init__(self):
        self.calls = []
        self.replies = {}
        self.state = 'COMPLETED'
        self.lock = Lock()

    def status(self):
        return ['claude', 'codex']

    def operation(self, workspace, intent_key):
        with self.lock:
            return self.replies.get((workspace,intent_key), {'state':'UNREGISTERED'})

    def send(self, data):
        assert data['handle'] is None and data['revision'] is None
        with self.lock:
            key = data['workspace'],data['intent_key']
            assert key not in self.replies
            self.calls.append(data)
            reply = ('{"summary":"Rename approved draft label","operations":'
                     '[{"op":"update-node","id":"AgentA","name":"Reviewed Agent"}]}'
                     if PROPOSAL_INSTRUCTIONS in data['prompt'] else 'Verified fixture chat response')
            self.replies[key] = dict(state=self.state, reply=reply, reason=None,
                                     receipt=dict(provider=data['provider'],session_id=str(uuid.uuid4())))
            return self.replies[key]


class AssistantTests(unittest.TestCase):
    setUp = fixtures.ExecutionTests.setUp
    request = http.ExecutionHTTPTests.request

    def initialize(self):
        self.operations = FixtureOperations()
        server, self.token = create_server(self.store, runtime=self.rt, session_operations=self.operations)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}/api/v2/'
        self.chat = '7'*32
        self.payload = dict(workspace=self.workspace,conversation=self.chat,provider='claude',model=None,effort=None,
                            mode='chat',text='fixture question',draft=None,files=[],run=None)
        self.work = self.root / 'spaces' / 'A'

    def post(self, route, payload):
        return self.request('assistant/'+route, payload)

    def details(self, **extra):
        return self.request('assistant/conversation?'+urlencode(
            dict(workspace=self.workspace,conversation=self.chat)|extra))

    def confirm(self, payload=None, intent='1'*32):
        value = payload or self.payload
        code, preview = self.post('preview',value)
        self.assertEqual(code,200,preview)
        return dict(value,intent=intent,preview_sha256=preview['sha256']),preview

    def test_context_preview_is_read_only_and_exact_and_duplicate_send_is_one(self):
        self.initialize()
        (self.work/'public.txt').write_text('PUBLIC_ASSISTANT_SCOPE')
        (self.work/'other.txt').write_text('DO_NOT_ATTACH_UNSELECTED')
        self.payload['files']=['public.txt']
        submitted, preview = self.confirm()
        self.assertIn('PUBLIC_ASSISTANT_SCOPE',preview['prompt'])
        self.assertNotIn('DO_NOT_ATTACH_UNSELECTED',preview['prompt'])
        self.assertEqual(preview['context']['workspace']['name'],'A')
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(self.store.db.list('assistant-turn'),[])
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.post('send',submitted), range(2)))
        self.assertEqual([code for code,_ in replies],[200,200],replies)
        self.assertEqual(len(self.operations.calls),1)
        self.assertEqual(self.operations.calls[0]['prompt'],preview['prompt'])
        _, history = self.details()
        self.assertEqual(len(history['turns']),1)
        self.assertEqual(history['turns'][0]['receipt']['provider'],'claude')
        self.assertEqual(self.post('send',dict(submitted,text='changed exact nonce'))[0],409)
        other = self.root/'spaces'/'B'
        other.mkdir()
        foreign = self.store.open_workspace(str(other))['id']
        self.assertEqual(self.details(workspace=foreign)[0],409)
        _, list_b = self.request('assistant/history?'+urlencode({'workspace':foreign}))
        self.assertEqual(list_b['conversations'],[])
        second, next_preview = self.confirm(dict(self.payload,text='follow up'),intent='2'*32)
        self.assertEqual(next_preview['context']['previous_turns'],1)
        self.assertIn('Verified fixture chat response',next_preview['prompt'])
        self.assertEqual(self.post('send',second)[0],200)
        self.assertTrue(all(row['handle'] is None for row in self.operations.calls))

    def test_duplicate_send_replays_when_the_first_request_updates_the_preview_context(self):
        self.initialize()
        submitted, _ = self.confirm()
        reached_prepare, release_prepare = Event(), Event()
        original_prepare = Assistant.prepare

        def hold_first_prepare(assistant, selection):
            if not reached_prepare.is_set():
                reached_prepare.set()
                if not release_prepare.wait(5):
                    raise AssertionError('Test never released duplicate prepare')
            return original_prepare(assistant, selection)

        with patch.object(Assistant, 'prepare', hold_first_prepare), ThreadPoolExecutor(max_workers=1) as pool:
            delayed = pool.submit(self.post, 'send', submitted)
            try:
                self.assertTrue(reached_prepare.wait(5))
                winner = self.post('send', submitted)
            finally:
                release_prepare.set()
            replay = delayed.result(timeout=5)
        self.assertEqual(winner[0], 200, winner)
        self.assertEqual(replay[0], 200, replay)
        self.assertEqual(replay[1]['id'], winner[1]['id'])
        self.assertEqual(len(self.operations.calls), 1)
        self.assertEqual(len(self.store.db.list('assistant-turn')), 1)

    def test_changed_file_and_unauthorized_paths_refuse_before_native_operation(self):
        self.initialize()
        (self.work/'public.txt').write_text('first')
        outside = self.root/'outside.txt'
        outside.write_text('outside')
        (self.work/'link.txt').symlink_to(outside)
        data = dict(self.payload,files=['public.txt'])
        submitted,_ = self.confirm(data)
        (self.work/'public.txt').write_text('new')
        self.assertEqual(self.post('send',submitted)[0],409)
        for path in ('link.txt','../outside.txt',str(outside)):
            self.assertEqual(self.post('preview',dict(data,files=[path]))[0],400)
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(self.store.db.list('assistant-turn'),[])

    def test_exact_unsaved_base_apply_is_validated_without_saving_or_running(self):
        self.initialize()
        selection = dict(id=self.draft['id'],revision=self.draft['revision'],document=self.doc)
        data = dict(self.payload,mode='proposal',text='Rename the Agent on the canvas.',draft=selection)
        submitted,_ = self.confirm(data)
        code, result = self.post('send',submitted)
        self.assertEqual(code,200,result)
        self.assertIn('proposal',result,result)
        self.assertEqual(result['proposal']['after']['nodes'][0]['name'],'Reviewed Agent')
        apply = dict(workspace=self.workspace,conversation=self.chat,turn=result['id'],draft=selection)
        changed = dict(self.doc,name='unsaved local change')
        self.assertEqual(self.post('apply',dict(apply,draft=dict(selection,document=changed)))[0],409)
        code, answer = self.post('apply',apply)
        self.assertEqual(code,200,answer)
        self.assertEqual(answer['document']['nodes'][0]['name'],'Reviewed Agent')
        self.assertEqual(self.store.draft(self.workspace,self.draft['id'])['graph'],self.doc)
        self.assertEqual(self.store.db.list('run'),[])
        self.store.save(self.workspace,self.draft['id'],self.draft['revision'],changed)
        self.assertEqual(self.post('apply',apply)[0],409)

    def test_unknown_readback_never_replays_or_accepts_next_turn(self):
        self.initialize()
        self.operations.state='RECOVERY_REQUIRED'
        submitted,_ = self.confirm()
        self.assertEqual(self.post('send',submitted)[1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.details()[1]['turns'][-1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.post('send',submitted)[1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.post('preview',dict(self.payload,text='second'))[0],409)
        self.assertEqual(len(self.operations.calls),1)

    def test_workspace_preferences_probe_and_pinned_agent_are_independent_of_session_writes(self):
        from v2.agent_records import KIND
        from v2.assistant import PROBE
        from v2.assistant_operations import AssistantOperations

        # Configure through real HTTP against a synthetic already-discovered CLI binding.
        server, self.token = create_server(self.store, runtime=self.rt)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}/api/v2/'
        self.chat = '7'*32
        identity = {'id': 'a'*32, 'revision': 'b'*64}
        role = dict(name='Assistant persona', role='Reader', responsibilities='Explain source',
                    background='role-only marker', goal='Answer', initial_prompt='Use context',
                    round_prompt='Show evidence', output_prompt='Cite lines', provider='claude',
                    model='sonnet', effort='low', permission='workspace-write', skills=['do-not-attach'])
        self.store.db.put(KIND, identity['id'], dict(identity, workspace=self.workspace, config=role))
        selection = dict(provider='claude', native_profile=None, model='sonnet', effort='low', identity=identity)
        data = dict(selection, workspace=self.workspace, conversation=self.chat, text='Read selected context',
                    mode='chat', draft=None, files=[], run=None)
        binding = dict(project=self.workspace, provider='claude', chat_only=True)
        status = dict(state='COMPLETED', reply='fixture result', receipt={'provider':'claude','resolved_model':'fixture'})
        def fake_config(workspace, selected):
            self.assertEqual(workspace, self.workspace)
            return binding, dict(selected, binding_sha256='1'*64, permission='none')
        with patch.object(AssistantOperations, 'config', side_effect=fake_config), \
                patch.object(AssistantOperations, 'operation', return_value=status), \
                patch.object(AssistantOperations, 'send_bound', return_value=status) as send:
            code, settings = self.post('configure', dict(selection, workspace=self.workspace))
            self.assertEqual((code,settings['legacy_providers'],settings['preferences']), (200, [], selection))
            self.assertEqual(self.request('assistant/settings?workspace='+self.workspace)[1]['preferences'],selection)
            submitted, preview = self.confirm(data)
            self.assertIn('role-only marker', preview['prompt'])
            self.assertNotIn('workspace-write', preview['prompt'])
            self.assertNotIn('do-not-attach', preview['prompt'])
            self.assertEqual(self.post('send',submitted)[1]['receipt']['resolved_model'],'fixture')
            self.assertEqual(self.post('send',submitted)[0],200)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0], binding)
            self.assertEqual(send.call_args.args[2], preview['prompt'])
            probe, probe_preview = self.confirm(dict(data, text=PROBE, mode='probe', identity=None),intent='2'*32)
            self.assertEqual(probe_preview['prompt'], PROBE)
            self.assertEqual(self.post('send',probe)[0],200)
            self.assertEqual(self.request('assistant/settings?workspace='+self.workspace)[1]['checks'][0]['state'],'COMPLETED')
            self.assertEqual(self.post('preview',dict(data, native_profile='/tmp/unapproved'))[0],400)
            self.store.db.put(KIND, identity['id'], dict(id=identity['id'],revision='c'*64,
                                                       workspace=self.workspace,config=role))
            self.assertEqual(self.post('preview',data)[0],409)

    def team_fixture(self, *, plan_kind='development', attached=None):
        from types import SimpleNamespace
        from v2.assistant_operations import AssistantOperations

        self.initialize()
        registry = self.rt.cli_providers
        adapter = SimpleNamespace(binding=lambda workspace, agent: dict(agent))
        self.addCleanup(patch.stopall)
        patch.object(registry, 'adapter', return_value=adapter).start()
        patch.object(registry, 'catalog_row', return_value={'permissions':['none','workspace-read','workspace-write']}).start()
        binding = {'provider':'codex', 'project':self.workspace, 'no_tools':True}
        def config(workspace, selected):
            self.assertEqual(workspace,self.workspace)
            return binding, dict(selected, binding_sha256='d'*64, requested_model='fixture', requested_effort='low',
                                 source='cli_catalog', permission='none')
        patch.object(AssistantOperations, 'config', side_effect=config).start()
        plan = dict(version=1,kind=plan_kind,summary='Build and review with fixed handoffs',name='Fixture team',rounds=3,
                    agents=[dict(slot=role,name=role,role=role,responsibilities=['First duty','Second duty'],
                                 background='Fixture source',goal='Meet the visible task',initial_prompt='Read the requirements',
                                 round_prompt='Use current round evidence',handoff_prompt='Return the evidence and next steps',
                                 permission='workspace-write' if role=='Developer' else 'workspace-read',skills=['Advisory only'])
                            for role in (('Developer','Tester','Reviewer') if plan_kind=='development' else ('Reviewer',))])
        if plan_kind=='update':
            plan.update(operations=[{'op':'update-node','id':'AgentA','name':'Review after revision'}],
                        bindings={'AgentA':'Reviewer'})
        status = {'state':'COMPLETED','reply':json.dumps(plan),'reason':None,'receipt':{'provider':'codex'}}
        patch.object(AssistantOperations, 'operation', return_value=status).start()
        self.team_send = patch.object(AssistantOperations, 'send_bound', return_value=status).start()
        draft = dict(id=self.draft['id'],revision=self.draft['revision'],document=self.doc) if plan_kind=='update' else None
        data = dict(self.payload,mode='team',provider='codex',native_profile=None,identity=None,model='fixture',effort='low',
                    draft=draft,files=attached or [])
        submitted,_ = self.confirm(data)
        code, turn = self.post('send',submitted)
        self.assertEqual((code,turn.get('team',{}).get('kind')),(200,plan_kind),turn)
        return dict(workspace=self.workspace,conversation=self.chat,turn=turn['id'],draft=draft,permissions=None,verification=None)

    def test_team_proposal_atomic_save_explicit_permissions_and_idempotence(self):
        from studio.server import Conflict
        from v2.designer_graph import document_nodes

        request = self.team_fixture()
        code, preview = self.post('team_preview',request)
        self.assertEqual(code,200,preview)
        self.assertEqual(preview['permissions'],{'Developer':'none','Tester':'none','Reviewer':'none'})
        self.assertEqual(preview['budget']['calls'],9)
        self.assertEqual(self.store.db.list('agent-config'),[])
        self.assertEqual(len(self.store.drafts(self.workspace)),1)
        approved = dict(request,permissions=preview['permissions'],preview_sha256=preview['sha256'],
                        authorization='confirm-team-configuration')
        self.assertEqual(self.post('team_confirm',dict(approved,authorization='automatic'))[0],400)
        self.assertEqual(self.post('team_confirm',dict(approved,permissions=dict(preview['permissions'],Developer='workspace-write')))[0],409)
        with patch.object(self.store.db,'save_draft_in',side_effect=Conflict('forced commit conflict')):
            self.assertEqual(self.post('team_confirm',approved)[0],409)
        self.assertEqual(self.store.db.list('agent-config'),[])
        self.assertEqual(len(self.store.drafts(self.workspace)),1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.post('team_confirm',approved), range(2)))
        self.assertEqual([code for code,_ in results],[200,200],results)
        self.assertEqual(results[0],results[1])
        answer = results[0][1]
        saved = {row['id']:row for row in self.store.db.list('agent-config')}
        self.assertEqual(len(saved),3)
        for row in saved.values():
            self.assertEqual(row['config']['permission'],'none')
            self.assertEqual(row['config']['skills'],[])
            self.assertIn('- First duty',row['config']['responsibilities'])
        for _, node in document_nodes(answer['draft']['graph']):
            if node['kind']=='agent':
                self.assertEqual(node['agent_revision'],saved[node['agent_id']]['revision'])
        self.assertEqual(self.post('team_confirm',dict(approved,preview_sha256='0'*64))[0],409)
        self.assertEqual(self.store.db.list('run'),[])
        self.team_send.assert_called_once()
        self.assertEqual(self.details()[1]['turns'][-1]['team']['saved_draft']['id'],answer['draft']['id'])

    def test_team_update_rechecks_base_and_preserves_draft_history(self):
        request = self.team_fixture(plan_kind='update')
        code, preview = self.post('team_preview',request)
        self.assertEqual(code,200,preview)
        self.assertEqual(preview['before'],self.doc)
        approved = dict(request,permissions=preview['permissions'],preview_sha256=preview['sha256'],
                        authorization='confirm-team-configuration')
        self.assertEqual(self.post('team_confirm',dict(approved,draft=dict(request['draft'],document=dict(
            self.doc,name='changed draft'))))[0],409)
        code, answer = self.post('team_confirm',approved)
        self.assertEqual(code,200,answer)
        self.assertTrue(answer['updated'])
        self.assertEqual(answer['draft']['id'],self.draft['id'])
        self.assertEqual(answer['draft']['graph']['nodes'][0]['name'],'Review after revision')
        self.assertEqual(self.store.draft(self.workspace,self.draft['id']),answer['draft'])
        with self.store.db.tx(read=True) as db:
            prior = db.execute('SELECT data FROM drafts WHERE id=? AND revision=?',
                               (self.draft['id'],self.draft['revision'])).fetchone()
        self.assertEqual(json.loads(prior[0])['graph'],self.doc)
        self.assertEqual(self.post('team_confirm',approved),(200,answer))

    def test_protected_team_binds_explicit_python_verification_and_refuses_changed_file(self):
        from test_v2_git_platform import initialize_repo
        from v2.development_graph import layout

        root = self.root/'spaces'/'A'
        initialize_repo(root,'https://github.com/example/fixture.git')
        check = root/'check.py'
        check.write_text("print('[4,10]')\n")
        request = self.team_fixture(attached=['check.py'])
        permissions = {'Developer':'workspace-write','Tester':'none','Reviewer':'workspace-read'}
        selected = dict(request,permissions=permissions,verification={'path':'check.py','expected_json':'[4,10]'})
        self.assertEqual(self.post('team_preview',dict(selected,verification={
            'path':'unselected.py','expected_json':'[]'}))[0],400)
        code, preview = self.post('team_preview',selected)
        self.assertEqual(code,200,preview)
        self.assertEqual(layout(preview['after'])['verification_node'],'Verify')
        self.assertEqual(preview['verification']['expected'],[4,10])
        approved = dict(selected,preview_sha256=preview['sha256'],authorization='confirm-team-configuration')
        check.write_text("print('[4,11]')\n")
        self.assertEqual(self.post('team_confirm',approved)[0],409)
        self.assertEqual(self.store.db.list('agent-config'),[])
        check.write_text("print('[4,10]')\n")
        code, saved = self.post('team_confirm',approved)
        self.assertEqual(code,200,saved)
        by_name = {row['config']['name']:row['config'] for row in saved['agents']}
        self.assertEqual((by_name['Developer']['mode'],by_name['Reviewer']['permission']),('Agent','workspace-read'))
        review = by_name['Reviewer']['output_prompt']
        self.assertIn('Verify 冻结验证回执的 result.candidate_sha256 原样复制',review)
        self.assertIn('单个文件的 SHA-256',review)
        self.assertIn('passed=false',review)
        self.assertNotIn('Verify 冻结验证回执',by_name['Developer']['output_prompt'])
        self.assertIn('再次询问同一项授权',self.team_send.call_args.args[2])
        self.assertEqual(self.store.db.list('run'),[])

    def test_diagnosis_uses_existing_failed_run_and_events_without_sending(self):
        self.initialize()
        with patch.object(self.rt,'spawn'):
            result = self.rt.launch(self.workspace,self.draft['id'],self.draft['revision'],'assistant-run-fixture')
        run = result['run']['id']
        activation = '6'*64
        self.store.db.put('activation',activation,dict(id=activation,run=run,kind='agent',node='AgentA',
                          name='Fixture',state='STARTING',iteration_path=[],inputs=[dict(edge='first',
                          source='upstream',activation='4'*64,content_sha256='3'*64)]))
        self.store.db.change('activation',activation,{'state':'FAILED','reason':'fixture upstream missing'},
                             event={'kind':'node-failed'})
        self.store.db.change('run',run,{'state':'FAILED'})
        query = urlencode({'workspace':self.workspace,'run':run})
        code, diagnosis = self.request('assistant/diagnosis?'+query)
        self.assertEqual(code,200,diagnosis)
        self.assertEqual(diagnosis['nodes'][0]['reason'],'fixture upstream missing')
        self.assertEqual(diagnosis['nodes'][0]['upstream'][0]['source'],'upstream')
        self.assertEqual(diagnosis['recent_error_events'][-1]['activation'],activation)
        _, preview = self.confirm(dict(self.payload,run=run))
        self.assertIn('fixture upstream missing',preview['prompt'])
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(len(self.store.db.list('run')),1)

    def test_diagnosis_keeps_completed_red_round_after_later_acceptance(self):
        from test_v2_development_runtime import DevelopmentRuntimeTests, development_document
        from v2.designer_scheduler import activation_key

        self.initialize()
        DevelopmentRuntimeTests.initial(self)
        draft = self.store.save(self.workspace,None,None,development_document(self.saved,rounds=3))
        for agent in self.saved:
            self.store.db.put('agent-config',agent['id'],agent)
        with patch.object(self.rt,'spawn'), patch('v2.runtime.freeze_agent',
                 side_effect=lambda rt,workspace,node: DevelopmentRuntimeTests.bind(self,node)):
            run = self.rt.launch(self.workspace,draft['id'],draft['revision'],'assistant-development-fixture')['run']['id']
        outputs = [('RepairLoop/Verify',[1],{'passed':False},'development-verified','actual red test'),
                   ('RepairLoop/Gate',[1],{'gate_verdict':'REJECT'},'delivery-gate-decided','repair first round'),
                   ('RepairLoop/Gate',[2],{'gate_verdict':'ACCEPT_WITH_LIMITATIONS'},'delivery-gate-decided','unresolved review'),
                   ('RepairLoop/Verify',[3],{'passed':True},'development-verified','actual green test'),
                   ('RepairLoop/Gate',[3],{'gate_verdict':'ACCEPT'},'delivery-gate-decided','approved repair')]
        source = activation_key(run,'RepairLoop/Reviewer',[1])
        for node,path,facts,kind,feedback in outputs:
            ident = activation_key(run,node,path)
            upstream = node.replace('/Gate','/Reviewer').replace('/Verify','/Tester')
            self.store.db.put('activation',ident,dict(id=ident,run=run,kind='tool',node=node,name=node,state='STARTING',
                              iteration_path=path,inputs=[dict(edge='fixture-handoff',source=upstream,
                              activation=activation_key(run,upstream,path),target_activation=ident,
                              handoff_id='f'*64,source_round=path,target_round=path,content='large input '*4000)]))
            self.store.db.change('activation',ident,dict(facts,state='COMPLETED',
                output={'answer':'large private output '*2000,'result':dict(facts,feedback=feedback+' '+str(self.store.state))}),
                event=dict(kind=kind,**facts))
        finish = activation_key(run,'Finish',[])
        output = {'answer':'accepted','result':{'gate_verdict':'ACCEPT'}}
        self.store.db.put('activation',finish,dict(id=finish,run=run,kind='result',node='Finish',name='Finish',
                          state='COMPLETED',iteration_path=[],output=output))
        self.store.db.change('run',run,dict(state='COMPLETED',output=output))
        for _ in range(104):
            self.store.db.event(run,'later-status',state='COMPLETED')

        code, diagnosis = self.request('assistant/diagnosis?'+urlencode(dict(workspace=self.workspace,run=run)))
        self.assertEqual((code,diagnosis['state']),(200,'COMPLETED'))
        problems = {(row['node'],tuple(row['iteration_path'])):row for row in diagnosis['nodes']}
        self.assertEqual({key:row['diagnostic_outcome'] for key,row in problems.items()},
                         {('RepairLoop/Verify',(1,)):'VERIFICATION_FAILED',('RepairLoop/Gate',(1,)):'REJECT',
                          ('RepairLoop/Gate',(2,)):'ACCEPT_WITH_LIMITATIONS'})
        first = problems['RepairLoop/Gate',(1,)]
        self.assertEqual(first['state'],'COMPLETED')
        self.assertEqual(first['reason'],'repair first round [已隐藏]')
        self.assertEqual(first['upstream'][0]['activation'],source)
        self.assertEqual(first['upstream'][0]['handoff_id'],'f'*64)
        self.assertEqual(first['upstream'][0]['target_round'],[1])
        self.assertEqual(first['upstream'][0]['target_activation'],first['activation'])
        self.assertTrue(first['inputs_paged'])
        self.assertTrue(first['evidence']['inputs']['truncated'])
        self.assertLessEqual(len(first['evidence']['inputs']['text'].encode()),800)
        self.assertIn('large input',first['evidence']['inputs']['text'])
        self.assertNotIn('large input '*100,json.dumps(diagnosis))
        event = next(event for event in diagnosis['recent_error_events'] if event['seq']==first['event_seq'])
        self.assertEqual((event['scope'],event['activation'],event['iteration_path'],event['gate_verdict']),
                         ('activation',first['activation'],[1],'REJECT'))
        self.assertEqual(len(diagnosis['recent_error_events']),3)
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(len(self.store.db.list('run')),1)
