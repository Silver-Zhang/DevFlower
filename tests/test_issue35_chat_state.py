"""Direct-chat sidebars must reflect a completed native operation (#35).

Synthetic native records only; no CLI or model call is claimed.
"""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

import test_agent_sessions
from studio.workbench_sessions import WorkbenchSessions
from test_workbench_runtime import fixture


class DirectChatStateTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.w = self.base.project['id']
        self.manager = WorkbenchSessions(self.rt)

    def test_completed_direct_chat_operation_reports_completed(self):
        provider, label = 'codex', 'direct-chat'
        home, sid = self.rt.native / label, test_agent_sessions.SID
        path = test_agent_sessions.native(home / ('.' + provider), provider, self.base.source)
        binding = dict(self.rt.operator['profiles']['default-' + provider],
                       runtime_profile='default-' + provider, home=str(home), session_id=sid)
        self.rt.db.put('chat-profile', label, dict(workspace=self.w, binding=binding))
        with path.open('a') as stream:
            stream.write(json.dumps(dict(type='response_item', payload=dict(
                type='message', role='assistant', phase='final_answer',
                content=[dict(type='output_text', text='QA_NATIVE_741')]))) + '\n')
        rows = self.rt.session_center.discover(self.w, True)['discovery']['sessions']
        handle = next(r['handle'] for r in rows
                      if self.rt.db.get('session-handle', r['handle'])['profile']['home'] == str(home))
        self.rt.db.put('session-operation', label + '-op', dict(
            id=label + '-op', workspace=self.w, binding=binding, cwd=str(self.base.source),
            permission='read', state='COMPLETED', created_at=time.time(),
            native_session=sid, requested_session=sid,
            receipt=dict(session_id=sid, process=dict(exit=0, timeout=False, cancelled=False, overflow=False))))
        row = next(r for r in self.manager.listing(self.w)['sessions'] if r['handle'] == handle)
        self.assertIsNone(row.get('workflow'))
        self.assertEqual(row['display_state'], 'COMPLETED')


if __name__ == '__main__':
    unittest.main()
