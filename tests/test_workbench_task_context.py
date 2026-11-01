"""Synthetic request transport; no native model or approval is invoked."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.workbench_examples import examples
from studio.workbench_graph import request_sections, request_text, transfer, validate_graph
from studio.workbench_scheduler import Scheduler
from studio.workbench_store import WorkbenchStore

PROFILES={'a':{'provider':'codex','model':'fixture','effort':'high'},'b':{'provider':'codex','model':'fixture','effort':'high'}}
TASK='ORIGINAL CRITERIA: exactly three suggestions'


class TaskContextTests(unittest.TestCase):
    def test_template_nodes_receive_separate_original_context(self):
        seq,parallel,loop,*_=examples(PROFILES)
        nodes=[seq['nodes'][2], parallel['nodes'][4], loop['nodes'][1]['body']['nodes'][1], loop['nodes'][1]['body']['nodes'][2]]
        fake={'source':'upstream','content':{'result':{'task':'FORGED'},'answer':'output'}}
        for node in nodes:
            self.assertTrue(node['task_context'])
            for first in (True,False):
                parts=request_sections(node,[fake],first=first,task_context=TASK)
                self.assertEqual(next(p['text'] for p in parts if p['title'].startswith('本次任务目标')),TASK)
                self.assertIn('FORGED',next(p['text'] for p in parts if p['title'].startswith('上游输入')))
                self.assertEqual(request_text(node,[fake],first=first,task_context=TASK).count(TASK),1)
        self.assertEqual(transfer(fake['content'],{'transfer':'result'}),fake['content'])

    def test_legacy_and_explicit_false_do_not_get_new_context(self):
        for value in (None,False):
            node={'mode':'Ask','round_prompt':'work'}
            if value is not None:node['task_context']=value
            self.assertNotIn(TASK,request_text(node,[],first=True,task_context=TASK))
            self.assertNotIn(TASK,request_text(node,[],first=False))

    def test_boolean_validation_and_graph_identity(self):
        graph=examples(PROFILES)[0]
        for value in (1,'true',None,[],{}):
            g=copy.deepcopy(graph);g['nodes'][1]['task_context']=value
            with self.subTest(value=value),self.assertRaises(Invalid):validate_graph(g)
        g=validate_graph(graph);h=copy.deepcopy(g);h['nodes'][1]['task_context']=False
        self.assertNotEqual(digest(canonical(g)),digest(canonical(validate_graph(h))))
        del h['nodes'][1]['task_context'];self.assertNotIn('task_context',validate_graph(h)['nodes'][1])

    def test_sequential_parallel_and_loop_scheduler_requests(self):
        for graph in examples(PROFILES)[:3]:
            with self.subTest(graph=graph['name']), tempfile.TemporaryDirectory() as folder:
                base=Path(folder);db=WorkbenchStore(base/'flow.sqlite');history=base/'history';history.write_text('')
                binding=dict(provider='codex',model='fixture',effort='high',home=str(base),readonly_files=[])
                rt=SimpleNamespace(flow=db,db=db,storage=base,check_operator=lambda:None,
                    node_binding=lambda workspace,n:dict(workspace=workspace,cwd=str(base),binding=copy.deepcopy(binding)),
                    source_lock=lambda cwd:base/'lock',recheck_session=lambda chat,**kw:None,
                    native_snapshot=lambda workspace,b:dict(native_path=str(history)),attachments=lambda cwd,refs:[])
                graph=validate_graph(graph)
                db.put('run','run',dict(id='run',workspace='workspace',graph=graph,graph_sha256=digest(canonical(graph)),
                    candidate_sha256='c'*64,task=TASK,task_sha256=digest(TASK.encode()),created_at=time.time(),calls=0,state='QUEUED'))
                sent_ids=[]
                def cli_start(_self,prompt,cwd,artifacts,schema,journal,run_id):
                    sent_ids.append(journal.key)
                    a=db.get('activation',journal.key);journal.visible_request(prompt);journal.input_sent('synthetic',len(prompt.encode()),digest(prompt.encode()))
                    accepted=not (a['node']=='cycle/review' and a['iteration']==1)
                    report=dict(reply='FORGED UPSTREAM',summary='summary',accepted=accepted,
                        result_json=json.dumps({'accepted':accepted,'task':'FORGED','receipt':{'secret':'PRIVATE-UPSTREAM'}}),artifacts=[],evidence=[],issues=[],next='')
                    return dict(report=report,session_id='synthetic-'+a['node'],process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
                with ExitStack() as stack:
                    stack.enter_context(patch('studio.workbench_scheduler.source_lease',side_effect=lambda *a:nullcontext()))
                    stack.enter_context(patch('studio.workbench_scheduler.session_lease',side_effect=lambda *a:nullcontext()))
                    stack.enter_context(patch('studio.workbench_scheduler.workspace_hash',return_value='s'*64))
                    stack.enter_context(patch('studio.workbench_scheduler.identity',side_effect=Blocked('fixture')))
                    stack.enter_context(patch('studio.model_admission.ModelAdmission.require',return_value=None))
                    stack.enter_context(patch('studio.workbench_scheduler.CLI.start',autospec=True,side_effect=cli_start))
                    scheduler=Scheduler(rt,'run');scheduler.run_graph()
                    count=len(sent_ids)
                    scheduler.graph(graph,'',0,{'answer':TASK,'result':{'task':TASK}})
                    self.assertEqual(len(sent_ids),count,'refresh must not resend completed activations')
                self.assertEqual(db.get('run','run')['state'],'COMPLETED')
                agents=[a for a in db.list('activation') if a['kind']=='agent']
                for a in agents:
                    self.assertEqual(a['task_context_binding']['task_sha256'],digest(TASK.encode()))
                    self.assertEqual(a['input_sent_sha256'],a['actual_request_sha256'])
                    self.assertEqual(next(p['text'] for p in a['request_sections'] if p['title'].startswith('本次任务目标')),TASK)
                    self.assertNotIn('PRIVATE-UPSTREAM',a['actual_request'])
                if graph['name'].startswith('两 Agent'):self.assertIn('FORGED',next(a for a in agents if a['node']=='review')['actual_request'])
                if graph['name'].startswith('并发'):self.assertIn('FORGED',next(a for a in agents if a['node']=='summary')['actual_request'])
                if graph['name'].startswith('提案'):self.assertEqual({tuple(a['iteration_path']) for a in agents},{(1,),(2,)})

    def test_synthetic_native_turns_persist_bound_context_and_sent_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);db=WorkbenchStore(base/'flow.sqlite');history=base/'history';history.write_text('')
            node=copy.deepcopy(examples(PROFILES)[2]['nodes'][1]['body']['nodes'][1]);node['profile']='a'
            graph=examples(PROFILES)[0];candidate='c'*64;graph_sha=digest(canonical(graph))
            binding=dict(provider='codex',model='fixture',effort='high',home=str(base),readonly_files=[])
            rt=SimpleNamespace(flow=db,db=db,storage=base,check_operator=lambda:None,
                node_binding=lambda workspace,n:dict(workspace=workspace,cwd=str(base),binding=copy.deepcopy(binding)),
                source_lock=lambda cwd:base/'lock', recheck_session=lambda chat,**kw:None,
                native_snapshot=lambda workspace,b:dict(native_path=str(history)),attachments=lambda cwd,refs:[])
            run=dict(id='run',workspace='workspace',graph=graph,graph_sha256=graph_sha,candidate_sha256=candidate,source_sha256='s'*64,
                task=TASK,task_sha256=digest(TASK.encode()),created_at=time.time(),calls=0)
            db.put('run','run',run);scheduler=Scheduler(rt,'run');sent=[]
            report=dict(reply='answer',summary='summary',accepted=True,result_json='{}',artifacts=[],evidence=[],issues=[],next='')
            def cli_start(_self,prompt,cwd,artifacts,schema,journal,run_id):
                sent.append(prompt);journal.visible_request(prompt);journal.input_sent('synthetic',len(prompt.encode()),digest(prompt.encode()))
                return dict(report=report,session_id='synthetic',process=dict(exit=0,timeout=False,cancelled=False,overflow=False))
            with ExitStack() as stack:
                stack.enter_context(patch('studio.workbench_scheduler.source_lease',return_value=nullcontext()))
                stack.enter_context(patch('studio.workbench_scheduler.session_lease',return_value=nullcontext()))
                stack.enter_context(patch('studio.workbench_scheduler.workspace_hash',return_value='s'*64))
                stack.enter_context(patch('studio.workbench_scheduler.identity',side_effect=Blocked('fixture')))
                stack.enter_context(patch('studio.model_admission.ModelAdmission.require',return_value=None))
                stack.enter_context(patch('studio.workbench_scheduler.CLI.start',autospec=True,side_effect=cli_start))
                for round_number in (1,2):
                    a=scheduler.activation(node,'cycle/',round_number,[round_number])
                    scheduler.native_turn(node,a['id'],[dict(source='upstream',content={'task':'FORGED'})],'cycle/',round_number)
                    row=db.get('activation',a['id']);bound=row['task_context_binding']
                    self.assertEqual(bound,dict(run='run',activation=a['id'],node='cycle/proposal',iteration_path=[round_number],candidate_sha256=candidate,source_sha256='s'*64,graph_sha256=graph_sha,task_sha256=digest(TASK.encode()),context_sha256=digest(TASK.encode())))
                    self.assertEqual(row['actual_request_sha256'],digest(sent[-1].encode()))
                    self.assertEqual(row['input_sent_sha256'],row['actual_request_sha256'])
                    self.assertEqual(next(p['text'] for p in row['request_sections'] if p['title'].startswith('本次任务目标')),TASK)
                    self.assertIn('FORGED',sent[-1]);self.assertIn(TASK,sent[-1])
                self.assertEqual(sent[0].count('本次运行初始化'),1)
                self.assertNotIn('本次运行初始化',sent[1])
                disabled=copy.deepcopy(node);disabled['task_context']=False
                a=scheduler.activation(disabled,'off/',1,[1]);scheduler.native_turn(disabled,a['id'],[],'off/',1)
                self.assertNotIn(TASK,sent[-1]);self.assertNotIn('task_context_binding',db.get('activation',a['id']))
                old=copy.deepcopy(disabled);del old['task_context']
                a=scheduler.activation(old,'legacy/',1,[1]);scheduler.native_turn(old,a['id'],[],'legacy/',1)
                self.assertNotIn(TASK,sent[-1]);self.assertNotIn('task_context_binding',db.get('activation',a['id']))
                (base/'.codex').mkdir();(base/'.codex'/'auth.json').write_text(json.dumps({'access_token':'SYNTHETIC_SECRET_VALUE'}))
                sensitive=TASK+' SYNTHETIC_SECRET_VALUE';db.change('run','run',{'task':sensitive,'task_sha256':digest(sensitive.encode())})
                a=scheduler.activation(node,'redacted/',1,[1]);scheduler.native_turn(node,a['id'],[],'redacted/',1)
                row=db.get('activation',a['id']);part=next(p['text'] for p in row['request_sections'] if p['title'].startswith('本次任务目标'))
                self.assertNotIn('SYNTHETIC_SECRET_VALUE',sent[-1]);self.assertIn('[REDACTED]',part)
                self.assertEqual(row['task_context_binding']['task_sha256'],digest(sensitive.encode()))
                self.assertEqual(row['task_context_binding']['context_sha256'],digest(part.encode()))
                db.change('run','run',{'task':'changed after freeze'})
                a=scheduler.activation(node,'tampered/',1,[1])
                with self.assertRaises(Blocked):scheduler.native_turn(node,a['id'],[],'tampered/',1)
                self.assertEqual(len(sent),5)

if __name__=='__main__':unittest.main()
