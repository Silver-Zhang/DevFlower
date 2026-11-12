"""The exported script owns monitoring/cancellation; fake CLIs, real processes."""
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from test_native_workflow_export import export, graph, FAKE_CLI


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux directory-lock contract')
class ScriptMonitorTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='devflower-script-monitor-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('codex', 'claude'):
            cli = self.bin / name
            cli.write_text('#!' + sys.executable + '\n' + FAKE_CLI, encoding='utf-8')
            cli.chmod(0o700)
        self.calls = self.root / 'calls.jsonl'
        self.env = dict(os.environ, PATH=str(self.bin), FAKE_CALLS=str(self.calls))
        filename, source = export.compile_workflow(graph(), workspace=self.project, native_config=True)
        self.script = self.root / filename
        self.script.write_text(source, encoding='utf-8')
        self.run_dir = self.root / 'run'
        self.child = None
        self.addCleanup(self.cleanup_process)

    def cleanup_process(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
            try:
                self.child.communicate(timeout=7)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.communicate(timeout=5)
        # Only this test's known CLI groups; needed for the intentional SIGKILL case.
        events = self.run_dir / 'events.jsonl'
        if self.child is not None and events.is_file():
            for line in events.read_text().splitlines():
                try:
                    row = json.loads(line)
                    if row.get('kind') == 'PROCESS_STARTED':
                        os.killpg(row['pid'], signal.SIGKILL)
                except (ValueError, ProcessLookupError):
                    pass

    def command(self, *args):
        return [sys.executable, '-I', '-B', str(self.script), '--run-dir', str(self.run_dir), *args]

    def execute(self, **environment):
        return subprocess.run(self.command('--workspace', str(self.project)),
                              env=dict(self.env, **environment), capture_output=True, text=True, timeout=10)

    def observe(self, *args, success=True):
        # Monitoring must work without a repository, Python imports or installed CLIs.
        result = subprocess.run(self.command(*args), cwd=self.root,
                                env=dict(os.environ, PATH='', PYTHONPATH=''),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        return json.loads(result.stdout) if success else result

    def start_slow(self):
        self.child = subprocess.Popen(self.command('--workspace', str(self.project)),
                                      env=dict(self.env, SLOW_PROVIDER='codex'),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = self.run_dir / 'events.jsonl'
            if self.calls.exists() and events.exists() and 'PROCESS_STARTED' in events.read_text():
                return
            if self.child.poll() is not None:
                self.fail(self.child.communicate())
            time.sleep(.01)
        self.fail('Fake CLI did not start')

    def bytes_snapshot(self):
        return {str(p.relative_to(self.run_dir)): p.read_bytes()
                for p in self.run_dir.rglob('*') if p.is_file()}

    def test_same_name_different_workflows_have_different_artifacts(self):
        first = export.compile_workflow(graph(), native_config=True)
        changed = graph()
        changed['nodes'][0]['round_prompt'] = 'A different workflow with the same display name'
        second = export.compile_workflow(changed, native_config=True)
        self.assertNotEqual(first[0], second[0])
        self.assertIn(sha256(first[1].encode()).hexdigest()[:8], first[0])
        self.assertEqual(first, export.compile_workflow(graph(), native_config=True))

    def test_status_and_cancel_of_completed_run_are_read_only(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.bytes_snapshot()
        for action in ('--status', '--cancel', '--status'):
            status = self.observe(action)
            self.assertEqual(status['status'], 'COMPLETED')
            self.assertFalse(status['active'])
            self.assertFalse(status['automatic_retry'])
            self.assertEqual(status['completed_nodes'], ['Developer', 'Reviewer'])
            self.assertEqual(status['script_sha256'], sha256(self.script.read_bytes()).hexdigest())
        self.assertEqual(before, self.bytes_snapshot())
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_inherited_setgid_does_not_make_private_logs_unusable(self):
        self.root.chmod(0o2700)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.observe('--status')['status'], 'COMPLETED')

    def test_monitor_missing_run_never_creates_files_or_calls(self):
        self.observe('--status', success=False)
        self.observe('--cancel', success=False)
        self.assertFalse(self.run_dir.exists())
        self.assertFalse(self.calls.exists())

    def test_monitor_requires_run_directory_and_refuses_execution_options(self):
        result = subprocess.run([sys.executable, str(self.script), '--status'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        for args in (('--status', '--task', 'do not run'), ('--cancel', '--dry-run'),
                     ('--status', '--workspace', str(self.project))):
            self.observe(*args, success=False)
        self.assertFalse(self.calls.exists())

    def test_cancel_is_cooperative_idempotent_and_stops_downstream(self):
        self.start_slow()
        self.assertEqual(self.observe('--status')['status'], 'RUNNING')
        first = self.observe('--cancel')
        self.assertIn(first['status'], ('CANCEL_REQUESTED', 'CANCELLED'))
        self.observe('--cancel')
        _, stderr = self.child.communicate(timeout=7)
        self.assertEqual(self.child.returncode, 128 + signal.SIGTERM, stderr)
        self.assertEqual(self.observe('--status')['status'], 'CANCELLED')
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertFalse((self.run_dir / 'Reviewer').exists())
        self.assertFalse((self.run_dir / 'result.txt').exists())

    def test_dead_owner_is_unknown_and_cancel_does_not_claim_success(self):
        self.start_slow()
        self.child.kill()
        self.child.communicate(timeout=5)
        before = self.bytes_snapshot()
        for action in ('--status', '--cancel'):
            status = self.observe(action)
            self.assertEqual(status['status'], 'RECOVERY_REQUIRED')
            self.assertFalse(status['active'])
        self.assertEqual(before, self.bytes_snapshot())
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_failure_is_not_completion_and_status_does_not_retry(self):
        self.assertNotEqual(self.execute(FAIL_PROVIDER='codex').returncode, 0)
        for _ in range(2):
            self.assertEqual(self.observe('--status')['status'], 'STOPPED')
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_modified_script_cannot_observe_or_cancel_another_artifact(self):
        self.start_slow()
        self.script.write_text(self.script.read_text() + '\n# edited after launch\n')
        self.observe('--cancel', success=False)
        self.assertFalse((self.run_dir / 'cancel.request').exists())
        self.assertIsNone(self.child.poll())

    def test_completed_result_and_node_answers_are_verified(self):
        self.assertEqual(self.execute().returncode, 0)
        for relative in ('result.txt', 'Developer/answer.txt', 'Reviewer/answer.txt'):
            path = self.run_dir / relative
            original = path.read_bytes()
            path.write_text('modified evidence')
            with self.subTest(relative=relative):
                self.assertEqual(self.observe('--status')['status'], 'RECOVERY_REQUIRED')
            path.write_bytes(original)
        self.assertEqual(self.observe('--status')['status'], 'COMPLETED')

    def test_incomplete_or_malformed_journal_is_never_success(self):
        self.assertEqual(self.execute().returncode, 0)
        path = self.run_dir / 'events.jsonl'
        raw = path.read_bytes()
        for changed in (raw[:-1], raw + b'{"kind":', b'not json\n', raw + raw.splitlines()[-1] + b'\n'):
            path.write_bytes(changed)
            self.assertEqual(self.observe('--status')['status'], 'RECOVERY_REQUIRED')
        path.write_bytes(raw)

    def test_live_partial_tail_is_only_a_running_observation(self):
        self.start_slow()
        path = self.run_dir / 'events.jsonl'
        original = path.read_bytes()
        path.write_bytes(original + b'{"kind":')
        try:
            self.assertEqual(self.observe('--status')['status'], 'RUNNING')
        finally:
            path.write_bytes(original)


    def test_corrupt_prior_answer_does_not_disable_cancellation(self):
        self.child = subprocess.Popen(self.command('--workspace', str(self.project)),
                                      env=dict(self.env, SLOW_PROVIDER='claude'),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.calls.exists() and len(self.calls.read_text().splitlines()) == 2:
                break
            time.sleep(.01)
        else:
            self.fail('Reviewer did not start')
        (self.run_dir / 'Developer' / 'answer.txt').write_text('modified evidence')
        status = self.observe('--cancel')
        self.assertTrue(status['cancel_requested'])
        self.assertEqual(status['status'], 'RECOVERY_REQUIRED')
        self.child.communicate(timeout=7)
        self.assertEqual(self.child.returncode, 128 + signal.SIGTERM)
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_invalid_manifest_fails_without_traceback_or_replay(self):
        self.assertEqual(self.execute().returncode, 0)
        (self.run_dir / 'workflow.json').write_text('{')
        failure = self.observe('--status', success=False)
        self.assertNotIn('Traceback', failure.stderr)
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_result_file_without_terminal_evidence_is_not_success(self):
        self.assertEqual(self.execute().returncode, 0)
        events = self.run_dir / 'events.jsonl'
        events.write_bytes(b'\n'.join(events.read_bytes().splitlines()[:-1]) + b'\n')
        self.assertTrue((self.run_dir / 'result.txt').is_file())
        self.assertEqual(self.observe('--status')['status'], 'RECOVERY_REQUIRED')

    def test_monitor_refuses_symlinked_result_and_nonregular_journal(self):
        self.assertEqual(self.execute().returncode, 0)
        result = self.run_dir / 'result.txt'
        result.unlink()
        result.symlink_to(self.calls)
        self.assertEqual(self.observe('--status')['status'], 'RECOVERY_REQUIRED')
        events = self.run_dir / 'events.jsonl'
        events.unlink()
        os.mkfifo(events)
        self.assertEqual(self.observe('--status')['status'], 'RECOVERY_REQUIRED')


if __name__ == '__main__':
    unittest.main()
