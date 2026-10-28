"""Short-lived, process-local synthetic probe results, never model admission."""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import time

from orchestration.common import Blocked
from .session_sources import opened

TTL=120.0
CAPACITY=64
TEMPLATE='native-synthetic-capabilities/1'
_lock=threading.Lock()
_cache=OrderedDict()
_flights={}
_pid=os.getpid()

def _after_fork():
    global _lock,_cache,_flights,_pid
    _lock=threading.Lock();_cache=OrderedDict();_flights={};_pid=os.getpid()

if hasattr(os,'register_at_fork'):os.register_at_fork(after_in_child=_after_fork)

def _check(deadline,cancelled):
    if cancelled():raise Blocked('本次原生调用已取消；未自动重发')
    if time.monotonic()>=deadline:raise Blocked('原生调用达到本次等待预算；未自动重发')

def _stamp(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def _bytes(path,deadline,cancelled,limit):
    with opened(path) as fd:
        before=os.fstat(fd)
        if before.st_size>limit:raise ValueError('identity file too large')
        h=hashlib.sha256();total=0
        while True:
            _check(deadline,cancelled)
            chunk=os.read(fd,1048576)
            if not chunk:break
            total+=len(chunk)
            if total>limit:raise ValueError('identity file too large')
            h.update(chunk)
        if _stamp(before)!=_stamp(os.fstat(fd)):raise ValueError('identity changed')
        return (_stamp(before),h.hexdigest())

def _mount_identity(path):
    v=os.statvfs(path)
    # Include the entire namespace mount table, including mount and superblock
    # options. Changes outside this scope conservatively invalidate entries too.
    with open('/proc/self/mountinfo','rb') as stream:
        data=stream.read(4_000_001)
    if len(data)>4_000_000:raise ValueError('mount table too large')
    target=os.fsencode(str(path));matches=[]
    for line in data.splitlines():
        fields=line.split(b' ')
        if len(fields)<6:raise ValueError('invalid mount table')
        mount=re.sub(rb'\\([0-7]{3})',lambda m:bytes([int(m.group(1),8)]),fields[4])
        if mount==b'/' or target==mount or target.startswith(mount+b'/'):
            matches.append((len(mount),fields[0]))
    if not matches:raise ValueError('mount identity missing')
    return (v.f_fsid,v.f_flag,max(matches)[1],hashlib.sha256(data).hexdigest())

def _namespace():
    def identity(path):
        s=os.stat(path);return (s.st_dev,s.st_ino)
    with open('/proc/sys/kernel/random/boot_id','r',encoding='ascii') as stream:boot=stream.read(128).strip()
    if not boot:raise ValueError('boot identity missing')
    with open('/proc/thread-self/status','r',encoding='ascii') as stream:
        fields=dict(line.split(':',1) for line in stream if ':' in line)
    names=('CapEff','CapBnd','CapAmb','NoNewPrivs','Seccomp','Seccomp_filters')
    security=tuple(fields[name].strip() for name in names)
    return (os.getpid(),os.getuid(),os.geteuid(),os.getgid(),os.getegid(),boot,tuple(os.uname()),
            identity('/proc/self/ns/user'),identity('/proc/self/ns/mnt'),security)

def _directory(fd,*,scope=False):
    s=os.fstat(fd)
    # Artifact parents may be created with the caller's umask, but the entire
    # tree must be behind the UID-private storage root.
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or scope and stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError('unsafe capability scope')
    try:os.getxattr(fd,'system.posix_acl_access')
    except OSError as exc:
        import errno
        if exc.errno not in (errno.ENODATA,errno.ENOTSUP,errno.EOPNOTSUPP):raise
    else:raise ValueError('directory ACL not cacheable')
    return (s.st_dev,s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))

