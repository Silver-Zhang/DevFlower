"""Adversarial synthetic catalog/routing contracts, distinct from native acceptance."""
import copy,time,unittest,sys,json
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).parent)]
from test_workbench_runtime import fixture
from studio.workbench_sessions import WorkbenchSessions
from studio.model_catalog import ModelCatalog,codex_models
from studio.routing import validate_router,rule_route,fallback,semantic_route,classifier_node
from studio.data_contracts import normalize,check
from studio.graph import Invalid
from orchestration.common import Blocked

class CatalogTests(unittest.TestCase):
 def setUp(self):
  self.base,self.rt=fixture(self, admission=True);self.manager=WorkbenchSessions(self.rt);self.cat=ModelCatalog(self.manager);self.profile=self.rt.operator['profiles']['default-codex']
 def test_native_catalog_pagination_and_effort(self):
  with patch.object(self.manager,'rpc',side_effect=[{'data':[{'model':self.profile['model'],'supportedReasoningEfforts':[{'reasoningEffort':self.profile['effort']}]}],'nextCursor':'two'},{'data':[{'model':'new-unapproved','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]},{'account':{'type':'apiKey'}}]) as rpc:
   value=self.cat.get('default-codex',self.profile,True)
  self.assertFalse(value['stale']);self.assertEqual(rpc.call_count,3);self.assertFalse(value['models'][0]['execution_allowed']);self.assertTrue(value['models'][0]['discovered']);self.assertTrue(value['models'][0]['operator_registered']);self.assertFalse(value['models'][1]['execution_allowed'])
 def test_failure_never_claims_preregistered_as_complete(self):
  with patch.object(self.manager,'rpc',side_effect=Blocked('native failed')):value=self.cat.get('default-codex',self.profile,True)
  self.assertTrue(value['stale']);self.assertFalse(value['discovery_complete']);self.assertIn('native failed',value['error']);self.assertIn('不是完整',value['source'])
 def test_refresh_failure_keeps_last_good_marked_stale(self):
  with patch.object(self.manager,'rpc',return_value={'data':[{'model':self.profile['model']}]}):first=self.cat.get('default-codex',self.profile,True)
  with patch.object(self.manager,'rpc',side_effect=Blocked('offline')):second=self.cat.get('default-codex',self.profile,True)
  self.assertEqual(first['updated_at'],second['updated_at']);self.assertTrue(second['stale']);self.assertEqual([dict(m,discovery_current=False) for m in first['models']],second['models']);self.assertFalse(second['models'][0]['discovery_current'])
 def test_invalid_proxy_envelope_preserves_last_good_catalog(self):
  profile=dict(self.profile,provider='claude')
  response=MagicMock()
  with patch('studio.model_catalog.configured_env',return_value={'ANTHROPIC_AUTH_TOKEN':'synthetic-not-a-secret'}),patch('studio.model_catalog.urllib.request.build_opener') as opener,patch.object(self.manager,'rpc',return_value={}):
   opener.return_value.open.return_value=response
   response.__enter__.return_value.read.return_value=json.dumps({'data':[{'id':profile['model']}]}).encode()
   first=self.cat.get('proxy',profile,True)
   for invalid in ([],None,'invalid',42):
    with self.subTest(envelope=invalid):
     response.__enter__.return_value.read.return_value=json.dumps(invalid).encode()
     value=self.cat.get('proxy',profile,True)
     self.assertEqual(value['updated_at'],first['updated_at']);self.assertEqual(value['models'],[dict(m,discovery_current=False) for m in first['models']]);self.assertFalse(value['models'][0]['execution_allowed'])
     self.assertTrue(value['stale']);self.assertFalse(value['discovery_complete']);self.assertIn('无效响应格式',value['error'])
 def test_expired_cache_does_not_become_live(self):
  with patch.object(self.manager,'rpc',return_value={'data':[{'model':self.profile['model']}]}):value=self.cat.get('default-codex',self.profile,True)
  with patch('studio.model_catalog.time.time',return_value=value['updated_at']+301):self.assertTrue(self.cat.get('default-codex',self.profile)['stale'])
 def test_profile_cache_is_not_shared(self):
  with patch.object(self.manager,'rpc',return_value={'data':[{'model':'profile-one'}]}):self.cat.get('one',self.profile,True)
  other=self.cat.get('two',self.profile)
  self.assertNotIn('profile-one',[m['id'] for m in other['models']])
 def test_empty_catalog_is_error(self):
  with patch.object(self.manager,'rpc',return_value={'data':[]}):value=self.cat.get('one',self.profile,True)
  self.assertTrue(value['stale']);self.assertIn('空模型目录',value['error'])
 def test_invalid_mode_and_unapproved_model_rejected(self):
  node=dict(profile='default-codex',provider='codex',session='new',mode='Shell')
  with self.assertRaises(Invalid):self.rt.node_binding(self.base.project['id'],node)
  with self.assertRaises(Invalid):self.rt.node_binding(self.base.project['id'],dict(node,mode='Ask',model='unapproved'))
 def test_new_chat_selected_model_mode_bound_in_permit(self):
  value=self.rt.session_center.prepare(dict(workspace=self.base.project['id'],handle='new',profile='default-codex',model=self.profile['model'],effort=self.profile['effort'],mode='Plan',permission='read',prompt='make plan'))
  self.assertEqual(value['data']['binding']['model'],self.profile['model']);self.assertEqual(value['data']['prompt'],'make plan');self.assertEqual(value['data']['permission'],'read');self.assertEqual(value['data']['binding']['role'],'reviewer')
 def test_failed_validation_leaves_no_session(self):
  with self.assertRaises(Invalid):self.rt.session_center.prepare(dict(workspace=self.base.project['id'],handle='new',profile='missing',mode='Ask',permission='read',prompt='test'))
  self.assertEqual(self.rt.db.listing('session-operation'),[])

 def test_concurrent_catalog_refresh_is_coalesced(self):
  import threading
  arrived,release=threading.Event(),threading.Event();outputs=[]
  def rpc(binding,cwd,method,params):
   if method=='cli/probe':arrived.set();self.assertTrue(release.wait(2));return dict(available=True,machine_output=True)
   if method=='model/list':return dict(data=[dict(model=self.profile['model'])])
   return dict(account={'type':'synthetic'})
  with patch.object(self.manager,'rpc',side_effect=rpc) as transport:
   thread=threading.Thread(target=lambda:outputs.extend(self.manager.capabilities(True,'default-codex')));thread.start();self.assertTrue(arrived.wait(2));other=self.manager.capabilities(True,'default-codex')[0];self.assertTrue(other['refresh_pending']);release.set();thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(transport.call_count,3)
  self.assertFalse(outputs[0]['stale']);self.assertFalse(self.manager.capabilities(False,'default-codex')[0]['stale'])
 def test_explicit_logged_out_profile_cannot_create(self):
  self.rt.flow.put('auth-probe','default-codex',dict(id='default-codex',at=time.time(),logged_in=False))
  with self.assertRaises(Blocked):self.rt.node_binding(self.base.project['id'],dict(profile='default-codex',provider='codex',mode='Ask'))

class RouterTests(unittest.TestCase):
 def setUp(self):
  self.n=dict(id='route',name='Router',routes=[dict(label='code',test=dict(field='result.task_type',op='equals',value='code')),dict(label='research',test=dict(field='result.task_type',op='equals',value='research'))],default_route='default')
 def test_rules_only_choose_declared_label(self):
  self.assertEqual(validate_router(self.n),{'code','research','default'});self.assertEqual(rule_route({'result':{'task_type':'code'}},self.n)['label'],'code')
 def test_missing_not_equals_does_not_route(self):
  self.n['routes'][0]['test']['op']='not_equals';self.assertIsNone(rule_route({},self.n))
 def test_no_match_and_type_error_have_explicit_default(self):
  self.assertIsNone(rule_route({},self.n));self.assertEqual(fallback(self.n,'unmatched')['label'],'default')
  self.n['routes'][0]['test']['op']='greater';self.assertTrue(rule_route({'result':{'task_type':'code'}},self.n)['fallback'])
 def test_no_default_is_failure(self):
  del self.n['default_route']
  with self.assertRaises(Invalid):fallback(self.n,'unmatched')
 def test_classifier_invalid_extra_low_confidence_unknown_nan(self):
  self.n['classifier']=dict(profile='p',provider='claude',model='m',effort='high',threshold=.8,categories=['code','research'])
  for value in [None,{},dict(category='code',confidence=.9,reason='ok',tools=['Bash']),dict(category='shell',confidence=.9,reason='x'),dict(category='code',confidence=.2,reason='low'),dict(category='code',confidence=float('nan'),reason='x')]:
   self.assertTrue(semantic_route(value,self.n)['fallback'])
  self.assertEqual(semantic_route(dict(category='research',confidence=.95,reason='docs'),self.n)['label'],'research')
  node=classifier_node(self.n);self.assertEqual(node['permission'],'read');self.assertEqual(node['mode'],'Ask')
 def test_malformed_categories_have_controlled_validation_error(self):
  import copy
  for categories in ([{}],[[]],[None],[1],['code','code'],[]):
   with self.subTest(categories=categories):
    node=copy.deepcopy(self.n);node['classifier']=dict(profile='p',provider='claude',model='m',effort='high',categories=categories)
    with self.assertRaises(Invalid):validate_router(node)
 def test_out_of_range_json_integer_uses_default(self):
  import json
  self.n['classifier']=dict(profile='p',provider='claude',model='m',effort='high',threshold=.8)
  for sign in ('','-'):
   payload=json.loads('{"category":"code","confidence":'+sign+'1'+'0'*400+',"reason":"invalid confidence"}')
   self.assertTrue(semantic_route(payload,self.n)['fallback'])
 def test_classifier_cannot_inject_permission(self):
  self.n['classifier']=dict(profile='p',permission='workspace-write')
  with self.assertRaises(Invalid):validate_router(self.n)
 def test_contract_required_and_json_types(self):
  spec=normalize([dict(field='result.score',type='number',required=True)],[])
  for value in ({},{'result':{'score':True}},{'result':{'score':'3'}},{'result':{'score':float('nan')}}):
   with self.assertRaises(Invalid):check(value,spec)
  check({'result':{'score':3}},spec)
 def test_private_contract_fields_rejected(self):
  with self.assertRaises(Invalid):normalize([dict(field='receipt.session_id',type='string')],[])

class RouterSchedulingTests(unittest.TestCase):
 def setUp(self):
  import tempfile
  from types import SimpleNamespace
  from studio.workbench_store import WorkbenchStore
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=WorkbenchStore(Path(self.tmp.name)/'flow.sqlite');self.rt=SimpleNamespace(flow=self.db,check_operator=lambda:None);self.calls=[]
 def graph(self,semantic=False):
  route=dict(id='router',kind='router',name='route',routes=[dict(label='code',test=dict(field='result.task_type',op='equals',value='code')),dict(label='research',test=dict(field='result.task_type',op='equals',value='research'))],default_route='default')
  if semantic:route['classifier']=dict(profile='default-codex',provider='codex',model='test',effort='high',timeout=10,retries=0,threshold=.8)
  nodes=[dict(id='start',kind='start',name='start'),route,*[dict(id=k,kind='agent',name=k) for k in ('code','research','default')],dict(id='end',kind='result',name='end')]
  return dict(version=3,name='Router test',nodes=nodes,edges=[dict(id='input',source='start',target='router'),*[dict(id='to_'+k,source='router',target=k,route=k) for k in ('code','research','default')],*[dict(id='from_'+k,source=k,target='end') for k in ('code','research','default')]])
 def execute(self,graph,reply=None):
  from studio.workbench_graph import validate_graph
  from studio.workbench_scheduler import Scheduler
  self.db.put('run','r',dict(id='r',graph=validate_graph(graph),state='QUEUED',task='research request',created_at=time.time(),paused=False,cancel_requested=False))
  def call(node,key,inputs,prefix,iteration):
   self.calls.append((node,inputs))
   if reply and node['id']=='router':return reply()
   return dict(answer=node['id'],result={})
  Scheduler(self.rt,'r',call).run_graph();return self.db.get('run','r')
 def test_no_match_runs_default_once_and_skips_others(self):
  value=self.execute(self.graph());self.assertEqual(value['state'],'COMPLETED');self.assertEqual([n['id'] for n,_ in self.calls],['default'])
  states={a['node']:a['state'] for a in self.db.list('activation')};self.assertEqual(states['code'],'SKIPPED');self.assertEqual(states['research'],'SKIPPED')
 def test_semantic_classifier_then_only_research(self):
  value=self.execute(self.graph(True),lambda:dict(result=dict(category='research',confidence=.95,reason='needs sources')));self.assertEqual(value['state'],'COMPLETED');self.assertEqual([n['id'] for n,_ in self.calls],['router','research']);self.assertEqual(self.calls[0][0]['permission'],'read')
 def test_oversized_confidence_schedules_default_once(self):
  value=self.execute(self.graph(True),lambda:dict(result=dict(category='research',confidence=10**400,reason='invalid confidence')))
  self.assertEqual(value['state'],'COMPLETED');self.assertEqual([n['id'] for n,_ in self.calls],['router','default'])
  route=next(a for a in self.db.list('activation') if a['node']=='router');self.assertTrue(route['route']['fallback'])
 def test_timeout_and_invalid_classification_default_is_visible(self):
  def timeout():raise Blocked('native timeout; owned process terminated')
  value=self.execute(self.graph(True),timeout);self.assertEqual(value['state'],'COMPLETED');route=next(a for a in self.db.list('activation') if a['node']=='router');self.assertTrue(route['route']['fallback']);self.assertIn('timeout',route['route']['reason'])
 def test_unknown_branch_graph_rejected(self):
  from studio.workbench_graph import validate_graph
  graph=self.graph();graph['edges'][1]['route']='unapproved'
  with self.assertRaises(Invalid):validate_graph(graph)
 def test_unselected_target_permissions_still_validated(self):
  base,rt=fixture(self, admission=True);graph=self.graph();graph['nodes'][2].update(profile='missing',provider='codex')
  with self.assertRaises(Invalid):rt.prepare_flow(dict(workspace=base.project['id'],graph=graph,task='route must not grant permission'))
 def test_payload_hash_and_target_identity_bound(self):
  from orchestration.common import canonical,digest
  self.execute(self.graph());node=next(a for a in self.db.list('activation') if a['node']=='default');item=node['inputs'][0]
  self.assertEqual(item['target_activation'],node['id']);self.assertEqual(item['target_round'],node['iteration_path']);self.assertEqual(item['content_sha256'],digest(canonical(item['content'])))


class ConditionMissingTests(unittest.TestCase):
 setUp=RouterSchedulingTests.setUp
 graph=RouterSchedulingTests.graph
 execute=RouterSchedulingTests.execute
 def test_missing_not_equals_is_else_with_explicit_reason(self):
  graph=self.graph();node=graph['nodes'][1];node.update(kind='condition',test=dict(field='result.missing',op='not_equals',value='code'));node.pop('routes');node.pop('default_route');graph['edges']=[e for e in graph['edges'] if e['source']!='router' or e['target'] in ('code','research')];graph['nodes']=[n for n in graph['nodes'] if n['id']!='default'];graph['edges']=[e for e in graph['edges'] if e['source']!='default']
  for e in graph['edges']:
   if e['source']=='router':e.pop('route');e['when']='true' if e['target']=='code' else 'false'
  result=self.execute(graph);self.assertEqual(result['state'],'COMPLETED');self.assertEqual([n['id'] for n,_ in self.calls],['research']);decision=next(a for a in self.db.list('activation') if a['node']=='router')['condition_decision'];self.assertTrue(decision['missing']);self.assertFalse(decision['matched'])
 def test_native_invalid_output_contract_has_no_recoverable_completion(self):
  from studio.workbench_examples import examples
  from studio.workbench_scheduler import Scheduler
  base,rt=fixture(self, admission=True);graph=examples(rt.operator['profiles'])[0];node=graph['nodes'][1];node['output_contract']=[dict(field='result.required',type='number',required=True)];run=dict(id='contract-fail',workspace=base.project['id'],graph=graph,created_at=time.time(),calls=0);rt.flow.put('run',run['id'],run);scheduler=Scheduler(rt,run['id']);activation=scheduler.activation(node,'',0,[])
  report=dict(reply='answer',summary='summary',accepted=False,result_json='{}',artifacts=[],evidence=[],issues=[],next='')
  with patch('studio.workbench_scheduler.CLI.start',return_value=dict(report=report,session_id='synthetic-session',process=dict(exit=0))):
   with self.assertRaises(Invalid):scheduler.native_turn(node,activation['id'],[],'',0)
  self.assertFalse((rt.storage/'flow-artifacts'/run['id']/activation['id']/'completion.json').exists())

class UnexpectedWorkerTests(unittest.TestCase):
 setUp=RouterSchedulingTests.setUp
 graph=RouterSchedulingTests.graph
 execute=RouterSchedulingTests.execute
 def test_runtime_error_is_persisted_without_message_or_replay(self):
  from studio.workbench_scheduler import Scheduler
  from studio.workbench_graph import validate_graph
  graph=self.graph();graph['nodes'][1]['routes'][0]['test']=dict(field='result.task',op='contains',value='research')
  self.db.put('run','r',dict(id='r',graph=validate_graph(graph),state='QUEUED',task='research',created_at=time.time()))
  calls=[]
  def error(*args):calls.append(args[0]['id']);raise RuntimeError('SECRET_ERROR_PAYLOAD')
  Scheduler(self.rt,'r',error).run_graph();row=self.db.get('run','r');self.assertEqual(row['state'],'RECOVERY_REQUIRED');self.assertIsNotNone(row['finished_at']);self.assertEqual(row['unexpected_error'],'RuntimeError');self.assertNotIn('SECRET_ERROR_PAYLOAD',str(row));self.assertEqual(calls,['code']);self.assertFalse(any(a['state']=='RUNNING' for a in self.db.list('activation')))
 def test_cleanup_never_stops_another_run_or_foreign_host(self):
  from studio.workbench_failures import abort_unexpected
  from orchestration.store import process_identity
  import os
  me=process_identity(os.getpid());self.db.put('run','r',dict(id='r',state='RUNNING'))
  for key,run,host in [('owned','r',me['host']),('other','another',me['host']),('foreign','r','different-host')]:self.db.put('activation',key,dict(id=key,run=run,state='RUNNING',process=dict(me,pid=999999,pgid=999999,sid=999999,host=host)))
  with patch('studio.workbench_failures.group_alive',return_value=True),patch('studio.workbench_failures.stop') as stop:
   abort_unexpected(self.rt,'r',RuntimeError('x'));self.assertEqual(stop.call_count,1)
  self.assertEqual(self.db.get('activation','other')['state'],'RUNNING')

 def test_sqlite_failure_persists_only_fixed_diagnostic_not_error_payload(self):
  import sqlite3
  from studio.workbench_failures import abort_unexpected
  self.db.put('run','r',dict(id='r',state='RUNNING'))
  error=sqlite3.OperationalError('SECRET_DATABASE_PAYLOAD')
  error.sqlite_errorcode=sqlite3.SQLITE_BUSY
  error.sqlite_errorname='SECRET_FORGED_ENUM'
  abort_unexpected(self.rt,'r',error)
  row=self.db.get('run','r')
  self.assertEqual(row['state'],'RECOVERY_REQUIRED');self.assertEqual(row['sqlite_errorcode'],5)
  self.assertEqual(row['sqlite_errorname'],'SQLITE_BUSY');self.assertNotIn('SECRET_',str(row))
  abort_unexpected(self.rt,'r',RuntimeError('second error must not overwrite'))
  self.assertEqual(self.db.get('run','r'),row)
