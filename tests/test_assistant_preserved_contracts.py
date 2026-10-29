"""Prompt edits must retain real data guards; no model or network calls."""
import copy
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio.canvas_assistant import expand_plan
from studio.configuration_assistant import plan_graph,ConfigurationAssistant
from orchestration.common import canonical,digest
from studio.data_contracts import check
from studio.graph import Invalid
from studio.workbench_graph import validate_graph


class PreservedAssistantContracts(unittest.TestCase):
    def condition_fixture(self):
        plan=copy.deepcopy(self.plan)
        plan['nodes'].insert(2,dict(self.plan['nodes'][1],id='cond',kind='condition',test=dict(field='result.score',op='exists',value=True)))
        plan['edges']=[dict(source='start',target='agent'),dict(source='agent',target='cond'),dict(source='cond',target='result')]
        raw=plan_graph(plan,self.choices);raw['nodes'][2].pop('input_contract')
        return plan,raw

    def test_default_condition_contract_prevents_implicit_read_change(self):
        plan,raw=self.condition_fixture();before=copy.deepcopy(raw)
        self.assertEqual(validate_graph(raw)['nodes'][2]['input_contract'],[dict(field='result.score',type='any',required=False)])
        plan['nodes'][2]['test']['field']='result.other'
        with self.assertRaisesRegex(Invalid,'输入合同'):plan_graph(plan,self.choices,previous=raw)
        self.assertEqual(raw,before)

    def test_default_condition_contract_prevents_omission_and_kind_change(self):
        for mode in ('omit','replace'):
            plan,raw=self.condition_fixture()
            if mode=='omit':plan['nodes'].pop(2);plan['edges']=[dict(source='start',target='agent'),dict(source='agent',target='result')]
            else:plan['nodes'][2]['kind']='merge';plan['nodes'][2].pop('test')
            with self.subTest(mode=mode),self.assertRaisesRegex(Invalid,'cond.*已有数据合同'):
                plan_graph(plan,self.choices,previous=raw)

    def test_default_and_none_contracts_are_retained_without_aliasing(self):
        for setting in ('missing',None):
            plan,raw=self.condition_fixture()
            if setting is None:raw['nodes'][2]['input_contract']=None
            before=copy.deepcopy(raw);updated=plan_graph(plan,self.choices,previous=raw)
            self.assertEqual(updated['nodes'][2]['input_contract'],[dict(field='result.score',type='any',required=False)])
            updated['nodes'][2]['input_contract'][0]['field']='result.changed'
            self.assertEqual(raw,before)

    def test_nested_default_guard_and_explicit_empty_contract(self):
        inner,raw_inner=self.condition_fixture()
        plan=copy.deepcopy(self.plan);plan['nodes'][1].update(id='outer',kind='loop',body=inner,max_rounds=2,until=dict(field='result.accepted',op='equals',value=True))
        plan['edges']=[dict(source='start',target='outer'),dict(source='outer',target='result')]
        old=plan_graph(plan,self.choices);old['nodes'][1]['body']=raw_inner
        body=plan['nodes'][1]['body'];body['nodes'].pop(2);body['edges']=[dict(source='start',target='agent'),dict(source='agent',target='result')]
        with self.assertRaisesRegex(Invalid,'outer/cond.*已有数据合同'):plan_graph(plan,self.choices,previous=old)
        old['nodes'][1]['body']['nodes'][2]['input_contract']=[]
        self.assertEqual(plan_graph(plan,self.choices,previous=old)['nodes'][1]['body']['nodes'][1]['id'],'agent')

    def test_default_router_contract_blocks_omission(self):
        plan,raw=self.condition_fixture();node=raw['nodes'][2];node.update(kind='router',routes=[dict(label='ok',test=dict(field='result.score',op='exists',value=True))]);node.pop('test')
        raw['edges'][2]['route']='ok'
        self.assertEqual(validate_graph(raw)['nodes'][2]['input_contract'],[dict(field='result.score',type='any',required=False)])
        plan['nodes'].pop(2);plan['edges']=[dict(source='start',target='agent'),dict(source='agent',target='result')]
        with self.assertRaisesRegex(Invalid,'cond.*已有数据合同'):plan_graph(plan,self.choices,previous=raw)

    def test_invalid_raw_base_keeps_cas_and_can_be_repaired(self):
        plan,raw=self.condition_fixture();raw['nodes'][1]['mode']='unsupported';raw['nodes'][1].pop('round_prompt',None)
        before=copy.deepcopy(raw)
        runtime=SimpleNamespace(check_operator=lambda:None,session_center=SimpleNamespace(workspace=lambda _:dict(path='/synthetic')))
        assistant=ConfigurationAssistant(runtime)
        with patch.object(assistant,'choices',return_value=self.choices):
            base=assistant._base(dict(workspace='w',graph=raw),canvas_only=True,allow_invalid=True)
        self.assertEqual(base['base_graph'],before);self.assertEqual(base['base_graph_sha256'],digest(canonical(before)))
        repaired=plan_graph(plan,self.choices,previous=base['base_graph'])
        self.assertEqual(repaired['nodes'][1]['mode'],'Ask');self.assertEqual(repaired['nodes'][2]['input_contract'][0]['field'],'result.score')
        self.assertEqual(raw,before)
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
        plan['nodes'].insert(2,copy.deepcopy(self.plan['nodes'][1]))
        plan['edges']=[dict(source='start',target='new_agent'),dict(source='new_agent',target='agent'),dict(source='agent',target='result')]
        updated=plan_graph(plan,self.choices,previous=self.guarded())['nodes'][1]
        self.assertEqual(updated['input_contract'],[]);self.assertEqual(updated['output_contract'],[])

    def test_omitted_or_replaced_guarded_identity_is_rejected(self):
        for replacement in (False,True):
            if replacement:
                previous=self.guarded();plan=copy.deepcopy(self.plan)
                plan['nodes'][1]['id']='new_agent'
                plan['edges']=[dict(source='start',target='new_agent'),dict(source='new_agent',target='result')]
            else:
                old=copy.deepcopy(self.plan);old['nodes'].insert(2,dict(old['nodes'][1],id='other',name='other'))
                old['edges']=[dict(source='start',target='agent'),dict(source='agent',target='other'),dict(source='other',target='result')]
                previous=plan_graph(old,self.choices);previous['nodes'][1]['input_contract']=[dict(field='answer',type='string',required=True)]
                plan=copy.deepcopy(old);plan['nodes'].pop(1)
                plan['edges']=[dict(source='start',target='other'),dict(source='other',target='result')]
            with self.subTest(replacement=replacement),self.assertRaisesRegex(Invalid,'agent.*已有数据合同.*显式编辑'):
                plan_graph(plan,self.choices,previous=previous)

    def loop_plan(self):
        plan=copy.deepcopy(self.plan)
        plan['nodes'][1].update(id='outer',kind='loop',body=copy.deepcopy(self.plan),max_rounds=2,
                                until=dict(field='result.accepted',op='equals',value=True))
        plan['edges']=[dict(source='start',target='outer'),dict(source='outer',target='result')]
        previous=plan_graph(plan,self.choices)
        previous['nodes'][1]['body']['nodes'][1]['output_contract']=[dict(field='result.score',type='number',required=True)]
        return plan,previous

    def test_removing_loop_with_guarded_descendant_is_rejected(self):
        plan,previous=self.loop_plan();plan['nodes'][1]=copy.deepcopy(self.plan['nodes'][1]);plan['edges']=copy.deepcopy(self.plan['edges'])
        with self.assertRaisesRegex(Invalid,'outer/agent.*已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_preserving_loop_but_omitting_guarded_descendant_is_rejected(self):
        plan,previous=self.loop_plan();body=plan['nodes'][1]['body'];body['nodes'][1]['id']='new_agent'
        body['edges']=[dict(source='start',target='new_agent'),dict(source='new_agent',target='result')]
        with self.assertRaisesRegex(Invalid,'outer/agent.*已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_loop_identity_cannot_change_kind_to_discard_child_guard(self):
        plan,previous=self.loop_plan();plan['nodes'][1]=dict(self.plan['nodes'][1],id='outer')
        with self.assertRaisesRegex(Invalid,'outer/agent.*已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_identical_nested_id_in_another_scope_cannot_move_guard(self):
        plan,previous=self.loop_plan();body=plan['nodes'][1]['body'];body['nodes'][1]['id']='fresh'
        body['edges']=[dict(source='start',target='fresh'),dict(source='fresh',target='result')]
        plan['nodes'].insert(2,copy.deepcopy(self.plan['nodes'][1]))
        plan['edges']=[dict(source='start',target='outer'),dict(source='outer',target='agent'),dict(source='agent',target='result')]
        with self.assertRaisesRegex(Invalid,'outer/agent.*已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_unguarded_node_can_be_removed(self):
        old=copy.deepcopy(self.plan);old['nodes'].insert(2,dict(old['nodes'][1],id='fresh',name='fresh'))
        old['edges']=[dict(source='start',target='agent'),dict(source='agent',target='fresh'),dict(source='fresh',target='result')]
        previous=plan_graph(old,self.choices);plan=copy.deepcopy(old);plan['nodes'].pop(1)
        plan['edges']=[dict(source='start',target='fresh'),dict(source='fresh',target='result')]
        updated=plan_graph(plan,self.choices,previous=previous)
        self.assertEqual(updated['nodes'][1]['id'],'fresh')

    def test_deeply_nested_guard_path_is_reported(self):
        plan,unused=self.loop_plan();inner=plan['nodes'][1]['body']['nodes'][1]
        inner.update(kind='loop',body=copy.deepcopy(self.plan),max_rounds=2,
                     until=dict(field='result.accepted',op='equals',value=True))
        previous=plan_graph(plan,self.choices)
        previous['nodes'][1]['body']['nodes'][1]['body']['nodes'][1]['input_contract']=[dict(field='answer',type='string',required=True)]
        plan['nodes'][1]=dict(self.plan['nodes'][1],id='outer')
        with self.assertRaisesRegex(Invalid,'outer/agent/agent.*已有数据合同'):
            plan_graph(plan,self.choices,previous=previous)

    def test_existing_task_context_scope_and_new_node_defaults_remain(self):
        for setting in (None,False,True):
            previous=self.guarded();old=previous['nodes'][1]
            if setting is None:old.pop('task_context',None)
            else:old['task_context']=setting
            plan=copy.deepcopy(self.plan);plan['nodes'].insert(2,dict(plan['nodes'][1],id='new_agent'))
            plan['edges']=[dict(source='start',target='agent'),dict(source='agent',target='new_agent'),dict(source='new_agent',target='result')]
            result=plan_graph(plan,self.choices,previous=previous)
            if setting is None:self.assertNotIn('task_context',result['nodes'][1])
            else:self.assertIs(result['nodes'][1]['task_context'],setting)
            self.assertIs(result['nodes'][2]['task_context'],True)

    def test_unknown_edge_metadata_is_named_and_remains_rejected(self):
        plan=copy.deepcopy(self.plan);plan['edges'][0]['id']='readonly-edge-id'
        with self.assertRaises(Invalid) as caught:expand_plan(plan,self.choices)
        self.assertIn('edges[0]',str(caught.exception));self.assertIn('id',str(caught.exception))
        self.assertIn('原草稿未修改',str(caught.exception))


if __name__=='__main__':unittest.main()
