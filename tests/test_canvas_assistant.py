"""Synthetic app boundaries; actual signed-in Copilot evidence stays separate."""
import copy,json,os,secrets,sqlite3,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).parent)]
from test_workbench_runtime import fixture
from studio.canvas_assistant import CanvasAssistant,decode_reply,expand_plan
from studio.configuration_assistant import ConfigurationAssistant
from studio.workbench_examples import examples
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked
from studio.copilot_client import CopilotRPC


class SyntheticBackend:
    calls=[];entered=0;closed=0;delay=0
    def __init__(self,cwd,**kw):self.cwd=cwd;self.cancelled=kw.get('cancelled',lambda:False);self.process=type('P',(),{'pid':os.getpid()})()
    def __enter__(self):type(self).entered+=1;return self
    def __exit__(self,*a):type(self).closed+=1
    def catalog(self):return dict(provider='devflower-agent',source='SYNTHETIC catalog; not native inference',models=[dict(id='auto',name='Synthetic Auto')],observed_at=time.time(),inference_calls=0,execution_verified=False)
    def chat(self,model,prompt):
        type(self).calls.append(json.loads(prompt.split('\n',1)[1]));end=time.monotonic()+type(self).delay
        while time.monotonic()<end:
            if self.cancelled():raise Blocked('Synthetic cancel')
            time.sleep(.01)
        ctx=type(self).calls[-1];name='Synthetic '+ctx['conversation'][-1]['text'][:40];profile,choice=next(iter(ctx['available_agents'].items()));m=choice['models'][0]
        nodes=[]
        for id,kind in [('start','start'),('analyze','agent'),('result','result')]:
            nodes.append(dict(id=id,kind=kind,name=id,profile=profile if kind=='agent' else '',model=m['id'] if kind=='agent' else '',effort=m['efforts'][0] if kind=='agent' else '',mode='Ask',initial_prompt='理解目标',round_prompt=ctx['conversation'][-1]['text'],handoff_prompt='简洁回答'))
        plan=dict(version=1,name=name,summary='Synthetic plan, not a native call',nodes=nodes,edges=[dict(source='start',target='analyze'),dict(source='analyze',target='result')])
        return dict(text=json.dumps(dict(reply='SYNTHETIC 画布已设计：'+name,plan=plan)),provider='devflower-agent',requested_model=model,resolved_model='unknown',session_id='synthetic',tools_enabled=False)


