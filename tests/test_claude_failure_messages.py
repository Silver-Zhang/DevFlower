"""Actual process flags, rather than model names, determine failure messages."""
import unittest
from unittest.mock import patch
import test_native_local
from studio.native_local import bind
from studio.local_claude_adapter import LocalClaude
from orchestration.common import Blocked


class ClaudeFailureMessages(unittest.TestCase):
    def test_timeout_cancel_overflow_and_nonzero_are_distinct_and_never_retried(self):
        owner=test_native_local.NativeLocalTests();owner.setUp();self.addCleanup(owner.doCleanups)
        binding=bind(owner.rt,owner.rt.operator['profiles']['default-claude'],owner.base.project['id'])
        binding.update(role='reviewer',project=owner.base.project['id'],timeout_seconds=1800)
        for flag,word in [('timeout','超时'),('cancelled','已取消'),('overflow','输出超过'),('nonzero','退出码 42')]:
            process=dict(exit=42 if flag=='nonzero' else 143,duration=300.641,timeout=False,cancelled=False,overflow=False)
            if flag!='nonzero':process[flag]=True
            with self.subTest(flag=flag),patch('studio.local_claude_adapter.execute',return_value=(process,b'',b'')) as call:
                with self.assertRaises(Blocked) as caught:
                    LocalClaude(binding,dict(step_timeout_seconds=1800,max_log_bytes=10000)).start('task',owner.base.source,owner.home)
                self.assertIn(word,str(caught.exception));self.assertIn('没有自动重发',str(caught.exception))
                if flag=='timeout':self.assertIn('1800',str(caught.exception));self.assertIn('300.641',str(caught.exception))
                self.assertEqual(call.call_count,1)
