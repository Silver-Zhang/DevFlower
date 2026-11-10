"""Workspace Agent drafts keep configuration, CLI selection and execution separate."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from studio.graph import Invalid
from studio.server import Conflict
from v2.agent_records import AgentRecords
from v2.documents import check_chain, validate_draft
from v2.runtime import V2Runtime
from v2.store import DraftStore


def configuration(path, **changes):
    return dict(dict(name='Example', role='Reviewer', responsibilities='Check results', background='Context',
                     goal='Find problems', template='reviewer', provider='claude', model=None,
                     model_source='native_default', effort='high', mode='Plan', permission='none', cwd=str(path),
                     session={'kind': 'new'}, skills=[], subagents='disabled', timeout=300, max_budget_usd='2.5',
                     initial_prompt='Initialize separately', round_prompt='Execute this round',
                     output_prompt='Handoff request'), **changes)


class AgentRecordTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        parent = root / 'workspaces'
        self.cwd, self.other = parent / 'first', parent / 'second'
        self.cwd.mkdir(parents=True)
        self.other.mkdir()
        self.store = DraftStore(root / 'state', [parent])
        self.first = self.store.open_workspace(str(self.cwd))['id']
        self.second = self.store.open_workspace(str(self.other))['id']
        self.agents = AgentRecords(self.store)

    def test_revision_conflict_idempotent_create_and_workspace_isolation(self):
        config = configuration(self.cwd)
        created = self.agents.save(self.first, '1' * 32, None, config)
        self.assertEqual(self.agents.save(self.first, created['id'], None, config), created)
        self.assertEqual(AgentRecords(self.store).list(self.first), {'agents': [created]})
        self.assertEqual(self.agents.list(self.second), {'agents': []})
        with self.assertRaises(Conflict):
            self.agents.get(self.second, created['id'])
        updated = self.agents.save(self.first, created['id'], created['revision'], dict(config, role='Developer'))
        with self.assertRaises(Conflict):
            self.agents.save(self.first, created['id'], created['revision'], config)
        with self.assertRaises(Conflict):
            self.agents.remove(self.first, updated['id'], created['revision'])
        self.assertEqual(self.agents.remove(self.first, updated['id'], updated['revision']), {'removed': updated['id']})

    def test_working_directory_and_browser_execution_fields_are_refused(self):
        self.cwd.joinpath('link').symlink_to(self.other)
        for path in (self.other, self.cwd / 'link', self.cwd / '..' / 'first'):
            with self.subTest(path=path), self.assertRaises(Invalid):
                self.agents.save(self.first, None, None, configuration(path))
        for extra in ({'command': 'claude'}, {'credential_env': {}}, {'native_profile': '/outside'}):
            with self.subTest(extra=extra), self.assertRaises(Invalid):
                self.agents.save(self.first, None, None, configuration(self.cwd, **extra))

    def test_existing_binding_requires_current_native_proof(self):
        session = {'kind': 'existing', 'handle': 'v1.' + 'a' * 64}
        config = configuration(self.cwd, session=session)
        with self.assertRaises(Invalid):
            self.agents.save(self.first, None, None, config)
        fake = SimpleNamespace(profiles={'claude': (SimpleNamespace(profile='current'), {})})
        fake.resolve = lambda *_: (SimpleNamespace(provider='claude', original_cwd=str(self.cwd)),
                                  SimpleNamespace(source=SimpleNamespace(profile='current')))
        self.agents.session_operations = fake
        with patch.object(self.agents, 'verify_session', return_value=None) as verify:
            saved = self.agents.save(self.first, None, None, config, 'current-revision')
            verify.assert_called_once_with(self.first, config, 'current-revision')
        edited = self.agents.save(self.first, saved['id'], saved['revision'], dict(config, role='Planner'))
        self.assertEqual(edited['config']['session'], session)
        with self.assertRaises(Invalid):
            self.agents.save(self.first, saved['id'], edited['revision'], dict(config, provider='codex'))

    def test_linked_workflow_draft_has_one_configuration_authority(self):
        node = dict(id='node1', name='Configured', position=dict(x=1, y=2), agent_id='1' * 32, agent_revision='a' * 64)
        doc = dict(version=1, name='Workflow', nodes=[node], edges=[])
        self.assertEqual(validate_draft(doc), doc)
        self.assertFalse(check_chain(doc)['issues'])
        for extra in ({'model': 'override'}, {'permission': 'read'}, {'agent_revision': None}):
            with self.subTest(extra=extra), self.assertRaises(Invalid):
                validate_draft(dict(doc, nodes=[dict(node, **extra)]))

    def test_direct_run_reuses_intent_without_creating_canvas_draft(self):
        with patch('v2.runtime.CodexProvider', return_value=None):
            runtime = V2Runtime(self.store)
        config = configuration(self.cwd)
        record = runtime.agents.save(self.first, None, None, config)
        binding = {'provider': 'claude', 'model': None, 'effort': 'high'}
        with (patch('v2.runtime.freeze_agent', return_value=(record, binding)),
              patch.object(runtime, 'spawn') as spawn):
            first = runtime.launch_agent(self.first, record['id'], record['revision'], 'fixture-direct-launch', 'task')
            runtime.agents.save(self.first, record['id'], record['revision'], dict(config, role='Changed'))
            again = runtime.launch_agent(self.first, record['id'], record['revision'], 'fixture-direct-launch', 'task')
            self.assertEqual(first['run']['id'], again['run']['id'])
            spawn.assert_called_once_with(first['run']['id'])
            with self.assertRaises(Conflict):
                runtime.launch_agent(self.first, record['id'], record['revision'], 'different-direct-intent', 'task')
        self.assertEqual(self.store.drafts(self.first), [])


if __name__ == '__main__':
    unittest.main()
