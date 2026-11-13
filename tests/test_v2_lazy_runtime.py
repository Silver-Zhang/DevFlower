"""Script-first bootstrap stays independent of optional managed execution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import devflower_v2
from orchestration.common import Blocked
from v2.agent_records import AgentRecords
from v2.server import create_server
import test_v2_script_export as fixtures
import test_v2_execution_http as legacy_http

ROOT = Path(__file__).resolve().parents[1]
# A fresh interpreter must not even import managed execution on the native path.
LAUNCHER = r'''
import importlib.abc, json, sys
class NoManagedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in {'v2.runtime', 'v2.provider', 'v2.cli_providers', 'v2.agent_execution',
                    'v2.codex_terminals', 'v2.session_operations', 'studio.workbench_scheduler'}:
            raise ImportError('UNEXPECTED_MANAGED_IMPORT: ' + name)
sys.meta_path.insert(0, NoManagedImports())
import devflower_v2
def ready(port, token, store):
    print(json.dumps([port, token]), flush=True)
raise SystemExit(devflower_v2.main(sys.argv[1:], ready=ready))
'''


class LazyRuntimeTests(unittest.TestCase):
    setUp = fixtures.ScriptExportTests.setUp
    http = fixtures.ScriptExportTests.http
    native_fixture = fixtures.ScriptExportTests.native_fixture
    stop_waiting_fixture = fixtures.ScriptExportTests.stop_waiting_fixture
    wait_for_state = fixtures.ScriptExportTests.wait_for_state

    @contextmanager
    def server(self, factory):
        server, token = create_server(self.store, runtime_factory=factory)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def stub_runtime(self):
        return SimpleNamespace(provider=None, cli_providers=SimpleNamespace(
            listing=Mock(return_value={'providers': []})), agents=AgentRecords(self.store),
            catalog=Mock(return_value={'models': [], 'inference_verified': False}))

    def test_actual_default_launcher_runs_native_chain_without_managed_imports(self):
        data, bin_dir = self.native_fixture()
        env = dict(os.environ, PATH=str(bin_dir), PYTHONPATH=str(ROOT / 'tools'))
        process = subprocess.Popen([sys.executable, '-B', '-c', LAUNCHER,
            '--state-dir', str(self.store.state), '--allow-root', str(self.project.parent),
            '--codex-cli', '/not-needed-for-native-scripts', '--no-browser'],
            env=env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'Launcher did not become ready')
            ready = process.stdout.readline()
            if not ready:
                self.fail(process.communicate(timeout=5))
            service = json.loads(ready)
            code, status = self.http(service, 'status')
            self.assertEqual(code, 200)
            self.assertFalse(status['managed_runtime_loaded'])
            self.assertIsNone(status['execution_available'])
            self.assertIsNone(status['provider'])  # No fabricated CLI metadata.
            for route in ('drafts?', 'agents?', 'runs?', 'project-files?', 'workflow-templates?', 'history-archives?'):
                self.assertEqual(self.http(service, route + 'workspace=' + self.workspace)[0], 200)
            saved = self.http(service, 'agents/save', dict(workspace=self.workspace, id=None, expected=None,
                config=dict(self.agent['config'], name='New native Agent'), session_revision=None))
            self.assertEqual(saved[0], 200, saved)
            checked = self.http(service, 'check', dict(workspace=self.workspace,
                document=self.draft['graph'], execution='native-cli'))
            self.assertTrue(checked[1]['execution_available'], checked)
            code, exported = self.http(service, 'drafts/export-python', self.request)
            self.assertEqual(code, 200)
            code, started = self.http(service, 'launch', data)
            self.assertEqual(code, 200, started)
            route = f"run?workspace={self.workspace}&id={started['run']['id']}"
            final = self.wait_for_state(service, route, lambda row: row['state'] == 'COMPLETED')
            self.assertEqual(final['script']['artifact']['sha256'], exported['sha256'])
            self.assertEqual(final['script']['completed_nodes'], ['Developer', 'Reviewer'])
            self.assertEqual(self.http(service, 'cancel', dict(workspace=self.workspace,
                run_id=started['run']['id']))[1]['state'], 'COMPLETED')
            self.assertFalse(self.http(service, 'status')[1]['managed_runtime_loaded'])
            self.assertEqual(len((self.project / 'calls.jsonl').read_text().splitlines()), 2)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                out, err = process.communicate(timeout=7)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
                raise
        self.assertEqual(process.returncode, 0, err)
        self.assertNotIn('UNEXPECTED_MANAGED_IMPORT', err)
        self.assertNotIn('Traceback', err)

    def test_parallel_explicit_requests_initialize_one_shared_runtime(self):
        instance = self.stub_runtime()
        def create():
            time.sleep(.05)
            return instance
        factory = Mock(side_effect=create)
        with self.server(factory) as service, patch('subprocess.Popen') as spawn:
            self.assertFalse(self.http(service, 'status')[1]['managed_runtime_loaded'])
            factory.assert_not_called()
            with ThreadPoolExecutor(6) as pool:
                replies = list(pool.map(lambda _: self.http(service, 'cli-providers'), range(6)))
            self.assertEqual([reply[0] for reply in replies], [200] * 6)
            factory.assert_called_once_with()
            self.assertTrue(self.http(service, 'status')[1]['managed_runtime_loaded'])
            self.assertFalse(self.http(service, 'status')[1]['provider_available'])
            self.assertEqual(instance.agents.get(self.workspace, self.agent['id']), self.agent)
            spawn.assert_not_called()

    def test_slow_compatibility_initialization_does_not_block_core_reads_or_compile(self):
        started, release = Event(), Event()
        instance = self.stub_runtime()
        def create():
            started.set()
            if not release.wait(10):
                raise TimeoutError('test did not release the compatibility initializer')
            return instance
        with self.server(create) as service, ThreadPoolExecutor(1) as pool:
            request = pool.submit(self.http, service, 'cli-providers')
            try:
                self.assertTrue(started.wait(5))
                self.assertFalse(self.http(service, 'status')[1]['managed_runtime_loaded'])
                self.assertEqual(self.http(service, 'agents?workspace=' + self.workspace)[0], 200)
                self.assertEqual(self.http(service, 'drafts/export-python', self.request)[0], 200)
                self.assertFalse(request.done())
            finally:
                release.set()
            self.assertEqual(request.result(timeout=5)[0], 200)

    def test_compatibility_failure_does_not_break_native_execution_or_auto_retry(self):
        data, bin_dir = self.native_fixture()
        factory = Mock(side_effect=OSError('synthetic optional initialization failure'))
        with self.server(factory) as service, patch.dict(os.environ, PATH=str(bin_dir)):
            self.assertEqual(self.http(service, 'cli-providers')[0], 503)
            for _ in range(2):
                self.assertFalse(self.http(service, 'status')[1]['managed_runtime_loaded'])
                self.assertEqual(self.http(service, 'runs?workspace=' + self.workspace), (200, []))
            code, started = self.http(service, 'launch', data)
            self.assertEqual(code, 200, started)
            route = f"run?workspace={self.workspace}&id={started['run']['id']}"
            self.wait_for_state(service, route, lambda row: row['state'] == 'COMPLETED')
            factory.assert_called_once_with()
            self.assertEqual(len((self.project / 'calls.jsonl').read_text().splitlines()), 2)

    def test_old_history_in_another_workspace_does_not_load_compatibility(self):
        other = self.project.parent / 'Other'
        other.mkdir()
        workspace = self.store.open_workspace(str(other))['id']
        self.store.db.put('run', 'a' * 32, {'id': 'a' * 32, 'workspace': workspace})
        instance = self.stub_runtime()
        instance.runs = Mock(return_value=[{'id': 'a' * 32, 'workspace': workspace, 'state': 'UNKNOWN'}])
        factory = Mock(return_value=instance)
        with self.server(factory) as service:
            self.assertEqual(self.http(service, 'runs?workspace=' + self.workspace), (200, []))
            factory.assert_not_called()
            self.assertEqual(self.http(service, 'runs?workspace=' + workspace)[1][0]['id'], 'a' * 32)
            factory.assert_called_once_with()
            instance.runs.assert_called_once_with(workspace)

    def test_unauthenticated_or_invalid_native_requests_do_not_activate_legacy(self):
        factory = Mock(side_effect=AssertionError('must not initialize'))
        with self.server(factory) as service:
            self.assertEqual(self.http(service, 'cli-providers', token='wrong')[0], 401)
            self.assertEqual(self.http(service, 'launch', {'execution': 'unknown'})[0], 400)
            self.assertEqual(self.http(service, 'check', dict(workspace=self.workspace,
                document=self.document, execution='unknown'))[0], 400)
            self.assertEqual(self.http(service, 'agents/save', dict(workspace=self.workspace, id=None,
                expected=None, config=dict(self.agent['config'], execution='unknown'), session_revision=None))[0], 400)
            factory.assert_not_called()

    def test_lazy_legacy_entry_keeps_existing_session_authority_checks(self):
        from v2.runtime import V2Runtime
        from test_v2_agent_records import configuration
        def create():
            with patch('v2.runtime.CodexProvider', side_effect=Blocked('fixture missing CLI')):
                return V2Runtime(self.store)
        factory = Mock(side_effect=create)
        config = configuration(self.project, session={'kind': 'existing', 'handle': 'v1.' + 'a' * 64})
        with self.server(factory) as service:
            result = self.http(service, 'agents/save', dict(workspace=self.workspace, id=None,
                expected=None, config=config, session_revision=None))
            self.assertEqual(result[0], 400, result)
            factory.assert_called_once_with()
            self.assertEqual(self.http(service, 'agents?workspace=' + self.workspace)[1]['agents'], [self.agent])
            self.assertEqual(self.store.db.list('run'), [])

    def test_assistant_and_git_entries_keep_the_shared_managed_runtime(self):
        instance = self.stub_runtime()
        factory = Mock(return_value=instance)
        with self.server(factory) as service, patch('v2.assistant.Assistant') as assistant, \
                patch('v2.git_platform.GitPlatform') as platform:
            assistant.return_value.settings.return_value = {'configured': False}
            platform.return_value.read.return_value = {'bound': False}
            self.assertEqual(self.http(service, 'assistant/settings?workspace=' + self.workspace),
                             (200, {'configured': False}))
            self.assertEqual(self.http(service, 'git-platform/repository?workspace=' + self.workspace),
                             (200, {'bound': False}))
            factory.assert_called_once_with()
            assistant.assert_called_once_with(self.store, None, instance)
            assistant.return_value.settings.assert_called_once_with(workspace=self.workspace)
            platform.assert_called_once_with(self.store, instance)
            platform.return_value.read.assert_called_once_with('repository', workspace=self.workspace)

    def test_native_and_managed_history_coexist_without_reclassifying_records(self):
        data, bin_dir = self.native_fixture()
        old = dict(id='a' * 32, workspace=self.workspace, name='Existing managed run', state='UNKNOWN')
        self.store.db.put('run', old['id'], old)
        instance = self.stub_runtime()
        instance.runs = Mock(return_value=[old])
        instance.snapshot = Mock(return_value=dict(run=old, state='RECOVERY_REQUIRED'))
        factory = Mock(return_value=instance)
        with self.server(factory) as service, patch.dict(os.environ, PATH=str(bin_dir)):
            self.assertEqual(self.http(service, 'runs?workspace=' + self.workspace), (200, [old]))
            code, started = self.http(service, 'launch', data)
            self.assertEqual(code, 200, started)
            run_id = started['run']['id']
            route = f'run?workspace={self.workspace}&id={run_id}'
            self.wait_for_state(service, route, lambda row: row['state'] == 'COMPLETED')
            code, rows = self.http(service, 'runs?workspace=' + self.workspace)
            self.assertEqual(code, 200)
            self.assertEqual({row['id'] for row in rows}, {old['id'], run_id})
            self.assertEqual(self.http(service, f"run?workspace={self.workspace}&id={old['id']}")[1],
                             dict(run=old, state='RECOVERY_REQUIRED'))
            instance.snapshot.assert_called_once_with(self.workspace, old['id'])
            self.assertEqual(self.store.db.get('run', old['id']), old)
            self.assertEqual(self.store.db.get('script-run', run_id)['run']['execution'], 'native-cli')
            self.assertEqual(len((self.project / 'calls.jsonl').read_text().splitlines()), 2)
            factory.assert_called_once_with()

    def test_explicit_compatibility_registrations_keep_startup_validation(self):
        args = ['--state-dir', str(self.store.state), '--allow-root', str(self.project.parent), '--no-browser']
        for option in ('--native-agent-profile', '--native-agent-home', '--git-delivery-target'):
            with self.subTest(option=option), patch('v2.runtime.V2Runtime',
                    side_effect=Blocked('fixture compatibility validation')) as constructor, \
                    patch.object(devflower_v2, 'create_server') as serve, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(devflower_v2.main(args + [option, 'fixture']), 1)
                constructor.assert_called_once()
                serve.assert_not_called()
                self.assertIn('fixture compatibility validation', error.getvalue())


class DeferredManagedHTTPTests(unittest.TestCase):
    """Reuse the original HTTP assertions with the real Runtime loaded on demand."""
    setUp = legacy_http.ExecutionHTTPTests.setUp
    request = legacy_http.ExecutionHTTPTests.request
    payload = legacy_http.ExecutionHTTPTests.payload
    test_http_concurrent_launch_exactly_one_durable_run_and_spawn = (
        legacy_http.ExecutionHTTPTests.test_http_concurrent_launch_exactly_one_durable_run_and_spawn)
    test_http_frozen_run_ownership_and_saved_edit_separate = (
        legacy_http.ExecutionHTTPTests.test_http_frozen_run_ownership_and_saved_edit_separate)
    test_http_worker_dead_projection_agrees_list_detail = (
        legacy_http.ExecutionHTTPTests.test_http_worker_dead_projection_agrees_list_detail)

    def start_server(self, runtime=None):
        factory = Mock(return_value=runtime or self.rt)
        server, self.token = create_server(self.store, runtime_factory=factory)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(factory.assert_called_once_with)
        self.url = 'http://127.0.0.1:' + str(server.server_port) + '/api/v2/'
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