class CanvasAssistantTests(unittest.TestCase):
    def setUp(self):
        self.base,self.rt=fixture(self);self.w=self.base.project['id'];self.g=examples(self.rt.operator['profiles'])[0]
        self.choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in self.rt.operator['profiles'].items()}
        patcher=patch.object(ConfigurationAssistant,'choices',return_value=self.choices);patcher.start();self.addCleanup(patcher.stop)
        self.ai=CanvasAssistant(self.rt);self.ai.backend=SyntheticBackend;self.addCleanup(self.ai.shutdown)
        SyntheticBackend.calls=[];SyntheticBackend.entered=0;SyntheticBackend.closed=0;SyntheticBackend.delay=0
    def data(self,**changes):
        d=dict(workspace=self.w,conversation=secrets.token_hex(16),request_id=secrets.token_hex(16),expected=None,message='创建只读分析',graph=copy.deepcopy(self.g),model='auto',allow_context=True);d.update(changes);return d
    def wait(self,key):
        for _ in range(300):
            value=self.ai.conversation(self.w,key)
            if value['turn']['state'] not in ('QUEUED','RUNNING'):return value
            time.sleep(.01)
        self.fail('Synthetic operation did not finish')
    def run_turn(self,data=None):
        data=data or self.data();v=self.ai.start(data);return self.wait(v['id'])
    def test_catalog_does_not_call_model_or_enable_execution(self):
        result=self.ai.catalog();self.assertEqual(SyntheticBackend.calls,[]);self.assertFalse(result['execution_verified']);self.assertEqual(SyntheticBackend.closed,1)
    def test_builtin_conversation_returns_visible_reply_and_draft_proposal(self):
        v=self.run_turn();self.assertEqual(v['turn']['state'],'COMPLETED');self.assertEqual([m['role'] for m in v['messages']],['user','assistant']);self.assertEqual(v['turn']['receipt']['provider'],'devflower-agent');self.assertTrue(v['turn']['proposal']['graph']);self.assertEqual(self.rt.flow.list('run'),[])
    def test_no_codex_or_claude_cli_used_for_configuration(self):
        with patch('orchestration.adapters.CLI.start',side_effect=AssertionError('Must not call native coding Agent')):self.assertEqual(self.run_turn()['turn']['state'],'COMPLETED')
    def test_second_turn_sees_current_canvas_and_conversation(self):
        v=self.run_turn();g=v['turn']['proposal']['graph'];g['name']='我的手动修改';d=self.data(conversation=v['id'],expected=v['revision'],message='请简化提示词',graph=g);result=self.run_turn(d)
        self.assertEqual(result['turn']['state'],'COMPLETED');self.assertEqual(len(SyntheticBackend.calls),2);self.assertEqual(SyntheticBackend.calls[-1]['current_graph']['name'],'我的手动修改');self.assertEqual(len(SyntheticBackend.calls[-1]['conversation']),3)
    def test_send_requires_explicit_context_consent(self):
        with self.assertRaises(Invalid):self.ai.start(self.data(allow_context=False))
        self.assertEqual(SyntheticBackend.calls,[])
    def test_unsupported_endpoint_or_authorization_rejected(self):
        for field in ('endpoint','api_key','authorization','command'):
            with self.subTest(field=field),self.assertRaises(Invalid):self.ai.start(self.data(**{field:'unsafe'}))
    def test_reading_and_reloading_conversation_does_not_call_model(self):
        v=self.run_turn()
        for _ in range(4):self.ai.conversation(self.w,v['id'])
        self.assertEqual(len(SyntheticBackend.calls),1)
    def test_duplicate_request_id_is_idempotent(self):
        d=self.data();v=self.run_turn(d);again=self.ai.start(d);self.assertEqual(v['id'],again['id']);self.assertEqual(len(SyntheticBackend.calls),1)
    def test_duplicate_request_with_different_payload_refused(self):
        d=self.data();self.run_turn(d)
        with self.assertRaises(Conflict):self.ai.start(dict(d,message='different'))
    def test_parallel_sends_in_one_conversation_are_rejected(self):
        SyntheticBackend.delay=.3;d=self.data();v=self.ai.start(d)
        with self.assertRaises(Blocked):self.ai.start(self.data(conversation=v['id'],expected=v['revision']))
        self.assertEqual(self.wait(v['id'])['turn']['state'],'COMPLETED')
    def test_wrong_workspace_cannot_read_or_cancel_conversation(self):
        v=self.run_turn()
        with patch.object(self.rt.session_center,'workspace',return_value={}):
            for fn in (self.ai.conversation,self.ai.cancel):
                with self.assertRaises(Blocked):fn('another',v['id'])
    def test_stale_revision_does_not_infer(self):
        v=self.run_turn()
        with self.assertRaises(Conflict):self.ai.start(self.data(conversation=v['id'],expected=0))
        self.assertEqual(len(SyntheticBackend.calls),1)
    def test_cancel_does_not_publish_or_apply_proposal(self):
        SyntheticBackend.delay=.5;v=self.ai.start(self.data());self.ai.cancel(self.w,v['id']);done=self.wait(v['id']);self.assertEqual(done['turn']['state'],'CANCELLED');self.assertIsNone(done['turn']['proposal']);self.assertEqual(self.rt.flow.list('run'),[])
    def test_recovery_does_not_replay_or_infer(self):
        v=self.run_turn();key=v['turn']['id'];self.rt.flow.change('canvas-turn',key,dict(state='RUNNING'));recovered=self.ai.conversation(self.w,v['id']);self.assertEqual(recovered['turn']['state'],'RECOVERY_REQUIRED');self.ai.cancel(self.w,v['id']);self.assertEqual(self.ai.conversation(self.w,v['id'])['turn']['state'],'CANCELLED');self.assertEqual(len(SyntheticBackend.calls),1)
    def test_edit_during_generation_refuses_apply(self):
        v=self.run_turn();g=copy.deepcopy(self.g);g['name']='最新编辑'
        with self.assertRaises(Conflict):ConfigurationAssistant(self.rt).apply(dict(workspace=self.w,id=v['turn']['proposal']['id'],graph=g,answer='apply'))
    def test_backend_failure_closes_owned_connection_and_preserves_user_message(self):
        with patch.object(SyntheticBackend,'chat',side_effect=Blocked('HTTP 402')):v=self.run_turn()
        self.assertEqual(v['turn']['state'],'FAILED');self.assertIn('402',v['turn']['reason']);self.assertEqual(len(v['messages']),1);self.assertEqual(SyntheticBackend.closed,1)
    def test_metadata_failure_inside_process_context_closes_connection(self):
        original=self.rt.flow.change
        def fail(kind,key,value,**kw):
            if 'process' in value:raise sqlite3.OperationalError('Synthetic full database')
            return original(kind,key,value,**kw)
        with patch.object(self.rt.flow,'change',side_effect=fail):v=self.run_turn()
        self.assertEqual(v['turn']['state'],'FAILED');self.assertEqual(SyntheticBackend.closed,1);self.assertEqual(SyntheticBackend.calls,[])
    def test_closed_service_refuses_new_inference(self):
        self.ai.shutdown()
        with self.assertRaises(Blocked):self.ai.start(self.data())
    def test_shutdown_also_cancels_owned_catalog_transport(self):
        entered=threading.Event();cancelled=threading.Event()
        class Catalog(SyntheticBackend):
            def catalog(self):
                entered.set()
                for _ in range(100):
                    if self.cancelled():cancelled.set();raise Blocked('Synthetic catalogue cancelled')
                    time.sleep(.01)
                return super().catalog()
        self.ai.backend=Catalog
        def run():
            try:self.ai.catalog()
            except Blocked:pass
        thread=threading.Thread(target=run);thread.start();self.assertTrue(entered.wait(2));self.ai.shutdown();thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(cancelled.is_set())
        with self.assertRaises(Blocked):self.ai.catalog()
    def test_model_reply_cannot_smuggle_credentials_or_permissions(self):
        for value in [dict(reply='ok',plan=None,authorization=True),dict(reply='ok',plan={'endpoint':'unsafe'}),dict(reply='',plan=None)]:
            with self.subTest(value=value),self.assertRaises((Invalid,Blocked)):decode_reply(json.dumps(value),self.choices)
    def test_plain_nonjson_reply_never_becomes_canvas(self):
        with self.assertRaises(Invalid):decode_reply('普通回答而非接口格式',self.choices)
    def test_compact_plan_expands_only_registered_local_defaults(self):
        plan=dict(version=1,name='compact',summary='compact',nodes=[dict(id='s',kind='start',name='开始'),dict(id='a',kind='agent',name='分析',profile='default-codex',round_prompt='分析问题'),dict(id='r',kind='result',name='结果')],edges=[dict(source='s',target='a'),dict(source='a',target='r')])
        result,graph=decode_reply(json.dumps(dict(reply='已设计',plan=plan)),self.choices);self.assertEqual(graph['nodes'][1]['model'],self.rt.operator['profiles']['default-codex']['model']);self.assertEqual(graph['nodes'][1]['mode'],'Ask');self.assertEqual(result['plan']['nodes'][0]['profile'],'')
        plan['nodes'][1]['permission']='workspace-write'
        with self.assertRaises(Invalid):expand_plan(plan,self.choices)
    def test_missing_entry_and_result_scaffolding_is_local_and_not_new_agent(self):
        plan=dict(version=1,name='compact',summary='compact',nodes=[dict(id='a',kind='agent',name='分析',profile='default-codex',round_prompt='分析')],edges=[])
        _,graph=decode_reply(json.dumps(dict(reply='已设计',plan=plan)),self.choices)
        self.assertEqual([n['kind'] for n in graph['nodes']].count('agent'),1);self.assertEqual({n['kind'] for n in graph['nodes']},{'start','agent','result'});self.assertEqual(len(graph['edges']),2)
    def test_scaffolding_never_repairs_cycle_or_allows_extra_permissions(self):
        plan=dict(version=1,name='bad',summary='bad',nodes=[dict(id='a',kind='agent',name='分析',profile='default-codex')],edges=[dict(source='a',target='a')])
        with self.assertRaises(Invalid):decode_reply(json.dumps(dict(reply='bad',plan=plan)),self.choices)

