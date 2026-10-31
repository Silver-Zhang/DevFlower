import io,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from studio.stream_response import read_stream,VisiblePrefix
from orchestration.common import Blocked

def stream(*values):
    return io.BytesIO(''.join('data: '+(v if isinstance(v,str) else json.dumps(v))+'\n\n' for v in values).encode())
class StreamingTests(unittest.TestCase):
 def test_openai_text_is_visible_and_hidden_fields_are_not_saved(self):
  values=[];result=read_stream(stream({'id':'1','choices':[{'delta':{'content':'Hello ','reasoning_content':'HIDDEN'},'finish_reason':None}]},{'choices':[{'delta':{'content':'world'},'finish_reason':'stop'}]},'[DONE]'),'openai',values.append)
  self.assertEqual(values,['Hello ','Hello world']);self.assertEqual(result['choices'][0]['message']['content'],'Hello world');self.assertNotIn('HIDDEN',str(result))
 def test_anthropic_tools_and_thinking(self):
  seen=[];result=read_stream(stream({'type':'message_start','message':{'id':'1','model':'m'}},{'type':'content_block_start','index':0,'content_block':{'type':'thinking'}},{'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'SECRET'}},{'type':'content_block_stop','index':0},{'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'t','name':'search_public_source','input':{}}},{'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{"query":"error"}'}},{'type':'content_block_stop','index':1},{'type':'message_delta','delta':{'stop_reason':'tool_use'}},{'type':'message_stop'}),'anthropic',seen.append)
  self.assertEqual(result['content'][0]['input'],{'query':'error'});self.assertNotIn('SECRET',str(result));self.assertEqual(seen,[])
 def test_incomplete_and_error_fail_closed(self):
  for source in [stream({'choices':[{'delta':{'content':'part'}}]}),stream({'error':{'message':'secret'}}),stream('[DONE]')]:
   with self.assertRaises(Blocked):read_stream(source,'openai',lambda _:None)
 def test_explicit_summary_only_and_incomplete_anthropic_block_fails(self):
  summaries=[]
  result=read_stream(stream({'choices':[{'delta':{'reasoning_content':'PRIVATE','reasoning_summary':'Public summary '},'finish_reason':'stop'}]},'[DONE]'),'openai',lambda _:None,summary_progress=summaries.append)
  self.assertEqual(summaries,['Public summary ']);self.assertEqual(result['choices'][0]['message']['reasoning_summary'],'Public summary ');self.assertNotIn('PRIVATE',str(result))
  bad=stream({'type':'message_start','message':{}},{'type':'content_block_start','index':0,'content_block':{'type':'text'}},{'type':'message_delta','delta':{'stop_reason':'end_turn'}},{'type':'message_stop'})
  with self.assertRaises(Blocked):read_stream(bad,'anthropic',lambda _:None)
 def test_deadline_and_cancel_apply_while_decoding(self):
  with self.assertRaises(Blocked):read_stream(stream('[DONE]'),'openai',lambda _:None,deadline=0)
  with self.assertRaises(Blocked):read_stream(stream('[DONE]'),'openai',lambda _:None,cancelled=lambda:True)
 def test_partial_credential_is_not_published(self):
  events=[];prefix=VisiblePrefix(['PRIVATE_FIXTURE_SECRET'],events.append)
  for text in ('Hello PRIVATE_FIX','Hello PRIVATE_FIXTURE_SECRET ','Hello PRIVATE_FIXTURE_SECRET and secret="two ','Hello PRIVATE_FIXTURE_SECRET and secret="two words" '):prefix.update(text)
  shown=str(events);self.assertNotIn('PRIVATE_FIX',shown);self.assertNotIn('two words',shown);self.assertTrue(any('Hello ' in e['text'] for e in events))
 def test_short_ascii_and_unicode_credentials_are_known(self):
  import tempfile
  from orchestration.privacy import credentials
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'.codex';path.mkdir();(path/'auth.json').write_text(json.dumps({'access_token':'短密钥','refresh_token':'x'},ensure_ascii=False))
   found=credentials(folder,include_short=True);self.assertIn('短密钥',found);self.assertIn('x',found);self.assertNotIn('x',credentials(folder))
 def test_chinese_stream_is_visible_while_split_secrets_are_withheld(self):
  events=[];prefix=VisiblePrefix(['秘密令牌甲乙','PRIVATE_FIXTURE_SECRET'],events.append)
  prefix.update('正在检查工作流配置。');self.assertEqual(events[-1]['text'],'正在检查工作流配置。')
  prefix.update('正在检查工作流配置。秘密令');self.assertNotIn('秘密令',str(events))
  prefix.update('正在检查工作流配置。秘密令牌甲乙已隐藏。PRIVATE_FIX');self.assertNotIn('PRIVATE_FIX',str(events));self.assertNotIn('秘密令牌甲乙',str(events));self.assertIn('已隐藏。',events[-1]['text'])
  split=[];mixed=VisiblePrefix(['中文SECRET'],split.append);mixed.update('前文。中文SECRETtail');self.assertEqual(split[-1]['text'],'前文。');mixed.update('前文。中文SECRETtail ');self.assertNotIn('中文SECRET',str(split))
