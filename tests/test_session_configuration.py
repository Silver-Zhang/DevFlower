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
