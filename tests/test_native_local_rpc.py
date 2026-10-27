"""Protocol negatives for native mode; these are not real model evidence."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked
from studio.local_codex_adapter import LocalCodex
from studio.native_codex_rpc import CodexRPC,metadata

SID='00000000-0000-4000-8000-000000000001'


class FakeRPC:
    error=None
    prior='prior-turn'
    events=[]
    instances=[]
    def __init__(self,*a,**kw):
        self.calls=[];self.identity={'pid':123};self.process=type('Process',(),{'returncode':None})()
        self.overflow=False;self.events=list(type(self).events);type(self).instances.append(self)
    def __enter__(self):return self
    def __exit__(self,*args):self.process.returncode=0
    def request(self,method,params):
        json.dumps(params)  # Same wire encoding constraint as the real client.
        self.calls.append((method,params))
        if method=='thread/resume' and self.error:raise Blocked(self.error)
        if method in ('thread/start','thread/resume'):return {'thread':{'id':SID}}
        if method=='thread/read':return {'thread':{'turns':[{'id':self.prior}]}}
        if method=='turn/start':return {'turn':{'id':'this-turn'}}
        return {}
    def next_event(self):
        if not self.events:raise Blocked('no more events')
        return self.events.pop(0)


class NativeLocalProtocolTests(unittest.TestCase):
    def setUp(self):
        FakeRPC.instances=[];FakeRPC.error=None;FakeRPC.prior='prior-turn'
        FakeRPC.events=[{'method':'item/completed','params':dict(threadId=SID,turnId='this-turn',
            item=dict(type='agentMessage',phase='final_answer',text='ACTUAL'))},
            {'method':'turn/completed','params':dict(threadId=SID,turn=dict(id='this-turn',status='completed'))}]
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.binding=dict(executable='native',home=str(self.home),config_root=str(self.home/'.codex'),
            provider='codex',model='model',effort='high',role='reviewer',session_id=SID,
            execution_backend='native-local',project='test',conversation_format='text',readonly_files=[])
        self.client=LocalCodex(self.binding,dict(step_timeout_seconds=10,max_log_bytes=50000))
        self.p=patch('studio.local_codex_adapter.CodexRPC',FakeRPC);self.p.start();self.addCleanup(self.p.stop)
    def run_turn(self):return self.client.start('the question',str(self.home),self.home)

    def test_native_writer_rejection_does_not_send_a_turn(self):
        FakeRPC.error='another native writer owns this session'
        with self.assertRaises(Blocked):self.run_turn()
        self.assertNotIn('turn/start',[k for k,_ in FakeRPC.instances[0].calls])

    def test_external_continuation_after_preview_requires_fresh_confirmation(self):
        self.binding['expected_native_turn_id']='old-turn'
        with self.assertRaisesRegex(Blocked,'另一端继续'):self.run_turn()
        self.assertNotIn('turn/start',[k for k,_ in FakeRPC.instances[0].calls])

    def test_old_turn_output_cannot_replace_the_current_answer(self):
        FakeRPC.events.insert(1,{'method':'item/completed','params':dict(threadId=SID,turnId='old-turn',
            item=dict(type='agentMessage',phase='final_answer',text='STALE'))})
        result=self.run_turn();self.assertEqual(result['report']['reply'],'ACTUAL')
        self.assertEqual(result['native_turn_id'],'this-turn')

    def test_foreign_thread_event_is_rejected(self):
        FakeRPC.events[0]['params']['threadId']='another-thread'
        with self.assertRaisesRegex(Blocked,'跨越'):self.run_turn()

    def test_missing_terminal_event_is_not_a_successful_turn(self):
        FakeRPC.events.pop()
        with self.assertRaises(Blocked):self.run_turn()

    def test_native_permission_flags_do_not_use_danger_full_access(self):
        self.run_turn();params=FakeRPC.instances[0].calls[0][1]
        self.assertEqual(params['sandbox'],'read-only');self.assertEqual(params['approvalPolicy'],'never')
        self.binding['role']='implementer';self.run_turn()
        self.assertEqual(FakeRPC.instances[-1].calls[0][1]['sandbox'],'workspace-write')

    def test_path_object_from_actual_model_probe_is_wire_serializable(self):
        value=self.client.start('probe',self.home,self.home)
        self.assertEqual(value['cwd'],str(self.home))

    def test_observer_metadata_refuses_mutating_methods(self):
        with self.assertRaises(Blocked):metadata(self.binding,str(self.home),'thread/resume',{'threadId':SID})

    def test_protocol_output_budget_stops_and_reaps_only_its_child(self):
        exe=self.home/'protocol-stub'
        exe.write_text('#!'+sys.executable+'\nimport sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if r.get("method")=="initialize":\n  print(json.dumps({"id":r["id"],"result":{}}),flush=True)\n elif "id" in r:\n  print("x"*1000,flush=True)\n')
        exe.chmod(0o700)
        with self.assertRaisesRegex(Blocked,'输出超过'):
            with CodexRPC(str(exe),self.home,self.home,self.home,5,cap=128) as rpc:
                rpc.request('thread/read',{'threadId':SID})
        self.assertIsNotNone(rpc.process.returncode)


if __name__=='__main__':unittest.main()
