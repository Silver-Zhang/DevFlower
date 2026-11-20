"""V2 serves and executes the same artifact as the terminal compiler."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from http.client import HTTPConnection
import json
import os
import signal
import select
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from devflower_export import compile_workflow, read_saved
from v2.agent_records import AgentRecords
from v2.server import create_server
from v2.store import DraftStore


class ScriptExportTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='v2-script-export-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / 'projects' / 'A'
        self.project.mkdir(parents=True)
        self.store = DraftStore(self.root / 'state', [self.project.parent])
        self.workspace = self.store.open_workspace(str(self.project))['id']
        self.agents = AgentRecords(self.store)
        self.agent = self.agents.save(self.workspace, None, None, dict(
            execution='native-cli', name='Developer', provider='codex', cwd=str(self.project),
            role='Developer', round_prompt='Implement the task', output_prompt='Deliver results'))
        self.document = dict(version=1, name='Native workflow', nodes=[dict(
            id='Developer', name='Developer', kind='agent', position=dict(x=0, y=0),
            agent_id=self.agent['id'], agent_revision=self.agent['revision'])], edges=[])
        self.draft = self.store.save(self.workspace, None, None, self.document)
        self.request = dict(workspace=self.workspace, id=self.draft['id'],
                            revision=self.draft['revision'], native_config=True)

    @contextmanager
    def running(self):
        server, token = create_server(self.store)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def post(self, service, data=None, token=None):
        return self.http(service, 'drafts/export-python', data or self.request, token)

    def http(self, service, route, data=None, token=None):
        port, expected_token = service
        connection = HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('POST' if data is not None else 'GET', '/api/v2/' + route,
                               json.dumps(data) if data is not None else None, {
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (expected_token if token is None else token)})
            response = connection.getresponse()
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def snapshot(self):
        with self.store.db.tx(read=True) as db:
            return tuple(db.execute(query).fetchall() for query in (
                'SELECT kind,id,data FROM records ORDER BY kind,id',
                'SELECT * FROM events ORDER BY seq', 'SELECT * FROM launch_intents ORDER BY workspace,intent_key'))

    def test_gui_and_terminal_compilers_produce_identical_bytes_without_mutation(self):
        database_before = (self.store.state / 'v2.sqlite').read_bytes()
        names_before = {path.name for path in self.store.state.iterdir()}
        graph, records, root = read_saved(self.store.state / 'v2.sqlite', self.workspace, self.draft['id'])
        self.assertEqual({path.name for path in self.store.state.iterdir()}, names_before)
        self.assertEqual((self.store.state / 'v2.sqlite').read_bytes(), database_before)
        filename, source = compile_workflow(graph, records, root, native_config=True)
        before = self.snapshot()
        with self.running() as service, patch('subprocess.Popen') as spawn:
            for _ in range(2):
                status, result = self.post(service)
                self.assertEqual(status, 200, result)
                self.assertEqual({key: result[key] for key in ('workspace','id','revision')},
                                 {key: self.request[key] for key in ('workspace','id','revision')})
                self.assertEqual((result['filename'], result['source']), (filename, source))
                self.assertEqual(result['sha256'], sha256(source.encode()).hexdigest())
            spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_export_requires_explicit_policy_and_current_saved_revision(self):
        before = self.snapshot()
        with self.running() as service:
            for data in (dict(self.request, native_config=False), dict(self.request, native_config='true'),
                         dict(self.request, revision='0'*64), dict(self.request, revision=None)):
                with self.subTest(data=data):
                    status, result = self.post(service, data)
                    self.assertEqual(status, 400, result)
                    self.assertNotIn('source', result)
        self.assertEqual(self.snapshot(), before)

    def test_export_rejects_unknown_agent_revision_and_does_not_rebind(self):
        document = dict(self.document, nodes=[dict(self.document['nodes'][0], agent_revision='f'*64)])
        draft = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], document)
        before = self.snapshot()
        with self.running() as service, patch('subprocess.Popen') as spawn:
            status, result = self.post(service, dict(self.request, revision=draft['revision']))
            self.assertEqual(status, 400)
            self.assertIn('stale', result['error'])
            spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_exact_native_agent_versions_coexist_and_compile_read_only(self):
        new = self.agents.save(self.workspace, self.agent['id'], self.agent['revision'],
                               dict(self.agent['config'], provider='claude', model='sonnet'))
        self.assertEqual(self.agents.get_native_version(self.workspace, self.agent['id'], self.agent['revision']),
                         self.agent)
        self.assertEqual(self.agents.references(self.workspace, new['id'], new['revision'])['workflows'], [])
        with self.running() as service, patch('subprocess.Popen') as spawn:
            original_before = self.snapshot()
            status, result = self.post(service)
            self.assertEqual(status, 200, result)
            self.assertEqual(self.snapshot(), original_before)
            self.assertIn("'provider': 'codex'", result['source'])
            nodes = self.document['nodes'] + [dict(self.document['nodes'][0], id='Reviewer', name='Reviewer',
                                                  agent_revision=new['revision'])]
            document = dict(self.document, nodes=nodes, edges=[dict(id='handoff', source='Developer', target='Reviewer')])
            draft = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], document)
            before = self.snapshot()
            status, result = self.post(service, dict(self.request, revision=draft['revision']))
            self.assertEqual(status, 200, result)
            self.assertIn("'provider': 'codex'", result['source'])
            self.assertIn("'provider': 'claude'", result['source'])
            self.assertIn("'model': 'sonnet'", result['source'])
            self.assertEqual(self.snapshot(), before)
            spawn.assert_not_called()

    def test_export_is_scoped_and_authenticated(self):
        other = self.project.parent / 'B'
        other.mkdir()
        workspace = self.store.open_workspace(str(other))['id']
        with self.running() as service:
            self.assertEqual(self.post(service, token='invalid')[0], 401)
            self.assertEqual(self.post(service, dict(self.request, workspace=workspace))[0], 400)

    def test_export_uses_only_selected_saved_document(self):
        edited = dict(self.document, name='New saved version')
        current = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], edited)
        with self.running() as service:
            self.assertEqual(self.post(service)[0], 400)
            status, result = self.post(service, dict(self.request, revision=current['revision']))
            self.assertEqual(status, 200, result)
            self.assertIn('New saved version', result['source'])


    def native_fixture(self):
        reviewer = self.agents.save(self.workspace, None, None, dict(
            self.agent['config'], name='Reviewer', provider='claude', round_prompt='Review the handoff'))
        graph = dict(self.document, nodes=self.document['nodes'] + [dict(
            id='Reviewer', name='Reviewer', kind='agent', position=dict(x=200, y=0),
            agent_id=reviewer['id'], agent_revision=reviewer['revision'])],
            edges=[dict(id='handoff', source='Developer', target='Reviewer')])
        self.draft = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], graph)
        self.request['revision'] = self.draft['revision']
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        fixture = r"""import json, sys, time
from pathlib import Path
tool = Path(sys.argv[0]).name
prompt = sys.stdin.read()
with Path('calls.jsonl').open('a') as log:
    log.write(json.dumps({'tool': tool, 'prompt': prompt, 'args': sys.argv[1:]}) + '\n')
