"""Non-Linux platform policy (#141).

macOS is simulated via sys.platform: this suite runs on Linux and does not
claim that the macOS UI was executed on real hardware. Windows stays refused:
the state directory is pinned with POSIX dir_fd/O_NOFOLLOW/O_DIRECTORY and
Linux /proc, so no Windows lock implementation is shipped or tested.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests'), str(ROOT)]

import devflower_start  # noqa: E402
from orchestration.common import Blocked  # noqa: E402
from test_workbench_runtime import fixture  # noqa: E402


class PlatformPolicyTests(unittest.TestCase):
    def test_platform_modes(self):
        with patch.object(devflower_start.os, 'name', 'posix'), \
             patch.object(devflower_start.sys, 'platform', 'linux'):
            self.assertEqual(devflower_start.platform_mode(), 'linux')
        with patch.object(devflower_start.os, 'name', 'posix'), \
             patch.object(devflower_start.sys, 'platform', 'darwin'):
            self.assertEqual(devflower_start.platform_mode(), 'readonly')
        with patch.object(devflower_start.os, 'name', 'nt'):
            self.assertEqual(devflower_start.platform_mode(), 'unsupported')

    def test_windows_refuses_before_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/'state'
            out, err = io.StringIO(), io.StringIO()
            with patch.object(devflower_start.os, 'name', 'nt'), \
                 redirect_stdout(out), redirect_stderr(err):
                code = devflower_start.main(['--check', '--state-dir', str(state)])
            self.assertEqual(code, 1)
            self.assertIn('Windows 尚未支持', err.getvalue())
            self.assertFalse(state.exists())

    def test_readonly_preflight_does_not_require_bwrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = devflower_start.system_preflight(Path(tmp), isolated=False)
        self.assertTrue(result)
        self.assertNotIn('/usr/bin/bwrap', result)


class RuntimeExecutionBlockTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.rt.execution_blocked = True
        self.rt.execution_reason = 'SYNTHETIC 只读平台'

    def test_open_workspace_is_read_only(self):
        row = self.rt.open_workspace(str(self.base.source))
        self.assertFalse(row['execution_allowed'])
        self.assertEqual(row['execution_reason'], 'SYNTHETIC 只读平台')

    def test_node_binding_refuses_execution(self):
        with self.assertRaisesRegex(Blocked, 'SYNTHETIC 只读平台'):
            self.rt.node_binding(self.base.project['id'], dict(
                profile='default-codex', provider='codex', model='fixture', effort='low', mode='Ask'))


if __name__ == '__main__':
    unittest.main()
