"""Frozen Skill resources and synthetic transport; no native CLI/model calls."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from orchestration.common import Blocked,canonical,digest
from studio.agent_extensions import AgentExtensions,skill_files
from studio.native_skill_pack import native_skill_pack
from native_protocol_mechanics import LocalCodex
from studio.graph import Invalid

SID='00000000-0000-4000-8000-000000000001'
RAW=('\n'.join(json.dumps(x) for x in [dict(type='thread.started',thread_id=SID),dict(type='item.completed',item=dict(type='agent_message',text='synthetic')),dict(type='turn.completed',usage={})])+'\n').encode()
class Flow:
 def __init__(self):self.records={}
 def put(self,kind,key,value):self.records[kind,key]=value
 def get(self,kind,key):
  if (kind,key) not in self.records:raise Invalid('not found')
  return self.records[kind,key]
class NativeSkillTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir();self.storage=self.root/'storage';self.storage.mkdir();self.home=self.root/'home';self.home.mkdir();self.codex=self.home/'.codex';self.codex.mkdir();self.art=self.storage/'artifacts';self.art.mkdir()
  self.rt=SimpleNamespace(storage=self.storage,flow=Flow(),operator={'profiles':{'p':dict(provider='codex',executable='/synthetic')},'native_local':{'codex':{'home':str(self.home),'config_root':str(self.codex)}}},session_center=SimpleNamespace(workspace=lambda w:dict(path=str(self.work))))
  self.source=self.work/'.agents/skills/check';self.source.mkdir(parents=True);(self.source/'SKILL.md').write_text('---\nname: check\ndescription: Synthetic checklist\n---\nRead the reference.\n');(self.source/'references').mkdir();(self.source/'references/a.txt').write_text('fixture reference');(self.source/'scripts').mkdir();(self.source/'scripts/check.py').write_text('print("fixture")')
  self.base=dict(provider='codex',execution_backend='native-local',runtime_profile='p',executable='/synthetic',home=str(self.home),config_root=str(self.codex),model='fixture',effort='high',role='reviewer',execution_mode='Ask',project='w',session_id=None,conversation_format='text',readonly_files=[],native_interaction='ordinary',native_protected=[str(self.storage)],subagents='disabled')
 def bind(self):
  with patch('studio.agent_extensions.capabilities',return_value={'skills':True,'subagents':True}),patch('studio.agent_extensions.managed_capability',return_value=True),patch('pathlib.Path.home',return_value=self.home):
   ext=AgentExtensions(self.rt);catalog=ext.catalog('w','p');self.assertTrue(catalog['capabilities']['subagents']);row=next(x for x in catalog['skills'] if x['name']=='check')
   b=dict(self.base);ext.bind('w',dict(skills=[row['id']]),b)
  return b
 def test_real_full_package_is_frozen_and_cleaned(self):
  b=self.bind();before={p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
  with native_skill_pack(b,str(self.work),[]) as pack:
   entry=pack.entries[0];root=Path(entry['root']);identity=pack.identity;self.assertEqual((root/'references/a.txt').read_text(),'fixture reference');self.assertEqual((root/'scripts/check.py').read_text(),'print("fixture")')
   self.assertEqual(entry['files'],{k:digest(v) for k,v in before.items()});self.assertEqual(entry['sha256'],digest(canonical(entry['files'])))
   self.assertIn(entry['entry'],pack.prompt('task'));self.assertNotEqual(root.parent,self.storage)
   self.assertEqual((root/'SKILL.md').stat().st_mode&0o777,0o400)
  self.assertFalse(root.exists());self.assert_retained(pack.root);self.assertEqual((pack.root.stat().st_dev,pack.root.stat().st_ino),identity);self.assertEqual(pack.cleanup_info['status'],'retained_empty');self.assertEqual(before,{p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()});self.assertEqual(before,skill_files(Path(b['skills'][0]['path'])))
 def assert_retained(self,root):
  self.assertTrue(root.is_dir());self.assertEqual(list(root.iterdir()),[]);self.assertEqual(root.stat().st_uid,os.getuid());self.assertEqual(root.stat().st_mode&0o777,0o700);self.addCleanup(root.rmdir)
 def test_two_sources_are_distinct_and_cannot_override(self):
  b=self.bind();source2=self.work/'.agents/skills/second';source2.mkdir();(source2/'SKILL.md').write_text('---\nname: second\ndescription: Synthetic second\n---\nOther\n')
  with patch('studio.agent_extensions.capabilities',return_value={'skills':True,'subagents':True}),patch('pathlib.Path.home',return_value=self.home):
   ext=AgentExtensions(self.rt);rows=ext.catalog('w','p')['skills'];ids=[x['id'] for x in rows if x['name'] in ('check','second')];ext.bind('w',dict(skills=ids),b)
  with native_skill_pack(b,str(self.work),[]) as pack:
   self.assertEqual(len(pack.entries),2);self.assertNotEqual(pack.entries[0]['root'],pack.entries[1]['root'])
 def test_other_providers_and_subagent_stay_disabled(self):
  self.rt.operator['profiles']['c']={'provider':'claude','executable':'/synthetic'}
  self.rt.operator['native_local']['claude']={'home':str(self.home),'config_root':str(self.home/'.claude')}
  with patch('studio.agent_extensions.capabilities',return_value={'skills':True,'subagents':True}):
   result=AgentExtensions(self.rt).catalog('w','c')
  self.assertFalse(result['capabilities']['skills']);self.assertFalse(result['capabilities']['subagents'])
  with self.assertRaises(Blocked):AgentExtensions(self.rt).bind('w',dict(skills=['a'*64]),dict(self.base,provider='claude',runtime_profile='c'))
  with self.assertRaises(Blocked):AgentExtensions(self.rt).bind('w',dict(skills=[],subagents='readonly'),dict(self.base))
 def test_unverified_capability_does_not_advertise_skills(self):
  with patch('studio.agent_extensions.capabilities',return_value={'skills':False,'subagents':False,'reason':'synthetic unavailable'}):
   result=AgentExtensions(self.rt).catalog('w','p')
  self.assertFalse(result['capabilities']['skills']);self.assertFalse(result['capabilities']['subagents']);self.assertEqual(result['skills'],[])
 def test_source_or_snapshot_drift_and_known_secret_rejected(self):
  b=self.bind();(self.source/'references/a.txt').write_text('changed')
  with self.assertRaises(Blocked):
   with native_skill_pack(b,str(self.work),[]):pass
  (self.source/'references/a.txt').write_text('fixture reference')
  with self.assertRaises(Blocked):
   with native_skill_pack(b,str(self.work),['fixture reference']):pass
  (Path(b['skills'][0]['path'])/'SKILL.md').write_text('tampered')
  with self.assertRaises(Blocked):
   with native_skill_pack(b,str(self.work),[]):pass
 def test_resource_pack_mutation_and_cancel_cleanup(self):
  b=self.bind();root=None
  with self.assertRaises(Blocked):
   with native_skill_pack(b,str(self.work),[]) as pack:
    root=pack.root;(root/pack.entries[0]['id']/'SKILL.md').chmod(0o600);pack.verify()
  self.assert_retained(root)
  with self.assertRaisesRegex(Blocked,'cancel'):
   with native_skill_pack(b,str(self.work),[]) as pack:
    root=pack.root;raise Blocked('cancel')
  self.assert_retained(root)
 def test_unsafe_parent_and_links_refused(self):
  b=self.bind()
  for roots in ([str(self.work)],[str(self.storage)]):
   with self.assertRaises(Blocked):
    with native_skill_pack(b,str(self.work),[],roots=roots):pass
  (self.source/'references/a.txt').unlink();(self.source/'references/a.txt').symlink_to(self.source/'SKILL.md')
  with self.assertRaises((Blocked,ValueError)):
   with native_skill_pack(b,str(self.work),[]):pass
 def test_hardlink_private_config_and_oversize_are_refused(self):
  for change in ('hardlink','private','oversize'):
   with self.subTest(change=change):
    source=self.work/'.agents/skills/unsafe';source.mkdir();(source/'SKILL.md').write_text('---\nname: unsafe\ndescription: Synthetic\n---\nFixture\n')
    if change=='hardlink':os.link(source/'SKILL.md',source/'alias')
    elif change=='private':(source/'config.toml').write_text('synthetic')
    else:(source/'huge').write_bytes(b'x'*524289)
    with self.assertRaises(Blocked):skill_files(source)
    import shutil;shutil.rmtree(source)
 def test_extra_pack_directory_and_unknown_versions_fail_closed(self):
  b=self.bind()
  with self.assertRaises(Blocked):
   with native_skill_pack(b,str(self.work),[]) as pack:
    (Path(pack.entries[0]['root'])/'unexpected').mkdir();pack.verify()
  with self.assertRaises(Blocked):
   with native_skill_pack(dict(b,provider='claude'),str(self.work),[]):pass
  with self.assertRaises(Blocked):
   with native_skill_pack(dict(b,subagents='readonly'),str(self.work),[]):pass
 def test_modified_pack_after_synthetic_send_fails_and_cleans_up(self):
  b=self.bind();sent=[];roots=[]
  class RPC:
   def __init__(self,*a):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  def execute(argv,*a,**kw):
   sent.append(True);root=Path(kw['input_text'].split('资源根: ')[1].split('\n')[0]);roots.append(root.parent)
   (root/'references/a.txt').chmod(0o600);(root/'references/a.txt').write_text('changed');kw['on_stdout'](RAW)
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  with ExitStack() as s:
   s.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC));s.enter_context(patch('studio.native_codex_exec_probe.require_exec_capability'));s.enter_context(patch('studio.native_codex_exec_probe.require_native_write_capability'));s.enter_context(patch('studio.native_codex_exec.check_protected_hardlinks_isolated'));s.enter_context(patch('orchestration.process.execute',side_effect=execute))
   with self.assertRaises(Blocked):LocalCodex(b,dict(step_timeout_seconds=5,max_log_bytes=100000)).start('task',str(self.work),self.art)
  self.assertEqual(len(sent),1);self.assert_retained(roots[0])
 def test_actual_prompt_receipt_and_explicit_arguments_use_one_synthetic_send(self):
  b=self.bind();events=[];observed={}
  class RPC:
   def __init__(self,*a):pass
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def request(self,*a):return {'config':{}}
  class Store:
   def get(self,run):return {'data':{'cancel_requested':False}}
   def intent(self,run,kind,data):events.append(kind);return 'op'
   def finish(self,*a,**kw):pass
   def visible_request(self,prompt):observed['visible']=prompt
   def input_sent(self,operation,size,sha):observed.update(sent_size=size,sent_sha=sha)
  def execute(argv,*a,**kw):
   observed['argv']=argv;observed['prompt']=kw['input_text'];observed['count']=observed.get('count',0)+1
   kw['store'].input_sent('synthetic',len(kw['input_text'].encode()),digest(kw['input_text'].encode()))
   self.assertIn('fixture reference',Path(observed['prompt'].split('资源根: ')[1].split('\n')[0],'references/a.txt').read_text());kw['on_stdout'](RAW)
   return dict(exit=0,timeout=False,overflow=False,cancelled=False),RAW,b''
  with ExitStack() as s:
   s.enter_context(patch('studio.local_codex_adapter.CodexRPC',RPC));s.enter_context(patch('studio.native_codex_exec_probe.require_exec_capability'));s.enter_context(patch('studio.native_codex_exec_probe.require_native_write_capability'));s.enter_context(patch('studio.native_codex_exec.check_protected_hardlinks_isolated'));s.enter_context(patch('orchestration.process.execute',side_effect=execute))
   result=LocalCodex(b,dict(step_timeout_seconds=5,max_log_bytes=100000)).start('user task',str(self.work),self.art,store=Store(),run='r')
  self.assertEqual(observed['count'],1);self.assertEqual(observed['prompt'],observed['visible']);self.assertEqual(observed['sent_sha'],digest(observed['visible'].encode()));self.assertEqual(observed['sent_size'],len(observed['visible'].encode()));self.assertIn('skills.include_instructions=false',observed['argv']);self.assertIn('skills.bundled.enabled=false',observed['argv']);self.assertEqual(result['skills'][0]['sha256'],b['skills'][0]['sha256']);self.assertEqual(events,['AGENT_REQUEST']);self.assertFalse(Path(result['skills'][0]['root']).exists());self.assertEqual(result['skill_cleanup']['status'],'retained_empty');self.assert_retained(Path(result['skill_cleanup']['path']))
