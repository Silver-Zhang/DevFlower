"""Synthetic concurrent confirmation; no real scheduler or Agent."""
import concurrent.futures,json,threading,unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from test_workbench_runtime import fixture
from studio.workbench_server import create_server

class HumanRaceTests(unittest.TestCase):
 def test_human_http_race_accepts_exactly_one_answer(self):
  base,rt=fixture(self);rt.flow.put('activation','human',dict(id='human',run='run',state='WAITING_CONFIRMATION'))
  server,token=create_server(rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  barrier=threading.Barrier(2);original=rt.flow.get
  def read(kind,key):
   row=original(kind,key)
   if kind=='activation' and key=='human' and row['state']=='WAITING_CONFIRMATION':barrier.wait(timeout=5)
   return row
  def send(answer):
   req=Request('http://127.0.0.1:'+str(server.server_port)+'/api/human',data=json.dumps(dict(run='run',activation='human',answer=answer)).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   try:
    with urlopen(req,timeout=10) as r:return r.status,json.load(r)
   except HTTPError as e:return e.code,json.load(e)
  with patch.object(rt.flow,'get',side_effect=read),concurrent.futures.ThreadPoolExecutor(2) as pool:
   results=list(pool.map(send,['SYNTHETIC-A','SYNTHETIC-B']))
  self.assertEqual(sorted(x[0] for x in results),[200,409],results)
  winner=next(x[1]['human_answer'] for x in results if x[0]==200)
  self.assertEqual(original('activation','human')['human_answer'],winner)
  self.assertEqual(len(rt.flow.events('run')),1)
