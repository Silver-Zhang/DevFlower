"""Static checks must report actionable problems without calling a model."""
import copy,unittest
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio.workbench_examples import examples
from studio.workflow_inspection import inspect_workflow

class BoundedInspectionTests(unittest.TestCase):
 def setUp(self):
  from types import SimpleNamespace
  self.rt=SimpleNamespace(operator={'profiles':{}},policy={})
 def test_bounded_list_counts_every_finding_and_invalid_input(self):
  def graph(n):return dict(version=3,name='repro',nodes=[dict(id=f'bad{i}',name=f'bad{i}',kind='agent') for i in range(n)],edges=[])
  for n,total,errors,warnings,omitted in [(1,9,8,1,0),(36,255,253,2,0),(64,451,449,2,195)]:
   result=inspect_workflow(self.rt,graph(n),'task')
   self.assertEqual((result['total'],result['error_count'],result['warning_count'],result['omitted_count'],result['truncated']),(total,errors,warnings,omitted,omitted>0))
   self.assertEqual(len(result['issues']),min(total,256))
  invalid=inspect_workflow(self.rt,None,'task')
  self.assertEqual((invalid['total'],invalid['error_count'],invalid['warning_count'],invalid['omitted_count'],invalid['truncated']),(1,1,0,0,False))

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
 def test_http_reports_full_counts_for_bounded_list(self):
  import json,threading
  from urllib.request import Request,urlopen
  from studio.workbench_server import create_server
  graph=dict(version=3,name='repro',nodes=[dict(id=f'bad{i}',name=f'bad{i}',kind='agent') for i in range(64)],edges=[])
  server,token=create_server(self.rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   request=Request('http://127.0.0.1:'+str(server.server_port)+'/api/workflow/check',data=json.dumps(dict(workspace=self.w,graph=graph,task='task')).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   with urlopen(request) as response:result=json.load(response)
   self.assertEqual((len(result['issues']),result['total'],result['error_count'],result['warning_count'],result['omitted_count'],result['truncated']),(256,451,449,2,195,True))
  finally:server.shutdown();server.server_close();thread.join()
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
 def test_selected_skill_content_drift_missing_and_unsupported(self):
  from orchestration.common import canonical,digest
  graph=examples(self.rt.operator['profiles'])[0];node=next(n for n in graph['nodes'] if n['kind']=='agent');skill='a'*64;node['skills']=[skill]
  folder=self.base.source/'skills'/'chosen';folder.mkdir(parents=True);f=folder/'SKILL.md';f.write_text('---\nname: chosen\ndescription: Example\n---\nOriginal\n')
  sha=digest(canonical({'SKILL.md':digest(f.read_bytes())}));self.rt.flow.put('skill-source',skill,dict(workspace=self.w,profile=node['profile'],path=str(folder),sha256=sha))
  def issues():return [i for i in inspect_workflow(self.rt,graph,'task',workspace_path=str(self.base.source),workspace_id=self.w)['issues'] if i.get('node')==node['id'] and i.get('field')=='skills' and i['severity']=='error']
  self.assertFalse(issues())
  f.write_text('---\nname: chosen\ndescription: Example\n---\nChanged\n');self.assertTrue(any('变化' in i['message'] for i in issues()))
  f.unlink();self.assertTrue(any('缺失' in i['message'] for i in issues()))
  f.write_text('---\nname: chosen\ndescription: Example\nallowed-tools: all\n---\n');self.assertTrue(any('不支持' in i['message'] for i in issues()))


class CurrentCatalogInspectionTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self)
 def test_static_copilot_identity_never_starts_credential_broker(self):
  from studio.workflow_inspection import _current_model_catalog
  name,profile=next(iter(self.rt.operator['profiles'].items()))
  profile=dict(profile,provider='copilot')
  self.rt.flow.put('model-discovery',name,dict(id=name))
  with patch('studio.copilot_adapter.credential',side_effect=AssertionError('static check must not start gh auth token')):
   self.assertIsNone(_current_model_catalog(self.rt,name,profile))
 def graph(self,kind):
  graph=examples(self.rt.operator['profiles'])[0 if kind=='agent' else 3]
  node=next(n for n in graph['nodes'] if n['kind']==kind)
  name=next(k for k,p in self.rt.operator['profiles'].items() if p['provider']=='codex')
  profile=self.rt.operator['profiles'][name]
  config=dict(profile=name,provider='codex',model=profile['model'],effort='high')
  if kind=='agent':node.update(config)
  else:node['classifier']=dict(config,timeout=60)
  return graph,node,name,profile
 def cache(self,name,profile,**changes):
  import json,time
  from orchestration.common import digest
  from studio.model_registration import profile_identity
  value=dict(id=name,profile_sha256=digest(json.dumps(profile,sort_keys=True).encode()),
   environment_sha256=profile_identity(profile),at=time.time(),attempted_at=time.time(),
   source='synthetic model/list; no provider call',error=None,
   models=[dict(id=profile['model'],name=profile['model'],efforts=['low'],source='synthetic model/list')])
  value.update(changes);self.rt.flow.put('model-discovery',name,value)
 def inspect(self,graph):
  with patch('studio.workbench_sessions.WorkbenchSessions.rpc',side_effect=AssertionError('static check must not query provider')),patch.object(self.rt.flow,'put',side_effect=AssertionError('static check must not write cache')):
   value=inspect_workflow(self.rt,graph,'task')
  self.assertFalse(value['execution_verified'])
  return value
 def test_fresh_native_effort_conflict_still_blocks_agent_and_classifier(self):
  for kind in ['agent','router']:
   with self.subTest(kind=kind):
    graph,node,name,profile=self.graph(kind);self.cache(name,profile)
    result=self.inspect(graph)
    field='effort' if kind=='agent' else 'classifier.effort'
    self.assertTrue(any(i['severity']=='error' and i['node']==node['id'] and i['field']==field for i in result['issues']))
 def test_stale_or_foreign_catalog_cannot_refute_current_effort(self):
  import time
  from studio.model_catalog import TTL
  cases=[dict(profile_sha256='0'*64),dict(environment_sha256='0'*64),dict(at=time.time()-TTL-1),dict(at=time.time()+TTL+1),dict(error='synthetic discovery failure'),dict(profile_sha256=None)]
  for kind in ['agent','router']:
   for changes in cases:
    with self.subTest(kind=kind,changes=changes):
     graph,node,name,profile=self.graph(kind);self.cache(name,profile,**changes)
     result=self.inspect(graph)
     self.assertEqual(result['error_count'],0,result)
     self.assertTrue(any(i['severity']=='warning' and i['node']==node['id'] and '目录' in i['message'] for i in result['issues']))
 def test_registration_efforts_do_not_become_native_capabilities(self):
  for kind in ['agent','router']:
   with self.subTest(kind=kind):
    graph,node,name,profile=self.graph(kind);self.cache(name,profile,models=[])
    self.assertEqual(self.inspect(graph)['error_count'],0)
 def test_refresh_replaces_old_error_with_current_evidence(self):
  for kind in ['agent','router']:
   with self.subTest(kind=kind):
    graph,node,name,profile=self.graph(kind);self.cache(name,profile)
    self.assertGreater(self.inspect(graph)['error_count'],0)
    self.cache(name,profile,models=[dict(id=profile['model'],name=profile['model'],efforts=['high'],source='synthetic current model/list')])
    self.assertEqual(self.inspect(graph)['error_count'],0)
 def test_nested_router_uses_the_same_current_catalog_rules(self):
  graph=examples(self.rt.operator['profiles'])[2];loop=next(n for n in graph['nodes'] if n['kind']=='loop')
  body,node,name,profile=self.graph('router');loop['body']=body
  self.cache(name,profile);result=self.inspect(graph)
  self.assertTrue(any(i['severity']=='error' and i['node']==loop['id']+'/'+node['id'] and i['field']=='classifier.effort' for i in result['issues']))
  self.cache(name,profile,environment_sha256='0'*64);result=self.inspect(graph)
  self.assertFalse(any(i['severity']=='error' and i['field']=='classifier.effort' for i in result['issues']))
 def test_missing_catalog_is_unknown_without_provider_or_cache_write(self):
  for kind in ['agent','router']:
   graph,node,name,profile=self.graph(kind)
   self.assertEqual(self.inspect(graph)['error_count'],0)
 def test_stale_catalog_does_not_relax_provider_or_permission_checks(self):
  graph,node,name,profile=self.graph('agent');self.cache(name,profile,environment_sha256='0'*64)
  node.update(provider='claude',mode='Ask',permission='workspace-write')
  issues=self.inspect(graph)['issues']
  self.assertTrue(any(i['severity']=='error' and i['field']=='profile' and '不一致' in i['message'] for i in issues))
  self.assertTrue(any(i['severity']=='error' and i['field']=='permission' for i in issues))
