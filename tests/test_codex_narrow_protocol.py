"""Protocol counterexamples: an ID match alone must never authorize a read."""
import copy
import io
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.codex_narrow_read import NarrowToolRequests,TOOLS,PROFILE
from studio.native_codex_rpc import CodexRPC


class NarrowProtocolTests(unittest.TestCase):
    def event(self):
        return dict(id=1,method='item/tool/call',params=dict(threadId='thread',turnId='turn',callId='call',tool='devflower_read_file',arguments=dict(path='allowed')))

    def test_valid_request_is_executed_exactly_once_and_replay_is_rejected(self):
        scope=SimpleNamespace(execute=Mock(return_value={'success':True}))
        requests=NarrowToolRequests(scope,'thread','turn')
        self.assertEqual(requests.handle(self.event()),(1,{'success':True}))
        for event in (self.event(),dict(self.event(),id=2)):
            with self.assertRaises(Blocked):requests.handle(event)
        scope.execute.assert_called_once_with('devflower_read_file',dict(path='allowed'))

    def test_cross_thread_turn_namespace_unknown_request_and_bad_ids_never_read(self):
        invalid=[]
        for key,value in [('threadId','foreign'),('turnId','foreign'),('namespace','shell'),('extra',True),('callId','')]:
            event=self.event();event['params'][key]=value;invalid.append(event)
        for ident in (None,True,[],{},'', 'x'*257):invalid.append(dict(self.event(),id=ident))
        invalid.append(dict(self.event(),method='item/permissions/requestApproval'))
        invalid.append(dict(self.event(),params=[]))
        for event in invalid:
            scope=SimpleNamespace(execute=Mock())
            with self.subTest(event=event),self.assertRaises(Blocked):NarrowToolRequests(scope,'thread','turn').handle(event)
            scope.execute.assert_not_called()

    def rpc(self):
        rpc=object.__new__(CodexRPC)
        rpc.cwd=Path('/synthetic/workspace');rpc.shared_config=SimpleNamespace(closed=False,read_scope=SimpleNamespace(validate=Mock()))
        rpc.next_id=0;rpc.events=[];rpc.send=Mock();rpc.active_thread=None;rpc.active_turn=None
        return rpc

    def test_client_server_id_collision_queues_request_until_real_response(self):
        rpc=self.rpc();event=self.event()
        rpc.poll=Mock(side_effect=[event,dict(id=1,result={})])
        self.assertEqual(rpc.request('initialize',{}),{})
        self.assertEqual(rpc.events,[event]);self.assertEqual(rpc.poll.call_count,2)

    def test_only_fixed_profile_and_tool_contract_may_start_or_resume(self):
        valid=dict(cwd='/synthetic/workspace',permissions=PROFILE,approvalPolicy='never',dynamicTools=TOOLS)
        invalid=[dict(valid,permissions=':full-access'),dict(valid,sandbox='read-only'),dict(valid,config={}),dict(valid,dynamicTools=[]),dict(valid,cwd='/foreign'),dict(valid,runtimeWorkspaceRoots=['/'])]
        for params in invalid:
            rpc=self.rpc()
            with self.subTest(params=params),self.assertRaises(Blocked):rpc.request('thread/start',params)
            rpc.send.assert_not_called()
        rpc=self.rpc()
        with self.assertRaises(Blocked):rpc.request('thread/resume',valid)
        rpc.send.assert_not_called()

    def test_native_fs_reads_and_turn_permission_overrides_are_denied(self):
        cases=[('fs/readFile',dict(path='/etc/passwd')),('turn/start',dict(threadId='thread',permissions=':full-access',input=[])),('turn/start',dict(threadId='thread',permissions=PROFILE,input=[dict(type='skill',path='/foreign')]))]
        for method,params in cases:
            rpc=self.rpc()
            with self.subTest(method=method),self.assertRaises(Blocked):rpc.request(method,params)
            rpc.send.assert_not_called()

    def test_server_must_confirm_actual_profile_and_single_workspace(self):
        for changes in (dict(activePermissionProfile=dict(id=':read-only')),dict(runtimeWorkspaceRoots=['/'])):
            rpc=self.rpc();result=dict(activePermissionProfile=dict(id=PROFILE),cwd='/synthetic/workspace',runtimeWorkspaceRoots=['/synthetic/workspace']);result.update(changes)
            rpc.poll=Mock(return_value=dict(id=1,result=result))
            with self.assertRaises(Blocked):rpc.request('thread/start',dict(cwd='/synthetic/workspace',permissions=PROFILE,approvalPolicy='never',dynamicTools=TOOLS))

    def test_cancelled_or_expired_response_does_not_write_any_bytes(self):
        for cancelled,deadline in [(True,time.monotonic()+10),(False,time.monotonic()-1)]:
            rpc=self.rpc();rpc.cancelled=lambda:cancelled;rpc.deadline=deadline;rpc.process=SimpleNamespace(stdin=io.BytesIO())
            with self.assertRaises(Blocked):rpc.respond_tool(1,dict(success=True))
            self.assertEqual(rpc.process.stdin.getvalue(),b'')

    def test_broken_stdin_during_cleanup_never_leaks_stdout(self):
        rpc=self.rpc();output=io.BytesIO()
        broken=SimpleNamespace(closed=False,close=Mock(side_effect=BrokenPipeError()))
        rpc.process=SimpleNamespace(poll=lambda:0,stdin=broken,stdout=output)
        rpc.selector=SimpleNamespace(close=Mock());rpc.owned_image=SimpleNamespace(close=Mock())
        rpc.close();self.assertTrue(output.closed);rpc.selector.close.assert_called_once();rpc.owned_image.close.assert_called_once()


class NarrowBindingTests(unittest.TestCase):
    def setUp(self):
        import test_native_local
        self.fixture=test_native_local.NativeLocalTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.rt=self.fixture.rt;self.workspace=self.fixture.base.project['id']

    def test_ask_plan_authorization_preview_names_only_the_two_readers(self):
        for mode in ('Ask','Plan'):
            value=self.rt.session_center.prepare(dict(workspace=self.workspace,handle='new',profile='default-codex',prompt='SYNTHETIC',permission='read',mode=mode,interaction='click'))
            binding=value['data']['binding']
            self.assertEqual(binding['tools'],[t['name'] for t in TOOLS]);self.assertEqual(binding['role'],'reviewer')
        self.assertEqual(self.rt.db.listing('session-operation'),[])


if __name__=='__main__':unittest.main(verbosity=2)
