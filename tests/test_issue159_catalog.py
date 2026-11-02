"""Assistant catalog failures: actionable 404 text and bounded retry (#159)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tests')]

from orchestration.common import Blocked  # noqa: E402
from studio.canvas_assistant import CanvasAssistant  # noqa: E402
from studio.devflower_agent import DevFlowerAgent  # noqa: E402
from test_workbench_runtime import fixture  # noqa: E402


class CatalogHintTests(unittest.TestCase):
    def agent(self, protocol):
        value = DevFlowerAgent.__new__(DevFlowerAgent)
        value.timeout = 5
        value.config = dict(protocol=protocol, base_url='https://relay.invalid/v1', model='m',
                            effort='high', api_key='SYNTHETIC')
        value._request = lambda *a, **k: (_ for _ in ()).throw(
            Blocked('助手模型接口 HTTP 404；该地址没有这个接口'))
        return value

    def test_404_explains_the_protocol_and_next_step(self):
        with self.assertRaisesRegex(Blocked, 'Anthropic 协议') as anthropic:
            self.agent('anthropic').catalog()
        self.assertIn('OpenAI 兼容地址', str(anthropic.exception))
        with self.assertRaisesRegex(Blocked, 'OpenAI 兼容中转'):
            self.agent('openai').catalog()

    def test_non_404_is_not_rewritten(self):
        agent = self.agent('openai')
        agent._request = lambda *a, **k: (_ for _ in ()).throw(
            Blocked('助手模型接口 HTTP 401；认证未通过'))
        with self.assertRaises(Blocked) as caught:
            agent.catalog()
        self.assertNotIn('OpenAI 兼容中转', str(caught.exception))


class FailingBackend:
    calls = 0

    def __init__(self, cwd, **kw): pass

    def __enter__(self): return self

    def __exit__(self, *a): return False

    def catalog(self):
        type(self).calls += 1
        raise Blocked('助手模型接口 HTTP 404；该地址没有这个接口')


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.base, self.rt = fixture(self)
        self.ai = CanvasAssistant(self.rt)
        self.addCleanup(self.ai.shutdown)
        # A custom backend skips the per-connection identity lookup; this test
        # is about how many failing requests reach the transport.
        self.ai.backend = lambda cwd, **kw: FailingBackend(cwd, **kw)
        FailingBackend.calls = 0

    def read(self, refresh=False):
        return CanvasAssistant.catalog(self.ai, refresh=refresh)

    def test_repeat_open_does_not_repeat_the_failing_request(self):
        with self.assertRaises(Blocked):
            self.read()
        with self.assertRaisesRegex(Blocked, '已缓存') as cached:
            self.read()
        self.assertEqual(FailingBackend.calls, 1)
        self.assertIn('刷新助手模型', str(cached.exception))

    def test_explicit_refresh_retries(self):
        with self.assertRaises(Blocked):
            self.read()
        with self.assertRaises(Blocked):
            self.read(refresh=True)
        self.assertEqual(FailingBackend.calls, 2)

    def test_success_clears_the_cached_failure(self):
        with self.assertRaises(Blocked):
            self.read()
        good = dict(provider='devflower-agent', models=[dict(id='m', name='m')], source='SYNTHETIC',
                    default_model='m', observed_at=time.time(), inference_calls=0)
        with patch.object(FailingBackend, 'catalog', lambda self: good):
            self.assertEqual(self.read(refresh=True)['models'], good['models'])
        self.assertIsNone(self.ai.catalog_failure)


if __name__ == '__main__':
    unittest.main()
