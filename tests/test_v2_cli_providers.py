"""Contract tests for native CLI Agent provider metadata and command routing."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.local_claude_adapter import LocalClaude
from studio.local_codex_adapter import LocalCodex
from v2.cli_providers import ClaudeCLI, CodexCLI, ProviderRegistry, model_id
from v2.session_lease import CODEX_COORDINATION_VERSION
from v2.store import DraftStore


class CliProviderTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        parent = self.root / 'projects'
        parent.mkdir()
        self.store = DraftStore(self.root / 'state', [parent])
        self.sid = str(uuid.uuid4())

    def test_registry_is_additive_read_only_until_explicit_refresh(self):
        registry = ProviderRegistry(self.store)
        with patch.object(registry.adapter('claude'), 'observe', return_value={'provider': 'claude'}) as observe:
            self.assertEqual([row['provider'] for row in registry.listing()['providers']], ['codex', 'claude'])
            observe.assert_not_called()
            self.assertEqual(registry.discover('claude')['providers'][1], {'provider': 'claude', 'stale': True})
            self.assertEqual(registry.listing()['providers'][1], {'provider': 'claude', 'stale': True})
            observe.assert_called_once_with()
        for unknown in ('copilot', 'dsh', 'zcode', [], None):
            with self.subTest(unknown=unknown), self.assertRaises(Invalid):
                registry.adapter(unknown)

    def test_claude_projection_distinguishes_config_alias_and_metadata_only(self):
        home = self.root / 'home'
        config = home / '.claude'
        config.mkdir(parents=True)
        (config / 'settings.json').write_text(json.dumps({'model': 'local-relay-model', 'effortLevel': 'high', 'env': {
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'local-relay-model', 'ANTHROPIC_AUTH_TOKEN': 'fixture-secret',
            'ANTHROPIC_BASE_URL': 'https://relay.invalid/anthropic'}}))
        fake = dict(executable='/usr/bin/true', _executable_sha256='fixture', supported=True,
                    cli_version='2.1.319 (Claude Code)', efforts=['low', 'high'], aliases=['sonnet', 'opus'],
                    permission_modes=['manual', 'plan'], native_budget=True, home=str(home), config_root=str(config),
                    chat_only=True)
        auth = subprocess.CompletedProcess([], 0, b'{"loggedIn":true,"apiKeySource":"fixture-secret"}')
        adapter = ClaudeCLI(self.store)
        with (patch.object(adapter, 'profile', return_value=fake), patch('v2.cli_providers.startup_environment', return_value={}),
              patch('v2.cli_providers.sealed_executable') as image, patch('v2.cli_providers.cli_read', return_value=auth) as read):
            image.return_value.__enter__.return_value = (5, 'fixture')
            view = adapter.observe()
        self.assertEqual(view['default_model'], {'id': 'local-relay-model', 'source': 'native_config'})
        self.assertEqual(view['default_effort'], 'high')
        self.assertTrue(view['authenticated'])
        self.assertFalse(view['catalog_available'])
        self.assertFalse(view['inference_verified'])
        self.assertTrue(view['modes']['Ask']['enabled'])
        self.assertTrue(view['modes']['Plan']['enabled'])
        self.assertFalse(view['modes']['Agent']['enabled'])
        self.assertEqual({model['id']: model['source'] for model in view['models']},
                         {'local-relay-model': 'native_config', 'sonnet': 'configured_alias', 'opus': 'cli_alias'})
        self.assertNotIn('fixture-secret', str(view))
        self.assertNotIn('relay.invalid', str(view))
        self.assertEqual(read.call_args.args[2][-3:], ['auth', 'status', '--json'])

    def test_command_building_uses_native_flags_and_validates_uuid(self):
        codex = CodexCLI(None, None)
        binding = dict(executable='/fixture/codex', provider='codex', role='reviewer', native_interaction='ordinary',
                       no_tools=True, native_protected=['/fixture/private'], model='fixture', effort='high')
        argv = codex.command(binding, config={}, session_id=self.sid, resume=True)
        self.assertEqual(argv[:3], ['/fixture/codex', 'exec', 'resume'])
        self.assertEqual(argv[-2:], [self.sid, '-'])
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn('features.shell_tool=false', argv)
        with self.assertRaises(Blocked):
            codex.command(binding, config={}, session_id='--last', resume=True)
        with self.assertRaises(Blocked):
            codex.command(dict(binding, v2_agent_scope='workspace-write'), config={})
        with self.assertRaises(Blocked):
            codex.command(dict(binding, v2_agent_plan=True), config={})
        self.assertIsInstance(codex.transport(binding, {}), LocalCodex)

        claude = ClaudeCLI(self.store)
        binding = dict(executable='/fixture/claude', chat_only=True, model=None, effort=None,
                       agent_permission_mode='manual', max_budget_usd='2.5')
        argv = claude.command(binding, session_id=self.sid)
        self.assertEqual(argv[-2:], ['--session-id', self.sid])
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'manual')
        self.assertEqual(argv[argv.index('--permission-prompts') + 1], 'none')
        self.assertEqual(argv[argv.index('--tools') + 1], '')
        self.assertEqual(argv[argv.index('--max-budget-usd') + 1], '2.5')
        self.assertNotIn('--model', argv)
        self.assertNotIn('--effort', argv)
        with self.assertRaises(Blocked):
            claude.command(dict(binding, v2_claude_scope=True), session_id=self.sid)
        with patch.object(claude, 'require_local'):
            self.assertIsInstance(claude.transport(binding, {}), LocalClaude)

    def test_workspace_write_binding_pins_project_trust_without_changing_read_scope(self):
        native = SimpleNamespace(version=CODEX_COORDINATION_VERSION, native_home=None,
                                 binding=lambda *_args, **_kwargs: dict(
                                     executable='/fixture/codex', account_sha256='bound-account',
                                     direct_environment_sha256='bound-config'))
        adapter = CodexCLI(native, None)
        agent = dict(model='fixture', effort='low', timeout=60, model_source='manual')
        with (patch('v2.cli_providers.os.access', return_value=True),
              patch('v2.codex_code_mode.companion_identity', return_value='bound-companion')):
            writer = adapter.binding('workspace', dict(agent, mode='Agent', permission='workspace-write'))
            reader = adapter.binding('workspace', dict(agent, mode='Ask', permission='workspace-read'))
            chat = adapter.binding('workspace', dict(agent, mode='Ask', permission='none'))
        self.assertIs(writer['v2_development_untrusted'], True)
        self.assertEqual(writer['v2_agent_scope'], 'workspace-write')
        self.assertEqual(writer['role'], 'implementer')
        self.assertEqual(writer['account_sha256'], 'bound-account')
        self.assertEqual(writer['direct_environment_sha256'], 'bound-config')
        self.assertEqual(writer['v2_codex_code_host_sha256'], 'bound-companion')
        self.assertEqual(reader['v2_agent_scope'], 'read')
        self.assertNotIn('v2_development_untrusted', reader)
        self.assertNotIn('v2_development_untrusted', chat)

    def test_catalog_model_identifiers_cannot_echo_known_secrets_or_endpoints(self):
        self.assertEqual(model_id('gateway/custom-2026'), 'gateway/custom-2026')
        self.assertIsNone(model_id('https://relay.invalid/model'))
        self.assertIsNone(model_id('model-fixture-token', {'fixture-token'}))


if __name__ == '__main__':
    unittest.main()
