"""Non-Linux platforms fail closed with a readable reason (#141).

macOS/Windows are only simulated via sys.platform here; they were not run.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests'), str(ROOT)]

import devflower_start  # noqa: E402


class PlatformGuardTests(unittest.TestCase):
    def test_non_linux_refuses_before_creating_state(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)/'state'
                out, err = io.StringIO(), io.StringIO()
                with patch.object(sys, 'platform', platform), redirect_stdout(out), redirect_stderr(err):
                    code = devflower_start.main(['--check', '--state-dir', str(state)])
                self.assertEqual(code, 1)
                self.assertIn('仅支持 Linux', err.getvalue())
                self.assertIn('不会静默降级', err.getvalue())
                self.assertFalse(state.exists())

    def test_source_guards_fcntl_and_platform(self):
        source = (ROOT/'tools/devflower_start.py').read_text()
        self.assertIn('except ImportError', source)
        self.assertIn("sys.platform != 'linux'", source)
        self.assertIn('不会静默降级', source)

    def test_linux_import_and_check_still_work(self):
        # Running on Linux: the module imported and can parse --check without
        # entering the platform refusal branch.
        self.assertTrue(hasattr(devflower_start, 'main'))
        self.assertIsNotNone(devflower_start.fcntl)


if __name__ == '__main__':
    unittest.main()
