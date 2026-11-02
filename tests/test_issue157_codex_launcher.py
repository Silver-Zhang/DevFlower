"""#157: an npm/shell Codex launcher is reported at the check stage."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests')]

from orchestration.common import Blocked  # noqa: E402
from studio.native_codex_image import incompatibility, static_elf  # noqa: E402
from studio.workflow_inspection import inspect_workflow  # noqa: E402
from test_workbench_runtime import fixture  # noqa: E402


def graph():
    return dict(version=3, name='launcher-fixture', nodes=[
        dict(id='start', kind='start', name='输入'),
        dict(id='agent', kind='agent', name='处理任务', profile='default-codex', provider='codex',
             model='gpt-6.1-sol', effort='high', mode='Ask', permission='read',
             initial_prompt='初始化', round_prompt='执行', handoff_prompt='交接'),
        dict(id='result', kind='result', name='结束')],
        edges=[dict(source='start', target='agent'), dict(source='agent', target='result')])


class LauncherShapeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.script = self.dir/'codex'
        self.script.write_text('#!/usr/bin/env node\nconsole.log("npm launcher")\n')
        self.script.chmod(0o755)

    def test_script_launcher_is_reported_before_execution(self):
        message = incompatibility(self.script)
        self.assertIn('脚本启动器', message)
        self.assertIn('静态原生二进制', message)
        self.assertIn('Claude Code', message)

    def test_static_elf_refuses_scripts_with_the_same_actionable_text(self):
        fd = os.open(self.script, os.O_RDONLY)
        try:
            with self.assertRaises(Blocked) as caught:
                static_elf(fd)
        finally:
            os.close(fd)
        self.assertIn('npm', str(caught.exception))
        self.assertIn('Claude Code', str(caught.exception))

    def test_missing_executable_still_returns_a_reason(self):
        self.assertIn('无法读取', incompatibility(self.dir/'missing'))


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.script = Path(tmp.name)/'codex'
        self.script.write_text('#!/usr/bin/env node\n')
        self.script.chmod(0o755)
        self.rt.operator['profiles']['default-codex']['executable'] = str(self.script)
        self.rt.operator['native_local'] = dict(codex=dict(home=str(self.script.parent),
                                                           config_root=str(self.script.parent)))

    def test_check_reports_the_reason_and_next_step(self):
        result = inspect_workflow(self.rt, graph(), '跑一次')
        messages = [i['message'] for i in result['issues']]
        self.assertTrue(any('Codex 无法执行' in m and 'Claude Code' in m for m in messages), messages)
        # It is an error, not a warning: the run would fail at the same place.
        self.assertTrue(any(i['severity'] == 'error' and 'Codex 无法执行' in i['message']
                            for i in result['issues']))

    def test_script_check_runs_without_invoking_the_launcher(self):
        # A script that would create a marker if executed must never run.
        marker = Path(self.script.parent)/'executed'
        self.script.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\n')
        inspect_workflow(self.rt, graph(), '跑一次')
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
