"""Real worker ownership and SQLite checkpoint recovery. No Provider requests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import Blocked, canonical, digest
from orchestration.store import process_identity
from v2 import worker_facts
from v2.recovery import observe, resume
from v2.runtime import V2Runtime
from v2.store import DraftStore
from test_v2_execution import SyntheticProvider
import test_v2_recovery as legacy_recovery


class DesignerRecoveryTests(unittest.TestCase):
    child = legacy_recovery.RecoveryTests.child
    sent = legacy_recovery.RecoveryTests.sent

    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.workspaces = self.root / 'workspaces'
        self.workspaces.mkdir()
        self.store = DraftStore(self.root / 'state', [self.workspaces])
        self.workspace = self.store.open_workspace(str(self.workspaces))['id']
        with patch('v2.runtime.CodexProvider', side_effect=Blocked('fixture has no Provider')):
            self.runtime = V2Runtime(self.store)
        self.document = dict(version=1, name='Recovery fixture', nodes=[
            self.node('Start', 'start', initial_data='{"marker":"original frozen data"}'),
            self.node('Finish', 'output', format='json'),
        ], edges=[dict(id='result', source='Start', source_port='out', target='Finish', target_port='in')])
        self.draft = self.store.save(self.workspace, None, None, self.document)

    def node(self, key, kind, **config):
        return dict(id=key, name=key, kind=kind, position=dict(x=0, y=0), config_version=1, config=config)

    def add_agent(self):
        self.runtime.provider = SyntheticProvider(self.store)
        self.document['nodes'].insert(1, dict(id='Agent', name='Agent', position=dict(x=0, y=0),
                                           provider='codex', model='fixture', effort='low', round_prompt='fixture'))
        self.document['edges'] = [dict(id='handoff', source='Start', target='Agent'),
                                  dict(id='result', source='Agent', target='Finish')]
        self.draft = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], self.document)

    def parked_run(self, *, agent=False):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        self.addCleanup(self.stop, process)
        owner = process_identity(process.pid)

        def park(run):
            row = self.store.db.change('run', run, dict(launch_state='SPAWNING'), states={'QUEUED'})
            worker_facts.publish_worker(self.store.db, run, row['definition_id'], owner)
            worker_facts.enter_worker(self.store.db, run, owner)
            self.store.db.change('run', run, dict(state='RUNNING'), states={'QUEUED'})
            key = digest(canonical([run, 'Start', []]))
            self.store.db.put('activation', key, dict(id=key, run=run, node='Start', kind='start', name='Start',
                state='STARTING', inputs=[], iteration=0, iteration_path=[], round=1, attempt=1))
            if agent:
                key = digest(canonical([run, 'Agent', []]))
                self.store.db.put('activation', key, dict(id=key, run=run, node='Agent', kind='agent', name='Agent',
                    state='STARTING', inputs=[], iteration=0, iteration_path=[], round=1, attempt=1))

        self.payload = dict(workspace=self.workspace, draft_id=self.draft['id'], revision=self.draft['revision'],
                            intent_key='typed-recovery-fixture', task='old task')
        with patch.object(self.runtime, 'spawn', side_effect=park):
            first = self.runtime.launch(**self.payload)
        return first['run']['id'], process

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)

    def settle(self, run):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            value = self.runtime.snapshot(self.workspace, run)
            if not value['worker_alive'] and value['state'] in ('COMPLETED', 'FAILED', 'CANCELLED'):
                return value
            time.sleep(.03)
        self.fail('Replacement worker did not finish')

    def test_dead_checkpoint_concurrent_resume_reuses_exact_run_and_frozen_definition(self):
        run, old = self.parked_run()
        with self.assertRaises(Blocked):
            resume(self.runtime, self.workspace, run)
        self.stop(old)
        self.assertEqual(observe(self.store.db.get('run', run)['worker']), 'dead')
        self.assertTrue(self.runtime.snapshot(self.workspace, run)['run']['resume_available'])
        self.assertEqual(self.runtime.launch(**self.payload)['run']['id'], run)
        self.assertEqual(self.store.db.get('run', run)['worker']['pid'], old.pid)
        self.document['nodes'][0]['config']['initial_data'] = '{"marker":"edited draft"}'
        self.store.save(self.workspace, self.draft['id'], self.draft['revision'], self.document)

        def try_resume(_):
            try:
                return resume(self.runtime, self.workspace, run)['run']['id']
            except Blocked:
                return None

        real_spawn = subprocess.Popen
        started = []
        def retain_child(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            self.addCleanup(self.stop, process)
            started.append(process)
            return process

        with patch('v2.runtime.subprocess.Popen', side_effect=retain_child):
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(try_resume, range(4))).count(run), 1)
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].wait(timeout=10), 0)
        value = self.settle(run)
        self.assertEqual(value['state'], 'COMPLETED')
        self.assertEqual(value['run']['output']['result'], {'marker': 'original frozen data'})
        self.assertEqual(value['run']['actual_calls'], 0)
        self.assertEqual(value['provider_outcome'], 'not_sent')
        self.assertEqual(len(self.store.db.list('run')), 1)
        self.assertEqual(len(self.store.db.list('activation')), 2)
        events = value['events']
        entries = [event for event in events if event['kind'] == 'worker-entered']
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0]['event_id'], entries[1]['event_id'])
        authorized = [event for event in events if event['kind'] == 'worker-resume-authorized']
        self.assertEqual(len(authorized), 1)
        self.assertEqual(authorized[0]['payload']['previous_worker']['pid'], old.pid)
        self.assertEqual(sum(event['kind'] == 'node-ready-recovered' for event in events), 1)
        self.assertEqual(DraftStore(self.root / 'state', [self.workspaces]).db.get('run', run)['id'], run)

    def test_foreign_or_contradictory_checkpoint_never_spawns(self):
        run, old = self.parked_run()
        self.stop(old)
        original = self.store.db.get('run', run)
        foreign = dict(original['worker'], host=original['worker']['host'] + '.foreign')
        self.store.db.change('run', run, dict(worker=foreign))
        self.assertFalse(self.runtime.snapshot(self.workspace, run)['run']['resume_available'])
        with self.assertRaises(Blocked):
            resume(self.runtime, self.workspace, run)
        self.store.db.change('run', run, dict(worker=original['worker']))
        activation = self.store.db.list('activation')[0]
        self.store.db.put('native-step', 'uncertain', dict(id='uncertain', run=run, activation=activation['id'],
                                                         kind='PROCESS', data={}))
        snap = self.runtime.snapshot(self.workspace, run)
        self.assertEqual(snap['state'], 'RECOVERY_REQUIRED')
        self.assertFalse(snap['run']['resume_available'])
        with self.assertRaises(Blocked):
            resume(self.runtime, self.workspace, run)
        self.assertEqual(len([e for e in self.store.db.events(run) if e['kind'] == 'worker-entered']), 1)

    def test_receipt_recovery_materializes_exact_agent_without_settling_downstream(self):
        self.add_agent()
        run, old = self.parked_run(agent=True)
        self.stop(old)
        row = self.store.db.get('run', run)
        key = digest(canonical([run, 'Agent', []]))
        agent = self.sent(row, self.store.db.get('activation', key), completed=True)
        value = self.runtime.snapshot(self.workspace, run)
        self.assertTrue(value['run']['resume_available'])
        self.assertEqual(value['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.store.db.get('activation', key)['state'], 'RUNNING')
        self.assertNotIn('output', value['run'])

        with patch.object(self.runtime, 'spawn') as spawn:
            resume(self.runtime, self.workspace, run)
        spawn.assert_called_once_with(run)
        recovered = self.store.db.get('activation', key)
        self.assertEqual(recovered['state'], 'COMPLETED')
        self.assertEqual(recovered['request_id'], agent['request_id'])
        self.assertEqual(recovered['output']['answer'], 'synthetic completion')
        self.assertEqual(len(self.store.db.list('native-step')), 1)
        self.assertTrue(any(event['kind'] == 'node-completion-recovered' and event['activation'] == key
                            for event in self.store.db.events(run)))

    def test_sent_unknown_agent_stops_owned_process_and_never_replays(self):
        self.add_agent()
        run, old = self.parked_run(agent=True)
        self.stop(old)
        key = digest(canonical([run, 'Agent', []]))
        source = self.sent(self.store.db.get('run', run), self.store.db.get('activation', key))
        with patch.object(self.runtime, 'spawn') as spawn:
            self.assertEqual(self.runtime.launch(**self.payload)['run']['id'], run)
            with self.assertRaises(Blocked):
                resume(self.runtime, self.workspace, run)
            result = self.runtime.cancel(self.workspace, run)
            with self.assertRaises(Blocked):
                resume(self.runtime, self.workspace, run)
        spawn.assert_not_called()
        self.assertEqual(result['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(result['provider_outcome'], 'unknown')
        self.assertEqual(len(self.store.db.list('native-step')), 1)
        self.assertEqual(self.store.db.get('activation', key)['input_sent_sha256'], source['input_sent_sha256'])

    def test_cancel_dead_safe_checkpoint_persists_without_worker_replacement(self):
        run, old = self.parked_run()
        self.stop(old)
        with patch.object(self.runtime, 'spawn') as spawn:
            value = self.runtime.cancel(self.workspace, run)
            with self.assertRaises(Blocked):
                resume(self.runtime, self.workspace, run)
        spawn.assert_not_called()
        self.assertEqual(value['run']['persisted_state'], 'CANCELLED')
        self.assertEqual(self.store.db.list('activation')[0]['state'], 'CANCELLED')
        self.assertTrue(any(event['kind'] == 'node-cancelled' for event in value['events']))


if __name__ == '__main__':
    unittest.main()
