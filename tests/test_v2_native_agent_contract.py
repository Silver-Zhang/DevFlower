"""Native script configurations preserve storage and never enter managed execution."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from devflower_export import ExportError, compile_workflow, read_saved
from orchestration.common import Blocked
from studio.graph import Invalid
from studio.server import Conflict
from test_v2_agent_records import configuration as managed_configuration
from v2.agent_execution import freeze_agent
from v2.agent_records import AgentRecords, validate_agent
from v2.assistant_repairs import preview_repair
from v2.assistant_teams import stage
from v2.runtime import V2Runtime
from v2.store import DraftStore


def native_configuration(path, **changes):
    return dict(execution='native-cli', name='Developer', provider='codex', cwd=str(path),
                role='Developer', round_prompt='Implement and verify the task.',
                output_prompt='Explain the result and artifact paths.') | changes


class NativeAgentContractTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.cwd = self.root / 'spaces' / 'project'
        self.cwd.mkdir(parents=True)
        self.store = DraftStore(self.root / 'state', [self.cwd.parent])
        self.workspace = self.store.open_workspace(str(self.cwd))['id']
        self.agents = AgentRecords(self.store)

    def save(self, **changes):
        return self.agents.save(self.workspace, None, None, native_configuration(self.cwd, **changes))

    def graph(self, *records):
        nodes = [dict(id='Start', kind='start', name='Task', position=dict(x=0, y=0),
                      config_version=1, config={})]
        nodes.extend(dict(id='Agent' + str(index), kind='agent', name=row['config']['name'],
                          position=dict(x=100 + index * 100, y=0),
                          agent_id=row['id'], agent_revision=row['revision'])
                     for index, row in enumerate(records))
        nodes.append(dict(id='Output', kind='output', name='Result', position=dict(x=600, y=0),
                          config_version=1, config={'format': 'text'}))
        return dict(version=1, name='Native workflow', nodes=nodes, edges=[
            dict(id='Edge' + str(index), source=first['id'], target=second['id'])
            for index, (first, second) in enumerate(zip(nodes, nodes[1:]))])

    def runtime(self):
        with patch('v2.runtime.CodexProvider', return_value=None):
            return V2Runtime(self.store)

    def records(self):
        with self.store.db.tx(read=True) as db:
            return db.execute('SELECT kind,id,data FROM records ORDER BY kind,id').fetchall()

    def test_minimal_configuration_saves_without_native_capability_management(self):
        config = native_configuration(self.cwd)
        with (patch.object(self.agents, 'verify_session', side_effect=AssertionError('session probe')),
              patch.object(self.agents, 'skills', side_effect=AssertionError('skill probe')),
              patch.object(self.agents, 'independent_home', side_effect=AssertionError('home probe'))):
            saved = self.agents.save(self.workspace, None, None, config)
        self.assertEqual(saved['config'], config)
        self.assertEqual(self.agents.get(self.workspace, saved['id'], saved['revision']), saved)
        self.assertEqual(self.agents.list(self.workspace), {'agents': [saved]})
        self.assertNotIn('native_home_pin', saved)
        self.assertEqual(self.store.db.list('run'), [])

    def test_explicit_contract_rejects_unknown_or_managed_fields(self):
        config = native_configuration(self.cwd)
        missing_contract = dict(config)
        del missing_contract['execution']
        values = [missing_contract, dict(config, execution='other'), dict(config, role='invalid\ud800')]
        values += [dict(config, **extra) for extra in (
            {'skills': []}, {'subagents': 'native'}, {'permission': 'native'},
            {'mode': 'Agent'}, {'session': {'kind': 'new'}}, {'native_profile': 'default'},
            {'command': 'codex'}, {'env': {'EXAMPLE': 'value'}})]
        for value in values:
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.agents.save(self.workspace, None, None, value)
        self.assertEqual(self.agents.list(self.workspace), {'agents': []})

    def test_native_save_preserves_revision_idempotency_and_workspace_scope(self):
        config = native_configuration(self.cwd)
        created = self.agents.save(self.workspace, 'a' * 32, None, config)
        self.assertEqual(self.agents.save(self.workspace, created['id'], None, config), created)
        with self.assertRaises(Conflict):
            self.agents.save(self.workspace, created['id'], None, dict(config, role='Reviewer'))
        updated = self.agents.save(self.workspace, created['id'], created['revision'],
                                  dict(config, role='Reviewer'))
        self.assertNotEqual(updated['revision'], created['revision'])
        with self.assertRaises(Conflict):
            self.agents.save(self.workspace, created['id'], created['revision'], config)
        other = self.cwd.parent / 'other'
        other.mkdir()
        other_id = self.store.open_workspace(str(other))['id']
        with self.assertRaises(Conflict):
            self.agents.get(other_id, created['id'])
        with self.assertRaises(Invalid):
            self.agents.save(self.workspace, None, None, native_configuration(other))

    def test_native_save_refuses_managed_session_proof_even_for_idempotent_replay(self):
        config = native_configuration(self.cwd)
        saved = self.agents.save(self.workspace, 'b' * 32, None, config)
        with self.assertRaisesRegex(Invalid, '受管会话'):
            self.agents.save(self.workspace, saved['id'], None, config, 'stale-session-proof')
        self.assertEqual(self.agents.get(self.workspace, saved['id']), saved)

    def test_saved_native_chain_exports_read_only_and_rejects_stale_agent_version(self):
        developer = self.save()
        reviewer = self.save(name='Reviewer', provider='claude', role='Reviewer',
                             round_prompt='Review the upstream result.')
        graph = self.graph(developer, reviewer)
        draft = self.store.save(self.workspace, None, None, graph)
        before = self.records()
        document, agents, workspace = read_saved(self.store.state / 'v2.sqlite', self.workspace, draft['id'])
        filename, source = compile_workflow(document, agents, workspace, native_config=True)
        self.assertEqual((filename, source), compile_workflow(document, agents, workspace, native_config=True))
        self.assertEqual(self.records(), before)
        namespace = {'__name__': 'exported_native_test'}
        exec(compile(source, filename, 'exec'), namespace)
        self.assertEqual([row['provider'] for row in namespace['WORKFLOW']['agents']], ['codex', 'claude'])
        self.assertTrue(all(row['model'] is None and row['effort'] is None
                            for row in namespace['WORKFLOW']['agents']))
        self.assertTrue(all(row['cwd'] == '.' for row in namespace['WORKFLOW']['agents']))
        self.agents.save(self.workspace, developer['id'], developer['revision'],
                         dict(developer['config'], round_prompt='Changed task.'))
        document, agents, workspace = read_saved(self.store.state / 'v2.sqlite', self.workspace, draft['id'])
        with self.assertRaisesRegex(ExportError, 'revision'):
            compile_workflow(document, agents, workspace, native_config=True)

    def test_managed_inspection_and_launch_refuse_native_before_reserving_or_spawning(self):
        record = self.save()
        runtime = self.runtime()
        graph = self.graph(record)
        draft = self.store.save(self.workspace, None, None, graph)
        with (patch.object(runtime, 'spawn') as spawn,
              patch.object(runtime.cli_providers, 'adapter', side_effect=AssertionError('adapter called'))):
            result = runtime.inspect(self.workspace, graph)
            self.assertTrue(any('导出 Python' in item['message'] for item in result['issues']))
            with self.assertRaisesRegex(Invalid, '导出 Python'):
                runtime.launch_agent(self.workspace, record['id'], record['revision'], 'native-direct-intent')
            with self.assertRaisesRegex(Invalid, '导出 Python'):
                runtime.launch(self.workspace, draft['id'], draft['revision'], 'native-canvas-intent')
            spawn.assert_not_called()
        self.assertEqual(self.store.db.list('run'), [])
        with self.store.db.tx(read=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM launch_intents').fetchone()[0], 0)

    def test_native_agent_is_not_admitted_to_legacy_assistant_repair(self):
        record = self.save()
        activation = 'c' * 64
        runtime = SimpleNamespace(agents=self.agents, snapshot=lambda *_: {'state': 'FAILED'})
        assistant = SimpleNamespace(runtime=runtime, diagnosis=lambda *_: {
            'nodes': [dict(activation=activation, state='FAILED')]})
        data = dict(workspace=self.workspace, run='d' * 32, activation=activation,
                    agent=record['id'], revision=record['revision'], task='Repair.', files=[])
        with self.assertRaisesRegex(Blocked, 'Python'):
            preview_repair(assistant, data)

    def test_managed_team_update_refuses_native_before_normalizing_or_saving(self):
        developer = self.save(timeout=None)
        reviewer = self.save(name='Reviewer', role='Reviewer')
        graph = self.graph(developer, reviewer)
        draft = self.store.save(self.workspace, None, None, graph)
        proposed = dict(name='Replacement', role='Reviewer', responsibilities='Review changes',
                        background='Project context', goal='Find defects', initial_prompt='Read the task.',
                        round_prompt='Review the result.', handoff_prompt='Explain defects.',
                        slot='Replacement', skills=[], permission='none')
        plan = dict(version=1, kind='update', summary='Replace reviewer', name='Updated workflow',
                    agents=[proposed], rounds=1, operations=[], bindings={'Agent1': 'Replacement'})
        turn = dict(id='e' * 32, base=dict(id=draft['id'], revision=draft['revision'], document=graph))
        registry = SimpleNamespace(adapter=lambda *_: SimpleNamespace(binding=lambda *_: {}))
        assistant = SimpleNamespace(store=self.store, runtime=SimpleNamespace(agents=self.agents),
                                    model_operations=SimpleNamespace(registry=registry))
        selected = dict(provider='codex', native_profile=None, requested_model=None, requested_effort=None)
        before = self.records()
        with (patch('v2.assistant_teams.model_config', return_value=(selected, ['none'])),
              patch('v2.assistant_teams.normalize_graph', side_effect=AssertionError('managed normalization'))):
            with self.assertRaisesRegex(Invalid, '导出 Python'):
                stage(assistant, self.workspace, turn, plan, {'Replacement': 'none'}, None)
        self.assertEqual(self.records(), before)

    def test_legacy_records_are_not_converted_and_keep_subagent_and_permission_guards(self):
        config = managed_configuration(self.cwd)
        self.assertEqual(validate_agent(config), config)
        saved = self.agents.save(self.workspace, None, None, config)
        self.save()
        self.assertEqual(self.agents.get(self.workspace, saved['id']), saved)
        self.assertNotIn('execution', saved['config'])
        for changes in ({'subagents': 'native'}, {'permission': 'native'}):
            with self.subTest(changes=changes):
                row = self.agents.save(self.workspace, None, None, dict(config, **changes))
                with self.assertRaisesRegex(Invalid, '尚未开放'):
                    freeze_agent(SimpleNamespace(agents=self.agents), self.workspace,
                                 dict(agent_id=row['id'], agent_revision=row['revision']))


if __name__ == '__main__':
    unittest.main()
