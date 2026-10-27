"""Codex's supported protocol against the user's canonical native store.

No HOME cloning, database import or transcript rewrite. Reads never resume a
thread. The native thread-store lock arbitrates writers across Desktop/CLI.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from orchestration.common import Blocked, clean_env
from orchestration.store import process_identity
from orchestration.process import stop
from orchestration.sandbox import LIBC


class CodexRPC:
    def __init__(self, executable, home, config_root, cwd, timeout=300, cancelled=lambda:False, cap=8_000_000):
        self.home, self.config_root, self.cwd = map(Path, (home, config_root, cwd))
        self.deadline = time.monotonic() + timeout
        self.cancelled = cancelled
        self.bytes,self.cap,self.overflow=0,cap,False
        self.active_thread,self.active_turn=None,None
        self.next_id, self.pending, self.events = 0, bytearray(), []
        env = clean_env(self.home); env.update(CODEX_HOME=str(self.config_root), TMPDIR='/tmp')
        # Configuration overrides apply only to this client. Native user config,
        # authentication and relay files are never written by DevFlower.
        argv = [executable, '-c', 'mcp_servers={}', '-c', 'features.multi_agent=false',
                '-c', 'features.apps=false', '-c', 'features.code_mode_host=false',
                '-c', 'check_for_update_on_startup=false', 'app-server', '--stdio']
        parent=os.getpid()
        def child_setup():
            if LIBC.prctl(1,signal.SIGKILL,0,0,0) or os.getppid()!=parent:os._exit(125)
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,preexec_fn=child_setup)
        self.identity = None
        self.selector = selectors.DefaultSelector()
        try:
            self.identity = process_identity(self.process.pid)
            if not self.identity: raise Blocked('原生服务进程身份未确认')
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            self.request('initialize', dict(clientInfo=dict(name='devflower_native', version='0.1'),
                                           capabilities=dict(experimentalApi=True)))
            self.send('initialized')
        except BaseException:
            self.close()
            raise

    def send(self, method, params=None, request_id=None):
        value = dict(method=method)
        if params is not None: value['params'] = params
        if request_id is not None: value['id'] = request_id
        self.process.stdin.write(json.dumps(value).encode()+b'\n'); self.process.stdin.flush()

    def poll(self):
        if self.cancelled(): raise Blocked('本次原生调用已取消；未自动重发')
        if time.monotonic() > self.deadline: raise Blocked('原生调用达到本次等待预算；未自动重发')
        if b'\n' not in self.pending:
            if not self.selector.select(.1): return None
            chunk = self.process.stdout.read1(65536)
            if not chunk: raise Blocked('原生连接已结束；未自动重发')
            self.bytes+=len(chunk)
            if self.bytes>self.cap:
                self.overflow=True;raise Blocked('原生输出超过本次预算；未自动重发')
            self.pending.extend(chunk)
        if len(self.pending) > 8_000_000: raise Blocked('原生消息超过读取预算')
        if b'\n' not in self.pending: return None
        line, _, rest = self.pending.partition(b'\n'); self.pending[:] = rest
        return json.loads(line)

    def request(self, method, params):
        self.next_id += 1; request_id = self.next_id
        self.send(method, params, request_id)
        while True:
            value = self.poll()
            if value is None: continue
            if value.get('id') == request_id:
                if 'error' in value:
                    message = value['error'].get('message', '原生服务拒绝请求')
                    if 'active writer' in message:
                        raise Blocked('此原生会话已由另一端持有。请先在原客户端结束或释放会话，再明确接管。')
                    raise Blocked(str(message)[:1200])
                result=value['result']
                if method=='turn/start':self.active_thread,self.active_turn=params['threadId'],result['turn']['id']
                if method=='thread/unsubscribe':self.active_thread,self.active_turn=None,None
                return result
            # Only visible items/status are consumed by the caller; hidden
            # reasoning notifications are neither logged nor persisted here.
            if len(self.events) >= 2000: raise Blocked('原生事件超过本次预算')
            self.events.append(value)

    def next_event(self):
        return self.events.pop(0) if self.events else self.poll()

    def close(self):
        if self.process.poll() is None:
            if self.active_thread and self.active_turn:
                try:self.send('turn/interrupt',dict(threadId=self.active_thread,turnId=self.active_turn),self.next_id+1)
                except (OSError,ValueError):pass
            try: self.process.stdin.close()
            except (OSError, ValueError): pass
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if self.identity: stop(self.identity)
                else:
                    # Unpublished, unreaped child still owns this exact PID.
                    try: os.killpg(self.process.pid, 9)
                    except ProcessLookupError: pass
                self.process.wait(timeout=3)
        self.selector.close()
        for stream in (self.process.stdin,self.process.stdout):
            if not stream.closed: stream.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def metadata(binding, cwd, method, params):
    if method not in {'model/list','account/read','thread/list','thread/read','thread/turns/list','thread/items/list'}:
        raise Blocked('此原生读取接口不执行恢复、启动、归档或删除')
    with CodexRPC(binding['executable'], binding['home'], binding['config_root'], cwd, 30) as rpc:
        return rpc.request(method, params)
