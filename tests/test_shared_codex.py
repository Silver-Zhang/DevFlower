"""Canonical app-server integration with a real CLI and synthetic local model."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.native_codex_rpc import metadata
from studio.shared_codex_config import shared_conversation_config
from studio.graph import Invalid

EXE=os.environ.get('DEVFLOWER_TEST_CODEX_SUPPORTED','/usr/lib/chatgpt/resources/codex')


class SharedCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.home=root/'home';self.home.mkdir()
        self.config=self.home/'.codex';self.config.mkdir();self.cwd=root/'workspace';self.cwd.mkdir()
        self.artifacts=root/'artifacts';self.artifacts.mkdir();self.calls=[]
        self.hold=threading.Event();self.hold.set();self.arrived=threading.Event()
        calls=self.calls
        hold,arrived=self.hold,self.arrived
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                arrived.set();hold.wait(10)
                msg={'id':'msg-'+str(len(calls)),'type':'message','status':'completed','role':'assistant',
                     'content':[{'type':'output_text','text':'SYNTHETIC_SHARED_'+str(len(calls)),'annotations':[]}]}
                response={'id':'resp-'+str(len(calls)),'object':'response','created_at':0,'status':'completed','model':'fixture',
                          'output':[msg],'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for event in ({'type':'response.created','response':dict(response,status='in_progress',output=[])},
                              {'type':'response.output_item.added','output_index':0,'item':dict(msg,status='in_progress',content=[])},
                              {'type':'response.output_item.done','output_index':0,'item':msg},
                              {'type':'response.completed','response':response}):
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.marker=root/'UNSELECTED_MCP_STARTED'
        helper=root/'mcp.py';helper.write_text('from pathlib import Path\nPath('+repr(str(self.marker))+').write_text("unexpected")\n')
        self.config_file=self.config/'config.toml'
        self.config_file.write_text('model_provider="fixture"\n[model_providers.fixture]\nname="fixture"\nbase_url="http://127.0.0.1:'+str(self.server.server_port)+'/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n[mcp_servers.unselected]\ncommand="/usr/bin/python3"\nargs=['+json.dumps(str(helper))+']\n')
        (self.cwd/'.codex').mkdir();(self.cwd/'.codex/config.toml').write_text('[mcp_servers.project_unselected]\ncommand="/usr/bin/python3"\nargs=['+json.dumps(str(helper))+']\n')
        self.binding=dict(executable=EXE,home=str(self.home),config_root=str(self.config),provider='codex',
            execution_backend='native-local',shared_conversation=True,no_tools=True,execution_mode='Ask',
            role='reviewer',model='fixture',effort='low',project='synthetic',readonly_files=[],
            conversation_format='text',subagents='disabled')
        self.limits=dict(step_timeout_seconds=25,max_log_bytes=2_000_000)

    def test_same_native_thread_is_listed_and_resumed_without_unselected_mcp(self):
        before=self.config_file.read_bytes()
        first=LocalCodex(self.binding,self.limits).start('FIRST_SYNTHETIC',self.cwd,self.artifacts)
        self.assertEqual(first['report']['reply'],'SYNTHETIC_SHARED_1')
        listing=metadata(self.binding,str(self.cwd),'thread/list',dict(cwd=str(self.cwd),limit=100))
        self.assertIn(first['session_id'],[x['id'] for x in listing['data']])
        self.assertEqual(len(listing['data']),1)
        second=LocalCodex(dict(self.binding,session_id=first['session_id'],expected_native_turn_id=first['native_turn_id']),self.limits).start('SECOND_SYNTHETIC',self.cwd,self.artifacts)
        self.assertEqual(first['session_id'],second['session_id']);self.assertNotEqual(first['native_turn_id'],second['native_turn_id'])
        self.assertEqual(second['report']['reply'],'SYNTHETIC_SHARED_2')
        self.assertEqual(len(self.calls),2);self.assertFalse(self.marker.exists())
        # Continue that exact thread through the actual CLI, then return to the
        # adapter. This is a native resume, never a copied-context conversation.
        args=[EXE,'exec','resume','--ignore-user-config','--ignore-rules','--strict-config','--skip-git-repo-check','--json',
              '-c','model_provider="fixture"','-c','model_providers.fixture.name="fixture"',
              '-c','model_providers.fixture.base_url="http://127.0.0.1:'+str(self.server.server_port)+'/v1"',
              '-c','model_providers.fixture.wire_api="responses"','-c','model_providers.fixture.requires_openai_auth=false',
              '-c','model_providers.fixture.supports_websockets=false','-c','features.shell_tool=false',
              '-c','features.code_mode_host=false','-c','features.unified_exec=false','-c','features.hooks=false',
              '-c','features.plugins=false','-c','features.apps=false','--model','fixture',first['session_id'],'THIRD_SYNTHETIC']
        process=subprocess.run(args,cwd=self.cwd,env={'HOME':str(self.home),'CODEX_HOME':str(self.config),
            'PATH':'/usr/bin:/bin','NO_PROXY':'*'},capture_output=True,timeout=15)
        self.assertEqual(process.returncode,0,process.stderr.decode(errors='replace')[-1200:])
        started=[json.loads(x) for x in process.stdout.splitlines() if json.loads(x).get('type')=='thread.started']
        self.assertEqual([x['thread_id'] for x in started],[first['session_id']])
        history=metadata(self.binding,str(self.cwd),'thread/read',dict(threadId=first['session_id'],includeTurns=True))['thread']['turns']
        fourth=LocalCodex(dict(self.binding,session_id=first['session_id'],expected_native_turn_id=history[-1]['id']),self.limits).start('FOURTH_SYNTHETIC',self.cwd,self.artifacts)
        self.assertEqual(fourth['session_id'],first['session_id']);self.assertEqual(fourth['report']['reply'],'SYNTHETIC_SHARED_4')
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.config_file.read_bytes(),before)
        files=list((self.config/'sessions').glob('*/*/*/*.jsonl'));self.assertEqual(len(files),1)
        rows=[json.loads(x) for x in files[0].read_text().splitlines()]
        self.assertEqual(rows[0]['payload']['source'],'vscode')
        native=metadata(self.binding,str(self.cwd),'thread/read',dict(threadId=first['session_id'],includeTurns=True))['thread']
        self.assertTrue(Path(native['path']).is_relative_to(self.config/'sessions'),native['path'])
        self.assertEqual(len(list(self.config.glob('state_*.sqlite'))),1)
        self.assertNotIn('FIRST_SYNTHETIC',json.dumps(self.calls[0].get('tools',[])))
        for index in (0,1,3):
            request=self.calls[index]
            names=[x.get('name','') for x in request.get('tools',[])]
            self.assertEqual(names,['request_user_input'])

    def test_shared_config_rejects_expanded_tools_and_write_modes(self):
        for changes in (dict(role='implementer'),dict(no_tools=False),dict(skills=[{}]),dict(subagents='readonly')):
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                with shared_conversation_config(dict(self.binding,**changes),self.cwd,self.artifacts):pass
        self.assertEqual(self.calls,[])

    def test_stale_preview_is_rejected_without_a_second_model_call(self):
        first=LocalCodex(self.binding,self.limits).start('FIRST',self.cwd,self.artifacts)
        with self.assertRaisesRegex(Blocked,'另一端继续'):
            LocalCodex(dict(self.binding,session_id=first['session_id'],expected_native_turn_id='stale'),self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
        self.assertEqual(len(self.calls),1);self.assertFalse(self.marker.exists())

    def test_preview_of_an_empty_thread_rejects_a_new_external_turn(self):
        first=LocalCodex(self.binding,self.limits).start('EXTERNAL_FIRST',self.cwd,self.artifacts)
        with self.assertRaisesRegex(Blocked,'另一端继续'):
            LocalCodex(dict(self.binding,session_id=first['session_id'],expected_native_turn_id=None),self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
        self.assertEqual(len(self.calls),1)

    def test_native_writer_lock_rejects_a_competing_client(self):
        first=LocalCodex(self.binding,self.limits).start('FIRST',self.cwd,self.artifacts)
        resumed=dict(self.binding,session_id=first['session_id'])
        self.arrived.clear();self.hold.clear();result=[]
        def run():
            try:result.append(LocalCodex(resumed,self.limits).start('HELD',self.cwd,self.artifacts))
            except BaseException as exc:result.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(self.arrived.wait(8),'first writer did not reach synthetic model')
            with self.assertRaisesRegex(Blocked,'另一端持有'):
                LocalCodex(resumed,self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
            # The unmodified native CLI must contend on the same underlying
            # writer lock, outside this app-server mount namespace.
            import tomllib
            from studio.native_codex_exec import provider_overrides
            cli=subprocess.Popen([EXE,'exec','resume','--ignore-user-config','--ignore-rules',
                '--skip-git-repo-check','--json',*provider_overrides(tomllib.loads(self.config_file.read_text())),
                '--model','fixture',first['session_id'],'MUST_NOT_SEND'],
                cwd=self.cwd,env={'HOME':str(self.home),'CODEX_HOME':str(self.config),'PATH':'/usr/bin:/bin','NO_PROXY':'*'},
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            try:
                stdout,stderr=cli.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                # Some native versions log the rejected writer but stay alive.
                # This test proves exclusion, not their process-exit behavior.
                import signal
                os.killpg(cli.pid,signal.SIGKILL);stdout,stderr=cli.communicate(timeout=3)
            self.assertNotEqual(cli.returncode,0,stdout.decode(errors='replace'))
            self.assertIn('active writer',stderr.decode(errors='replace').lower())
            self.assertEqual(len(self.calls),2)
        finally:self.hold.set();thread.join(15)
        self.assertFalse(thread.is_alive());self.assertIsInstance(result[0],dict)

    def test_owned_exec_contender_stops_on_real_native_writer_conflict(self):
        import time,tomllib
        from orchestration.process import execute
        from studio.local_codex_adapter import writer_conflict_observer
        from studio.native_codex_exec import provider_overrides
        first=LocalCodex(self.binding,self.limits).start('FIRST_SYNTHETIC',self.cwd,self.artifacts)
        resumed=dict(self.binding,session_id=first['session_id']);self.arrived.clear();self.hold.clear();result=[]
        def run():
            try:result.append(LocalCodex(resumed,self.limits).start('HELD_SYNTHETIC',self.cwd,self.artifacts))
            except BaseException as exc:result.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(self.arrived.wait(8))
            args=[EXE,'exec','resume','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json',*provider_overrides(tomllib.loads(self.config_file.read_text())),'--model','fixture',first['session_id'],'MUST_NOT_SEND']
            started=time.monotonic()
            with self.assertRaisesRegex(Blocked,'另一写入者'):
                execute(args,self.cwd,self.home,timeout=10,isolate=False,env_extra={'CODEX_HOME':str(self.config),'NO_PROXY':'*'},on_stderr=writer_conflict_observer())
            self.assertLess(time.monotonic()-started,8);self.assertEqual(len(self.calls),2);self.assertTrue(thread.is_alive())
        finally:self.hold.set();thread.join(15)
        self.assertFalse(thread.is_alive());self.assertIsInstance(result[0],dict)

    def test_configuration_change_during_startup_never_sends_the_prompt(self):
        from studio.shared_codex_config import SharedConversationConfig
        wrap=SharedConversationConfig.wrap
        def changed(value,argv):
            result=wrap(value,argv)
            self.config_file.write_text(self.config_file.read_text()+'\n[mcp_servers.late]\ncommand="/bin/false"\n')
            return result
        with patch.object(SharedConversationConfig,'wrap',changed),self.assertRaisesRegex(Blocked,'配置在启动期间变化'):
            LocalCodex(self.binding,self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
        self.assertEqual(self.calls,[]);self.assertFalse(self.marker.exists())

    def test_config_unlinked_after_wrap_is_never_recreated_in_native_store(self):
        from studio.shared_codex_config import SharedConversationConfig
        wrap=SharedConversationConfig.wrap
        def changed(value,argv):
            result=wrap(value,argv);self.config_file.unlink();return result
        with patch.object(SharedConversationConfig,'wrap',changed),self.assertRaisesRegex(Blocked,'配置在启动期间变化'):
            LocalCodex(self.binding,self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
        self.assertFalse(self.config_file.exists());self.assertEqual(self.calls,[])

    def test_external_interactive_thread_is_adopted_without_a_replacement_uuid(self):
        from studio.native_codex_rpc import CodexRPC
        from studio.native_codex_exec import provider_overrides
        import tomllib
        provider=provider_overrides(tomllib.loads(self.config_file.read_text()))
        with shared_conversation_config(self.binding,self.cwd,self.artifacts) as pinned:
            with CodexRPC(EXE,self.home,self.config,self.cwd,20,shared_config=pinned,provider_args=provider) as rpc:
                thread=rpc.request('thread/start',dict(model='fixture',cwd=str(self.cwd),sandbox='read-only',approvalPolicy='never',ephemeral=False))['thread']
                turn=rpc.request('turn/start',dict(threadId=thread['id'],input=[dict(type='text',text='EXTERNAL_SYNTHETIC')],model='fixture',effort='low'))['turn']['id']
                while True:
                    event=rpc.next_event()
                    if event and event.get('method')=='turn/completed':break
        result=LocalCodex(dict(self.binding,session_id=thread['id'],expected_native_turn_id=turn),self.limits).start('ADOPT_EXISTING',self.cwd,self.artifacts)
        self.assertEqual(result['session_id'],thread['id']);self.assertEqual(len(self.calls),2)
        self.assertEqual(len(list((self.config/'sessions').glob('*/*/*/*.jsonl'))),1)

class SharedBindingTests(unittest.TestCase):
    def setUp(self):
        import test_native_local
        self.base=test_native_local.NativeLocalTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.rt=self.base.rt;self.workspace=self.base.base.project['id']

    def test_click_prepare_pins_shared_no_tool_binding_without_starting(self):
        value=self.rt.session_center.prepare(dict(workspace=self.workspace,handle='new',profile='default-codex',
            prompt='SYNTHETIC_ONLY',permission='read',mode='Ask',interaction='click',shared_conversation=True))
        binding=value['data']['binding']
        self.assertTrue(binding['shared_conversation']);self.assertTrue(binding['no_tools']);self.assertEqual(binding['tools'],[])
        self.assertEqual(binding['home'],str(self.base.home));self.assertEqual(self.rt.db.listing('session-operation'),[])

    def test_shared_selection_cannot_grant_write_or_cross_provider_tools(self):
        for node in (dict(profile='default-codex',mode='Agent',permission='workspace-write'),dict(profile='default-claude',mode='Ask')):
            with self.subTest(node=node),self.assertRaises(Blocked):
                self.rt.node_binding(self.workspace,dict(node,shared_conversation=True),direct=True,admission=False)
        with self.assertRaises(Invalid):
            self.rt.node_binding(self.workspace,dict(profile='default-codex',mode='Ask',shared_conversation='true'),direct=True,admission=False)

    def test_previously_shared_thread_cannot_be_silently_downgraded(self):
        from test_workspace_sessions import SID
        binding=dict(self.base.binding,runtime_profile='default-codex',shared_conversation=True,no_tools=True,tools=[],session_id=SID)
        handle=dict(profile=dict(provider='codex',home=str(self.base.home),config_root=str(self.base.home/'.codex')),
                    native=dict(cwd=self.base.base.project['path'],cwd_changed=False,session_id=SID,sha256='synthetic'),ambiguous=False)
        self.rt.db.put('chat-profile','shared-test',dict(workspace=self.workspace,binding=binding))
        with patch.object(self.rt.session_center,'resolve',return_value=('synthetic',handle)):
            with self.assertRaisesRegex(Blocked,'保持共享选项'):
                self.rt.node_binding(self.workspace,dict(profile='default-codex',session='synthetic',mode='Ask',shared_conversation=False),direct=True,admission=False)
        self.assertEqual(self.rt.db.listing('session-operation'),[])

    def test_discovery_offers_existing_native_interactive_threads_but_not_exec(self):
        from studio.workbench_sessions import WorkbenchSessions
        from test_workspace_sessions import native,SID
        from studio.session_sources import scan
        path=native(self.base.home/'.codex','codex',self.base.base.source)
        rows=[json.loads(x) for x in path.read_text().splitlines()];rows[0]['payload']['source']='cli'
        path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        records,_=scan(str(self.base.home/'.codex/sessions'),'codex',include_paths=True)
        self.assertEqual(records[0]['native_source'],'cli')
        # Keep this a discovery-shape test: no fake native metadata acceptance.
        import time
        self.rt.native_local_index={('codex',self.base.base.project['path']):(time.monotonic()+60,[SID])}
        manager=WorkbenchSessions(self.rt)
        listed=manager.listing(self.workspace)['sessions']
        self.assertEqual(len(listed),1);self.assertTrue(listed[0]['choices']['shared_conversation'])
        rows[0]['payload']['source']='exec';path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        listed=manager.listing(self.workspace)['sessions']
        self.assertFalse(listed[0]['choices']['shared_conversation'])


if __name__=='__main__':unittest.main()
