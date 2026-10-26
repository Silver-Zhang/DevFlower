"""Folder, reusable design, whole-run history and direct assistant counterexamples.

Synthetic native evidence is explicitly separated from actual model acceptance.
"""
import copy,json,os,secrets,time,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import test_canvas_assistant as ca
import test_provider_assistant as pa
from test_workbench_runtime import fixture,structured_session
from studio.workbench_sessions import WorkbenchSessions
from studio.workflow_library import WorkflowLibrary
from studio.run_journal import file_reference,run_conversation
from studio.workbench_examples import examples
from studio.workbench_graph import validate_graph
from studio.canvas_assistant import decode_reply
from studio.devflower_agent import DevFlowerAgent
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked

class FolderAndLibraryTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.library=WorkflowLibrary(self.rt);self.g=examples(self.rt.operator['profiles'])[0]
 def save(self,**kw):return self.library.save(dict(workspace=self.w,graph=self.g,name='我的分析流程',**kw))
 def test_parent_browsing_leaves_execution_roots_without_authorizing(self):
  parent=self.base.source.parent;v=self.rt.folder(str(parent));self.assertEqual(v['parent'],str(parent.parent));self.assertFalse(v['execution_allowed']);self.assertTrue(v['locations'])
  outside=parent/'outside';outside.mkdir();w=self.rt.open_workspace(str(outside));self.assertFalse(w['execution_allowed'])
  with self.assertRaises(Blocked):self.rt.node_binding(w['id'],self.g['nodes'][1],admission=False)
 def test_filesystem_browse_does_not_expose_system_private_or_symlink(self):
  v=self.rt.folder('/');self.assertNotIn('proc',[x['name'] for x in v['folders']]);self.assertNotIn('etc',[x['name'] for x in v['folders']])
  for p in ['/proc','/etc',str(self.rt.storage),str(self.rt.native)]:
   with self.subTest(p=p),self.assertRaises((Blocked,Invalid)):self.rt.folder(p)
  link=self.base.source/'shortcut';link.symlink_to(self.base.source.parent,target_is_directory=True);self.assertNotIn('shortcut',[x['name'] for x in self.rt.folder(str(self.base.source))['folders']])
 def test_template_survives_reopening_and_is_not_a_run(self):
  row=self.save();self.assertEqual(WorkflowLibrary(self.rt).listing(self.w)['templates'][0]['id'],row['id']);self.assertTrue(row['favorite']);self.assertEqual(self.rt.flow.list('run'),[])
 def test_template_removes_native_sessions_context_answers_and_private_edges(self):
  self.g['nodes'][1].update(session='native-handle',context_from='old-conversation',context_refs=['private.txt'],human_answer='approval',receipt={'private':'not portable'})
  self.g['edges'][0]['receipt']={'private':'no'};row=self.save();n=row['graph']['nodes'][1]
  self.assertEqual(n['session'],'new');self.assertFalse({'context_from','context_refs','human_answer','receipt'}&set(n));self.assertNotIn('receipt',row['graph']['edges'][0]);self.assertEqual(self.g['nodes'][1]['session'],'native-handle')
 def test_template_update_is_cas_and_versions_are_retained(self):
  a=self.save();b=self.library.save(dict(workspace=self.w,graph=self.g,name='新版',id=a['id'],expected=a['revision']))
  self.assertNotEqual(a['revision'],b['revision']);self.assertEqual(len(self.rt.flow.list('workflow-template-version')),2)
  with self.assertRaises(Conflict):self.library.save(dict(workspace=self.w,graph=self.g,name='旧覆盖',id=a['id'],expected=a['revision']))
 def test_template_delete_is_recoverable_and_workspace_scoped(self):
  a=self.save();data=dict(workspace=self.w,id=a['id'],expected=a['revision'],operation='trash');b=self.library.manage(data);self.assertEqual(b['collection'],'trash');c=self.library.manage(dict(data,expected=b['revision'],operation='restore'));self.assertEqual(c['collection'],'active')
  with patch.object(self.rt.session_center,'workspace',return_value={}),self.assertRaises(Blocked):self.library.manage(dict(data,workspace='other',expected=c['revision']))
 def test_template_invalid_shape_rejected(self):
  with self.assertRaises(Invalid):self.library.save(dict(workspace=self.w,name=4,graph=self.g))
  with self.assertRaises((Invalid,Blocked)):self.library.save(dict(workspace=self.w,name='bad',graph={'version':3,'nodes':[],'edges':[]}))
 def test_file_reference_is_metadata_only_and_can_point_to_line(self):
  p=self.base.source/'README.md';p.write_text('safe content')
  with patch('studio.run_journal.opened',wraps=__import__('studio.session_sources',fromlist=['opened']).opened):v=file_reference(self.rt,self.w,'README.md#L2')
  self.assertEqual(v['path'],str(p));self.assertEqual(v['line'],2);self.assertFalse(v['contents_read'])
 def test_file_reference_rejects_outside_command_credentials_and_links(self):
  p=self.base.source/'real.txt';p.write_text('text');(self.base.source/'link.txt').symlink_to(p)
  for ref in ['../outside','%2e%2e/other','vscode://command/evil','file:///etc/passwd','https://example.com','link.txt','.env','api_key.txt',str(self.rt.operator_file)]:
   with self.subTest(ref=ref),self.assertRaises((Invalid,Blocked,ValueError)):file_reference(self.rt,self.w,ref)
 def test_complex_builtin_uses_existing_semantics_and_bounded_budget(self):
  g=examples(self.rt.operator['profiles'])[-1];validate_graph(g);kinds={n['kind'] for n in g['nodes']};self.assertTrue({'merge','condition','loop','agent'}<=kinds);self.assertEqual(g['budget']['calls'],16);self.assertEqual(next(n for n in g['nodes'] if n['kind']=='loop')['max_rounds'],2)

class RunJournalTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt);structured_session(self.base,self.rt,'journal-A');structured_session(self.base,self.rt,'journal-B');self.run='journal-A-run'
 def test_reading_whole_run_retains_task_and_two_answers_without_inference(self):
  with patch('subprocess.Popen',side_effect=AssertionError('history must not launch')):v=run_conversation(self.manager,self.w,self.run)
  self.assertEqual(v['run'],self.run);answers=[e for e in v['entries'] if e['kind']=='assistant'];self.assertEqual(len(answers),2);self.assertNotIn('journal-B',json.dumps(answers));self.assertNotIn('HIDDEN_RESULT_REASONING',json.dumps(v));self.assertEqual(len(self.rt.flow.list('run')),2)
 def test_old_candidate_is_not_relabelled_as_current(self):
  v=run_conversation(self.manager,self.w,self.run);self.assertEqual(v['candidate_sha256'],'unknown');self.assertEqual(v['viewer_candidate_sha256'],self.rt.candidate_sha256)
 def test_foreign_workspace_and_failed_receipt_cannot_become_verified_answer(self):
  with patch.object(self.rt.session_center,'workspace',return_value={}),self.assertRaises(Blocked):run_conversation(self.manager,'foreign',self.run)
  a=self.rt.flow.get('activation','journal-A-turn-2');a['output']['receipt']['process']['exit']=1;self.rt.flow.put('activation',a['id'],a);v=run_conversation(self.manager,self.w,self.run);self.assertEqual(len([e for e in v['entries'] if e['kind']=='assistant']),1)
 def test_ordered_handoffs_strip_private_data_and_do_not_duplicate(self):
  self.rt.flow.event(self.run,'handoff-sent',node='loop/analyze',source='start',content={'answer':'Visible handoff','receipt':{'private':'FORBIDDEN'},'credentials':'FORBIDDEN'})
  a=run_conversation(self.manager,self.w,self.run);b=run_conversation(self.manager,self.w,self.run);self.assertEqual(a,b);self.assertNotIn('FORBIDDEN',json.dumps(a));self.assertEqual(len([e for e in a['entries'] if e['kind']=='handoff']),1)
  known=[e['at'] for e in a['entries'] if e['at'] is not None];self.assertEqual(known,sorted(known))