def identity(executable,scope,artifacts,ordinary,no_tools,write,deadline,cancelled):
    """Return a conservative key or None; unknown context never hits cache."""
    if not ordinary or not scope:return None
    try:
        _check(deadline,cancelled)
        root=Path(scope);art=Path(artifacts)
        if not root.is_absolute() or not art.is_absolute() or '..' in root.parts or '..' in art.parts:return None
        relative=art.relative_to(root)
        if not relative.parts:return None
        with opened(root,directory=True) as fd:
            rootstat=os.fstat(fd);rootmode=_directory(fd,scope=True);levels=[];current=os.dup(fd)
            try:
                for part in relative.parts:
                    nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=current)
                    os.close(current);current=nxt;levels.append(_directory(current))
                if os.fstat(current).st_dev!=rootstat.st_dev:raise ValueError('artifact filesystem differs')
                scope_mount=_mount_identity(root);art_mount=_mount_identity(art)
                if scope_mount!=art_mount:raise ValueError('artifact mount differs')
            finally:os.close(current)
        exe=Path(executable).resolve(strict=True)
        helper=Path('/usr/bin/python3').resolve(strict=True)
        here=Path(__file__).resolve().parent
        files=(here/'native_capability_cache.py',here/'native_codex_exec_probe.py',here/'native_codex_exec.py',here.parent/'orchestration/sandbox.py',here.parent/'orchestration/common.py')
        key=(_namespace(),str(exe),_bytes(exe,deadline,cancelled,512*1024*1024),str(root),
             (rootstat.st_dev,rootstat.st_ino,rootstat.st_mode,rootstat.st_uid,rootstat.st_gid),rootmode,
             tuple(levels),scope_mount,TEMPLATE,tuple(_bytes(p,deadline,cancelled,4*1024*1024) for p in files),
             str(helper),_bytes(helper,deadline,cancelled,512*1024*1024),
             bool(ordinary),bool(no_tools),bool(write))
        _check(deadline,cancelled)
        return key
    except Blocked:raise
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):return None

def _reset_after_fork():
    global _pid
    if _pid!=os.getpid():
        _cache.clear();_flights.clear();_pid=os.getpid()

def clear():
    with _lock:_cache.clear();_flights.clear()

def run(key,recheck,deadline,cancelled,probe):
    """Singleflight; only completed literal True may become a cache hit."""
    if key is None:return probe()
    _check(deadline,cancelled)
    with _lock:
        _reset_after_fork();now=time.monotonic()
        expiry=_cache.get(key)
        if expiry is not None and expiry>now:
            _cache.move_to_end(key);hit=True;flight=None;owner=False
        else:
            _cache.pop(key,None);hit=False;flight=_flights.get(key);owner=flight is None
            if owner:
                if len(_flights)>=CAPACITY:raise Blocked('本机能力探测并发已满；请稍后重新启动任务')
                flight={'event':threading.Event(),'ok':False};_flights[key]=flight
    if hit:
        if recheck()!=key:raise Blocked('本机能力环境已变化；未发送任务')
        _check(deadline,cancelled)
        if time.monotonic()>=expiry:raise Blocked('本机能力证明已过期；未自动重试或发送任务')
        return True
    if not owner:
        while not flight['event'].wait(min(.05,max(0,deadline-time.monotonic()))):_check(deadline,cancelled)
        _check(deadline,cancelled)
        if not flight['ok']:raise Blocked('并发本机能力探测未通过；未自动重试或发送任务')
        if recheck()!=key:raise Blocked('本机能力环境已变化；未发送任务')
        _check(deadline,cancelled)
        if time.monotonic()>=flight['expiry']:raise Blocked('本机能力证明已过期；未自动重试或发送任务')
        return True
    try:
        if probe() is not True:raise Blocked('本机能力探测未确认成功；未发送任务')
        _check(deadline,cancelled)
        if recheck()!=key:raise Blocked('本机能力环境已变化；未发送任务')
        with _lock:
            _reset_after_fork();expiry=time.monotonic()+TTL;_cache[key]=expiry;_cache.move_to_end(key)
            while len(_cache)>CAPACITY:_cache.popitem(last=False)
            flight['expiry']=expiry;flight['ok']=True
        return True
    finally:
        with _lock:
            if _flights.get(key) is flight:_flights.pop(key,None)
            flight['event'].set()
