"""Synthetic v3 ordinary Codex configuration identity and transport tests."""
import os
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from orchestration.common import Blocked, digest
from studio.model_registration import direct_environment_identity, profile_identity, ModelRegistration
from studio.preference_baseline import original, preference_only, worker_baseline
from studio.server import Conflict
from studio.native_local import key as session_key
import test_direct_preference_identity as fixtures
RAW=fixtures.RAW

VALUES=('STEPS_PROSE','STEPS_COMMANDS','STEPS_EXECUTION')

class DesktopIdentityTests(unittest.TestCase):
 setUp=fixtures.DirectPreferenceIdentityTests.setUp
 mutate_preferences=fixtures.DirectPreferenceIdentityTests.mutate_preferences
 check=fixtures.DirectPreferenceIdentityTests.check
 transport=fixtures.DirectPreferenceIdentityTests.transport
 assert_explicit=fixtures.DirectPreferenceIdentityTests.assert_explicit
 def desktop(self,mode,extra=''):
  self.config.write_bytes(RAW+('\n[desktop]\nconversationDetailMode="'+mode+'"\n'+extra).encode())
 def v3(self):
  self.b['direct_environment_version']=3
  self.b['direct_environment_sha256']=direct_environment_identity(self.b)
 def test_three_enum_values_before_and_during_preflight_keep_argv(self):
  self.v3();seen=[]
  for mode in VALUES:
   with self.subTest(mode=mode):
    self.config.write_bytes(RAW);self.v3();self.desktop(mode);self.check();_,_,argv=self.transport();self.assert_explicit(argv);seen.append(argv[0])
    self.config.write_bytes(RAW);self.v3();_,_,argv=self.transport(lambda:self.desktop(mode));self.assert_explicit(argv);seen.append(argv[0])
  self.assertTrue(all(argv==seen[0] for argv in seen))
  self.assertIn('--ignore-user-config',seen[0]);self.assertIn('--ignore-rules',seen[0]);self.assertIn('--strict-config',seen[0]);self.assertIn('approval_policy="never"',seen[0])
 def test_legacy_and_v2_reject_and_cannot_upgrade_pin(self):
  for version in (None,1,2):
   self.config.write_bytes(RAW);b=dict(self.b)
   if version is None:b.pop('direct_environment_version');b.pop('direct_environment_scope')
   else:b['direct_environment_version']=version
   b['direct_environment_sha256']=direct_environment_identity(b);self.desktop(VALUES[0])
   with self.subTest(version=version):
    with self.assertRaises(Conflict):self.check(b)
    with self.assertRaises(Blocked):self.transport(b=b)
    upgraded=dict(b,direct_environment_version=3,direct_environment_scope='native-codex-ordinary')
    with self.assertRaises(Conflict):self.check(upgraded)
    self.config.write_bytes(RAW)
    with self.assertRaises(Blocked):self.transport(lambda:self.desktop(VALUES[1]),b=b)
 def test_bad_desktop_and_sibling_changes_fail_closed(self):
  self.v3()
  for config in (RAW+b'\ndesktop=1\n',RAW+b'\n[desktop]\nconversationDetailMode="WRONG"\n',RAW+b'\n[desktop]\nconversationDetailMode=1\n',RAW+b'\n[desktop]\nconversationDetailMode=["STEPS_PROSE"]\n',RAW+b'\n[desktop]\nunknown=true\n',RAW+b'\n[desktop]\nconversationDetailMode="STEPS_PROSE"\nunknown=true\n'):
   # desktop=1 must be at top level, not inside the last TOML table.
   if config==RAW+b'\ndesktop=1\n':config=b'desktop=1\n'+RAW
   with self.subTest(config=config):
    self.config.write_bytes(config)
    with self.assertRaises((Blocked,Conflict)):self.check()
    with self.assertRaises(Blocked):self.transport()
 def test_security_controls_remain_bound(self):
  changes=[lambda:self.config.write_bytes(RAW.replace(b'example.invalid',b'other.invalid')),lambda:self.config.write_bytes(RAW.replace(b'read-only',b'workspace-write')),lambda:self.config.write_bytes(RAW.replace(b'"nested"',b'"different"')),lambda:self.auth.write_text('other'),lambda:self.code.write_text('other'),lambda:self.exe.write_text('other')]
  for change in changes:
   self.config.write_bytes(RAW);self.auth.write_text('synthetic-auth');self.code.write_text('synthetic-policy');self.exe.write_text('synthetic executable');self.v3();change()
   with self.subTest(change=change),self.assertRaises(Conflict):self.check()
 def test_readonly_config_remains_raw(self):
  self.b['readonly_files'].append([str(self.config),'.codex/config.toml']);self.v3();self.desktop(VALUES[1])
  with self.assertRaises(Conflict):self.check()
 def test_baseline_original_pin_and_worker_fd(self):
  operator={'native_local':{'codex':{'config_root':str(self.config_root)}},'dependencies':{str(self.config):digest(RAW)}}
  baseline=original(operator);self.assertEqual(baseline['raw'],RAW);self.assertEqual(baseline['pin'],digest(RAW))
  self.desktop(VALUES[2]);self.assertTrue(preference_only(baseline,str(self.config)));self.assertEqual(operator['dependencies'][str(self.config)],digest(RAW))
  env={};rt=SimpleNamespace(preference_baseline=baseline,assistant_settings=None)
  with worker_baseline(rt,env) as fds:
   self.assertEqual(len(fds),1)
   inherited=dict(env,DEVFLOWER_PINNED_CONFIG_FD=str(os.dup(fds[0])))
   with patch.dict(os.environ,inherited):restored=original(operator)
  self.assertEqual(restored['raw'],RAW);self.assertTrue(preference_only(restored,str(self.config)))
  self.desktop(VALUES[0],'unknown=true\n');self.assertFalse(preference_only(restored,str(self.config)))
  self.desktop('INVALID');self.assertFalse(preference_only(restored,str(self.config)))
  self.config.write_bytes(RAW+b'\n[desktop]\nunknown=true\n');pin=digest(self.config.read_bytes());operator['dependencies'][str(self.config)]=pin;typed=original(operator)
  self.config.write_bytes(RAW+b'\n[desktop]\nunknown=1\n');self.assertFalse(preference_only(typed,str(self.config)))
 def test_terminal_versions_retain_session_identity_and_old_hash(self):
  from native_protocol_mechanics import native_cli_argv
  self.config.write_bytes(RAW);v1=dict(self.b);v1.pop('direct_environment_version');v1.pop('direct_environment_scope');v2=dict(self.b);v3=dict(self.b,direct_environment_version=3)
  hashes=[direct_environment_identity(x) for x in (v1,v2,v3)];sid=[session_key(x) for x in (v1,v2,v3)]
  self.desktop(VALUES[0]);self.assertNotEqual(direct_environment_identity(v1),hashes[0]);self.assertNotEqual(direct_environment_identity(v2),hashes[1]);self.assertEqual(direct_environment_identity(v3),hashes[2]);self.assertEqual([session_key(x) for x in (v1,v2,v3)],sid)
  for b in (v1,v2,v3):
   argv=native_cli_argv(dict(b,native_interactive=True),b['session_id']);self.assertEqual(argv[argv.index('--model')+1],'explicit-model');self.assertIn('model_reasoning_effort="high"',argv)
 def test_unknown_version_and_nonordinary_rejected(self):
  for value in (4,True,'3'):
   with self.subTest(value=value),self.assertRaises(Blocked):direct_environment_identity(dict(self.b,direct_environment_version=value))
  self.v3()
  with self.assertRaises(Blocked):self.transport(b=dict(self.b,native_interaction=None))
 def test_runtime_issues_v3_and_explicit_v2_stays_v2(self):
  from studio.workbench_runtime import WorkbenchRuntime
  from studio.graph import Invalid
  profile=dict(provider='codex',model='explicit-model',effort='high',executable=str(self.exe),readonly_files=self.b['readonly_files'])
  class Flow:
   def get(self,*args):raise Invalid('no synthetic record')
  rt=SimpleNamespace(session_center=SimpleNamespace(workspace=lambda w:dict(path=str(self.root))),operator=dict(profiles={'p':profile},native_local={'codex':True}),flow=Flow(),native=self.root,policy=dict(models={}),policy_sha='policy',policy_file=self.code,validate_source=lambda *a,**k:None)
  native={k:v for k,v in self.b.items() if k not in ('direct_environment_version','direct_environment_scope','direct_environment_sha256','native_interaction')}
  node=dict(profile='p',model='explicit-model',effort='high',mode='Ask',direct_environment_version=999)
  with patch('studio.model_registration.ModelRegistration.choices',return_value={}),patch('studio.native_local.bind',return_value=native),patch('studio.agent_extensions.AgentExtensions.bind'):
   new=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True)['binding']
   old=WorkbenchRuntime.node_binding(rt,'w',node,admission=False,direct=True,direct_environment_version=2)['binding']
   self.assertEqual(new['direct_environment_version'],3);self.assertEqual(old['direct_environment_version'],2)
   self.desktop(VALUES[0]);self.check(new)
   with self.assertRaises(Conflict):self.check(old)
