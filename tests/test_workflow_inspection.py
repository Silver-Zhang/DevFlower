"""Static checks must report actionable problems without calling a model."""
import copy,unittest
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio.workbench_examples import examples
from studio.workflow_inspection import inspect_workflow

class WorkflowInspectionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self);self.w=self.base.project['id']
 def test_collects_nested_configuration_and_connectivity_without_calls(self):
  graph=examples(self.rt.operator['profiles'])[2]
  loop=next(n for n in graph['nodes'] if n['kind']=='loop')
  agent=next(n for n in loop['body']['nodes'] if n['kind']=='agent')
  agent.update(profile='unknown',model='',effort='',round_prompt='')
  graph['edges'].append(dict(id='broken',source='missing',target=loop['id']))
  with patch('studio.workbench_runtime.WorkbenchRuntime.node_binding',side_effect=AssertionError('must not invoke executor')):
   result=inspect_workflow(self.rt,graph,'')
  issues=result['issues'];self.assertTrue(any(i.get('node')==loop['id']+'/'+agent['id'] for i in issues));self.assertTrue(any(i.get('edge')=='broken' for i in issues));self.assertTrue(any(i['field']=='task' for i in issues));self.assertTrue(all(i['severity'] in ('error','warning') for i in issues))
 def test_good_graph_never_claims_execution_success(self):
  graph=examples(self.rt.operator['profiles'])[0]
  result=inspect_workflow(self.rt,graph,'synthetic task')
  self.assertFalse(result['execution_verified']);self.assertTrue(any(i['severity']=='warning' for i in result['issues']))
 def test_router_reports_each_classifier_problem_and_missing_default_at_node(self):
  graph=examples(self.rt.operator['profiles'])[3]
  router=next(n for n in graph['nodes'] if n['kind']=='router')
  router.pop('default_route')
  router['classifier']=dict(profile='missing',provider='codex',model='',effort='',timeout=999)
  with patch('studio.workbench_runtime.WorkbenchRuntime.node_binding',side_effect=AssertionError('must not call provider')):
   issues=inspect_workflow(self.rt,graph,'task')['issues']
  fields={i['field'] for i in issues if i.get('node')==router['id']}
  self.assertTrue({'classifier.profile','classifier.model','classifier.effort','classifier.timeout','default_route'} <= fields,fields)
 def test_nested_router_reports_full_path(self):
  graph=examples(self.rt.operator['profiles'])[2]
  loop=next(n for n in graph['nodes'] if n['kind']=='loop')
  nested=examples(self.rt.operator['profiles'])[3]
  router=next(n for n in nested['nodes'] if n['kind']=='router')
  router['classifier']=dict(profile='missing',provider='codex',model='',effort='',timeout=5)
  loop['body']=nested
  issues=inspect_workflow(self.rt,graph,'task')['issues']
  self.assertTrue(any(i.get('node')==loop['id']+'/'+router['id'] and i.get('field')=='classifier.timeout' for i in issues))
 def test_scalar_handoff_reports_missing_required_and_type_mismatch(self):
  graph=examples(self.rt.operator['profiles'])[0]
  edge=graph['edges'][1];edge['transfer']='answer'
  target=next(n for n in graph['nodes'] if n['id']==edge['target'])
  target['input_contract']=[dict(field='result.score',type='number',required=True),dict(field='result',type='number',required=True)]
  issues=inspect_workflow(self.rt,graph,'task')['issues']
  errors=[i['message'] for i in issues if i.get('edge')==edge['id'] and i['severity']=='error']
  self.assertTrue(any('result.score' in s for s in errors),errors)
  self.assertTrue(any('类型' in s and 'result' in s for s in errors),errors)

 def test_http_returns_nested_classifier_issues_without_provider_call(self):
  import json,threading
  from urllib.request import Request,urlopen
  from studio.workbench_server import create_server
  graph=examples(self.rt.operator['profiles'])[2]
  loop=next(n for n in graph['nodes'] if n['kind']=='loop')
  body=examples(self.rt.operator['profiles'])[3]
  router=next(n for n in body['nodes'] if n['kind']=='router')
  router['classifier']=dict(profile='missing',provider='codex',model='',effort='',timeout=999)
  loop['body']=body
  server,token=create_server(self.rt)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   request=Request('http://127.0.0.1:'+str(server.server_port)+'/api/workflow/check',data=json.dumps(dict(workspace=self.w,graph=graph,task='task')).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   with patch('studio.workbench_runtime.WorkbenchRuntime.node_binding',side_effect=AssertionError('no provider call')):
    with urlopen(request) as response:result=json.load(response)
   self.assertFalse(result['execution_verified'])
   self.assertTrue(any(i.get('node')=='cycle/route' and i.get('field')=='classifier.timeout' for i in result['issues']))
  finally:server.shutdown();server.server_close();thread.join()
 def test_agent_limits_and_condition_types_have_node_locations(self):
  graph=examples(self.rt.operator['profiles'])[5]
  agent=next(n for n in graph['nodes'] if n['kind']=='agent');agent.update(timeout=4,context_refs='wrong',skills=['not-an-id'])
  condition=next(n for n in graph['nodes'] if n['kind']=='condition');condition['test']=dict(field='result.score',op='greater',value='ten')
  issues=inspect_workflow(self.rt,graph,'task')['issues']
  fields={i['field'] for i in issues if i.get('node')==agent['id']}
  self.assertTrue({'timeout','context_refs','skills'}<=fields,fields)
  self.assertTrue(any(i.get('node')==condition['id'] and i.get('field')=='test' for i in issues))
 def test_declared_handoff_types_are_compared(self):
  graph=examples(self.rt.operator['profiles'])[0]
  edge=graph['edges'][1];source=next(n for n in graph['nodes'] if n['id']==edge['source']);target=next(n for n in graph['nodes'] if n['id']==edge['target'])
  source['output_contract']=[dict(field='result.score',type='number',required=True)]
  target['input_contract']=[dict(field='result.score',type='boolean',required=True)]
  issues=inspect_workflow(self.rt,graph,'task')['issues']
  self.assertTrue(any(i.get('edge')==edge['id'] and 'result.score' in i['message'] and '类型' in i['message'] for i in issues))
 def test_context_files_are_checked_without_reading_contents(self):
  graph=examples(self.rt.operator['profiles'])[0];agent=next(n for n in graph['nodes'] if n['kind']=='agent')
  (self.base.source/'oversized.txt').write_text('x'*16001);(self.base.source/'linked.txt').symlink_to('oversized.txt')
  agent['context_refs']=['missing.txt','oversized.txt','linked.txt']
  with patch('studio.workflow_inspection.os.read',side_effect=AssertionError('inspection must not read contents')):
   issues=inspect_workflow(self.rt,graph,'task',workspace_path=str(self.base.source))['issues']
  messages=[i['message'] for i in issues if i.get('node')==agent['id'] and i['field']=='context_refs']
  for name in agent['context_refs']:self.assertTrue(any(name in m for m in messages),messages)
 def test_disconnected_cycle_reports_each_unreachable_node(self):
  graph=examples(self.rt.operator['profiles'])[0]
  graph['nodes'] += [dict(id='lostA',kind='output',name='失联 A'),dict(id='lostB',kind='output',name='失联 B')]
  graph['edges'] += [dict(id='lost1',source='lostA',target='lostB'),dict(id='lost2',source='lostB',target='lostA')]
  issues=inspect_workflow(self.rt,graph,'task')['issues']
  for node in ('lostA','lostB'):self.assertTrue(any(i.get('node')==node and '开始' in i['message'] for i in issues),node)
 def test_classifier_cached_evidence_and_budget(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[3];route=next(n for n in graph['nodes'] if n['kind']=='router');profile=rt.operator['profiles']['default-codex']
  route['classifier']=dict(profile='default-codex',provider='codex',model=profile['model'],effort=profile['effort'],timeout=60,retries=0);rt.flow.put('cli-probe','default-codex',dict(available=False));rt.flow.put('auth-probe','default-codex',dict(logged_in=False));graph['budget']['calls']=1
  issues=inspect_workflow(rt,graph,'task')['issues']
  self.assertTrue(any(i.get('node')==route['id'] and 'CLI' in i['message'] for i in issues));self.assertTrue(any(i.get('node')==route['id'] and '未登录' in i['message'] for i in issues));self.assertTrue(any(i.get('field')=='budget' and i['severity']=='warning' for i in issues))
 def test_field_handoff_matches_result_contract(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[0];edge=graph['edges'][1];edge.update(transfer='field',field='result.score')
  source=next(n for n in graph['nodes'] if n['id']==edge['source']);target=next(n for n in graph['nodes'] if n['id']==edge['target'])
  source['output_contract']=[dict(field='result.score',type='number',required=True)];target['input_contract']=[dict(field='result',type='boolean',required=True)]
  issues=inspect_workflow(rt,graph,'task')['issues']
  self.assertTrue(any(i.get('edge')==edge['id'] and '类型' in i['message'] for i in issues))
 def test_merge_input_schema_conflict_and_router_missing_branch(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[1];merge=next(n for n in graph['nodes'] if n['kind']=='merge');merge['input_contract']=[dict(field='result.inputs',type='string',required=True),dict(field='summary',type='string',required=True)]
  issues=inspect_workflow(rt,graph,'task')['issues'];messages=[i['message'] for i in issues if i.get('node')==merge['id']]
  self.assertTrue(any('result.inputs' in m and '类型' in m for m in messages),messages);self.assertTrue(any('summary' in m for m in messages),messages)
  router_graph=examples(rt.operator['profiles'])[3];router=next(n for n in router_graph['nodes'] if n['kind']=='router');router_graph['edges']=[e for e in router_graph['edges'] if not (e['source']==router['id'] and e.get('route')=='code')]
  issues=inspect_workflow(rt,router_graph,'task')['issues'];self.assertTrue(any(i.get('node')==router['id'] and i.get('field')=='routes' and 'code' in i['message'] for i in issues))
 def test_skill_identity_and_native_extension_are_checked_without_binding(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[0];node=next(n for n in graph['nodes'] if n['kind']=='agent');skill='a'*64;node['skills']=[skill]
  issues=inspect_workflow(rt,graph,'task',workspace_id=base.project['id'])['issues'];self.assertTrue(any(i.get('node')==node['id'] and i['field']=='skills' and i['severity']=='error' for i in issues))
  rt.flow.put('skill-source',skill,dict(workspace=base.project['id'],profile=node['profile']));issues=inspect_workflow(rt,graph,'task',workspace_id=base.project['id'])['issues'];self.assertFalse(any(i.get('node')==node['id'] and i['field']=='skills' and i['severity']=='error' for i in issues))
 def test_route_condition_requires_declared_compatible_field(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[3];router=next(n for n in graph['nodes'] if n['kind']=='router');router['routes'][0]['test']=dict(field='result.score',op='greater',value=4);router['input_contract']=[dict(field='result.score',type='string',required=True)]
  issues=inspect_workflow(rt,graph,'task')['issues'];self.assertTrue(any(i.get('node')==router['id'] and i.get('field')=='routes' and '类型' in i['message'] for i in issues))
  router['input_contract']=[];issues=inspect_workflow(rt,graph,'task')['issues'];self.assertTrue(any(i.get('node')==router['id'] and 'result.score' in i['message'] and '合同' in i['message'] for i in issues))
 def test_fixed_executor_and_permissions_are_inspected(self):
  base,rt=self.base,self.rt;graph=examples(rt.operator['profiles'])[0];node=next(n for n in graph['nodes'] if n['kind']=='agent');node.update(kind='delivery',profile='missing-fixed-executor')
  issues=inspect_workflow(rt,graph,'task')['issues'];self.assertTrue(any(i.get('node')==node['id'] and i.get('field')=='profile' and i['severity']=='error' for i in issues))
