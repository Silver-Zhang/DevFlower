"""Rehearse the local acceptance helper with fake CLIs, never model inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import devflower_script_acceptance as acceptance

FAKE = r'''import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
prompt = sys.stdin.read()
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(name + '\n')
token = ('a' if pathlib.Path.cwd().name == 'project-a' else 'b') * 32
if name == 'codex':
    pathlib.Path('addition.py').write_text('def add(a, b):\n    return a + b\n')
    pathlib.Path('delivery.txt').write_text(token + '\n')
    answer = 'DELIVERY_TOKEN=' + token
    pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(answer)
else:
    assert 'DELIVERY_TOKEN=' + token in prompt
    answer = 'REVIEW_OK ' + token
    pathlib.Path('review.txt').write_text(answer + '\n')
    print(json.dumps({'type':'result', 'is_error':False, 'result':answer}))
'''


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='devflower-acceptance-test-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.bundle = self.root / 'bundle'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls'
        for name in ('codex', 'claude'):
            path = self.bin / name
            path.write_text('#!' + sys.executable + '\n' + FAKE)
            path.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin), CALLS=str(self.calls), PYTHONPATH='')
        self.script = acceptance.prepare(self.bundle)

    def complete(self):
        for name in ('a', 'b'):
            result = subprocess.run([sys.executable, '-I', '-B', str(self.script),
                                     '--workspace', str(self.bundle / ('project-' + name)),
                                     '--run-dir', str(self.bundle / ('run-' + name))],
                                    cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_has_no_agent_calls_and_never_overwrites(self):
        self.assertFalse(self.calls.exists())
        self.assertTrue((self.bundle / 'project-a' / '.git').is_dir())
        before = self.script.read_bytes()
        with self.assertRaises(FileExistsError):
            acceptance.prepare(self.bundle)
        self.assertEqual(self.script.read_bytes(), before)

    def test_two_independent_runs_and_read_only_verification(self):
        self.complete()
        before = self.calls.read_bytes()
        report = acceptance.verify(self.bundle)
        self.assertEqual(report['status'], 'SCENARIO_CHECKS_PASSED')
        self.assertEqual(report['gui_acceptance'], 'NOT_RUN')
        self.assertEqual(len(report['runs']), 2)
        self.assertEqual(self.calls.read_bytes(), before)
        self.assertEqual(before.splitlines(), [b'codex', b'claude', b'codex', b'claude'])

    def test_agent_verdict_cannot_replace_independent_arithmetic_check(self):
        self.complete()
        (self.bundle / 'project-b' / 'addition.py').write_text('def add(a, b):\n    return 0\n')
        with self.assertRaises(subprocess.CalledProcessError):
            acceptance.verify(self.bundle)
        self.assertEqual(len(self.calls.read_text().splitlines()), 4)

    def test_missing_run_does_not_launch_or_retry(self):
        with self.assertRaises(subprocess.CalledProcessError):
            acceptance.verify(self.bundle)
        self.assertFalse(self.calls.exists())

    def test_modified_script_is_not_executed_by_verifier(self):
        self.script.write_text('raise SystemExit("must not execute edited script")')
        with self.assertRaisesRegex(ValueError, 'script has changed'):
            acceptance.verify(self.bundle)
        self.assertFalse(self.calls.exists())

    def test_modified_handoff_is_not_accepted(self):
        self.complete()
        prompt = self.bundle / 'run-a' / 'Reviewer' / 'prompt.txt'
        prompt.write_text(prompt.read_text().replace('DELIVERY_TOKEN=' + 'a' * 32, 'FORGED'))
        with self.assertRaisesRegex(ValueError, 'handoff'):
            acceptance.verify(self.bundle)


if __name__ == '__main__':
    unittest.main()
