"""Incomplete internal bindings fail closed before any process (#77)."""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex


class IncompleteBindingTests(unittest.TestCase):
    def test_missing_executable_is_a_controlled_refusal(self):
        adapter = LocalCodex({'provider': 'codex', 'role': 'reviewer', 'execution_mode': 'Ask',
                              'model': 'fixture', 'effort': 'low'}, {'step_timeout_seconds': 30})
        with self.assertRaisesRegex(Blocked, '内部执行绑定缺少必需字段.*executable'):
            adapter._start_narrow('SYNTHETIC', '/nonexistent', {}, None, None, None, None)

    def test_missing_provider_is_a_controlled_refusal(self):
        adapter = LocalCodex({'executable': '/nonexistent/codex', 'role': 'reviewer', 'execution_mode': 'Ask',
                              'model': 'fixture', 'effort': 'low'}, {'step_timeout_seconds': 30})
        with self.assertRaisesRegex(Blocked, '内部执行绑定缺少必需字段.*provider'):
            adapter._start_narrow('SYNTHETIC', '/nonexistent', {}, None, None, None, None)


if __name__ == '__main__':
    unittest.main()
