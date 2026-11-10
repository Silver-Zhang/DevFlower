"""Frozen development topology and feedback scheduling over actual V2 records."""
from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from studio.graph import Invalid
from v2.definitions import compile_definition
from v2.designer_scheduler import DesignerScheduler
from v2.development_graph import EXIT

from test_v2_canvas_structure import edge, graph, node
import test_v2_execution as fixtures
from test_v2_git_platform import git, initialize_repo


def development_document(agents, *, rounds=2):
    body = graph([
        node('Seed','start'),
        node('Developer','agent',agent_id=agents[0]['id'],agent_revision=agents[0]['revision']),
        node('Tester','agent',agent_id=agents[1]['id'],agent_revision=agents[1]['revision']),
        node('Verify','tool',tool='verify-json',permission='workspace-read',path='check.py',
             expected_json='{"cases":[4,10]}'),
        node('Reviewer','agent',agent_id=agents[2]['id'],agent_revision=agents[2]['revision']),
        node('Gate','tool',tool='delivery-gate',permission='none',developer_node='Developer',
             tester_node='Tester',reviewer_node='Reviewer',verification_node='Verify'),
        node('RoundOutput','output')],
        [edge('rd'+str(i),source,target) for i,(source,target) in enumerate(zip(
            ('Seed','Developer','Tester','Verify','Reviewer','Gate'),
            ('Developer','Tester','Verify','Reviewer','Gate','RoundOutput')))])
    body['name']='Development round'
    document = graph([node('Start','start',initial_data='{"task":"Implement doubling"}'),
                     node('RepairLoop','loop',max_iterations=rounds,body=body,exit_condition=deepcopy(EXIT)),
                     node('Finish','output',format='json')],
                    [edge('launch','Start','RepairLoop'),edge('deliver','RepairLoop','Finish')])
    document['name']='Disposable development workflow'
    return document


class DevelopmentRuntimeTests(unittest.TestCase):
    setUp = fixtures.ExecutionTests.setUp

    def initial(self):
        self.project = self.root/'spaces'/'A'
        initialize_repo(self.project,'https://github.com/example/gate-fixture.git')
        (self.project/'check.py').write_text('print(0)\n')
        git(self.project,'add','check.py')
        git(self.project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','pin checker')
        self.saved = [dict(id=str(i)*32,revision=str(i)*64,
                           config=dict(provider='codex' if i<3 else 'claude',mode='Agent' if i==1 else 'Ask',
                                       permission='workspace-write' if i==1 else 'none',round_prompt='fixture'))
                      for i in (1,2,3)]
        self.doc = development_document(self.saved)

    def bind(self, node):
        saved = next(row for row in self.saved if row['id']==node['agent_id'])
        binding = {'cwd':str(self.project),'session_id':None}
        if saved['config']['permission']=='workspace-write':
            binding['v2_agent_scope']='workspace-write'
        return saved,binding

    def compile(self, document):
        draft = self.store.save(self.workspace,None,None,document)
        return compile_definition(self.store,None,self.workspace,draft,agent_binding=self.bind)

    def test_frozen_layout_checks_roles_test_entry_and_exit(self):
        self.initial()
        compiled = self.compile(self.doc)
        loop = compiled['graph']['nodes'][1]
        self.assertTrue(loop['development'])
        self.assertTrue(all(e['transfer']=='envelope' for e in loop['body']['edges']))
        verify = next(row for row in loop['body']['nodes'] if row['id']=='Verify')
        self.assertEqual(verify['expected_json'],{'cases':[4,10]})
        self.assertRegex(verify['entry_sha256'],r'^[0-9a-f]{64}$')
        wrong = deepcopy(self.doc)
        wrong['nodes'][1]['config']['exit_condition']['value']='REJECT'
        with self.assertRaisesRegex(Invalid,'ACCEPT'):
            self.compile(wrong)
        self.saved[2]['config']['permission']='workspace-write'
        with self.assertRaisesRegex(Invalid,'只读'):
            self.compile(self.doc)
        self.saved[2]['config']['permission']='none'
        wrong = deepcopy(self.doc)
        body = wrong['nodes'][1]['config']['body']
        body['nodes'][4]['agent_id'] = self.saved[0]['id']
        with self.assertRaisesRegex(Invalid,'独立'):
            self.compile(wrong)

    def run_rounds(self, rounds):
        self.initial()
        compiled = self.compile(development_document(self.saved,rounds=rounds))
        ident = 'development-fixture'
        run = dict(id=ident,workspace=self.workspace,graph=compiled['graph'],state='QUEUED',task='Double',
                   created_at=time.time(),designer_runtime=1)
        self.store.db.put('run',ident,run)
        rt = SimpleNamespace(flow=self.store.db,store=self.store,check_operator=lambda:None)
        developer_inputs = []
        def agent(node, _, inputs, __, round_number):
            if node['id']=='Developer':
                developer_inputs.append(inputs[0]['content']['result'])
            return dict(answer=node['id'],result={})
        def verify(rt, run_id, activation, node):
            return dict(answer='fixture only',result={'passed':activation['round']>1,'verification_sha256':'b'*64})
        def gate(rt, run_id, activation, node):
            verdict = 'REJECT' if activation['round']==1 else 'ACCEPT'
            return dict(answer='repair fixture',result=dict(gate_verdict=verdict,candidate_sha256='a'*64))
        with patch('v2.development_evidence.verify_candidate',verify), patch('v2.development_evidence.delivery_gate',gate):
            DesignerScheduler(rt,ident,executor=agent).run_graph()
        return self.store.db.get('run',ident),developer_inputs,self.store.db.events(ident)

    def test_rejected_round_returns_real_recorded_feedback_and_accept_stops(self):
        run, inputs, events = self.run_rounds(2)
        self.assertEqual(run['state'],'COMPLETED')
        self.assertEqual(run['output']['result']['gate_verdict'],'ACCEPT')
        self.assertEqual(inputs,[{'task':'Implement doubling'},{'gate_verdict':'REJECT','candidate_sha256':'a'*64}])
        self.assertEqual([(e['iteration_path'],e['gate_verdict']) for e in events if e['kind']=='delivery-gate-decided'],
                         [([1],'REJECT'),([2],'ACCEPT')])

    def test_nonaccept_at_limit_is_failed(self):
        run, inputs, events = self.run_rounds(1)
        self.assertEqual(run['state'],'FAILED')
        self.assertNotIn('output',run)
        self.assertEqual(len(inputs),1)
        self.assertIn('loop-limit-reached',[e['kind'] for e in events])
