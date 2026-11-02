"""Templates start on a tool this host can launch (#140)."""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from studio.workbench_examples import examples


def profile(provider, executable):
    return dict(provider=provider, executable=executable, model='fixture-model', effort='low')


def agent_providers(graph):
    found = []
    for node in graph['nodes']:
        if node['kind'] == 'agent':
            found.append(node['provider'])
        if node.get('body'):
            found.extend(agent_providers(node['body']))
    return found


class TemplateProfileTests(unittest.TestCase):
    def test_non_static_codex_falls_back_to_claude(self):
        graphs = examples({'default-codex': profile('codex', '/bin/echo'),
                           'default-claude': profile('claude', '/bin/echo')})
        for graph in graphs:
            self.assertIn('claude', agent_providers(graph), graph['name'])

    def test_codex_only_install_keeps_codex_as_last_resort(self):
        graphs = examples({'default-codex': profile('codex', '/bin/echo')})
        self.assertEqual(set(agent_providers(graphs[0])), {'codex'})

    def test_claude_only_install_uses_claude(self):
        graphs = examples({'default-claude': profile('claude', '/bin/echo')})
        self.assertEqual(set(agent_providers(graphs[0])), {'claude'})


if __name__ == '__main__':
    unittest.main()
