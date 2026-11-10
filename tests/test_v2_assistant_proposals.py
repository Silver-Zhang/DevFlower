"""The native reply is untrusted input. Only closed V2 draft operations are projected."""
import copy
import json
from unittest import TestCase

from studio.graph import Invalid
from v2.assistant_proposals import proposal


def node(ident, kind, x=0, config=None):
    value = dict(id=ident, name=ident, kind=kind, position=dict(x=x, y=0))
    if kind != 'agent':
        value.update(config_version=1, config=config or {})
    return value


def edge(ident, source, target, source_port='out'):
    return dict(id=ident, source=source, target=target, source_port=source_port, target_port='in')


def graph(*nodes, edges=()):
    return dict(version=1, name='Proposal fixture', nodes=list(nodes), edges=list(edges),
                viewport=dict(x=0, y=0, zoom=1))


def plan(*operations):
    return json.dumps(dict(summary='Verified proposal preview', operations=operations))


class ProposalTests(TestCase):
    def test_preview_adds_connects_removes_and_updates_without_editing_base(self):
        base = graph(node('Start', 'start'), node('Old', 'agent', 300), node('End', 'output', 600),
                     edges=[edge('old-in', 'Start', 'Old'), edge('old-out', 'Old', 'End')])
        original = copy.deepcopy(base)
        reply = plan(dict(op='remove-node', id='Old'),
                     dict(op='add-node', id='New', kind='agent', name='New Agent'),
                     dict(op='connect', **edge('new-in', 'Start', 'New')),
                     dict(op='connect', **edge('new-out', 'New', 'End')),
                     dict(op='update-node', id='End', config={'format':'json'}))
        result = proposal(base, '```json\n' + reply + '\n```')
        self.assertEqual(base, original)
        self.assertEqual(result['document']['viewport'], base['viewport'])
        self.assertEqual([edge['id'] for edge in result['document']['edges']], ['new-in', 'new-out'])
        self.assertEqual(result['document']['nodes'][1]['config'], {'format':'json'})
        self.assertEqual(result['document']['nodes'][2]['kind'], 'agent')
        self.assertNotIn('agent_id', result['document']['nodes'][2])

    def test_existing_agent_references_cannot_be_rebound_or_recycled(self):
        agent = dict(node('Agent', 'agent'), agent_id='a'*32, agent_revision='b'*64)
        base = graph(agent)
        renamed = proposal(base, plan(dict(op='update-node', id='Agent', name='Renamed')))
        self.assertEqual(renamed['document']['nodes'][0], dict(agent, name='Renamed'))
        for reply in (plan(dict(op='update-node', id='Agent', agent_id='c'*32)),
                      plan(dict(op='update-node', id='Agent', config={'permission':'workspace-write'})),
                      plan(dict(op='remove-node', id='Agent'),
                           dict(op='add-node', id='Agent', name='Reused', kind='agent'))):
            with self.subTest(reply=reply), self.assertRaises(Invalid):
                proposal(base, reply)

    def test_approval_inside_loop_guards_topology_and_branch_configuration(self):
        body = graph(node('Review', 'approval', config={'instructions':'review'}))
        base = graph(node('Loop', 'loop', config={'max_iterations':2, 'body':body}),
                     node('Switch', 'condition', config={'expression':{'field':'result.x','op':'exists','value':True}}))
        for operation in (dict(op='remove-node', id='Loop'), dict(op='update-node', id='Loop', config={'max_iterations':1}),
                          dict(op='update-node', id='Switch', config={'expression':{'field':'result.y','op':'exists','value':True}})):
            with self.subTest(operation=operation), self.assertRaisesRegex(Invalid, '人工审批'):
                proposal(base, plan(operation))
        result = proposal(base, plan(dict(op='update-node', id='Loop', name='Review loop')))
        self.assertEqual(result['document']['nodes'][0]['config']['body'], body)

    def test_tool_authority_and_model_owned_loop_body_are_refused(self):
        base = graph(node('Tool','tool',config={'tool':'read-file','permission':'workspace-read','path':'input.txt'}))
        for operation in (dict(op='add-node', id='Shell', kind='tool', name='Tool', config={'tool':'command'}),
                          dict(op='update-node', id='Tool', config={'path':'private.txt'}),
                          dict(op='remove-node', id='Tool'),
                          dict(op='add-node', id='Loop', kind='loop', name='Loop', config={'body':graph()})):
            with self.subTest(operation=operation), self.assertRaises(Invalid):
                proposal(base, plan(operation))

    def test_invalid_graph_unknown_shape_and_oversized_model_reply_are_refused(self):
        base = graph(node('First','agent'), node('Second','agent'), edges=[edge('first','First','Second')])
        replies = [plan(dict(op='connect', **edge('back','Second','First'))),
                   plan(dict(op='connect', **edge('wrong','Second','First','unknown-port'))),
                   plan(dict(op='update-node', id='First', name='First')),
                   '{"summary":"x","summary":"y","operations":[]}', 'x'*60_001,
                   plan(*[dict(op='update-node', id='First', name=str(i)) for i in range(21)])]
        for reply in replies:
            with self.subTest(reply=reply[:100]), self.assertRaises(Invalid):
                proposal(base, reply)

    def test_connect_uses_port_validation_in_legacy_agent_draft(self):
        first, second = (node(ident, 'agent') for ident in ('First', 'Second'))
        first.pop('kind')
        second.pop('kind')
        base = graph(first, second, edges=[dict(id='old', source='First', target='Second')])
        with self.assertRaisesRegex(Invalid, '循环'):
            proposal(base, plan(dict(op='connect', id='back', source='Second', target='First')))
