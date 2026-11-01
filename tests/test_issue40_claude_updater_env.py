"""DevFlower-launched Claude processes opt out of CLI self-update (#40)."""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from orchestration.common import cli_probe_env
from studio.native_local import native_environment


class ClaudeUpdaterEnvTests(unittest.TestCase):
    def test_native_environment_disables_claude_self_update(self):
        env = native_environment({'provider': 'claude', 'config_root': '/synthetic/.claude'})
        self.assertEqual(env.get('DISABLE_AUTOUPDATER'), '1')
        self.assertEqual(env.get('DISABLE_UPDATES'), '1')

    def test_codex_environment_is_untouched(self):
        env = native_environment({'provider': 'codex', 'config_root': '/synthetic/.codex'})
        self.assertEqual(env, {'CODEX_HOME': '/synthetic/.codex'})

    def test_cli_probe_environment_disables_claude_self_update(self):
        env = cli_probe_env()
        self.assertEqual(env.get('DISABLE_AUTOUPDATER'), '1')
        self.assertEqual(env.get('DISABLE_UPDATES'), '1')
        self.assertNotIn('HOME', env)


if __name__ == '__main__':
    unittest.main()