class CopilotProtocolTests(unittest.TestCase):
    def test_burst_stream_backpressure_keeps_final_rpc_response(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'copilot';executable.write_text('''#!/usr/bin/python3
import json,sys
def send(d):
 b=json.dumps(d).encode();sys.stdout.buffer.write(f'Content-Length: {len(b)}\\r\\n\\r\\n'.encode()+b);sys.stdout.buffer.flush()
while True:
 h=sys.stdin.buffer.readline()
 if not h:break
 n=int(h.split(b':')[1]);sys.stdin.buffer.readline();d=json.loads(sys.stdin.buffer.read(n))
 if d['method']=='connect':result={'ok':True,'protocolVersion':3,'version':'synthetic'}
 else:
  for _ in range(1600):send({'jsonrpc':'2.0','method':'session.event','params':{'sessionId':'s','event':{'type':'assistant.message_delta','data':{'deltaContent':'x'}}}})
  result={'completed':True}
 send({'jsonrpc':'2.0','id':d['id'],'result':result})
''');executable.chmod(0o700)
            with patch('studio.copilot_client.shutil.which',return_value=str(executable)):
                with CopilotRPC(root,timeout=3,notify=lambda _:time.sleep(.0003)) as client:
                    self.assertEqual(client.request('flood',{}),{'completed':True})
    def test_unknown_model_never_creates_session_or_falls_back(self):
        client=CopilotRPC('.')
        with patch.object(client,'catalog',return_value={'models':[{'id':'auto'}]}),patch.object(client,'request') as request:
            with self.assertRaises(Blocked):client.chat('unavailable','hello')
        request.assert_not_called()
    def test_chat_disables_native_tools_and_configuration_side_effects(self):
        client=CopilotRPC('.');client.version='synthetic';client.events.extend([dict(sessionId='s',event=dict(type='assistant.message',data=dict(content='visible answer'))),dict(sessionId='s',event=dict(type='session.idle',data={}))])
        with patch.object(client,'catalog',return_value={'models':[{'id':'auto'}]}),patch.object(client,'request',side_effect=[{'sessionId':'s'},{}]) as request:
            result=client.chat('auto','hello')
        params=request.call_args_list[0].args[1];self.assertEqual(params['availableTools'],[])
        for k in ['requestPermission','requestUserInput','enableConfigDiscovery','enableFileHooks','enableSkills','enableHostGitOperations']:self.assertFalse(params[k])
        self.assertEqual(result['resolved_model'],'unknown');self.assertFalse(result['tools_enabled'])
    def test_reverse_tool_callback_is_rejected_not_executed(self):
        client=CopilotRPC('.');client.deadline=time.monotonic()+1;client.messages.put(dict(method='tool.call',id=5,params={'command':'must never run'}))
        with patch.object(client,'_send') as send:
            with self.assertRaises(Blocked):client._next()
        self.assertEqual(send.call_args.args[0]['error']['code'],-32601)
    def test_missing_auth_does_not_query_catalog(self):
        client=CopilotRPC('.')
        with patch.object(client,'request',return_value={'isAuthenticated':False}) as request:
            with self.assertRaises(Blocked):client.catalog()
        self.assertEqual(request.call_count,1)
    def test_metadata_exception_reaps_real_owned_transport_group(self):
        with tempfile.TemporaryDirectory() as root:
            executable=Path(root)/'copilot';executable.write_text('''#!/usr/bin/python3
import json,sys,os
while True:
 h=sys.stdin.buffer.readline()
 if not h:break
 n=int(h.split(b':')[1]);sys.stdin.buffer.readline();d=json.loads(sys.stdin.buffer.read(n))
 r={'jsonrpc':'2.0','id':d['id'],'result':{'ok':True,'protocolVersion':3,'version':'synthetic'}}
 b=json.dumps(r).encode();sys.stdout.buffer.write(f'Content-Length: {len(b)}\\r\\n\\r\\n'.encode()+b);sys.stdout.buffer.flush()
''');executable.chmod(0o700)
            with patch('studio.copilot_client.shutil.which',return_value=str(executable)):
                client=CopilotRPC(root,timeout=5)
                with self.assertRaises(sqlite3.OperationalError):
                    with client:
                        from orchestration.store import process_identity
                        identity=process_identity(client.process.pid)
                        raise sqlite3.OperationalError('After real Popen, before metadata publish')
                self.assertIsNotNone(client.process.poll())
                from orchestration.process import group_alive
                self.assertFalse(group_alive(identity))

if __name__=='__main__':unittest.main()
