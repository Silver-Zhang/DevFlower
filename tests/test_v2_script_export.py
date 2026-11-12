"""The GUI exports the exact terminal compiler artifact without executing it."""
from contextlib import contextmanager
from hashlib import sha256
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
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
        port, expected_token = service
        connection = HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('POST', '/api/v2/drafts/export-python', json.dumps(data or self.request), {
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

    def test_export_rejects_stale_agent_and_does_not_launch_or_rebind(self):
        self.agents.save(self.workspace, self.agent['id'], self.agent['revision'],
                         dict(self.agent['config'], role='Changed'))
        before = self.snapshot()
        with self.running() as service, patch('subprocess.Popen') as spawn:
            status, result = self.post(service)
            self.assertEqual(status, 400)
            self.assertIn('stale', result['error'])
            spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)

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


if __name__ == '__main__':
    unittest.main()
