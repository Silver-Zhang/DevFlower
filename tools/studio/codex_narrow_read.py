"""Descriptor-pinned, bounded reads for Codex Ask/Plan dynamic tools.

Native tools never receive these host descriptors or workspace mounts. A tool
request is data, not an authorization grant; roots come only from the binding.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat

from orchestration.common import Blocked
from .session_sources import clean_path, opened, regular_at, Refused
from .workspace_read_policy import safe_parts,IgnoreRules
from .graph import Invalid

TOOLS = [dict(type='function', name=name, description=description,
              inputSchema=dict(type='object', properties=dict(path=dict(type='string')),
                               required=['path'], additionalProperties=False))
         for name, description in (
             ('devflower_read_file', 'Read one UTF-8 text file in the approved workspace or explicit read paths. No links, writes, shell or network. Maximum 16000 bytes.'),
             ('devflower_list_directory', 'List at most 200 entries in an approved directory. No recursion, links, shell or network.'))]
PROFILE = 'devflower_narrow_read'
HIDDEN = {'.git', '.agents', '.codex', '.claude', '.aws', '.devflower', '.ssh', '.gnupg', '.azure', '.kube'}


def uses_narrow_read(binding):
    return (binding.get('provider')=='codex' and binding.get('execution_backend')=='native-local' and
            binding.get('role')=='reviewer' and binding.get('execution_mode') in ('Ask','Plan') and
            not binding.get('shared_conversation') and not binding.get('no_tools') and not binding.get('admission_probe'))


def identity(fd):
    s=os.fstat(fd)
    return s.st_dev,s.st_ino,stat.S_IFMT(s.st_mode)


class NarrowReadScope:
    def __init__(self,binding,cwd,*,check=lambda:None):
        self.cwd=clean_path(str(cwd));self.check=check;self.closed=True
        paths=binding.get('read_paths',[])
        if not isinstance(paths,list) or len(paths)>64:
            raise Blocked('显式读取路径格式或数量无效；未启动任务')
        self.paths=sorted({self.cwd,*(clean_path(p) for p in paths)})
        if any(p in ('/','/home','/tmp','/var/tmp',str(Path.home())) for p in self.paths):
            raise Blocked('读取授权不能是宿主根或公共目录')
        denied=[str(Path(__file__).resolve().parents[2])]
        denied.extend(binding.get('native_protected',[]))
        if binding.get('config_root'):denied.append(binding['config_root'])
        denied.extend(p for p,_ in binding.get('readonly_files',[]))
        self.denied=tuple(Path(clean_path(p)) for p in denied)
        for p in self.paths:
            if self._denied(Path(p)):raise Blocked('读取授权包含受保护目录；未启动任务')
            if p!=self.cwd:safe_parts(self._classified(Path(p)).as_posix())
        self.stack=ExitStack();self.roots=[];self.policies={};self.calls=0;self.bytes=0

    def _denied(self,path):
        return bool(set(path.parts)&HIDDEN) or any(path==p or path.is_relative_to(p) for p in self.denied)

    def _classified(self,path):
        # The chosen workspace is the root of its relative policy. An outside
        # explicit grant must not hide a sensitive/hidden parent by presenting
        # only a harmless basename. Strong authority roots apply everywhere.
        return path.relative_to(self.cwd) if path.is_relative_to(self.cwd) else Path(*path.parts[1:])

    def __enter__(self):
        try:
            for value in self.paths:
                path=Path(value)
                try:
                    fd=self.stack.enter_context(opened(path,directory=True));directory=True
                except NotADirectoryError:
                    fd=self.stack.enter_context(opened(path));directory=False
                # Directory grants outside the workspace carry no authority.
                # Other file grants and the workspace remain usable. In-workspace
                # directories retain their existing ignore-policy behavior.
                if directory and not path.is_relative_to(Path(self.cwd)):
                    continue
                if not directory and os.fstat(fd).st_nlink!=1:raise Refused('hardlink')
                self.roots.append((path,fd,directory,identity(fd)))
            for root in self.roots:
                if not root[2]:continue
                path=root[0]
                def opener(relative,root=root):return self._open(root[0]/relative,root,directory=True)
                self.policies[str(path)]=IgnoreRules(opener,snapshot=True,forbidden=lambda p,path=path:self._denied(path/p))
            self.closed=False;self.validate();return self
        except BaseException:
            self.stack.close();raise

    def __exit__(self,*args):
        self.closed=True;self.stack.close()

    def validate(self):
        if self.closed:raise Blocked('本次读取授权已结束')
        self.check()
        try:
            for path,fd,directory,stamp in self.roots:
                with opened(path,directory=directory) as current:
                    if identity(current)!=stamp or identity(fd)!=stamp:
                        raise Blocked('读取授权路径已替换；本次请求已停止')
                    if not directory and os.fstat(current).st_nlink!=1:
                        raise Blocked('读取授权文件链接数已变化；本次请求已停止')
        except (OSError,Refused):
            raise Blocked('读取授权路径已失效；本次请求已停止') from None
        for policy in self.policies.values():policy.validate()

    def _path(self,value):
        if not isinstance(value,str) or not value or len(value)>4096 or '..' in Path(value).parts:
            raise Refused('path')
        path=Path(clean_path(value if value.startswith('/') else self.cwd+'/'+value))
        if self._denied(path):raise Refused('protected')
        candidates=[r for r in self.roots if path==r[0] or r[2] and path.is_relative_to(r[0])]
        if not candidates:raise Refused('outside')
        root=max(candidates,key=lambda r:len(r[0].parts))
        relative=self._classified(path)
        try:safe_parts(relative.as_posix())
        except (Blocked,Invalid):raise Refused('sensitive') from None
        # Exact non-sensitive file grants may bypass workspace ignore rules;
        # directory grants cannot. Strong authority/credential denies above
        # and the sensitive filename filter always take precedence.
        if root[2] and self.policies[str(root[0])].denied(path.relative_to(root[0])):raise Refused('ignored')
        return path,root

    @contextmanager
    def _open(self,path,root,*,directory=False):
        base,fd,is_directory,_=root
        if path==base:
            if directory!=is_directory:raise Refused('kind')
            if directory:
                child=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                try:yield child
                finally:os.close(child)
            else:yield fd
            return
        if not is_directory:raise Refused('outside')
        with ExitStack() as stack:
            parent=os.dup(fd);stack.callback(os.close,parent)
            parts=path.relative_to(base).parts
            for component in parts[:-1] if not directory else parts:
                child=os.open(component,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                stack.callback(os.close,child);parent=child
            if directory:
                child=os.open('.',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
                stack.callback(os.close,child);yield child
            else:
                with regular_at(parts[-1],dir_fd=parent) as child:yield child

    def execute(self,name,arguments):
        self.validate()
        self.calls+=1
        if self.calls>64:raise Blocked('本次读取工具调用超过64次；已停止')
        if name not in {t['name'] for t in TOOLS}:raise Blocked('未知读取工具；已停止')
        try:
            if not isinstance(arguments,dict) or set(arguments)!={'path'}:raise Refused('arguments')
            path,root=self._path(arguments['path'])
            if name=='devflower_read_file':
                with self._open(path,root) as fd:
                    before=os.fstat(fd)
                    if before.st_size>16000:raise Refused('budget')
                    raw=os.pread(fd,16001,0);after=os.fstat(fd)
                    fields=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)
                    if fields(before)!=fields(after) or after.st_nlink!=1:raise Refused('changed')
                    if len(raw)>16000 or b'\0' in raw:raise Refused('budget_or_binary')
                    with self._open(path,root) as current:
                        if fields(os.fstat(current))!=fields(after):raise Refused('replaced')
                    value=dict(text=raw.decode('utf-8'))
            else:
                if not root[2]:raise Refused('kind')
                policy=self.policies[str(root[0])];relative=path.relative_to(root[0]);policy.remember(relative)
                entries=[]
                with self._open(path,root,directory=True) as fd,os.scandir(fd) as iterator:
                    stamp=identity(fd)
                    if not os.fstat(fd).st_mode&0o444:raise PermissionError()
                    count=0
                    for entry in iterator:
                        count+=1
                        if count>200:raise Refused('budget')
                        if entry.name in HIDDEN or self._denied(path/entry.name):continue
                        try:
                            safe_parts((relative/entry.name).as_posix())
                        except (Blocked,Invalid):continue
                        if policy.denied(relative/entry.name):continue
                        s=entry.stat(follow_symlinks=False)
                        if not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) and s.st_nlink==1):continue
                        if any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in entry.name):continue
                        entries.append(dict(name=entry.name,type='directory' if stat.S_ISDIR(s.st_mode) else 'file'))
                    with self._open(path,root,directory=True) as current:
                        if identity(current)!=stamp:raise Refused('replaced')
                value=dict(entries=sorted(entries,key=lambda x:x['name']))
            self.validate()
            encoded=json.dumps(value,ensure_ascii=False)
            self.bytes+=len(encoded.encode())
            if self.bytes>128000:raise Blocked('本次读取返回超过128000字节；已停止')
            return dict(success=True,contentItems=[dict(type='inputText',text=encoded)])
        except (OSError,Refused,UnicodeError,ValueError):
            # No OS exception filename, existence oracle or partial bytes.
            self.validate()
            return dict(success=False,contentItems=[dict(type='inputText',text='无法读取：路径未授权、不可读、已变化，或超出文本/目录预算。')])


class NarrowToolRequests:
    """Bind each server request and tool call ID to exactly one active turn."""
    def __init__(self,scope,thread,turn):
        self.scope,self.thread,self.turn=scope,thread,turn
        self.ids=set();self.calls=set()

    def handle(self,event):
        p=event.get('params',{});ident=event.get('id');call=p.get('callId') if isinstance(p,dict) else None
        if (event.get('method')!='item/tool/call' or type(ident) not in (str,int) or
                isinstance(ident,str) and not 1<=len(ident)<=256 or ident in self.ids or
                not isinstance(p,dict) or p.get('threadId')!=self.thread or p.get('turnId')!=self.turn or
                not isinstance(call,str) or not 1<=len(call)<=256 or call in self.calls or
                set(p)-{'arguments','callId','threadId','turnId','tool','namespace'} or p.get('namespace') is not None):
            raise Blocked('窄读取收到未知、重复或跨会话请求；已停止')
        self.ids.add(ident);self.calls.add(call)
        return ident,self.scope.execute(p.get('tool'),p.get('arguments'))
