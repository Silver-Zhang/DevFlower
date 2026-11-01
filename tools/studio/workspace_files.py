"""Bounded, read-only project browsing. Native stores and authority stay hidden."""
import os,re,stat
from contextlib import contextmanager
from pathlib import Path
from orchestration.common import Blocked,digest
from .graph import Invalid
from .session_sources import opened
from .agent_sessions import safe_text

@contextmanager
def project_opened(path, *, directory=False):
    # Keep the pinned-inode reader and its permission/symlink refusals. Only
    # translate a read denial; never export an OS error's absolute filename.
    try:
        with opened(str(path),directory=directory) as fd:
            yield fd
    except PermissionError:
        message=('无法读取所选文件夹，请核对本机读取权限或重新选择可读取的目录。' if directory else
                 '无法读取所选文件，请核对本机读取权限或选择其他文件。')
        raise PermissionError(message) from None

def safe_parts(value):
    from .workspace_read_policy import safe_parts as checked
    return checked(value)

def path_for(rt,workspace,value):
    root=Path(rt.session_center.workspace(workspace)['path']);rel=safe_parts(value);p=root/rel
    from .native_codex_exec import protected_paths
    if any(p.is_relative_to(Path(x)) for x in protected_paths(rt,{})):raise Blocked('受保护目录不能作为项目文件查看')
    from .workspace_read_policy import ignored
    if ignored(root,rel):raise Blocked('忽略文件默认不向文件浏览器开放')
    return p

def listing(rt,workspace,value=''):
    p=path_for(rt,workspace,value);rows=[];truncated=False
    with project_opened(p,directory=True) as fd,os.scandir(fd) as entries:
        for i,item in enumerate(entries):
            if i>=512:truncated=True;break
            try:
                safe_parts(item.name);s=item.stat(follow_symlinks=False)
                if stat.S_ISLNK(s.st_mode) or not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) and s.st_nlink==1):continue
                rel=(Path(value)/item.name).as_posix();path_for(rt,workspace,rel)
                rows.append(dict(name=item.name,path=rel,directory=stat.S_ISDIR(s.st_mode),size=s.st_size))
            except (Blocked,Invalid,OSError):continue
    return dict(workspace=workspace,path=value,entries=sorted(rows,key=lambda x:(not x['directory'],x['name'])),truncated=truncated)

def preview(rt,workspace,value):
    if not value:raise Invalid('请选择文件')
    p=path_for(rt,workspace,value)
    with project_opened(p) as fd:
        before=os.fstat(fd)
        if before.st_size>16000:raise Invalid('文件超过 16000 字节预览预算；请在编辑器中打开')
        raw=os.read(fd,16001);after=os.fstat(fd)
        if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Blocked('文件在读取期间变化，请刷新')
    if len(raw)>16000 or b'\x00' in raw:raise Invalid('二进制或过大文件不能预览')
    try:text=raw.decode('utf-8')
    except UnicodeError:raise Invalid('只支持 UTF-8 文本预览') from None
    from orchestration.privacy import credentials,redact
    extra=[x[0] for profile in rt.operator['profiles'].values() for x in profile.get('readonly_files',[])]
    return dict(workspace=workspace,path=value,text=safe_text(redact(text,credentials(rt.native,extra))),sha256=digest(raw),readonly=True)
