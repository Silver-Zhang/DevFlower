"""Real installed Codex + synthetic localhost model, never real credentials."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.codex_narrow_read import TOOLS
import test_shared_codex as shared_fixture


class NarrowCodexTests(unittest.TestCase):
    def setUp(self):
        shared_fixture.SharedCodexTests.setUp(self)
        self.binding.pop('shared_conversation');self.binding.pop('no_tools')
        self.binding['read_paths']=[]
        self.allowed=self.cwd/'allowed.txt';self.allowed.write_text('SYNTHETIC_ALLOWED_READ')
        self.denied=self.cwd.parent/'not-granted.txt';self.denied.write_text('SYNTHETIC_DENIED_READ')
        self.plan=[];owner=self
        self.rpc_metadata=[];self.executed_readers=[]
        self.addCleanup(self.record_synthetic_evidence)
        from studio.native_codex_rpc import CodexRPC
        from studio.codex_narrow_read import NarrowReadScope
        next_event=CodexRPC.next_event;execute=NarrowReadScope.execute
        def observed(rpc):
            event=next_event(rpc)
            if event and rpc.home==owner.home:
                params=event.get('params',{});item=params.get('item',{})
                owner.rpc_metadata.append(dict(method=event.get('method'),request_id=event.get('id'),thread=params.get('threadId'),turn=params.get('turnId'),item_type=item.get('type'),tool=item.get('tool')))
            return event
        def reader(scope,name,args):
            if scope.cwd==str(owner.cwd):owner.executed_readers.append(name)
            return execute(scope,name,args)
        for manager in (patch.object(CodexRPC,'next_event',observed),patch.object(NarrowReadScope,'execute',reader)):
            manager.start();self.addCleanup(manager.stop)
        def do_POST(handler):
            request=json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
            owner.calls.append(request);index=len(owner.calls)
            owner.arrived.set();owner.hold.wait(10)
            if index%2:
                planned=owner.plan.pop(0);planned=planned if isinstance(planned,list) else [planned]
                items=[dict(id='fc-'+str(index)+'-'+str(i),type='function_call',status='completed',call_id='call_'+str(index)+'_'+str(i),name=name,arguments=json.dumps(args)) for i,(name,args) in enumerate(planned)]
            else:
                items=[dict(id='msg-'+str(index),type='message',status='completed',role='assistant',content=[dict(type='output_text',text='SYNTHETIC_NARROW_DONE',annotations=[])])]
            response=dict(id='resp-'+str(index),object='response',created_at=0,status='completed',model='fixture',output=items,usage=dict(input_tokens=1,output_tokens=1,total_tokens=2))
            handler.send_response(200);handler.send_header('Content-Type','text/event-stream');handler.end_headers()
            events=[dict(type='response.created',response=dict(response,status='in_progress',output=[]))]
            for i,item in enumerate(items):events.extend([dict(type='response.output_item.added',output_index=i,item=dict(item,status='in_progress')),dict(type='response.output_item.done',output_index=i,item=item)])
            events.append(dict(type='response.completed',response=response))
            for event in events:
                handler.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());handler.wfile.flush()
        self.server.RequestHandlerClass.do_POST=do_POST

    def record_synthetic_evidence(self):
        target=os.environ.get('DEVFLOWER_NARROW_EVIDENCE')
        if target:
            folder=Path(target);folder.mkdir(parents=True,exist_ok=True)
            (folder/(self._testMethodName+'.json')).write_text(json.dumps(dict(executable=self.binding['executable'],synthetic_only=True,real_account=False,requests=self.calls,rpc_metadata=self.rpc_metadata,executed_readers=self.executed_readers),ensure_ascii=False,indent=2)+'\n')

    def run_read(self,path,**binding):
        self.plan.append(('devflower_read_file',dict(path=str(path))))
        return LocalCodex(dict(self.binding,**binding),self.limits).start('SYNTHETIC_READ_TEST',self.cwd,self.artifacts)

    def assert_catalog(self):
        for call in self.calls:
            self.assertEqual({t.get('name') for t in call['tools']},{'request_user_input',*(t['name'] for t in TOOLS)})

    def test_actual_dynamic_read_in_approved_workspace(self):
        config=self.config_file.read_bytes()
        result=self.run_read('allowed.txt')
        self.assertEqual(result['report']['reply'],'SYNTHETIC_NARROW_DONE')
        self.assertEqual(result['permission_mode'],'native-workspace-explicit-read')
        self.assertEqual(len(self.calls),2);self.assert_catalog()
        self.assertIn('SYNTHETIC_ALLOWED_READ',json.dumps(self.calls[1]))
        self.assertFalse(self.marker.exists());self.assertEqual(self.config_file.read_bytes(),config)

    def test_actual_dynamic_read_denies_owned_ungranted_canary(self):
        self.assertTrue(self.denied.is_file())
        result=self.run_read(self.denied)
        self.assertEqual(result['report']['reply'],'SYNTHETIC_NARROW_DONE')
        self.assertEqual(len(self.calls),2);self.assert_catalog()
        self.assertNotIn('SYNTHETIC_DENIED_READ',json.dumps(self.calls))
        self.assertIn('无法读取',json.dumps(self.calls[1],ensure_ascii=False))
        self.assertEqual(self.denied.read_text(),'SYNTHETIC_DENIED_READ')

    def test_native_restart_resume_keeps_id_and_actual_reader(self):
        first=self.run_read('allowed.txt')
        second=self.run_read('allowed.txt',session_id=first['session_id'],expected_native_turn_id=first['native_turn_id'])
        self.assertEqual(first['session_id'],second['session_id'])
        self.assertNotEqual(first['native_turn_id'],second['native_turn_id'])
        self.assertEqual(len(self.calls),4);self.assert_catalog()
        self.assertIn('SYNTHETIC_ALLOWED_READ',json.dumps(self.calls[3]))

    def test_pure_shared_mode_refuses_persistent_reader_tools(self):
        first=self.run_read('allowed.txt')
        with self.assertRaisesRegex(Blocked,'外部工具'):
            LocalCodex(dict(self.binding,shared_conversation=True,no_tools=True,session_id=first['session_id']),self.limits).start('MUST_NOT_SEND',self.cwd,self.artifacts)
        self.assertEqual(len(self.calls),2)

    def test_missing_rollout_path_cannot_skip_persistent_tool_verification(self):
        from studio.native_codex_rpc import CodexRPC
        first=self.run_read('allowed.txt');request=CodexRPC.request
        def missing(rpc,method,params):
            result=request(rpc,method,params)
            if rpc.home==self.home and method=='thread/resume':result['thread']['path']=None
            return result
        with patch.object(CodexRPC,'request',missing),self.assertRaisesRegex(Blocked,'工具元数据路径'):
            self.run_read('allowed.txt',session_id=first['session_id'])
        self.assertEqual(len(self.calls),2)

    def test_native_explicit_single_file_and_directory_and_bounded_listing(self):
        explicit=self.cwd.parent/'explicit-dir';explicit.mkdir();(explicit/'one').write_text('EXPLICIT_DIRECTORY')
        single=self.cwd.parent/'explicit-file';single.write_text('EXPLICIT_FILE')
        self.binding['read_paths']=[str(explicit),str(single)]
        self.run_read(explicit/'one');self.assertNotIn('EXPLICIT_DIRECTORY',json.dumps(self.calls[-1]))
        self.assertIn('无法读取',json.dumps(self.calls[-1],ensure_ascii=False))
        self.run_read(single);self.assertIn('EXPLICIT_FILE',json.dumps(self.calls[-1]))
        self.plan.append(('devflower_list_directory',dict(path='.')))
        LocalCodex(self.binding,self.limits).start('SYNTHETIC_LIST',self.cwd,self.artifacts)
        self.assertIn('allowed.txt',json.dumps(self.calls[-1]));self.assert_catalog()

    def test_native_symlinks_hardlink_traversal_and_proc_are_denied(self):
        (self.cwd/'link').symlink_to(self.denied);(self.cwd/'chain').symlink_to(self.cwd/'link')
        os.link(self.denied,self.cwd/'hard')
        for path in ['link','chain','hard','../not-granted.txt','/proc/self/fd/0']:
            with self.subTest(path=path):
                self.run_read(path)
                self.assertIn('无法读取',json.dumps(self.calls[-1],ensure_ascii=False))
                self.assertNotIn('SYNTHETIC_DENIED_READ',json.dumps(self.calls))

    def test_config_mcp_change_before_turn_stops_business_request(self):
        from studio.shared_codex_config import SharedConversationConfig
        wrap=SharedConversationConfig.wrap
        def changed(value,argv):
            result=wrap(value,argv)
            if value.binding['home']==str(self.home):
                self.config_file.write_text(self.config_file.read_text()+'\n[mcp_servers.late]\ncommand="/bin/false"\n')
            return result
        with patch.object(SharedConversationConfig,'wrap',changed),self.assertRaises(Blocked):self.run_read('allowed.txt')
        self.assertEqual(self.calls,[]);self.assertFalse(self.marker.exists())

    def test_workspace_replaced_before_start_stops_business_request(self):
        from studio.shared_codex_config import SharedConversationConfig
        wrap=SharedConversationConfig.wrap
        replacement=self.cwd.parent/'replacement';replacement.mkdir();(replacement/'allowed.txt').write_text('NOT_GRANTED_REPLACEMENT')
        def changed(value,argv):
            result=wrap(value,argv)
            if value.binding['home']==str(self.home):
                self.cwd.rename(self.cwd.parent/'original-workspace');self.cwd.symlink_to(replacement,target_is_directory=True)
            return result
        with patch.object(SharedConversationConfig,'wrap',changed),self.assertRaises(Blocked):self.run_read('allowed.txt')
        self.assertEqual(self.calls,[])

    def test_model_cannot_invoke_unregistered_shell(self):
        self.plan.append(('exec_command',dict(cmd='printf SYNTHETIC_UNAUTHORIZED_EXECUTION')))
        result=LocalCodex(self.binding,self.limits).start('SYNTHETIC_UNKNOWN_TOOL',self.cwd,self.artifacts)
        self.assertEqual(result['report']['reply'],'SYNTHETIC_NARROW_DONE')
        self.assertEqual(len(self.calls),2);self.assert_catalog()
        outputs=[item for item in self.calls[1]['input'] if item.get('type')=='function_call_output']
        print('SYNTHETIC_UNKNOWN_TOOL_OUTPUT',json.dumps(outputs,ensure_ascii=False))
        self.assertTrue(outputs)
        self.assertNotIn('SYNTHETIC_UNAUTHORIZED_EXECUTION',json.dumps(outputs))
        self.assertRegex(json.dumps(outputs).lower(),'unknown|unrecognized|unsupported|not found|not supported')
        self.assertEqual(self.executed_readers,[])
        self.assertFalse(any(e['method']=='item/tool/call' or e['item_type']=='commandExecution' for e in self.rpc_metadata))

    def test_native_default_ignore_sensitive_and_directory_listing(self):
        (self.cwd/'.gitignore').write_text('ignored.txt\n')
        (self.cwd/'ignored.txt').write_text('SYNTHETIC_IGNORED_VALUE')
        (self.cwd/'.env').write_text('SYNTHETIC_SENSITIVE_VALUE')
        for name in ('ignored.txt','.env'):
            self.run_read(name)
            outputs=[x for x in self.calls[-1]['input'] if x.get('type')=='function_call_output']
            self.assertIn('无法读取',json.dumps(outputs,ensure_ascii=False))
            self.assertNotIn('SYNTHETIC_IGNORED_VALUE',json.dumps(outputs));self.assertNotIn('SYNTHETIC_SENSITIVE_VALUE',json.dumps(outputs))
        self.plan.append(('devflower_list_directory',dict(path='.')))
        LocalCodex(self.binding,self.limits).start('SYNTHETIC_LIST_POLICY',self.cwd,self.artifacts)
        outputs=[x for x in self.calls[-1]['input'] if x.get('type')=='function_call_output']
        self.assertIn('allowed.txt',json.dumps(outputs));self.assertNotIn('ignored.txt',json.dumps(outputs));self.assertNotIn('.env',json.dumps(outputs))

    def test_native_explicit_non_sensitive_file_can_override_ignore_only(self):
        (self.cwd/'.gitignore').write_text('ignored.txt\n');p=self.cwd/'ignored.txt';p.write_text('EXPLICIT_IGNORED_ALLOWED')
        self.binding['read_paths']=[str(p)]
        self.run_read(p)
        self.assertIn('EXPLICIT_IGNORED_ALLOWED',json.dumps(self.calls[-1]))
        sensitive=self.cwd/'.env';sensitive.write_text('SYNTHETIC_SENSITIVE_VALUE')
        self.binding['read_paths']=[str(sensitive)]
        with self.assertRaises(Blocked):self.run_read(sensitive)
        self.assertEqual(len(self.calls),2)

    def test_native_ignore_changed_during_startup_never_sends_business_prompt(self):
        from studio.shared_codex_config import SharedConversationConfig
        policy=self.cwd/'.gitignore';policy.write_text('ignored.txt\n')
        wrap=SharedConversationConfig.wrap
        def changed(value,argv):
            result=wrap(value,argv)
            if value.binding['home']==str(self.home):policy.write_text('')
            return result
        with patch.object(SharedConversationConfig,'wrap',changed),self.assertRaises(Blocked):self.run_read('allowed.txt')
        self.assertEqual(self.calls,[])

    def test_other_builtin_names_and_mixed_parallel_calls_cannot_expand_authority(self):
        for name in ('write_stdin','view_image','apply_patch','SYNTHETIC_UNKNOWN_TOOL'):
            self.plan.append((name,dict(path=str(self.denied))))
            LocalCodex(self.binding,self.limits).start('SYNTHETIC_UNKNOWN_BUILTIN',self.cwd,self.artifacts)
            outputs=[item for item in self.calls[-1]['input'] if item.get('type')=='function_call_output']
            self.assertTrue(outputs);self.assertIn('unsupported call',json.dumps(outputs));self.assertEqual(self.executed_readers,[])
        self.plan.append([('exec_command',dict(cmd='printf SYNTHETIC_UNAUTHORIZED_EXECUTION')),('devflower_read_file',dict(path='allowed.txt'))])
        LocalCodex(self.binding,self.limits).start('SYNTHETIC_MIXED_CALLS',self.cwd,self.artifacts)
        outputs=[item for item in self.calls[-1]['input'] if item.get('type')=='function_call_output']
        self.assertEqual(len(outputs),2);self.assertIn('unsupported call: exec_command',json.dumps(outputs))
        self.assertIn('SYNTHETIC_ALLOWED_READ',json.dumps(outputs));self.assertNotIn('SYNTHETIC_DENIED_READ',json.dumps(self.calls))
        self.assertEqual(self.executed_readers,['devflower_read_file']);self.assert_catalog()

    def test_native_question_tool_never_grants_extra_file_access(self):
        question=dict(id='synthetic',header='权限',question='Synthetic question',options=[dict(label='允许',description='Synthetic only'),dict(label='拒绝',description='Synthetic only')])
        self.plan.append(('request_user_input',dict(questions=[question])))
        try:LocalCodex(self.binding,self.limits).start('SYNTHETIC_QUESTION',self.cwd,self.artifacts)
        except Blocked:pass  # A server-initiated question is unsupported/stop.
        else:
            outputs=[item for item in self.calls[-1]['input'] if item.get('type')=='function_call_output']
            self.assertTrue(outputs);self.assertRegex(json.dumps(outputs).lower(),'unavailable|not available|unsupported')
        self.assertEqual(self.executed_readers,[])

    def test_stale_preview_does_not_send(self):
        first=self.run_read('allowed.txt')
        with self.assertRaisesRegex(Blocked,'另一端继续'):
            self.run_read('allowed.txt',session_id=first['session_id'],expected_native_turn_id='stale')
        self.assertEqual(len(self.calls),2)

    def test_revoked_grant_does_not_authorize_a_new_read_on_same_id(self):
        explicit=self.cwd.parent/'revocable';explicit.write_text('PREVIOUSLY_GRANTED')
        self.binding['read_paths']=[str(explicit)]
        first=self.run_read(explicit)
        explicit.write_text('REVOKED_NEW_CONTENT')
        self.binding['read_paths']=[]
        second=self.run_read(explicit,session_id=first['session_id'],expected_native_turn_id=first['native_turn_id'])
        self.assertEqual(first['session_id'],second['session_id'])
        self.assertIn('无法读取',json.dumps(self.calls[-1],ensure_ascii=False))
        self.assertNotIn('REVOKED_NEW_CONTENT',json.dumps(self.calls))

    def test_unsupported_executable_stops_before_business_model(self):
        with self.assertRaises(Blocked):self.run_read('allowed.txt',executable='/usr/bin/false')
        self.assertEqual(self.calls,[])

    def test_concurrent_writer_is_rejected_without_stopping_holder(self):
        first=self.run_read('allowed.txt')
        self.arrived.clear();self.hold.clear();results=[]
        def run():
            try:results.append(self.run_read('allowed.txt',session_id=first['session_id']))
            except BaseException as exc:results.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(self.arrived.wait(10))
            with self.assertRaisesRegex(Blocked,'另一端持有'):
                self.run_read('allowed.txt',session_id=first['session_id'])
            self.assertEqual(len(self.calls),3)
        finally:self.hold.set();thread.join(12)
        self.assertFalse(thread.is_alive());self.assertIsInstance(results[0],dict)
        self.assertEqual(results[0]['session_id'],first['session_id']);self.assertEqual(len(self.calls),4)

    def test_cancellation_stops_owned_turn_without_a_tool_read_or_retry(self):
        state={'cancel_requested':False}
        class Store:
            def get(self,run):return dict(data=state)
            def intent(self,*args):return 'synthetic-operation'
            def finish(self,*args,**kwargs):pass
        self.arrived.clear();self.hold.clear();results=[]
        self.plan.append(('devflower_read_file',dict(path='allowed.txt')))
        def run():
            try:results.append(LocalCodex(self.binding,self.limits).start('SYNTHETIC_CANCEL',self.cwd,self.artifacts,store=Store(),run='synthetic'))
            except BaseException as exc:results.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(self.arrived.wait(10));state['cancel_requested']=True
            thread.join(6)
        finally:self.hold.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertIsInstance(results[0],Blocked)
        self.assertEqual(len(self.calls),1)

    def test_timeout_is_not_success_and_does_not_automatically_resubmit(self):
        # Warm compatibility separately so the intended timeout is a held
        # business turn, not a failed preflight. Both calls use one scope.
        from studio.codex_narrow_probe import require_narrow_capability
        self.run_read('allowed.txt');self.calls.clear();self.plan.clear()
        self.hold.clear();self.plan.append(('devflower_read_file',dict(path='allowed.txt')))
        started=time.monotonic()
        try:
            with self.assertRaises(Blocked):LocalCodex(self.binding,dict(self.limits,step_timeout_seconds=1.5)).start('SYNTHETIC_TIMEOUT',self.cwd,self.artifacts)
        finally:self.hold.set()
        self.assertLess(time.monotonic()-started,8)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main(verbosity=2)
