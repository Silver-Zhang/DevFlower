"""Scoped read-only run evidence, independent from provider execution."""
import hashlib
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import test_v2_execution as execution
import test_v2_execution_http as http
from v2.run_inspector import EVENT_PAGE, EXPORT_BYTES, PAYLOAD_PAGE


class RunInspectorTests(unittest.TestCase):
    setUp = execution.ExecutionTests.setUp
    start_server = http.ExecutionHTTPTests.start_server
    request = http.ExecutionHTTPTests.request

    def setUpRun(self):
        with patch.object(self.rt, 'spawn'):
            first = self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'run-inspector-first')
            other = self.rt.launch(self.workspace, self.draft['id'], self.draft['revision'], 'run-inspector-other')
        self.run = first['run']['id']
        self.foreign_run = other['run']['id']
        self.activation = '1' * 64
        self.second = '2' * 64
        self.foreign_activation = '3' * 64
        self.marker = 'WORKSPACE_OUTPUT_🟢'
        self.secret = 'PRIVATE_PLAINTEXT_OPERATOR_KEY'
        self.private = str(self.store.state / 'flow-artifacts' / 'native-file')
        rows = [dict(id=self.activation, run=self.run, node='Loop/Agent', iteration_path=[2,1], kind='agent'),
                dict(id=self.second, run=self.run, node='Other', iteration_path=[], kind='result'),
                dict(id=self.foreign_activation, run=self.foreign_run, node='Other', iteration_path=[], kind='result')]
        for row in rows:
            row.update(state='FAILED', started_at=time.time(), reason='approval denied', name=row['node'])
            if row['id'] == self.activation:
                row.update(output={'answer': self.marker * 6000, 'env': self.secret},
                           actual_request='authorized text ' + self.secret, actual_request_sha256='5'*64,
                           input_sent_sha256='5'*64, input_sent_bytes=12,
                           native_tool_activity=[{'command': 'cat ' + self.private, 'output': 'test stdout',
                                                  'env': self.secret, 'raw_stderr': self.secret}],
                           private_state=self.private, executable='/usr/bin/sensitive-binary',
                           receipt={'process': {'argv': [self.secret], 'exit': 2}, 'native_history': self.private})
            self.store.db.put('activation', row['id'], row)
        # Raw native-step is never projected, even if it belongs to the exact activation.
        self.store.db.put('native-step', 'raw', dict(id='raw', run=self.run, activation=self.activation,
                         kind='AGENT_REQUEST', status='RESERVED', data={'argv': [self.secret], 'cwd': self.private}))
        for index in range(EVENT_PAGE + 2):
            self.store.db.event(self.run, 'node-running', node='Loop/Agent', activation=self.activation,
                                **({'iteration_path': [2,1]} if index else {}), state='RUNNING',
                                hidden={'argv': [self.secret]})
        self.store.db.event(self.run, 'handoff-sent', node='Other', source='Loop/Agent',
                            activation=self.activation, target_activation=self.second, source_round=[2,1],
                            target_round=[], content={'answer':'handed over', 'api_key':self.secret})
        self.store.db.event(self.run, 'run-finished', state='FAILED')
        with patch.dict('os.environ', {'ANTHROPIC_AUTH_TOKEN': self.secret}):
            self.start_server()

    def inspector(self, route, **query):
        return self.request('run-inspector/' + route + '?' + urlencode(dict(workspace=self.workspace, run=self.run)|query))

    def test_event_pages_exact_scope_and_historical_unassociated(self):
        self.setUpRun()
        code, first = self.inspector('events')
        self.assertEqual(code, 200)
        self.assertTrue(first['more'])
        self.assertEqual(len(first['events']), EVENT_PAGE)
        legacy = next(e for e in first['events'] if e['kind']=='node-running')
        self.assertEqual((legacy['scope'], legacy['activation']), ('unassociated', None))
        self.assertEqual(first['events'][-1]['iteration_path'], [2,1])
        _, second = self.inspector('events', after=first['after'])
        self.assertTrue(all(e['seq'] > first['after'] for e in second['events']))
        handoff = next(e for e in second['events'] if e['kind']=='handoff-sent')
        self.assertEqual((handoff['activation'], handoff['source_activation']), (self.second, self.activation))
        self.assertEqual(handoff['content'], {'answer':'handed over'})
        _, scoped = self.inspector('events', activation=self.activation, after=first['after'])
        self.assertTrue(all(e['scope']=='activation' for e in scoped['events']))
        self.assertIn(handoff, scoped['events'])
        self.assertEqual(self.inspector('events', activation=self.foreign_activation)[0], 409)
        self.assertEqual(self.inspector('events', workspace='f'*64)[0], 400)
        self.assertEqual(self.inspector('events', after=-1)[0], 400)
        self.assertEqual(self.request('run-inspector/events?workspace='+self.workspace+'&run='+self.run, {})[0], 405)

    def test_unicode_payload_pages_and_stale_revision(self):
        self.setUpRun()
        query = dict(activation=self.activation, field='output')
        code, first = self.inspector('payload', **query)
        self.assertEqual(code, 200)
        self.assertEqual(first['evidence'], 'persisted_public_record')
        self.assertEqual((len(first['text']), first['next_offset']), (PAYLOAD_PAGE, PAYLOAD_PAGE))
        parts = [first['text']]
        page = first
        while page['next_offset'] is not None:
            code, page = self.inspector('payload', **query, offset=page['next_offset'], sha256=first['sha256'])
            self.assertEqual(code, 200)
            parts.append(page['text'])
        text = ''.join(parts)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), first['sha256'])
        self.assertEqual(json.loads(text), {'answer': self.marker * 6000})
        self.assertEqual(self.inspector('payload', **query, offset=1)[0], 400)
        self.assertEqual(self.inspector('payload', activation=self.foreign_activation, field='output')[0], 409)
        self.store.db.change('activation', self.activation, {'output': {'answer':'new revision'}})
        self.assertEqual(self.inspector('payload', **query, offset=first['next_offset'], sha256=first['sha256'])[0], 409)

    def test_export_and_legacy_snapshot_never_return_private_fields(self):
        self.setUpRun()
        before = len(self.store.db.events(self.run))
        for path in ('run-inspector/export?workspace='+self.workspace+'&run='+self.run,
                     'run?workspace='+self.workspace+'&id='+self.run):
            code, data = self.request(path)
            self.assertEqual(code, 200)
            encoded = json.dumps(data, ensure_ascii=False)
            self.assertNotIn(self.secret, encoded)
            self.assertNotIn(self.private, encoded)
            self.assertNotIn('raw_stderr', encoded)
            self.assertNotIn('sensitive-binary', encoded)
        _, value = self.inspector('export')
        self.assertEqual(value['definition']['id'], value['run']['definition_id'])
        self.assertEqual(value['format'], 'devflower.v2.run-detail')
        self.assertLess(len(json.dumps(value).encode()), EXPORT_BYTES)
        self.assertFalse(value['coverage']['complete_public_snapshot'])
        self.assertFalse(value['coverage']['causal_associations_complete'])
        self.assertEqual(value['coverage']['omitted_activation_payloads'], 1)
        self.assertEqual(len(self.store.db.events(self.run)), before)
