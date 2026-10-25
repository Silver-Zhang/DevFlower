"""Composite startup and deletion boundaries; native responses here are synthetic."""
import copy,json,time,unittest
from unittest.mock import patch
from test_workbench_runtime import fixture,structured_session
from studio.workbench_examples import examples
from studio.workbench_graph import validate_graph
from studio.model_admission import ModelAdmission
from studio.workbench_sessions import WorkbenchSessions
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked


class UserStartupTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=False);self.w=self.base.project['id'];self.gate=ModelAdmission(self.rt)
 def request(self):
  p=self.rt.operator['profiles']['default-codex'];return dict(workspace=self.w,profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask')
 def test_automatic_preview_does_not_call_or_grant(self):
  g=examples(self.rt.operator['profiles'])[0]
  with patch('studio.model_admission.CLI.start',side_effect=AssertionError('Preparing cannot call models')):
   p=self.rt.prepare_flow(dict(workspace=self.w,graph=g,task='User task',auto_verify=True))
  self.assertEqual(len(p['data']['admission_checks']),2)
  with self.assertRaises(Blocked):self.rt.launch_flow(p['id'],p['sha256'],'Confirmed action without successful checks')
  self.assertEqual(self.rt.flow.list('run'),[])
 def test_legacy_unverified_prepare_still_rejected(self):
  with self.assertRaises(Blocked):self.rt.prepare_flow(dict(workspace=self.w,graph=examples(self.rt.operator['profiles'])[0],task='Task'))
 def test_chat_preview_waits_for_check_but_launch_never_bypasses(self):
  p=self.rt.session_center.prepare(dict(workspace=self.w,handle='new',profile='default-codex',prompt='Hello',permission='read',auto_verify=True))
  self.assertEqual(len(p['data']['admission_checks']),1)
  with patch.object(self.rt.session_center,'_spawn',side_effect=AssertionError('No business process before admission')):
   with self.assertRaises(Blocked):self.rt.session_center.launch(p['id'],p['sha256'],'Actual confirmation')
  self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_check_request_preserves_mode_tools_and_timeout(self):
  selected=self.rt.node_binding(self.w,dict(profile='default-claude',mode='Plan',timeout=77,tools=['Read']),admission=False)
  check=self.gate.pending_check(selected)
  self.assertEqual(check['selection']['tools'],['Read']);self.assertEqual(check['selection']['timeout'],77)
  reconstructed=self.rt.node_binding(self.w,dict(check['selection'],session='new'),admission=False)
  self.assertEqual(self.gate.key(selected['binding'],selected['cwd']),self.gate.key(reconstructed['binding'],reconstructed['cwd']))
 def test_duplicate_configuration_has_one_check(self):
  g=examples(self.rt.operator['profiles'])[0];g['nodes'][2].update(copy.deepcopy({k:v for k,v in g['nodes'][1].items() if k not in ['id','name','position']}))
  p=self.rt.prepare_flow(dict(workspace=self.w,graph=g,task='Task',auto_verify=True));self.assertEqual(len(p['data']['admission_checks']),1)
 def test_unknown_model_does_not_gain_authorization(self):
  g=examples(self.rt.operator['profiles'])[0];g['nodes'][1]['model']='unapproved-model'
  with self.assertRaises(Invalid):self.rt.prepare_flow(dict(workspace=self.w,graph=g,task='Task',auto_verify=True))
 def test_valid_check_reuse_has_no_extra_call_and_revocation_wins(self):
  selected=self.rt.node_binding(self.w,dict(profile='default-codex',mode='Ask'),admission=False)
  permit=self.gate.prepare(self.request());scope=self.gate.scope(selected['binding'],selected['cwd']);key=self.gate.key(selected['binding'],selected['cwd'])
  self.rt.db.put('model-admission',key,dict(state='VERIFIED',verified_at=time.time(),scope=scope))
  with patch('studio.model_admission.CLI.start',side_effect=AssertionError('Do not call again')):
   self.assertTrue(self.gate.verify(permit['id'],permit['sha256'],'Reuse within actual confirmation',reuse_verified=True)['execution_allowed'])
  self.gate.invalidate(selected['binding'],selected['cwd']);self.assertIsNotNone(self.gate.pending_check(selected))
  with self.assertRaises(Blocked):self.gate.require(selected['binding'],selected['cwd'])
 def test_automatic_flag_rejects_type_confusion(self):
  with self.assertRaises(Invalid):self.rt.prepare_flow(dict(workspace=self.w,graph=examples(self.rt.operator['profiles'])[0],task='Task',auto_verify='yes'))
 def test_failed_composite_confirmation_cannot_probe_or_start_workflow_or_chat(self):
  for kind in ('workflow','chat'):
   with self.subTest(kind=kind):
    permit=(self.rt.prepare_flow(dict(workspace=self.w,graph=examples(self.rt.operator['profiles'])[0],task='Business input must not be sent',auto_verify=True)) if kind=='workflow' else
     self.rt.session_center.prepare(dict(workspace=self.w,handle='new',profile='default-codex',prompt='Business input must not be sent',permission='read',auto_verify=True)))
    check=permit['data']['admission_checks'][0];selected=self.rt.node_binding(self.w,dict(check['selection'],session='new'),admission=False)
    self.gate.invalidate(selected['binding'],selected['cwd'],'operator-revoked-between-preview-and-confirmation')
    prepared=self.gate.prepare(check['selection'])
    with patch.object(WorkbenchSessions,'rpc') as metadata,patch('studio.model_admission.CLI.start') as cli,patch.object(self.rt,'spawn') as flow_spawn,patch.object(self.rt.session_center,'_spawn') as chat_spawn:
     with self.assertRaises(Blocked):self.gate.verify(prepared['id'],prepared['sha256'],'Normal confirmation without deliberate retry',reuse_verified=True)
     with self.assertRaises(Blocked):
      if kind=='workflow':self.rt.launch_flow(permit['id'],permit['sha256'],'Normal confirmation')
      else:self.rt.session_center.launch(permit['id'],permit['sha256'],'Normal confirmation')
    metadata.assert_not_called();cli.assert_not_called();flow_spawn.assert_not_called();chat_spawn.assert_not_called()
  self.assertEqual(self.rt.flow.list('run'),[]);self.assertEqual(self.rt.db.listing('session-operation'),[])


class DraftDeleteTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=True);self.w=self.base.project['id'];self.g=validate_graph(examples(self.rt.operator['profiles'])[0])
  self.d=self.rt.flow.save('draft',self.g,None,self.w)
 def test_delete_keeps_immutable_revision_and_completed_history(self):
  self.rt.flow.put('run','old',dict(id='old',state='COMPLETED',draft_id='draft'))
  self.assertTrue(self.rt.flow.delete_draft('draft',self.d['revision'],self.w)['history_preserved'])
  with self.assertRaises(Invalid):self.rt.flow.get('draft','draft')
  self.assertEqual(self.rt.flow.get('run','old')['state'],'COMPLETED')
  with self.rt.flow.tx(read=True) as db:self.assertEqual(db.execute('select count(*) from drafts').fetchone()[0],1)
 def test_late_save_cannot_resurrect_deleted_draft(self):
  self.rt.flow.delete_draft('draft',self.d['revision'],self.w)
  with self.assertRaises(Conflict):self.rt.flow.save('draft',self.g,self.d['revision'],self.w)
 def test_changed_revision_not_deleted(self):
  g=copy.deepcopy(self.g);g['name']='Changed';self.rt.flow.save('draft',g,self.d['revision'],self.w)
  with self.assertRaises(Conflict):self.rt.flow.delete_draft('draft',self.d['revision'],self.w)
  self.assertEqual(self.rt.flow.get('draft','draft')['graph']['name'],'Changed')
 def test_running_unknown_or_paused_prevents_delete(self):
  for state in ['RUNNING','QUEUED','RECOVERY_REQUIRED']:
   with self.subTest(state=state):
    self.rt.flow.put('run','live',dict(id='live',state=state,draft_id='draft',paused=True))
    with self.assertRaises(Conflict):self.rt.flow.delete_draft('draft',self.d['revision'],self.w)
 def test_wrong_workspace_cannot_delete_or_overwrite(self):
  with self.assertRaises(Conflict):self.rt.flow.delete_draft('draft',self.d['revision'],'other-workspace')
  with self.assertRaises(Conflict):self.rt.flow.save('draft',self.g,self.d['revision'],'other-workspace')
 def test_deleted_after_preview_cannot_start(self):
  p=self.rt.prepare_flow(dict(workspace=self.w,graph=self.g,task='Task',draft_id='draft'))
  self.rt.flow.delete_draft('draft',self.d['revision'],self.w)
  with patch.object(self.rt,'spawn',side_effect=AssertionError('Deleted graph cannot start')):
   with self.assertRaises(Conflict):self.rt.launch_flow(p['id'],p['sha256'],'Actual confirmation')
  self.assertEqual(self.rt.flow.list('run'),[])
 def test_legacy_or_foreign_draft_cannot_be_bound_to_current_run(self):
  for owner in (None,'other-workspace'):
   with self.subTest(owner=owner):
    self.rt.flow.change('draft','draft',{'workspace':owner})
    with self.assertRaises(Conflict):self.rt.prepare_flow(dict(workspace=self.w,graph=self.g,task='Task',draft_id='draft'))
  self.assertEqual(self.rt.flow.list('run'),[])
 def test_launch_rechecks_draft_workspace_after_preview(self):
  p=self.rt.prepare_flow(dict(workspace=self.w,graph=self.g,task='Task',draft_id='draft'))
  self.rt.flow.change('draft','draft',{'workspace':'other-workspace'})
  with patch.object(self.rt,'spawn') as spawn:
   with self.assertRaises(Conflict):self.rt.launch_flow(p['id'],p['sha256'],'Actual confirmation')
  spawn.assert_not_called();self.assertEqual(self.rt.flow.list('run'),[])


class NodeConversationTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=True);self.w=self.base.project['id'];self.path,self.row=structured_session(self.base,self.rt,'ux')
  self.rt.flow.change('activation','ux-turn-2',dict(native_history_offset=0,native_history_end=self.path.stat().st_size,native_history_path=str(self.path)))
  self.sessions=WorkbenchSessions(self.rt)
 def read(self):return self.sessions.node_conversation(self.w,'ux-run','ux-turn-2')
 def test_exact_scope_and_no_hidden_reasoning(self):
  r=self.read();self.assertEqual(r['saved_answer'],'SYNTHETIC ux final answer 2');self.assertNotIn('HIDDEN_RESULT_REASONING',json.dumps(r));self.assertEqual(r['session']['handle'],self.row['handle'])
 def test_waiting_node_without_native_chat_is_not_a_connection_error(self):
  self.rt.flow.put('activation','pending',dict(id='pending',run='ux-run',node='pending-agent',kind='agent',state='PENDING'))
  result=self.sessions.node_conversation(self.w,'ux-run','pending');self.assertEqual(result['history_status'],'waiting');self.assertEqual(result['messages'],[]);self.assertFalse(result['process_alive'])
 def test_wrong_run_workspace_or_non_agent_refused(self):
  for w,run in [('wrong','ux-run'),(self.w,'other')]:
   with self.assertRaises((Blocked,Invalid)):self.sessions.node_conversation(w,run,'ux-turn-2')
  self.rt.flow.change('activation','ux-turn-2',dict(kind='condition'))
  with self.assertRaises(Blocked):self.read()
 def test_dead_process_unknown_not_fake_activity(self):
  self.rt.flow.change('activation','ux-turn-2',dict(state='RUNNING',process=None));r=self.read();self.assertEqual(r['state'],'UNKNOWN');self.assertNotIn('saved_answer',r)
 def test_missing_native_history_not_invented(self):
  self.path.unlink();r=self.read();self.assertEqual(r['history_status'],'unavailable');self.assertEqual(r['messages'],[]);self.assertEqual(r['saved_answer'],'SYNTHETIC ux final answer 2')
 def test_mismatched_receipt_cannot_supply_answer(self):
  a=self.rt.flow.get('activation','ux-turn-2');a['output']['receipt']['session_id']='different';self.rt.flow.put('activation',a['id'],a)
  self.assertNotIn('saved_answer',self.read())
 def test_native_byte_range_excludes_earlier_and_later_rounds(self):
  offset=self.path.stat().st_size
  def append(text):
   with self.path.open('a') as f:f.write(json.dumps(dict(type='assistant',sessionId=self.row['session_id'],message=dict(role='assistant',content=text)))+'\n')
  append('VISIBLE_CURRENT_ROUND');end=self.path.stat().st_size;append('LATER_ROUND_MUST_NOT_APPEAR')
  self.rt.flow.change('activation','ux-turn-2',dict(native_history_offset=offset,native_history_end=end))
  r=self.read();self.assertEqual(r['history_status'],'available');self.assertEqual([m['text'] for m in r['messages']],['VISIBLE_CURRENT_ROUND'])
 def test_non_boundary_offset_is_unavailable_not_full_history_fallback(self):
  self.rt.flow.change('activation','ux-turn-2',dict(native_history_offset=1))
  r=self.read();self.assertEqual(r['messages'],[]);self.assertEqual(r['history_status'],'unavailable')
 def test_live_chat_identity_includes_home_not_just_native_uuid(self):
  _,other=structured_session(self.base,self.rt,'other-home')
  binding=self.rt.flow.get('node-session','ux-chat')['binding']
  self.rt.db.put('session-operation','chat',dict(id='chat',binding=binding,workspace=self.w,cwd=str(self.base.source),created_at=time.time(),state='COMPLETED',permission='read'))
  self.assertEqual(self.row['session_id'],other['session_id'])
  resolved=self.sessions.operation_session(self.w,'chat')['session']
  self.assertEqual(resolved['handle'],self.row['handle']);self.assertNotEqual(resolved['handle'],other['handle'])
 def test_chat_operation_lookup_rejects_another_workspace(self):
  binding=self.rt.flow.get('node-session','ux-chat')['binding']
  self.rt.db.put('session-operation','chat',dict(id='chat',binding=binding,workspace=self.w,cwd=str(self.base.source),created_at=time.time(),state='COMPLETED',permission='read'))
  with self.assertRaises(Blocked):self.sessions.operation_session('other-workspace','chat')

class LongRunAdmissionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=False);self.gate=ModelAdmission(self.rt);self.w=self.base.project['id']
  self.selected=self.rt.node_binding(self.w,dict(profile='default-codex',mode='Ask'),admission=False);self.binding=self.selected['binding'];self.cwd=self.selected['cwd']
  self.key=self.gate.key(self.binding,self.cwd);self.seed()
  self.rt.flow.put('run','long',dict(id='long',automatic_admission=True,authorization={'answers':['Actual composite run authorization']},cancel_requested=False,calls=1,graph={'budget':{'calls':8}}))
  self.rt.flow.put('activation','node',dict(id='node',run='long',node='agent',state='STARTING'))
 def seed(self):
  self.rt.db.put('model-admission',self.key,dict(id='1'*32,state='VERIFIED',verified_at=time.time()-400,scope=self.gate.scope(self.binding,self.cwd)))
 def metadata(self,b,cwd,method,params):
  if method=='model/list':return dict(data=[dict(model=self.binding['model'],supportedReasoningEfforts=[dict(reasoningEffort=self.binding['effort'])])])
  return dict(account={'type':'synthetic'},logged_in=True)
 def reply(self,cli,prompt,cwd,artifacts,schema,journal,key):
  return dict(report={'reply':prompt.rsplit(': ',1)[1]},requested_model=cli.binding['model'],requested_effort=cli.binding['effort'],session_id='10000000-0000-4000-8000-000000000001',process=dict(exit=0,timeout=False,overflow=False,cancelled=False))
 def ensure(self,reply=None):
  with patch.object(WorkbenchSessions,'rpc',self.metadata),patch('studio.model_admission.CLI.start',autospec=True,side_effect=reply or self.reply) as cli:
   result=self.gate.ensure_for_node(self.selected,'long','node')
  return result,cli.call_count
 def test_expired_success_refreshes_once_within_frozen_run_budget(self):
  r,count=self.ensure();self.assertTrue(r['execution_allowed']);self.assertEqual(count,1);self.assertEqual(self.rt.flow.get('run','long')['connection_checks'],1)
  self.assertFalse(self.rt.flow.get('activation','node')['connection_check']);self.assertEqual(self.ensure()[1],0)
 def test_same_node_check_is_not_replayed_when_expired_again(self):
  self.ensure();self.seed()
  with self.assertRaisesRegex(Blocked,'不会重复调用'):self.ensure()
 def test_revoked_certificate_is_never_silently_rearmed(self):
  self.gate.invalidate(self.binding,self.cwd,'operator-revoked')
  with patch('studio.model_admission.CLI.start',side_effect=AssertionError('Revoked grant cannot call again')):
   with self.assertRaises(Blocked):self.gate.ensure_for_node(self.selected,'long','node')
 def test_revocation_races_start_without_being_overwritten(self):
  original=self.gate._publish
  def publish(event,**kw):
   if event['state']=='STARTING':self.gate.invalidate(self.binding,self.cwd,'operator-revoked')
   return original(event,**kw)
  with patch.object(self.gate,'_publish',publish),patch('studio.model_admission.CLI.start',side_effect=AssertionError('CAS must reject before invocation')):
   with self.assertRaises(Blocked):self.gate.ensure_for_node(self.selected,'long','node')
  self.assertEqual(self.rt.db.get('model-admission',self.key)['reason'],'operator-revoked')
 def test_no_spare_budget_prevents_additional_model_call(self):
  self.rt.flow.change('run','long',{'graph':{'budget':{'calls':1}}})
  with self.assertRaisesRegex(Blocked,'预算不足'):self.ensure()
  self.assertEqual(self.rt.db.listing('session-operation'),[])
 def test_cancel_request_reaches_actual_native_process_journal(self):
  def cancel(cli,prompt,cwd,artifacts,schema,journal,key):
   self.rt.flow.change('run','long',{'cancel_requested':True});self.assertTrue(journal.get(key)['data']['cancel_requested'])
   raise Blocked('Synthetic process cancellation')
  with self.assertRaises(Blocked):self.ensure(cancel)
  self.assertFalse(self.gate.status(self.binding,self.cwd)['execution_allowed'])
 def test_legacy_run_never_uses_implicit_refresh(self):
  self.rt.flow.change('run','long',{'automatic_admission':False})
  with self.assertRaises(Blocked):self.ensure()

