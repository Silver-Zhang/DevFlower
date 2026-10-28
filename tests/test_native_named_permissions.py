"""Named native permission profiles; no real Provider or credentials."""
import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
class NamedPermissionsTests(unittest.TestCase):
 def test_named_profile_has_explicit_deny_and_no_legacy_override(self):
  from studio.native_codex_exec import permission_profile_args
  import tomllib
  args=permission_profile_args(['/tmp/authority','/tmp/用户 auth','/tmp/😀 " quoted'],True)
  text=' '.join(args);self.assertIn('permissions.devflower.filesystem=',text);self.assertIn(':workspace_roots',text);self.assertIn('"/tmp/authority"',text);self.assertIn('"deny"',text);self.assertIn('default_permissions="devflower"',text);self.assertNotIn('sandbox_mode=',text)
  parsed=tomllib.loads('value='+args[1].split('=',1)[1])['value'];self.assertEqual(parsed['/tmp/😀 " quoted'],'deny');self.assertEqual(parsed[':workspace_roots'],'write')
  for value in ('relative','/tmp/../outside','/',None):
   with self.subTest(value=value),self.assertRaises(Blocked):permission_profile_args([value],True)
 def test_nested_deny_roots_fold_without_collapsing_siblings(self):
  import tomllib
  from studio.native_codex_exec import permission_profile_args
  args=permission_profile_args(['/tmp/用户 x/config/auth.json','/tmp/用户 x/config','/tmp/用户 x/config2/auth','/tmp/独立','/tmp/用户 x/config/a/b'],True)
  value=tomllib.loads('v='+args[1].split('=',1)[1])['v']
  self.assertEqual(value['/tmp/用户 x/config'],'deny');self.assertNotIn('/tmp/用户 x/config/auth.json',value);self.assertNotIn('/tmp/用户 x/config/a/b',value)
  self.assertEqual(value['/tmp/用户 x/config2/auth'],'deny');self.assertEqual(value['/tmp/独立'],'deny')
 def test_nested_deny_folding_does_not_resolve_symlink_aliases(self):
  import tomllib
  from studio.native_codex_exec import permission_profile_args
  with tempfile.TemporaryDirectory() as folder:
   target=Path(folder)/'target';target.mkdir();alias=Path(folder)/'alias';alias.symlink_to(target,target_is_directory=True)
   args=permission_profile_args([str(alias/'child'),str(alias),str(target)],False)
   value=tomllib.loads('v='+args[1].split('=',1)[1])['v']
   self.assertEqual(value[str(alias)],'deny');self.assertEqual(value[str(target)],'deny');self.assertNotIn(str(alias/'child'),value)
 def test_paths_come_from_host_authority_and_protect_running_source(self):
  from studio.native_codex_exec import protected_paths,exec_args
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder);rt=SimpleNamespace(storage=p/'state',native=p/'native',operator_file=p/'operator',policy_file=p/'policy',protected=[root,p/'authority',Path('/usr/bin/python3')],operator={'profiles':{'x':{'executable':'/usr/bin/python3','readonly_files':[[str(p/'credential'),'auth']]}},'native_local':{'codex':{'home':str(p/'home'),'config_root':str(p/'home/.codex')}}})
   binding=dict(config_root=str(p/'home/.codex'));paths=protected_paths(rt,binding)
   for path in (p/'state',p/'native',p/'operator',p/'policy',p/'authority',p/'credential',p/'home/.codex'):self.assertIn(str(path),paths)
   self.assertIn(str(root),paths);self.assertNotIn('/usr/bin/python3',paths)
   with self.assertRaises(Blocked):exec_args(dict(executable='/bin/true',role='reviewer',model='m',effort='high',native_interaction='ordinary'),[])
 def test_automatic_features_are_explicitly_disabled(self):
  from studio.native_codex_exec import FEATURES
  for feature in ('in_app_browser','hooks','plugins','apps','skill_mcp_dependency_install','multi_agent'):
   self.assertIs(FEATURES.get(feature),False,feature)
 def test_preexisting_protected_hardlink_blocks_without_reading_content(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);authority=base/'authority';authority.mkdir();secret=authority/'secret';secret.write_text('synthetic')
   check_protected_hardlinks([str(authority)])
   os.link(secret,base/'alias')
   with self.assertRaisesRegex(Blocked,'硬链接'):check_protected_hardlinks([str(authority)])
   self.assertTrue((base/'alias').exists())
 def test_all_aliases_inside_deny_roots_are_allowed_without_double_counting(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'protected';child=root/'history';child.mkdir(parents=True);first=child/'a';first.write_text('synthetic');os.link(first,child/'b')
   check_protected_hardlinks([str(root),str(child),str(root),str(first)])
   os.link(first,Path(folder)/'outside')
   with self.assertRaisesRegex(Blocked,'硬链接'):check_protected_hardlinks([str(root),str(child),str(root)])
 def test_many_internal_history_links_are_metadata_only(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'protected';root.mkdir()
   for n in range(127):
    first=root/('first-'+str(n));first.write_text('synthetic');os.link(first,root/('second-'+str(n)))
   with patch('builtins.open',side_effect=AssertionError('content read')):
    check_protected_hardlinks([str(root)])
 def test_protected_root_symlink_and_special_alias_fail_closed(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'protected';root.mkdir();alias=Path(folder)/'root-link';alias.symlink_to(root,target_is_directory=True)
   with self.assertRaises(Blocked):check_protected_hardlinks([str(alias)])
   fifo=root/'pipe';os.mkfifo(fifo);os.link(fifo,Path(folder)/'outside-fifo')
   with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
 def test_protected_link_change_during_scan_fails_closed(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'protected';root.mkdir();first=root/'a';first.write_text('synthetic');os.link(first,root/'b')
   real=os.fwalk;calls=[]
   def changed(*args,**kwargs):
    calls.append(1)
    if len(calls)==2:os.link(first,Path(folder)/'outside')
    yield from real(*args,**kwargs)
   with patch('studio.native_codex_exec.os.fwalk',side_effect=changed):
    with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
   (Path(folder)/'outside').unlink();calls.clear()
   def removed(*args,**kwargs):
    calls.append(1)
    if len(calls)==2:(root/'b').unlink()
    yield from real(*args,**kwargs)
   with patch('studio.native_codex_exec.os.fwalk',side_effect=removed):
    with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
 def test_nested_symlink_is_not_followed_or_counted_as_an_alias(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'protected';root.mkdir();first=root/'a';first.write_text('synthetic');os.link(first,root/'b')
   outside=Path(folder)/'outside';outside.mkdir();(outside/'extra').symlink_to(first)
   (root/'link').symlink_to(outside,target_is_directory=True)
   check_protected_hardlinks([str(root)])
   os.link(root/'link',Path(folder)/'outside-link',follow_symlinks=False)
   with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
 def test_protected_scan_refuses_unreadable_and_unbounded_roots(self):
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.native_codex_exec.os.fwalk',side_effect=PermissionError('synthetic')):
    with self.assertRaises(Blocked):check_protected_hardlinks([folder])
   for roots in (['/'],['relative'],[folder]*513):
    with self.assertRaises(Blocked):check_protected_hardlinks(roots)
 def test_sqlite_wal_disappearing_during_scan_is_not_a_link_failure(self):
  import os,sqlite3
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);db=root/'workbench.sqlite';conn=sqlite3.connect(db);self.addCleanup(conn.close)
   conn.execute('pragma journal_mode=wal');conn.execute('create table entries(v)');conn.commit();wal=Path(str(db)+'-wal');self.assertTrue(wal.exists())
   real=os.stat;changed=[]
   def stat_once(path,*a,**kw):
    if path==wal.name and not changed:
     changed.append(True);conn.execute('pragma wal_checkpoint(truncate)');conn.close();raise FileNotFoundError(2,'synthetic WAL vanished',path)
    return real(path,*a,**kw)
   with patch('studio.native_codex_exec.os.stat',side_effect=stat_once):check_protected_hardlinks([str(root)])
   self.assertTrue(changed)
 def test_live_sqlite_checkpoint_and_preflight_keep_unknown_alias_refusal(self):
  import os,sqlite3,threading
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);db=root/'workbench.sqlite';done=threading.Event();errors=[]
   def churn():
    try:
     for i in range(50):
      with sqlite3.connect(db,timeout=2) as conn:
       conn.execute('pragma journal_mode=wal');conn.execute('create table if not exists t(v)');conn.execute('insert into t values (?)',(i,));conn.commit();conn.execute('pragma wal_checkpoint(truncate)')
    except Exception as exc:errors.append(exc)
    finally:done.set()
   thread=threading.Thread(target=churn);thread.start();checks=0
   try:
    while not done.is_set():check_protected_hardlinks([str(root)]);checks+=1
   finally:thread.join(5)
   self.assertFalse(thread.is_alive());self.assertFalse(errors);self.assertGreater(checks,0)
   secret=root/'authority';secret.write_text('synthetic');outside=root.parent/(root.name+'-alias');os.link(secret,outside)
   try:
    with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
   finally:outside.unlink()
 def test_new_unlinked_state_file_during_scan_does_not_invalidate(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'existing').write_text('synthetic');real=os.fwalk;calls=[]
   def mutate(*args,**kw):
    calls.append(1)
    if len(calls)==2:(root/'new-state').write_text('synthetic')
    yield from real(*args,**kw)
   with patch('studio.native_codex_exec.os.fwalk',side_effect=mutate):check_protected_hardlinks([str(root)])
 def test_relinked_shared_file_and_replaced_root_are_rejected(self):
  import os
  from studio.native_codex_exec import check_protected_hardlinks
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder)/'authority';root.mkdir();first=root/'a';first.write_text('synthetic');os.link(first,root/'b');real=os.fwalk;calls=[]
   def relink(*args,**kw):
    calls.append(1)
    if len(calls)==2:(root/'b').unlink();os.link(first,root/'b')
    yield from real(*args,**kw)
   with patch('studio.native_codex_exec.os.fwalk',side_effect=relink):
    with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
   calls.clear()
   def replace(*args,**kw):
    calls.append(1)
    if len(calls)==2:root.rename(root.with_name('prior'));root.mkdir()
    yield from real(*args,**kw)
   with patch('studio.native_codex_exec.os.fwalk',side_effect=replace):
    with self.assertRaises(Blocked):check_protected_hardlinks([str(root)])
 def test_feature_check_rejects_unknown_or_enabled_flags(self):
  import time
  from studio.native_codex_exec_probe import _verify_disabled_features,_AUTOMATIC_DISABLED
  output='\n'.join(key+' stable false' for key in _AUTOMATIC_DISABLED).encode()
  with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=output,stderr=b'')):
   _verify_disabled_features('/bin/true','/tmp',{},time.monotonic()+5,lambda:False)
  with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=output.replace(b'hooks stable false',b'hooks stable true'),stderr=b'')):
   with self.assertRaisesRegex(ValueError,'hooks'):_verify_disabled_features('/bin/true','/tmp',{},time.monotonic()+5,lambda:False)
  with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b'')):
   with self.assertRaisesRegex(ValueError,'in_app_browser'):_verify_disabled_features('/bin/true','/tmp',{},time.monotonic()+5,lambda:False)
 def test_nine_field_matrix_requires_authority_and_auth_denied(self):
  from studio.native_codex_exec_probe import _sandbox_result
  base=[True,True,False,True,True,True,False,False,False,False,False,False,True,True,False,False,True]
  self.assertTrue(_sandbox_result(base,True,True))
  for index in (6,7,8,9,10,11,14,15):
   v=base.copy();v[index]=True;self.assertFalse(_sandbox_result(v,True,True))
  v=base.copy();v[16]=False;self.assertFalse(_sandbox_result(v,True,True))
  self.assertFalse(_sandbox_result([True,True,False,True,True,True,False,False,False],True,False))
 def test_probe_reports_real_cli_error(self):
  from studio.native_codex_exec_probe import require_native_write_capability
  with tempfile.TemporaryDirectory() as folder:
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=2,stdout=b'',stderr=b'error: invalid permission profile')):
    with self.assertRaisesRegex(Blocked,'invalid permission profile'):require_native_write_capability('/bin/true',folder,True)
   unsafe=b'[true,true,false,true,true,true,false,false,false,false,false,false,true,true,true,false,true]'
   with patch('studio.native_codex_exec_probe._run_command',return_value=SimpleNamespace(returncode=0,stdout=unsafe,stderr=b'')):
    with self.assertRaisesRegex(Blocked,'创建认证硬链接'):require_native_write_capability('/bin/true',folder,True)
 def test_model_tool_probe_requires_named_profile_and_real_tool_event(self):
  import json,time
  from urllib.request import Request,urlopen
  from studio.native_codex_exec_probe import _tool_boundary_probe
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);home=base/'home';work=base/'work';outside=base/'outside'
   for p in (home,work,outside):p.mkdir()
   program='print(json.dumps([True,True,False,True,True,True,False,False,False,False,False,False,True,True,False,False,True]))\n';marker='DFPROBE:[true, true, false, true, true, true, false, false, false, false, false, false, true, true, false, false, true]';argv=[]
   def fake(args,*,cwd,env,input_data,deadline,cancelled):
    argv.extend(args);entry=next(x for x in args if x.startswith('model_providers.fixture.base_url='));url=json.loads(entry.split('=',1)[1])+'/responses'
    for inputs in ([],[{'type':'function_call_output','call_id':'call','output':'Process exited with code 0\n'+marker}]):
     with urlopen(Request(url,data=json.dumps({'input':inputs}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=3) as response:response.read()
    event={'type':'item.completed','item':{'type':'command_execution','exit_code':0,'aggregated_output':marker}}
    return SimpleNamespace(returncode=0,stdout=(json.dumps(event)+'\n').encode(),stderr=b'')
   with patch('studio.native_codex_exec_probe._run_command',side_effect=fake):_tool_boundary_probe('/bin/true',base,home,work,outside,program,True,time.monotonic()+10,lambda:False,ordinary=True)
   self.assertIn('default_permissions="devflower"',argv);self.assertFalse(any(x.startswith('sandbox_mode=') for x in argv));self.assertIn(str(work/'authority'),str(argv));self.assertIn(str(base/'protected-native-home'),str(argv));self.assertNotIn(str(base/'protected-native-home/auth.json'),str(argv))
 def test_authority_change_during_preflight_never_sends_prompt(self):
  from studio.local_codex_adapter import LocalCodex
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  class Store:
   def get(self,*a):return {'data':{}}
   def check_before_spawn(self):raise Blocked('authority changed')
  with tempfile.TemporaryDirectory() as folder:
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[],native_interaction='ordinary',native_protected=[folder+'/authority'])
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability'),patch('orchestration.process.execute') as execute:
    with self.assertRaisesRegex(Blocked,'authority changed'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('not sent',folder,folder,store=Store(),run='r')
   execute.assert_not_called()
 def test_hardlink_created_during_capability_check_blocks_dispatch(self):
  import os
  from studio.local_codex_adapter import LocalCodex
  class RPC:
   def __init__(self,*a,**kw):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  with tempfile.TemporaryDirectory() as folder:
   secret=Path(folder)/'authority';secret.mkdir();(secret/'private').write_text('synthetic')
   binding=dict(executable='native',home=folder,config_root=folder,provider='codex',model='m',effort='high',role='reviewer',session_id=None,project='test',readonly_files=[],native_interaction='ordinary',native_protected=[str(secret)])
   def link(*a,**kw):os.link(secret/'private',Path(folder)/'alias')
   with patch('studio.local_codex_adapter.CodexRPC',RPC),patch('studio.native_codex_exec_probe.require_exec_capability'),patch('studio.native_codex_exec_probe.require_native_write_capability',side_effect=link),patch('orchestration.process.execute') as execute:
    with self.assertRaisesRegex(Blocked,'硬链接'):LocalCodex(binding,dict(step_timeout_seconds=10,max_log_bytes=10000)).start('not sent',folder,folder)
   execute.assert_not_called();self.assertTrue((Path(folder)/'alias').exists())
