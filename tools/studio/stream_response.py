"""Decode bounded SSE output from supported model APIs; no hidden reasoning."""
import json,re,time
from orchestration.common import Blocked


def read_stream(response, protocol, progress, *, deadline=float("inf"), cancelled=lambda:False, public_error=lambda _: "", summary_progress=lambda _:None):
    size=0; count=0; data=[]; done=False; finished=False; text=''; text_bytes=0; last_emit=0.; summary=''; model='unknown'; identity=None; usage={}
    blocks={}; closed_blocks=set(); calls={}; started=False
    def emit(raw):
        nonlocal text,text_bytes,last_emit
        if not isinstance(raw,str):raise Blocked('模型流文本格式无效；没有自动重发')
        text_bytes+=len(raw.encode())
        if text_bytes>150000:raise Blocked('模型回复超过预算')
        text+=raw
        now=time.monotonic()
        if not last_emit or now-last_emit>=.1:
            progress(text);last_emit=now
    def event(raw):
        nonlocal done,finished,identity,model,usage,summary,started
        if raw=='[DONE]':
            if protocol!='openai' or not finished:raise Blocked('模型流提前结束；没有自动重发')
            done=True;return
        try:value=json.loads(raw)
        except (ValueError,RecursionError):raise Blocked('模型流事件格式无效；没有自动重发') from None
        if not isinstance(value,dict):raise Blocked('模型流事件格式无效；没有自动重发')
        if value.get('error') or value.get('type')=='error':
            detail=value.get('error');detail=detail.get('message') if isinstance(detail,dict) else detail
            message=public_error(detail)
            raise Blocked('模型流返回错误'+(('：'+message) if message else '')+'；没有自动重发')
        if protocol=='openai':
            identity=value.get('id',identity);model=value.get('model',model)
            if isinstance(value.get('usage'),dict):usage=value['usage']
            choices=value.get('choices',[])
            if not isinstance(choices,list):raise Blocked('模型流格式无效')
            if not choices:return
            if len(choices)!=1 or not isinstance(choices[0],dict):raise Blocked('模型流包含多个候选，未应用')
            choice=choices[0];delta=choice.get('delta') or {}
            if not isinstance(delta,dict):raise Blocked('模型流格式无效')
            if finished and (delta.get('content') or delta.get('tool_calls')):raise Blocked('模型流结束后仍有内容；未应用草稿')
            if delta.get('content') is not None:emit(delta['content'])
            if isinstance(delta.get('reasoning_summary'),str):
                summary+=delta['reasoning_summary']
                if len(summary)>8000:raise Blocked('公开摘要超过预算')
                summary_progress(summary)
            for call in delta.get('tool_calls',[]):
                if not isinstance(call,dict) or type(call.get('index')) is not int:raise Blocked('模型工具流格式无效')
                part=calls.setdefault(call['index'],dict(id='',name='',arguments=''))
                if call.get('id'):part['id']+=call['id']
                f=call.get('function') or {}
                if not isinstance(f,dict):raise Blocked('模型工具流格式无效')
                part['name']+=f.get('name') or '';part['arguments']+=f.get('arguments') or ''
            reason=choice.get('finish_reason')
            if reason:
                if reason not in ('stop','tool_calls'):raise Blocked('模型流未正常完成：'+str(reason)[:80])
                finished=True
        else:
            kind=value.get('type')
            if kind=='message_start':
                if started:raise Blocked('模型流重复开始；未应用草稿')
                started=True;m=value.get('message') or {};identity=m.get('id');model=m.get('model','unknown');usage=m.get('usage') or {}
            elif kind=='content_block_start':
                if not started or finished:raise Blocked('模型内容流顺序无效')
                index=value.get('index');block=value.get('content_block')
                if type(index) is not int or not isinstance(block,dict) or index in blocks:raise Blocked('模型内容流格式无效')
                t=block.get('type');blocks[index]=dict(type=t,id=block.get('id'),name=block.get('name'),text='',arguments='',input=block.get('input'))
                if t=='text' and block.get('text'):blocks[index]['text']+=block['text'];emit(block['text'])
                if t=='reasoning_summary' and block.get('text'):
                    if not isinstance(block['text'],str):raise Blocked('公开摘要格式无效')
                    blocks[index]['text']+=block['text'];summary+=block['text']
                    if len(summary)>8000:raise Blocked('公开摘要超过预算')
                    summary_progress(summary)
            elif kind=='content_block_delta':
                index=value.get('index');delta=value.get('delta') or {}
                if index not in blocks or index in closed_blocks or not isinstance(delta,dict):raise Blocked('模型内容流缺少起始事件')
                block=blocks[index];t=delta.get('type')
                if block['type']=='text' and t=='text_delta':block['text']+=delta.get('text','');emit(delta.get('text',''))
                elif block['type']=='reasoning_summary' and t=='text_delta':
                    part=delta.get('text','')
                    if not isinstance(part,str):raise Blocked('公开摘要格式无效')
                    block['text']+=part;summary+=part
                    if len(summary)>8000:raise Blocked('公开摘要超过预算')
                    summary_progress(summary)
                elif block['type']=='tool_use' and t=='input_json_delta':block['arguments']+=delta.get('partial_json','')
            elif kind=='content_block_stop':
                index=value.get('index')
                if index not in blocks or index in closed_blocks:raise Blocked('模型内容流结束事件无效')
                closed_blocks.add(index)
            elif kind=='message_delta':
                if isinstance(value.get('usage'),dict):usage.update(value['usage'])
                stop=(value.get('delta') or {}).get('stop_reason')
                if stop:
                    if stop not in ('end_turn','tool_use','stop_sequence'):raise Blocked('模型流未正常完成：'+str(stop)[:80])
                    finished=True
            elif kind=='message_stop':
                if not started or not finished or len(closed_blocks)!=len(blocks):raise Blocked('模型流提前结束；没有自动重发')
                done=True
    while not done:
        if cancelled():raise Blocked('助手生成已取消；没有自动重发')
        if time.monotonic()>=deadline:raise Blocked('助手请求已达到本轮时间上限；没有自动重发')
        count+=1
        if count>40000:raise Blocked('模型流事件数量超过预算')
        line=response.readline(2000001);size+=len(line)
        if size>2000000:raise Blocked('模型回复超过预算')
        if not line:
            if cancelled():raise Blocked('助手生成已取消；没有自动重发')
            if time.monotonic()>=deadline:raise Blocked('助手请求已达到本轮时间上限；没有自动重发')
            raise Blocked('模型流中断；没有自动重发')
        try:line=line.decode('utf-8').rstrip('\r\n')
        except UnicodeDecodeError:raise Blocked('模型流编码无效；没有自动重发') from None
        if not line:
            if data:event('\n'.join(data));data=[]
        elif line.startswith('data:'):data.append(line[5:].lstrip(' '))
    if text:progress(text)
    if protocol=='openai':
        tools=[dict(id=c['id'],type='function',function=dict(name=c['name'],arguments=c['arguments'])) for _,c in sorted(calls.items())]
        message=dict(role='assistant',content=text or None)
        if tools:message['tool_calls']=tools
        if summary:message['reasoning_summary']=summary
        return dict(id=identity,model=model,choices=[dict(message=message)],usage=usage)
    result=[]
    for _,block in sorted(blocks.items()):
        if block['type']=='text':result.append(dict(type='text',text=block['text']))
        elif block['type']=='reasoning_summary':result.append(dict(type='reasoning_summary',text=block['text']))
        elif block['type']=='tool_use':
            try:args=json.loads(block['arguments']) if block['arguments'] else block['input']
            except (ValueError,RecursionError):raise Blocked('模型工具参数不完整；未应用草稿') from None
            result.append(dict(type='tool_use',id=block['id'],name=block['name'],input=args))
    return dict(id=identity,model=model,content=result,usage=usage)