class UsagePrivacyTests(unittest.TestCase):
 def test_only_numeric_accounting_fields_cross_the_ui_boundary(self):
  from studio.usage import public_usage
  value=public_usage(dict(semantics='session-cumulative-do-not-sum',usage=dict(input_tokens=3,output_tokens=4,debug='PRIVATE_PROTOCOL',authorization='SECRET',cache_read_input_tokens=2),total_cost_usd=.01))
  self.assertNotIn('PRIVATE_PROTOCOL',json.dumps(value));self.assertNotIn('SECRET',json.dumps(value));self.assertEqual(value['usage']['input_tokens'],3)
 def test_unknown_boolean_negative_and_nonfinite_values_not_used_as_cost(self):
  from studio.usage import public_usage
  value=public_usage(dict(semantics='other',usage=dict(input_tokens=True,output_tokens=-1,cached_input_tokens=float('inf'),cache_read_input_tokens=10**1000),total_cost_usd='SECRET'))
  self.assertEqual(value,dict(semantics='unknown',usage={},total_cost_usd='unknown'))


class ChatHistoryProjectionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self,admission=True);self.w=self.base.project['id'];_,self.row=structured_session(self.base,self.rt,'chat-only')
  self.binding=self.rt.flow.get('node-session','chat-only-chat')['binding']
  with self.rt.flow.tx() as db:db.execute("DELETE FROM records WHERE kind IN ('activation','node-session','run')")
  receipt=dict(provider=self.binding['provider'],home=self.binding['home'],cwd=str(self.base.source),session_id=self.row['session_id'],requested_model=self.binding['model'],requested_effort=self.binding['effort'],process=dict(exit=0,timeout=False,overflow=False,cancelled=False))
  self.op=dict(id='chat',workspace=self.w,binding=self.binding,cwd=str(self.base.source),state='COMPLETED',created_at=20,user_prompt='MY_ACTUAL_QUESTION',prompt='MY_ACTUAL_QUESTION with runtime mode',permission='read',reply='SAVED_CLAUDE_ANSWER',receipt=receipt)
  # This fixture represents a managed native request, not an unrelated
  # freeform message. Give it the real process window now recorded by the
  # journal; original reload/ordering/receipt assertions remain unchanged.
  _,native=self.rt.session_center.resolve(self.w,self.row['handle'])
  from datetime import datetime
  rows=[json.loads(line) for line in __import__('pathlib').Path(native['native']['path']).read_text().splitlines()]
  at=next(datetime.fromisoformat(row['timestamp'].replace('Z','+00:00')).timestamp() for row in rows if isinstance(row.get('timestamp'),str))
  self.op.update(native_started_at=at-1,native_finished_at=at+1)
  self.rt.db.put('session-operation','chat',self.op);self.sessions=WorkbenchSessions(self.rt)
 def turns(self):return self.sessions.history(self.w,self.row['handle'])['conversation_turns']
 def test_structured_claude_chat_answer_survives_history_reload(self):
  for _ in range(2):
   turns=self.turns();self.assertEqual(len(turns),1);self.assertEqual(turns[0]['user_text'],'MY_ACTUAL_QUESTION');self.assertEqual(turns[0]['answer'],'SAVED_CLAUDE_ANSWER')
   self.assertEqual(turns[0]['answer_source'],'saved-runtime-result')
 def test_turns_are_chronological_pairs_not_all_questions_before_answers(self):
  second=copy.deepcopy(self.op);second.update(id='second',created_at=30,user_prompt='SECOND_QUESTION',reply='SECOND_ANSWER');self.rt.db.put('session-operation','second',second)
  turns=self.turns();self.assertEqual([(t['user_text'],t['answer']) for t in turns],[('MY_ACTUAL_QUESTION','SAVED_CLAUDE_ANSWER'),('SECOND_QUESTION','SECOND_ANSWER')])
 def test_mismatched_native_receipt_never_creates_a_completed_answer(self):
  for key,value in [('home','other-home'),('cwd','other-cwd'),('provider','other'),('requested_model','other-model'),('requested_effort','other-effort'),('session_id','other-session')]:
   with self.subTest(field=key):
    op=copy.deepcopy(self.op);op['receipt'][key]=value;self.rt.db.put('session-operation','chat',op)
    self.assertFalse(any(t.get('answer')=='SAVED_CLAUDE_ANSWER' for t in self.turns()))
 def test_failed_and_cancelled_operations_do_not_claim_an_answer(self):
  for state in ['FAILED','CANCELLED']:
   op=copy.deepcopy(self.op);op['state']=state;self.rt.db.put('session-operation','chat',op)
   turns=self.turns();self.assertEqual(turns[0]['state'],state);self.assertIsNone(turns[0]['answer'])
 def test_same_uuid_in_other_home_cannot_supply_chat_turns(self):
  _,other=structured_session(self.base,self.rt,'foreign-home')
  turns=self.sessions.history(self.w,other['handle'])['conversation_turns'];self.assertFalse(any(t['id']=='chat' for t in turns))

