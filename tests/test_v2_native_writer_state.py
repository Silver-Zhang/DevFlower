from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from v2.native_writer_state import require_native_writer_idle
from v2.session_identity import SessionRefused
from v2.store import DraftStore


class NativeWriterStateTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        workspace = folder / 'workspace'
        workspace.mkdir()
        self.store = DraftStore(folder / 'state', [workspace])
        self.binding = {'provider': 'codex', 'config_root': '/fixture/native'}
        self.target = str(uuid4())

    def refuse(self, code, **kwargs):
        with self.assertRaises(SessionRefused) as error:
            require_native_writer_idle(self.store, self.binding, self.target, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_unknown_standalone_or_pty_blocks_other_writers(self):
        operation = dict(binding=self.binding, target=self.target, state='RUNNING', effect_uncertain=True)
        self.store.db.put('native-session-operation', 'operation', operation)
        self.refuse('RECOVERY_REQUIRED')
        require_native_writer_idle(self.store, self.binding, self.target, ignore_operation='operation')
        self.store.db.change('native-session-operation', 'operation', {'state': 'COMPLETED', 'effect_uncertain': False})
        self.store.db.put('native-session-pty', 'pty', dict(binding=self.binding, session_id=self.target,
                          state='RUNNING', pending_effect=True))
        self.refuse('RECOVERY_REQUIRED')
        require_native_writer_idle(self.store, self.binding, self.target, ignore_pty='pty')

    def test_unknown_workflow_detected_before_any_native_uuid_is_observed(self):
        self.store.db.put('node-session', 'chat', {'binding': dict(self.binding, new_session_id=self.target)})
        self.store.db.put('activation', 'activation', {'chat_key': 'chat', 'state': 'FAILED',
                                                      'effect_uncertain': True})
        self.refuse('RECOVERY_REQUIRED')
        require_native_writer_idle(self.store, self.binding, self.target, ignore_chat='chat')
        self.store.db.change('activation', 'activation', {'state': 'RUNNING', 'effect_uncertain': False})
        self.refuse('LEASE_HELD')
        self.store.db.change('activation', 'activation', {'receipt_verified': True})
        require_native_writer_idle(self.store, self.binding, self.target)

    def test_other_profile_or_provider_with_same_uuid_does_not_block(self):
        for key, changes in (('provider', {'provider': 'claude'}), ('root', {'config_root': '/fixture/other'})):
            binding = dict(self.binding, **changes)
            self.store.db.put('native-session-operation', key, {'binding': binding,
                              'target': self.target, 'state': 'RECOVERY_REQUIRED'})
        require_native_writer_idle(self.store, self.binding, self.target)
