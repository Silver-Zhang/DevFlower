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
    def __init__(self,cwd,**kw):self.cwd=cwd;self.cancelled=kw.get('cancelled',lambda:False);self.notify=kw.get('notify',lambda e:None);self.process=type('P',(),{'pid':os.getpid()})()
    def __enter__(self):type(self).entered+=1;return self
    def __exit__(self,*a):type(self).closed+=1
    def catalog(self):return dict(provider='devflower-agent',source='SYNTHETIC catalog; not native inference',models=[dict(id='auto',name='Synthetic Auto')],observed_at=time.time(),inference_calls=0,execution_verified=False)
    def chat(self,model,prompt):
        type(self).calls.append(json.loads(prompt.split('\n',1)[1]));end=time.monotonic()+type(self).delay
        while time.monotonic()<end:
            if self.cancelled():raise Blocked('Synthetic cancel')
            time.sleep(.01)
        ctx=type(self).calls[-1]
        if ctx['conversation'][-1]['text']=='SYNTHETIC-TOOL-ERROR-UI':
            self.notify(dict(type='assistant.tool_start',name='read_public_source'))
            self.notify(dict(type='assistant.tool_error',name='read_public_source',error='公开源码工具参数无效或为空。'))
        if ctx['conversation'][-1]['text']=='SYNTHETIC-STREAM-UI':
            self.notify(dict(type='assistant.message_delta',text='正在检查工作流配置。'))
            until=time.monotonic()+4
            while time.monotonic()<until:
                if self.cancelled():raise Blocked('Synthetic cancel')
                time.sleep(.01)
        if ctx.get('intent')=='chat':return dict(text=json.dumps(dict(reply='SYNTHETIC 普通解释，没有修改画布',plan=None)),public_summaries=[dict(source='provider-public-summary',text='SYNTHETIC 公开依据')])
        name='Synthetic '+ctx['conversation'][-1]['text'][:40];profile,choice=next(iter(ctx['available_agents'].items()));m=choice['models'][0]
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
    def test_context_separates_readonly_metadata_from_proposal_nodes(self):
        node=next(n for n in self.g['nodes'] if n['kind']=='agent')
        node['input_contract']=[dict(field='answer',type='string',required=True)]
        result=self.run_turn(self.data(graph=self.g))
        self.assertEqual(result['turn']['state'],'COMPLETED')
        context=SyntheticBackend.calls[-1]['current_graph']
        visible=next(n for n in context['nodes'] if n['id']==node['id'])
        self.assertFalse({'provider','responsibility','input_contract','output_contract'}&set(visible))
        metadata=next(n for n in context['readonly_metadata']['nodes'] if n['id']==node['id'])
        self.assertEqual(metadata['provider'],node['provider'])
        self.assertEqual(metadata['input_contract'],node['input_contract'])
    def test_external_proposal_and_apply_keep_existing_data_contracts(self):
        node=next(n for n in self.g['nodes'] if n['id']=='analyze')
        node['input_contract']=[dict(field='answer',type='string',required=True)]
        node['output_contract']=[dict(field='result.score',type='number',required=True)]
        result=self.run_turn(self.data(graph=self.g));self.assertEqual(result['turn']['state'],'COMPLETED')
        proposal=result['turn']['proposal']
        applied=ConfigurationAssistant(self.rt).apply(dict(workspace=self.w,id=proposal['id'],graph=self.g,answer={'action':'apply-proposal'}))
        current=next(n for n in applied['graph']['nodes'] if n['id']=='analyze')
        from studio.data_contracts import check
        for key in ('input_contract','output_contract'):
            self.assertEqual(current[key],node[key])
            with self.assertRaises(Invalid):check({},current[key])
    def test_explicit_correction_sees_rejected_plan_without_automatic_retry(self):
        rejected=json.dumps(dict(reply='未通过的提案',plan=dict(version=1,name='invalid',summary='invalid',nodes=[],edges=[])))
        with patch.object(SyntheticBackend,'chat',return_value=dict(text=rejected)):
            v=self.run_turn()
        self.assertEqual(v['turn']['state'],'FAILED');self.assertEqual(v['turn']['model_response'],rejected)
        self.assertEqual(SyntheticBackend.calls,[])
        corrected=self.run_turn(self.data(conversation=v['id'],expected=v['revision'],message='请修正上次被拒绝的方案'))
        self.assertEqual(corrected['turn']['state'],'COMPLETED');self.assertEqual(len(SyntheticBackend.calls),1)
        previous=SyntheticBackend.calls[0]['previous_rejected_proposal']
        self.assertEqual(previous['response'],rejected);self.assertIs(previous['authority'],False)

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
    def test_late_cancel_cannot_overwrite_completed_turn(self):
        v=self.run_turn();stale=copy.deepcopy(v);stale['turn']['state']='RUNNING'
        actual=self.ai.conversation
        with patch.object(self.ai,'conversation',side_effect=[stale,actual(self.w,v['id'])]):
            self.ai.cancel(self.w,v['id'])
        self.assertEqual(self.ai.conversation(self.w,v['id'])['turn']['state'],'COMPLETED')
        self.assertEqual(len(SyntheticBackend.calls),1)
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