class AssistantIntentTests(unittest.TestCase):
 setUp=ca.CanvasAssistantTests.setUp
 data=ca.CanvasAssistantTests.data
 wait=ca.CanvasAssistantTests.wait
 run_turn=ca.CanvasAssistantTests.run_turn
 def test_general_chat_without_source_or_tool_and_persists_public_summary(self):
  reply=dict(text=json.dumps({'reply':'这是普通解释','plan':None}),public_summaries=[{'source':'provider-public-summary','text':'公开简要依据'}])
  with patch.object(self.rt,'validate_source',side_effect=AssertionError('chat must not read source')),patch.object(ca.SyntheticBackend,'chat',return_value=reply):v=self.run_turn(self.data(intent='chat'))
  self.assertEqual(v['turn']['state'],'COMPLETED');self.assertIsNone(v['turn']['proposal']);self.assertEqual(v['messages'][-1]['kind'],'answer');self.assertEqual(v['messages'][-1]['public_summaries'][0]['text'],'公开简要依据')
 def test_advanced_current_graph_context_is_preserved_without_native_handles(self):
  g=examples(self.rt.operator['profiles'])[-1]
  loop=next(n for n in g['nodes'] if n['kind']=='loop');loop['body']['nodes'][1]['context_refs']=['private.txt']
  with patch.object(ca.SyntheticBackend,'chat',return_value={'text':json.dumps({'reply':'已解释','plan':None})}) as call:v=self.run_turn(self.data(graph=g))
  ctx=json.loads(call.call_args[0][1].split('\n',1)[1]);node=next(n for n in ctx['current_graph']['nodes'] if n['kind']=='loop');self.assertEqual(node['max_rounds'],2);self.assertEqual(len(node['body']['nodes']),4);self.assertNotIn('private.txt',json.dumps(ctx))
 def test_chat_invalid_snapshot_does_not_start_backend(self):
  with self.assertRaises(Invalid):self.ai.start(self.data(intent='chat',graph={'version':3,'nodes':[],'edges':[]}))
  self.assertEqual(ca.SyntheticBackend.calls,[])
 def test_chat_mode_cannot_accept_injected_proposal(self):
  with patch.object(ca.SyntheticBackend,'chat',return_value={'text':json.dumps({'reply':'bad','plan':{'version':1,'name':'p','summary':'p','nodes':[],'edges':[]}})}):v=self.run_turn(self.data(intent='chat'))
  self.assertEqual(v['turn']['state'],'FAILED');self.assertIsNone(v['turn']['proposal'])
 def test_intent_changes_cannot_replay_existing_request(self):
  d=self.data(intent='auto');self.run_turn(d)
  with self.assertRaises(Conflict):self.ai.start(dict(d,intent='chat'))
 def test_complex_plan_is_validated_not_merely_described(self):
  g=examples(self.rt.operator['profiles'])[-1]
  def plan(g):
   nodes=[]
   for n in g['nodes']:
    v={k:copy.deepcopy(n[k]) for k in ('id','kind','name','profile','model','effort','mode','initial_prompt','round_prompt','handoff_prompt','test','strategy','max_rounds','until') if k in n}
    if n['kind']=='loop':v['body']=plan(n['body'])
    nodes.append(v)
   return dict(version=1,name=g['name'],summary='Complex plan',nodes=nodes,edges=[{k:e[k] for k in ('source','target','when','transfer') if k in e} for e in g['edges']])
  result,graph=decode_reply(json.dumps({'reply':'草稿','plan':plan(g)}),self.choices);self.assertTrue(graph);self.assertIn('condition',{n['kind'] for n in graph['nodes']})
  p=plan(g);next(n for n in p['nodes'] if n['kind']=='loop')['max_rounds']=6
  with self.assertRaises((Invalid,Blocked)):decode_reply(json.dumps({'reply':'bad','plan':p}),self.choices)

class PublicAssistantOutputTests(unittest.TestCase):
 setUp=pa.DirectAgentTests.setUp
 call=pa.DirectAgentTests.call
 def test_openai_explicit_reasoning_field_is_visible_redacted_not_hidden(self):
  self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],protocol='openai'));self.reply=dict(id='synthetic',choices=[dict(message=dict(content='回答',reasoning_content='公开依据 PRIVATE_FIXTURE_SECRET',thinking='HIDDEN'))])
  r=self.call();self.assertEqual(r['public_summaries'][0]['source'],'provider-visible-field:reasoning_content');self.assertNotIn('PRIVATE_FIXTURE_SECRET',json.dumps(r));self.assertNotIn('HIDDEN',json.dumps(r))
 def test_anthropic_hidden_thinking_never_promoted(self):
  self.reply['content'].append({'type':'thinking','thinking':'HIDDEN'});r=self.call();self.assertEqual(r['public_summaries'],[]);self.assertNotIn('HIDDEN',json.dumps(r))
 def test_chat_request_omits_canvas_tools(self):
  with DevFlowerAgent(self.rt) as a:a.intent='chat';r=a.chat('synthetic-model','general explanation')
  self.assertNotIn('tools',self.requests[0][1]);self.assertEqual(r['tools_enabled'],[])
