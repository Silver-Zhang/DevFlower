"""Native Plan protocol and separate transition authorization."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.common import Blocked
from studio.graph import Invalid
from v2.codex_agent_plan import await_mode_settings, native_mode, scope_matches
from v2.runtime import V2Runtime


class CodexPlanTests(unittest.TestCase):
    binding = dict(cwd='/approved/workspace', model='fixture-model', effort='low')
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
