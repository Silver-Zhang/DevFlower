from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from orchestration.common import Blocked, canonical, digest
from orchestration.sandbox import abi
from studio.native_codex_exec import exec_args
from v2.codex_terminals import backend_args
from test_native_codex_image import compile_static
from v2.codex_agent_scope import agent_skill_options, prepare_agent_scope, scope_options
from v2.codex_code_mode import NAME, companion_identity, pin_code_mode_host


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

    def test_development_pins_one_literal_project_key_without_changing_other_exec(self):
        import tomllib
        cwd = '/workspace/round.one/"数据"'
        binding = dict(executable='/fixture/codex',role='reviewer',model='fixture',effort='low',
                       native_interaction='ordinary',native_protected=['/fixture/store'],cwd=cwd,provider='codex')
        self.assertFalse(any(arg.startswith('projects=') for arg in exec_args(binding,[])))
        self.assertFalse(any(arg.startswith('projects=') for arg in backend_args(binding,[],'/fixture/pinned','p')))
        binding['v2_development_untrusted'] = True
        for args in (exec_args(binding,[]),backend_args(binding,[],'/fixture/pinned','p',read_only=True)):
            [override] = [arg for arg in args if arg.startswith('projects=')]
            self.assertEqual(tomllib.loads(override),{'projects':{cwd:{'trust_level':'untrusted'}}})
        with self.assertRaises(Blocked):
            exec_args(dict(binding,cwd='relative'),[])

    @unittest.skipUnless(abi() >= 6 and os.access('/usr/bin/cc', os.X_OK),
                         'requires Linux Landlock ABI 6 and a static C compiler')
    def test_code_mode_companion_is_pinned_and_has_no_files_credentials_or_network(self):
        with TemporaryDirectory() as path:
            root = Path(path)
            main = root / 'codex'
            companion = root / NAME
            private = root / 'private'
            private.write_text('unavailable to native companion')
            compile_static(main, '''
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc < 2) return 11;
  int status;
  int pid = fork();
  if (pid < 0) return 12;
  if (!pid) {
    if (argc == 2) execl(argv[1], argv[1], (char *)0);
    else execl(argv[1], argv[1], "--listen", argv[2], (char *)0);
    _exit(13);
  }
  return waitpid(pid, &status, 0) == pid && WIFEXITED(status) ? WEXITSTATUS(status) : 14;
}
''')
            self.assertIsNone(companion_identity(main))
            compile_static(companion, '''
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void) {
  int pair[2];
  if (getenv("DEVFLOWER_TEST_SECRET")) return 21;
  if (fopen(''' + json.dumps(str(private)) + ''', "r")) return 22;
  if (socket(AF_INET, SOCK_STREAM, 0) >= 0 || socket(AF_UNIX, SOCK_STREAM, 0) >= 0) return 23;
  if (socketpair(AF_UNIX, SOCK_STREAM, 0, pair)) return 24;
  close(pair[0]); close(pair[1]);
  puts("scoped");
  return 0;
}
''')
            expected = companion_identity(main)
            staged = root / 'stage'
            staged.mkdir(mode=0o700)
            launched = staged / 'codex'
            os.link(main, launched)
            wrapper = staged / NAME
            with ExitStack() as held:
                verify = pin_code_mode_host(launched, main, expected, held)
                verify()
                args = [str(launched), str(wrapper)]
                env = dict(os.environ, DEVFLOWER_TEST_SECRET='test-only')
                for extra in ([], ['grpc+stdio://']):
                    result = subprocess.run(args + extra, env=env, capture_output=True, timeout=10)
                    self.assertEqual((result.returncode, result.stdout), (0, b'scoped\n'))
                result = subprocess.run(args + ['http://outside.invalid'], env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 125)
                wrapper.unlink()
                wrapper.symlink_to(main)
                with self.assertRaises(Blocked):
                    verify()
            replacement = root / 'replacement'
            compile_static(replacement, 'int main(void){return 23;}')
            replacement.replace(companion)
            with ExitStack() as held, self.assertRaises(Blocked):
                pin_code_mode_host(launched, main, expected, held)
            companion.unlink()
            companion.symlink_to(main)
            with self.assertRaises(Blocked):
                companion_identity(main)

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
