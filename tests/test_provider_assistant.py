"""DevFlower-owned direct API Agent and Copilot workflow boundary regressions.

All servers/receipts here are explicit synthetic fixtures. Real evidence is
recorded outside the source tree.
"""
import copy,json,os,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).parent)]
from studio.devflower_agent import AgentConnection,DevFlowerAgent,AGENT_ID,canvas_tools
from studio.copilot_adapter import CopilotAdapter
from studio.agent_sessions import conversation
from studio.session_sources import scan
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked,digest

class DirectAgentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.rt=SimpleNamespace(assistant_settings=Path(self.tmp.name)/'settings',storage=Path(self.tmp.name),operator={'profiles':{}},check_operator=lambda:None)
  self.requests=[];self.reply=dict(id='message-synthetic',model='synthetic-model',content=[dict(type='text',text='助手自己的回复')],usage={'input_tokens':2,'output_tokens':3});self.status=200;owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):self.answer({'data':[{'id':'synthetic-model'}]})
   def do_POST(self):
    data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));owner.requests.append((self.path,data,dict(self.headers)))
    if getattr(owner,'delay',False):time.sleep(.8)
    self.answer(owner.reply)
   def answer(self,v):
    try:
     raw=json.dumps(v).encode();self.send_response(owner.status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    except (BrokenPipeError,ConnectionResetError):pass
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
  self.cfg=AgentConnection(self.rt);self.data=dict(expected=None,protocol='anthropic',base_url='http://127.0.0.1:'+str(self.server.server_port),model='synthetic-model',effort='high',api_key='PRIVATE_FIXTURE_SECRET');self.cfg.save(self.data)
 def call(self):
  with DevFlowerAgent(self.rt) as agent:
   agent.conversation='own-conversation';return agent.chat('synthetic-model','visible context')
 def test_own_agent_without_any_coding_cli(self):
  with patch('subprocess.Popen',side_effect=AssertionError('CLI must never be spawned')):r=self.call()
  self.assertEqual(r['agent_id'],AGENT_ID);self.assertEqual(r['provider'],'devflower-agent');self.assertIsNone(r['native_session_id']);self.assertEqual(r['session_id'],'own-conversation');self.assertFalse(r['transport']['coding_cli'])
 def test_dedicated_system_and_closed_canvas_tools(self):
  self.call();_,d,_=self.requests[0];self.assertIn('DevFlower 专属助手',d['system']);self.assertEqual([t['name'] for t in d['tools']],['propose_workflow','request_workflow_run','search_public_source','read_public_source']);self.assertEqual(d['messages'][0]['content'],'visible context');self.assertEqual(d['output_config'],{'effort':'high'})
 def test_tool_call_becomes_proposal_data_not_execution(self):
  plan={'version':1,'name':'draft','summary':'draft','nodes':[],'edges':[]};self.reply['content']=[dict(type='tool_use',name='propose_workflow',input={'reply':'草稿','plan':plan})]
  r=self.call();self.assertEqual(json.loads(r['text'])['plan'],plan);self.assertEqual(list(self.rt.storage.glob('*.sqlite')),[])
 def test_unprovided_tool_rejected(self):
  self.reply['content']=[dict(type='tool_use',name='run_shell',input={})]
  with self.assertRaises(Blocked):self.call()
 def test_multiple_tools_rejected(self):
  self.reply['content']=[dict(type='tool_use',name='propose_workflow',input={})]*2
  with self.assertRaises(Blocked):self.call()
 def test_malformed_tool_arguments_rejected(self):
  self.reply['content']=[dict(type='tool_use',name='propose_workflow',input={'command':'bad'})]
  with self.assertRaises(Blocked):self.call()
 def test_catalog_no_inference_and_separate_source(self):
  with DevFlowerAgent(self.rt) as agent:r=agent.catalog()
  self.assertEqual(self.requests,[]);self.assertEqual(r['inference_calls'],0);self.assertEqual(r['provider'],'model-api')
 def test_credentials_never_in_public_config_or_receipt(self):
  self.reply['content'][0]['text']='PRIVATE_FIXTURE_SECRET';r=self.call()
  self.assertNotIn('PRIVATE_FIXTURE_SECRET',json.dumps(self.cfg.public()));self.assertNotIn('PRIVATE_FIXTURE_SECRET',json.dumps(r));self.assertEqual(self.requests[0][2]['x-api-key'],'PRIVATE_FIXTURE_SECRET');self.assertEqual(self.cfg.path.stat().st_mode&0o777,0o600)
 def test_config_compare_and_swap_rejects_stale_update(self):
  fp=self.cfg.public()['fingerprint'];self.cfg.save(dict(self.data,expected=fp,model='second'))
  with self.assertRaises(Conflict):self.cfg.save(dict(self.data,expected=fp,model='third'))
 def test_blank_key_preserves_existing_without_returning_it(self):
  public=self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],api_key=''))
  self.assertNotIn('api_key',public);self.assertEqual(self.cfg.read()['api_key'],'PRIVATE_FIXTURE_SECRET')
 def test_remote_plaintext_and_embedded_credentials_rejected(self):
  for url in ('http://example.com','https://user:password@example.com','https://example.com/?api_key=x'):
   with self.subTest(url=url),self.assertRaises(Invalid):self.cfg.validate(dict(version=1,protocol='anthropic',base_url=url,model='m',effort='high'))
 def test_changing_model_server_never_forwards_old_credential(self):
  with self.assertRaisesRegex(Invalid,'单独填写密钥'):
   self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],base_url='https://different.example',api_key=''))
  self.assertEqual(self.cfg.read()['api_key'],'PRIVATE_FIXTURE_SECRET')
 def test_unknown_protocol_and_fields_rejected(self):
  with self.assertRaises(Invalid):self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],command='copilot'))
  with self.assertRaises(Invalid):self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],protocol='coding-cli'))
 def test_remote_failure_has_no_implicit_retry(self):
  self.status=402
  with self.assertRaisesRegex(Blocked,'402'):self.call()
  self.assertEqual(len(self.requests),1)
 def test_cancel_closes_pending_response_without_replay(self):
  self.delay=True;cancel=threading.Event();errors=[]
  def work():
   try:
    with DevFlowerAgent(self.rt,timeout=3,cancelled=cancel.is_set) as agent:agent.chat('synthetic-model','context')
   except Blocked as exc:errors.append(str(exc))
  thread=threading.Thread(target=work);thread.start()
  for _ in range(100):
   if self.requests:break
   time.sleep(.01)
  cancel.set();thread.join(1);self.assertFalse(thread.is_alive());self.assertTrue(errors);self.assertEqual(len(self.requests),1)
 def test_openai_compatible_direct_model_backend(self):
  self.cfg.save(dict(self.data,expected=self.cfg.public()['fingerprint'],protocol='openai'));self.reply=dict(id='synthetic',model='synthetic-model',choices=[dict(message=dict(content='direct answer'))])
  r=self.call();self.assertEqual(json.loads(r['text'])['reply'],'direct answer');path,data,_=self.requests[0];self.assertEqual(path,'/v1/chat/completions');self.assertEqual(data['tools'][0]['function']['name'],'propose_workflow')
 def test_unconfigured_assistant_can_open_settings(self):
  self.cfg.path.unlink();self.assertFalse(self.cfg.public()['configured'])

