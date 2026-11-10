"""Targeted V2 standalone effect counterexamples with no external inference."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.store import process_identity
from studio.graph import Invalid
from studio.server import Conflict
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

    def test_explicit_relay_must_be_the_approved_endpoint_and_model(self):
        path = self.base / 'writer.json'
        value = {'version': 1, 'claude': {'model': 'deepseek-flash', 'effort': 'low',
                                           'anthropic_base_url': 'https://api.deepseek.com/anthropic'}}
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        self.assertEqual(load_write_profiles(path), {'claude': value['claude']})
        value['claude']['anthropic_base_url'] = 'https://synthetic.invalid/anthropic'
        path.write_text(json.dumps(value))
        with self.assertRaises(Invalid):
            load_write_profiles(path)


if __name__ == '__main__':
    unittest.main()
