"""DevFlower hide/unhide is metadata-only and reversible (#39).

Synthetic native records only; no CLI or model call is claimed.
"""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from orchestration.common import digest
from studio.workbench_sessions import WorkbenchSessions
from test_workbench_runtime import fixture, structured_session


class SessionVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.w = self.base.project['id']
        self.manager = WorkbenchSessions(self.rt)

    def handles(self, view='active'):
        return [r['handle'] for r in self.manager.listing(self.w, view)['sessions']]

    def test_devflower_hide_is_metadata_only_and_reversible(self):
        path, row = structured_session(self.base, self.rt, 'hide-A', 'codex')
        before = digest(path.read_bytes())
        hidden = self.manager.change(self.w, row['handle'], 'hide')
        self.assertIs(hidden['hidden'], True)
        self.assertIs(hidden['native_modified'], False)
        self.assertEqual(hidden['collection'], 'active')
        self.assertNotIn(row['handle'], self.handles('active'))
        rows = self.manager.listing(self.w, 'hidden')['sessions']
        self.assertIn(row['handle'], [r['handle'] for r in rows])
        self.assertIs(next(r for r in rows if r['handle'] == row['handle'])['hidden'], True)
        self.assertEqual(digest(path.read_bytes()), before)
        shown = self.manager.change(self.w, row['handle'], 'unhide')
        self.assertIs(shown['hidden'], False)
        self.assertIn(row['handle'], self.handles('active'))
        self.assertNotIn(row['handle'], self.handles('hidden'))
        self.assertEqual(digest(path.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
