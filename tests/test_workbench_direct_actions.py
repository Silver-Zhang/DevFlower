"""Direct actions use synthetic CLI boundaries; they do not prove provider access."""
import unittest
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio.workbench_examples import examples

class DirectActions(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self)
        self.workspace=self.base.project['id']

    def test_click_prepares_and_launches_without_certificate_or_probe(self):
        graph=examples(self.rt.operator['profiles'])[0]
        request=dict(workspace=self.workspace, graph=graph, task='synthetic direct request', interaction='click')
        with patch('studio.model_admission.ModelAdmission.require', side_effect=AssertionError('certificate requested')), \
             patch('studio.model_admission.ModelAdmission._verify', side_effect=AssertionError('probe called')), \
             patch.object(self.rt,'spawn') as spawn:
            permit=self.rt.prepare_flow(request)
            result=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
            again=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
        self.assertEqual(result['run']['id'],again['run']['id'])
        self.assertEqual(spawn.call_count,1)
        auth=self.rt.flow.get('run',result['run']['id'])['authorization']
        self.assertEqual(auth['action'],'start')
        self.assertNotIn('answers',auth)

    def test_discovered_terra_selection_and_effort_start_without_nonce(self):
        from studio.model_catalog import ModelCatalog
        from studio.model_registration import ModelRegistration
        from studio.workbench_sessions import WorkbenchSessions
        name='default-codex'; profile=self.rt.operator['profiles'][name]
        manager=WorkbenchSessions(self.rt)
        with patch.object(manager,'rpc',side_effect=[{'data':[{'model':'gpt-6.1-terra','supportedReasoningEfforts':[{'reasoningEffort':'high'},{'reasoningEffort':'medium'}]}]}, {'account':{'type':'synthetic'}}]):
            catalog=ModelCatalog(manager).get(name,profile,True,workspace=self.workspace)
        model=next(m for m in catalog['models'] if m['id']=='gpt-6.1-terra')
        self.assertFalse(model['execution_allowed'])
        registry=ModelRegistration(self.rt)
        registry.select(dict(workspace=self.workspace,profile=name,model='gpt-6.1-terra',effort='medium'))
        registry.select(dict(workspace=self.workspace,profile=name,model='gpt-6.1-terra',effort='high'))
        alternate=self.rt.node_binding(self.workspace,dict(profile=name,model='gpt-6.1-terra',effort='medium',mode='Ask'),admission=False,direct=True)
        self.assertEqual(alternate['binding']['effort'],'medium')
        graph=examples(self.rt.operator['profiles'])[0]
        for node in graph['nodes']:
            if node['kind']=='agent':node.update(profile=name,provider='codex',model='gpt-6.1-terra',effort='high')
        with patch('studio.model_admission.ModelAdmission.require',side_effect=AssertionError('unexpected certificate')),patch.object(self.rt,'spawn'):
            permit=self.rt.prepare_flow(dict(workspace=self.workspace,graph=graph,task='test',interaction='click'))
            result=self.rt.launch_flow(permit['id'],permit['sha256'],{'action':'start'})
        self.assertTrue(any(b['binding']['model']=='gpt-6.1-terra' for b in result['run']['bindings'].values()))

    def test_click_chat_provider_failure_is_real_failure_without_retry(self):
        from orchestration.common import Blocked
        p=self.rt.operator['profiles']['default-codex']
        with patch('studio.model_admission.ModelAdmission.require',side_effect=AssertionError('certificate requested')),patch.object(self.rt.session_center,'_spawn',side_effect=Blocked('synthetic spawn stopped')):
            permit=self.rt.session_center.prepare(dict(workspace=self.workspace,handle='new',profile='default-codex',model=p['model'],effort=p['effort'],mode='Ask',permission='read',prompt='synthetic task',interaction='click'))
            op=self.rt.session_center.launch(permit['id'],permit['sha256'],{'action':'send'})
            again=self.rt.session_center.launch(permit['id'],permit['sha256'],{'action':'send'})
        self.assertEqual(op['id'],again['id'])
        self.rt.db.update('session-operation',op['id'],dict(state='QUEUED'))
        with patch('studio.session_center.CLI.start',side_effect=Blocked('synthetic provider rejected')) as call:
            self.rt.session_center.execute(op['id'])
        result=self.rt.session_center.operation(op['id'],self.workspace)
        self.assertEqual(result['state'],'FAILED');self.assertIn('synthetic provider rejected',result['reason']);self.assertEqual(call.call_count,1)

    def test_changed_source_and_wrong_action_reject(self):
        from studio.server import Conflict
        from studio.graph import Invalid
        graph=examples(self.rt.operator['profiles'])[0]
        p=self.rt.prepare_flow(dict(workspace=self.workspace,graph=graph,task='test',interaction='click'))
        with self.assertRaises(Invalid):self.rt.db.consume_click(p['id'],p['sha256'],'send')
        (self.base.source/'changed.txt').write_text('changed')
        with self.assertRaises(Conflict):self.rt.launch_flow(p['id'],p['sha256'],{'action':'start'})
        self.assertEqual(self.rt.flow.list('run'),[])

    def test_http_click_path_and_repeat(self):
        import json,threading
        from urllib.request import Request,urlopen
        from studio.workbench_server import create_server
        server,token=create_server(self.rt)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        def post(path,data):
            request=Request(base+path,data=json.dumps(data).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urlopen(request) as response:return json.load(response)
        graph=examples(self.rt.operator['profiles'])[0]
        with patch.object(self.rt,'spawn') as spawn,patch('studio.model_admission.ModelAdmission.require',side_effect=AssertionError('certificate requested')):
            permit=post('/api/prepare',dict(workspace=self.workspace,graph=graph,task='http synthetic',interaction='click'))
            data=dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'start'})
            first=post('/api/start',data);second=post('/api/start',data)
        self.assertEqual(first['run']['id'],second['run']['id']);self.assertEqual(spawn.call_count,1)

    def test_direct_terminal_opens_once_without_certificate(self):
        import subprocess,sys,time
        from test_workbench_runtime import structured_session
        from studio.native_terminals import NativeTerminals
        from studio.workbench_sessions import WorkbenchSessions
        _,session=structured_session(self.base,self.rt,'direct-terminal','codex')
        manager=NativeTerminals(self.rt,WorkbenchSessions(self.rt));self.addCleanup(manager.shutdown)
        request=dict(workspace=self.workspace,handle=session['handle'],interaction='click')
        with patch('studio.model_admission.ModelAdmission.require',side_effect=AssertionError('certificate requested')):
            permit=manager.prepare(request)
            real=subprocess.Popen
            def spawn(argv, **kwargs):
                self.assertIn('--terminal-id',argv)
                return real([sys.executable,'-B','-c','import time;time.sleep(2)'],**kwargs)
            with patch('studio.native_terminals.subprocess.Popen',side_effect=spawn) as call:
                data=dict(id=permit['id'],sha256=permit['sha256'],answer={'action':'open-terminal'},cols=80,rows=24)
                first=manager.start(data);second=manager.start(data)
            self.assertEqual(first['id'],second['id']);self.assertEqual(call.call_count,1)
            manager.close(self.workspace,first['id'])

    def test_configuration_assistant_click_has_no_nonce_and_is_idempotent(self):
        from studio.configuration_assistant import ConfigurationAssistant
        from orchestration.common import Blocked
        p=self.rt.operator['profiles']['default-codex'];assistant=ConfigurationAssistant(self.rt)
        choices={k:dict(provider=v['provider'],models=[dict(id=v['model'],efforts=[v['effort']])]) for k,v in self.rt.operator['profiles'].items()}
        graph=examples(self.rt.operator['profiles'])[0]
        import subprocess
        real=subprocess.Popen;spawns=[]
        def spawn(argv,**kw):
            if any(str(item).endswith('configuration_worker.py') for item in argv):
                spawns.append(argv);raise Blocked('synthetic spawn stopped')
            return real(argv,**kw)
        with patch.object(ConfigurationAssistant,'choices',return_value=choices),patch('studio.model_admission.ModelAdmission.require',side_effect=AssertionError('certificate requested')),patch('studio.configuration_assistant.subprocess.Popen',side_effect=spawn):
            permit=assistant.prepare(dict(workspace=self.workspace,graph=graph,goal='test',profile='default-codex',model=p['model'],effort=p['effort'],interaction='click'))
            a=assistant.start(permit['id'],permit['sha256'],{'action':'generate'});b=assistant.start(permit['id'],permit['sha256'],{'action':'generate'})
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(spawns),1);self.assertEqual(a['state'],'FAILED')

    def test_rotated_codex_auth_keeps_status_readable_but_not_executable_drift(self):
        import json,threading
        from pathlib import Path
        from urllib.request import Request,urlopen
        from studio.workbench_server import create_server
        from orchestration.common import Blocked
        profile=self.rt.operator['profiles']['default-codex']
        auth=next(Path(src) for src,dest in profile['readonly_files'] if dest=='.codex/auth.json')
        auth.write_text('{"OPENAI_API_KEY":"synthetic-rotated-key"}')
        server,token=create_server(self.rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/status',headers={'Authorization':'Bearer '+token})
        with urlopen(req) as response:self.assertEqual(json.load(response)['version'],3)
        executable=profile['executable'];old=self.rt.operator['dependencies'][executable]
        self.rt.operator['dependencies'][executable]='0'*64
        try:
            with self.assertRaises(Blocked):self.rt.check_operator()
        finally:self.rt.operator['dependencies'][executable]=old

    def test_auth_rotation_requires_new_click_snapshot(self):
        from pathlib import Path
        from studio.server import Conflict
        graph=examples(self.rt.operator['profiles'])[0]
        for n in graph['nodes']:
            if n.get('kind')=='agent':
                p=self.rt.operator['profiles']['default-codex'];n.update(profile='default-codex',provider='codex',model=p['model'],effort=p['effort'])
        request=dict(workspace=self.workspace,graph=graph,task='test',interaction='click')
        first=self.rt.prepare_flow(request)
        auth=next(Path(src) for src,dest in self.rt.operator['profiles']['default-codex']['readonly_files'] if dest=='.codex/auth.json')
        auth.write_text('{"OPENAI_API_KEY":"synthetic-new-key"}')
        with self.assertRaises(Conflict):self.rt.launch_flow(first['id'],first['sha256'],{'action':'start'})
        second=self.rt.prepare_flow(request)
        with patch.object(self.rt,'spawn') as spawn:self.rt.launch_flow(second['id'],second['sha256'],{'action':'start'})
        self.assertEqual(spawn.call_count,1)

    def test_chat_uses_configured_timeout_without_certificate(self):
        profile=self.rt.operator['profiles']['default-codex'];profile['timeout_seconds']=2400
        # Keep the operator identity valid for this controlled fixture.
        from orchestration.common import save,digest
        save(self.rt.operator_file,self.rt.operator);self.rt.operator_sha256=digest(self.rt.operator_file.read_bytes())
        permit=self.rt.session_center.prepare(dict(workspace=self.workspace,handle='new',profile='default-codex',model=profile['model'],effort=profile['effort'],mode='Ask',permission='read',prompt='synthetic',interaction='click'))
        self.assertEqual(permit['data']['binding']['timeout_seconds'],2400)
