"""Regression for the genuine nested-round defect found by a real Codex workflow.

Agent calls here are a deterministic protocol double, not model acceptance.
"""
from pathlib import Path
import json
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import digest
from studio.workbench_examples import examples
from studio.workbench_graph import validate_graph
from studio.workbench_scheduler import Scheduler
from studio.workbench_store import WorkbenchStore


class CompletesOnDone:
    def __init__(self,fn,args):self.fn=fn;self.args=args;self.completed=False;self.error=None
    def done(self):
        if not self.completed:
            try:self.fn(*self.args)
            except BaseException as exc:self.error=exc
            self.completed=True
        return True
    def result(self):
        assert self.completed
        if self.error:raise self.error
class CompletionWindowPool:
    def __init__(self,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def submit(self,fn,*args):return CompletesOnDone(fn,args)

class NestedRoundTests(unittest.TestCase):
    def nested_graph(self):
        profiles={'a':{'provider':'codex','model':'synthetic','effort':'high'},'b':{'provider':'claude','model':'synthetic','effort':'high'}}
        inner=examples(profiles)[1]
        body={'version':3,'name':'outer-body','nodes':[
            {'id':'start','kind':'start','name':'outer input'},
            {'id':'inner','kind':'loop','name':'inner loop','body':inner,'max_rounds':1,
             'until':{'field':'result.accepted','op':'equals','value':True}},
            {'id':'result','kind':'result','name':'outer result'}],
            'edges':[{'id':'in','source':'start','target':'inner'},{'id':'out','source':'inner','target':'result'}]}
        graph={'version':3,'name':'nested','budget':{'calls':20,'seconds':60},'nodes':[
            {'id':'start','kind':'start','name':'input'},
            {'id':'outer','kind':'loop','name':'outer loop','body':body,'max_rounds':2,
             'until':{'field':'result.score','op':'equals','value':2}},
            {'id':'result','kind':'result','name':'result'}],
            'edges':[{'id':'in','source':'start','target':'outer'},{'id':'out','source':'outer','target':'result'}]}
        return graph

    def test_nested_merge_executes_again_in_a_new_outer_round(self):
        graph=self.nested_graph()
        with tempfile.TemporaryDirectory() as temp:
            store=WorkbenchStore(Path(temp)/'flow.sqlite');rt=SimpleNamespace(flow=store,check_operator=lambda:None)
            store.put('run','nested',dict(id='nested',state='QUEUED',task='test distinct outer rounds',graph=validate_graph(graph),created_at=time.time(),paused=False,cancel_requested=False))
            summaries=[]
            def agent(node,key,inputs,prefix,iteration):
                if node['id']=='summary':summaries.append(inputs)
                return {'answer':node['id'],'result':{'accepted':True,'score':len(summaries)}}
            Scheduler(rt,'nested',agent).run_graph();result=store.get('run','nested')
            self.assertEqual(result['state'],'COMPLETED',result.get('reason'))
            self.assertEqual(len(summaries),2,'new outer round must not reuse prior inner merge/summary')
            merges=[a for a in store.list('activation') if a['node']=='outer/inner/merge']
            self.assertEqual(len(merges),2)
            self.assertNotEqual(merges[0]['id'],merges[1]['id'])
            self.assertEqual(result['output']['result']['score'],2)
            rows={a['id']:a for a in store.list('activation')}
            self.assertEqual({tuple(a['iteration_path']) for a in merges},{(1,1),(2,1)})
            for outer_round,inputs in enumerate(summaries,1):
                self.assertEqual(len(inputs),1)
                merged=rows[inputs[0]['activation']]
                self.assertEqual(merged['node'],'outer/inner/merge')
                self.assertEqual(inputs[0]['source'],merged['node'])
                self.assertEqual(inputs[0]['run'],'nested')
                self.assertEqual(inputs[0]['iteration'],1)
                self.assertEqual(inputs[0]['iteration_path'],[outer_round,1])
                self.assertEqual(merged['iteration_path'],[outer_round,1])
                self.assertEqual(inputs[0]['content'],merged['output'])
                sources=merged['output']['sources']
                self.assertEqual({i['source'] for i in sources},{'outer/inner/branchA','outer/inner/branchB'})
                for source in sources:
                    origin=rows[source['activation']]
                    self.assertEqual(source['source'],origin['node'])
                    self.assertEqual(source['run'],'nested')
                    self.assertEqual(source['iteration'],1)
                    self.assertEqual(source['iteration_path'],[outer_round,1])
                    self.assertEqual(origin['iteration_path'],[outer_round,1])
                    self.assertEqual(source['content'],origin['output'])
                    self.assertEqual(source['content']['result']['score'],outer_round-1)
            rounds=[e for e in store.events('nested') if e['kind']=='loop-round']
            self.assertEqual([e['iteration_path'] for e in rounds],[[1],[1,1],[2],[2,1]])
            for event in rounds:
                self.assertEqual(event['round'],event['iteration_path'][-1])
            handoffs=[e for e in store.events('nested') if e['kind'] in ('handoff-ready','handoff-received')]
            self.assertTrue(handoffs)
            for event in handoffs:
                origin=rows[event['activation']]
                self.assertEqual(event['source'],origin['node'])
                self.assertEqual(event['iteration_path'],origin['iteration_path'])
                self.assertEqual(event['content'],origin['output'])

    def test_native_session_and_initialization_reused_across_ancestor_rounds(self):
        graph=self.nested_graph()
        graph['nodes'][1]['until']['value']=4
        inner=graph['nodes'][1]['body']['nodes'][1]
        inner.update(max_rounds=2,until={'field':'result.inner_round','op':'equals','value':2})
        for node in inner['body']['nodes']:
            if node['kind']=='agent':node['initial_prompt']='INIT-MARK'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();(root/'native').mkdir()
            store=WorkbenchStore(root/'flow.sqlite')
            def binding(workspace,node):
                return dict(workspace=workspace,cwd=str(source),binding=dict(provider=node['provider'],
                    model=node['model'],effort=node['effort'],home=str(root/'native'/node['id']),session_id=None))
            rt=SimpleNamespace(flow=store,db=store,storage=root/'storage',check_operator=lambda:None,
                node_binding=binding,source_lock=lambda cwd:root/'source.lock',recheck_session=lambda chat:None,
                attachments=lambda cwd,paths:[],native_snapshot=lambda workspace,binding:{'native_path':str(Path(binding['home'])/'synthetic-history.jsonl')})
            store.put('run','native',dict(id='native',workspace='test',state='QUEUED',task='nested session reuse',
                graph=validate_graph(graph),created_at=time.time(),paused=False,cancel_requested=False))
            calls=[];summaries=[]
            def native_cli(provider,binding,options):
                def start(prompt,cwd,artifacts,schema,journal,run):
                    activation=store.get('activation',journal.key)
                    node=activation['node'];sid='session-'+node.replace('/','-')
                    calls.append(dict(node=node,path=activation['iteration_path'],session=binding['session_id'],prompt=prompt))
                    # A bounded native-history double for the new per-activation
                    # byte range. No genuine Provider execution is claimed here.
                    transcript=Path(binding['home'])/'synthetic-history.jsonl';transcript.parent.mkdir(parents=True,exist_ok=True)
                    with transcript.open('a') as stream:stream.write(json.dumps({'synthetic_turn':activation['id']})+'\n')
                    if node.endswith('/summary'):summaries.append(activation['id'])
                    journal.input_sent(None,len(prompt.encode()),digest(prompt.encode()))
                    return dict(session_id=sid,report=dict(reply=node,summary=node,accepted=True,
                        result_json=json.dumps(dict(score=len(summaries),inner_round=activation['iteration'])),
                        artifacts=[],evidence=[],issues=[],next=''))
                return SimpleNamespace(start=start)
            with patch('studio.workbench_scheduler.CLI',side_effect=native_cli), patch('studio.model_admission.ModelAdmission.require',return_value=dict(key='0'*64,verification_id='synthetic-nested-certificate')):
                Scheduler(rt,'native').run_graph()
            result=store.get('run','native')
            self.assertEqual(result['state'],'COMPLETED',result.get('reason'))
            self.assertEqual(result['output']['result']['score'],4)
            self.assertEqual(len(calls),12)
            chats=store.list('node-session');self.assertEqual(len(chats),3)
            for chat in chats:
                turns=sorted((a for a in store.list('activation') if a.get('chat_key')==chat['id']),key=lambda a:a['iteration_path'])
                self.assertEqual([a['iteration_path'] for a in turns],[[1,1],[1,2],[2,1],[2,2]])
                self.assertEqual(len({a['id'] for a in turns}),4)
                self.assertEqual([a['initialization_included'] for a in turns],[True,False,False,False])
                self.assertTrue(chat['initialized'])
                self.assertTrue(all(a['native_history_end']>a['native_history_offset'] for a in turns))
                self.assertEqual([a['native_history_offset'] for a in turns][1:],[a['native_history_end'] for a in turns][:-1])
                self.assertTrue(all(a['receipt']['session_id']==chat['binding']['session_id'] for a in turns))
                sent=sorted((c for c in calls if c['node']==chat['node']),key=lambda c:c['path'])
                self.assertEqual([c['session'] for c in sent],[None]+[chat['binding']['session_id']]*3)
                self.assertEqual([c['prompt'].count('INIT-MARK') for c in sent],[1,0,0,0])
            events=[e for e in store.events('native') if e['kind']=='handoff-sent']
            self.assertEqual(len(events),12)
            self.assertEqual({tuple(e['iteration_path']) for e in events},{(1,1),(1,2),(2,1),(2,2)})
            for event in events:
                origin=store.get('activation',event['activation'])
                self.assertEqual(event['source'],origin['node'])
                self.assertEqual(event['iteration_path'],origin['iteration_path'])

    def test_completed_future_cannot_leave_a_stale_activation_snapshot(self):
        graph=examples({'code':{'provider':'codex','model':'synthetic','effort':'high'},'review':{'provider':'claude','model':'synthetic','effort':'high'}})[0]
        graph['nodes']=[n for n in graph['nodes'] if n['id']!='review'];graph['edges']=[dict(id='in',source='start',target='analyze'),dict(id='out',source='analyze',target='result')]
        with tempfile.TemporaryDirectory() as temp:
            store=WorkbenchStore(Path(temp)/'flow.sqlite');rt=SimpleNamespace(flow=store,check_operator=lambda:None)
            store.put('run','window',dict(id='window',state='QUEUED',task='actual scheduling-window regression',graph=validate_graph(graph),created_at=time.time(),paused=False,cancel_requested=False))
            calls=[]
            def execute(node,key,inputs,prefix,iteration):calls.append(key);return {'answer':'completed exactly in the scheduling window','result':{'accepted':True}}
            with patch('studio.workbench_scheduler.ThreadPoolExecutor',CompletionWindowPool),patch('studio.workbench_scheduler.time.sleep',lambda _:None):Scheduler(rt,'window',execute).run_graph()
            row=store.get('run','window');self.assertEqual(row['state'],'COMPLETED',row.get('reason'));self.assertEqual(len(calls),1,'a completed side effect must never be repeated')
            self.assertEqual(row['output']['answer'],'completed exactly in the scheduling window')


if __name__=='__main__':unittest.main()
