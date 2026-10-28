"""New drafts inherit timeouts without rewriting saved workflows."""
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import unittest
from studio.node_defaults import profile_timeout
from studio.workbench_examples import examples


class NodeDefaultsTests(unittest.TestCase):
    def test_profile_value_bounds_and_invalid_values(self):
        for value,want in [(1800,1800),(10,10),(3600,3600),(1,10),(9000,3600),
                           (None,300),(True,300),('1800',300),(2.5,300)]:
            with self.subTest(value=value):self.assertEqual(profile_timeout({'timeout_seconds':value}),want)
        self.assertEqual(profile_timeout({}),300)

    def test_every_new_example_including_nested_loop_uses_its_provider(self):
        profiles={'codex':dict(provider='codex',model='codex',effort='high',timeout_seconds=1800),
                  'claude':dict(provider='claude',model='claude',effort='high',timeout_seconds=1200)}
        def check(graph):
            for node in graph['nodes']:
                if node['kind']=='agent':self.assertEqual(node['timeout'],profiles[node['profile']]['timeout_seconds'])
                if node['kind']=='loop':check(node['body'])
        for graph in examples(profiles):check(graph)
        old=examples(profiles)[0];old['nodes'][1]['timeout']=300;before=copy.deepcopy(old)
        profiles['codex']['timeout_seconds']=2000;examples(profiles)
        self.assertEqual(old,before)
