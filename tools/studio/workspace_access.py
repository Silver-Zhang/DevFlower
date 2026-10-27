"""Host-owned folder selection scope; project files never grant authority."""
import os
import stat
from pathlib import Path
from .graph import Invalid
from .session_sources import clean_path, opened
from orchestration.common import Blocked, digest


def native_workspace_view(rt, path):
    """Derive a host-owned partial view; never accept exclusions from a client."""
    from .native_codex_exec import protected_paths
    root=Path(path)
    if not root.is_absolute() or root in (Path('/'),Path('/home'),Path.home()):
        raise Blocked('请选择具体工作区，不能授权系统或主目录。')
    root=root.resolve(strict=True)
    excluded=[]
    for value in protected_paths(rt,{}):
        protected=Path(value).resolve(strict=False)
        if root.is_relative_to(protected):
            raise Blocked('此工作区本身属于受保护的框架或权威目录，不能运行 Agent。')
        if protected.is_relative_to(root):excluded.append(protected.relative_to(root).as_posix())
    excluded=sorted(set(excluded))
    return dict(kind='native-workspace-view-v1',root=str(root),excluded=excluded)


def native_workspace_hash(rt, view):
    """Hash only this partial view; fail closed on aliases and source races."""
    from orchestration.common import canonical, included, workspace_metadata
    from .native_codex_exec import check_protected_hardlinks,protected_paths
    if view!=native_workspace_view(rt,view['root']):raise Blocked('工作区保护范围已变化，请重新准备。')
    check_protected_hardlinks(protected_paths(rt,{}))
    root=Path(view['root']);excluded=list(view['excluded'])
    if workspace_metadata(root):excluded.append('.devflower')
    entries=[]
    def onerror(exc):raise Blocked('工作区无法完整读取，未启动任务：'+str(exc))
    for folder,dirs,files in os.walk(root,followlinks=False,onerror=onerror):
        dirs[:]=sorted(d for d in dirs if d!='.git' and included((Path(folder)/d).relative_to(root).as_posix(),excluded))
        links=[d for d in dirs if (Path(folder)/d).is_symlink()];dirs[:]=[d for d in dirs if d not in links]
        for name in sorted(files+links):
            if name=='.git':continue
            path=Path(folder)/name;rel=path.relative_to(root).as_posix()
            if not included(rel,excluded):continue
            before=path.lstat()
            if stat.S_ISLNK(before.st_mode):
                data=os.fsencode(os.readlink(path));mode='120000';after=path.lstat()
                if (before.st_dev,before.st_ino,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_mtime_ns):
                    raise Blocked('工作区链接在检查期间变化，请重试。')
            elif stat.S_ISREG(before.st_mode):
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as stream:
                    opened_stat=os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink!=1:raise Blocked('工作区含硬链接或特殊文件，无法安全排除受保护数据。')
                    data=stream.read();after=os.fstat(stream.fileno())
                key=lambda s:(s.st_dev,s.st_ino,s.st_mtime_ns,s.st_size,s.st_mode,s.st_nlink)
                if key(before)!=key(after) or key(opened_stat)!=key(after):raise Blocked('工作区在检查期间变化，请重试。')
                mode='100755' if before.st_mode & 0o111 else '100644'
            else:raise Blocked('工作区含不支持的特殊文件：'+rel)
            if rel=='.gitmodules' or (rel.endswith('.gitattributes') and b'filter=' in data):raise Blocked('不支持 submodule/LFS/clean filters')
            entries.append(dict(path=rel,mode=mode,size=len(data),sha256=digest(data)))
    return digest(canonical(dict(view=view,entries=sorted(entries,key=lambda x:x['path']))))


def selected_scope(rt, path, *, grant=False):
    """Bind an explicit authenticated folder selection to this installation.

    A replaced directory, changed operator or new candidate needs a new pick.
    Browsing and copied .devflower files cannot create this host-only record.
    """
    with opened(path, directory=True) as fd:
        st = os.fstat(fd)
    value = dict(path=path, device=st.st_dev, inode=st.st_ino,
                 operator=rt.operator_sha256, candidate=rt.candidate_sha256)
    key = digest(path.encode())
    if grant:
        policy=view_policy(rt,path)
        allow_parent=(path!='/home' and policy['contains'] and not policy['inside'] and not policy['system'] and
                      not rt.trust and bool(rt.operator.get('native_local',{}).get('codex')))
        if policy['protected_container'] and not allow_parent:
            raise Blocked('此位置包含系统、认证或正在运行的工作台数据；请选择具体项目文件夹。')
        if rt.trust: rt.trust.permits(path, rt.operator_file)
        rt.db.put('selected-workspace-scope', key, value)
        return True
    try: return rt.db.get('selected-workspace-scope', key) == value
    except Invalid: return False


def browse_directory(path):
    try:
        requested = clean_path(path)
        actual = str(Path(requested).resolve(strict=True))
        with opened(actual, directory=True): pass
        return actual
    except (OSError, ValueError, RuntimeError):
        raise Invalid('无法打开这个位置：请检查文件夹是否存在，以及当前用户是否有读取权限。') from None


def view_policy(rt, path):
    p = Path(path)
    private = [*rt.protected, rt.storage, rt.native]
    # Native HOME is shared with the user; its credential and session roots
    # are never implicitly included in a workspace-write grant.
    for provider, value in getattr(rt,'operator',{}).get('native_local',{}).items():
        if isinstance(value,dict) and value.get('config_root'):
            private.append(Path(value['config_root']))
            if provider=='claude' and value.get('home'):private.append(Path(value['home'])/'.claude.json')
    inside = any(p.is_relative_to(x) for x in private)
    contains = any(Path(x).is_relative_to(p) for x in private)
    system = path in ('/', str(Path.home())) or any(p.is_relative_to(x) for x in
        ('/proc','/sys','/dev','/etc','/usr','/bin','/sbin','/lib','/lib64','/boot','/root'))
    return dict(protected_container=inside or contains or system,inside=inside,contains=contains,system=system,
                hosted_records=inside or system,
                reason='此目录可以浏览；为保护系统或工作台数据，记录保存在独立本机记录区。' if inside or system else None)
