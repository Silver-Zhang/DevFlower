import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
import uuid
from unittest.mock import patch

from orchestration.common import Blocked, canonical, digest
from orchestration.process import execute
from studio.local_claude_adapter import scoped_terminal
from v2.claude_agent_scope import BWRAP, prepare_scope, project_skills, scope_supported
from v2.session_identity import StorageRootIdentity


class ClaudeScopeTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        workspace, state, home = (root / name for name in ('workspace', 'state', 'home'))
        for path in (workspace, state, home / '.claude'):
            path.mkdir(parents=True)
        executable = str(Path('/usr/bin/python3').resolve())
        with open(executable, 'rb') as stream:
            fingerprint = hashlib.file_digest(stream, 'sha256').hexdigest()
        binding = dict(provider='claude', v2_claude_scope=True, cwd=str(workspace),
                       cwd_identity=StorageRootIdentity.capture(workspace).fields(), home=str(home),
                       config_root=str(home / '.claude'), _metadata_guard=str(state), executable=executable,
                       root_identity=StorageRootIdentity.capture(home / '.claude').fields(),
                       _executable_sha256=fingerprint)
        return workspace, binding

    def test_skill_projection_pins_both_sources_and_rejects_changed_selection(self):
        with TemporaryDirectory() as folder:
            cwd, binding = self.fixture(folder)
            picks = []
            for source in (cwd / '.claude/skills', Path(binding['config_root']) / 'skills'):
                file = source / 'example/SKILL.md'
                file.parent.mkdir(parents=True)
                data = b'---\nname: example\ndescription: Native proof\n---\nFixture instruction\n'
                file.write_bytes(data)
                picks.append(dict(path=str(file), name='example', sha256=digest(canonical({'SKILL.md': digest(data)}))))
            binding['v2_agent_skills'] = picks
            names, plugins = project_skills(binding, str(cwd), Path(folder) / 'projection')
            self.assertEqual(names, {'df-workspace:example', 'df-user:example'})
            self.assertEqual(set(plugins), {'df-workspace', 'df-user'})
            Path(picks[-1]['path']).write_bytes(data + b'changed')
            with self.assertRaises(Blocked):
                project_skills(binding, str(cwd), Path(folder) / 'other')

    def test_terminal_requires_native_skill_inventory_and_exact_session(self):
        sid = str(uuid.uuid4())
        raw = '\n'.join(map(json.dumps, [
            {'type': 'system', 'subtype': 'init', 'session_id': sid, 'skills': ['df-user:example']},
            {'type': 'result', 'session_id': sid, 'result': 'output'}]))
        self.assertEqual(json.loads(scoped_terminal(raw, {'df-user:example'}, sid))['result'], 'output')
        with self.assertRaises(Blocked):
            scoped_terminal(raw, {'df-workspace:example'}, sid)

    def test_missing_isolation_and_missing_auth_fail_before_spawn(self):
        with TemporaryDirectory() as folder:
            cwd, binding = self.fixture(folder)
            with patch('v2.claude_agent_scope.scope_supported', return_value=False), self.assertRaises(Blocked):
                with prepare_scope(binding, str(cwd), str(uuid.uuid4()), False, {}):
                    self.fail('unsupported isolation launched')
            with patch('v2.claude_agent_scope.scope_supported', return_value=True), self.assertRaises(Blocked):
                with prepare_scope(binding, str(cwd), str(uuid.uuid4()), False, {}):
                    self.fail('unconfigured authentication launched')

    def test_real_namespace_seals_workspace_and_bash_and_publishes_exact_inode(self):
        # Docker CI runs the other cases; the host native job runs this one.
        required = os.environ.get('DEVFLOWER_REQUIRE_CLAUDE_SCOPE_TEST')
        if not scope_supported() and not required:
            self.skipTest('requires Bubblewrap and Landlock ABI 6')
        self.assertTrue(scope_supported(), 'required host isolation dependencies missing')
        probe = subprocess.run([BWRAP, '--unshare-user', '--disable-userns', '--ro-bind', '/', '/',
                                '--', '/usr/bin/true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode and not required:
            self.skipTest('user namespaces unavailable')
        self.assertEqual(probe.returncode, 0, 'required host namespace probe failed')
        with TemporaryDirectory() as folder:
            cwd, binding = self.fixture(folder)
            private = Path(folder) / 'canary'
            private.write_text('private')
            (cwd / 'link').symlink_to(private)
            (cwd / 'proc-link').symlink_to('/proc/self/environ')
            sid = str(uuid.uuid4())
            fake = {'ANTHROPIC_API_KEY': 'synthetic-noncredential'}
            with prepare_scope(binding, str(cwd), sid, False, fake) as scope:
                argv = scope.prefix[:scope.prefix.index('--restricted')]
                native = """
import os, pathlib, subprocess, sys
for path in (sys.argv[1], 'link', '/proc/self/environ', 'proc-link'):
    try: pathlib.Path(path).read_bytes()
    except OSError: pass
    else: raise AssertionError(path)
assert os.environ['ANTHROPIC_API_KEY'] == 'synthetic-noncredential'
script = '''
set -eu
test -z "$(printenv ANTHROPIC_API_KEY || true)"
for path in "$1" link proc-link /proc/1/environ; do
    if cat "$path" >/dev/null 2>&1; then exit 12; fi
done
if python3 -c 'import socket; socket.socket()' >/dev/null 2>&1; then exit 13; fi
printf shell > shell-output
'''
subprocess.run([os.environ['SHELL'], '-c', script, 'bash', sys.argv[1]], check=True)
pathlib.Path('native-output').write_text('native')
sys.stdin.read()
pathlib.Path(sys.argv[2]).write_text('native transcript\\n')
"""
                stage = Path(binding['config_root']) / '.devflower-scoped-history' / sid / (sid + '.jsonl')
                destination = argv[argv.index('claude') + 1] + '/' + sid + '.jsonl'
                process, _, err = execute([*argv, '-I', '-c', native, str(private), destination], cwd,
                                          binding['home'], input_text='start', timeout=15, isolate=False,
                                          backend='native-local', env_extra=scope.environment,
                                          pass_fds=scope.fds, process_attest=scope.attest)
                self.assertEqual(process['exit'], 0, err)
                self.assertNotIn(fake['ANTHROPIC_API_KEY'], argv)
                ino = stage.stat().st_ino
                scope.publish_history()
            self.assertEqual((cwd / 'native-output').read_text(), 'native')
            self.assertEqual((cwd / 'shell-output').read_text(), 'shell')
            published = next((Path(binding['config_root']) / 'projects').rglob(sid + '.jsonl'))
            self.assertEqual(published.stat().st_ino, ino)
            self.assertEqual(published.read_text(), 'native transcript\n')
            self.assertFalse(stage.exists())
