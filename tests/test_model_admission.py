"""Synthetic adversarial admission checks. Real CLI acceptance is recorded separately."""
import copy,json,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio.model_admission import ModelAdmission,TTL
from studio.model_catalog import ModelCatalog
from studio.workbench_sessions import WorkbenchSessions
from orchestration.common import Blocked,canonical,digest
from studio.graph import Invalid
from studio.server import Conflict

class AdmissionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=False);self.gate=ModelAdmission(self.rt);self.name='default-codex';self.p=self.rt.operator['profiles'][self.name];self.w=self.base.project['id']
  self.node=dict(profile=self.name,provider='codex',model=self.p['model'],effort=self.p['effort'],mode='Ask',permission='read')
 def selected(self,node=None):return self.rt.node_binding(self.w,node or self.node,admission=False)
 def request(self,**changes):return dict(workspace=self.w,**{k:v for k,v in dict(self.node,**changes).items() if k!='provider'})
 def permit(self,**changes):return self.gate.prepare(self.request(**changes))
 def metadata(self,b,cwd,method,params):
  if method=='model/list':return dict(data=[dict(model=self.p['model'],supportedReasoningEfforts=[dict(reasoningEffort=self.p['effort'])])])
  if method=='account/read':return dict(account=dict(type='synthetic'))
  return dict(logged_in=True)
 def response(self,cli,prompt,cwd,artifacts,schema,journal,key):
  nonce=prompt.rsplit(': ',1)[1]
  return dict(report={'reply':nonce},requested_model=cli.binding['model'],requested_effort=cli.binding['effort'],session_id='10000000-0000-4000-8000-000000000001',process=dict(exit=0,timeout=False,overflow=False,cancelled=False),resolved_effort='unknown',resolved_model='unknown')
 def verify(self,permit=None,side_effect=None):
  permit=permit or self.permit()
  with patch.object(WorkbenchSessions,'rpc',self.metadata),patch('studio.model_admission.CLI.start',autospec=True,side_effect=side_effect or self.response):
   return self.gate.verify(permit['id'],permit['sha256'],'Synthetic test authorization')
 def test_registration_is_not_execution_admission(self):
  row=ModelCatalog(WorkbenchSessions(self.rt)).get(self.name,self.p)['models'][0]
  self.assertTrue(row['operator_registered']);self.assertFalse(row['discovered']);self.assertFalse(row['execution_allowed'])
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,self.node)
 def test_fresh_directory_omits_default_old_p1_rejected(self):
  manager=WorkbenchSessions(self.rt)
  with patch.object(manager,'rpc',side_effect=[dict(data=[dict(model='other')]),dict(account=dict(type='synthetic'))]):catalog=ModelCatalog(manager).get(self.name,self.p,True)
  self.assertTrue(catalog['discovery_complete']);default=next(m for m in catalog['models'] if m['id']==self.p['model']);self.assertTrue(default['operator_registered']);self.assertFalse(default['discovered']);self.assertFalse(default['execution_allowed'])
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,self.node)
 def test_discovered_unregistered_does_not_gain_permission(self):
  with patch.object(WorkbenchSessions,'rpc',side_effect=[dict(data=[dict(model='unapproved')]),dict(account={'type':'synthetic'})]):row=ModelCatalog(WorkbenchSessions(self.rt)).get(self.name,self.p,True)['models'][0]
  self.assertTrue(row['discovered']);self.assertFalse(row['operator_registered']);self.assertFalse(row['execution_allowed'])
  with self.assertRaises(Invalid):self.gate.prepare(self.request(model='unapproved'))
 def test_registration_unsupported_by_actual_invocation_stays_blocked(self):
  with self.assertRaisesRegex(Blocked,'unsupported'):
   self.verify(side_effect=Blocked('unsupported model from synthetic native transport'))
  selected=self.selected();self.assertFalse(self.gate.status(selected['binding'],selected['cwd'])['execution_allowed'])
  self.assertEqual(self.rt.flow.list('run'),[]);self.assertTrue(all(x['state']=='FAILED' for x in self.rt.db.listing('session-operation')))
 def test_successful_exact_native_verification_admits(self):
  self.assertTrue(self.verify()['execution_allowed']);self.assertEqual(self.rt.node_binding(self.w,self.node)['binding']['model'],self.p['model'])
 def test_omitted_alias_can_only_use_explicit_equivalent_native_verification(self):
  def omitted(b,c,m,p):return dict(data=[dict(model='other')]) if m=='model/list' else dict(account={'type':'synthetic'})
  permit=self.permit()
  with patch.object(WorkbenchSessions,'rpc',side_effect=omitted),patch('studio.model_admission.CLI.start',autospec=True,side_effect=self.response) as cli:
   result=self.gate.verify(permit['id'],permit['sha256'],'Synthetic authorized equivalent check')
  self.assertTrue(result['execution_allowed']);self.assertEqual(cli.call_count,1)
 def test_effort_mismatch_native_metadata_rejects_before_call(self):
  def mismatch(b,c,m,p):return dict(data=[dict(model=self.p['model'],supportedReasoningEfforts=[dict(reasoningEffort='different')])]) if m=='model/list' else dict(account={'type':'synthetic'})
  permit=self.permit()
  with patch.object(WorkbenchSessions,'rpc',side_effect=mismatch),patch('studio.model_admission.CLI.start') as cli:
   with self.assertRaisesRegex(Blocked,'推理强度'):self.gate.verify(permit['id'],permit['sha256'],'Synthetic authorization')
  cli.assert_not_called()
 def test_profile_switch_does_not_share_certificate(self):
  self.verify();self.rt.operator['profiles']['second']=copy.deepcopy(self.p)
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,dict(self.node,profile='second'))
 def test_mode_switch_requires_verification(self):
  self.verify()
  for mode in ('Plan','Agent'):
   with self.subTest(mode=mode),self.assertRaises(Blocked):self.rt.node_binding(self.w,dict(self.node,mode=mode,permission='read'))
 def test_model_and_effort_not_matched_by_similar_name(self):
  self.verify()
  for change in (dict(model=self.p['model']+'[1m]'),dict(effort=self.p['effort']+'er')):
   with self.subTest(change=change),self.assertRaises(Invalid):self.rt.node_binding(self.w,dict(self.node,**change))
 def test_timeout_and_tool_scope_change_invalidates(self):
  self.verify()
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,dict(self.node,timeout=301))
  b=self.selected()['binding'];self.assertFalse(self.gate.status(dict(b,tools=['different']),str(self.base.source))['execution_allowed'])
 def test_expired_certificate_and_future_timestamp_not_trusted(self):
  self.verify();s=self.selected();status=self.gate.status(s['binding'],s['cwd'])
  for now in (status['verified_at']+TTL,status['verified_at']-1):
   with patch('studio.model_admission.time.time',return_value=now):self.assertFalse(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])
 def test_catalog_cache_and_legacy_success_never_create_certificate(self):
  self.rt.flow.put('model-catalog','codex',dict(models=[dict(id=self.p['model'],efforts=[self.p['effort']])]))
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,self.node)
 def test_real_call_failure_revokes_and_retains_success_history(self):
  self.verify();s=self.selected();self.gate.invalidate(s['binding'],s['cwd']);self.assertFalse(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])
  self.assertEqual({v['state'] for v in self.rt.db.listing('model-admission-event')},{'VERIFIED','FAILED'})
 def test_configuration_change_rejects_before_using_certificate(self):
  self.verify();self.rt.operator_file.write_text('{}')
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,self.node)
 def test_policy_change_worker_recheck_rejects(self):
  self.verify();s=self.selected();self.rt.policy_file.write_text('{}')
  with self.assertRaises(Blocked):self.gate.require(s['binding'],s['cwd'])
 def test_nonce_mismatch_and_failed_receipts_never_certify(self):
  for field,value in [('report',{'reply':'wrong'}),('requested_effort','wrong'),('process',dict(exit=1)),('process',dict(exit=0,timeout=True)),('session_id',None)]:
   def bad(*args):r=self.response(*args);r[field]=value;return r
   with self.subTest(field=field,value=value),self.assertRaises(Blocked):self.verify(side_effect=bad)
 def test_verify_requires_confirmation_and_single_use(self):
  permit=self.permit()
  with self.assertRaises((Blocked,Invalid)):self.gate.verify(permit['id'],permit['sha256'],'')
  self.verify(permit)
  with self.assertRaises(Conflict):self.verify(permit)
 def test_prepare_and_status_have_no_native_side_effects(self):
  with patch('studio.model_admission.CLI.start') as cli:
   self.permit();self.gate.selection_status(self.request())
  cli.assert_not_called();self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_probe_uses_dedicated_empty_source_home_and_readonly_flag(self):
  def inspect(cli,prompt,cwd,artifacts,schema,journal,key):
   self.assertTrue(cli.binding['admission_probe']);self.assertTrue(Path(cwd).is_relative_to(self.rt.storage));self.assertNotEqual(Path(cwd),self.base.source);self.assertTrue(Path(cli.binding['home']).is_relative_to(self.rt.storage));self.assertNotIn('task',prompt.lower())
   return self.response(cli,prompt,cwd,artifacts,schema,journal,key)
  self.verify(side_effect=inspect);self.assertEqual(self.rt.db.listing('chat-profile'),[])
 def test_claude_uses_actual_cli_not_proxy_listing_as_proof(self):
  p=self.rt.operator['profiles']['default-claude'];permit=self.gate.prepare(self.request(profile='default-claude',model=p['model'],effort=p['effort']))
  with patch('studio.model_catalog.proxy_models',return_value=[dict(id=p['model'],efforts=[],source='proxy /v1/models')]),patch.object(WorkbenchSessions,'rpc',return_value=dict(logged_in=True)),patch('studio.model_admission.CLI.start',autospec=True,side_effect=self.response) as cli:
   self.assertTrue(self.gate.verify(permit['id'],permit['sha256'],'Synthetic authorization')['execution_allowed'])
  self.assertEqual(cli.call_args.args[0].binding['provider'],'claude');self.assertEqual(cli.call_count,1)

 def test_launch_rechecks_certificate_after_preview(self):
  from studio.workbench_examples import examples
  # Seed the second provider only through the same synthetic native boundary.
  self.verify()
  p=self.rt.operator['profiles']['default-claude']
  with patch('studio.model_catalog.proxy_models',return_value=[dict(id=p['model'],efforts=[],source='proxy /v1/models')]):
   self.verify(self.permit(profile='default-claude',model=p['model'],effort=p['effort']))
  permit=self.rt.prepare_flow(dict(workspace=self.w,graph=examples(self.rt.operator['profiles'])[0],task='Synthetic no spawn after revoked admission'))
  selected=self.selected();self.gate.invalidate(selected['binding'],selected['cwd'])
  with patch.object(self.rt,'spawn') as spawn,self.assertRaises(Blocked):self.rt.launch_flow(permit['id'],permit['sha256'],'Synthetic authorized run')
  spawn.assert_not_called();self.assertEqual(self.rt.flow.list('run'),[])
 def test_actual_session_call_failure_revokes_certificate(self):
  self.verify();center=self.rt.session_center
  permit=center.prepare(dict(workspace=self.w,handle='new',profile=self.name,prompt='Synthetic failure',permission='read',model=self.p['model'],effort=self.p['effort'],mode='Ask'))
  with patch.object(center,'_spawn'):
   op=center.launch(permit['id'],permit['sha256'],'Synthetic authorized call')
  with patch('studio.session_center.CLI.start',side_effect=Blocked('Synthetic real-call boundary failure')):center.execute(op['id'])
  self.assertEqual(center.operation(op['id'],self.w)['state'],'FAILED')
  with self.assertRaises(Blocked):self.rt.node_binding(self.w,self.node)
 def test_current_logged_out_evidence_overrides_prior_verification(self):
  self.verify();self.rt.flow.put('auth-probe',self.name,dict(id=self.name,at=time.time(),logged_in=False))
  selected=self.selected_without_auth()
  with self.assertRaises(Blocked):self.gate.require(selected['binding'],selected['cwd'])
 def selected_without_auth(self):
  return dict(binding=dict(self.p,runtime_profile=self.name,execution_mode='Ask',role='reviewer',tools=['native'],timeout_seconds=300,isolation='bwrap',external_sandbox=True,admission_policy=self.rt.policy_sha,admission_policy_file=str(self.rt.policy_file)),cwd=str(self.base.source))
 def test_permission_unknown_and_browser_endpoint_are_rejected(self):
  for change in (dict(permission='sudo'),dict(model='unknown'),dict(effort='unknown'),dict(timeout=1),dict(endpoint='https://unapproved.invalid')):
   with self.subTest(change=change),self.assertRaises(Invalid):self.gate.prepare(self.request(**change))

 def test_transport_and_process_environment_changes_invalidate(self):
  self.verify();selected=self.selected();b=selected['binding'];cwd=selected['cwd']
  self.assertFalse(self.gate.status(dict(b,buffered_tool_transport=True),cwd)['execution_allowed'])
  with patch.dict('os.environ',{'HTTPS_PROXY':'http://different-profile-proxy.invalid:443'}):self.assertFalse(self.gate.status(b,cwd)['execution_allowed'])

 def native_receipt(self):
  return dict(process=dict(exit=0,timeout=False,overflow=False,cancelled=False),requested_model=self.p['model'],requested_effort=self.p['effort'],session_id='10000000-0000-4000-8000-000000000001')
 def test_long_successful_call_renews_from_its_valid_start_not_old_catalog(self):
  self.verify();s=self.selected();v=self.gate.require(s['binding'],s['cwd'])
  with patch('studio.model_admission.time.time',return_value=v['verified_at']+TTL+30):
   self.assertTrue(self.gate.renew(s['binding'],s['cwd'],self.native_receipt(),v));self.assertTrue(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])
 def test_late_success_cannot_rearm_revoked_or_reverified_configuration(self):
  self.verify();s=self.selected();v=self.gate.require(s['binding'],s['cwd']);self.gate.invalidate(s['binding'],s['cwd'])
  self.assertFalse(self.gate.renew(s['binding'],s['cwd'],self.native_receipt(),v));self.assertFalse(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])
  self.verify();self.assertFalse(self.gate.renew(s['binding'],s['cwd'],self.native_receipt(),v));self.assertTrue(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])
 def test_failed_check_does_not_leave_previous_success_usable(self):
  self.verify()
  with self.assertRaises(Blocked):self.verify(side_effect=Blocked('Synthetic new native failure'))
  s=self.selected();self.assertFalse(self.gate.status(s['binding'],s['cwd'])['execution_allowed'])

 def test_inflight_verification_cannot_overwrite_concurrent_revocation(self):
  selected=self.selected()
  def revoked(cli,*args):
   self.gate.invalidate(selected['binding'],selected['cwd'],'concurrent-native-call-failed')
   return self.response(cli,*args)
  with self.assertRaisesRegex(Blocked,'迟到回执'):
   self.verify(side_effect=revoked)
  status=self.gate.status(selected['binding'],selected['cwd'])
  self.assertFalse(status['execution_allowed']);self.assertEqual(self.rt.db.get('model-admission',status['key'])['state'],'FAILED')

 def automatic(self,permit):
  return self.gate.verify(permit['id'],permit['sha256'],'Normal composite confirmation; no manual retry',reuse_verified=True)
 def test_automatic_failed_or_operator_revoked_scope_has_no_native_probe(self):
  s=self.selected()
  for reason in ('native-call-failed','operator-revoked'):
   with self.subTest(reason=reason):
    self.gate.invalidate(s['binding'],s['cwd'],reason);before=self.rt.db.get('model-admission',self.gate.key(s['binding'],s['cwd']))
    permit=self.permit()
    with patch.object(WorkbenchSessions,'rpc') as metadata,patch('studio.model_admission.CLI.start') as cli:
     with self.assertRaisesRegex(Blocked,'自动检查不会重试'):self.automatic(permit)
    cli.assert_not_called();metadata.assert_not_called()
    self.assertEqual(self.rt.db.get('model-admission',before['key']),before)
    self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_revocation_between_status_prepare_and_verify_is_not_retry_intent(self):
  s=self.selected()
  self.assertEqual(self.gate.selection_status(self.request())['status'],'unverified')
  self.gate.invalidate(s['binding'],s['cwd'],'revoke-after-status')
  before_prepare=self.permit()
  self.gate.invalidate(s['binding'],s['cwd'],'revoke-after-prepare')
  with patch.object(WorkbenchSessions,'rpc') as metadata,patch('studio.model_admission.CLI.start') as cli:
   with self.assertRaises(Blocked):self.automatic(before_prepare)
  cli.assert_not_called();metadata.assert_not_called()
  self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_revoke_before_starting_cas_wins_for_missing_and_expired_proofs(self):
  original=self.gate._publish
  for timeout,previous in ((301,'missing'),(302,'expired')):
   with self.subTest(previous=previous):
    s=self.selected(dict(self.node,timeout=timeout));key=self.gate.key(s['binding'],s['cwd'])
    if previous=='expired':self.rt.db.put('model-admission',key,dict(id='e'*32,state='VERIFIED',verified_at=time.time()-TTL,scope=self.gate.scope(s['binding'],s['cwd'])))
    permit=self.permit(timeout=timeout)
    def publish(event,**kw):
     if event['state']=='STARTING':self.gate.invalidate(s['binding'],s['cwd'],'concurrent-operator-revoked')
     return original(event,**kw)
    with patch.object(self.gate,'_publish',publish),patch.object(WorkbenchSessions,'rpc') as metadata,patch('studio.model_admission.CLI.start') as cli:
     with self.assertRaisesRegex(Blocked,'未发起原生调用'):self.automatic(permit)
    cli.assert_not_called();metadata.assert_not_called()
    self.assertEqual(self.rt.db.get('model-admission',key)['reason'],'concurrent-operator-revoked')
  self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_first_check_expired_check_and_valid_reuse_remain_distinct(self):
  with patch.object(WorkbenchSessions,'rpc',self.metadata),patch('studio.model_admission.CLI.start',autospec=True,side_effect=self.response) as cli:
   self.assertTrue(self.automatic(self.permit())['execution_allowed']);self.assertEqual(cli.call_count,1)
   self.assertTrue(self.automatic(self.permit())['execution_allowed']);self.assertEqual(cli.call_count,1)
   s=self.selected();key=self.gate.key(s['binding'],s['cwd']);proof=self.rt.db.get('model-admission',key)
   self.rt.db.put('model-admission',key,dict(proof,verified_at=time.time()-TTL))
   self.assertTrue(self.automatic(self.permit())['execution_allowed']);self.assertEqual(cli.call_count,2)
   # A genuine manual revalidation must probe even while the proof is valid.
   permit=self.permit();self.assertTrue(self.gate.verify(permit['id'],permit['sha256'],'Explicit user manual revalidation')['execution_allowed']);self.assertEqual(cli.call_count,3)
 def test_explicit_manual_retry_probes_only_exact_failed_configuration(self):
  s=self.selected();other=self.selected(dict(self.node,timeout=301))
  for selected in (s,other):self.gate.invalidate(selected['binding'],selected['cwd'],'operator-revoked')
  with patch.object(WorkbenchSessions,'rpc',self.metadata),patch('studio.model_admission.CLI.start',autospec=True,side_effect=self.response) as cli:
   permit=self.permit();self.assertTrue(self.gate.verify(permit['id'],permit['sha256'],'User clicked retry connection; disclosed readonly nonce cost',reuse_verified=False)['execution_allowed'])
  self.assertEqual(cli.call_count,1);self.assertTrue(cli.call_args.args[0].binding['admission_probe'])
  self.assertFalse(self.gate.status(other['binding'],other['cwd'])['execution_allowed'])
 def test_unknown_inflight_check_is_not_automatically_replayed(self):
  s=self.selected();key=self.gate.key(s['binding'],s['cwd'])
  self.rt.db.put('model-admission',key,dict(id='a'*32,state='STARTING',scope=self.gate.scope(s['binding'],s['cwd'])))
  self.assertEqual(self.gate.status(s['binding'],s['cwd'])['status'],'unknown')
  with patch.object(WorkbenchSessions,'rpc') as metadata,patch('studio.model_admission.CLI.start') as cli:
   with self.assertRaises(Blocked):self.automatic(self.permit())
  cli.assert_not_called();metadata.assert_not_called()

if __name__=='__main__':unittest.main()
