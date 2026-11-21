"""Exercise generated scripts through independent run directories and native CLI fixtures."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from devflower_export import compile_workflow, document_agents
from workbench.agents.agent_records import AgentRecords
from workbench.runs.script_runs import ScriptRuns
from workbench.workspace.store import DraftStore
from workbench.workspace.workflow_templates import classic_templates


CLI = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

assert 'DEEPSEEK_API_KEY' not in os.environ
prompt = sys.stdin.read()
if 'Wait for cancellation' in prompt:
    time.sleep(30)
answer = 'Final delivery'
if prompt.startswith('Department\\nProducer'):
    output = Path('progress.txt')
    output.write_text(prompt.split('Workflow task\\n')[1].split('\\n\\n')[0])
    answer = json.dumps({'result': {'schema_version': 1, 'summary': output.read_text(), 'data': {}},
                         'artifacts': ['progress.txt']})
Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(answer)
"""


class WorkflowSeriesTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text(CLI)
        codex.chmod(0o700)
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                        DEEPSEEK_API_KEY='fixture-assistant-key')
        self.document = dict(version=1, name='Progress work', nodes=[
            dict(id='Producer', name='Producer', kind='agent', provider='codex', execution='native-cli',
                 role='Producer', cwd='.', round_prompt='', output_prompt='', task_prompt='Draft',
                 native_output=dict(version=1, kind='result'), artifact_paths=['progress.txt']),
            dict(id='Report', name='Report', kind='agent', provider='codex')],
            edges=[dict(id='HandOff', source='Producer', target='Report',
                        transfer=dict(mode='none'))])
        self.script = self.compile_script(self.document)

    def compile_script(self, document):
        filename, source = compile_workflow(document, workspace=self.project, native_config=True)
        script = self.root / filename
        script.write_text(source)
        return script

    def command(self, *args, script=None):
        return [sys.executable, str(script or self.script), *map(str, args)]

    def call(self, *args, script=None, ok=True):
        result = subprocess.run(self.command(*args, script=script), cwd=self.project, env=self.env,
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result

    def options(self, previous, *args, script=None):
        return json.loads(self.call('--continue-from', previous, '--continuation-options',
                                    *args, script=script).stdout)

    def launch(self, directory, task, *args, script=None, ok=True):
        return self.call('--run-dir', directory, '--task', task, *args, script=script, ok=ok)

    def state(self, directory, *, script=None):
        return json.loads(self.call('--run-dir', directory, '--status', '--historical', '--details',
                                    script=script).stdout)

    @staticmethod
    def fingerprint(directory):
        return {str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob('*')) if path.is_file()}

    def test_three_independent_runs_transfer_only_verified_selections(self):
        first, second, third = [self.root / f'round-{number}' for number in range(1, 4)]
        self.launch(first, 'Round one')
        first_bytes = self.fingerprint(first)
        choices = self.options(first)
        self.assertEqual([option['id'] for option in choices['options']],
                         ['result', 'structured:Producer', 'artifact:Producer:0'])
        self.launch(second, 'Round two', '--continue-from', first,
                    '--inherit', 'result', '--inherit', 'artifact:Producer:0')
        second_bytes = self.fingerprint(second)
        self.launch(third, 'Round three', '--continue-from', second, '--inherit', 'structured:Producer')
        runs = [self.state(path) for path in (first, second, third)]
        self.assertEqual([run['status'] for run in runs], ['COMPLETED'] * 3)
        ids = [run['series']['run_id'] for run in runs]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([run['series']['id'] for run in runs], [ids[0]] * 3)
        self.assertEqual([run['series']['round'] for run in runs], [1, 2, 3])
        self.assertEqual([run['series']['previous_run_id'] for run in runs], [None, *ids[:2]])
        for index, path in enumerate((first, second, third)):
            self.assertEqual((path / 'task.txt').read_text(), ('Round one', 'Round two', 'Round three')[index])
        self.assertEqual(self.fingerprint(first), first_bytes)
        self.assertEqual(self.fingerprint(second), second_bytes)
        inherited = json.loads((second / 'continuation.json').read_text())['deliveries']
        self.assertEqual(inherited[0]['value'], 'Final delivery')
        self.assertEqual(Path(inherited[1]['file']).read_text(), 'Round one')
        self.assertEqual((self.project / 'progress.txt').read_text(), 'Round three')
        self.assertIn('Selected previous run deliveries', (second / 'Producer/prompt.txt').read_text())
        self.assertNotIn('Selected previous run deliveries', (second / 'Report/prompt.txt').read_text())
        third_inherited = json.loads((third / 'continuation.json').read_text())['deliveries'][0]
        self.assertEqual(third_inherited['value']['result']['summary'], 'Round two')

    def test_explicit_version_change_and_corrupt_or_incomplete_predecessor(self):
        first, second = self.root / 'first', self.root / 'second'
        self.launch(first, 'Round one')
        edited = self.compile_script(dict(self.document, name='Edited progress work'))
        old_hash, new_hash = [sha256(script.read_bytes()).hexdigest() for script in (self.script, edited)]
        choices = self.options(first, '--previous-script-sha256', old_hash, script=edited)
        self.assertTrue(choices['changed'])
        self.assertEqual((choices['previous_hash'], choices['current_hash']), (old_hash, new_hash))
        self.assertNotEqual(choices['previous_version']['source_sha256'], choices['current_version']['source_sha256'])
        common = ('--continue-from', first, '--inherit', 'result')
        self.assertIn('Script version changed', self.launch(second, 'Round two', *common,
            '--previous-script-sha256', old_hash, script=edited, ok=False).stderr)
        self.assertFalse(second.exists())
        self.launch(second, 'Round two', *common, '--previous-script-sha256', old_hash,
                    '--accept-version', new_hash, script=edited)
        self.assertEqual(self.state(second, script=edited)['status'], 'COMPLETED')
        for mode in ('result', 'journal', 'candidate'):
            damaged = self.root / ('damaged-' + mode)
            shutil.copytree(first, damaged)
            if mode == 'journal':
                journal = damaged / 'events.jsonl'
                journal.write_bytes(b'\\n'.join(journal.read_bytes().splitlines()[:-1]) + b'\\n')
            else:
                target = 'result.txt' if mode == 'result' else 'Producer/candidate-files/progress.txt'
                (damaged / target).write_text('Altered')
            refused = self.root / ('refused-' + mode)
            report = self.launch(refused, 'Do not run', '--continue-from', damaged, '--inherit', 'result', ok=False)
            self.assertIn('not a verified completion', report.stderr)
            self.assertFalse(refused.exists())
        active = self.root / 'active'
        worker = subprocess.Popen(self.command('--run-dir', active, '--task', 'Wait for cancellation'),
                                  cwd=self.project, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (active / 'Producer/prompt.txt').is_file():
                    break
                time.sleep(.02)
            self.assertTrue((active / 'Producer/prompt.txt').is_file())
            refused = self.root / 'refused-active'
            report = self.launch(refused, 'Do not run', '--continue-from', active, '--inherit', 'result', ok=False)
            self.assertIn('not a verified completion: RUNNING', report.stderr)
            self.assertFalse(refused.exists())
        finally:
            worker.terminate()
            worker.communicate(timeout=5)

    def test_entry_tool_receives_new_task_and_verified_previous_result_without_secret(self):
        command = ('import json, os, sys; assert "DEEPSEEK_API_KEY" not in os.environ; '
                   'raw = sys.stdin.read(); print(json.loads(raw)["task"] if raw else "first")')
        script = self.compile_script(dict(version=1, name='Command led work', nodes=[
            dict(id='Start', kind='start'),
            dict(id='Tool', kind='tool', config=dict(tool='command', permission='none', cwd='.',
                                                   argv=[sys.executable, '-c', command], timeout=5)),
            dict(id='Output', kind='output')],
            edges=[dict(id='In', source='Start', target='Tool'), dict(id='Out', source='Tool', target='Output')]))
        one, two = self.root / 'tool-one', self.root / 'tool-two'
        self.launch(one, 'Initial', script=script)
        self.launch(two, 'Follow up', '--continue-from', one, '--inherit', 'result', script=script)
        upstream = json.loads((two / 'Tool/upstream.json').read_text())
        self.assertEqual(upstream['task'], 'Follow up')
        value = upstream['continuation']['deliveries'][0]['value']
        self.assertEqual(json.loads(value)['result']['stdout'].strip(), 'first')
        self.assertEqual((two / 'Tool/stdout.log').read_text().strip(), 'Follow up')
        self.assertEqual(self.state(two, script=script)['status'], 'COMPLETED')

    def test_every_classic_classifies_after_binding_real_profile_revision(self):
        store = DraftStore(self.root / 'state', [self.project])
        workspace = store.open_workspace(str(self.project))['id']
        agents = AgentRecords(store)
        record = agents.save(workspace, None, None, dict(execution='native-cli', name='Fixture',
            provider='codex', cwd=str(self.project), role='Test role', round_prompt='', output_prompt=''))
        for item in classic_templates():
            document = item['graph']
            with self.subTest(name=document['name']):
                for actor in document_agents(document):
                    self.assertNotIn('agent_id', actor)
                    actor.update(agent_id=record['id'], agent_revision=record['revision'])
                checked = ScriptRuns(store, agents).check(workspace, document)
                self.assertEqual(checked['status'], 'unsupported' if item['reference'] else 'compilable', checked)


if __name__ == '__main__':
    unittest.main()
