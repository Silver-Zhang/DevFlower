"""Prompt edits must retain real data guards; no model or network calls."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio.canvas_assistant import expand_plan
from studio.configuration_assistant import plan_graph
from studio.data_contracts import check
from studio.graph import Invalid


class PreservedAssistantContracts(unittest.TestCase):
    def setUp(self):
        self.choices={'p':dict(provider='codex',models=[dict(id='m',efforts=['high'])])}
        self.plan=dict(version=1,name='合同保留反例',summary='synthetic',nodes=[
            dict(id=i,kind=k,name=i,profile='p' if k=='agent' else '',
                 model='m' if k=='agent' else '',effort='high' if k=='agent' else '',
                 mode='Ask',initial_prompt='init',round_prompt='task',handoff_prompt='reply')
            for i,k in [('start','start'),('agent','agent'),('result','result')]],
            edges=[dict(source='start',target='agent'),dict(source='agent',target='result')])

    def guarded(self):
        previous=plan_graph(self.plan,self.choices)
        node=previous['nodes'][1]
        node['input_contract']=[dict(field='answer',type='string',required=True)]
        node['output_contract']=[dict(field='result.score',type='number',required=True)]
        return previous

    def test_prompt_edit_retains_enforcement_and_does_not_alias_original(self):
        previous=self.guarded();before=copy.deepcopy(previous)
        plan=copy.deepcopy(self.plan);plan['nodes'][1]['round_prompt']='只修改提示词'
        updated=plan_graph(plan,self.choices,previous=previous)['nodes'][1]
        for key in ('input_contract','output_contract'):
            self.assertEqual(updated[key],previous['nodes'][1][key])
            with self.assertRaises(Invalid):check({},updated[key])
        check({'answer':'real input'},updated['input_contract'])
        check({'result':{'score':1}},updated['output_contract'])
        with self.assertRaises(Invalid):check({'result':{'score':True}},updated['output_contract'])
        updated['input_contract'][0]['required']=False
        self.assertEqual(previous,before)

    def test_nested_prompt_edit_retains_inner_and_outer_guards(self):
        plan=copy.deepcopy(self.plan);loop=plan['nodes'][1]
        loop.update(kind='loop',body=copy.deepcopy(self.plan),max_rounds=2,
                    until=dict(field='result.accepted',op='equals',value=True))
        previous=plan_graph(plan,self.choices)
        outer=previous['nodes'][1]
        outer['input_contract']=[dict(field='answer',type='string',required=True)]
        outer['body']['nodes'][1]['output_contract']=[dict(field='result.score',type='number',required=True)]
        updated=plan_graph(plan,self.choices,previous=previous)['nodes'][1]
        with self.assertRaises(Invalid):check({},updated['input_contract'])
        with self.assertRaises(Invalid):check({},updated['body']['nodes'][1]['output_contract'])

    def test_existing_guarded_identity_cannot_be_reused_to_change_kind(self):
        previous=self.guarded();plan=copy.deepcopy(self.plan)
        plan['nodes'][1].update(kind='loop',body=copy.deepcopy(self.plan),max_rounds=2,
                                until=dict(field='result.accepted',op='equals',value=True))
        with self.assertRaisesRegex(Invalid,'已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_unknown_metadata_stays_rejected_with_nested_path_and_keys(self):
        plan=copy.deepcopy(self.plan);plan['nodes'][1].update(kind='loop',body=copy.deepcopy(self.plan))
        nested=plan['nodes'][1]['body']['nodes'][1]
        nested.update(provider='codex',input_contract=[])
        with self.assertRaises(Invalid) as caught:expand_plan(plan,self.choices)
        message=str(caught.exception)
        self.assertIn('agent/agent',message);self.assertIn('provider',message)
        self.assertIn('input_contract',message);self.assertIn('原草稿未修改',message)

    def test_new_identity_does_not_inherit_another_nodes_guard_by_name(self):
        plan=copy.deepcopy(self.plan);plan['nodes'][1]['id']='new_agent'
        plan['edges']=[dict(source='start',target='new_agent'),dict(source='new_agent',target='result')]
        updated=plan_graph(plan,self.choices,previous=self.guarded())['nodes'][1]
        self.assertEqual(updated['input_contract'],[]);self.assertEqual(updated['output_contract'],[])

    def test_unknown_edge_metadata_is_named_and_remains_rejected(self):
        plan=copy.deepcopy(self.plan);plan['edges'][0]['id']='readonly-edge-id'
        with self.assertRaises(Invalid) as caught:expand_plan(plan,self.choices)
        self.assertIn('edges[0]',str(caught.exception));self.assertIn('id',str(caught.exception))
        self.assertIn('原草稿未修改',str(caught.exception))


if __name__=='__main__':unittest.main()
