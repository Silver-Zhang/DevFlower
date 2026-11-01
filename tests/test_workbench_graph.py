"""Protocol/store/scheduler negative tests. Synthetic agents are not native acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_examples import examples
from studio.workbench_graph import validate_graph,predicate,request_text,transfer
from studio.workbench_scheduler import Scheduler,REPLY
from studio.workbench_store import WorkbenchStore

PROFILES={'a':{'provider':'codex','model':'registered-test-model','effort':'high'},'b':{'provider':'claude','model':'registered-test-model','effort':'high'}}


class GraphContractTests(unittest.TestCase):
    def test_native_output_schema_is_strict_without_limiting_structured_fields(self):
        self.assertFalse(REPLY['additionalProperties']);self.assertEqual(set(REPLY['required']),set(REPLY['properties']))
        self.assertEqual(REPLY['properties']['result_json']['type'],'string')
    def test_three_examples_validate_without_fixed_development_chain(self):
        for graph in examples(PROFILES):self.assertEqual(validate_graph(graph)['version'],3)

    def test_legacy_is_not_silently_migrated(self):
        with self.assertRaises(Invalid):validate_graph({'version':2})

    def test_unbounded_cycle_rejected(self):
        g=examples(PROFILES)[0];g['edges'].append({'id':'back','source':'review','target':'analyze'})
        with self.assertRaises(Invalid):validate_graph(g)

    def test_duplicate_edge_and_nodes_rejected(self):
        for field in ('edges','nodes'):
            g=examples(PROFILES)[0];g[field].append(copy.deepcopy(g[field][0]))
            with self.assertRaises(Invalid):validate_graph(g)

    def test_disconnected_nodes_and_implicit_fanin_refused(self):
        g=examples(PROFILES)[0];g['edges']=g['edges'][:-1]
        with self.assertRaises(Invalid):validate_graph(g)
        g=examples(PROFILES)[1];g['nodes'][3]['kind']='agent'
        with self.assertRaises(Invalid):validate_graph(g)

    def test_loop_budget_is_integer_and_at_most_five(self):
        for value in (0,6,True,'5'):
            g=examples(PROFILES)[2];g['nodes'][1]['max_rounds']=value
            with self.assertRaises(Invalid):validate_graph(g)

    def test_no_eval_or_shell_predicates(self):
        for field in ('__import__("os").system("id")','x[0]','x;rm -rf /'):
            with self.assertRaises(Invalid):predicate({},dict(field=field,op='equals',value=True))

    def test_condition_boolean_and_number_are_distinct(self):
        self.assertFalse(predicate({'result':{'accepted':1}},dict(field='result.accepted',op='equals',value=True)))
        with self.assertRaises(Invalid):predicate({'result':{'score':True}},dict(field='result.score',op='greater',value=0))

    def test_condition_numeric_equality_uses_json_number_semantics(self):
        for actual,expected in [(1.0,1),(-2.0,-2),(0,0.0),(1e8,100000000)]:
            with self.subTest(actual=actual,expected=expected):
                self.assertTrue(predicate({'result':{'score':actual}},dict(field='result.score',op='equals',value=expected)))
                self.assertFalse(predicate({'result':{'score':actual}},dict(field='result.score',op='not_equals',value=expected)))
        for actual,expected in [(True,1),(1,'1'),(None,0),(2,1.0)]:
            self.assertFalse(predicate({'result':{'score':actual}},dict(field='result.score',op='equals',value=expected)))

    def test_prompt_lifecycle_new_activation_includes_init_resume_does_not(self):
        n={'initial_prompt':'INIT-MARK','round_prompt':'ROUND-MARK','handoff_prompt':'OUTPUT-MARK'}
        first=request_text(n,[{'source':'A','content':'upstream-data'}],first=True)
        later=request_text(n,[],first=False)
        self.assertEqual(first.count('INIT-MARK'),1);self.assertNotIn('INIT-MARK',later)
        self.assertIn('ROUND-MARK',later);self.assertIn('OUTPUT-MARK',later);self.assertIn('upstream-data',first)

    def test_context_truncation_is_explicit(self):
        r=transfer({'answer':'x'*1000},{'transfer':'answer','max_chars':128})
        self.assertTrue(r['truncated']);self.assertEqual(r['budget'],128)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=WorkbenchStore(Path(self.tmp.name)/'flow.sqlite')
    def tearDown(self):self.tmp.cleanup()
    def test_draft_cas_keeps_revisions_and_rejects_stale_save(self):
        one=self.db.save('x',examples(PROFILES)[0],None);two=self.db.save('x',examples(PROFILES)[1],one['revision'])
        with self.assertRaises(Conflict):self.db.save('x',examples(PROFILES)[2],one['revision'])
        self.assertEqual(self.db.get('draft','x'),two)
        with self.db.tx() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0],2)
    def test_state_and_event_share_transaction(self):
        self.db.put('activation','a',dict(run='r',node='n',state='PENDING',iteration=2))
        self.db.change('activation','a',{'state':'COMPLETED'},states={'PENDING'},event={'kind':'done'})
        self.assertEqual(self.db.events('r')[0]['node'],'n');self.assertEqual(self.db.events('r')[0]['iteration'],2)
        with self.assertRaises(Conflict):self.db.change('activation','a',{'state':'FAILED'},states={'PENDING'},event={'kind':'bad'})
        self.assertEqual(len(self.db.events('r')),1)
    def test_reconnect_cursor_does_not_replay_all_events(self):
        self.db.event('r','one');last=self.db.event('r','two');self.db.event('other','private')
        self.assertEqual([x['kind'] for x in self.db.events('r',last['seq']-1)],['two'])


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self);self.addCleanup(self.tmp.cleanup);self.runtime=SimpleNamespace(flow=self.db,check_operator=lambda:None)
        self.calls=[];self.lock=threading.Lock()
    def run_graph(self,graph,fn=None):
        self.db.put('run','r',dict(id='r',graph=validate_graph(graph),state='QUEUED',task='actual task input',created_at=time.time(),paused=False,cancel_requested=False))
        def agent(n,key,inputs,prefix,iteration):
            with self.lock:self.calls.append((n['id'],iteration,inputs))
            return (fn(n,iteration,inputs) if fn else {'answer':n['id'],'result':{'accepted':True}})
        Scheduler(self.runtime,'r',agent).run_graph();return self.db.get('run','r')
    def test_sequential_agents_use_actual_upstream_without_entire_chat(self):
        r=self.run_graph(examples(PROFILES)[0]);self.assertEqual(r['state'],'COMPLETED')
        self.assertEqual([x[0] for x in self.calls],['analyze','review'])
        self.assertEqual(self.calls[1][2][0]['source'],'analyze');self.assertNotIn('history',self.calls[1][2][0])
    def test_stop_during_native_turn_marks_cancelled_and_never_starts_downstream(self):
        def agent(n,i,inputs):
            self.db.change('run','r',{'cancel_requested':True})
            raise Invalid('native process cancelled by actual supervisor')
        r=self.run_graph(examples(PROFILES)[0],agent)
        self.assertEqual(r['state'],'CANCELLED');self.assertEqual([x[0] for x in self.calls],['analyze'])
        node=next(x for x in self.db.list('activation') if x['node']=='analyze')
        self.assertEqual(node['state'],'CANCELLED')
    def test_two_real_threads_join_once_with_both_provenance(self):
        barrier=threading.Barrier(2);seen=[]
        def agent(n,iteration,inputs):
            if n['id'] in ('branchA','branchB'):barrier.wait(timeout=2)
            else:seen.append(inputs)
            return {'answer':n['id'],'result':{'accepted':True}}
        r=self.run_graph(examples(PROFILES)[1],agent);self.assertEqual(r['state'],'COMPLETED');self.assertEqual(len(seen),1)
        merge=next(x for x in self.db.list('activation') if x['node']=='merge')
        self.assertEqual({i['source'] for i in merge['output']['sources']},{'branchA','branchB'})
        self.assertTrue(all(i['run']=='r' and i['iteration']==0 for i in merge['output']['sources']))
    def test_failed_upstream_terminates_merge_not_infinite_wait(self):
        def agent(n,i,inputs):
            if n['id']=='branchB':raise Invalid('real synthetic protocol failure')
            return {'answer':'A','result':{}}
        r=self.run_graph(examples(PROFILES)[1],agent);self.assertEqual(r['state'],'FAILED')
        merge=next(x for x in self.db.list('activation') if x['node']=='merge');self.assertEqual(merge['state'],'SKIPPED')
    def test_loop_early_exit_and_same_round_only(self):
        def agent(n,i,inputs):
            loop=next(a for a in self.db.list('activation') if a['node']=='cycle')
            self.assertEqual(loop['state'],'RUNNING')
            return {'answer':str(i),'result':{'accepted':i==2}}
        r=self.run_graph(examples(PROFILES)[2],agent);self.assertEqual(r['state'],'COMPLETED')
        self.assertEqual([(n,i) for n,i,_ in self.calls],[('proposal',1),('review',1),('proposal',2),('review',2)])
        for n,i,inputs in self.calls:self.assertTrue(all(x['iteration']==i for x in inputs))
    def test_loop_limit_is_a_failure_not_fake_success(self):
        r=self.run_graph(examples(PROFILES)[2],lambda n,i,x:{'answer':'no','result':{'accepted':False}})
        self.assertEqual(r['state'],'FAILED');self.assertIn('最大轮数',r['reason']);self.assertEqual(len(self.calls),10)
    def test_condition_nonselected_branch_is_skipped(self):
        g=examples(PROFILES)[1];g['nodes'][0]['kind']='start';g['nodes'].insert(1,{'id':'choose','kind':'condition','name':'choose','test':{'field':'result.accepted','op':'equals','value':True}})
        g['edges']=[e for e in g['edges'] if e['source']!='start'];g['edges'] += [{'id':'to_choose','source':'start','target':'choose'}, {'id':'yes','source':'choose','target':'branchA','when':'true'},{'id':'no','source':'choose','target':'branchB','when':'false'}]
        # Alternative branches converge at the result. A default all-input
        # merge is separately required to refuse a missing branch's payload.
        g['nodes']=[n for n in g['nodes'] if n['id'] not in ('merge','summary')]
        g['edges']=[e for e in g['edges'] if e['source'] not in ('merge','summary') and e['target'] not in ('merge','summary')]
        g['edges'] += [{'id':'a_result','source':'branchA','target':'result'},{'id':'b_result','source':'branchB','target':'result'}]
        r=self.run_graph(g);self.assertEqual(r['state'],'COMPLETED')
        a=next(x for x in self.db.list('activation') if x['node']=='branchA');self.assertEqual(a['state'],'SKIPPED')

    def test_float_upstream_selects_equal_integer_branch(self):
        g=examples(PROFILES)[1];source=next(n for n in examples(PROFILES)[0]['nodes'] if n['id']=='analyze')
        g['nodes']=[n for n in g['nodes'] if n['id'] not in ('merge','summary')]
        g['nodes'].extend([source,{'id':'choose','kind':'condition','name':'choose','test':{'field':'result.score','op':'equals','value':1}}])
        g['edges']=[{'id':'start_source','source':'start','target':'analyze'},{'id':'source_choose','source':'analyze','target':'choose'},{'id':'yes','source':'choose','target':'branchA','when':'true'},{'id':'no','source':'choose','target':'branchB','when':'false'},{'id':'a_result','source':'branchA','target':'result'},{'id':'b_result','source':'branchB','target':'result'}]
        result=self.run_graph(g,lambda n,i,inputs:{'answer':'synthetic','result':{'score':1.0}})
        self.assertEqual(result['state'],'COMPLETED')
        states={a['node']:a['state'] for a in self.db.list('activation')}
        self.assertEqual(states['branchA'],'COMPLETED');self.assertEqual(states['branchB'],'SKIPPED')





class IncompleteMergeTests(unittest.TestCase):
    def test_default_merge_never_passes_with_a_filtered_upstream_input(self):
        with tempfile.TemporaryDirectory() as temp:
            db=WorkbenchStore(Path(temp)/'flow.sqlite');rt=SimpleNamespace(flow=db,check_operator=lambda:None)
            graph=examples(PROFILES)[1];graph['nodes'][1].update(kind='condition',test={'field':'result.missing','op':'exists'})
            next(e for e in graph['edges'] if e['source']=='branchA' and e['target']=='merge')['when']='true'
            db.put('run','r',dict(id='r',state='QUEUED',task='conditional missing input',graph=validate_graph(graph),created_at=time.time()))
            calls=[]
            def agent(n,*args):calls.append(n['id']);return {'answer':'actual synthetic result','result':{}}
            Scheduler(rt,'r',agent).run_graph();merge=next(a for a in db.list('activation') if a['node']=='merge')
            self.assertEqual(merge['state'],'SKIPPED');self.assertEqual(merge['received'],1);self.assertEqual(merge['expected'],2)
            self.assertNotIn('output',merge);self.assertNotIn('summary',calls);self.assertEqual(db.get('run','r')['state'],'FAILED')


if __name__=="__main__":unittest.main()
