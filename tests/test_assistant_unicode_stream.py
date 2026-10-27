import json,sys,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio.devflower_agent import AgentConnection,DevFlowerAgent
class UnicodeSSE(unittest.TestCase):
 def test_visible_chinese_and_split_unicode_secret(self):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);(home/'.codex').mkdir();(home/'.codex/auth.json').write_text(json.dumps({'access_token':'秘密令牌甲乙'},ensure_ascii=False));sent=threading.Event();release=threading.Event();events=[];results=[];errors=[];calls=[]
   class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
     self.rfile.read(int(self.headers['Content-Length']));calls.append(1);self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
     def send(x):self.wfile.write(('data: '+(x if isinstance(x,str) else json.dumps(x,ensure_ascii=False))+'\n\n').encode());self.wfile.flush()
     try:
      send({'choices':[{'delta':{'content':'正在检查工作流配置。秘密令'},'finish_reason':None}]});sent.set();release.wait(3)
      send({'choices':[{'delta':{'content':'牌甲乙完成。','reasoning_content':'隐藏推理'},'finish_reason':'stop'}]});send('[DONE]')
     except (BrokenPipeError,ConnectionResetError):pass
   server=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();rt=SimpleNamespace(storage=home,operator={'profiles':{'fixture':{'home':str(home),'readonly_files':[]}}},check_operator=lambda:None)
   AgentConnection(rt).save(dict(expected=None,protocol='openai',base_url='http://127.0.0.1:'+str(server.server_port),model='fixture',effort='default',api_key=''))
   def work():
    try:
     with DevFlowerAgent(rt,timeout=5,notify=events.append) as agent:results.append(agent.chat('fixture','synthetic'))
    except Exception as exc:errors.append(exc)
   worker=threading.Thread(target=work);worker.start()
   try:
    self.assertTrue(sent.wait(2))
    for _ in range(100):
     if any(x.get('text') for x in events):break
     time.sleep(.01)
    self.assertTrue(worker.is_alive());self.assertTrue(any('正在检查工作流配置。' in x.get('text','') for x in events));self.assertNotIn('秘密令',str(events));release.set();worker.join(3);self.assertFalse(worker.is_alive());self.assertFalse(errors);self.assertEqual(len(calls),1);self.assertNotIn('秘密令牌甲乙',str(events)+str(results));self.assertNotIn('隐藏推理',str(events)+str(results))
   finally:release.set();server.shutdown();server.server_close();t.join();worker.join(3)
if __name__=='__main__':unittest.main()