class NativeMixedConversationTests(unittest.TestCase):
 def extra(self,rows,ops=(),related=(),has_managed=True,original=None):
  from studio.conversation_projection import native_turns
  return native_turns(related,list(ops),rows,rows if original is None else original,has_managed)
 def row(self,role,text,offset,at):return dict(role=role,text=text,native_offset=offset,native_end=offset+5,native_at=at)
 def test_terminal_question_and_answer_survive_later_managed_chat(self):
  rows=[self.row('user','MANAGED FULL PROMPT',10,11),self.row('assistant','old reply',20,12),self.row('user','NATIVE QUESTION',30,20),self.row('assistant','NATIVE ANSWER',40,21),self.row('user','SECOND MANAGED PROMPT',50,30),self.row('assistant','new reply',60,31)]
  ops=[dict(prompt='MANAGED FULL PROMPT',native_started_at=10,native_finished_at=13),dict(prompt='SECOND MANAGED PROMPT',native_started_at=29,native_finished_at=32)]
  turns=self.extra(rows,ops);self.assertEqual([t['messages'][0]['text'] for t in turns],['NATIVE QUESTION','NATIVE ANSWER']);self.assertTrue(all('state' not in t and t['kind']=='native' for t in turns))
 def test_workflow_range_cannot_be_replayed_as_freeform_native_turn(self):
  rows=[self.row('user','wrapped workflow request',10,1),self.row('assistant','workflow text',20,2),self.row('user','after workflow',30,3)]
  related=[(dict(native_history_offset=0,native_history_end=25),{})]
  self.assertEqual([t['messages'][0]['text'] for t in self.extra(rows,related=related)],['after workflow'])
 def test_legacy_exact_request_fallback_keeps_repeated_plain_question(self):
  rows=[self.row('user','ROLE\nFULL MANAGED QUESTION\nSCHEMA',10,1),self.row('assistant','No response requested.',20,2),self.row('user','QUESTION',30,3),self.row('assistant','answer',40,4)]
  turns=self.extra(rows,[dict(prompt='FULL MANAGED QUESTION\nSCHEMA')]);self.assertEqual([t['messages'][0]['text'] for t in turns],['QUESTION','answer'])
 def test_only_sanitized_display_text_is_returned(self):
  rows=[self.row('user','SAFE [REDACTED]',10,1)];original=[dict(rows[0],text='RAW SECRET')]
  self.assertEqual(self.extra(rows,original=original)[0]['messages'][0]['text'],'SAFE [REDACTED]')
 def test_unknown_legacy_clock_does_not_invent_mixed_chronology(self):
  rows=[self.row('user','old unknown',10,None)]
  self.assertEqual(self.extra(rows),[]);self.assertEqual(self.extra(rows,has_managed=False)[0]['messages'][0]['text'],'old unknown')

 def test_literal_old_prompt_after_exact_managed_interval_is_a_new_native_message(self):
  rows=[self.row('user','same full prompt',10,11),self.row('assistant','managed reply',20,12),self.row('user','same full prompt',30,20)]
  ops=[dict(prompt='same full prompt',native_started_at=10,native_finished_at=13)]
  self.assertEqual(self.extra(rows,ops)[0]['messages'][0]['text'],'same full prompt')
