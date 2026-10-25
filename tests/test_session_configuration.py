"""Adversarial synthetic boundaries. Real CLI/UI evidence is recorded separately."""
from pathlib import Path
import copy,json,time,unittest
from contextlib import contextmanager
from unittest.mock import patch
from test_workbench_runtime import fixture,structured_session
from orchestration.adapters import CLI
from orchestration.common import Blocked,canonical,digest
from studio.graph import Invalid
from studio.server import Conflict
from studio.native_preferences import inherit_claude_display
from studio.terminal_phase import terminal_phase
from studio.model_registration import ModelRegistration,profile_identity
from studio.workbench_sessions import WorkbenchSessions
from studio.session_batch import SessionBatch
from studio.configuration_assistant import ConfigurationAssistant,plan_graph,schema
from studio.workbench_examples import examples
from studio.model_admission import ModelAdmission

class NativePresentationTests(unittest.TestCase):
 def setUp(self):self.base,self.rt=fixture(self);self.root=self.rt.native/'test';self.root.mkdir(parents=True)
 def prefs(self,value,prior=None):
  src=self.base.root/'operator-prefs';(src/'.claude').mkdir(parents=True,exist_ok=True);(src/'.claude/settings.json').write_text('{}');(src/'.claude.json').write_text(json.dumps(value))
  if prior is not None:(self.root/'.claude.json').write_text(json.dumps(prior))
  self.binding=dict(home=str(self.root),provider='claude',readonly_files=[[str(src/'.claude/settings.json'),'.claude/settings.json']]);return src
 def test_only_completed_display_preferences_copied_not_auth_or_trust(self):
  src=self.prefs(dict(hasCompletedOnboarding=True,theme='dark',lastOnboardingVersion='1',projects={'private':{'hasTrustDialogAccepted':True}},apiKey='SECRET',permissions={'allow':['*']}));before=(src/'.claude.json').read_bytes()
  self.assertTrue(inherit_claude_display(self.binding,self.rt.native));self.assertEqual(json.loads((self.root/'.claude.json').read_text()),dict(hasCompletedOnboarding=True,theme='dark',lastOnboardingVersion='1'));self.assertEqual((src/'.claude.json').read_bytes(),before)
 def test_explicit_reset_is_respected(self):
  self.prefs(dict(hasCompletedOnboarding=True),dict(hasCompletedOnboarding=False));self.assertFalse(inherit_claude_display(self.binding,self.rt.native))
 def test_no_completed_source_does_not_skip_setup(self):
  self.prefs(dict(theme='dark'));self.assertFalse(inherit_claude_display(self.binding,self.rt.native));self.assertFalse((self.root/'.claude.json').exists())
 def test_outside_managed_home_not_written(self):
  self.prefs(dict(hasCompletedOnboarding=True));self.binding['home']=str(self.base.root);self.assertFalse(inherit_claude_display(self.binding,self.rt.native))
 def test_symlink_preferences_refused(self):
  self.prefs(dict(hasCompletedOnboarding=True));(self.root/'.claude.json').symlink_to(self.base.root/'elsewhere')
  with self.assertRaises((ValueError,Blocked)):inherit_claude_display(self.binding,self.rt.native)
 def test_theme_is_not_ready(self):self.assertEqual(terminal_phase('claude','Choose the text style that looks best\n> Auto',True)['phase'],'theme')
 def test_trust_or_auth_not_ready(self):
  for text,expected in [('Do you trust the files','trust'),('Please run /login','authentication')]:self.assertEqual(terminal_phase('claude',text,True)['phase'],expected)
 def test_late_auto_permission_offer_supersedes_ready_prompt(self):
  self.assertEqual(terminal_phase('claude','❯ \n high /effort\nMakeautomodeyourdefaultpermissionmode?\n❯Yes,setautomodeasmydefaultpermissionmode',True)['phase'],'permissions')
 def test_claude_native_terminal_pins_readonly_permission_settings(self):
  from devflower_native_terminal import native_cli_argv
  argv=native_cli_argv(dict(provider='claude',executable='/synthetic/claude',model='synthetic',effort='high'),'10000000-0000-4000-8000-000000000001');settings=json.loads(argv[argv.index('--settings')+1]);self.assertEqual(settings['permissions']['disableAutoMode'],'disable');self.assertEqual(settings['permissions']['disableBypassPermissionsMode'],'disable');self.assertEqual(argv[argv.index('--permission-mode')+1],'plan')
 def test_actual_cr_only_prompt_clears_dismissed_permission_offer(self):
  text='❯ \n high /effort\nMakeautomodeyourdefaultpermissionmode?\n❯Yes,setautomodeasmydefaultpermissionmode\nNo,keepmanualmode\r----\r❯\u00a0 \r----\r⏸ plan mode on (shift+tab to cycle) ● high /effort\r'
  self.assertEqual(terminal_phase('claude',text,True)['phase'],'ready')
 def test_codex_native_terminal_pins_version_without_startup_update(self):
  from devflower_native_terminal import native_cli_argv
  argv=native_cli_argv(dict(provider='codex',executable='/synthetic/codex',model='synthetic',effort='xhigh'),'10000000-0000-4000-8000-000000000001');self.assertIn('check_for_update_on_startup=false',argv);self.assertEqual(argv[argv.index('--model')+1],'synthetic')
 def test_actual_codex_upgrade_menu_is_not_agent_ready(self):
  text='loading Resuming session… ›AskCodextodoanything Updateavailable ›1.Updatenow(runs`npm install -g @openai/codex`) 2.Skip 3.Skipuntilnextversion entercontinue·escskip'
  self.assertEqual(terminal_phase('codex',text,True)['phase'],'update')
 def test_codex_actual_prompt_supersedes_dismissed_upgrade_menu(self):
  text='Updatenow(runs`npm install -g @openai/codex`) 3.Skipuntilnextversion\r›AskCodextodoanything'
  self.assertEqual(terminal_phase('codex',text,True)['phase'],'ready')
 def test_actual_ink_workspace_trust_screen_is_not_output_or_ready(self):
  self.assertEqual(terminal_phase('claude','Quicksafetycheck:Isthisaprojectyoucreatedoroneyoutrust?\r\n❯No,exit\r\nYes,Itrustthisfolder\r\nEntertoconfirm·Esctocancel',True)['phase'],'trust')
 def test_actual_later_prompt_supersedes_setup(self):self.assertEqual(terminal_phase('claude','Choose the text style\n❯ \n high /effort',True)['phase'],'ready')
 def test_output_without_prompt_is_not_ready(self):self.assertEqual(terminal_phase('claude','Welcome to Claude',True)['phase'],'output')
 def test_exited_not_ready(self):self.assertEqual(terminal_phase('codex','Ask Codex',False)['phase'],'closed')
 def test_conversation_accepts_plain_text_and_legacy_reply(self):
  for text in ['用户可读回答。','{"reply":"老会话"}']:
   self.assertTrue(CLI.conversation_report(text)['reply'])
  with self.assertRaises(Blocked):CLI.conversation_report('')
 def test_claude_native_envelope_plain_answer_keeps_real_session(self):
  sid='10000000-0000-4000-8000-000000000001';cli=CLI('claude',dict(conversation_format='text'),{})
  r=cli.collect(dict(exit=0,timeout=False,overflow=False,cancelled=False),json.dumps(dict(type='result',subtype='success',is_error=False,session_id=sid,result='可见回答')))
  self.assertEqual(r['report'],dict(reply='可见回答'));self.assertEqual(r['session_id'],sid)

class RegistrationTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.name='default-claude';self.p=self.rt.operator['profiles'][self.name];self.reg=ModelRegistration(self.rt);self.catalog()
 def catalog(self,**changes):
  value=dict(id=self.name,profile_sha256=digest(json.dumps(self.p,sort_keys=True).encode()),environment_sha256=profile_identity(self.p),at=time.time(),error=None,models=[dict(id='discovered-model',efforts=['high','low'])]);value.update(changes);self.rt.flow.put('model-discovery',self.name,value)
 def prepare(self,**changes):return self.reg.prepare(dict(workspace=self.w,profile=self.name,model='discovered-model',effort='high',**changes))
 def confirm(self,p):return self.reg.confirm(p['id'],p['sha256'],'Actual explicit UI selection (synthetic test)')
 def test_discovered_model_can_be_configured_but_does_not_gain_execution(self):
  r=self.confirm(self.prepare());self.assertFalse(r['execution_allowed']);b=self.rt.node_binding(self.w,dict(profile=self.name,model='discovered-model',effort='high',mode='Ask'),admission=False)
  with self.assertRaises(Blocked):ModelAdmission(self.rt).require(b['binding'],b['cwd'])
 def test_effort_not_reported_rejected(self):
  with self.assertRaises(Invalid):self.reg.prepare(dict(workspace=self.w,profile=self.name,model='discovered-model',effort='max'))
 def test_stale_catalog_rejected(self):
  self.catalog(at=time.time()-301)
  with self.assertRaises(Blocked):self.prepare()
 def test_catalog_auth_rotation_rejected(self):
  self.catalog(environment_sha256='other')
  with self.assertRaises(Blocked):self.prepare()
 def test_catalog_failure_rejected(self):
  self.catalog(error='HTTP 402')
  with self.assertRaises(Blocked):self.prepare()
 def test_omitted_model_not_fabricated(self):
  self.catalog(models=[])
  with self.assertRaises(Invalid):self.prepare()
 def test_catalog_change_during_confirmation_rejected(self):
  p=self.prepare();self.catalog(at=time.time()-1)
  with self.assertRaises(Conflict):self.confirm(p)
 def test_registration_cas_prevents_old_configuration_overwrite(self):
  a=self.prepare();b=self.prepare();self.confirm(a)
  with self.assertRaises(Conflict):self.confirm(b)
 def test_registered_binding_stale_after_new_registration(self):
  self.confirm(self.prepare());binding=self.rt.node_binding(self.w,dict(profile=self.name,model='discovered-model',effort='high',mode='Ask'),admission=False)['binding'];self.confirm(self.prepare())
  with self.assertRaises(Blocked):self.reg.require(binding)

class BatchTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt);self.batch=SessionBatch(self.manager)
  self.paths=[];self.handles=[]
  for label in ('batch-A','batch-B'):
   path,row=structured_session(self.base,self.rt,label);self.paths.append((path,path.read_bytes()));self.handles.append(row['handle']);self.rt.flow.change('run',label+'-run',dict(draft_id='workflow-shared'))
 def prepare(self):return self.batch.prepare(dict(workspace=self.w,workflow='draft:workflow-shared'))
 def confirm(self,p):return self.batch.confirm(p['id'],p['sha256'],'Move shown fixed members to recoverable trash')
 def test_whole_group_to_trash_keeps_native_history_and_runs(self):
  self.assertEqual(self.confirm(self.prepare())['trashed'],2)
  for h in self.handles:self.assertEqual(self.rt.flow.get('session-meta',h)['collection'],'trash')
  for path,raw in self.paths:self.assertEqual(path.read_bytes(),raw)
  self.assertEqual(len(self.rt.flow.list('run')),2)
 def test_changed_member_prevents_all_sideeffects(self):
  p=self.prepare();self.rt.flow.change('run','batch-B-run',dict(draft_id='other'))
  with self.assertRaises(Conflict):self.confirm(p)
  self.assertEqual(self.rt.flow.list('session-meta'),[])
 def test_busy_group_does_not_prepare(self):
  with patch.object(self.manager,'busy',return_value=True),self.assertRaises(Blocked):self.prepare()
 def test_external_writer_starting_after_home_lease_prevents_whole_batch(self):
  import os,subprocess,sys
  p=self.prepare();original_lease=__import__('studio.session_batch',fromlist=['lease']).lease;children=[]
  @contextmanager
  def occupied_after_lease(path):
   with original_lease(path):
    if not children:
     home=p['data']['members'][0]['home']
     child=subprocess.Popen([sys.executable,'-B','-c',"import ctypes,sys;ctypes.CDLL(None).prctl(15,ctypes.c_char_p(b'claude'),0,0,0);print('ready',flush=True);sys.stdin.read(1)"],env=dict(os.environ,HOME=home),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE);children.append(child)
     self.assertEqual(child.stdout.readline(),b'ready\n')
    yield
  try:
   with patch('studio.session_batch.lease',side_effect=occupied_after_lease),self.assertRaises(Blocked):self.confirm(p)
   self.assertEqual(self.rt.flow.list('session-meta'),[])
   for path,raw in self.paths:self.assertEqual(path.read_bytes(),raw)
  finally:
   for child in children:
    child.communicate(b'x',timeout=5);self.assertEqual(child.returncode,0)
 def test_own_held_home_lease_is_not_an_external_writer(self):
  p=self.prepare();original=self.manager.native_process_busy;held=[]
  def inspect(home,sid):
   held.append(self.rt.session_center._busy(home));return original(home,sid)
  with patch.object(self.manager,'native_process_busy',side_effect=inspect):result=self.confirm(p)
  self.assertEqual(result['trashed'],2);self.assertIn(True,held)
 def test_meta_cas_change_does_not_trash_other_member(self):
  p=self.prepare();self.rt.flow.put('session-meta',self.handles[1],dict(id=self.handles[1],workspace=self.w,alias='New'))
  with self.assertRaises(Conflict):self.confirm(p)
  self.assertTrue(all(r.get('collection')!='trash' for r in self.rt.flow.list('session-meta')))
 def test_native_turn_finishes_between_member_check_and_lease_is_rejected(self):
  from orchestration.store import lease
  p=self.prepare();changed=[False]
  @contextmanager
  def changed_lease(path):
   if not changed[0]:
    changed[0]=True
    with self.paths[0][0].open('a') as stream:stream.write(json.dumps(dict(type='user',sessionId='10000000-0000-4000-8000-000000000001',message=dict(role='user',content='New native activity')))+'\n')
   with lease(path):yield
  with patch('studio.session_batch.lease',changed_lease),self.assertRaises(Conflict):self.confirm(p)
  self.assertEqual(self.rt.flow.list('session-meta'),[])
 def test_new_queued_writer_prevents_group_mutation(self):
  p=self.prepare();home=p['data']['members'][0]['home'];self.rt.db.put('session-operation','queued',dict(state='QUEUED',binding=dict(home=home)))
  with self.assertRaises(Blocked):self.confirm(p)
  self.assertEqual(self.rt.flow.list('session-meta'),[])

class ConfigurationTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.ai=ConfigurationAssistant(self.rt);self.g=examples(self.rt.operator['profiles'])[0];self.choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in self.rt.operator['profiles'].items()}
  self.choice_patch=patch.object(ConfigurationAssistant,'choices',return_value=self.choices);self.choice_patch.start();self.addCleanup(self.choice_patch.stop)
 def plan(self):
  p=self.rt.operator['profiles']['default-codex'];nodes=[]
  for id,kind in [('start','start'),('analyze','agent'),('result','result')]:nodes.append(dict(id=id,kind=kind,name=id,profile='default-codex' if kind=='agent' else '',model=p['model'] if kind=='agent' else '',effort=p['effort'] if kind=='agent' else '',mode='Ask',initial_prompt='理解任务',round_prompt='回答问题',handoff_prompt='简洁回答'))
  return dict(version=1,name='AI proposed',summary='Data only',nodes=nodes,edges=[dict(source='start',target='analyze'),dict(source='analyze',target='result')])
 def proposal(self):return self.ai.external(dict(workspace=self.w,graph=self.g,plan=self.plan()))
 def test_plan_strict_schema_builds_actual_executable_graph(self):self.assertEqual(plan_graph(self.plan(),self.choices)['nodes'][1]['profile'],'default-codex')
 def test_plan_cannot_supply_permission_or_endpoint(self):
  for field in ('permission','command','endpoint','authorization'):
   plan=self.plan();plan['nodes'][1][field]='unsafe'
   with self.subTest(field=field),self.assertRaises(Blocked):plan_graph(plan,self.choices)
 def test_unregistered_model_rejected(self):
  plan=self.plan();plan['nodes'][1]['model']='fake'
  with self.assertRaises(Invalid):plan_graph(plan,self.choices)
 def test_cycle_rejected(self):
  plan=self.plan();plan['edges'].append(dict(source='analyze',target='start'))
  with self.assertRaises(Invalid):plan_graph(plan,self.choices)
 def test_oversized_plan_rejected(self):
  plan=self.plan();plan['summary']='x'*6001
  with self.assertRaises(Blocked):plan_graph(plan,self.choices)
 def test_blank_canvas_unknown_fields_and_unbound_edges_rejected(self):
  for g in [dict(version=3,nodes=[],edges=[],credentials='SECRET'),dict(version=3,nodes=[],edges=[dict(source='a',target='b')]),dict(version=3,nodes='bad',edges=[])]:
   with self.subTest(g=g),self.assertRaises(Invalid):self.ai._base(dict(workspace=self.w,graph=g))
 def test_external_proposal_never_claims_native_receipt_or_executes(self):
  with patch('studio.configuration_assistant.CLI.start',side_effect=AssertionError('Do not invoke')):v=self.proposal()
  self.assertEqual(v['source_kind'],'external-unverified');self.assertIsNone(v['receipt']);self.assertEqual(self.rt.flow.list('run'),[])
 def test_apply_changes_draft_only_not_permissions_or_execution(self):
  p=self.proposal();v=self.ai.apply(dict(workspace=self.w,id=p['id'],graph=self.g,answer='Apply shown draft'))
  self.assertTrue(v['applied_to_draft_only']);self.assertFalse(v['execution_started']);self.assertFalse(v['permissions_granted']);self.assertEqual(self.rt.flow.list('draft'),[])
 def test_edit_during_generation_prevents_overwrite(self):
  p=self.proposal();g=copy.deepcopy(self.g);g['name']='User edited'
  with self.assertRaises(Conflict):self.ai.apply(dict(workspace=self.w,id=p['id'],graph=g,answer='Apply'))
 def test_source_change_prevents_apply(self):
  p=self.proposal();(self.base.source/'new.py').write_text('changed')
  with self.assertRaises(Conflict):self.ai.apply(dict(workspace=self.w,id=p['id'],graph=self.g,answer='Apply'))
 def test_configuration_context_has_no_workspace_or_write_tools(self):
  p=self.rt.operator['profiles']['default-codex'];v=ModelAdmission(self.rt).select(dict(workspace=self.w,profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask',context='configuration'))
  self.assertNotEqual(v['cwd'],str(self.base.source));self.assertEqual(v['binding']['tools'],[]);self.assertTrue(v['binding']['no_tools']);self.assertEqual(v['binding']['role'],'reviewer')
 def test_configuration_context_cannot_request_writes(self):
  p=self.rt.operator['profiles']['default-codex']
  with self.assertRaises(Invalid):ModelAdmission.validate_request(dict(workspace=self.w,profile='default-codex',model=p['model'],effort=p['effort'],mode='Agent',context='configuration'))
 def generation(self):
  p=self.rt.operator['profiles']['default-codex'];selection=dict(workspace=self.w,profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask',context='configuration');s=ModelAdmission(self.rt).select(selection);key='config-operation';self.rt.db.put('session-operation',key,dict(self.ai._base(dict(workspace=self.w,graph=self.g)),id=key,state='QUEUED',configuration=True,diagnostic=True,created_at=time.time(),profile='default-codex',permission='read',binding=s['binding'],cwd=s['cwd'],cancel_requested=False,goal='Only configure'))
  gate=ModelAdmission(self.rt);scope=gate.scope(s['binding'],s['cwd']);self.rt.db.put('model-admission',gate.key(s['binding'],s['cwd']),dict(id='config-valid',state='VERIFIED',verified_at=time.time(),scope=scope));return key
 def generated(self,**kw):
  key=self.generation();report=self.plan()
  def response(*args,**kwargs):
   if kw.get('cancel'):self.rt.db.update('session-operation',key,dict(cancel_requested=True))
   if kw.get('invalid'):report['authorization']=True
   if kw.get('newer'):
    row=self.rt.db.get('session-operation',key);gate=ModelAdmission(self.rt);k=gate.key(row['binding'],row['cwd']);cert=self.rt.db.get('model-admission',k);self.rt.db.put('model-admission',k,dict(cert,id='newer-valid'))
   if kw.get('fail'):raise Blocked('Native HTTP 502')
   return dict(report=report,session_id='10000000-0000-4000-8000-000000000001',requested_model='test-model',requested_effort='high',resolved_model='unknown',resolved_effort='unknown',process=dict(exit=0,timeout=False,overflow=False,cancelled=False))
  with patch.object(ModelAdmission,'renew',return_value=not kw.get('revoke')),patch('studio.configuration_assistant.CLI.start',side_effect=response):self.ai.execute(key)
  return self.rt.db.get('session-operation',key)
 def test_native_generation_persists_receipt_but_does_not_run_workflow(self):
  v=self.generated();self.assertEqual(v['state'],'COMPLETED');self.assertEqual(v['receipt']['session_id'],'10000000-0000-4000-8000-000000000001');self.assertEqual(self.rt.flow.list('run'),[])
 def cancellation_before_dispatch(self,at_lease=False):
  key=self.generation();row=self.rt.db.get('session-operation',key);gate=ModelAdmission(self.rt);scope_key=gate.key(row['binding'],row['cwd']);before=gate.db.get('model-admission',scope_key)
  if not at_lease:self.rt.db.update('session-operation',key,dict(cancel_requested=True))
  original_lease=__import__('studio.configuration_assistant',fromlist=['lease']).lease
  @contextmanager
  def cancel_after_lease(path):
   with original_lease(path):
    if at_lease:self.rt.db.update('session-operation',key,dict(cancel_requested=True))
    yield
  with patch('studio.configuration_assistant.lease',side_effect=cancel_after_lease),patch('studio.configuration_assistant.CLI.start',side_effect=AssertionError('Cancelled work must not reach native adapter')) as call:self.ai.execute(key)
  call.assert_not_called();self.assertEqual(self.rt.db.get('session-operation',key)['state'],'CANCELLED');self.assertEqual(gate.db.get('model-admission',scope_key),before)
 def test_queued_cancellation_never_calls_model_or_revokes_certificate(self):self.cancellation_before_dispatch()
 def test_cancellation_during_lease_acquisition_never_calls_model(self):self.cancellation_before_dispatch(at_lease=True)
 def test_cancel_at_result_publication_never_becomes_completed(self):self.assertEqual(self.generated(cancel=True)['state'],'CANCELLED')
 def test_revocation_during_generation_prevents_proposal_publication(self):self.assertEqual(self.generated(revoke=True)['state'],'FAILED')
 def test_native_invalid_structured_output_fails_closed(self):self.assertEqual(self.generated(invalid=True)['state'],'FAILED')
 def test_native_failure_never_produces_proposal(self):
  v=self.generated(fail=True);self.assertEqual(v['state'],'FAILED');self.assertNotIn('proposed_graph',v)

 def test_failed_native_configuration_revokes_its_exact_certificate(self):
  v=self.generated(fail=True);self.assertEqual(v['state'],'FAILED');gate=ModelAdmission(self.rt);self.assertFalse(gate.status(v['binding'],v['cwd'])['execution_allowed'])
  with self.assertRaises(Blocked):gate.require(v['binding'],v['cwd'])
 def test_late_failure_cannot_revoke_newer_valid_certificate(self):
  v=self.generated(fail=True,newer=True);gate=ModelAdmission(self.rt);self.assertTrue(gate.status(v['binding'],v['cwd'])['execution_allowed']);self.assertEqual(self.rt.db.get('model-admission',gate.key(v['binding'],v['cwd']))['id'],'newer-valid')

 def test_completion_between_status_reads_returns_same_completed_payload(self):
  key=self.generation();graph=plan_graph(self.plan(),self.choices)
  def publish(*a):
   self.rt.db.update('session-operation',key,dict(state='COMPLETED',proposed_graph=graph,receipt=dict(session_id='ACTUAL-SYNTHETIC-RECEIPT')));return dict(state='COMPLETED')
  with patch.object(self.rt.session_center,'operation',side_effect=publish):v=self.ai.status(self.w,key)
  self.assertEqual(v['state'],'COMPLETED');self.assertEqual(v['graph'],graph);self.assertEqual(v['receipt']['session_id'],'ACTUAL-SYNTHETIC-RECEIPT')
 def test_cancelled_generation_has_no_applyable_graph(self):
  v=self.generated(cancel=True);self.assertIsNone(self.ai.status(self.w,v['id'])['graph'])
 def test_native_failure_revokes_captured_scope_even_when_dependency_rotates(self):
  key=self.generation();v=self.rt.db.get('session-operation',key);dep=self.base.root/'overlay.json';dep.write_text('{}');b=dict(v['binding'],readonly_files=[[str(dep),'.codex/auth.json']]);self.rt.db.update('session-operation',key,dict(binding=b));gate=ModelAdmission(self.rt);k=gate.key(b,v['cwd']);cert=dict(id='before-rotation',state='VERIFIED',verified_at=time.time(),scope=gate.scope(b,v['cwd']));self.rt.db.put('model-admission',k,cert)
  def fail(*a,**kw):dep.write_text('{"changed":true}');raise Blocked('Synthetic native failure during configuration rotation')
  with patch('studio.configuration_assistant.CLI.start',side_effect=fail):self.ai.execute(key)
  dep.write_text('{}');self.assertFalse(gate.status(b,v['cwd'])['execution_allowed']);self.assertEqual(self.rt.db.get('model-admission',k)['state'],'FAILED')
 def test_common_failure_reference_uses_original_key_not_rotated_environment(self):
  p=self.rt.operator['profiles']['default-codex'];s=ModelAdmission(self.rt).select(dict(workspace=self.w,profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask'));b=s['binding'];gate=ModelAdmission(self.rt);key=gate.key(b,s['cwd']);scope=gate.scope(b,s['cwd']);self.rt.db.put('model-admission',key,dict(id='original',state='VERIFIED',verified_at=time.time(),scope=scope));ref=gate.require(b,s['cwd'])
  with patch('studio.model_registration.profile_identity',return_value='rotated-environment'):self.assertTrue(gate.invalidate_verification(b,s['cwd'],ref))
  self.assertFalse(gate.status(b,s['cwd'])['execution_allowed']);self.assertEqual(self.rt.db.get('model-admission',key)['scope'],scope)

 def test_conversation_preflight_rejection_cannot_revoke_newer_certificate(self):
  p=self.rt.operator['profiles']['default-codex'];s=self.rt.node_binding(self.w,dict(profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask'),admission=False);g=ModelAdmission(self.rt);k=g.key(s['binding'],s['cwd']);self.rt.db.put('model-admission',k,dict(id='original',state='VERIFIED',verified_at=time.time(),scope=g.scope(s['binding'],s['cwd'])))
  permit=self.rt.session_center.prepare(dict(workspace=self.w,handle='new',profile='default-codex',prompt='User task',permission='read',model=p['model'],effort=p['effort'],mode='Ask'));self.rt.db.put('session-operation','preflight',dict(permit['data'],id='preflight',state='QUEUED',created_at=time.time(),cancel_requested=False))
  newer=dict(id='newer-after-queued',state='VERIFIED',verified_at=time.time(),scope=g.scope(s['binding'],s['cwd']));self.rt.db.put('model-admission',k,newer);(self.base.source/'README.md').write_text('User edited after preview')
  with patch('studio.session_center.CLI.start',side_effect=AssertionError('Must not call native agent')) as call:self.rt.session_center.execute('preflight')
  call.assert_not_called();self.assertEqual(self.rt.db.get('session-operation','preflight')['state'],'FAILED');self.assertEqual(self.rt.db.get('model-admission',k),newer)

class ProcessOccupancyTests(unittest.TestCase):
 def setUp(self):self.base,self.rt=fixture(self);self.manager=WorkbenchSessions(self.rt)
 def probe(self,exists,identity):
  from types import SimpleNamespace
  class Gone:
   name='99999999'
   def __truediv__(self,key):
    def missing():raise FileNotFoundError('Synthetic process exited between proc reads')
    return SimpleNamespace(read_text=lambda:'codex',read_bytes=missing)
   def exists(self):return exists
  with patch.object(self.rt.session_center,'_busy',return_value=False),patch('studio.workbench_sessions.Path.glob',return_value=[Gone()]),patch('studio.workbench_sessions.process_identity',return_value=identity,create=True):return self.manager.busy(dict(home=str(self.rt.native/'test-scope')),'10000000-0000-4000-8000-000000000001')
 def test_exited_unrelated_process_cannot_make_every_session_busy(self):self.assertFalse(self.probe(False,None))
 def test_live_uninspectable_process_still_fails_closed(self):self.assertTrue(self.probe(True,dict(state='R')))
 def test_zombie_cannot_be_a_native_writer(self):self.assertFalse(self.probe(True,dict(state='Z')))

class AtomicConfigurationDispatchTests(unittest.TestCase):
 def setUp(self):
  self.helper=ConfigurationTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups);self.rt=self.helper.rt;self.ai=self.helper.ai
 def certificate(self,key):
  row=self.rt.db.get('session-operation',key);g=ModelAdmission(self.rt);return g,g.key(row['binding'],row['cwd'])
 def assert_cancelled_without_proposal(self,key):
  row=self.rt.db.get('session-operation',key);self.assertEqual(row['state'],'CANCELLED')
  for field in ('plan','proposed_graph','receipt'):self.assertNotIn(field,row)
  if row.get('dispatch_step'):self.assertEqual(self.rt.db.get('session-step',row['dispatch_step'])['status'],'CANCELLED')
 def response(self,key):
  row=self.rt.db.get('session-operation',key)
  return dict(report=self.helper.plan(),session_id='10000000-0000-4000-8000-000000000001',requested_model=row['binding']['model'],requested_effort=row['binding']['effort'],process=dict(exit=0,timeout=False,overflow=False,cancelled=False))
 def test_cancel_wins_immediately_before_atomic_dispatch_claim(self):
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k);claim=self.ai._claim_dispatch
  def cancel_then_claim(key):self.rt.db.update('session-operation',key,dict(cancel_requested=True));return claim(key)
  with patch.object(self.ai,'_claim_dispatch',side_effect=cancel_then_claim),patch('studio.configuration_assistant.CLI.start') as call:self.ai.execute(key)
  call.assert_not_called();self.assert_cancelled_without_proposal(key);self.assertEqual(g.db.get('model-admission',k),before);self.assertNotIn('dispatch_step',self.rt.db.get('session-operation',key))
 def test_claim_is_durable_and_cannot_be_replayed(self):
  key=self.helper.generation();self.rt.db.update('session-operation',key,dict(state='STARTING'));self.assertTrue(self.ai._claim_dispatch(key));row=self.rt.db.get('session-operation',key);step=self.rt.db.get('session-step',row['dispatch_step']);self.assertEqual(step['kind'],'CONFIGURATION_DISPATCH');self.assertEqual(step['status'],'INTENT')
  with self.assertRaises(Blocked):self.ai._claim_dispatch(key)
  self.rt.db.update('session-operation',key,dict(cancel_requested=True));self.assertFalse(self.ai._claim_dispatch(key));self.assertEqual(len(self.rt.db.listing('session-step')),1)
 def test_cancel_after_claim_is_rejected_before_real_adapter_process_intent(self):
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k)
  def cancelled_adapter(*args,**kw):
   self.rt.db.update('session-operation',key,dict(cancel_requested=True));args[-2].intent(key,'PROCESS',dict(argv=['synthetic-never-spawned']))
   self.fail('Cancelled request must not create process intent')
  with patch('studio.configuration_assistant.CLI.start',side_effect=cancelled_adapter):self.ai.execute(key)
  self.assert_cancelled_without_proposal(key);self.assertEqual(g.db.get('model-admission',k),before);self.assertEqual([r['kind'] for r in self.rt.db.listing('session-step')],['CONFIGURATION_DISPATCH'])
 def test_cancelled_synthetic_success_cannot_renew_or_publish(self):
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k)
  def cancelled_response(*args,**kw):self.rt.db.update('session-operation',key,dict(cancel_requested=True));return self.response(key)
  with patch('studio.configuration_assistant.CLI.start',side_effect=cancelled_response),patch.object(ModelAdmission,'renew') as renew:self.ai.execute(key)
  renew.assert_not_called();self.assert_cancelled_without_proposal(key);self.assertEqual(g.db.get('model-admission',k),before)
 def test_renew_checks_cancellation_in_same_certificate_transaction(self):
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k);row=self.rt.db.get('session-operation',key);verification=g.require(row['binding'],row['cwd']);self.rt.db.update('session-operation',key,dict(cancel_requested=True))
  self.assertFalse(g.renew(row['binding'],row['cwd'],self.response(key),verification,unless_cancelled=key));self.assertEqual(g.db.get('model-admission',k),before)
 def test_cancel_after_completed_renewal_still_discards_proposal(self):
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k);original=ModelAdmission.renew
  def renew_before_cancel(gate,*args,**kw):
   result=original(gate,*args,**kw);self.assertTrue(result);self.rt.db.update('session-operation',key,dict(cancel_requested=True));return result
  with patch('studio.configuration_assistant.CLI.start',side_effect=lambda *a,**kw:self.response(key)),patch.object(ModelAdmission,'renew',new=renew_before_cancel):self.ai.execute(key)
  self.assert_cancelled_without_proposal(key);self.assertNotEqual(g.db.get('model-admission',k)['id'],before['id'])
 def test_separate_store_cannot_cancel_between_claim_read_and_commit(self):
  import threading
  from studio.usable_store import Storage
  from studio.configuration_assistant import ConfigurationJournal
  key=self.helper.generation();self.rt.db.update('session-operation',key,dict(state='STARTING'));other=Storage(self.rt.db.path.parent);started=threading.Event();done=threading.Event();errors=[]
  def cancel():
   started.set()
   try:other.update('session-operation',key,dict(cancel_requested=True))
   except Exception as e:errors.append(e)
   finally:done.set()
  writer=threading.Thread(target=cancel);original=self.rt.db.db;test=self
  class Cursor:
   def __init__(self,cursor):self.cursor=cursor
   def fetchone(self):
    row=self.cursor.fetchone();writer.start();test.assertTrue(started.wait(1));test.assertFalse(done.wait(.04),'Separate SQLite writer must wait for dispatch transaction');return row
  class Connection:
   def __init__(self,db):self.db=db
   def __enter__(self):self.db.__enter__();return self
   def __exit__(self,*a):return self.db.__exit__(*a)
   def execute(self,sql,params=()):
    cursor=self.db.execute(sql,params)
    return Cursor(cursor) if sql.startswith('SELECT data FROM objects') and "kind='session-operation'" in sql else cursor
  @contextmanager
  def connection():
   with original() as db:yield Connection(db)
  with patch.object(self.rt.db,'db',side_effect=connection):self.assertTrue(self.ai._claim_dispatch(key))
  writer.join(3);self.assertFalse(writer.is_alive());self.assertEqual(errors,[]);self.assertTrue(done.is_set());row=self.rt.db.get('session-operation',key);self.assertTrue(row['cancel_requested']);self.assertTrue(row['dispatch_step'])
  with self.assertRaises(Blocked):ConfigurationJournal(self.rt.db,key).intent(key,'AGENT_REQUEST',dict(requested_session='10000000-0000-4000-8000-000000000001'))
  self.assertTrue(self.ai._finish_cancelled(key));self.assert_cancelled_without_proposal(key)

 def test_cancellation_committed_before_failure_revocation_preserves_certificate(self):
  from studio.usable_store import Storage
  key=self.helper.generation();g,k=self.certificate(key);before=g.db.get('model-admission',k);other=Storage(self.rt.db.path.parent);original=ModelAdmission.invalidate;observed=[]
  def cancel_before_certificate_transaction(gate,*args,**kw):
   other.update('session-operation',key,dict(cancel_requested=True));observed.append(kw.get('unless_cancelled'));return original(gate,*args,**kw)
  with patch('studio.configuration_assistant.CLI.start',side_effect=Blocked('Synthetic pre-spawn failure')),patch.object(ModelAdmission,'invalidate',new=cancel_before_certificate_transaction):self.ai.execute(key)
  self.assertEqual(observed,[key]);self.assert_cancelled_without_proposal(key);self.assertEqual(g.db.get('model-admission',k),before)
 def test_pre_spawn_failure_without_cancellation_still_revokes_captured_certificate(self):
  key=self.helper.generation();g,k=self.certificate(key)
  with patch('studio.configuration_assistant.CLI.start',side_effect=Blocked('Synthetic unsupported native configuration')):self.ai.execute(key)
  self.assertEqual(self.rt.db.get('session-operation',key)['state'],'FAILED');self.assertEqual(g.db.get('model-admission',k)['state'],'FAILED')
 def test_cancelled_started_native_process_still_revokes_uncertain_certificate(self):
  key=self.helper.generation();g,k=self.certificate(key)
  def cancelled_started(*args,**kw):
   args[-2].process_started=True;self.rt.db.update('session-operation',key,dict(cancel_requested=True));raise Blocked('Synthetic owned process cancelled after start')
  with patch('studio.configuration_assistant.CLI.start',side_effect=cancelled_started):self.ai.execute(key)
  self.assert_cancelled_without_proposal(key);self.assertEqual(g.db.get('model-admission',k)['state'],'FAILED')
