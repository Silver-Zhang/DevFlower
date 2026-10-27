"""A synthetic streaming API drives a real bounded public-source tool round trip."""
import json,sys,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).parent)]
from test_workbench_runtime import fixture
from studio.devflower_agent import AgentConnection,DevFlowerAgent

class StreamToolTests(unittest.TestCase):
 def test_search_then_answer_reports_real_tool_activity_without_extra_calls(self):
  base,rt=fixture(self);requests=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(data)
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
    def send(v):self.wfile.write(('data: '+(v if isinstance(v,str) else json.dumps(v))+'\n\n').encode());self.wfile.flush()
    if len(requests)==1:
     send({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'tool1','type':'function','function':{'name':'search_public_source','arguments':json.dumps({'query':'Expected an opaque authorized session handle'})}}]},'finish_reason':'tool_calls'}]})
    else:send({'choices':[{'delta':{'content':'Synthetic diagnostic answer '},'finish_reason':'stop'}]})
    send('[DONE]')
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(server.server_port),model='m',effort='default',api_key=''))
   events=[]
   with DevFlowerAgent(rt,timeout=5,notify=events.append) as agent:
    agent.intent='chat';result=agent.chat('m','synthetic error')
   self.assertEqual(result['inference_calls'],2);self.assertEqual(len(requests),2)
   self.assertTrue(any(e.get('type')=='assistant.tool_start' and e.get('name')=='search_public_source' for e in events))
   self.assertTrue(any(e.get('type')=='assistant.tool' and e.get('count',0)>0 for e in events))
   self.assertIn('tools/studio/session_center.py',json.dumps(requests[1]))
  finally:server.shutdown();server.server_close();thread.join()
