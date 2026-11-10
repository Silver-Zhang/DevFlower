"""Typed scheduling fixtures over the production V2 SQLite/event store."""
import time
from types import SimpleNamespace
from unittest import TestCase

from studio.graph import Invalid
from v2.definitions import compile_definition
from v2.designer_graph import condition
from v2.designer_scheduler import DesignerScheduler, activation_key

from test_v2_canvas_structure import edge, graph, node
import test_v2_execution as fixtures


class DesignerRuntimeTests(TestCase):
    setUp = fixtures.ExecutionTests.setUp

    def schedule(self, document, executor, task='fixture'):
        draft = self.store.save(self.workspace, None, None, document)
        definition = compile_definition(self.store, self.rt.provider, self.workspace, draft, task)
        self.assertEqual(definition['designer_runtime'], 1)
        self.assertEqual(definition['draft_document'], document)
        run = self.store.db.put('run', 'typed-run', dict(id='typed-run', state='QUEUED', created_at=time.time(),
                                graph=definition['graph'], task=task, designer_runtime=1))
        rt = SimpleNamespace(flow=self.store.db, check_operator=lambda: None)
        DesignerScheduler(rt, run['id'], executor=executor).run_graph()
        return self.store.db.get('run', run['id']), {row['node']: row for row in self.store.db.list('activation')}

    def test_typed_input_two_agents_summary_and_output_with_durable_identities(self):
        def agent(id):
            value = node(id, 'agent', provider='codex', model='fixture', effort='low', round_prompt='fixture')
            value.pop('kind')
            return value
        doc = graph([node('Start', 'start', initial_data='{"topic":"fixtures","secret":"not public"}'),
                     agent('First'), agent('Second'), node('Output', 'output', format='json')],
                    [edge('one', 'Start', 'First'), edge('two', 'First', 'Second', transfer={'mode':'summary','max_chars':40}),
                     edge('three', 'Second', 'Output')])
        calls = []
        def executor(node, key, inputs, prefix, iteration):
            calls.append((node['id'], key, inputs))
            if node['id'] == 'First':
                return dict(answer='ordinary answer', summary='bounded summary', result={'ok': True})
            return dict(answer='end answer', summary='end summary', result={'received': inputs[0]['content']})
        run, rows = self.schedule(doc, executor)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(run['output']['result'], {'received': 'bounded summary'})
        self.assertEqual([item[0] for item in calls], ['First', 'Second'])
        self.assertEqual(calls[0][2][0]['content'], {'answer':'fixture', 'result':{'topic':'fixtures'}})
        self.assertEqual(calls[1][2][0]['activation'], rows['First']['id'])
        self.assertEqual(calls[1][1], activation_key(run['id'], 'Second', []))
        self.assertTrue(all(row['state'] == 'COMPLETED' for row in rows.values()))
        events = self.store.db.events(run['id'])
        self.assertEqual(sum(event['kind'] == 'node-completed' for event in events), 4)
        self.assertEqual(len({event['handoff_id'] for event in events if event['kind'].startswith('handoff-')}), 3)

    def test_control_only_handoff_and_failure_do_not_invent_output(self):
        doc = graph([node('Start', 'start'), node('Output', 'output', format='json')],
                    [edge('order', 'Start', 'Output', transfer={'mode':'none','max_chars':1})])
        run, rows = self.schedule(doc, lambda *_: self.fail('deterministic workflow must not call a Provider'))
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertIsNone(rows['Output']['inputs'][0]['content'])
        self.assertEqual(run['output']['result'], {})

    def test_condition_and_ordered_router_skip_unselected_branches(self):
        score = dict(field='result.score', op='greater', value=5)
        second = dict(field='result.score', op='less', value=20)
        expression = {'all': [score, {'any': [second, {'not': {'field':'result.ready','op':'equals','value':True}}]}]}
        routes = [{'id':'first','label':'first rule','test':score}, {'id':'second','label':'second rule','test':second}]
        doc = graph([node('Start', 'start', initial_data='{"score":10,"ready":true}'),
                     node('Test', 'condition', expression=expression), node('Route', 'router', rules=routes),
                     node('Selected', 'output', format='json'), node('Other', 'output'), node('Else', 'output')],
                    [edge('input','Start','Test'), edge('testYes','Test','Route','true'),
                     edge('testNo','Test','Else','false'), edge('first','Route','Selected','first'),
                     edge('second','Route','Other','second')])
        run, rows = self.schedule(doc, lambda *_: self.fail('deterministic control must not call a Provider'))
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(run['output']['result'], {'score': 10, 'ready': True})
        self.assertEqual(rows['Test']['selected_port'], 'true')
        self.assertEqual(rows['Route']['decision']['label'], 'first')
        self.assertEqual(rows['Other']['state'], 'SKIPPED')
        self.assertEqual(rows['Else']['state'], 'SKIPPED')
        decisions = [event for event in self.store.db.events(run['id']) if event['kind'] in ('condition-decided', 'route-selected')]
        self.assertEqual([event['port'] for event in decisions], ['true', 'first'])

    def test_null_missing_and_type_mismatch_are_distinct(self):
        data = {'result': {'maybe': None, 'number': True}}
        present = condition(data, {'field':'result.maybe','op':'equals','value':None})
        missing = condition(data, {'field':'result.absent','op':'not_equals','value':None})
        self.assertEqual((present['matched'], present['tests'][0]['presence']), (True, 'null'))
        self.assertEqual((missing['matched'], missing['tests'][0]['presence']), (False, 'missing'))
        with self.assertRaisesRegex(Invalid, '大小比较'):
            condition(data, {'field':'result.number','op':'greater','value':0})

    def test_router_without_match_and_fallback_does_not_dispatch(self):
        match = {'field':'result.route','op':'equals','value':'planned'}
        route = node('Route', 'router', rules=[{'id':'planned','label':'planned route','test':match}])
        doc = graph([node('Start', 'start'), route, node('Output', 'output')],
                    [edge('into','Start','Route'), edge('out','Route','Output','planned')])
        run, rows = self.schedule(doc, lambda *_: self.fail('unselected downstream must not dispatch'))
        self.assertEqual(run['state'], 'FAILED')
        self.assertIn('没有匹配规则且未配置默认分支', rows['Route']['reason'])
        self.assertEqual(rows['Output']['state'], 'SKIPPED')
