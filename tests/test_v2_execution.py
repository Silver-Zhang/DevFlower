"""V2 frozen execution/ownership negative fixtures; never a real Provider."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import Blocked, canonical, digest
from studio.server import Conflict
from studio.workbench_scheduler import Scheduler
from v2.store import DraftStore
from v2.definitions import compile_definition
from v2.runtime import V2Runtime, verify_completion, terminal_correlated
from v2.provider import CodexProvider


class SyntheticProvider:
    def __init__(self, store, executable=None): self.store = store
    def binding(self, workspace, node):
        return dict(provider='codex', executable='/bin/true', model=node['model'], effort=node['effort'],
                    _executable_sha256='a' * 64, home='/synthetic', config_root='/synthetic/.codex')


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        spaces = self.root / 'spaces'; spaces.mkdir(); (spaces / 'A').mkdir()
        self.store = DraftStore(self.root / 'state', [spaces]); self.workspace = self.store.open_workspace(str(spaces / 'A'))['id']
        self.doc = dict(version=1, name='synthetic execution', nodes=[dict(id='AgentA', name='A', position=dict(x=10, y=20),
            provider='codex', model='fixture', effort='low', round_prompt='synthetic only')], edges=[])
        self.draft = self.store.save(self.workspace, None, None, self.doc)
        with patch('v2.runtime.CodexProvider', SyntheticProvider): self.rt = V2Runtime(self.store)

    def definition(self): return compile_definition(self.store, self.rt.provider, self.workspace, self.draft)

    def test_compile_has_technical_nodes_but_saved_draft_remains_exact(self):
        definition = self.definition()
        self.assertEqual([n['kind'] for n in definition['graph']['nodes']], ['start','agent','result'])
        self.assertEqual(self.store.draft(self.workspace, self.draft['id'])['graph'], self.doc)
        self.assertNotEqual(definition['graph_sha256'], self.draft['revision'])

    def test_publication_failure_keeps_intent_and_never_sends(self):
        original_write = os.write
        go = []
        def watch_write(fd, data):
            if data == b'GO\n': go.append(data)
            return original_write(fd, data)
        with patch('v2.worker_facts.publish_worker', side_effect=OSError('synthetic publication failure')), \
                patch('v2.runtime.os.write', side_effect=watch_write), self.assertRaises(OSError):
            self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'synthetic-launch-key-1')
        self.assertEqual(go, [])
        run = self.store.db.list('run')[0]
        self.assertEqual(run['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(run['actual_calls'], 0)
        self.assertEqual(self.store.db.list('native-step'), [])
        self.assertEqual(self.store.db.list('activation'), [])
        with patch.object(self.rt, 'spawn') as spawn:
            retry = self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'synthetic-launch-key-1')
        spawn.assert_not_called(); self.assertEqual(retry['run']['id'], run['id'])
        with self.assertRaises(Conflict): self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'synthetic-launch-key-1', 'changed')

    def test_frozen_definition_does_not_change_after_draft_edit(self):
        with patch.object(self.rt, 'spawn'):
            run = self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'synthetic-launch-key-2')['run']
        old = self.store.db.get('definition', run['definition_id'])
        doc = copy.deepcopy(self.doc); doc['nodes'][0]['round_prompt'] = 'later draft'
        self.store.save(self.workspace, self.draft['id'], self.draft['revision'], doc)
        self.assertEqual(self.store.db.get('definition', run['definition_id']), old)

    def test_actual_request_reports_only_the_frozen_workspace_write_authorization(self):
        title = '本次启动已授权的工作区写入'
        cases = [
            ('Agent', 'workspace-write', 'workspace-write', True),
            ('Agent', 'workspace-write', 'read', False),
            ('Ask', 'none', 'workspace-write', False),
        ]
        for index, (mode, permission, scope, authorized) in enumerate(cases, 1):
            with self.subTest(mode=mode, permission=permission, frozen_scope=scope):
                cwd = self.store.workspace(self.workspace)['path']
                ident, revision = str(index)*32, str(index)*64
                config = dict(name='Fixture', role='Developer', mode=mode, permission=permission,
                              responsibilities='', background='', goal='', initial_prompt='Review request',
                              round_prompt='Modify only after operator approval.', output_prompt='')
                row = dict(id=ident, revision=revision, workspace=self.workspace, config=config)
                self.store.db.put('agent-config',ident,row)
                binding = dict(provider='codex', model='fixture', effort='low', cwd=cwd, cwd_identity=[],
                               runtime_profile='fixture', root_identity=[], local_lock_root='/fixture',
                               execution_mode=mode, v2_agent_scope=scope, home=str(self.root/'fixture-home'))
                with patch.object(self.rt,'spawn'), patch('v2.runtime.freeze_agent', return_value=(row,binding)):
                    run = self.rt.launch_agent(self.workspace,ident,revision,'fixture-write-prompt-'+str(index))['run']
                self.rt.worker_run = run['id']
                definition = self.store.db.get('definition',run['definition_id'])
                node = next(item for item in definition['graph']['nodes'] if item['kind']=='agent')
                activation = Scheduler(self.rt,run['id']).activation(node,'',0,())
                adapter = Mock()
                def record(prompt, cwd, artifacts, schema, journal, run_id):
                    journal.visible_request(prompt)
                    raise Blocked('fixture stops before native process')
                adapter.transport.return_value.start.side_effect = record
                with (patch.object(self.rt,'check_operator'),
                      patch.object(self.rt.cli_providers,'adapter_for', return_value=adapter),
                      self.assertRaisesRegex(Blocked,'fixture stops before native process')):
                    self.rt._native_turn(node,activation['id'],[],'',0)
                actual = self.store.db.get('activation',activation['id'])['actual_request']
                self.assertIn('Modify only after operator approval.',actual)
                self.assertEqual(title in actual,authorized)
                if authorized:
                    self.assertIn('独立的人工作业审批、任务中的其他明确批准条件以及原生沙箱限制仍然有效',actual)

    def test_scheduler_preserves_sent_uncertainty_after_dead_process(self):
        with patch.object(self.rt, 'spawn'):
            run = self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'synthetic-launch-key-3')['run']
        scheduler = Scheduler(self.rt, run['id'])
        node = self.definition()['graph']['nodes'][1]; activation = scheduler.activation(node, '', 0, ())
        def uncertain(*args):
            self.store.db.change('activation', activation['id'], {'effect_uncertain': True, 'state': 'RUNNING'})
            raise Blocked('synthetic missing receipt')
        scheduler.executor = uncertain
        scheduler.execute_node(node, activation['id'], [], '', 0, {}, ())
        self.assertEqual(self.store.db.get('activation', activation['id'])['state'], 'RECOVERY_REQUIRED')

    def test_receipt_correlation_negative_cases(self):
        process = dict(pid=111, host='fixture', start='123')
        activation = dict(observed_session='native-A', process=process, input_sent_operation_id='op1',
                          input_sent_sha256='request-hash', actual_request_sha256='request-hash', executed_image_sha256='image',
                          native_terminal_events=[dict(type='turn.completed', session_id='native-A', native_turn_id=None)])
        receipt = dict(session_id='native-A', native_turn_id=None, requested_model='fixture', requested_effort='low',
            process=dict(exit=0, timeout=False, overflow=False, cancelled=False, process=process, operation_id='op1'),
            report=dict(reply='synthetic', summary='', accepted=False, result_json='{}', artifacts=[], evidence=[], issues=[], next=''))
        activation['process_result'] = copy.deepcopy(receipt['process'])
        binding = dict(model='fixture', effort='low', _executable_sha256='image')
        verify_completion(activation, receipt, binding)
        changes = [lambda a,r:a.update(native_terminal_events=[]),lambda a,r:r.update(session_id='other'),
                   lambda a,r:r['process'].update(process=dict(pid=222)),lambda a,r:r.update(native_turn_id='other'),
                   lambda a,r:a.update(input_sent_sha256='other'),lambda a,r:r.update(resolved_model='other')]
        for change in changes:
            a,r = copy.deepcopy(activation),copy.deepcopy(receipt); change(a,r)
            with self.subTest(change=change),self.assertRaises(Blocked): verify_completion(a,r,binding)

    def test_terminal_failure_uses_same_process_request_turn_correlation(self):
        proc = dict(process={'pid':111}, operation_id='op1', exit=1, timeout=False, cancelled=False, overflow=False)
        activation = dict(observed_session='native-A', native_turn_id='turn-A', process={'pid':111}, process_result=copy.deepcopy(proc),
            input_sent_operation_id='op1', input_sent_sha256='sent', actual_request_sha256='sent', executed_image_sha256='image',
            native_terminal_events=[dict(type='turn.failed',session_id='native-A',native_turn_id='turn-A')])
        binding = {'_executable_sha256':'image'}
        self.assertTrue(terminal_correlated(activation,proc,binding,'turn.failed','native-A','turn-A'))
        for change in [lambda p:p.update(process={'pid':222}),lambda p:p.update(operation_id='other')]:
            bad = copy.deepcopy(proc);change(bad)
            self.assertFalse(terminal_correlated(activation,bad,binding,'turn.failed','native-A','turn-A'))
        self.assertFalse(terminal_correlated(activation,proc,binding,'turn.failed','native-A','wrong-turn'))

    def test_scheduler_definite_terminal_failure_and_cancel_are_not_unknown(self):
        for cancelled in (False,True):
            with patch.object(self.rt,'spawn'):
                run=self.rt.launch(self.workspace,self.draft['id'],self.draft['revision'],'definite-failure-'+str(cancelled))['run']
            self.store.db.change('run',run['id'],{'cancel_requested':cancelled})
            scheduler=Scheduler(self.rt,run['id']);node=self.definition()['graph']['nodes'][1]
            activation=scheduler.activation(node,'',0,())
            def failed(*args):
                self.store.db.change('activation',activation['id'],{'effect_uncertain':False,'terminal_failure_verified':True})
                raise Blocked('synthetic correlated terminal failure')
            scheduler.executor=failed;scheduler.execute_node(node,activation['id'],[],'',0,{},())
            self.assertEqual(self.store.db.get('activation',activation['id'])['state'],'CANCELLED' if cancelled else 'FAILED')

    def test_native_rpc_payload_is_not_public_catalog_error(self):
        fake = object.__new__(CodexProvider); fake.store = self.store; fake.home = '/synthetic'
        fake.config_root = '/synthetic/.codex'; fake.image = dict(path='/bin/true',sha256='a'*64); fake.version='fixture'
        secret = 'SYNTHETIC_SECRET_VALUE_ABC123'; url = 'https://private.example.invalid/token'
        with patch('v2.provider.CodexRPC', side_effect=Blocked('RPC auth ' + secret + ' ' + url)):
            result = fake.discover()
        serialized = json.dumps(result) + json.dumps(self.store.db.get('provider-catalog','codex'))
        self.assertNotIn(secret, serialized); self.assertNotIn(url, serialized)
        self.assertTrue(result['error']); self.assertTrue(result['stale'])


class WorkerGateTests(unittest.TestCase):
    def gate_case(self, mode):
        from v2 import worker
        from orchestration.store import process_identity
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        read_fd,write_fd=os.pipe();entered=threading.Event();result=[];original_select=worker.select.select
        own=process_identity(os.getpid());published=dict(own)
        if mode=='mismatch':published['start']='0'
        store=SimpleNamespace(state=Path(temp.name),db=SimpleNamespace(get=lambda *args:dict(state='QUEUED',worker=published)))
        store_factory=Mock(return_value=store);runtime=Mock();scheduler=Mock()
        def waiting(read,write,error,timeout):
            entered.set();return original_select(read,write,error,timeout)
        def execute():
            try:result.append(worker.main())
            except BaseException as exc:result.append(exc)
        argv=['worker','--state-dir',temp.name,'--allow-root',temp.name,'--run','fixture-run','--codex-cli','/fixture','--start-fd',str(read_fd)]
        with patch.object(worker,'DraftStore',store_factory),patch.object(worker,'V2Runtime',runtime),patch.object(worker,'Scheduler',scheduler),patch.object(worker.select,'select',side_effect=waiting),patch.object(sys,'argv',argv):
            thread=threading.Thread(target=execute);started=time.monotonic();thread.start()
            self.assertTrue(entered.wait(1));self.assertTrue(thread.is_alive());store_factory.assert_not_called();runtime.assert_not_called()
            if mode=='eof':os.close(write_fd);write_fd=None
            elif mode!='timeout':os.write(write_fd,b'GO\n')
            thread.join(12);self.assertFalse(thread.is_alive())
            if write_fd is not None:os.close(write_fd)
            self.assertEqual(result,[0 if mode=='go' else 1])
            if mode=='go':runtime.assert_called_once();scheduler.return_value.run_graph.assert_called_once()
            else:runtime.assert_not_called();scheduler.assert_not_called()
            if mode=='timeout':self.assertGreaterEqual(time.monotonic()-started,9.5)
    def test_actual_pipe_eof_cannot_enter_execution_layer(self):self.gate_case('eof')
    def test_actual_pipe_timeout_cannot_enter_execution_layer(self):self.gate_case('timeout')
    def test_published_owner_and_go_enters_controlled_scheduler(self):self.gate_case('go')
    def test_go_with_wrong_published_owner_is_refused(self):self.gate_case('mismatch')


if __name__ == '__main__': unittest.main()