class AssistantToolEnvelopeTests(unittest.TestCase):
    """Synthetic API replies through the real Agent, finalizer and private SQLite."""
    envelope='<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="read_public_source">\n<｜｜DSML｜｜ parameter name="path" string="true">tools/studio/workbench_graph.py</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'

    def scenario(self,protocol,text,*,source_first=False,proposal=False):
        from types import SimpleNamespace
        from studio.devflower_agent import DevFlowerAgent
        from studio.workbench_store import WorkbenchStore
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);flow=WorkbenchStore(root/'private.sqlite')
            graph=dict(version=3,name='Private protocol fixture',nodes=[],edges=[])
            rt=SimpleNamespace(storage=root,operator=dict(profiles={}),flow=flow,db=flow,check_operator=lambda:None,
                session_center=SimpleNamespace(workspace=lambda _:dict(id='W',path=str(root))))
            choices={'fixture-codex':dict(provider='codex',models=[dict(id='SYNTHETIC',efforts=['medium'])])}
            plan=dict(version=1,name='未执行草稿',summary='仅合成传输',nodes=[dict(id='a',kind='agent',name='分析',profile='fixture-codex',round_prompt='只读分析')],edges=[])
            requests=[];source_calls=[]
            args=dict(path='tools/studio/workbench_graph.py',start=60,end=60)
            class Reader:
                def __init__(self,*a,**kw):pass
                def read(self,*a):
                    source_calls.append(a)
                    return dict(path=args['path'],start=60,end=60,text='60: SYNTHETIC PUBLIC SOURCE')
                def search(self,*a):raise AssertionError('No undeclared source search')
            class Backend(DevFlowerAgent):
                def __init__(self,cwd,**kw):super().__init__(rt,cwd,**kw)
                def __enter__(self):
                    self.config=dict(protocol=protocol,base_url='http://127.0.0.1',model='SYNTHETIC',effort='medium',api_key='',fingerprint='private-fixture')
                    return self
                def _request(self,method,path,payload=None):
                    requests.append((method,path,copy.deepcopy(payload)))
                    if len(requests)>(2 if source_first else 1):raise AssertionError('No automatic protocol retry')
                    source=source_first and len(requests)==1
                    name='read_public_source' if source else 'propose_workflow'
                    inputs=args if source else dict(reply='仅生成未执行草稿',plan=plan)
                    structured=source or proposal
                    if protocol=='anthropic':
                        content=[dict(type='text',text=text)]
                        if structured:content.append(dict(type='tool_use',id='fixture-tool',name=name,input=inputs))
                        return dict(id='fixture-response',model='SYNTHETIC',content=content,usage={}),None
                    message=dict(role='assistant',content=text)
                    if structured:message['tool_calls']=[dict(id='fixture-tool',type='function',function=dict(name=name,arguments=json.dumps(inputs)))]
                    return dict(id='fixture-response',model='SYNTHETIC',choices=[dict(message=message)],usage={}),None
            manager=CanvasAssistant(rt);manager.backend=Backend
            messages=[dict(role='user',text='解释并修复当前错误')]
            flow.put('canvas-conversation','C',dict(id='C',workspace='W',messages=messages,revision=1,agent_id='devflower-assistant/v1',active_turn='T'))
            flow.put('canvas-turn','T',dict(id='T',state='QUEUED',workspace='W',conversation='C',intent='workflow',source=dict(kind='canvas-only',root=str(root)),choices=choices,context=graph,messages=messages,model='SYNTHETIC',images=[],base_graph=graph,base_graph_sha256='private-fixture',proposal=None,reply=None,receipt=None))
            try:
                with patch('studio.public_source_tool.PublicSourceReader',Reader),patch.object(ConfigurationAssistant,'choices',return_value=choices):
                    manager._run('T',threading.Event())
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline:
                        with manager.lock:active=bool(manager.active)
                        if not active:break
                        time.sleep(.01)
                    self.assertFalse(active,'Private finalizer did not finish')
                # A fresh store reads committed state, not an in-memory projection.
                persisted=WorkbenchStore(flow.path);turn=persisted.get('canvas-turn','T');conversation=persisted.get('canvas-conversation','C')
                self.assertEqual(persisted.list('run'),[])
                self.assertEqual(source_calls,[(args['path'],60,60)] if source_first else [])
                self.assertEqual(len(requests),2 if source_first else 1)
                self.assertEqual([r[1] for r in requests],['/v1/messages' if protocol=='anthropic' else '/v1/chat/completions']*len(requests))
                for _,_,payload in requests:
                    self.assertEqual(payload['model'],'SYNTHETIC')
                    self.assertEqual(payload.get('output_config',{}).get('effort') if protocol=='anthropic' else payload['reasoning_effort'],'medium')
                if source_first:self.assertEqual([v['name'] for v in turn['tool_activity']],['read_public_source','read_public_source'])
                return turn,conversation
            finally:manager.shutdown()

    def failed_envelope(self,protocol,source_first=False):
        turn,conversation=self.scenario(protocol,' \n'+self.envelope+'\n ',source_first=source_first)
        self.assertEqual(turn['state'],'FAILED')
        for text in ('协议','未执行','未修改画布','tool_use','tool_calls','请'):
            self.assertIn(text,turn['reason'])
        self.assertIsNone(turn['proposal']);self.assertIsNone(turn['reply']);self.assertIsNone(turn['receipt'])
        self.assertEqual([m['role'] for m in conversation['messages']],['user'])
        self.assertEqual(conversation['revision'],1)

    def test_anthropic_initial_envelope_is_persisted_as_failed(self):self.failed_envelope('anthropic')
    def test_openai_initial_envelope_is_persisted_as_failed(self):self.failed_envelope('openai')
    def test_anthropic_envelope_after_source_tool_is_persisted_as_failed(self):self.failed_envelope('anthropic',True)
    def test_openai_envelope_after_source_tool_is_persisted_as_failed(self):self.failed_envelope('openai',True)

    def test_plain_clarification_and_other_tags_remain_answers(self):
        for protocol in ('anthropic','openai'):
            for text in ('请先说明需要修复哪个节点。','<example>这是普通标签文本</example>'):
                with self.subTest(protocol=protocol,text=text):
                    turn,_=self.scenario(protocol,text)
                    self.assertEqual(turn['state'],'COMPLETED');self.assertEqual(turn['reply'],text);self.assertIsNone(turn['proposal'])

    def test_quoted_or_explained_envelopes_remain_answers(self):
        for protocol in ('anthropic','openai'):
            for text in ('```text\n'+self.envelope+'\n```','这是待解释的协议示例：\n'+self.envelope,self.envelope+'\n以上是协议示例，请勿执行。'):
                with self.subTest(protocol=protocol,text=text):
                    turn,_=self.scenario(protocol,text)
                    self.assertEqual(turn['state'],'COMPLETED');self.assertEqual(turn['reply'],text)

    def test_structured_source_tool_then_plain_answer_remains_supported(self):
        for protocol in ('anthropic','openai'):
            with self.subTest(protocol=protocol):
                turn,_=self.scenario(protocol,'根据公开源码，字段应使用 result.accepted。',source_first=True)
                self.assertEqual(turn['state'],'COMPLETED');self.assertIsNone(turn['proposal'])

    def test_structured_source_tool_then_proposal_remains_supported(self):
        for protocol in ('anthropic','openai'):
            with self.subTest(protocol=protocol):
                turn,conversation=self.scenario(protocol,self.envelope,source_first=True,proposal=True)
                self.assertEqual(turn['state'],'COMPLETED',turn.get('reason'));self.assertTrue(turn['proposal']['graph'])
                self.assertEqual(conversation['messages'][-1]['kind'],'proposal')
                self.assertEqual(turn['receipt']['inference_calls'],2)


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

