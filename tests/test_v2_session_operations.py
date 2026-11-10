"""Targeted V2 standalone effect counterexamples with no external inference."""
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.common import Blocked, digest
from orchestration.process import execute
from orchestration.store import process_identity
from studio.graph import Invalid
from studio.local_claude_adapter import LocalClaude
from studio.native_codex_image import sealed_executable
from studio.server import Conflict
from v2.claude_session_profile import discover_cli, environment_identity, startup_environment
from v2.native_history_config import HistoryConfig
from v2.native_sessions import NativeSessions
from v2.session_identity import SessionRefused, StorageRootIdentity
from v2.session_operations import KIND, OperationJournal, SessionOperations, load_write_profiles, operation_id
from v2.store import DraftStore
from v2.session_lease import codex_presentation_guard


class SessionOperationsTests(unittest.TestCase):
    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.base = Path(root.name)
        self.home = self.base / 'home'
        config = self.home / '.codex'
        (config / 'sessions').mkdir(parents=True)
        parent = self.base / 'projects'
        self.cwd = parent / 'test'
        self.cwd.mkdir(parents=True)
        self.store = DraftStore(self.base / 'state', [parent])
        self.workspace = self.store.open_workspace(str(self.cwd))['id']
        reader = NativeSessions(self.store, HistoryConfig(True, False, ()), b'q' * 32, home=self.home)
        codex = SimpleNamespace(home=str(self.home), config_root=str(config),
                               binding=lambda _, node: dict(node, executable='/usr/bin/true'),
                               require_account=lambda _: None)
        self.operations = SessionOperations(self.store, reader, codex, {'codex': True})
        self.intent = dict(workspace=self.workspace, intent_key='synthetic_session_effect', provider='codex',
                           model='fixture', effort='low', handle=None, revision=None, prompt='synthetic only')

    def test_unknown_spawn_callback_failure_has_no_replay(self):
        observed = []

        class Spawned:
            def __init__(self, *_):
                pass

            def start(self, _prompt, _cwd, _artifacts, _schema, journal, key):
                observed.append(key)
                journal.process_started = True
                raise OSError('synthetic post-spawn journal failure')

        key = operation_id(self.workspace, self.intent['intent_key'])
        with patch('v2.session_operations.LocalCodex', Spawned):
            self.assertEqual(self.operations.send(self.intent)['state'], 'RESERVED')
            deadline = time.monotonic() + 5
            while self.store.db.get(KIND, key)['state'] == 'RESERVED' and time.monotonic() < deadline:
                time.sleep(.01)
            result = self.operations.operation(self.workspace, self.intent['intent_key'])
            self.assertEqual(result['state'], 'RECOVERY_REQUIRED')
            self.assertEqual(self.operations.send(self.intent), result)
            with self.assertRaises(Conflict):
                self.operations.send(dict(self.intent, prompt='changed'))
        self.assertEqual(observed, [key])

    def test_replaced_same_path_cwd_rejected_before_input(self):
        pinned = StorageRootIdentity.capture(self.cwd)
        self.cwd.rename(self.cwd.with_name('original'))
        self.cwd.mkdir()
        journal = OperationJournal(self.operations, {'binding': {'cwd': str(self.cwd),
                                                                  'cwd_identity': pinned.fields()}}, lambda: None)
        process = subprocess.Popen(['/usr/bin/sleep', '15'], cwd=self.cwd, start_new_session=True)
        try:
            identity = process_identity(process.pid)
            with self.assertRaises(SessionRefused) as caught:
                journal._require_process_scope(identity)
            self.assertEqual(caught.exception.code, 'LOCATOR_REFUSED')
        finally:
            process.terminate()
            process.wait(timeout=3)

    def test_claude_managed_eligibility_tracks_latest_completed_exact_history(self):
        sid = str(uuid.uuid4())
        locator = SimpleNamespace(workspace_id=self.workspace, native_uuid=sid)
        binding = dict(provider='claude', project=self.workspace, cwd=str(self.cwd), home=str(self.home),
                       config_root=str(self.home / '.claude'), runtime_profile='default-claude', root_identity=[1, 2, 3],
                       cwd_identity=[4, 5, 6], cli_version='fixture', _executable_sha256='image',
                       direct_environment_sha256='profile', cli_environment_sha256='env')
        record = SimpleNamespace(ref=SimpleNamespace(relative=Path('project') / (sid + '.jsonl'), sha='original'))
        base = dict(workspace=self.workspace, provider='claude', observed_session=sid, target=sid,
                    state='COMPLETED', receipt={'session_id': sid}, relative=str(record.ref.relative),
                    native_after_sha256='original')
        def status(config=binding):
            with self.store.db.tx(read=True) as db:
                return self.operations.claude_history_state(config, locator, record, db)

        self.assertEqual(status(), 'EXTERNAL_SESSION')
        self.store.db.put(KIND, 'new', dict(base, handle=None, binding=dict(binding, new_session_id=sid)))
        self.assertEqual(status(), 'managed')
        self.assertEqual(status(dict(binding, cli_environment_sha256='other')), 'MANAGED_HISTORY_CHANGED')
        self.store.db.put(KIND, 'resume', dict(base, handle='opaque', binding=binding, native_after_sha256='latest'))
        self.assertEqual(status(), 'MANAGED_HISTORY_CHANGED')
        record.ref.sha = 'latest'
        self.assertEqual(status(), 'managed')
        self.store.db.put(KIND, 'unknown', dict(base, binding=binding, state='RECOVERY_REQUIRED'))
        self.assertEqual(status(), 'RECOVERY_REQUIRED')

    def test_native_group_guard_observes_missing_uuid_under_coordination(self):
        root = self.home / '.codex'
        locks = root / 'thread-writer-locks'
        locks.mkdir()
        coordination = locks / '.coordination.lock'
        coordination.touch()
        sid = str(uuid.uuid4())
        with coordination.open() as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(SessionRefused) as caught:
                with codex_presentation_guard(root, [sid]):
                    self.fail('the native coordination gate was already held')
            self.assertEqual(caught.exception.code, 'EXTERNAL_BUSY')
        with codex_presentation_guard(root, [sid]) as check:
            check()
        self.assertEqual(set(locks.iterdir()), {coordination})

    def test_claude_native_defaults_and_generic_legacy_relay(self):
        path = self.base / 'writer.json'
        path.write_text(json.dumps({'version': 1, 'claude': True}))
        path.chmod(0o600)
        self.assertEqual(load_write_profiles(path), {'claude': {}})
        legacy = {'model': 'fixture-model', 'effort': 'low',
                  'anthropic_base_url': 'https://relay.example.invalid/anthropic'}
        path.write_text(json.dumps({'version': 1, 'claude': legacy}))
        self.assertEqual(load_write_profiles(path), {'claude': legacy})
        path.write_text(json.dumps({'version': 1, 'claude': {**legacy, 'home': str(self.home), 'managed': True}}))
        with self.assertRaises(Invalid):
            load_write_profiles(path)

    def test_native_spawn_uses_sealed_bytes_after_path_and_inode_replacement(self):
        executable = self.base / 'operator-cli'
        approved = Path('/usr/bin/true').read_bytes()
        attacker = Path('/usr/bin/env').read_bytes()
        synthetic_secret = 'fixture-credential-must-not-reach-replacement'
        environment = {'ANTHROPIC_AUTH_TOKEN': synthetic_secret}

        for atomic in (True, False):
            with self.subTest(atomic=atomic):
                executable.write_bytes(approved)
                executable.chmod(0o700)
                old_inode = executable.stat().st_ino

                class SwapAfterApproval:
                    def intent(self, _run, kind, _data):
                        assert kind == 'PROCESS'
                        if atomic:
                            replacement = executable.with_name('replacement')
                            replacement.write_bytes(attacker)
                            replacement.chmod(0o700)
                            replacement.replace(executable)
                        else:
                            executable.write_bytes(attacker)
                        return 'fixture'

                    def finish(self, *_args, **_updates):
                        pass

                    def get(self, _run):
                        return {'data': {}}

                with sealed_executable(executable, 'devflower-test', expected_sha256=digest(approved)) as (fd, _):
                    process, out, err = execute([str(executable)], self.cwd, self.home, timeout=5,
                        isolate=False, backend='native-local', env_extra=environment, executable_fd=fd,
                        store=SwapAfterApproval())
                self.assertEqual(executable.stat().st_ino == old_inode, not atomic)
                self.assertEqual(process['exit'], 0)
                self.assertEqual((out, err), (b'', b''))
                process, out, _ = execute([str(executable)], self.cwd, self.home, timeout=5,
                    isolate=False, backend='native-local', env_extra=environment)
                self.assertEqual(process['exit'], 0)
                self.assertIn(synthetic_secret.encode(), out)

    def test_replacement_before_claude_snapshot_never_reaches_transport(self):
        executable = self.base / 'changed-cli'
        shutil.copy('/usr/bin/env', executable)
        binding = dict(executable=str(executable), home=str(self.home), config_root=str(self.home / '.claude'),
                       role='reviewer', project=self.workspace, chat_only=True, model=None, effort=None,
                       _executable_sha256=digest(Path('/usr/bin/true').read_bytes()))
        adapter = LocalClaude(binding, {'step_timeout_seconds': 5, 'max_log_bytes': 4096},
                              credential_env={'ANTHROPIC_AUTH_TOKEN': 'fixture-secret'})
        with patch('studio.local_claude_adapter.execute') as transport:
            with self.assertRaisesRegex(Blocked, '未启动'):
                adapter.start('synthetic only', self.cwd, self.base)
            transport.assert_not_called()

    def test_unverified_claude_cli_version_refuses_before_profile_use(self):
        with patch('v2.claude_session_profile.shutil.which', return_value='/usr/bin/true'):
            with self.assertRaisesRegex(Invalid, '2.1.319'):
                discover_cli()

    def test_startup_environment_allowlist_and_legacy_conflict(self):
        source = {'ANTHROPIC_API_KEY': 'synthetic-api-key', 'ANTHROPIC_MODEL': 'fixture-model',
                  'ANTHROPIC_BASE_URL': 'https://relay.example.invalid/anthropic',
                  'SSH_AUTH_SOCK': '/tmp/not-a-cli-auth-input', 'LD_PRELOAD': '/tmp/not-forwarded.so',
                  'OPENAI_API_KEY': 'not-claude', 'CLAUDE_CONFIG_DIR': '/tmp/not-an-approved-profile'}
        snapshot = startup_environment(source)
        self.assertEqual(set(snapshot), {'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'ANTHROPIC_BASE_URL'})
        self.assertNotIn(source['ANTHROPIC_API_KEY'], environment_identity(snapshot))
        with self.assertRaises(Invalid):
            startup_environment(source, 'https://different.example.invalid')
        with self.assertRaises(Invalid):
            startup_environment({}, source['ANTHROPIC_BASE_URL'])
        self.assertEqual(startup_environment(source, source['ANTHROPIC_BASE_URL']), snapshot)
        source['ANTHROPIC_API_KEY'] = 'rotated-fixture-key'
        self.assertNotEqual(environment_identity(snapshot), environment_identity(startup_environment(source)))


if __name__ == '__main__':
    unittest.main()
