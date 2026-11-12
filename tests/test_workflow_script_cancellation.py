"""Cancellation of real local process groups; fake CLIs, no model requests."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = load('devflower_export')
runtime = load('workflow_script_runtime')

FAKE_CLI = r'''import json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(name + '\n')
if name != 'codex':
    sys.exit('Downstream must not run')
child_code = ''' + repr("""import os, pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(60)
""") + r'''
child = subprocess.Popen([sys.executable, '-c', child_code, os.environ['CHILD_READY']])
while not pathlib.Path(os.environ['CHILD_READY']).exists():
    time.sleep(.01)
pathlib.Path(os.environ['PIDS']).write_text(json.dumps({'leader':os.getpid(),'child':child.pid}))
if os.environ.get('EXIT_LEADER'):
    sys.exit(7)
time.sleep(60)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'POSIX/Linux process-group contract')
class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='devflower-cancellation-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('codex', 'claude'):
            cli = self.bin / name
            cli.write_text('#!' + sys.executable + '\n' + FAKE_CLI, encoding='utf-8')
            cli.chmod(0o700)
        self.run_dir = self.root / 'run'
        self.pids = self.root / 'pids.json'
        self.env = dict(os.environ, PATH=str(self.bin), CALLS=str(self.root / 'calls'),
                        PIDS=str(self.pids), CHILD_READY=str(self.root / 'child-ready'))
        self.wrapper = None
        self.addCleanup(self.stop_owned_processes)

    def stop_owned_processes(self):
        if self.pids.exists():
            try:
                os.killpg(json.loads(self.pids.read_text())['leader'], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.wrapper is not None:
            if self.wrapper.poll() is None:
                self.wrapper.kill()
            self.wrapper.communicate(timeout=5)

    def start(self, *, timeout=None, exit_leader=False, cancel_during_launch=False):
        graph = {'version': 1, 'name': 'Cancellation', 'nodes': [
            {'id': 'Developer', 'provider': 'codex', 'timeout': timeout},
            {'id': 'Reviewer', 'provider': 'claude'}],
            'edges': [{'id': 'handoff', 'source': 'Developer', 'target': 'Reviewer'}]}
        filename, source = export.compile_workflow(graph, workspace=self.project, native_config=True)
        if cancel_during_launch:
            # Deliver a real signal after fork/exec but before the caller receives
            # its Popen handle. Run this fault in the exported subprocess only.
            injection = '''
_real_popen = subprocess.Popen
def _cancel_before_handle_assignment(*args, **kwargs):
    process = _real_popen(*args, **kwargs)
    deadline = time.monotonic() + 5
    while not Path(os.environ['PIDS']).exists() and time.monotonic() < deadline:
        time.sleep(.01)
    os.kill(os.getpid(), signal.SIGTERM)
    return process
subprocess.Popen = _cancel_before_handle_assignment
'''
            source = source.replace("if __name__ == '__main__':", injection + "\nif __name__ == '__main__':")
        script = self.root / filename
        script.write_text(source, encoding='utf-8')
        self.wrapper = subprocess.Popen(
            [sys.executable, '-I', '-B', str(script), '--workspace', str(self.project),
             '--run-dir', str(self.run_dir)],
            env=dict(self.env, **({'EXIT_LEADER': '1'} if exit_leader else {})),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return json.loads(self.pids.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(.01)
        self.fail('Fake CLI did not start before deadline')

    @staticmethod
    def running(pid):
        try:
            # Orphans can remain as zombies until the container init reaps them.
            return Path('/proc', str(pid), 'stat').read_text().split()[2] != 'Z'
        except FileNotFoundError:
            return False

    def assert_stopped(self, pids, event, *, cancelled=False):
        stdout, stderr = self.wrapper.communicate(timeout=8)
        self.assertNotEqual(self.wrapper.returncode, 0, (stdout, stderr))
        deadline = time.monotonic() + 2
        while any(self.running(pid) for pid in pids.values()) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(any(self.running(pid) for pid in pids.values()), pids)
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['codex'])
        self.assertFalse((self.run_dir / 'Reviewer').exists())
        self.assertFalse((self.run_dir / 'result.txt').exists())
        events = [json.loads(line) for line in (self.run_dir / 'events.jsonl').read_text().splitlines()]
        self.assertIn(event, [row['kind'] for row in events])
        self.assertEqual(events[-1]['kind'], 'RUN_STOPPED')
        self.assertIs(events[-1]['automatic_retry'], False)
        self.assertEqual(events[-1]['status'], 'CANCELLED' if cancelled else 'STOPPED')

    def test_sigterm_stops_cli_and_resistant_child_without_touching_other_session(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                     start_new_session=True)
        self.addCleanup(unrelated.wait, 5)
        self.addCleanup(unrelated.terminate)
        pids = self.start()
        self.wrapper.send_signal(signal.SIGTERM)
        self.assert_stopped(pids, 'CANCELLED', cancelled=True)
        self.assertEqual(self.wrapper.returncode, 128 + signal.SIGTERM)
        self.assertIsNone(unrelated.poll())

    def test_sigint_stops_cli_and_resistant_child(self):
        pids = self.start()
        self.wrapper.send_signal(signal.SIGINT)
        self.assert_stopped(pids, 'CANCELLED', cancelled=True)
        self.assertEqual(self.wrapper.returncode, 128 + signal.SIGINT)

    def test_signal_during_launch_does_not_lose_the_process_handle(self):
        pids = self.start(cancel_during_launch=True)
        self.assert_stopped(pids, 'CANCELLED', cancelled=True)

    def test_timeout_stops_group_even_when_leader_exits_on_term(self):
        pids = self.start(timeout=1)
        self.assert_stopped(pids, 'UNKNOWN')

    def test_failed_leader_does_not_leave_its_tool_running(self):
        pids = self.start(exit_leader=True)
        self.assert_stopped(pids, 'CLI_FAILED')

    def test_script_main_restores_signal_handlers_on_success_and_failure(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        manifest = {'name': 'Restoration', 'agents': [], 'input': ''}
        for fail in (False, True):
            def workflow(context, task):
                self.assertNotEqual(signal.getsignal(signal.SIGTERM), previous[signal.SIGTERM])
                if fail:
                    raise runtime.WorkflowError('synthetic failure')
                return {'answer': 'done'}
            args = ['workflow.py', '--workspace', str(self.project), '--run-dir', str(self.root / str(fail))]
            with self.subTest(fail=fail), patch.object(sys, 'argv', args):
                self.assertEqual(runtime.script_main(manifest, workflow), 1 if fail else 0)
                self.assertEqual({sig: signal.getsignal(sig) for sig in previous}, previous)

    def test_cancel_while_writing_result_is_not_published_as_completed(self):
        manifest = {'name': 'Finalization cancellation', 'agents': [], 'input': ''}
        original_write = runtime._write

        def cancel_after_write(path, value):
            original_write(path, value)
            if path.name == 'result.txt':
                os.kill(os.getpid(), signal.SIGTERM)

        args = ['workflow.py', '--workspace', str(self.project), '--run-dir', str(self.run_dir)]
        with patch.object(sys, 'argv', args), patch.object(runtime, '_write', cancel_after_write):
            status = runtime.script_main(manifest, lambda *_: {'answer': 'preserved evidence'})
        self.assertEqual(status, 128 + signal.SIGTERM)
        events = [json.loads(line) for line in (self.run_dir / 'events.jsonl').read_text().splitlines()]
        self.assertNotIn('RUN_COMPLETED', [row['kind'] for row in events])
        self.assertEqual((events[-1]['kind'], events[-1]['status']), ('RUN_STOPPED', 'CANCELLED'))
        self.assertEqual((self.run_dir / 'result.txt').read_text(), 'preserved evidence')


if __name__ == '__main__':
    unittest.main()
