"""Local attachment to an existing supervised PTY; never spawn/resume a CLI.

The socket and capability file belong to the host, not the Agent sandbox. Each
connection observes the same bounded, redacted PTY buffer. One connection (or
the workbench) owns input, guarded by a monotonically increasing generation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import threading
import time

from orchestration.common import Blocked, canonical
from .graph import Invalid
from .server import Conflict
from .native_terminals import CONTROL_BUDGET
from .session_sources import opened

LIMIT = 2_000_000
FIELDS = frozenset(('version','socket','token','terminal','workspace','session_id','provider','server_pid'))
ABSTRACT = re.compile(r'df-native-[0-9a-f]{32}\Z')
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')

def _unique_pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate descriptor field')
        value[key]=item
    return value

def descriptor_address(data, path):
    if (not isinstance(data,dict) or set(data)!=FIELDS or type(data['version']) is not int or
        data['version'] not in (1,2) or type(data['server_pid']) is not int or data['server_pid']<=0 or
        any(not isinstance(data[k],str) or not data[k] or len(data[k])>4096 or any(ord(c)<32 for c in data[k])
            for k in ('socket','token','terminal','workspace','session_id','provider'))):
        raise Blocked('本地接管文件格式或版本无效')
    if data['version']==1:
        if not Path(data['socket']).is_absolute() or Path(data['socket']).parent!=path.parent:
            raise Blocked('本地接管地址不属于此私有目录')
        return data['socket']
    if not ABSTRACT.fullmatch(data['socket']) or not TOKEN.fullmatch(data['token']) or path.name!='attach.json' or path.parent.parent.name!='native-attach':
        raise Blocked('本地接管地址或私有目录无效')
    return '\0'+data['socket']


def receive(stream):
    line = stream.readline(LIMIT + 1)
    if not line or len(line) > LIMIT or not line.endswith(b'\n'):
        raise Blocked('本地连接已断开或消息超限；未重发输入')
    value = json.loads(line)
    if not isinstance(value, dict):
        raise Invalid('本地连接消息必须是对象')
    return value


class NativeAttach:
    def __init__(self, terminals, workspace, key):
        self.terminals, self.workspace, self.key = terminals, workspace, key
        self.closed = threading.Event()
        self.clients = set()
        self.guard = threading.Lock()
        self.secret = secrets.token_urlsafe(32)
        self.listener = None
        self.rootfd = self.filefd = None
        self.root_identity = None
        self.cleanup_info = None
        try:
            storage=Path(terminals.rt.storage)
            with opened(storage,directory=True) as fd:
                st=os.fstat(fd)
                if st.st_uid!=os.getuid() or not stat.S_ISDIR(st.st_mode) or st.st_mode&0o022:
                    raise Blocked('本地接管存储目录不安全')
                try:os.mkdir('native-attach',0o700,dir_fd=fd)
                except FileExistsError:pass
                base=os.open('native-attach',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                try:
                    info=os.fstat(base)
                    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
                        raise Blocked('本地接管私有目录不安全')
                    name=secrets.token_hex(16)
                    os.mkdir(name,0o700,dir_fd=base)
                    self.root=storage/'native-attach'/name
                    self.path=self.root/'attach.json'
                    self.rootfd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=base)
                    rootinfo=os.fstat(self.rootfd)
                    self.root_identity=(rootinfo.st_dev,rootinfo.st_ino)
                    if rootinfo.st_uid!=os.getuid() or stat.S_IMODE(rootinfo.st_mode)!=0o700:
                        raise Blocked('本地接管目录身份不安全')
                finally:os.close(base)
            self.socket_path='df-native-'+secrets.token_hex(16)
            self.listener = socket.socket(socket.AF_UNIX)
            self.listener.bind('\0'+self.socket_path)
            self.listener.listen(4)
            self.listener.settimeout(.2)
            row = terminals.snapshot(workspace, key)
            data = dict(version=2, socket=self.socket_path, token=self.secret,
                        terminal=key, workspace=workspace, session_id=row['session_id'],
                        provider=row['provider'], server_pid=os.getpid())
            self.filefd=os.open('attach.json',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.rootfd)
            data_bytes=canonical(data)
            if os.write(self.filefd,data_bytes)!=len(data_bytes):raise OSError('short descriptor write')
            with opened(self.root,directory=True) as check:
                st=os.fstat(check)
                if (st.st_dev,st.st_ino)!=self.root_identity or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
                    raise Blocked('本地接管目录身份已变化')
            self.thread = threading.Thread(target=self._accept, daemon=True, name='native-attach')
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def _accept(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.guard:
                if len(self.clients) >= 4:
                    client.close()
                    continue
                self.clients.add(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True, name='native-observer').start()

    def _serve(self, client):
        owner = 'local:' + secrets.token_hex(16)
        authenticated = False
        try:
            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
            client.settimeout(3)
            with client.makefile('rwb') as stream:
                hello = receive(stream)
                if set(hello) != {'token', 'terminal', 'workspace', 'session_id'}:
                    return
                row = self.terminals.snapshot(self.workspace, self.key)
                if (not row.get('process_alive') or row.get('state')!='RUNNING' or
                        not secrets.compare_digest(str(hello['token']), self.secret) or
                        (hello['terminal'], hello['workspace'], hello['session_id']) !=
                        (self.key, self.workspace, row['session_id'])):
                    return
                authenticated = True
                stream.write(canonical(dict(ok=True, owner=owner, session_id=row['session_id'], provider=row['provider'])) + b'\n')
                stream.flush()
                while not self.closed.is_set():
                    request = receive(stream)
                    try:
                        op = request.get('op')
                        if op == 'observe' and set(request) <= {'op', 'after'}:
                            result = self.terminals.snapshot(self.workspace, self.key, request.get('after', 0))
                        elif op == 'claim' and set(request) == {'op', 'generation', 'deadline'}:
                            result = self.terminals.claim(self.workspace, self.key, request['generation'], owner, deadline=request['deadline'])
                        elif op == 'release' and set(request) == {'op', 'generation', 'deadline'}:
                            result = self.terminals.release(self.workspace, self.key, request['generation'], owner, deadline=request['deadline'])
                        elif op in ('claim', 'release') and set(request) == {'op', 'generation'}:
                            raise Blocked('本地接管客户端版本过旧；请重新打开最新接管工具并读取状态。')
                        elif op == 'input' and set(request) == {'op', 'generation', 'sequence', 'text'}:
                            result = self.terminals.input(self.workspace, self.key, request['sequence'], request['text'], owner, request['generation'])
                        elif op == 'resize' and set(request) == {'op', 'generation', 'cols', 'rows'}:
                            result = self.terminals.resize(self.workspace, self.key, request['cols'], request['rows'], owner, request['generation'])
                        else:
                            raise Invalid('本地连接不接受命令、路径或进程启动请求')
                        reply = dict(ok=True, result=result)
                    except (Blocked, Invalid, Conflict, OSError, ValueError) as exc:
                        reply = dict(ok=False, error=str(exc))
                    stream.write(canonical(reply) + b'\n')
                    stream.flush()
        except (OSError, ValueError, TypeError, Blocked, Invalid):
            pass
        finally:
            if authenticated:
                self.terminals.detach(self.workspace, self.key, owner)
            with self.guard:
                self.clients.discard(client)
            client.close()

    def close(self):
        self.closed.set()
        if self.listener is not None:self.listener.close()
        with self.guard:
            for client in list(self.clients):
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        # Invalidate the pinned capability inode. Never remove a mutable name:
        # even an apparently empty root may have been replaced after stat.
        if self.filefd is not None:
            try:
                os.ftruncate(self.filefd,0)
                self.cleanup_info=dict(status='retained_private_metadata',path=str(self.path))
            except OSError as exc:
                self.cleanup_info=dict(status='cleanup_failed',path=str(self.path),reason=str(exc))
            finally:os.close(self.filefd);self.filefd=None
        if self.rootfd is not None:
            try:
                current=self.root.lstat()
                if (current.st_dev,current.st_ino)!=self.root_identity or not stat.S_ISDIR(current.st_mode):
                    self.cleanup_info=dict(status='identity_changed',path=str(self.root),capability_cleanup=self.cleanup_info)
            except OSError as exc:self.cleanup_info=dict(status='identity_changed',path=str(self.root),reason=str(exc),capability_cleanup=self.cleanup_info)
            finally:os.close(self.rootfd);self.rootfd=None


class AttachClient:
    def __init__(self, descriptor):
        path = Path(descriptor)
        # Every ancestor of the generated descriptor is trusted by the caller;
        # the descriptor itself must be single-link, private, regular and owned.
        with opened(path.parent,directory=True) as parentfd:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW,dir_fd=parentfd)
            try:
                info = os.fstat(fd)
                parent = os.fstat(parentfd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    info.st_mode & 0o077 or info.st_nlink != 1 or
                    not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
                    raise Blocked('本地接管文件必须由当前用户私有持有')
                self.data = json.loads(os.read(fd, 4097),object_pairs_hook=_unique_pairs)
            finally:os.close(fd)
        address=descriptor_address(self.data,path)
        self.socket = socket.socket(socket.AF_UNIX)
        try:
            self.socket.settimeout(3)
            self.socket.connect(address)
            pid, uid, _ = struct.unpack('3i', self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid() or pid != self.data['server_pid']:
                raise Blocked('本地接管服务进程身份已变化')
            self.stream = self.socket.makefile('rwb')
            self.stream.write(canonical({k: self.data[k] for k in ('token', 'terminal', 'workspace', 'session_id')}) + b'\n')
            self.stream.flush()
            self.hello = receive(self.stream)
            if not self.hello.get('ok') or self.hello.get('session_id') != self.data['session_id']:
                raise Blocked('本地接管握手未确认')
        except BaseException:
            if hasattr(self, 'stream'): self.close()
            else: self.socket.close()
            raise

    def request(self, op, **data):
        try:
            if op in ('claim', 'release'):
                data = dict(data, deadline=time.monotonic()+CONTROL_BUDGET)
            self.stream.write(canonical(dict(op=op, **data)) + b'\n')
            self.stream.flush()
            reply = receive(self.stream)
        except (OSError, ValueError, Blocked):
            try: self.close()
            except (OSError, ValueError): pass
            raise Blocked('本地请求结果未确认；请重新连接读取真实状态，不要自动重发。') from None
        if not reply.get('ok'):
            raise Blocked(reply.get('error', '接管结果未确认，未重发'))
        return reply['result']

    def close(self):
        # A buffered writer may otherwise flush an unfinished request while
        # closing after a timeout. Disable the socket before closing streams.
        try: self.socket.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError): pass
        try: self.stream.close()
        except (OSError, ValueError): pass
        finally: self.socket.close()
