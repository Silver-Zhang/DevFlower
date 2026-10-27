"""Synthetic HTTP streams only, never a real Provider."""
import importlib.util,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio import devflower_agent as agent_module
from orchestration.common import Blocked

class StreamHTTP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.rt=SimpleNamespace(storage=Path(self.tmp.name),operator={'profiles':{}},check_operator=lambda:None)
  self.release=threading.Event();self.sent=threading.Event();self.request_seen=threading.Event();self.before_first=threading.Event();self.before_first.set();self.requests=[];self.break_stream=False;self.error_message=None;self.first_text='Visible early ';owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));owner.requests.append(request)
    if owner.error_message:
     raw=json.dumps({'error':{'message':owner.error_message}}).encode();self.send_response(402);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();owner.request_seen.set();owner.before_first.wait(3)
    def send(obj):self.wfile.write(('data: '+(obj if isinstance(obj,str) else json.dumps(obj))+'\n\n').encode());self.wfile.flush()
    try:
     if self.path.endswith('/messages'):
      send({'type':'message_start','message':{'id':'synthetic','model':'m','usage':{'input_tokens':1}}});send({'type':'content_block_start','index':0,'content_block':{'type':'thinking'}});send({'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'NEVER_VISIBLE'}});send({'type':'content_block_stop','index':0})
      send({'type':'content_block_start','index':1,'content_block':{'type':'text','text':''}});send({'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'Visible early '}});owner.sent.set();owner.release.wait(3)
      send({'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'PRIVATE_FIXTURE_SECRET '}});send({'type':'content_block_stop','index':1});send({'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':2}})
      if not owner.break_stream:send({'type':'message_stop'})
     else:
      send({'id':'synthetic','model':'m','choices':[{'delta':{'content':owner.first_text,'reasoning_content':'NEVER_VISIBLE'},'finish_reason':None}]});owner.sent.set();owner.release.wait(3)
      send({'choices':[{'delta':{'content':'PRIVATE_FIX'},'finish_reason':None}]});send({'choices':[{'delta':{'content':'TURE_SECRET '},'finish_reason':'stop'}]})
      if not owner.break_stream:send('[DONE]')
    except (BrokenPipeError,ConnectionResetError):pass
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown);self.addCleanup(self.release.set)
  agent_module.AgentConnection(self.rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
 def test_first_text_before_response_end_and_redaction(self):
  events=[];results=[];errors=[]
  def work():
   try:
    with agent_module.DevFlowerAgent(self.rt,timeout=5,notify=events.append) as a:results.append(a.chat('m','hello'))
   except Exception as e:errors.append(e)
  thread=threading.Thread(target=work);thread.start();self.assertTrue(self.sent.wait(2))
  for _ in range(100):
   if any(e.get('text')=='Visible early ' for e in events):break
   time.sleep(.01)
  self.assertTrue(any(e.get('text')=='Visible early ' for e in events));self.assertTrue(thread.is_alive());self.release.set();thread.join(3)
  self.assertFalse(errors);self.assertEqual(len(results),1);self.assertEqual(len(self.requests),1);self.assertTrue(self.requests[0]['stream']);self.assertNotIn('PRIVATE_FIXTURE_SECRET',str(events)+str(results));self.assertNotIn('NEVER_VISIBLE',str(events)+str(results))
 def test_truncated_stream_fails_without_retry(self):
  self.break_stream=True;self.release.set()
  with agent_module.DevFlowerAgent(self.rt,timeout=5) as a:
   with self.assertRaises(Blocked):a.chat('m','hello')
  self.assertEqual(len(self.requests),1)
 def test_cancel_closes_delayed_stream(self):
  cancelled=threading.Event();errors=[]
  def work():
   try:
    with agent_module.DevFlowerAgent(self.rt,timeout=5,cancelled=cancelled.is_set) as a:a.chat('m','hello')
   except Blocked as e:errors.append(str(e))
  thread=threading.Thread(target=work);thread.start();self.assertTrue(self.sent.wait(2));cancelled.set();thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(errors);self.assertEqual(len(self.requests),1)

 def test_provider_error_is_readable_and_redacted(self):
  self.error_message='Quota exhausted for PRIVATE_FIXTURE_SECRET'
  with agent_module.DevFlowerAgent(self.rt,timeout=5) as a:
   with self.assertRaises(Blocked) as error:a.chat('m','hello')
  self.assertIn('Quota exhausted',str(error.exception));self.assertNotIn('PRIVATE_FIXTURE_SECRET',str(error.exception));self.assertEqual(len(self.requests),1)

 def test_anthropic_visible_and_thinking_boundary(self):
  cfg=agent_module.AgentConnection(self.rt);v=cfg.public();cfg.save(dict(expected=v['fingerprint'],protocol='anthropic',base_url=v['base_url'],model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
  events=[];results=[]
  def work():
   with agent_module.DevFlowerAgent(self.rt,timeout=5,notify=events.append) as a:results.append(a.chat('m','hello'))
  thread=threading.Thread(target=work);thread.start();self.assertTrue(self.sent.wait(2))
  for _ in range(100):
   if any(e.get('text')=='Visible early ' for e in events):break
   time.sleep(.01)
  self.assertTrue(any(e.get('text')=='Visible early ' for e in events));self.assertTrue(thread.is_alive());self.release.set();thread.join(3)
  self.assertFalse(thread.is_alive());self.assertEqual(len(results),1);self.assertNotIn('NEVER_VISIBLE',str(results)+str(events));self.assertNotIn('PRIVATE_FIXTURE_SECRET',str(results)+str(events));self.assertEqual(len(self.requests),1)
 def test_canvas_persists_live_turn_for_http_reconnect_without_replay(self):
  import secrets
  from unittest.mock import patch
  from urllib.request import Request,urlopen
  from test_workbench_runtime import fixture
  from studio.canvas_assistant import CanvasAssistant
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  from studio.workbench_server import create_server
  self.first_text='正在检查工作流配置。';base,rt=fixture(self);workspace=base.project['id'];graph=examples(rt.operator['profiles'])[0]
  agent_module.AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
  assistant=CanvasAssistant(rt);self.addCleanup(assistant.shutdown)
  choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in rt.operator['profiles'].items()}
  key=secrets.token_hex(16)
  with patch.object(ConfigurationAssistant,'choices',return_value=choices),patch('studio.canvas_assistant.CanvasAssistant',return_value=assistant):
   server,token=create_server(rt);threading.Thread(target=server.serve_forever,daemon=True).start()
   try:
    started=assistant.start(dict(workspace=workspace,conversation=key,request_id=secrets.token_hex(16),expected=None,message='synthetic question',graph=graph,model='m',allow_context=True,intent='chat'))
    self.assertTrue(self.sent.wait(2));url='http://127.0.0.1:'+str(server.server_port)+'/api/canvas-assistant/conversation?workspace='+workspace+'&id='+key
    for _ in range(100):
     with urlopen(Request(url,headers={'Authorization':'Bearer '+token})) as response:live=json.load(response)
     if live['turn'].get('visible_text'):break
     time.sleep(.02)
    self.assertEqual(live['turn']['state'],'RUNNING');self.assertEqual(live['turn']['visible_text'],self.first_text);self.assertEqual(len(self.requests),1)
    with urlopen(Request(url,headers={'Authorization':'Bearer '+token})) as response:again=json.load(response)
    self.assertEqual(again['turn']['id'],started['turn']['id']);self.assertEqual(len(self.requests),1)
    self.release.set()
    for _ in range(100):
     done=assistant.conversation(workspace,key)
     if done['turn']['state'] not in ('QUEUED','RUNNING'):break
     time.sleep(.02)
    self.assertEqual(done['turn']['state'],'COMPLETED');self.assertEqual(len(self.requests),1);self.assertNotIn('PRIVATE_FIXTURE_SECRET',json.dumps(done));self.assertNotIn('NEVER_VISIBLE',json.dumps(done));self.assertIsNone(done['turn']['proposal'])
   finally:server.shutdown();server.server_close()
 def test_canvas_incomplete_stream_remains_failed_without_proposal_or_replay(self):
  import secrets
  from unittest.mock import patch
  from test_workbench_runtime import fixture
  from studio.canvas_assistant import CanvasAssistant
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  self.first_text='正在检查工作流配置。';base,rt=fixture(self);workspace=base.project['id'];graph=examples(rt.operator['profiles'])[0]
  agent_module.AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
  assistant=CanvasAssistant(rt);self.addCleanup(assistant.shutdown);self.break_stream=True;self.release.set()
  choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in rt.operator['profiles'].items()};key=secrets.token_hex(16)
  with patch.object(ConfigurationAssistant,'choices',return_value=choices):
   assistant.start(dict(workspace=workspace,conversation=key,request_id=secrets.token_hex(16),expected=None,message='synthetic question',graph=graph,model='m',allow_context=True,intent='chat'))
   for _ in range(200):
    value=assistant.conversation(workspace,key)
    if value['turn']['state'] not in ('QUEUED','RUNNING'):break
    time.sleep(.02)
  self.assertEqual(value['turn']['state'],'FAILED');self.assertIsNone(value['turn']['proposal']);self.assertEqual(len(self.requests),1);self.assertEqual(len(value['messages']),1);self.assertNotIn('PRIVATE_FIXTURE_SECRET',json.dumps(value));self.assertNotIn('NEVER_VISIBLE',json.dumps(value))
 def test_total_deadline_ends_real_stream_without_retry(self):
  started=time.monotonic()
  with agent_module.DevFlowerAgent(self.rt,timeout=.4) as agent:
   with self.assertRaises(Blocked) as error:agent.chat('m','synthetic')
  self.assertIn('时间上限',str(error.exception));self.assertLess(time.monotonic()-started,2);self.assertEqual(len(self.requests),1)
 def test_cancel_chinese_partial_does_not_finish_or_replay(self):
  from unittest.mock import patch
  from test_workbench_runtime import fixture
  from studio.canvas_assistant import CanvasAssistant
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  import secrets
  self.first_text='正在检查工作流配置。';base,rt=fixture(self);workspace=base.project['id'];graph=examples(rt.operator['profiles'])[0]
  agent_module.AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'));assistant=CanvasAssistant(rt);self.addCleanup(assistant.shutdown)
  choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in rt.operator['profiles'].items()};key=secrets.token_hex(16)
  with patch.object(ConfigurationAssistant,'choices',return_value=choices):
   assistant.start(dict(workspace=workspace,conversation=key,request_id=secrets.token_hex(16),expected=None,message='synthetic',graph=graph,model='m',allow_context=True,intent='chat'));self.assertTrue(self.sent.wait(2))
   for _ in range(100):
    value=assistant.conversation(workspace,key)
    if value['turn'].get('visible_text'):break
    time.sleep(.02)
   self.assertEqual(value['turn']['visible_text'],self.first_text);assistant.cancel(workspace,key)
   for _ in range(100):
    value=assistant.conversation(workspace,key)
    if value['turn']['state'] not in ('QUEUED','RUNNING'):break
    time.sleep(.02)
   self.assertEqual(value['turn']['state'],'CANCELLED');self.assertIsNone(value['turn']['proposal']);self.assertEqual(len(value['messages']),1);self.assertEqual(len(self.requests),1)

 def test_cancel_while_progress_database_locked(self):
  import secrets,sqlite3
  from urllib.request import Request,urlopen
  from unittest.mock import patch
  from test_workbench_runtime import fixture
  from studio.canvas_assistant import CanvasAssistant
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  from studio.workbench_server import create_server
  self.before_first.clear();base,rt=fixture(self);workspace=base.project['id'];graph=examples(rt.operator['profiles'])[0]
  agent_module.AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
  assistant=CanvasAssistant(rt);self.addCleanup(assistant.shutdown);key=secrets.token_hex(16)
  choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in rt.operator['profiles'].items()}
  entered=threading.Event();original_change=rt.flow.change
  def observed_change(kind,key,changes,**kw):
   if 'visible_text' in changes:entered.set()
   return original_change(kind,key,changes,**kw)
  with patch.object(ConfigurationAssistant,'choices',return_value=choices),patch.object(rt.flow,'change',side_effect=observed_change),patch('studio.canvas_assistant.CanvasAssistant',return_value=assistant):
   server,token=create_server(rt);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
   assistant.start(dict(workspace=workspace,conversation=key,request_id=secrets.token_hex(16),expected=None,message='synthetic',graph=graph,model='m',allow_context=True,intent='chat'))
   self.assertTrue(self.request_seen.wait(3));db=sqlite3.connect(rt.flow.path);db.execute('BEGIN IMMEDIATE');self.before_first.set();self.assertTrue(self.sent.wait(2));self.assertTrue(entered.wait(2))
   cancelled=threading.Event();result=[];network_worker=next(iter(assistant.active.values()))[1]
   def stop():
    try:
     data=json.dumps(dict(workspace=workspace,conversation=key)).encode()
     with urlopen(Request('http://127.0.0.1:'+str(server.server_port)+'/api/canvas-assistant/cancel',data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})) as response:result.append(json.load(response))
    finally:cancelled.set()
   worker=threading.Thread(target=stop);worker.start()
   try:
    self.assertTrue(cancelled.wait(1),'cancellation blocked by SQLite progress write')
    network_worker.join(1)
    self.assertFalse(network_worker.is_alive(),'model worker remains blocked in progress persistence')
   finally:db.rollback();db.close();worker.join(4)
   for _ in range(150):
    value=assistant.conversation(workspace,key)
    if value['turn']['state'] not in ('QUEUED','RUNNING','RECOVERY_REQUIRED'):break
    time.sleep(.02)
   self.assertEqual(value['turn']['state'],'CANCELLED');self.assertEqual(len(self.requests),1);self.assertIsNone(value['turn']['proposal'])
   with urlopen(Request('http://127.0.0.1:'+str(server.server_port)+'/api/canvas-assistant/conversation?workspace='+workspace+'&id='+key,headers={'Authorization':'Bearer '+token})) as response:reloaded=json.load(response)
   self.assertEqual(reloaded['turn']['state'],'CANCELLED');self.assertEqual(len(self.requests),1)

 def test_deadline_while_progress_database_locked(self):
  import secrets,sqlite3
  from unittest.mock import patch
  from test_workbench_runtime import fixture
  from studio.canvas_assistant import CanvasAssistant
  from studio.configuration_assistant import ConfigurationAssistant
  from studio.workbench_examples import examples
  self.before_first.clear();base,rt=fixture(self);workspace=base.project['id'];graph=examples(rt.operator['profiles'])[0]
  agent_module.AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(self.server.server_port),model='m',effort='default',api_key='PRIVATE_FIXTURE_SECRET'))
  assistant=CanvasAssistant(rt);self.addCleanup(assistant.shutdown);assistant.backend=lambda cwd,**kw:agent_module.DevFlowerAgent(rt,cwd,**dict(kw,timeout=.7));key=secrets.token_hex(16)
  choices={k:dict(provider=p['provider'],models=[dict(id=p['model'],efforts=[p['effort']])]) for k,p in rt.operator['profiles'].items()}
  with patch.object(ConfigurationAssistant,'choices',return_value=choices):
   assistant.start(dict(workspace=workspace,conversation=key,request_id=secrets.token_hex(16),expected=None,message='synthetic',graph=graph,model='m',allow_context=True,intent='chat'))
   self.assertTrue(self.request_seen.wait(3));network_worker=next(iter(assistant.active.values()))[1]
   db=sqlite3.connect(rt.flow.path);db.execute('BEGIN IMMEDIATE');self.before_first.set();self.assertTrue(self.sent.wait(2))
   try:
    network_worker.join(2);self.assertFalse(network_worker.is_alive(),'network worker exceeded its deadline while SQLite was locked')
    value=assistant.conversation(workspace,key);self.assertNotEqual(value['turn']['state'],'COMPLETED');self.assertTrue(value['turn'].get('persistence_pending'))
   finally:db.rollback();db.close()
   for _ in range(150):
    value=assistant.conversation(workspace,key)
    if value['turn']['state'] not in ('QUEUED','RUNNING','RECOVERY_REQUIRED'):break
    time.sleep(.02)
   self.assertEqual(value['turn']['state'],'FAILED');self.assertIn('时间上限',value['turn']['reason']);self.assertIsNone(value['turn']['proposal']);self.assertEqual(len(self.requests),1)
