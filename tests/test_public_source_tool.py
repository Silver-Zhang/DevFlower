import unittest
from unittest.mock import patch
from test_workbench_runtime import fixture
from studio.public_source_tool import PublicSourceReader
from studio.graph import Invalid
class PublicSourceToolTests(unittest.TestCase):
 def setUp(self):self.base,self.rt=fixture(self);self.reader=PublicSourceReader(self.rt)
 def test_public_source_is_bounded_and_version_bound(self):
  value=self.reader.read('tools/studio/workbench_graph.py',1,8)
  self.assertEqual(value['candidate_sha256'],self.rt.candidate_sha256);self.assertIn('validate',self.reader.read('tools/studio/workflow_inspection.py',1,20)['text'])
  with patch.object(self.rt,'check_operator',side_effect=Invalid('changed')):
   with self.assertRaises(Invalid):self.reader.read('tools/studio/workbench_graph.py',1,8)
 def test_manifest_covers_shipped_public_modules_without_runtime_discovery(self):
  from pathlib import Path
  from studio.public_source_tool import ROOT,PUBLIC_FILES
  expected={str(p.relative_to(ROOT)) for folder in ('tools/studio','tools/orchestration') for p in (ROOT/folder).glob('*.py')}
  expected.update(str(p.relative_to(ROOT)) for p in (ROOT/'docs').rglob('*.md'))
  expected.update(str(p.relative_to(ROOT)) for p in (ROOT/'tools/studio/workbench_ui').iterdir() if p.is_file() and p.suffix in ('.jsx','.mjs','.css'))
  self.assertFalse(expected-PUBLIC_FILES,sorted(expected-PUBLIC_FILES))
  for path in ('tools/studio/managed_claude.py','tools/studio/stream_response.py','tools/devflower_native_terminal.py','tools/studio/workbench_ui/CanvasAssistant.jsx'):
   self.assertIn(path,PUBLIC_FILES)
  self.assertNotIn('tools/studio/private-receipt.json',PUBLIC_FILES)
 def test_private_and_traversal_paths_rejected(self):
  for path in ('/etc/passwd','../auth.json','.git/config','tools/studio/../../etc/passwd','tools/studio/private-receipt.json'):
   with self.subTest(path=path),self.assertRaises(Invalid):self.reader.read(path,1,10)
 def test_openai_tool_round_trip_only_sends_approved_excerpt(self):
  import json
  from studio.devflower_agent import DevFlowerAgent
  events=[];client=DevFlowerAgent(self.rt,notify=events.append);client.config=dict(protocol='openai',model='fixture',effort='default',api_key='',fingerprint='synthetic');client.intent='auto';client.conversation='synthetic'
  calls=[]
  def request(method,path,data):
   calls.append(data.copy())
   if len(calls)==1:return {'choices':[{'message':{'content':None,'tool_calls':[{'id':'a','type':'function','function':{'name':'read_public_source','arguments':json.dumps(dict(path='tools/studio/workbench_graph.py',start=1,end=5))}}]}}]},'fixture'
   self.assertEqual(data['messages'][-1]['role'],'tool');self.assertIn('workbench_graph.py',data['messages'][-1]['content'])
   return {'choices':[{'message':{'content':'Synthetic diagnosis'}}]},'fixture'
  with patch.object(client,'_request',side_effect=request):result=client.chat('fixture','explain error')
  self.assertEqual(json.loads(result['text'])['reply'],'Synthetic diagnosis');self.assertEqual(len(calls),2);self.assertEqual([e['type'] for e in events],['assistant.turn_start','assistant.tool_start','assistant.tool']);self.assertEqual(events[1]['name'],'read_public_source');self.assertEqual(events[2]['path'],'tools/studio/workbench_graph.py')
 def test_reader_budget_and_auth_path_rejection(self):
  for _ in range(3):self.reader.read('tools/studio/workbench_graph.py',1,3)
  with self.assertRaises(Invalid):self.reader.read('tools/studio/workbench_graph.py',1,3)
  with self.assertRaises(Invalid):PublicSourceReader(self.rt).read('tools/studio/workbench_graph.py',1,1001)
 def test_anthropic_tool_round_trip(self):
  import json
  from studio.devflower_agent import DevFlowerAgent
  client=DevFlowerAgent(self.rt);client.config=dict(protocol='anthropic',model='fixture',effort='default',api_key='',fingerprint='synthetic');client.intent='auto';client.conversation='synthetic';calls=[]
  def request(method,path,data):
   calls.append(1)
   if len(calls)==1:return {'content':[{'type':'tool_use','id':'a','name':'read_public_source','input':dict(path='tools/studio/workbench_graph.py',start=1,end=5)}]},'fixture'
   self.assertEqual(data['messages'][-1]['content'][0]['type'],'tool_result');return {'content':[{'type':'text','text':'Synthetic diagnosis'}]},'fixture'
  with patch.object(client,'_request',side_effect=request):result=client.chat('fixture','diagnose')
  self.assertEqual(json.loads(result['text'])['reply'],'Synthetic diagnosis');self.assertEqual(len(calls),2)
 def test_cancel_before_transport_does_not_open_network(self):
  from studio.devflower_agent import DevFlowerAgent
  from orchestration.common import Blocked
  client=DevFlowerAgent(self.rt,cancelled=lambda:True);client.config=dict(protocol='openai',base_url='http://127.0.0.1',api_key='')
  with patch('studio.devflower_agent.http.client.HTTPConnection') as transport:
   with self.assertRaises(Blocked):client._request('POST','/v1/chat/completions',{})
   transport.assert_not_called()
 def test_symlink_and_fifo_are_refused(self):
  import os,tempfile
  from pathlib import Path
  from studio.session_sources import Refused
  with tempfile.TemporaryDirectory(dir=self.rt.storage) as folder:
   root=Path(folder);(root/'docs').mkdir();link=root/'docs/WORKBENCH.md';link.symlink_to('/etc/passwd')
   with patch('studio.public_source_tool.ROOT',root):
    with self.assertRaises((Refused,OSError)):PublicSourceReader(self.rt).read('docs/WORKBENCH.md',1,2)
    link.unlink();os.mkfifo(link)
    with self.assertRaises((Refused,OSError)):PublicSourceReader(self.rt).read('docs/WORKBENCH.md',1,2)
 def test_total_deadline_refuses_another_transport(self):
  import time
  from orchestration.common import Blocked
  from studio.devflower_agent import DevFlowerAgent
  client=DevFlowerAgent(self.rt,timeout=1);client.config=dict(protocol='openai',base_url='http://127.0.0.1',api_key='');client.deadline=time.monotonic()-1
  with patch('studio.devflower_agent.http.client.HTTPConnection') as transport:
   with self.assertRaisesRegex(Blocked,'时间上限'):client._request('POST','/v1/chat/completions',{})
   transport.assert_not_called()

 def test_literal_search_finds_real_session_error(self):
  result=self.reader.search('Expected an opaque authorized session handle')
  hit=next(x for x in result['matches'] if x['path']=='tools/studio/session_center.py')
  excerpt=self.reader.read(hit['path'],hit['line'],hit['line'])
  self.assertIn('Expected an opaque authorized session handle',excerpt['text'])
 def test_real_http_search_then_read_session_error(self):
  import json,threading
  from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
  from studio.devflower_agent import DevFlowerAgent
  seen=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append(body)
    if len(seen)==1:msg={'content':None,'tool_calls':[{'id':'s','type':'function','function':{'name':'search_public_source','arguments':json.dumps({'query':'Expected an opaque authorized session handle'})}}]}
    elif len(seen)==2:
     found=json.loads(body['messages'][-1]['content'])['matches'];hit=next(x for x in found if x['path']=='tools/studio/session_center.py')
     msg={'content':None,'tool_calls':[{'id':'r','type':'function','function':{'name':'read_public_source','arguments':json.dumps({'path':hit['path'],'start':hit['line'],'end':hit['line']})}}]}
    else:
     assert 'Expected an opaque authorized session handle' in body['messages'][-1]['content'];msg={'content':'Synthetic diagnosis: refresh and select the authorized session.'}
    raw=json.dumps({'choices':[{'message':msg}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(thread.join);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  client=DevFlowerAgent(self.rt,timeout=10);client.config=dict(protocol='openai',base_url='http://127.0.0.1:'+str(server.server_port),model='fixture',effort='default',api_key='synthetic-key',fingerprint='synthetic');client.intent='chat';client.conversation='synthetic'
  result=client.chat('fixture','Explain the error');self.assertEqual(len(seen),3);self.assertIn('Synthetic diagnosis',json.loads(result['text'])['reply']);self.assertNotIn('synthetic-key',json.dumps(result))
