"""Workspace and intent checks around the real V2 HTTP assistant API."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Lock, Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
import uuid

import test_v2_execution as fixtures
import test_v2_execution_http as http
from v2.assistant_proposals import PROPOSAL_INSTRUCTIONS
from v2.server import create_server


class FixtureOperations:
    """Synthetic transport, with the same public result and readback contract."""
    codex_terminal = False

    def __init__(self):
        self.calls = []
        self.replies = {}
        self.state = 'COMPLETED'
        self.lock = Lock()

    def status(self):
        return ['claude', 'codex']

    def operation(self, workspace, intent_key):
        with self.lock:
            return self.replies.get((workspace,intent_key), {'state':'UNREGISTERED'})

    def send(self, data):
        assert data['handle'] is None and data['revision'] is None
        with self.lock:
            key = data['workspace'],data['intent_key']
            assert key not in self.replies
            self.calls.append(data)
            reply = ('{"summary":"Rename approved draft label","operations":'
                     '[{"op":"update-node","id":"AgentA","name":"Reviewed Agent"}]}'
                     if PROPOSAL_INSTRUCTIONS in data['prompt'] else 'Verified fixture chat response')
            self.replies[key] = dict(state=self.state, reply=reply, reason=None,
                                     receipt=dict(provider=data['provider'],session_id=str(uuid.uuid4())))
            return self.replies[key]


class AssistantTests(unittest.TestCase):
    setUp = fixtures.ExecutionTests.setUp
    request = http.ExecutionHTTPTests.request

    def initialize(self):
        self.operations = FixtureOperations()
        server, self.token = create_server(self.store, runtime=self.rt, session_operations=self.operations)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}/api/v2/'
        self.chat = '7'*32
        self.payload = dict(workspace=self.workspace,conversation=self.chat,provider='claude',model=None,effort=None,
                            mode='chat',text='fixture question',draft=None,files=[],run=None)
        self.work = self.root / 'spaces' / 'A'

    def post(self, route, payload):
        return self.request('assistant/'+route, payload)

    def details(self, **extra):
        return self.request('assistant/conversation?'+urlencode(
            dict(workspace=self.workspace,conversation=self.chat)|extra))

    def confirm(self, payload=None, intent='1'*32):
        value = payload or self.payload
        code, preview = self.post('preview',value)
        self.assertEqual(code,200,preview)
        return dict(value,intent=intent,preview_sha256=preview['sha256']),preview

    def test_context_preview_is_read_only_and_exact_and_duplicate_send_is_one(self):
        self.initialize()
        (self.work/'public.txt').write_text('PUBLIC_ASSISTANT_SCOPE')
        (self.work/'other.txt').write_text('DO_NOT_ATTACH_UNSELECTED')
        self.payload['files']=['public.txt']
        submitted, preview = self.confirm()
        self.assertIn('PUBLIC_ASSISTANT_SCOPE',preview['prompt'])
        self.assertNotIn('DO_NOT_ATTACH_UNSELECTED',preview['prompt'])
        self.assertEqual(preview['context']['workspace']['name'],'A')
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(self.store.db.list('assistant-turn'),[])
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _: self.post('send',submitted), range(2)))
        self.assertEqual([code for code,_ in replies],[200,200],replies)
        self.assertEqual(len(self.operations.calls),1)
        self.assertEqual(self.operations.calls[0]['prompt'],preview['prompt'])
        _, history = self.details()
        self.assertEqual(len(history['turns']),1)
        self.assertEqual(history['turns'][0]['receipt']['provider'],'claude')
        self.assertEqual(self.post('send',dict(submitted,text='changed exact nonce'))[0],409)
        other = self.root/'spaces'/'B'
        other.mkdir()
        foreign = self.store.open_workspace(str(other))['id']
        self.assertEqual(self.details(workspace=foreign)[0],409)
        _, list_b = self.request('assistant/history?'+urlencode({'workspace':foreign}))
        self.assertEqual(list_b['conversations'],[])
        second, next_preview = self.confirm(dict(self.payload,text='follow up'),intent='2'*32)
        self.assertEqual(next_preview['context']['previous_turns'],1)
        self.assertIn('Verified fixture chat response',next_preview['prompt'])
        self.assertEqual(self.post('send',second)[0],200)
        self.assertTrue(all(row['handle'] is None for row in self.operations.calls))

    def test_changed_file_and_unauthorized_paths_refuse_before_native_operation(self):
        self.initialize()
        (self.work/'public.txt').write_text('first')
        outside = self.root/'outside.txt'
        outside.write_text('outside')
        (self.work/'link.txt').symlink_to(outside)
        data = dict(self.payload,files=['public.txt'])
        submitted,_ = self.confirm(data)
        (self.work/'public.txt').write_text('new')
        self.assertEqual(self.post('send',submitted)[0],409)
        for path in ('link.txt','../outside.txt',str(outside)):
            self.assertEqual(self.post('preview',dict(data,files=[path]))[0],400)
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(self.store.db.list('assistant-turn'),[])

    def test_exact_unsaved_base_apply_is_validated_without_saving_or_running(self):
        self.initialize()
        selection = dict(id=self.draft['id'],revision=self.draft['revision'],document=self.doc)
        data = dict(self.payload,mode='proposal',text='Rename the Agent on the canvas.',draft=selection)
        submitted,_ = self.confirm(data)
        code, result = self.post('send',submitted)
        self.assertEqual(code,200,result)
        self.assertIn('proposal',result,result)
        self.assertEqual(result['proposal']['after']['nodes'][0]['name'],'Reviewed Agent')
        apply = dict(workspace=self.workspace,conversation=self.chat,turn=result['id'],draft=selection)
        changed = dict(self.doc,name='unsaved local change')
        self.assertEqual(self.post('apply',dict(apply,draft=dict(selection,document=changed)))[0],409)
        code, answer = self.post('apply',apply)
        self.assertEqual(code,200,answer)
        self.assertEqual(answer['document']['nodes'][0]['name'],'Reviewed Agent')
        self.assertEqual(self.store.draft(self.workspace,self.draft['id'])['graph'],self.doc)
        self.assertEqual(self.store.db.list('run'),[])
        self.store.save(self.workspace,self.draft['id'],self.draft['revision'],changed)
        self.assertEqual(self.post('apply',apply)[0],409)

    def test_unknown_readback_never_replays_or_accepts_next_turn(self):
        self.initialize()
        self.operations.state='RECOVERY_REQUIRED'
        submitted,_ = self.confirm()
        self.assertEqual(self.post('send',submitted)[1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.details()[1]['turns'][-1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.post('send',submitted)[1]['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.post('preview',dict(self.payload,text='second'))[0],409)
        self.assertEqual(len(self.operations.calls),1)

    def test_diagnosis_uses_existing_failed_run_and_events_without_sending(self):
        self.initialize()
        with patch.object(self.rt,'spawn'):
            result = self.rt.launch(self.workspace,self.draft['id'],self.draft['revision'],'assistant-run-fixture')
        run = result['run']['id']
        activation = '6'*64
        self.store.db.put('activation',activation,dict(id=activation,run=run,kind='agent',node='AgentA',
                          name='Fixture',state='STARTING',iteration_path=[],inputs=[dict(edge='first',
                          source='upstream',activation='4'*64,content_sha256='3'*64)]))
        self.store.db.change('activation',activation,{'state':'FAILED','reason':'fixture upstream missing'},
                             event={'kind':'node-failed'})
        self.store.db.change('run',run,{'state':'FAILED'})
        query = urlencode({'workspace':self.workspace,'run':run})
        code, diagnosis = self.request('assistant/diagnosis?'+query)
        self.assertEqual(code,200,diagnosis)
        self.assertEqual(diagnosis['nodes'][0]['reason'],'fixture upstream missing')
        self.assertEqual(diagnosis['nodes'][0]['upstream'][0]['source'],'upstream')
        self.assertEqual(diagnosis['recent_error_events'][-1]['activation'],activation)
        _, preview = self.confirm(dict(self.payload,run=run))
        self.assertIn('fixture upstream missing',preview['prompt'])
        self.assertEqual(self.operations.calls,[])
        self.assertEqual(len(self.store.db.list('run')),1)
