"""Process-local guard on metadata entry points; no real shared CLI execution."""
import os,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import devflower_start
import devflower_native_terminal as terminal
from orchestration import adapters
from orchestration.common import cli_probe_env,Blocked
from studio.agent_extensions import _probe

class ProbeUpdateGuardTests(unittest.TestCase):
 def check_env(self,env):
  self.assertEqual(env['DISABLE_AUTOUPDATER'],'1');self.assertEqual(env['DISABLE_UPDATES'],'1')
 def test_guard_is_process_local_and_does_not_inherit_auth(self):
  before=dict(os.environ)
  with patch.dict(os.environ,ANTHROPIC_API_KEY='SYNTHETIC-MUST-NOT-INHERIT'):
   env=cli_probe_env('/SYNTHETIC/HOME');self.check_env(env);self.assertNotIn('ANTHROPIC_API_KEY',env);self.assertEqual(env['HOME'],'/SYNTHETIC/HOME')
  self.assertEqual(before,dict(os.environ))
 def test_public_start_metadata_passes_guard(self):
  def call(argv,**kw):self.check_env(kw['env']);return SimpleNamespace(returncode=0,stdout=b'synthetic metadata')
  with patch.object(devflower_start.subprocess,'run',side_effect=call):self.assertEqual(devflower_start.probe(['/SYNTHETIC/claude','--help'],Path('/SYNTHETIC/HOME')),'synthetic metadata')
 def test_doctor_version_help_and_auth_all_pass_guard(self):
  seen=[]
  def call(argv,**kw):
   self.check_env(kw['env']);seen.append(argv[1:]);return SimpleNamespace(returncode=0,stdout=b'{"loggedIn":true} --output-format --resume')
  with patch.object(adapters.subprocess,'run',side_effect=call):value=adapters.probe('/SYNTHETIC/claude','claude')
  self.assertEqual(seen,[['--version'],['--help'],['auth','status']]);self.assertTrue(value['available'])
 def test_extension_capability_probe_passes_guard(self):
  def call(argv,**kw):self.check_env(kw['env']);return SimpleNamespace(returncode=0,stdout=b'--agents JSON')
  _probe.cache_clear();self.addCleanup(_probe.cache_clear)
  with patch('studio.agent_extensions.subprocess.run',side_effect=call):self.assertTrue(_probe('/SYNTHETIC/claude','claude',1,1)['skills'])
 def test_terminal_help_passes_guard_before_native_launch(self):
  def call(argv,**kw):self.check_env(kw['env']);return SimpleNamespace(returncode=0,stdout=b'--strict-mcp-config --mcp-config --tools --permission-mode --settings')
  with tempfile.TemporaryDirectory() as folder,patch.object(terminal.subprocess,'run',side_effect=call):terminal.require_claude_terminal_tools(dict(executable='/SYNTHETIC/claude'),folder)
 def test_missing_terminal_capabilities_remain_blocked(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(terminal.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'--help')):
   with self.assertRaises(Blocked):terminal.require_claude_terminal_tools(dict(executable='/SYNTHETIC/claude'),folder)
 def test_timeout_remains_blocked(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(terminal.subprocess,'run',side_effect=subprocess.TimeoutExpired('synthetic',10)):
   with self.assertRaisesRegex(Blocked,'超时'):terminal.require_claude_terminal_tools(dict(executable='/SYNTHETIC/claude'),folder)
