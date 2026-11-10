from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from orchestration.common import Blocked, canonical, digest
from studio.native_codex_exec import exec_args
from v2.codex_agent_scope import agent_skill_options, prepare_agent_scope, scope_options


class CodexAgentScopeTests(unittest.TestCase):
    def test_scope_replaces_wide_legacy_profile_and_pins_tool_policy(self):
        binding = {'executable': '/fixture/codex', 'role': 'implementer', 'model': 'fixture',
                   'effort': 'low', 'native_interaction': 'ordinary', 'native_protected': ['/fixture/store']}
        options = scope_options('devflower_agent_fixture', '/workspace/child', '/fixture/pinned', 'workspace-write')
        args = exec_args(binding, [], scope_options=options)
        self.assertIn('default_permissions="devflower_agent_fixture"', args)
        self.assertIn('permissions.devflower_agent_fixture.filesystem={":root"="deny",":minimal"="read",'
                      '"/workspace/child"="write","/fixture/pinned"="read"}', args)
        self.assertNotIn('default_permissions="devflower"', args)
        self.assertIn('permissions.devflower_agent_fixture.network.enabled=false', args)
        self.assertIn('notify=[]', args)
        self.assertGreater(args.index('features.code_mode_host=false'), args.index('features.code_mode_host=true'))
        self.assertIn('"/workspace/child"="read"', scope_options(
            'devflower_agent_fixture', '/workspace/child', '/fixture/pinned', 'read')[1])

    def test_mismatched_role_or_protected_workspace_is_rejected_before_spawn(self):
        binding = {'provider': 'codex', 'execution_backend': 'native-local', 'native_interaction': 'ordinary',
                   'cwd': '/fixture/workspace',
                   '_metadata_guard': '/fixture/state', 'v2_agent_scope': 'workspace-write',
                   'role': 'reviewer', 'native_protected': ['/fixture/workspace/private']}
        with self.assertRaises(Blocked), prepare_agent_scope(binding, binding['cwd']):
            self.fail('read role was given workspace write')
        binding['role'] = 'implementer'
        with self.assertRaises(Blocked), prepare_agent_scope(binding, binding['cwd']):
            self.fail('overlapping protected path was admitted')

    def test_native_skill_options_require_approved_bytes_and_disable_other_paths(self):
        with TemporaryDirectory() as path:
            root = Path(path)
            cwd = root / 'project'
            home = root / 'home'
            chosen = cwd / '.agents/skills/choice/SKILL.md'
            chosen.parent.mkdir(parents=True)
            text = b'---\nname: choice\ndescription: fixture\n---\nApproved body'
            chosen.write_bytes(text)
            other = home / '.codex/skills/choice/SKILL.md'
            binding = {'home': str(home), 'config_root': str(home / '.codex'),
                       'v2_agent_skills': [{'path': str(chosen), 'name': 'choice',
                                            'sha256': digest(canonical({'SKILL.md': digest(text)}))}]}
            inventory = {'data': [{'cwd': str(cwd), 'skills': [{'path': str(chosen)}, {'path': str(other)}]}]}
            options = agent_skill_options(binding, str(cwd), inventory)
            self.assertIn('skills.include_instructions=false', options)
            self.assertIn('path="' + str(chosen) + '",enabled=true', options[-1])
            self.assertIn('path="' + str(other) + '",enabled=false', options[-1])
            chosen.write_bytes(text + b' modified')
            with self.assertRaises(Blocked):
                agent_skill_options(binding, str(cwd), inventory)
