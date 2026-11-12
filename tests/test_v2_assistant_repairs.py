"""Assistant repair admission, exact authorization, and Runtime reservation."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.server import Conflict
from test_v2_agent_records import configuration
from v2.assistant_repairs import CONFIRM, launch_repair, preview_repair, recent_repair, repair_intent, repair_status
from v2.run_inspector import RunInspector
from v2.runtime import V2Runtime
from v2.store import DraftStore


class AssistantRepairTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.cwd = root / 'spaces' / 'repair'
        self.cwd.mkdir(parents=True)
        self.candidate = self.cwd / 'code.py'
        self.candidate.write_text('print(1)\n')
        self.git('init', '-b', 'repair-test')
        self.git('add', 'code.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Fixture')
        self.store = DraftStore(root / 'state', [self.cwd.parent])
        self.workspace = self.store.open_workspace(str(self.cwd))['id']
        with patch('v2.runtime.CodexProvider', return_value=None):
            self.runtime = V2Runtime(self.store)
        config = configuration(self.cwd, mode='Agent', permission='workspace-write')
        self.agent = self.runtime.agents.save(self.workspace, None, None, config)
        self.source, self.activation = '1' * 32, '2' * 64
        self.source_state = {'state': 'FAILED', 'worker_alive': False}
        snapshot = self.runtime.snapshot
        self.runtime.snapshot = lambda workspace, run: self.source_state if run == self.source else snapshot(workspace, run)
        self.assistant = SimpleNamespace(store=self.store, runtime=self.runtime, inspector=RunInspector(self.store),
            diagnosis=lambda *args: {'definition_id': '3' * 64, 'nodes': [{
                'activation': self.activation, 'node': 'Verify', 'state': 'FAILED', 'reason': 'Expected 2'}]})
        self.data = dict(workspace=self.workspace, run=self.source, activation=self.activation, agent=self.agent['id'],
                         revision=self.agent['revision'], task='Fix code.py and report verification.', files=['code.py'])

    def git(self, *args):
        return subprocess.run(['/usr/bin/git', *args], cwd=self.cwd, check=True, capture_output=True)

    def confirmation(self, **changes):
        preview = preview_repair(self.assistant, self.data)
        return dict(self.data, intent='repair-fixture-intent', preview_sha256=preview['sha256'],
                    authorization=CONFIRM) | changes

    def test_preview_is_read_only_and_authorized_launch_replays_one_run(self):
        approved = self.confirmation()
        self.assertEqual(self.store.db.list('run'), [])
        self.assertEqual(repair_intent(self.assistant, self.workspace, self.source, approved['intent']),
                         {'state': 'UNREGISTERED'})
        binding = {'provider': 'claude', 'model': None, 'effort': 'high'}
        with (patch('v2.runtime.freeze_agent', return_value=(self.agent, binding)),
              patch.object(self.runtime, 'spawn') as spawn):
            first = launch_repair(self.assistant, approved)
            self.assertEqual(first['source_run'], self.source)
            self.assertFalse(first['files'][0]['changed'])
            self.candidate.write_text('print(2)\n')
            replay = launch_repair(self.assistant, approved)
            self.assertEqual(replay['run'], first['run'])
            lookup = repair_intent(self.assistant, self.workspace, self.source, approved['intent'])
            self.assertEqual(lookup['run'], first['run'])
            self.assertEqual(recent_repair(self.assistant, self.workspace, self.source), first['run'])
            with self.assertRaises(Conflict):
                repair_intent(self.assistant, self.workspace, '4' * 32, approved['intent'])
            self.assertTrue(repair_status(self.assistant, self.workspace, first['run'])['files'][0]['changed'])
            spawn.assert_called_once_with(first['run'])
            with self.assertRaises(Conflict):
                launch_repair(self.assistant, dict(approved, task='Different request'))
        with self.assertRaises(Invalid):
            launch_repair(self.assistant, dict(approved, authorization=None))
        self.assertEqual(self.store.drafts(self.workspace), [])

    def test_stale_files_unknown_run_and_protected_branch_are_refused(self):
        approved = self.confirmation()
        self.candidate.write_text('print(7)\n')
        with self.assertRaises(Conflict):
            launch_repair(self.assistant, approved)
        self.source_state['state'] = 'RECOVERY_REQUIRED'
        with self.assertRaises(Conflict):
            preview_repair(self.assistant, self.data)
        self.source_state['state'] = 'FAILED'
        self.git('switch', '-c', 'main')
        with self.assertRaises(Blocked):
            preview_repair(self.assistant, self.data)
        self.assertEqual(self.store.db.list('run'), [])

    def test_worker_rechecks_git_branch_before_any_native_provider_work(self):
        approved = self.confirmation()
        binding = {'provider': 'claude', 'model': None, 'effort': 'high'}
        with (patch('v2.runtime.freeze_agent', return_value=(self.agent, binding)),
              patch.object(self.runtime, 'spawn')):
            launched = launch_repair(self.assistant, approved)
        self.runtime.worker_run = launched['run']
        self.git('switch', '-c', 'different-branch')
        with (patch.object(self.runtime.cli_providers, 'adapter_for') as adapter,
              self.assertRaisesRegex(Blocked, 'Git 分支或 HEAD 已变化')):
            self.runtime.check_operator()
        adapter.assert_not_called()
