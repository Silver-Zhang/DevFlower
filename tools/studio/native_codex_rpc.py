"""Codex's supported protocol against the user's canonical native store.

No HOME cloning, database import or transcript rewrite. Reads never resume a
thread. The native thread-store lock arbitrates writers across Desktop/CLI.
"""
from __future__ import annotations
import json
import fcntl
import os
import stat
from contextlib import contextmanager, nullcontext
from pathlib import Path
import selectors
import signal
import subprocess
import time

from orchestration.common import Blocked, clean_env
from orchestration.store import process_identity
from orchestration.process import stop
from orchestration.sandbox import LIBC
from .session_sources import opened


@contextmanager
def metadata_guard(storage, timeout=30, cancelled=lambda:False):
    """Serialize our short metadata processes with the terminal preflight scan.

    The lock is only coordination: the protected-tree scan remains unchanged
    and still rejects any external or otherwise unexplained mutation.
    """
    deadline=time.monotonic()+timeout
    try:
        with opened(storage,directory=True) as directory:
            fd=os.open('.native-metadata-preflight.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
            try:
                s=os.fstat(fd)
                if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_nlink!=1 or s.st_mode&0o077:
                    raise Blocked('原生元数据协调锁不安全；未启动任务')
                while True:
                    if cancelled():raise Blocked('本次原生调用已取消；未自动重发')
                    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline:raise Blocked('原生元数据协调超时；未自动重发') from None
                        time.sleep(.02)
                current=os.stat('.native-metadata-preflight.lock',dir_fd=directory,follow_symlinks=False)
                if (current.st_dev,current.st_ino,current.st_mode,current.st_nlink)!=(s.st_dev,s.st_ino,s.st_mode,s.st_nlink):
                    raise Blocked('原生元数据协调锁已变化；未启动任务')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise Blocked('原生元数据协调超时；未自动重发')
                yield remaining
                current=os.stat('.native-metadata-preflight.lock',dir_fd=directory,follow_symlinks=False)
                end=os.fstat(fd)
                if (current.st_dev,current.st_ino,current.st_mode,current.st_nlink)!=(s.st_dev,s.st_ino,s.st_mode,s.st_nlink) or (end.st_dev,end.st_ino,end.st_mode,end.st_nlink)!=(s.st_dev,s.st_ino,s.st_mode,s.st_nlink):
                    raise Blocked('原生元数据协调锁已变化；未启动任务')
            finally:os.close(fd)
    except OSError as exc:
        raise Blocked('原生元数据协调失败；未启动任务：'+str(exc)) from None


class CodexRPC:
    def __init__(self, executable, home, config_root, cwd, timeout=300, cancelled=lambda:False, cap=8_000_000,
                 *, shared_config=None, provider_args=(), executable_image=None):
        self.home, self.config_root, self.cwd = map(Path, (home, config_root, cwd))
        self.deadline = time.monotonic() + timeout
        self.cancelled = cancelled
        self.bytes,self.cap,self.overflow=0,cap,False
        self.active_thread,self.active_turn=None,None
        self.next_id, self.pending, self.events = 0, bytearray(), []
        self.shared_config=shared_config
        env = clean_env(self.home); env.update(CODEX_HOME=str(self.config_root), TMPDIR='/tmp')
        # Configuration overrides apply only to this client. Native user config,
        # authentication and relay files are never written by DevFlower.
        argv = [executable, '-c', 'features.multi_agent=false', '-c', 'features.plugins=false',
                '-c', 'features.apps=false', '-c', 'features.browser_use=false',
                '-c', 'features.computer_use=false', '-c', 'features.code_mode_host=false',
                '-c', 'check_for_update_on_startup=false', 'app-server', '--stdio']
        if shared_config is not None:
            from .codex_narrow_read import TOOLS,PROFILE
            from .shared_codex_config import SharedConversationConfig,STORE_ALIAS
            if (type(shared_config) is not SharedConversationConfig or shared_config.closed or
                    shared_config.cwd!=str(self.cwd) or shared_config.binding['home']!=str(self.home) or
                    shared_config.binding['config_root']!=str(self.config_root)):
                raise Blocked('共享会话配置与本次执行身份不匹配')
            overrides=['-c','features.shell_tool=false','-c','features.unified_exec=false',
                       '-c','features.goals=false','-c','features.default_mode_request_user_input=false',
                       '-c','features.sleep_tool=false','-c','features.tool_suggest=false',
                       '-c','features.hooks=false','-c','features.view_image=false',
                       '-c','features.skill_mcp_dependency_install=false',
                       '-c','skills.include_instructions=false','-c','skills.bundled.enabled=false',
                       '-c','sqlite_home='+json.dumps(STORE_ALIAS),
                       '-c','project_doc_max_bytes=0','-c','web_search="disabled"']
            if shared_config.read_scope is not None:
                from .codex_narrow_read import PROFILE
                # Native helpers see an empty workspace and only their static
                # executable. Host user files are read by the bounded client
                # tools, never by a shell, MCP, Skill or built-in file tool.
                from .native_codex_image import EXECUTABLE_ALIAS
                executable=EXECUTABLE_ALIAS
                filesystem='{":root"="deny",":workspace_roots"="read",'+json.dumps(executable)+'="read"}'
                overrides+=['-c','permissions.'+PROFILE+'.filesystem='+filesystem,
                            '-c','permissions.'+PROFILE+'.network.enabled=false',
                            '-c','default_permissions='+json.dumps(PROFILE)]
            argv=shared_config.wrap(argv[:-2]+list(provider_args)+overrides+argv[-2:])
        parent=os.getpid()
        def child_setup():
            if LIBC.prctl(1,signal.SIGKILL,0,0,0) or os.getppid()!=parent:os._exit(125)
        from .native_codex_image import NativeImage
        self.owned_image=None
        if shared_config is not None:
            image=shared_config.executable_image
        else:
            image=executable_image
            if image is None:image=self.owned_image=NativeImage(executable)
        image.validate()
        try:
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,preexec_fn=child_setup,
                executable=None if shared_config is not None else '/proc/self/fd/'+str(image.fd),
                pass_fds=shared_config.pass_fds if shared_config is not None else (image.fd,))
        except BaseException:
            if self.owned_image is not None:self.owned_image.close()
            raise
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
        from .codex_narrow_read import TOOLS,PROFILE
        shared_config=getattr(self,'shared_config',None)
        # App-server reloads user/project configuration when a thread starts.
        # An MCP server added after discovery can therefore start before a
        # client-side check or per-name override can see it. Until the CLI
        # offers a pinned/isolated config layer, do not start a tool-bearing
        # native thread using the user's mutable configuration.
        if method in ('thread/start','thread/resume','turn/start') and shared_config is None:
            if method=='thread/resume':
                raise Blocked('原生 Codex 工具配置无法固定；exec 续聊也不能在同一原生写锁内原子核验上一回合。此会话暂只能只读查看，请勿盲目重发。')
            raise Blocked('原生 Codex 工具配置无法固定：外部 MCP 或插件可能在启动时进入本次会话。为保护工作区，暂不能发送或运行；模型目录和历史仍可只读查看。')
        if shared_config is not None:
            if shared_config.closed:raise Blocked('共享会话配置已经关闭')
            if shared_config.read_scope is not None:shared_config.read_scope.validate()
            if method in ('thread/start','thread/resume'):
                narrow=shared_config.read_scope is not None
                extra={'dynamicTools','permissions'} if narrow else set()
                invalid_profile=(params.get('permissions')!=PROFILE or 'sandbox' in params) if narrow else params.get('sandbox')!='read-only'
                invalid_tools=narrow and ((method=='thread/start' and params.get('dynamicTools')!=TOOLS) or (method=='thread/resume' and 'dynamicTools' in params))
                if (params.get('cwd')!=str(self.cwd) or invalid_profile or
                        params.get('approvalPolicy')!='never' or
                        set(params)-{'threadId','cwd','sandbox','approvalPolicy','model','ephemeral','excludeTurns','threadSource'}-extra or
                        invalid_tools):
                    raise Blocked('共享会话仅允许固定工作区的无工具只读配置')
            extra={'permissions'} if shared_config.read_scope is not None else set()
            if method=='turn/start' and (set(params)-{'threadId','input','model','effort','outputSchema'}-extra or
                    extra and params.get('permissions')!=PROFILE or
                    any(x.get('type')!='text' for x in params.get('input',[]))):
                raise Blocked('共享会话当前只接收文本与已冻结的显式上下文')
            if method not in ('initialize','thread/start','thread/resume','thread/read','thread/name/set','turn/start','thread/unsubscribe'):
                raise Blocked('共享会话不执行此原生操作')
        self.next_id += 1; request_id = self.next_id
        self.send(method, params, request_id)
        while True:
            value = self.poll()
            if value is None: continue
            if type(value.get('id')) is int and value['id']==request_id and 'method' not in value:
                if 'error' in value:
                    message = value['error'].get('message', '原生服务拒绝请求')
                    if 'active writer' in message:
                        raise Blocked('此原生会话已由另一端持有。请先在原客户端结束或释放会话，再明确接管。')
                    raise Blocked(str(message)[:1200])
                result=value['result']
                if shared_config is not None and shared_config.read_scope is not None and method in ('thread/start','thread/resume'):
                    if (result.get('activePermissionProfile',{}).get('id')!=PROFILE or
                            result.get('cwd')!=str(self.cwd) or result.get('runtimeWorkspaceRoots')!=[str(self.cwd)]):
                        raise Blocked('原生服务未确认固定窄读取配置；未发送任务')
                if method=='turn/start':self.active_thread,self.active_turn=params['threadId'],result['turn']['id']
                if method=='thread/unsubscribe':self.active_thread,self.active_turn=None,None
                return result
            # Only visible items/status are consumed by the caller; hidden
            # reasoning notifications are neither logged nor persisted here.
            if len(self.events) >= 2000: raise Blocked('原生事件超过本次预算')
            self.events.append(value)

    def next_event(self):
        return self.events.pop(0) if self.events else self.poll()

    def respond_tool(self,request_id,result):
        if (self.shared_config is None or self.shared_config.read_scope is None or
                type(request_id) not in (str,int) or not isinstance(result,dict)):
            raise Blocked('无效的受限工具响应')
        self.shared_config.read_scope.validate()
        if self.cancelled() or time.monotonic()>self.deadline:raise Blocked('工具响应前已取消或超时；未自动重发')
        self.process.stdin.write(json.dumps(dict(id=request_id,result=result)).encode()+b'\n');self.process.stdin.flush()

    def close(self):
        try:
            if self.process.poll() is None:
                if self.active_thread and self.active_turn:
                    try:self.send('turn/interrupt',dict(threadId=self.active_thread,turnId=self.active_turn),self.next_id+1)
                    except (OSError,ValueError):pass
                try:self.process.stdin.close()
                except (OSError,ValueError):pass
                try:self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if self.identity:stop(self.identity)
                    else:
                        # An unpublished, unreaped child still owns this PID.
                        try:os.killpg(self.process.pid,9)
                        except ProcessLookupError:pass
                    self.process.wait(timeout=3)
        finally:
            self.selector.close()
            if self.owned_image is not None:self.owned_image.close()
            for stream in (self.process.stdin,self.process.stdout):
                try:
                    if not stream.closed:stream.close()
                except (OSError,ValueError):pass

    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def metadata(binding, cwd, method, params):
    if method not in {'model/list','account/read','thread/list','thread/read','thread/turns/list','thread/items/list'}:
        raise Blocked('此原生读取接口不执行恢复、启动、归档或删除')
    lock=binding.get('_metadata_guard')
    with metadata_guard(lock) if lock else nullcontext(30) as remaining:
        if remaining<=0:raise Blocked('原生元数据协调超时；未自动重发')
        with CodexRPC(binding['executable'], binding['home'], binding['config_root'], cwd, remaining) as rpc:
            return rpc.request(method, params)