class VisiblePrefix:
    """Publish only complete text tokens, after removing known credentials.

    A suffix which could become a split credential is held until the next
    chunk. Holding incomplete tokens also protects common token patterns.
    """
    def __init__(self,secrets,notify):
        self.secrets=[s for s in set(secrets) if isinstance(s,str) and s]
        self.notify=notify;self.last=''
    def update(self,text,final=False):
        from orchestration.privacy import redact
        from .agent_sessions import safe_text
        end=len(text)
        if not final:
            # CJK text usually has no spaces. Keep unfinished ASCII tokens
            # buffered, while treating Chinese characters as display boundaries.
            for i in range(len(text)-1,-1,-1):
                ch=text[i];code=ord(ch)
                if ch.isspace() or 0x3400<=code<=0x9fff or 0xf900<=code<=0xfaff or 0x20000<=code<=0x3134f or ch in '。，、；：！？）》」』】':end=i+1;break
            else:end=0
            for secret in self.secrets:
                for n in range(1,min(len(secret)-1,len(text))+1):
                    if text.endswith(secret[:n]):end=min(end,len(text)-n)
            token=re.search(r'(?i)\b(?:sk-|gh[pousr]_|github_pat_|AKIA|eyJ)[^\s,;}\]]*$',text)
            if token:end=min(end,token.start())
            pending=re.search(r'''(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization|cookie)[\"']?\s*[:=]\s*([\"'])(?![^\n]*\1)[^\n]*$''',text)
            if pending:end=min(end,pending.start())
            marker=text.rfind('-----BEGIN ')
            if marker>=0 and '-----END ' not in text[marker:]:end=min(end,marker)
            # A complete secret may cross the final display boundary even
            # when more unfinished text follows. Handle overlapping secrets.
            while True:
                before=end
                for secret in self.secrets:
                    start=text.find(secret,max(0,end-len(secret)+1))
                    if 0<=start<end<start+len(secret):end=start
                if end==before:break
        visible=redact(text[:end],self.secrets)
        visible=re.sub(r'(?i)\b(?:sk-|gh[pousr]_|github_pat_|AKIA|eyJ)[^\s,;}\]]+', '[REDACTED]',visible)
        visible=safe_text(visible)[:12000]
        if visible and visible!=self.last:self.last=visible;self.notify(dict(type='assistant.message_delta',text=visible))
