"""Typed designer persistence and the existing runtime authorization boundary."""
import copy
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.server import Conflict
from v2.canvas_structure import TYPES
from v2.definitions import compile_definition
from v2.documents import check_chain, validate_draft
from v2.draft_transfer import parse_archive
from v2.runtime import V2Runtime

import test_v2_draft_transfer as transfer
import test_v2_execution as execution
import test_v2_execution_http as http


def node(id, kind, **config):
    value = dict(id=id, name=id, position={'x': 0, 'y': 0}, kind=kind)
    if kind == 'agent':
        return dict(value, **config)
    return dict(value, config_version=1, config=config)


def edge(id, source, target, port='out', **extra):
    return dict(id=id, source=source, target=target, source_port=port, target_port='in', **extra)


def graph(nodes, edges=()):
    return dict(version=1, name='Typed fixture', nodes=nodes, edges=list(edges), viewport={'x': 14, 'y': 0, 'zoom': .8})


class CanvasStructureTests(TestCase):
    setUp = execution.ExecutionTests.setUp
    start_server = http.ExecutionHTTPTests.start_server
    request = http.ExecutionHTTPTests.request

    def test_each_type_and_exact_port_config_survive_storage_and_archive(self):
        predicate = {'field': 'result.ready', 'op': 'equals', 'value': True}
        rows = [node('Start', 'start', initial_data='{"fixture":true}'),
                node('Agent', 'agent'), node('Condition', 'condition', expression=predicate),
                node('Router', 'router', rules=[{'id': 'yes', 'label': 'Before', 'test': predicate}]),
                node('Merge', 'merge', strategy='selected'), node('Loop', 'loop', max_iterations=4, exit_condition=predicate),
                node('Approval', 'approval', policy='single-reviewer', instructions='Confirm'),
                node('Tool', 'tool', tool='read-file', permission='workspace-read'),
                node('Validation', 'validation', rules='fixture checks'), node('Output', 'output', format='json')]
        doc = graph(rows, [edge('route', 'Router', 'Merge', 'yes'), edge('fallback', 'Router', 'Merge', 'fallback'),
                           edge('initial', 'Start', 'Agent', transfer={'mode': 'summary', 'max_chars': 70})])
        self.assertEqual({row['kind'] for row in rows}, set(TYPES))
        first = self.store.save(self.workspace, None, None, doc)
        self.assertEqual(self.store.draft(self.workspace, first['id'])['graph'], doc)
        self.assertEqual(parse_archive(transfer.archive(doc)), json.loads(transfer.archive(doc))['document_sha256'])
        updated = copy.deepcopy(doc)
        updated['nodes'][3]['config']['rules'][0]['label'] = 'Renamed'
        second = self.store.save(self.workspace, first['id'], first['revision'], updated)
        self.assertEqual(second['graph']['edges'], doc['edges'])
        self.assertNotEqual(second['revision'], first['revision'])
        with self.assertRaises(Conflict):
            self.store.save(self.workspace, first['id'], first['revision'], doc)
        issues = check_chain(doc)['issues']
        self.assertTrue(any(item.get('code') == 'RUNTIME_NODE_UNAVAILABLE' for item in issues))
        self.assertTrue(any(item.get('code') == 'RUNTIME_GRAPH_UNCONNECTED' for item in issues))

    def test_typed_invalid_versions_ports_graph_and_permissions_are_rejected(self):
        rows = [node('Start', 'start'), node('A', 'agent'), node('B', 'agent'), node('C', 'condition'),
                node('End', 'output')]
        baseline = graph(rows, [edge('start', 'Start', 'A'), edge('condition', 'A', 'C'), edge('true', 'C', 'B', 'true')])
        changes = [
            (lambda d: d['nodes'][0].update(config_version=True), 'version 1'),
            (lambda d: d['nodes'][0]['config'].update(credentials='private'), '字段'),
            (lambda d: d['nodes'][1].update(provider='codex'), '字段'),
            (lambda d: d['nodes'][3]['config'].update(expression={'field':'x','op':'exec','value':1}), '运算符'),
            (lambda d: d['edges'].append(edge('back', 'B', 'Start')), '没有所选输入端口'),
            (lambda d: d['edges'].append(edge('back', 'End', 'A')), '没有所选输出端口'),
            (lambda d: d['edges'].append(edge('wrong', 'C', 'End', 'not-a-port')), '没有所选输出端口'),
            (lambda d: d['edges'].append(edge('same', 'C', 'B', 'true')), '已有连线'),
            (lambda d: d['edges'].append(edge('over', 'C', 'End', 'true')), '只允许一条'),
            (lambda d: d['edges'].append(edge('over', 'Start', 'B')), '只接收一条'),
            (lambda d: d['edges'][0].update(transfer={'mode':'answer','max_chars':True}), '长度上限'),
        ]
        self.assertEqual(validate_draft(baseline), baseline)
        for mutate, reason in changes:
            candidate = copy.deepcopy(baseline)
            mutate(candidate)
            with self.subTest(reason=reason), self.assertRaisesRegex(Invalid, reason):
                validate_draft(candidate)
        cycle = graph([node('A', 'agent'), node('B', 'agent')], [edge('e1', 'A', 'B'), edge('e2', 'B', 'A')])
        with self.assertRaisesRegex(Invalid, '循环'):
            validate_draft(cycle)

    def test_default_explicit_agent_ports_compile_into_existing_answer_chain(self):
        ref = dict(agent_id='1'*32, agent_revision='2'*64)
        doc = graph([node('A', 'agent', **ref), node('B', 'agent', **ref)], [edge('e1', 'A', 'B',
                    transfer={'mode':'answer','max_chars':16000})])
        row = self.store.save(self.workspace, None, None, doc)
        record = dict(id=ref['agent_id'], revision=ref['agent_revision'], config=dict(round_prompt='synthetic'))
        frozen = compile_definition(self.store, self.rt.provider, self.workspace, row,
                                    agent_binding=lambda node: (record, {'provider':'codex'}))
        self.assertEqual(frozen['draft_document'], doc)
        handoff = next(edge for edge in frozen['graph']['edges'] if edge['id'] == 'e1')
        self.assertEqual(handoff, dict(id='e1',source='A',target='B',transfer='answer',max_chars=16000))
        self.assertEqual(self.store.db.list('run'), [])

    def test_http_refuses_unsupported_or_unbound_without_provider_and_reports_unresolved_reference(self):
        with patch('v2.runtime.CodexProvider', side_effect=Blocked('未找到 Codex CLI')):
            runtime = V2Runtime(self.store)
        self.start_server(runtime)
        ref = dict(id='Agent', name='Unresolved fixture', position=dict(x=0, y=0), agent_id='1'*32, agent_revision='2'*64)
        values = [
            graph([node('Tool', 'tool'), ref]),
            graph([node('A', 'agent'), node('B', 'agent')], [edge('e1', 'A', 'B', transfer={'mode':'result','max_chars':16000})]),
        ]
        with patch.object(runtime, 'spawn', Mock(side_effect=AssertionError('never spawn'))) as spawn:
            for index, doc in enumerate(values):
                code, row = self.request('drafts/save', dict(workspace=self.workspace, id=None, expected=None, document=doc))
                self.assertEqual(code, 200)
                code, check = self.request('check', dict(workspace=self.workspace, document=doc))
                self.assertEqual(code, 200)
                self.assertFalse(check['execution_available'])
                if not index:
                    self.assertIn('AGENT_REFERENCE_UNRESOLVED', [issue.get('code') for issue in check['issues']])
                code, result = self.request('launch', dict(workspace=self.workspace, draft_id=row['id'], revision=row['revision'],
                    intent_key=f'phase4-refuse-intent-{index}', task=''))
                self.assertEqual(code, 400)
                self.assertIn('已保存配置版本' if index else '执行尚未接入', result['error'])
            spawn.assert_not_called()
        for kind in ('run', 'activation', 'definition', 'native-step'):
            self.assertEqual(self.store.db.list(kind), [])
        with self.store.db.tx(read=True) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM launch_intents').fetchone()[0], 0)
