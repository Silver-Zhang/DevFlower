"""Bounded stdio MCP projection for a host-owned managed child endpoint.

This module does not itself create the endpoint or grant execution authority.
The catalog remains disabled until the host service and supervision are wired.
"""
import json
import hashlib
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
import threading
import time

from orchestration.common import Blocked,canonical
from .session_sources import opened

MAX_LINE=32768
ADDRESS=re.compile(r'df-child-[0-9a-f]{32}\Z')
TOKEN=re.compile(r'[A-Za-z0-9_-]{43}\Z')
SCHEMA={'type':'object','properties':{'slot':{'type':'integer','enum':[1,2]},'task':{'type':'string','minLength':1,'maxLength':8192}},'required':['slot','task'],'additionalProperties':False}

def _pairs(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate field')
        result[k]=v
    return result

def parse(data):
    value=json.loads(data,object_pairs_hook=_pairs)
    if not isinstance(value,dict):raise ValueError('object required')
    return value

def arguments(value):
    if not isinstance(value,dict) or set(value)!={'slot','task'} or type(value['slot']) is not int or value['slot'] not in (1,2) or not isinstance(value['task'],str):
        raise ValueError('invalid tool arguments')
    try:raw=value['task'].encode('utf-8')
    except UnicodeEncodeError:raise ValueError('invalid task') from None
    if not raw or len(raw)>8192 or not value['task'].strip():raise ValueError('invalid task')
    return dict(slot=value['slot'],task=value['task'])

def descriptor(path):
    path=Path(path)
    with opened(path.parent,directory=True) as parent:
        st=os.fstat(parent)
        if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Blocked('托管子任务描述符目录不安全')
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:raise Blocked('托管子任务描述符不安全')
            raw=os.read(fd,4097)
        finally:os.close(fd)
    if len(raw)>4096:raise Blocked('托管子任务描述符超限')
    try:data=parse(raw)
    except (ValueError,UnicodeError):raise Blocked('托管子任务描述符无效') from None
    version=data.get('version')
    fields={'version','socket','token','server_pid'} | ({'deadline'} if version==2 else set())
    if (set(data)!=fields or type(version) is not int or version not in (1,2) or
        type(data['server_pid']) is not int or data['server_pid']<=0 or not isinstance(data['socket'],str) or not ADDRESS.fullmatch(data['socket']) or
        not isinstance(data['token'],str) or not TOKEN.fullmatch(data['token'])):raise Blocked('托管子任务描述符身份无效')
    if version==2:remaining(data['deadline'])
    return data

def remaining(deadline):
    if type(deadline) not in (int,float) or not math.isfinite(deadline):raise Blocked('子任务时间预算无效')
    left=deadline-time.monotonic()
    if left<=0 or left>604800:raise Blocked('子任务时间预算不可用')
    return left

def read_line(stream,sock,deadline):
    parts=[]
    for _ in range(MAX_LINE+1):
        sock.settimeout(remaining(deadline))
        part=stream.read(1)
        if not part:break
        parts.append(part)
        if part==b'\n':break
    remaining(deadline)
    return b''.join(parts)

def remote_call(path,values):
    """One request, no retry; endpoint authenticates the bridge separately."""
    data=descriptor(path);sock=socket.socket(socket.AF_UNIX)
    try:
        deadline=data['deadline'] if data['version']==2 else time.monotonic()+300
        sock.settimeout(remaining(deadline));sock.connect('\0'+data['socket'])
        pid,uid,_=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if pid!=data['server_pid'] or uid!=os.getuid():raise Blocked('托管子任务服务身份变化')
        with sock.makefile('rwb') as stream:
            sock.settimeout(remaining(deadline));stream.write(canonical(dict(version=1,token=data['token'],arguments=arguments(values)))+b'\n');stream.flush()
            line=read_line(stream,sock,deadline)
            if not line or len(line)>MAX_LINE or not line.endswith(b'\n'):raise Blocked('子任务结果未确认；不要重发')
            reply=parse(line)
            if set(reply)!={'ok','result'} or type(reply['ok']) is not bool:raise Blocked('子任务结果未确认；不要重发')
            return reply
    finally:sock.close()

def params_without_meta(value):
    if not isinstance(value,dict):raise ValueError('invalid params')
    if '_meta' in value and not isinstance(value['_meta'],dict):raise ValueError('invalid metadata')
    return {key:val for key,val in value.items() if key!='_meta'}

def handle(request,call):
    if request.get('jsonrpc')!='2.0' or not isinstance(request.get('method'),str):raise ValueError('invalid JSON-RPC')
    method=request['method'];ident=request.get('id')
    if method=='notifications/initialized' and (set(request)=={'jsonrpc','method'} or
       set(request)=={'jsonrpc','method','params'} and request['params']=={}):return None
    if set(request)-{'jsonrpc','id','method','params'} or 'id' not in request or type(ident) not in (str,int):raise ValueError('invalid JSON-RPC fields')
    params=params_without_meta(request.get('params',{}))
    if method=='initialize':
        result=dict(protocolVersion='2025-03-26',capabilities={'tools':{}},serverInfo={'name':'devflower-managed-readonly','version':'1'})
    elif method=='tools/list':
        if params:raise ValueError('invalid params')
        result={'tools':[dict(name='delegated_readonly',description='启动或查询本次任务的受控只读子任务。',inputSchema=SCHEMA)]}
    elif method=='tools/call':
        if set(params)!={'name','arguments'} or params['name']!='delegated_readonly':raise ValueError('unknown tool')
        reply=call(arguments(params['arguments']))
        result={'content':[{'type':'text','text':json.dumps(reply.get('result'),ensure_ascii=False)}],'isError':not reply.get('ok',False)}
    else:raise ValueError('unsupported method')
    return dict(jsonrpc='2.0',id=ident,result=result)

class Diagnostic:
    """Bounded private protocol metadata; never records values from requests."""
    def __init__(self,path):
        self.fd=None;self.lock=threading.Lock();self.count=0
        with opened(Path(path).parent,directory=True) as parent:
            from .native_capability_cache import _directory
            try:_directory(parent,scope=True)
            except (ValueError,OSError):raise Blocked('诊断目录不安全') from None
            st=os.fstat(parent)
            if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Blocked('诊断目录不安全')
            self.fd=os.open('bridge-diagnostic.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
    def record(self,line=b'',request=None,status='unknown'):
        allowed={'jsonrpc','id','method','params','protocolVersion','capabilities','clientInfo','name','arguments','slot','task','cursor','_meta'}
        methods={'initialize','notifications/initialized','tools/list','tools/call'}
        req=request if isinstance(request,dict) else {};params=req.get('params')
        keys=lambda obj:sorted(k if k in allowed else '<other>' for k in obj if isinstance(k,str))[:32] if isinstance(obj,dict) else []
        method=req.get('method');method=method if isinstance(method,str) and method in methods else 'other'
        row=dict(method=method,keys=keys(req),params_keys=keys(params),length=len(line),sha256=hashlib.sha256(line).hexdigest(),status=status)
        raw=canonical(row)+b'\n'
        with self.lock:
            if self.fd is not None and self.count<32:
                try:
                    if os.write(self.fd,raw)!=len(raw):raise OSError('short diagnostic write')
                    self.count+=1
                except OSError:
                    os.close(self.fd);self.fd=None
                    print('受管子任务桥诊断写入失败；后续状态未知',file=sys.stderr)
    def close(self):
        with self.lock:
            if self.fd is not None:os.close(self.fd);self.fd=None

def serve(source,sink,call,diagnostic=None):
    write_lock=threading.Lock();slots=threading.BoundedSemaphore(2);workers=[]
    def emit(reply):
        if reply is not None:
            with write_lock:sink.write(canonical(reply)+b'\n');sink.flush()
    def dispatch(request,line):
        try:
            try:reply=handle(request,call);status='handled'
            except Exception:reply=dict(jsonrpc='2.0',id=request.get('id'),error=dict(code=-32600,message='请求无效或子任务结果未确认；未自动重发'));status='error'
            if diagnostic:diagnostic.record(line,request,status)
            try:emit(reply)
            except (OSError,ValueError):pass
        finally:slots.release()
    while True:
        line=source.readline(MAX_LINE+1)
        if not line:break
        request=None
        try:
            if len(line)>MAX_LINE or not line.endswith(b'\n'):raise ValueError('message limit')
            request=parse(line)
            if request.get('method')=='tools/call':
                params=params_without_meta(request.get('params',{}))
                if (request.get('jsonrpc')!='2.0' or set(request)-{'jsonrpc','id','method','params'} or type(request.get('id')) not in (str,int) or
                    set(params)!={'name','arguments'} or params['name']!='delegated_readonly'):
                    raise ValueError('invalid tool request')
                arguments(params['arguments'])
                if slots.acquire(blocking=False):
                    workers=[worker for worker in workers if worker.is_alive()]
                    worker=threading.Thread(target=dispatch,args=(request,line),daemon=True);workers.append(worker);worker.start();reply=None
                else:reply=dict(jsonrpc='2.0',id=request.get('id'),error=dict(code=-32000,message='子任务并发已满；未自动重发'))
            else:reply=handle(request,call)
        except Exception:
            reply=dict(jsonrpc='2.0',id=request.get('id') if 'request' in locals() and isinstance(request,dict) and type(request.get('id')) in (str,int) else None,
                       error=dict(code=-32600,message='请求无效或子任务结果未确认；未自动重发'))
        if diagnostic and not (isinstance(request,dict) and request.get('method')=='tools/call' and reply is None):
            diagnostic.record(line,request,'error' if isinstance(reply,dict) and 'error' in reply else 'handled')
        emit(reply)
        if len(line)>MAX_LINE or not line.endswith(b'\n'):break
        request=None
    # A disconnected stdin cannot block this bridge indefinitely. The caller
    # must supervise and cancel any corresponding host-owned child separately.
    for worker in workers:worker.join(1)

def main():
    if len(sys.argv)!=2:raise SystemExit(2)
    try:descriptor(sys.argv[1])
    except Exception:
        print('受管子任务桥启动失败；诊断不可用',file=sys.stderr);raise SystemExit(1) from None
    diagnostic=None
    try:
        diagnostic=Diagnostic(sys.argv[1]);diagnostic.record(status='started')
    except Exception:print('受管子任务桥诊断不可用',file=sys.stderr)
    try:serve(sys.stdin.buffer,sys.stdout.buffer,lambda values:remote_call(sys.argv[1],values),diagnostic)
    finally:
        if diagnostic:diagnostic.close()

if __name__=='__main__':main()