if tool == 'codex':
    while Path('wait').exists():
        time.sleep(.02)
    if Path('fail').exists():
        print('fixture failure', file=sys.stderr)
        sys.exit(7)
    assert sys.argv[1:3] == ['exec', '--output-last-message']
    Path(sys.argv[3]).write_text('IMPLEMENTED DELIVERY_TOKEN=fixture-test-nonce')
else:
    assert sys.argv[1:] == ['-p', '--output-format', 'json']
    assert 'IMPLEMENTED' in prompt
    print(json.dumps(dict(type='result', is_error=False, result='REVIEWED')))
"""
        for cli in ('codex', 'claude'):
            path = bin_dir / cli
            path.write_text('#!' + sys.executable + '\n' + fixture)
            path.chmod(0o700)
        self.addCleanup(self.stop_waiting_fixture)
        return dict(workspace=self.workspace, draft_id=self.draft['id'], revision=self.draft['revision'],
                    intent_key='script-native-intent', task='A real task file', execution='native-cli'), bin_dir

    def stop_waiting_fixture(self):
        (self.project / 'wait').unlink(missing_ok=True)

    def wait_for_state(self, service, route, predicate):
        for _ in range(150):
            status, snapshot = self.http(service, route)
            self.assertEqual(status, 200, snapshot)
            if predicate(snapshot):
                return snapshot
            time.sleep(.03)
        self.fail(snapshot)

    def test_check_failure_projects_into_gui_and_blocks_both_continuation_paths(self):
        command = dict(id='Check', name='Business check', kind='tool', config_version=1,
                       position=dict(x=0, y=0), config=dict(tool='command', cwd='.',
                       argv=[sys.executable, '-c', "print('CHECK-FAIL'); raise SystemExit(7)"]))
        graph = dict(self.document, execution='native-cli', nodes=[command])
        draft = self.store.save(self.workspace, self.draft['id'], self.draft['revision'], graph)
        with self.running() as service:
            for version in (13, 14):
                with self.subTest(compiler_version=version), patch('devflower_export.COMPILER_VERSION', version):
                    launch = dict(workspace=self.workspace, draft_id=draft['id'], revision=draft['revision'],
                                  intent_key='business-check-version-' + str(version), task='Check evidence',
                                  execution='native-cli')
                    code, value = self.http(service, 'launch', launch)
                    self.assertEqual(code, 200, value)
                    run = value['run']
                    params = '?workspace=' + self.workspace + '&id=' + run['id']
                    final = self.wait_for_state(service, 'run' + params, lambda row: row['state'] == 'CHECK_FAILED')
                    self.assertEqual(final['script']['failed_checks'], [dict(node='Check', returncode=7)])
                    self.assertEqual(final['script'].get('legacy_tool_outcome', False), version == 13)
                    code, listed = self.http(service, 'script-run/summaries?workspace=' + self.workspace + '&ids=' + run['id'])
                    self.assertEqual(code, 200, listed)
                    self.assertGreater(listed[0].pop('verified_at'), 0)
                    self.assertEqual(listed, [dict(id=run['id'], state='CHECK_FAILED', task='Check evidence',
                                                  failures=[dict(node='Check', name='Business check', returncode=7)])])
                    code, options = self.http(service, 'script-run/continuation' + params)
                    self.assertEqual(code, 409, options)
                    for choice in ('previous', 'current'):
                        next_run = dict(launch, intent_key=f'blocked-continuation-{version}-{choice}',
                                        continue_from=dict(run_id=run['id'], selection=['result'], version=choice,
                                                           sha256=final['script']['artifact']['sha256']))
                        code, result = self.http(service, 'launch', next_run)
                        self.assertEqual(code, 409, result)
            self.assertEqual(len(self.store.db.list('script-run')), 2)

    def test_duplicate_launch_reconnect_and_terminal_use_frozen_artifact(self):
        data, bin_dir = self.native_fixture()
        (self.project / 'wait').touch()
        with patch.dict(os.environ, PATH=str(bin_dir)):
            with self.running() as service:
                _, exported = self.post(service)
                with ThreadPoolExecutor(2) as pool:
                    responses = list(pool.map(lambda _: self.http(service, 'launch', data), range(2)))
                self.assertTrue(all(status == 200 for status, _ in responses), responses)
                run_id = responses[0][1]['run']['id']
                self.assertEqual(run_id, responses[1][1]['run']['id'])
                query = f'workspace={self.workspace}&id={run_id}'
                self.wait_for_state(service, 'run?' + query,
                                    lambda s: any(e['kind'] == 'PROCESS_STARTED' for e in s['script'].get('events', [])))
                self.store.save(self.workspace, self.draft['id'], self.draft['revision'],
                                dict(self.draft['graph'], name='Later edit'))
                self.assertEqual(self.http(service, 'launch', dict(data, task='Other task'))[0], 409)
            # The original owner is still working when the HTTP server is gone.
            self.stop_waiting_fixture()
            with self.running() as service:
                self.assertEqual(self.http(service, 'launch', data)[1]['run']['id'], run_id)
                finished = self.wait_for_state(service, 'run?' + query, lambda s: s['state'] == 'COMPLETED')
                self.assertEqual(finished['script']['document'], self.draft['graph'])
                self.assertEqual(finished['script']['completed_nodes'], ['Developer', 'Reviewer'])
                self.assertEqual([e['returncode'] for e in finished['script']['events'] if e['kind'] == 'PROCESS_EXITED'], [0, 0])
                self.assertEqual(len(self.http(service, 'runs?workspace=' + self.workspace)[1]), 1)
                _, persisted = self.http(service, 'script-run/source?' + query)
                self.assertEqual(persisted, {key: exported[key] for key in ('filename', 'source', 'sha256')})
                _, details = self.http(service, 'script-run/evidence?' + query + '&node=Reviewer')
                self.assertEqual(details['answer']['text'], 'REVIEWED')
                self.assertEqual(json.loads(details['prompt']['text'].rsplit(' (data)\n', 1)[1])['answer'], 'IMPLEMENTED DELIVERY_TOKEN=fixture-test-nonce')
                self.assertEqual(self.http(service, 'script-run/evidence?' + query + '&node=../task.txt')[0], 400)
            self.assertEqual(len((self.project/'calls.jsonl').read_text().splitlines()), 2)
            other = self.root / 'terminal'
            other.mkdir()
            artifact = other / persisted['filename']
            artifact.write_text(persisted['source'])
            result = subprocess.run([sys.executable, '-I', str(artifact), '--workspace', str(other),
                                     '--task', data['task'], '--run-dir', str(other/'run')],
                                    cwd=self.root, capture_output=True, text=True, timeout=5)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, 'REVIEWED'), result.stderr)

    def test_cancel_during_native_call_is_cooperative_and_never_launches_downstream(self):
        data, bin_dir = self.native_fixture()
        (self.project / 'wait').touch()
        with self.running() as service, patch.dict(os.environ, PATH=str(bin_dir)):
            status, started = self.http(service, 'launch', data)
            self.assertEqual(status, 200, started)
            run_id = started['run']['id']
            route = f'run?workspace={self.workspace}&id={run_id}'
            self.wait_for_state(service, route, lambda s: (self.project/'calls.jsonl').is_file())
            for _ in range(2):
                status, result = self.http(service, 'cancel', dict(workspace=self.workspace, run_id=run_id))
                self.assertEqual(status, 200, result)
                self.assertTrue(result['script']['cancel_requested'])
            stopped = self.wait_for_state(service, route, lambda s: s['state'] == 'CANCELLED')
            self.assertEqual(stopped['script']['completed_nodes'], [])
            self.assertEqual(len((self.project/'calls.jsonl').read_text().splitlines()), 1)

    @unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'pidfd_open'), 'Linux pidfd required')
    def test_export_supervisor_cleans_owned_group_after_hard_owner_death(self):
        marker = self.project / 'processes'
        code = (
            "import os,signal,sys,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "child=os.fork(); "
            "time.sleep(30) if child==0 else Path(sys.argv[1]).write_text(f'{os.getpid()} {child}'); "
            "time.sleep(30)"
        )
        graph = dict(version=1, name='Guarded tool', nodes=[
            dict(id='Clock', name='Timer', kind='tool', config=dict(
                tool='command', argv=[sys.executable, '-c', code, str(marker)]))], edges=[])
        filename, source = compile_workflow(graph, workspace=self.project, native_config=True)
        (self.project / filename).write_text(source)
        run = self.project / 'evidence'
        argv = [sys.executable, '-I', str(self.project / filename), '--run-dir', str(run)]
        owner = subprocess.Popen(argv, cwd=self.project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as unrelated:
            try:
                deadline = time.monotonic() + 5
                while (not marker.is_file() or not marker.read_text() or 'PROCESS_STARTED' not in
                       (run / 'events.jsonl').read_text()) and time.monotonic() < deadline:
                    time.sleep(.02)
                pid, child = map(int, marker.read_text().split())
                with ExitStack() as descriptors:
                    leader_handle = os.pidfd_open(pid)
                    descriptors.callback(os.close, leader_handle)
                    child_handle = os.pidfd_open(child)
                    descriptors.callback(os.close, child_handle)
                    owners = select.poll()
                    members = select.poll()
                    owners.register(leader_handle, select.POLLIN)
                    members.register(child_handle, select.POLLIN)
                    owner.send_signal(signal.SIGKILL)
                    self.assertEqual(owner.wait(timeout=5), -signal.SIGKILL)
                    requested = subprocess.run(argv + ['--cancel'], cwd=self.project,
                                               capture_output=True, timeout=5)
                    snapshot = json.loads(requested.stdout)
                    self.assertEqual(requested.returncode, 1)
                    self.assertEqual(snapshot['status'], 'RECOVERY_REQUIRED')
                    self.assertFalse(snapshot['owner_active'])
                    self.assertEqual(snapshot['last_process_observation']['pgid'], pid)
                    self.assertTrue(owners.poll(5000))
                    self.assertTrue(members.poll(5000))
                    self.assertIsNone(unrelated.poll())
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait(5)
                unrelated.terminate()

    def test_invalid_native_route_and_failed_call_leave_no_downstream_dispatch(self):
        data, bin_dir = self.native_fixture()
        (self.project / 'fail').touch()
        with self.running() as service, patch.dict(os.environ, PATH=str(bin_dir)):
            for invalid in (dict(data, execution='other'), dict(data, task_files=[{'path':'file'}])):
                self.assertEqual(self.http(service, 'launch', invalid)[0], 400)
            self.assertEqual(self.http(service, 'runs?workspace=' + self.workspace)[1], [])
            status, start = self.http(service, 'launch', data)
            self.assertEqual(status, 200, start)
            route = f"run?workspace={self.workspace}&id={start['run']['id']}"
            stopped = self.wait_for_state(service, route, lambda s: s['state'] == 'STOPPED')
            self.assertIn('exited with 7', stopped['script']['reason'])
            self.assertEqual(len((self.project/'calls.jsonl').read_text().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
