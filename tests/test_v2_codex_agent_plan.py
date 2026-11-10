"""Native Plan protocol and separate transition authorization."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.native_codex_exec import FEATURES
from v2.codex_agent_plan import await_mode_settings, native_mode, require_effective_config, scope_matches
from v2.runtime import V2Runtime


class CodexPlanTests(unittest.TestCase):
    binding = dict(cwd='/approved/workspace', config_root='/approved/profile', model='fixture-model', effort='low')
    profile = 'approved_profile'
    sid = '018f410e-d65d-7000-9000-123456789abc'

    def settings(self):
        return dict(cwd=self.binding['cwd'], model=self.binding['model'], approvalPolicy='never',
                    activePermissionProfile={'id': self.profile, 'extends': None},
                    collaborationMode=native_mode(self.binding, "plan"))

    def test_plan_mode_requires_native_ack_on_exact_session_and_read_scope(self):
        def event(settings, sid=self.sid):
            return dict(method='thread/settings/updated', params={'threadId': sid, 'threadSettings': settings})
        settings = self.settings()
        self.assertTrue(scope_matches(settings, self.binding, self.profile))
        await_mode_settings(SimpleNamespace(next_event=lambda: event(settings)), self.binding, self.profile, self.sid, "plan")
        for invalid in (
            event(dict(settings, approvalPolicy='on-request')),
            event(dict(settings, activePermissionProfile={'id': self.profile, 'extends': 'outside'})),
            event(dict(settings, collaborationMode={'mode': 'default', 'settings': {}})),
            event(settings, 'different-session'),
            {'method': 'item/tool/requestUserInput', 'id': 3},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(Blocked):
                await_mode_settings(SimpleNamespace(next_event=lambda: invalid), self.binding, self.profile, self.sid, "plan")

    def test_effective_workspace_config_disables_extensions_and_keeps_the_operator_provider(self):
        features = dict.fromkeys(FEATURES, False)
        features.update(worktrees=False, default_mode_request_user_input=False, unified_exec=False)
        approved = dict(features=features, notify=[], mcp_servers={'project-marker': {'enabled': False}})
        rpc = SimpleNamespace(request=Mock(return_value={'config': approved}))
        check = lambda: require_effective_config(rpc, self.binding, ['-c', 'model_provider="openai"'], read_only=True)
        check()
        rpc.request.assert_called_once_with('config/read', {'cwd': self.binding['cwd'], 'includeLayers': False})
        for changed in (
            dict(features=dict(features, plugins=True)),
            dict(mcp_servers={'late-new-server': {'enabled': True}}),
            dict(mcp_servers={'missing-flag': {'command': '/bin/sh'}}),
            dict(mcp_servers={'path.to.new.server': {'enabled': False}}),
            dict(notify=['/bin/sh']),
            dict(openai_base_url='https://unexpected.invalid'),
        ):
            rpc.request.return_value = {'config': dict(approved, **changed)}
            with self.subTest(changed=changed), self.assertRaises(Blocked):
                check()

    def test_agent_transition_has_distinct_explicit_approval_and_stable_identity(self):
        runtime = SimpleNamespace(launch=Mock(return_value={'state': 'QUEUED'}))
        start = lambda consent: V2Runtime.launch_plan_agent(runtime, 'workspace', '1' * 32, 'node', 'intent', consent)
        with self.assertRaises(Invalid):
            start('none')
        runtime.launch.assert_not_called()
        self.assertEqual(start('confirm-workspace-write'), {'state': 'QUEUED'})
        original = runtime.launch.call_args
        self.assertEqual(start('confirm-workspace-write'), {'state': 'QUEUED'})
        self.assertEqual(runtime.launch.call_args, original)
        self.assertEqual(original.kwargs['plan_source'], ('1' * 32, 'node'))


if __name__ == '__main__':
    unittest.main()
