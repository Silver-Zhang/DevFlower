"""Skipped downstream nodes report the real cause (#139)."""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from studio.workbench_scheduler import skip_reason


class SkipReasonTests(unittest.TestCase):
    def test_skipped_upstream_is_reported_as_upstream_failure(self):
        incoming = [{'source': 'branchA'}, {'source': 'branchB'}]
        states = {'branchA': {'state': 'SKIPPED'}, 'branchB': {'state': 'COMPLETED'}}
        self.assertEqual(skip_reason(incoming, states), '上游失败、取消或状态未确认')

    def test_route_or_condition_filter_keeps_branch_reason(self):
        incoming = [{'source': 'route', 'route': 'code'}, {'source': 'start'}]
        states = {'route': {'state': 'COMPLETED'}, 'start': {'state': 'COMPLETED'}}
        self.assertEqual(skip_reason(incoming, states), '未选择此条件分支')

    def test_direct_completed_upstreams_with_condition_filter(self):
        incoming = [{'source': 'check', 'when': 'true'}]
        self.assertEqual(skip_reason(incoming, {'check': {'state': 'COMPLETED'}}), '未选择此条件分支')


if __name__ == '__main__':
    unittest.main()