class CopilotNodeTests(unittest.TestCase):
 def setUp(self):
  self.sid=str(uuid.uuid4());self.b=dict(provider='copilot',model='auto',effort='default',role='reviewer',tools=[],execution_mode='Ask',session_id=None,project='synthetic',home='/synthetic')
  self.adapter=CopilotAdapter(self.b,dict(step_timeout_seconds=30,max_log_bytes=2000000));self.process=dict(exit=0,timeout=False,cancelled=False,overflow=False)
 def test_adapter_dispatches_as_workflow_provider(self):
  from orchestration.adapters import CLI
  with patch('studio.copilot_adapter.CopilotAdapter.start',return_value={'provider':'copilot'}) as call:
   self.assertEqual(CLI('copilot',self.b,{}).start('task','/cwd','/artifacts')['provider'],'copilot');self.assertEqual(call.call_count,1)
 def test_native_identity_and_output_belong_to_copilot(self):
  def transport(b,cwd,artifacts,request,**kw):
   self.assertFalse(request['resume']);return dict(session_id=request['session_id'],report={'answer':'synthetic'},resolved_model='synthetic',provider_prompt_sha256=digest(request['prompt'].encode())),self.process
  with patch('studio.copilot_adapter.invoke',side_effect=transport):r=self.adapter.start('task','/cwd','/artifacts')
  self.assertEqual(r['provider'],'copilot');self.assertEqual(r['permission_mode'],'external-bwrap/context-only/no-native-tools')
 def test_visible_request_includes_actual_schema_instruction(self):
  with tempfile.TemporaryDirectory() as folder:
   schema=Path(folder)/'schema.json';schema.write_text('{"type":"object"}')
   def transport(b,cwd,artifacts,request,**kw):
    self.assertIn('JSON Schema',request['prompt']);self.assertIn('{"type": "object"}',request['prompt'])
    from orchestration.common import digest
    return dict(session_id=request['session_id'],report={},provider_prompt_sha256=digest(request['prompt'].encode())),self.process
   with patch('studio.copilot_adapter.invoke',side_effect=transport):self.adapter.start('task','/cwd','/artifacts',schema)
 def test_resume_keeps_explicit_uuid(self):
  def transport(b,cwd,artifacts,request,**kw):
   self.assertTrue(request['resume']);self.assertEqual(request['session_id'],self.sid);return dict(session_id=self.sid,report={},provider_prompt_sha256=digest(request['prompt'].encode())),self.process
  with patch('studio.copilot_adapter.invoke',side_effect=transport):self.assertEqual(self.adapter.start('task','/cwd','/artifacts',session=self.sid)['session_id'],self.sid)
 def test_wrong_native_session_refused(self):
  with patch('studio.copilot_adapter.invoke',return_value=(dict(session_id=str(uuid.uuid4()),report={}),self.process)),self.assertRaises(Blocked):self.adapter.start('task','/cwd','/artifacts',session=self.sid)
 def test_write_tools_agent_mode_and_effort_are_fail_closed(self):
  for change in ({'role':'implementer'},{'tools':['Bash']},{'execution_mode':'Agent'},{'effort':'high'}):
   with self.subTest(change=change),patch('studio.copilot_adapter.invoke') as call,self.assertRaises(Blocked):
    CopilotAdapter(dict(self.b,**change),self.adapter.limits).start('task','/cwd','/artifacts')
   self.assertFalse(call.called)
 def test_native_history_is_readable_and_hidden_content_excluded(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);p=root/self.sid/'events.jsonl';p.parent.mkdir();rows=[dict(type='session.start',timestamp='2026-10-26T00:00:00Z',data=dict(sessionId=self.sid,copilotVersion='1.0.85',context=dict(cwd=folder))),dict(type='user.message',data=dict(content='visible question')),dict(type='assistant.reasoning',data=dict(content='HIDDEN')),dict(type='assistant.message',data=dict(content='visible answer'))];p.write_text('\n'.join(map(json.dumps,rows))+'\n')
   sessions,coverage=scan(root,'copilot',include_paths=True);self.assertEqual(coverage['accepted'],1);self.assertEqual(sessions[0]['session_id'],self.sid)
   visible=conversation('copilot',p);self.assertEqual([v['text'] for v in visible],['visible question','visible answer'])
   p.rename(p.with_name('wrong.jsonl'));self.assertEqual(scan(root,'copilot')[0],[])
 def test_node_binding_rejects_forged_copilot_write(self):
  from test_workbench_runtime import fixture
  broker=patch('studio.copilot_adapter.credential',return_value='synthetic-auth');broker.start();self.addCleanup(broker.stop)
  base,rt=fixture(self);rt.operator['profiles']['copilot']=dict(rt.operator['profiles']['default-codex'],provider='copilot',model='auto',effort='default')
  for change in ({'mode':'Agent'},{'tools':['Read']},{'mode':'Ask','permission':'workspace-write'}):
   with self.subTest(change=change),self.assertRaises((Blocked,Invalid)):rt.node_binding(base.project['id'],dict(profile='copilot',**change),admission=False)
  result=rt.node_binding(base.project['id'],dict(profile='copilot',mode='Plan'),admission=False);self.assertEqual(result['binding']['tools'],[])

