"""B-batch synthetic regressions; no live accounts or transcript rewriting."""
import copy,json,time,unittest,hashlib
from types import SimpleNamespace
from unittest.mock import patch
from test_workbench_runtime import fixture,structured_session
from studio.workflow_inspection import inspect_workflow
from studio.workbench_examples import examples
from studio.workbench_sessions import WorkbenchSessions
from studio.run_journal import run_conversation
from orchestration.common import canonical,digest

class InspectionGroupingTests(unittest.TestCase):
 def test_isolated_duplicate_names_have_one_connection_finding_each_and_keep_prompt(self):
  base,rt=fixture(self);g=examples(rt.operator['profiles'])[0]
  for i in range(2):
   n=copy.deepcopy(g['nodes'][1]);n.update(id='private-random-node-'+str(i),name='同名',round_prompt='');g['nodes'].append(n)
  before=copy.deepcopy(g);v=inspect_workflow(rt,g,'task')
  for i in range(2):
   ident='private-random-node-'+str(i);rows=[x for x in v['issues'] if x['node']==ident]
   connections=[x for x in rows if x['field']=='edge'];self.assertEqual(len(connections),1,rows)
   self.assertIn('输入',connections[0]['message']);self.assertIn('输出',connections[0]['message'])
   self.assertTrue(any(x['field']=='round_prompt' for x in rows))
  self.assertNotEqual(*[next(x['message'] for x in v['issues'] if x['node']=='private-random-node-'+str(i) and x['field']=='edge') for i in range(2)])
  self.assertNotIn('private-random-node',json.dumps([x['message'] for x in v['issues']]))
  self.assertEqual(g,before)
 def test_invalid_edges_and_unhandled_structure_errors_are_not_hidden(self):
  base,rt=fixture(self);g=examples(rt.operator['profiles'])[0]
  g['edges'].append(dict(id='bad-edge',source='missing',target='missing'))
  v=inspect_workflow(rt,g,'task');self.assertTrue(any(x['edge']=='bad-edge' for x in v['issues']))
  g['nodes'][1].update(id='private-random-type',kind='unsupported',name='处理者')
  v=inspect_workflow(rt,g,'task');rows=[x for x in v['issues'] if x['field']=='graph']
  self.assertTrue(any('不支持的节点类型' in x['message'] and '处理者' in x['message'] for x in rows),rows)
  self.assertEqual(next(x for x in rows if '不支持的节点类型' in x['message'])['node'],'private-random-type')
  self.assertNotIn('private-random-type',json.dumps([x['message'] for x in v['issues']]))

class TerminalOutputTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];structured_session(self.base,self.rt,'b-result');self.run='b-result-run';self.manager=WorkbenchSessions(self.rt)
  self.end=next(n for n in self.rt.flow.get('run',self.run)['graph']['nodes'] if n['kind']=='result')
 def result(self,state='COMPLETED',**kw):
  self.rt.flow.put('activation','terminal-result',dict(id='terminal-result',run=self.run,node=self.end['id'],name=self.end['name'],kind='result',state=state,iteration_path=[],**kw))
  return [x for x in run_conversation(self.manager,self.w,self.run)['entries'] if x['kind']=='result']
 def test_completed_terminal_shows_actual_answer_and_structured_artifacts_without_receipt(self):
  rows=self.result(output=dict(answer='ACTUAL FINAL ANSWER',result={'score':3,'password':'FORBIDDEN'},artifacts=['report.md'],receipt={'secret':'FORBIDDEN'}),finished_at=23)
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['content']['answer'],'ACTUAL FINAL ANSWER');self.assertEqual(rows[0]['content']['result'],{'score':3});self.assertEqual(rows[0]['content']['artifacts'],['report.md']);self.assertNotIn('FORBIDDEN',json.dumps(rows))
 def test_no_output_and_running_never_promote_partial_payload_to_result(self):
  rows=self.result(output={});self.assertIn('未配置结果输出',rows[0]['text'])
  self.rt.flow.change('run',self.run,{'state':'RUNNING','created_at':time.time()});rows=self.result(state='RUNNING',output={'answer':'UNFINISHED'});self.assertIn('等待',rows[0]['text']);self.assertNotIn('UNFINISHED',json.dumps(rows))
 def test_nested_result_does_not_become_final_and_other_runs_do_not_leak(self):
  rows=self.result(output={'answer':'FINAL'});self.rt.flow.put('activation','nested',dict(id='nested',run=self.run,node='loop/'+self.end['id'],kind='result',state='COMPLETED',output={'answer':'NESTED'}))
  self.assertEqual([x['content']['answer'] for x in run_conversation(self.manager,self.w,self.run)['entries'] if x['kind']=='result'],['FINAL'])

class NativeActivityScopeTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.manager=WorkbenchSessions(self.rt)
  self.path,self.row=structured_session(self.base,self.rt,'b-native','codex');self.run='b-native-run';node='analyze'
  old=self.rt.flow.get('node-session','b-native-chat');binding=dict(old['binding'],execution_backend='native-local',project=self.w)
  binding['config_root']=binding['home']+'/.codex';self.sid=binding['session_id']
  self.rt.db.put('chat-profile','b-native',dict(workspace=self.w,binding=binding))
  chat=digest(canonical([self.run,node]));self.aid=digest(canonical([self.run,node,[]]))
  self.rt.flow.put('node-session',chat,dict(old,id=chat,node=node,binding=binding))
  self.start=self.path.stat().st_size
  def tool(call,kind,**kw):return dict(type='response_item',payload=dict(type=kind,call_id=call,**kw))
  with self.path.open('a') as stream:
   stream.write(json.dumps(tool('denied','function_call',name='exec_command',arguments='{"cmd":"NEVER_EXECUTE"}'))+'\n')
   stream.write(json.dumps(tool('denied','function_call_output',output='unsupported call: exec_command'))+'\n')
  self.end=self.path.stat().st_size
  with self.path.open('a') as stream:stream.write(json.dumps(tool('foreign','function_call',name='FOREIGN_TURN',arguments='NO_LEAK'))+'\n')
  self.raw=self.path.read_bytes()
  item=self.rt.flow.get('activation','b-native-turn-1')
  self.rt.flow.put('activation',self.aid,dict(item,id=self.aid,node=node,iteration_path=[],chat_key=chat,observed_session=self.sid,native_turn_id='exact-turn',native_history_offset=self.start,native_history_end=self.end,native_history_path=str(self.path)))
  self.rt.session_center.discover(self.w,True)
 def read(self):
  with patch('studio.native_local.codex_history',return_value=[dict(role='assistant',text='Visible answer',turn_id='exact-turn')]):return self.manager.node_conversation(self.w,self.run,self.aid)
 def test_rejected_activity_appears_in_node_and_run_without_other_turns_or_mutation(self):
  value=self.read();self.assertEqual(value['history_status'],'available');self.assertEqual(value['activity_history_status'],'available')
  self.assertEqual(len(value['activity']),1);self.assertIn('unsupported call: exec_command',value['activity'][0]['result']);self.assertNotIn('FOREIGN_TURN',json.dumps(value));self.assertEqual(self.path.read_bytes(),self.raw)
  with patch('studio.native_local.codex_history',return_value=[]):rows=run_conversation(self.manager,self.w,self.run)['entries']
  tools=[e for e in rows if e['kind']=='tool' and e.get('activation')==self.aid];self.assertEqual(len(tools),1);self.assertNotIn('NO_LEAK',json.dumps(tools))
 def test_missing_range_or_changed_identity_never_falls_back_to_whole_session(self):
  for change in ({'native_history_end':None},{'observed_session':'foreign'}):
   old=self.rt.flow.get('activation',self.aid);self.rt.flow.change('activation',self.aid,change)
   with patch.object(self.manager,'history',wraps=self.manager.history) as history:
    value=self.read();self.assertEqual(value['activity'],[]);self.assertEqual(value['activity_history_status'],'unavailable');self.assertEqual(history.call_count,0)
   self.rt.flow.put('activation',self.aid,old)
 def test_changed_path_or_activation_commit_rejects_activity(self):
  self.rt.flow.change('activation',self.aid,{'native_history_path':'/wrong/path'})
  self.assertEqual(self.read()['activity'],[])

if __name__=='__main__':unittest.main()
