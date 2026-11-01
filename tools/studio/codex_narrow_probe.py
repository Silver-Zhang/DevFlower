"""Fail-closed compatibility check against a synthetic local provider.

The exact executable must expose only the tested client readers and the native
question tool. A new executable/build is never trusted from its version string.
No user prompt, credentials or user-native thread enters this probe.
"""
from contextlib import nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from orchestration.common import Blocked,save,canonical
from .native_codex_image import NativeImage,binary_digest
from .codex_narrow_read import NarrowReadScope,TOOLS

_CACHE={}
_LOCK=threading.Lock()


def require_narrow_capability(executable,artifacts,timeout,cancelled=lambda:False,*,model,effort,authorization_sha256,image=None):
    with NativeImage(executable) if image is None else nullcontext(image) as pinned:
        return _require(pinned,artifacts,timeout,cancelled,model,effort,authorization_sha256)


def _require(image,artifacts,timeout,cancelled,model,effort,authorization_sha256):
    image.validate();path=image.path;digest=image.sha256
    modules=('codex_narrow_probe.py','codex_narrow_read.py','native_codex_rpc.py','native_codex_image.py','shared_codex_config.py','local_codex_adapter.py','workspace_read_policy.py','workspace_files.py','session_sources.py')
    contract=hashlib.sha256(canonical(dict(protocol='native-narrow-read/2',tools=TOOLS,authorization=authorization_sha256,
        code={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in modules}))).hexdigest()
    key=(digest,model,effort,contract)
    with _LOCK:
        if cancelled():raise Blocked('窄读取兼容性核验前已取消')
        if key not in _CACHE:
            _CACHE[key]=_probe(path,digest,artifacts,min(timeout,25),cancelled,model,effort,image=image)
        proof=dict(_CACHE[key])
        proof['contract_sha256']=contract
        proof['does_not_grant_read_authority']=True
    if cancelled():raise Blocked('窄读取兼容性核验后已取消')
    save(Path(artifacts)/'narrow-capability.json',proof)
    return proof


def _probe(executable,digest,artifacts,timeout,cancelled,model,effort,*,image=None):
    from .local_codex_adapter import LocalCodex
    captured=[];errors=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<2_000_000:self.send_error(413);return
            request=json.loads(self.rfile.read(n));index=len(captured)+1
            names=[t.get('name') for t in request.get('tools',[])]
            if len(names)!=3 or set(names)!={'request_user_input',*(t['name'] for t in TOOLS)}:
                errors.append('Unexpected native tool catalog')
            captured.append(dict(names=names,input=request.get('input')))
            if index==1 and not errors:
                item=dict(id='fc_probe',type='function_call',status='completed',call_id='call_probe',name='devflower_read_file',arguments=json.dumps(dict(path='canary')))
            else:
                item=dict(id='msg_probe',type='message',status='completed',role='assistant',content=[dict(type='output_text',text='SYNTHETIC_NARROW_CAPABILITY',annotations=[])])
            response=dict(id='resp_'+str(index),object='response',created_at=0,status='completed',model='fixture',output=[item],usage=dict(input_tokens=1,output_tokens=1,total_tokens=2))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            try:
                for event in (dict(type='response.created',response=dict(response,status='in_progress',output=[])),dict(type='response.output_item.added',output_index=0,item=dict(item,status='in_progress')),dict(type='response.output_item.done',output_index=0,item=item),dict(type='response.completed',response=response)):
                    self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):errors.append('Probe connection ended')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    started=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='narrow-probe-',dir=artifacts) as directory:
            root=Path(directory);home=root/'home';home.mkdir();config=home/'.codex';config.mkdir();cwd=root/'workspace';cwd.mkdir();runs=root/'artifacts';runs.mkdir()
            (cwd/'canary').write_text('SYNTHETIC_NARROW_CANARY')
            (config/'config.toml').write_text('model_provider="fixture"\n[model_providers.fixture]\nname="fixture"\nbase_url="http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n')
            binding=dict(provider='codex',execution_backend='native-local',execution_mode='Ask',role='reviewer',executable=executable,home=str(home),config_root=str(config),model=model,effort=effort,project='synthetic-capability',read_paths=[],readonly_files=[],subagents='disabled',conversation_format='text')
            def check():
                if cancelled() or time.monotonic()-started>timeout:raise Blocked('窄读取兼容性核验已取消或超时')
            with NarrowReadScope(binding,cwd,check=check) as scope:
                scope.binary_sha256=digest
                result=LocalCodex(binding,dict(step_timeout_seconds=timeout,max_log_bytes=2_000_000))._start_shared('SYNTHETIC_CAPABILITY_ONLY',cwd,runs,None,None,None,None,_read_scope=scope,_executable_image=image)
            if (errors or len(captured)!=2 or result['report'].get('reply')!='SYNTHETIC_NARROW_CAPABILITY' or
                    'SYNTHETIC_NARROW_CANARY' not in json.dumps(captured[1]['input'])):
                raise Blocked('原生CLI的窄读取工具目录或协议不符合固定合同；业务请求未发送')
            return dict(version=1,binary_sha256=digest,model=model,effort=effort,tools=sorted(captured[0]['names']),synthetic_model_requests=2,actual_dynamic_read=True,real_account=False)
    except Exception as exc:
        if isinstance(exc,Blocked):raise
        raise Blocked('原生CLI窄读取兼容性核验失败；业务请求未发送') from None
    finally:
        server.shutdown();server.server_close();worker.join(2)
