"""Real local persistence/privacy tests; no paid model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).parent)]
from orchestration.common import Blocked, canonical
from studio.assistant_settings import DEFAULT_CONNECTION, settings_root
from studio.devflower_agent import AgentConnection
from studio.server import Conflict
from studio.session_sources import Refused


class PersistentAssistantTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.settings = self.root/'private-settings'
        self.connection = self.config('runtime-A')

    def config(self, name):
        storage = self.root/name; storage.mkdir(exist_ok=True)
        return AgentConnection(SimpleNamespace(storage=storage, assistant_settings=self.settings,
                                               operator={'profiles': {}}, check_operator=lambda: None))

    def save(self, config=None, **changes):
        return (config or self.connection).save(dict({'expected':None, **DEFAULT_CONNECTION, 'api_key':'SYNTHETIC_PRIVATE_KEY', **changes}))

    def test_default_is_deepseek_without_a_native_cli(self):
        value = self.connection.public()
        self.assertFalse(value['configured']); self.assertEqual(value['model'], 'deepseek-flash')
        self.assertEqual(value['protocol'], 'openai'); self.assertEqual(value['settings_scope'], 'host')
        self.assertFalse(value['coding_cli_required']); self.assertFalse(self.settings.exists())

    def test_default_settings_location_and_explicit_host_override(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'xdg'), 'DEVFLOWER_SETTINGS_HOME': ''}):
            self.assertEqual(settings_root(), self.root/'xdg/devflower')
        with patch.dict(os.environ, {'DEVFLOWER_SETTINGS_HOME': str(self.settings)}):
            self.assertEqual(settings_root(), self.settings)

    def test_saved_connection_survives_another_installation_and_process(self):
        public = self.save(); second = self.config('runtime-B')
        self.assertEqual(second.public(), public)
        script = "from types import SimpleNamespace; from pathlib import Path; from studio.devflower_agent import AgentConnection; import sys,json; print(json.dumps(AgentConnection(SimpleNamespace(storage=Path(sys.argv[1]),operator={'profiles':{}},check_operator=lambda:None)).public()))"
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'tools'),
                   DEVFLOWER_SETTINGS_HOME=str(self.settings), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-c', script, str(second.rt.storage)], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), public)
        self.assertNotIn('SYNTHETIC_PRIVATE_KEY', result.stdout)
        self.assertFalse((second.rt.storage/self.connection.path.name).exists())
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.connection.path.stat().st_mode & 0o777, 0o600)

    def test_two_instances_reject_stale_connection_update(self):
        public = self.save(); second = self.config('runtime-B')
        new = self.connection.save(dict({'expected':public['fingerprint'], **DEFAULT_CONNECTION, 'api_key':'', 'model':'chosen-model'}))
        with self.assertRaises(Conflict):
            second.save(dict(expected=public['fingerprint'], **DEFAULT_CONNECTION, api_key=''))
        self.assertEqual(second.public(), new)
        self.assertEqual(second.read()['api_key'], 'SYNTHETIC_PRIVATE_KEY')

    def test_legacy_explicit_connection_migrates_once_and_remains_untouched(self):
        data = dict(version=1, **DEFAULT_CONNECTION, api_key='LEGACY_PRIVATE_KEY')
        self.connection.legacy.write_bytes(canonical(data)); self.connection.legacy.chmod(0o600)
        original = self.connection.legacy.read_bytes()
        self.assertTrue(self.connection.public()['configured'])
        self.assertEqual(self.config('runtime-B').read()['api_key'], 'LEGACY_PRIVATE_KEY')
        self.assertEqual(self.connection.legacy.read_bytes(), original)
        # An older installation cannot overwrite the newer global choice.
        self.connection.legacy.write_bytes(canonical(dict(data, model='stale-model')))
        self.assertEqual(self.connection.read()['model'], DEFAULT_CONNECTION['model'])

    def test_symlink_ancestor_is_not_followed(self):
        target = self.root/'elsewhere'; target.mkdir(mode=0o700)
        self.settings.symlink_to(target, target_is_directory=True)
        with self.assertRaises((OSError, Refused)): self.save()
        self.assertEqual(list(target.iterdir()), [])

    def test_symlink_and_hardlinked_config_refused_without_modifying_target(self):
        self.save(); original = self.connection.path.read_bytes()
        target = self.root/'target'; self.connection.path.rename(target)
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.connection.path.symlink_to(target)
                else: os.link(target, self.connection.path)
                with self.assertRaises((Blocked, Refused, OSError)): self.save()
                self.assertEqual(target.read_bytes(), original)
                self.connection.path.unlink()

    def test_world_readable_settings_or_directory_fail_closed(self):
        self.save(); self.connection.path.chmod(0o644)
        with self.assertRaisesRegex(Blocked, '私有普通文件'): self.connection.read()
        self.connection.path.chmod(0o600); self.settings.chmod(0o755)
        with self.assertRaisesRegex(Blocked, '0700'): self.save()

    def test_lock_symlink_and_hardlink_never_truncate_target(self):
        self.save(); lock = self.settings/'.connection.lock'; lock.unlink()
        target = self.root/'target'; target.write_text('preserve'); target.chmod(0o600)
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink': lock.symlink_to(target)
                else: os.link(target, lock)
                with self.assertRaises((Blocked, OSError)): self.save()
                self.assertEqual(target.read_text(), 'preserve'); lock.unlink()

    def test_owned_workers_inherit_exact_settings_root(self):
        from studio.preference_baseline import worker_baseline
        from orchestration.common import clean_env
        env = clean_env()
        rt = SimpleNamespace(assistant_settings=self.settings, preference_baseline=None)
        with worker_baseline(rt, env):
            self.assertEqual(env['DEVFLOWER_SETTINGS_HOME'], str(self.settings))
            output = subprocess.check_output([sys.executable, '-B', '-c',
                "from studio.assistant_settings import settings_root; print(settings_root())"],
                env=dict(env, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'tools')), text=True)
        self.assertEqual(output.strip(), str(self.settings))

    def test_runtime_registers_private_settings_as_protected(self):
        from test_workbench_runtime import fixture
        base, rt = fixture(self)
        self.assertIn(rt.assistant_settings, rt.protected)


if __name__ == '__main__': unittest.main()
