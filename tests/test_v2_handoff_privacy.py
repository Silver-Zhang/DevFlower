"""Known-format synthetic secrets on the actual V2 output/transfer/request path.

This is bounded filtering proof, not universal secret recognition or real CLI.
"""
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked,digest
from orchestration.store import process_identity
from studio.workbench_scheduler import Scheduler
import test_v2_execution as fixtures


class HandoffPrivacyTests(unittest.TestCase):
    setUp=fixtures.ExecutionTests.setUp

    def test_public_completion_handoff_downstream_request_filter_with_marker_control(self):
        raw_values=['SYNTHETIC_FIELD_PRIVATE_123','SYNTHETIC_NESTED_PRIVATE_456','SYNTHETIC_BEARER_789012','sk-synthetic_not_a_real_key_abcdef','SYNTHETIC_TOKEN_2345']
        marker='Q09_NONSECRET_MARKER'
        doc=json.loads(json.dumps(self.doc));b=dict(doc['nodes'][0],id='AgentB',name='B',round_prompt='Use actual upstream public input')
        doc['nodes'].append(b);doc['edges']=[dict(id='A_to_B',source='AgentA',target='AgentB')]
        draft=self.store.save(self.workspace,None,None,doc)
        with patch.object(self.rt,'spawn'):run=self.rt.launch(self.workspace,draft['id'],draft['revision'],'synthetic-privacy-'+uuid.uuid4().hex)['run']
        self.rt.worker_run=run['id'];self.rt.provider.require_local=lambda binding:None;self.rt.provider.require_account=lambda binding:None
        seen=[];store=self.store
        class SyntheticCLI:
            def __init__(self,*a):pass
            def start(self,prompt,cwd,artifacts,schema,journal,run_id):
                seen.append(prompt);journal.visible_request(prompt);key=journal.key;op=uuid.uuid4().hex;sid=str(uuid.uuid4())
                process=process_identity(__import__('os').getpid());proc=dict(process=process,operation_id=op,exit=0,timeout=False,cancelled=False,overflow=False)
                store.db.put('native-step',op,dict(id=op,run=run_id,activation=key,kind='PROCESS',status='EXITED',data=proc))
                store.db.change('activation',key,dict(process=process,process_result=proc,observed_session=sid,executed_image_sha256='a'*64))
                journal.input_sent(op,len(prompt.encode()),digest(prompt.encode()));journal.native_terminal_event(dict(type='turn.completed',session_id=sid,native_turn_id=None))
                report=dict(reply=marker+' Bearer '+raw_values[2]+' '+raw_values[3]+' token='+raw_values[4],summary='',accepted=True,
                    result_json=json.dumps(dict(api_key=raw_values[0],nested=dict(credentials=raw_values[1],useful=marker))),artifacts=[],evidence=[],issues=[],next='')
                return dict(session_id=sid,native_turn_id=None,requested_model='fixture',requested_effort='low',resolved_model='unknown',resolved_effort='unknown',process=proc,report=report)
        with patch('v2.runtime.LocalCodex',SyntheticCLI):Scheduler(self.rt,run['id'],executor=self.rt.native_turn).run_graph()
        self.assertEqual(self.store.db.get('run',run['id'])['state'],'COMPLETED');self.assertEqual(len(seen),2)
        a,b=sorted([n for n in self.store.db.list('activation') if n['run']==run['id'] and n['kind']=='agent'],key=lambda n:n['node'])
        self.assertIn(marker,a['output']['answer']);self.assertIn(marker,b['actual_request']);self.assertIn('[REDACTED]',b['actual_request'])
        self.assertEqual(b['inputs'][0]['content'],a['output']['answer']);self.assertNotIn('api_key',a['output']['result']);self.assertEqual(a['output']['result']['nested']['useful'],marker)
        public=json.dumps(self.rt.snapshot(self.workspace,run['id']))+json.dumps(self.store.db.events(run['id']))
        for p in (self.store.state/'flow-artifacts'/run['id']).rglob('completion.json'):public+=p.read_text()
        for value in raw_values:self.assertNotIn(value,public);self.assertNotIn(value,seen[1])
        self.assertIn(marker,public)

    def test_untrusted_error_payload_is_not_in_public_run_or_safe_error_record(self):
        with patch.object(self.rt,'spawn'):run=self.rt.launch(self.workspace,self.draft['id'],self.draft['revision'],'synthetic-error-'+uuid.uuid4().hex)['run']
        self.rt.worker_run=run['id'];self.rt.provider.require_local=lambda binding:None;self.rt.provider.require_account=lambda binding:None
        raw='Bearer SYNTHETIC_ERROR_PRIVATE_12345 https://private.invalid/secret'
        with patch('v2.runtime.LocalCodex') as cli:
            cli.return_value.start.side_effect=Blocked(raw)
            Scheduler(self.rt,run['id'],executor=self.rt.native_turn).run_graph()
        public=json.dumps(self.rt.snapshot(self.workspace,run['id']))+json.dumps(self.store.db.events(run['id']))
        self.assertNotIn('SYNTHETIC_ERROR_PRIVATE_12345',public);self.assertNotIn('private.invalid',public)
        a=next(n for n in self.store.db.list('activation') if n['run']==run['id'] and n['kind']=='agent')
        self.assertEqual(a['error_message_sha256'],digest(raw.encode()))

if __name__=='__main__':unittest.main()
