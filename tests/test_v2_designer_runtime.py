"""Typed scheduling fixtures over the production V2 SQLite/event store."""
import time
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from types import SimpleNamespace
from unittest import TestCase

from orchestration.store import process_identity
from studio.graph import Invalid
from studio.server import Conflict
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
                                graph=definition['graph'], task=task, workspace=self.workspace, designer_runtime=1))
        rt = SimpleNamespace(flow=self.store.db, store=self.store, check_operator=lambda: None)
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

    def parallel_doc(self, strategy):
        def agent(id):
            item = node(id, 'agent', provider='codex', model='fixture', effort='low', round_prompt='fixture')
            item.pop('kind')
            return item
        return graph([node('Start','start'), agent('First'), agent('Second'),
                      node('Join','merge',strategy=strategy), node('Output','output')],
                     [edge('into1','Start','First'), edge('into2','Start','Second'),
                      edge('merge1','First','Join'), edge('merge2','Second','Join'), edge('out','Join','Output')])

    def test_parallel_all_keeps_edge_order_and_runs_both_activations_once(self):
        first, second = Event(), Event()
        calls = []
        def executor(node, key, *_):
            calls.append(key)
            here, peer = (first, second) if node['id']=='First' else (second, first)
            here.set()
            if not peer.wait(3):
                raise Invalid('peer did not run in parallel')
            if node['id']=='First':
                time.sleep(.15)
            return {'answer':node['id'], 'result':{}}
        run, rows = self.schedule(self.parallel_doc('all'), executor)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(rows['Join']['output']['answer'], 'First\n\nSecond')
        self.assertEqual(rows['Join']['merge_activations'], [rows['First']['id'], rows['Second']['id']])
        self.assertEqual((len(calls), len(set(calls))), (2, 2))
        events = self.store.db.events(run['id'])
        self.assertEqual(sum(event['kind']=='merge-completed' for event in events), 1)

    def test_any_releases_downstream_then_retains_lower_priority_failure(self):
        started = Event()
        def executor(node, key, *_):
            if node['id']=='First':
                if not started.wait(3):
                    raise Invalid('second branch did not start')
                return {'answer':'first wins', 'result':{}}
            started.set()
            for _ in range(100):
                out = [a for a in self.store.db.list('activation') if a['node']=='Output']
                if out and out[0]['state']=='COMPLETED':
                    raise Invalid('known independent failure')
                time.sleep(.025)
            raise Invalid('ANY did not let downstream proceed')
        run, rows = self.schedule(self.parallel_doc('any'), executor)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(run['output']['answer'], 'first wins')
        self.assertEqual(rows['Second']['reason'], 'known independent failure')
        self.assertEqual(run['partial_failures'], [rows['Second']['id']])
        self.assertEqual(rows['Join']['merge_activations'], [rows['First']['id']])

    def test_any_ignores_faster_lower_priority_result_until_first_fails(self):
        lower = Event()
        def executor(node, key, *_):
            if node['id']=='Second':
                lower.set()
                return {'answer':'second available', 'result':{}}
            if not lower.wait(3):
                raise Invalid('parallel branch missing')
            time.sleep(.1)
            status = [a for a in self.store.db.list('activation') if a['node']=='Join']
            if status[0]['state']=='COMPLETED':
                raise Invalid('merge incorrectly chose fast result')
            raise Invalid('first failed')
        run, rows = self.schedule(self.parallel_doc('any'), executor)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(rows['First']['reason'], 'first failed')
        self.assertEqual(rows['Join']['merge_activations'], [rows['Second']['id']])


    def test_nested_loop_preserves_iteration_identity_and_exact_exit(self):
        agent = node('Increment', 'agent', provider='codex', model='fixture', effort='low', round_prompt='increment')
        agent.pop('kind')
        increment = graph([node('InnerStart','start'), agent, node('InnerEnd','output')],
                          [edge('in','InnerStart','Increment'), edge('out','Increment','InnerEnd')])
        missing = {'field': 'result.stop', 'op': 'exists', 'value': True}
        inner = node('Inner', 'loop', max_iterations=2, exit_condition=missing, body=increment)
        outer_body = graph([node('RoundStart','start'), inner, node('RoundEnd','output')],
                           [edge('in','RoundStart','Inner'), edge('out','Inner','RoundEnd')])
        exit_at_four = {'field':'result.count','op':'equals','value':4}
        outer = node('Outer', 'loop', max_iterations=3, exit_condition=exit_at_four, body=outer_body)
        doc = graph([node('Start','start',initial_data='{"count":0}'), outer, node('End','output',format='json')],
                    [edge('in','Start','Outer'), edge('out','Outer','End')])
        calls = []
        def executor(node, key, inputs, prefix, iteration):
            self.assertEqual(prefix, 'Outer/Inner/')
            count = inputs[0]['content']['result']['count']
            calls.append((key, count, iteration))
            return {'answer': str(count + 1), 'result': {'count': count + 1}}
        run, rows = self.schedule(doc, executor)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(run['output']['result'], {'count': 4})
        self.assertEqual(rows['Outer']['loop_exit'], 'condition')
        self.assertEqual([item['iteration_path'] for item in rows['Outer']['loop_results']], [[1], [2]])
        paths = [[1,1], [1,2], [2,1], [2,2]]
        self.assertEqual(calls, [(activation_key(run['id'], 'Outer/Inner/Increment', path), count, path[-1])
                                 for count, path in enumerate(paths)])
        inner_rows = [row for row in self.store.db.list('activation') if row['node']=='Outer/Inner']
        self.assertEqual({(row['loop_round'],row['loop_exit']) for row in inner_rows}, {(2,'limit')})
        events = [e for e in self.store.db.events(run['id']) if e['kind']=='loop-round-completed']
        self.assertEqual(len(events), 6)
        self.assertEqual({e['loop_round'] for e in events}, {1,2})

    def test_fixed_read_tool_and_validation_do_not_follow_untrusted_path(self):
        root = self.root / 'spaces' / 'A'
        (root / 'approved.txt').write_text('approved input')
        (self.root / 'outside.txt').write_text('outside sentinel')
        (root / 'link.txt').symlink_to(self.root / 'outside.txt')
        doc = graph([node('Start','start',initial_data='{"path":"link.txt"}'),
                     node('Read','tool',tool='read-file',permission='workspace-read',path='approved.txt'),
                     node('Validate','validation',rules='{"field":"result.text","op":"equals","value":"approved input"}'),
                     node('OK','output'),node('Bad','output')],
                    [edge('in','Start','Read'),edge('read','Read','Validate'),
                     edge('pass','Validate','OK','pass'),edge('fail','Validate','Bad','fail')])
        run, rows = self.schedule(doc, lambda *_: self.fail('Tool and Validation are local, deterministic nodes'))
        self.assertEqual(run['output']['result'], {'text':'approved input','path':'approved.txt','readonly':True})
        self.assertEqual(rows['Bad']['state'], 'SKIPPED')
        self.assertNotIn('outside sentinel', str(run))
        from v2.project_files import read_project
        with self.assertRaises(Invalid):
            read_project(self.store, self.workspace, 'link.txt', preview=True)

    def approval(self, decision=None, pause=False):
        doc = graph([node('Start','start',initial_data='{"approved":true}'),
                     node('Human','approval',instructions='Review exact data',timeout=4 if decision else 1),
                     node('End','output',format='json'), node('Reject','output')],
                    [edge('in','Start','Human'), edge('approved','Human','End','approved'),
                     edge('rejected','Human','Reject','rejected')])
        draft = self.store.save(self.workspace, None, None, doc)
        definition = compile_definition(self.store, self.rt.provider, self.workspace, draft)
        run_id = '1'*32
        self.store.db.put('definition', definition['id'], definition)
        self.store.db.put('run', run_id, dict(id=run_id, state='QUEUED', created_at=time.time(),
                graph=definition['graph'], definition_id=definition['id'], task='fixture', workspace=self.workspace,
                designer_runtime=1, worker=process_identity(os.getpid()), paused=False))
        key = activation_key(run_id, 'Human', [])
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(DesignerScheduler(self.rt, run_id, executor=lambda *_: self.fail('never calls model')).run_graph)
            try:
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    waiting = self.store.db.get('run',run_id)
                    if waiting.get('waiting_approval') == key:
                        break
                    time.sleep(.02)
                self.assertEqual(waiting['state'], 'WAITING_CONFIRMATION')
                self.assertEqual(self.store.db.get('activation',key)['state'], 'WAITING_CONFIRMATION')
                self.assertEqual(self.store.db.get('activation',activation_key(run_id,'End',[]))['state'], 'WAITING')
                if pause:
                    self.rt.control_designer(self.workspace,run_id,paused=True)
                if decision:
                    self.rt.control_designer(self.workspace,run_id,activation=key,decision=decision)
                    self.rt.control_designer(self.workspace,run_id,activation=key,decision=decision)
                    with self.assertRaises(Conflict):
                        self.rt.control_designer(self.workspace,run_id,activation=key,
                                                 decision='reject' if decision=='approve' else 'approve')
                if pause:
                    self.assertEqual(self.store.db.get('run',run_id)['state'],'PAUSED')
                    self.assertEqual(self.store.db.get('activation',activation_key(run_id,'End',[]))['state'],'WAITING')
                    self.rt.control_designer(self.workspace,run_id,paused=False)
                work.result(timeout=6)
            finally:
                if not work.done():
                    self.store.db.change('run', run_id, {'cancel_requested': True})
        events = self.store.db.events(run_id)
        user_events = [e for e in events if e['kind']=='human-decision']
        if decision:
            self.assertEqual(len(user_events), 1)
            self.assertEqual((user_events[0]['actor'],user_events[0]['activation'],user_events[0]['decision']),
                             (f'local-user:{os.geteuid()}',key,decision))
            self.assertEqual((user_events[0]['workspace'],user_events[0]['definition_id']),
                             (self.workspace, definition['id']))
        else:
            self.assertEqual(user_events, [])
        return self.store.db.get('run', run_id), events

    def test_approval_requires_user_and_respects_durable_pause(self):
        run, events = self.approval('approve', pause=True)
        self.assertEqual(run['state'], 'COMPLETED')
        self.assertEqual(run['output']['result'], {'approved':True})
        self.assertIn('human-approved', [e['kind'] for e in events])

    def test_rejection_stops_followup_including_rejected_port(self):
        run, events = self.approval('reject')
        self.assertEqual(run['state'], 'FAILED')
        self.assertIn('拒绝', run['stop_reason'])
        self.assertNotIn('output', run)
        self.assertEqual(self.store.db.get('activation',activation_key(run['id'],'Reject',[]))['state'],'SKIPPED')
        self.assertIn('human-rejected', [e['kind'] for e in events])

    def test_approval_times_out_without_model_or_synthetic_decision(self):
        run, events = self.approval()
        self.assertEqual(run['state'], 'FAILED')
        self.assertIn('超时', run['stop_reason'])
        self.assertIn('human-timeout', [e['kind'] for e in events])
