"""Narrow Copilot headless JSON-RPC transport; no shell, MCP or agent tools.

Uses the public Copilot SDK wire protocol. No inference in catalog(). Every
owned process is reaped even when publication or a callback raises.
"""
from collections import deque
from pathlib import Path
import json, os, queue, shutil, signal, subprocess, sys, threading, time
from orchestration.common import Blocked
from orchestration.store import process_identity
from .agent_sessions import safe_text


class CopilotRPC:
    def __init__(self, cwd, timeout=120, cancelled=lambda: False, notify=lambda event: None, executable=None):
        self.cwd=str(cwd);self.timeout=timeout;self.cancelled=cancelled;self.notify=notify;self.executable=executable
        self.process=None;self.messages=queue.Queue(maxsize=512);self.events=deque();self.sequence=0
        self.reader_stop=threading.Event();self.reader_error=None

    def __enter__(self):
        executable=self.executable or shutil.which('copilot')
        if not executable:raise Blocked('未安装 GitHub Copilot CLI，请先安装并登录')
        try:
            owner=process_identity(os.getpid())
            self.process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('copilot_bridge.py')),
                str(os.getpid()),str(owner['start']),executable],cwd=self.cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,start_new_session=True,bufsize=0)
            self.deadline=time.monotonic()+self.timeout
            self.reader=threading.Thread(target=self._read,daemon=True);self.reader.start()
            result=self.request('connect',{'supportedTaskKinds':[], 'clientInfo':{'extensionName':'DevFlower','extensionVersion':'0.1'}})
            if result.get('protocolVersion')!=3:raise Blocked('Copilot RPC 版本未验证；未开启执行')
            self.version=result.get('version','unknown')
            return self
        except BaseException:
            self.close();raise

    def _read(self):
        try:
            total=0
            while not self.reader_stop.is_set():
                # Bound headers as well as bodies; no unbounded readline.
                header=self.process.stdout.readline(128)
                if not header:raise EOFError()
                if not header.startswith(b'Content-Length:') or not header.endswith(b'\n'):raise ValueError('RPC header')
                size=int(header.split(b':',1)[1].strip())
                if not 0<size<=2_000_000:raise ValueError('RPC size')
                if self.process.stdout.readline(4) not in (b'\r\n',b'\n'):raise ValueError('RPC separator')
                body=bytearray()
                while len(body)<size:
                    part=self.process.stdout.read(size-len(body))
                    if not part:raise EOFError()
                    body.extend(part)
                total+=size
                if total>8_000_000:raise ValueError('RPC output budget')
                value=json.loads(body)
                # Backpressure the pipe instead of dropping a final response
                # when a fast stream outruns a durable activity subscriber.
                while not self.reader_stop.is_set():
                    try:self.messages.put(value,timeout=.1);break
                    except queue.Full:continue
        except BaseException as exc:
            self.reader_error=exc

    def _send(self,value):
        body=json.dumps(value,separators=(',',':')).encode()
        self.process.stdin.write(f'Content-Length: {len(body)}\r\n\r\n'.encode()+body);self.process.stdin.flush()

    def _next(self):
        while True:
            if self.cancelled():raise Blocked('已取消；不会自动重发模型请求')
            if time.monotonic()>=self.deadline:raise Blocked('Copilot 请求超时；未自动重试')
            try:value=self.messages.get(timeout=.1)
            except queue.Empty:
                if self.reader_error:raise Blocked('Copilot 连接已关闭或响应超限；请检查登录和网络')
                continue
            if isinstance(value,BaseException):raise Blocked('Copilot 连接已关闭或响应超限；请检查登录和网络')
            if 'method' in value and 'id' in value:
                # Context-only workflow Agent exposes no native tool/permission surface.
                self._send(dict(jsonrpc='2.0',id=value['id'],error=dict(code=-32601,message='DevFlower workflow Agent has no native tools')))
                raise Blocked('Copilot 请求了未授权的工具/权限；已拒绝执行')
            if value.get('method')=='session.event':
                event=value.get('params',{}).get('event',{})
                self.notify(event)
            return value

    def request(self,method,params):
        self.sequence+=1;index=self.sequence
        self._send(dict(jsonrpc='2.0',id=index,method=method,params=params))
        while True:
            value=self._next()
            if value.get('id')==index:
                if 'error' in value:
                    error=value['error'];raise Blocked('Copilot RPC 拒绝请求（代码 '+str(error.get('code','unknown'))+'）：'+safe_text(str(error.get('message','unknown')))[:1000])
                return value.get('result',{})
            if value.get('method')=='session.event':self.events.append(value['params'])

    def catalog(self):
        auth=self.request('auth.getStatus',{})
        if not auth.get('isAuthenticated'):raise Blocked('Copilot 尚未登录，请在本机完成 Copilot 登录')
        data=self.request('models.list',{});models=data.get('models')
        if not isinstance(models,list) or any(not isinstance(x,dict) or not isinstance(x.get('id'),str) for x in models):raise Blocked('Copilot 返回了无效的模型目录')
        return dict(provider='copilot',source='Copilot RPC models.list',cli_version=self.version,observed_at=time.time(),
                    authenticated=True,models=models,inference_calls=0,execution_verified=False)

    def chat(self,model,prompt,session_id=None,resume=False):
        catalog=self.catalog()
        if model not in [m['id'] for m in catalog['models']]:raise Blocked('当前账号模型目录中没有此模型；未替换模型或发起推理')
        options=dict(model=model,availableTools=[],excludedTools=[],
            systemMessage=dict(mode='replace',content='你是 DevFlower 工作流中的 GitHub Copilot Agent。只根据提供的上下文执行当前任务；没有工具、文件或交付权限。'),
            workingDirectory=self.cwd,streaming=True,requestPermission=False,requestUserInput=False,
            enableConfigDiscovery=False,enableFileHooks=False,enableSkills=False,enableHostGitOperations=False)
        if session_id:options['sessionId']=session_id
        session=self.request('session.resume' if resume else 'session.create',options)
        sid=session['sessionId'];self.session=sid
        if session_id and sid!=session_id:raise Blocked('Copilot 未恢复明确绑定的原生会话')
        self.request('session.send',dict(sessionId=sid,prompt=prompt))
        answer='';resolved='unknown';usage=None
        while True:
            params=self.events.popleft() if self.events else self._next().get('params',{})
            if params.get('sessionId')!=sid:continue
            event=params.get('event',{});kind=event.get('type');data=event.get('data',{})
            if kind=='assistant.message':answer=data.get('content','')
            if kind=='session.error':raise Blocked('Copilot 推理失败；未修改画布：'+safe_text(str(data.get('message',data.get('errorType','unknown'))))[:1000])
            if kind=='assistant.usage':
                resolved=data.get('model','unknown');usage={k:data[k] for k in ('inputTokens','outputTokens','cost') if k in data}
            if kind=='session.idle' and answer:break
        if not isinstance(answer,str) or not answer.strip():raise Blocked('Copilot 没有返回可见回答；未生成假会话或修改画布')
        return dict(text=answer,provider='copilot',requested_model=model,resolved_model=resolved,session_id=sid,
                    cli_version=self.version,effort='unknown',usage=usage,tools_enabled=False)

    def close(self):
        p=self.process
        if not p:return
        self.reader_stop.set()
        if p.stdin:
            try:p.stdin.close()
            except OSError:pass
        try:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
            p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            p.wait(timeout=3)
        finally:
            if p.stdout:p.stdout.close()

    def __exit__(self,*args):self.close()
