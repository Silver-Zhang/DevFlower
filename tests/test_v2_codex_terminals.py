"""Scoped Codex PTY policy and durable lifecycle counterexamples."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.common import Blocked
from studio.graph import Invalid
from v2.codex_pty_gateway import CodexProtocol, Refused
from v2.codex_terminals import CodexTerminals, guarded_workspace
from v2.session_identity import StorageRootIdentity
from v2.session_lease import CODEX_COORDINATION_VERSION
from studio.session_sources import opened
from v2.store import DraftStore


class CodexProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = '01900000-0000-7000-8000-000000000001'
        self.cwd = '/fixture/approved'
        self.literal = 'fixture-credential-never-forward'
        self.effects = []
        self.scope = Mock()
        self.ready = Mock()
        self.protocol = CodexProtocol(dict(cwd=self.cwd, model='fixture', effort='low'),
                                      self.session, {self.literal}, self.effects.append,
                                      on_resume=self.scope, on_ready=self.ready)
        self.next_id = 0
        self.reply(self.send('initialize', {}), {})

    def send(self, method, params):
        self.next_id += 1
        return self.protocol.input(dict(id=self.next_id, method=method, params=params))

    def reply(self, request, result):
        return self.protocol.backend(dict(id=request['id'], result=result))['result']

    def resume(self, **changes):
        raw = dict(thread=dict(id=self.session, sessionId=self.session, cwd=self.cwd,
            path='/fixture/protected/store/history', preview=self.literal, originator=self.literal,
            historyMode='paginated', createdAt=1, updatedAt=2, ephemeral=False, cliVersion='0.163.0',
            source='exec', status={'type': 'idle'}, modelProvider='fixture'),
            activePermissionProfile={'id': 'devflower'}, approvalPolicy='never',
            model='fixture', cwd=self.cwd, runtimeWorkspaceRoots=[self.cwd])
        raw.update(changes)
        request = self.send('thread/resume', {'threadId': self.session})
        self.assertEqual(request['params']['runtimeWorkspaceRoots'], [self.cwd])
        self.scope.assert_called_once()
        return self.reply(request, raw)

    def test_method_and_uuid_scope_and_only_pinned_overrides(self):
        for method, params in (
            ('thread/read', {'threadId': 'different'}),
            ('thread/list', {}),
            ('thread/start', {'threadId': self.session}),
            ('thread/shellCommand', {'threadId': self.session, 'command': 'cat /outside'}),
            ('config/read', {'cwd': self.cwd}),
            ('thread/resume', {'threadId': self.session, 'cwd': '/outside'}),
            ('thread/resume', {'threadId': self.session, 'permissions': 'danger-full-access'}),
            ('thread/resume', {'threadId': self.session, 'config': {'modelProvider': 'exfiltrate'}}),
        ):
            with self.subTest(method=method, params=params), self.assertRaises(Refused):
                self.send(method, params)
        resumed = self.resume()
        self.ready.assert_called_once()
        self.assertIsNone(resumed['thread']['path'])
        self.assertNotIn(self.literal, str(resumed))
        with self.assertRaises(Refused):
            self.send('thread/resume', {'threadId': self.session})

    def test_profile_response_must_match_before_readiness(self):
        with self.assertRaises(Refused):
            self.resume(activePermissionProfile={'id': 'untrusted'})
        self.ready.assert_not_called()
        self.assertEqual(self.effects, [])

    def test_turn_reconstructs_text_and_records_effect_before_reply(self):
        self.resume()
        params = {'threadId': self.session, 'input': [{'type': 'text', 'text': 'hello'}]}
        for extra in (
            {'model': 'arbitrary'}, {'modelProvider': 'other'}, {'permissions': 'full-access'},
            {'cwd': '/outside'}, {'approvalPolicy': 'on-request'}, {'input': [{'type': 'localImage', 'path': '/outside'}]},
            {'input': [{'type': 'text', 'text': 'hello', 'text_elements': [{'extra': True}]}]},
        ):
            with self.subTest(extra=extra), self.assertRaises(Refused):
                self.send('turn/start', params | extra)
        request = self.send('turn/start', params)
        self.assertEqual(self.effects, [True])
        self.assertEqual(request['params'], dict(threadId=self.session,
            input=[dict(type='text', text='hello', text_elements=[])],
            cwd=self.cwd, model='fixture', effort='low', permissions='devflower',
            runtimeWorkspaceRoots=[self.cwd], approvalPolicy='never'))
        turn = dict(id='native-turn', items=[], status='inProgress')
        self.reply(request, {'turn': turn})
        with self.assertRaises(Refused):
            self.send('turn/start', params)
        self.assertIsNone(self.protocol.backend(dict(method='item/agentMessage/delta',
            params=dict(threadId=self.session, turnId=turn['id'], delta=self.literal))))
        self.assertIsNone(self.protocol.backend(dict(method='turn/completed',
            params=dict(threadId='different', turn=dict(turn, status='completed')))))
        self.assertEqual(self.effects, [True])
        result = self.protocol.backend(dict(method='turn/completed', params=dict(threadId=self.session,
            turn=dict(turn, status='completed', items=[dict(type='agentMessage', id='message',
                      phase='final_answer', text=self.literal)]))))
        self.assertNotIn(self.literal, str(result))
        self.assertEqual(self.effects, [True, False])

    def test_unconfirmed_native_turn_cannot_be_resent(self):
        self.resume()
        params = {'threadId': self.session, 'input': [{'type': 'text', 'text': 'hello'}]}
        request = self.send('turn/start', params)
        response = self.protocol.backend(dict(id=request['id'], error={'message': self.literal}))
        self.assertNotIn(self.literal, str(response))
        with self.assertRaises(Refused):
            self.send('turn/start', params)
        self.assertEqual(self.effects, [True])


class CodexTerminalGateTests(unittest.TestCase):
    def test_activation_requires_operator_opt_in_exact_version_and_runtime(self):
        operation = SimpleNamespace(codex_terminal=True, codex=SimpleNamespace(version=CODEX_COORDINATION_VERSION))
        with patch('v2.codex_terminals.RUNTIME', ('/usr/bin/true', '/nonexistent/fixture-bwrap')):
            self.assertFalse(CodexTerminals.available(operation))
        with patch('v2.codex_terminals.RUNTIME', ('/usr/bin/true',)):
            self.assertTrue(CodexTerminals.available(operation))
            operation.codex.version = 'future-unverified'
            self.assertFalse(CodexTerminals.available(operation))
            self.assertFalse(CodexTerminals.available(None))

    def test_workspace_preflight_rejects_hardlinks_and_special_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / 'workspaces'
            cwd = parent / 'approved'
            cwd.mkdir(parents=True)
            store = DraftStore(root / 'state', [parent])
            outside = parent / 'outside'
            outside.write_text('outside')
            binding = dict(cwd=str(cwd), cwd_identity=StorageRootIdentity.capture(cwd).fields())
            with opened(cwd, directory=True) as fd:
                guarded_workspace(store, binding, fd)
                (cwd / 'symlink').symlink_to(outside)
                guarded_workspace(store, binding, fd)
                (cwd / 'hardlink').hardlink_to(outside)
                with self.assertRaises(Blocked):
                    guarded_workspace(store, binding, fd)
                (cwd / 'hardlink').unlink()
                import os
                os.mkfifo(cwd / 'fifo')
                with self.assertRaises(Blocked):
                    guarded_workspace(store, binding, fd)

    def test_unrecognized_browser_owner_and_command_options_fail_closed(self):
        manager = object.__new__(CodexTerminals)
        with self.assertRaises(Invalid):
            manager.action('claim', dict(workspace='x', id='a'*64, owner='local:forged', generation=0))
        with self.assertRaises(Invalid):
            manager.action('stop', dict(workspace='x', id='a'*64, command='kill -9 1'))


if __name__ == '__main__':
    unittest.main()
