"""Task selection and data contracts through V2 authorization, scheduling and recovery."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from orchestration.common import digest
from studio.graph import Invalid
from studio.server import Conflict
from v2.data_handoff import field_contract, structured_output, verify_artifacts
from v2.designer_graph import handoff
from v2.designer_scheduler import DesignerScheduler
from v2.recovery import project, validate_data_completion

from test_v2_canvas_structure import edge, graph, node
import test_v2_execution as execution
import test_v2_execution_http as http
import test_v2_designer_runtime as designer


class DataHandoffTests(unittest.TestCase):
    setUp = execution.ExecutionTests.setUp
    start_server = http.ExecutionHTTPTests.start_server
    request = http.ExecutionHTTPTests.request
    payload = http.ExecutionHTTPTests.payload
    schedule = designer.DesignerRuntimeTests.schedule

    def test_task_file_preview_freeze_no_reread_on_replay_and_no_raw_secrets(self):
        self.start_server()
        root = self.root/'spaces'/'A'
        source = root/'notes.txt'
        marker = 'SYNTHETIC_RELAY_HEADER_VALUE'
        (root/'other.txt').write_text('unselected marker')
        source.write_text('已选项目资料\n'+marker)
        (root/'link.txt').symlink_to(source)
        os.link(source,root/'hard.txt')
        (root/'large.txt').write_text('x'*16001)

        def preview(path):
            return self.request('task-file?'+urlencode(dict(workspace=self.workspace,path=path)))

        for path in ('notes.txt','hard.txt','link.txt','../A/notes.txt','/etc/passwd','large.txt'):
            self.assertIn(preview(path)[0],(400,409),path)
        (root/'hard.txt').unlink()
        with patch.dict(os.environ,{'ANTHROPIC_CUSTOM_HEADERS':'X-Demo: '+marker}):
            code, view = preview('notes.txt')
            self.assertEqual(code,200,view)
            self.assertEqual(view['text'],'已选项目资料\n[REDACTED]')
            self.assertEqual(view['sha256'],digest(view['text'].encode()))
            selected = dict(path=view['path'],sha256=view['sha256'])
            payload = dict(self.payload(),task_files=[selected])
            with patch.object(self.rt,'spawn') as spawn:
                self.assertEqual(self.request('launch',dict(payload,task_files=None))[0],400)
                self.assertEqual(self.request('launch',dict(payload,task_files=[selected,selected]))[0],400)
                source.write_text('changed before launch')
                self.assertEqual(self.request('launch',payload)[0],409)
                spawn.assert_not_called()
                self.assertEqual(self.store.db.list('run'),[])
                source.write_text('已选项目资料\n'+marker)
                code, value = self.request('launch',payload)
                self.assertEqual(code,200,value)
                ident = value['run']['id']
                definition = self.store.db.get('definition',value['run']['definition_id'])
                self.assertEqual(definition['task_files'],[dict(selected,bytes=view['bytes'],text=view['text'])])
                self.assertEqual(value['definition']['task_files'],[dict(selected,bytes=view['bytes'])])
                from v2.worker_facts import _original_authority
                saved = self.store.db.get('run',ident)
                with self.store.db.tx(read=True) as tx:
                    self.assertEqual(_original_authority(tx,saved)['run'],ident)
                self.assertNotIn(marker,json.dumps(value))
                source.unlink()
                self.assertEqual(self.request('launch',payload)[1]['run']['id'],ident)
                self.assertEqual(self.request('launch',dict(payload,task_files=[]))[0],409)
                spawn.assert_called_once_with(ident)

    def test_required_nested_field_handoff_preserves_exact_selection(self):
        def agent(ident):
            item = node(ident,'agent',provider='codex',model='fixture',effort='low',round_prompt='fixture')
            item.pop('kind')
            return item
        fields = [
            dict(field='result.metrics.score',type='number',required=True),
            dict(field='result.active',type='boolean',required=True),
            dict(field='result.nullable',type='null',required=False),
            dict(field='result.absent',type='string',required=False),
        ]
        document = graph([agent('First'),agent('Next')],[edge('selected','First','Next',
                         transfer=dict(mode='fields',fields=fields,max_chars=2000))])
        expected = {'result':{'metrics':{'score':8},'active':False,'nullable':None}}
        calls = []
        def turn(node,key,inputs,*_):
            calls.append(node['id'])
            if node['id']=='First':
                return dict(answer='not selected',result={'metrics':{'score':8,'extra':9},
                            'active':False,'nullable':None,'unknown':'do not include'})
            self.assertEqual(inputs[0]['content'],expected)
            return dict(answer='ok',result=expected['result'])
        run, rows = self.schedule(document,turn)
        self.assertEqual(run['state'],'COMPLETED')
        self.assertEqual(calls,['First','Next'])
        self.assertEqual(rows['Next']['inputs'][0]['content'],expected)
        self.assertEqual(rows['Next']['inputs'][0]['content_sha256'],digest(
            json.dumps(expected,sort_keys=True,separators=(',',':')).encode()))
        with self.assertRaisesRegex(Invalid,'上限'):
            handoff(expected,dict(transfer='fields',fields=fields,max_chars=2),control=False)
        with self.assertRaisesRegex(Invalid,'类型'):
            handoff({'result':{'metrics':{'score':True}}},dict(transfer='fields',fields=fields,max_chars=2000),control=False)
        self.start_server()
        for field,type_name in (([],'any'),('result.safe',{}),('result.auth_token','string'),('artifacts','array')):
            invalid = deepcopy(document)
            invalid['edges'][0]['transfer']['fields'] = [dict(field=field,type=type_name,required=True)]
            self.assertEqual(self.request('check',dict(workspace=self.workspace,document=invalid))[0],400)

    def test_real_artifact_fingerprint_scopes_provider_activation_and_live_file(self):
        root = self.root/'spaces'/'A'
        item = root/'artifact.bin'
        item.write_bytes(b'artifact\x00bytes')
        run = dict(id='1'*32,workspace=self.workspace)
        row = dict(id='2'*64,run=run['id'],node='Writer',state='COMPLETED',provider='codex',
                   receipt_verified=True,receipt={'session_id':'fixed-session'})
        spec = dict(output_contract=field_contract([dict(field='result.summary',type='string',required=True)]),
                    artifact_paths=['artifact.bin'])
        receipt = dict(report=dict(result_json='{"summary":"complete","env":"private field"}',
                                   artifacts=['artifact.bin']),session_id='fixed-session')
        output = dict(answer='native reply',result={},artifacts=[])
        with patch('v2.data_handoff.task_literals',return_value=set()):
            contract = structured_output(self.store,run,row,spec,{'provider':'codex'},str(root),receipt,output)
        self.assertEqual(contract,{'state':'PASSED'})
        self.assertEqual(output['result'],{'summary':'complete'})
        proof, = output['artifacts']
        self.assertEqual(proof['sha256'],digest(b'artifact\x00bytes'))
        self.assertEqual(proof['activation'],row['id'])
        validate_data_completion(run,row,{'provider':'codex'},spec,
                                 dict(output=output,receipt=receipt,data_contract=contract))
        verify_artifacts(self.store,self.workspace,run['id'],row,[proof],str(root))
        with self.assertRaises(Invalid):
            verify_artifacts(self.store,self.workspace,run['id'],dict(row,provider='claude'),[proof],str(root))
        with self.assertRaises(Invalid):
            verify_artifacts(self.store,self.workspace,run['id'],row,[proof],str(root/'nested'))
        item.write_bytes(b'changed')
        with self.assertRaises(Conflict):
            verify_artifacts(self.store,self.workspace,run['id'],row,[proof],str(root))
        for path in ('/etc/passwd','unapproved.txt'):
            received = dict(receipt,report=dict(receipt['report'],artifacts=[path]))
            with patch('v2.data_handoff.read_project') as read:
                bad = structured_output(self.store,run,row,spec,{'provider':'codex'},str(root),received,
                                        dict(answer='native reply',result={},artifacts=[]))
                self.assertEqual(bad['state'],'FAILED')
                read.assert_not_called()

    def test_claude_json_projection_and_semantic_failure_leave_native_completion(self):
        from studio.workbench_data import public_data
        root = self.root/'spaces'/'A'
        run = dict(id='1'*32,workspace=self.workspace,state='FAILED',created_at=0)
        row = dict(id='2'*64,run=run['id'],node='Claude',kind='agent',state='RUNNING')
        spec = dict(output_contract=field_contract([dict(field='result.ok',type='boolean',required=True)]))
        answer = '```json\n{"result":{"token":"redact generic key","ok":false},"artifacts":[]}\n```'
        receipt = dict(report={'reply':answer},session_id='fixed-session')
        output = public_data(dict(answer=answer,result={},artifacts=[]))
        original = output['answer']
        contract = structured_output(self.store,run,row,spec,{'provider':'claude'},str(root),receipt,output)
        self.assertEqual(contract,{'state':'PASSED'})
        self.assertEqual(output['result'],{'ok':False})
        self.assertEqual(output['answer'],original)
        output = dict(answer='native text with no required result',result={},artifacts=[])
        receipt['report']['reply'] = output['answer']
        failed = structured_output(self.store,run,row,spec,{'provider':'claude'},str(root),receipt,output)
        self.assertEqual(failed['state'],'FAILED')
        completion = dict(receipt=receipt,output=output,data_contract=failed)
        from orchestration.common import canonical
        definition = dict(graph={'nodes':[dict(id='Claude',kind='agent')]})
        definition['id'] = run['definition_id'] = digest(canonical(definition))
        with patch('v2.recovery.read_completion',return_value=completion):
            value = project(self.rt,run,definition,[row],[])
        recovered, = value['nodes']
        self.assertEqual((recovered['state'],recovered['provider_outcome'],recovered['receipt_verified']),
                         ('FAILED','completed',True))
        self.assertEqual(recovered['reason'],failed['reason'])

    def test_invalid_handoff_stops_before_downstream_provider(self):
        def agent(ident):
            item = node(ident,'agent',provider='codex',model='fixture',effort='low',round_prompt='fixture')
            item.pop('kind')
            return item

        contract = [dict(field='result.score',type='number',required=True)]
        document = graph([agent('First'),agent('Next')],[edge('checked','First','Next',
                         transfer=dict(mode='fields',fields=contract,max_chars=1000))])
        calls = []
        def turn(spec,*_):
            calls.append(spec['id'])
            return {'answer':'verified transport','result':{'score':False}}
        run, rows = self.schedule(document,turn)
        self.assertEqual(run['state'],'FAILED')
        self.assertEqual(calls,['First'])
        self.assertEqual(rows['First']['state'],'COMPLETED')
        self.assertEqual(rows['Next']['state'],'FAILED')
        self.assertIn('连线 checked',rows['Next']['reason'])
        self.assertTrue(any(event['kind']=='handoff-contract-failed' and event['activation']==rows['Next']['id']
                            for event in self.store.db.events(run['id'])))