class InspectionHandoffTests(unittest.TestCase):
    setUp=CanvasAssistantTests.setUp
    data=CanvasAssistantTests.data
    wait=CanvasAssistantTests.wait
    run_turn=CanvasAssistantTests.run_turn
    def test_inspection_is_bound_and_rechecked_with_full_locations(self):
        from studio.workflow_inspection import inspect_workflow
        graph=copy.deepcopy(self.g);edge=graph['edges'][0];edge['field']='example'
        task='diagnose';checked=inspect_workflow(self.rt,graph,task,workspace_path=self.base.project['path'],workspace_id=self.w)
        attachment=dict(workspace=self.w,graph=copy.deepcopy(graph),task=task,issues=checked['issues'])
        data=self.data(graph=graph,intent='chat',inspection=attachment,message='请分析检查结果')
        result=self.run_turn(data);self.assertEqual(result['turn']['state'],'COMPLETED')
        received=SyntheticBackend.calls[-1]['conversation'][-1]['inspection'];self.assertEqual(received['issues'],checked['issues']);self.assertEqual(received['workspace'],self.w)
        self.assertIn('graph_sha256',received);self.assertIn('task_sha256',received)
        for key in ('total','error_count','warning_count','omitted_count','truncated'):self.assertEqual(received[key],checked[key])
        self.assertEqual(SyntheticBackend.calls[-1]['current_graph']['edges'][0]['id'],edge['id'])
        bad=self.data(graph=graph,intent='chat',inspection=dict(attachment,workspace='different'))
        with self.assertRaises(Exception):self.ai.start(bad)
        bad=self.data(graph=graph,intent='chat',inspection=dict(attachment,issues=[]))
        with self.assertRaises(Exception):self.ai.start(bad)
        changed=copy.deepcopy(graph);changed['name']='another';bad=self.data(graph=changed,intent='chat',inspection=attachment)
        with self.assertRaises(Exception):self.ai.start(bad)

    def test_truncated_inspection_counts_reach_assistant(self):
        from studio.workflow_inspection import inspect_workflow
        graph=dict(version=3,name='repro',nodes=[dict(id=f'bad{i}',name=f'bad{i}',kind='agent') for i in range(64)],edges=[])
        task='diagnose';checked=inspect_workflow(self.rt,graph,task,workspace_path=self.base.project['path'],workspace_id=self.w)
        attachment=dict(workspace=self.w,graph=graph,task=task,issues=checked['issues'])
        result=self.run_turn(self.data(graph=graph,intent='chat',inspection=attachment,message='请分批修复并重查'))
        self.assertEqual(result['turn']['state'],'COMPLETED')
        received=SyntheticBackend.calls[-1]['conversation'][-1]['inspection']
        self.assertEqual((received['total'],received['error_count'],received['warning_count'],received['omitted_count'],received['truncated']),(451,449,2,195,True))
        self.assertEqual(len(received['issues']),256)

    def test_invalid_checked_graph_can_be_explained_and_proposal_uses_cas(self):
        from studio.workflow_inspection import inspect_workflow
        from studio.server import Conflict
        graph=copy.deepcopy(self.g);node=next(n for n in graph['nodes'] if n['kind']=='agent');node['mode']='unsupported'
        task='diagnose';issues=inspect_workflow(self.rt,graph,task,workspace_path=self.base.project['path'],workspace_id=self.w)['issues']
        self.assertTrue(any(i['node']==node['id'] and i['field']=='mode' for i in issues))
        data=self.data(graph=graph,inspection=dict(workspace=self.w,graph=copy.deepcopy(graph),task=task,issues=issues),message='请修复检查结果')
        started=self.ai.start(data)
        for _ in range(500):
            turn=self.rt.flow.get('canvas-turn',data['request_id'])
            if turn['state'] in ('COMPLETED','FAILED','CANCELLED'):break
            time.sleep(.01)
        self.assertEqual(turn['state'],'COMPLETED',turn.get('reason'))
        proposal=turn['proposal'];self.assertTrue(proposal)
        altered=copy.deepcopy(graph);altered['name']='edited after check'
        with self.assertRaises(Conflict):ConfigurationAssistant(self.rt).apply(dict(workspace=self.w,id=proposal['id'],graph=altered,answer={'action':'apply-proposal'}))
        applied=ConfigurationAssistant(self.rt).apply(dict(workspace=self.w,id=proposal['id'],graph=graph,answer={'action':'apply-proposal'}))
        self.assertFalse(applied['execution_started'])

    def test_failed_diagnosis_keeps_public_evidence(self):
        evidence=dict(path='tools/studio/workbench_graph.py',start=1,end=1,text='1: public excerpt',candidate_sha256='synthetic')
        def failed(client,model,prompt):
            client.notify(dict(type='assistant.tool',name='read_public_source',path=evidence['path'],start=1,end=1,evidence=evidence))
            raise Blocked('模型在预算用尽后仍请求工具；请重新诊断')
        with patch.object(SyntheticBackend,'chat',failed):
            data=self.data(intent='chat',message='解释错误');self.ai.start(data)
            for _ in range(500):
                turn=self.rt.flow.get('canvas-turn',data['request_id'])
                if turn['state'] in ('FAILED','COMPLETED','CANCELLED'):break
                time.sleep(.01)
        self.assertEqual(turn['state'],'FAILED');self.assertEqual(turn['tool_activity'][-1]['evidence'],evidence);self.assertIn('预算',turn['reason'])

    def test_tool_failure_activity_survives_failed_turn_and_http_reload(self):
        from studio.workbench_server import create_server
        from urllib.request import Request,urlopen
        server,token=create_server(self.rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def failed(client,model,prompt):
            client.notify(dict(type='assistant.tool_start',name='read_public_source'))
            client.notify(dict(type='assistant.tool_error',name='read_public_source',error='参数无效 token=sk-'+'x'*32))
            raise Blocked('模型连接失败；未自动重试')
        with patch.object(SyntheticBackend,'chat',failed):
            data=self.data(intent='chat',message='解释错误');self.ai.start(data);result=self.wait(data['conversation'])
        self.assertEqual(result['turn']['state'],'FAILED');activity=result['turn']['tool_activity'][-1]
        self.assertEqual(activity['state'],'failed');self.assertEqual(activity['name'],'read_public_source');self.assertIn('参数无效',activity['error']);self.assertNotIn('sk-'+'x'*32,json.dumps(activity))
        url='http://127.0.0.1:'+str(server.server_port)+'/api/canvas-assistant/conversation?workspace='+self.w+'&id='+data['conversation']
        with urlopen(Request(url,headers={'Authorization':'Bearer '+token})) as response:remote=json.load(response)
        self.assertEqual(remote['turn']['tool_activity'][-1],activity);self.assertEqual(remote['turn']['state'],'FAILED')