class CredentialBrokerTests(unittest.TestCase):
 def test_absent_home_never_uses_source_and_caller_token_not_inherited(self):
  from studio.copilot_adapter import credential
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);config=root/'hosts.yml';config.write_text('synthetic configuration');exe=root/'gh';exe.write_text('synthetic executable')
   profile={'auth':dict(kind='github-cli',executable=str(exe),config_file=str(config))}
   def run(argv,**kw):
    self.assertEqual(argv,[str(exe),'auth','token','--hostname','github.com']);self.assertNotEqual(kw['cwd'],folder);self.assertEqual(kw['env']['HOME'],kw['cwd']);self.assertTrue(kw['env']['XDG_STATE_HOME'].startswith(kw['cwd']+'/'));self.assertNotIn('GH_TOKEN',kw['env']);self.assertNotIn('GITHUB_TOKEN',kw['env']);self.assertEqual(kw['env']['GH_CONFIG_DIR'],folder)
    self.saved=kw['cwd'];return SimpleNamespace(returncode=0,stdout=b'SYNTHETIC_ONLY_TOKEN')
   with patch.dict(os.environ,{'GH_TOKEN':'WRONG_CALLER_CREDENTIAL'},clear=True),patch('studio.copilot_adapter.subprocess.run',side_effect=run):self.assertEqual(credential(profile),'SYNTHETIC_ONLY_TOKEN')
   self.assertFalse(Path(self.saved).exists());self.assertFalse((root/'.local').exists())
 def test_invalid_broker_descriptor_and_failure_are_closed(self):
  from studio.copilot_adapter import credential
  with self.assertRaises(Blocked):credential({'auth':{'kind':'shell','command':'anything'}})
