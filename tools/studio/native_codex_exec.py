"""Narrow native Codex exec configuration. Never copy a native HOME or secrets."""
from __future__ import annotations
import json
import re
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from orchestration.common import Blocked

# Config keys are written individually as TOML; an empty MCP table does not
# replace lower layers. --ignore-user-config is a required executable capability.
FEATURES={'plugins':False,'apps':False,'multi_agent':False,'browser_use':False,
          'computer_use':False,'view_image':False,'goals':False,'code_mode_host':True,
          'shell_tool':True,'in_app_browser':False,'hooks':False,
          'skill_mcp_dependency_install':False}
_PROVIDER_TYPES={
    'name':str,'base_url':str,'wire_api':str,'request_max_retries':int,
    'stream_max_retries':int,'stream_idle_timeout_ms':int,'websocket_connect_timeout_ms':int,
    'requires_openai_auth':bool,'supports_websockets':bool,'supports_standalone_web_search':bool,
}
_PROVIDER_DENY={'env_key','env_key_instructions','experimental_bearer_token','auth','gateway_oauth',
                'aws','capabilities','query_params','http_headers','env_http_headers','model_catalog_url'}

def _toml(value):
    if type(value) is str:return json.dumps(value,ensure_ascii=True)
    if type(value) is bool:return 'true' if value else 'false'
    if type(value) is int and 0<=value<=86400000:return str(value)
    raise Blocked('原生 Provider 配置类型不受支持；未启动会话')

def _url(value):
    try: u=urlsplit(value)
    except ValueError:raise Blocked('原生 Provider 地址无效') from None
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise Blocked('原生 Provider 地址包含不支持的身份或参数；未启动会话')
    return value

def provider_overrides(config):
    if not isinstance(config,dict):raise Blocked('无法读取原生 Provider 的有效配置')
    if any(config.get(k) is not None for k in ('forced_login_method','forced_chatgpt_workspace_id')):
        raise Blocked('原生账号约束尚不能安全迁移到本次客户端；未启动会话')
    name=config.get('model_provider') or 'openai'
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',name):raise Blocked('原生 Provider 身份无效')
    args=['-c','model_provider='+_toml(name)]
    auth_store=config.get('cli_auth_credentials_store')
    if auth_store is not None:
        if auth_store not in ('file','keyring','auto','ephemeral'):
            raise Blocked('原生认证存储方式未识别；未更换账号或启动会话')
        args+=['-c','cli_auth_credentials_store='+_toml(auth_store)]
    for key in ('chatgpt_base_url','openai_base_url'):
        value=config.get(key)
        if value is not None:
            if not isinstance(value,str):raise Blocked('原生 Provider 地址类型不受支持')
            args+=['-c',key+'='+_toml(_url(value))]
    providers=config.get('model_providers') or {}
    if not isinstance(providers,dict):raise Blocked('原生 Provider 列表无效')
    selected=providers.get(name)
    if selected is None:
        if name!='openai':raise Blocked('所选原生 Provider 未能核验；未替换为默认连接')
        return args
    if not isinstance(selected,dict):raise Blocked('所选原生 Provider 格式无效')
    for key,value in selected.items():
        if value is None:continue
        if key in _PROVIDER_DENY or key not in _PROVIDER_TYPES:raise Blocked('所选原生 Provider 包含尚未验证的认证或配置字段；未启动会话')
        if type(value) is not _PROVIDER_TYPES[key]:raise Blocked('所选原生 Provider 配置类型不受支持')
        if key=='base_url':_url(value)
        args+=['-c','model_providers.'+name+'.'+key+'='+_toml(value)]
    return args

def protected_paths(runtime,binding):
    """Host-owned authority and credentials only; never model-provided paths."""
    root=Path(__file__).resolve().parents[2]
    paths=[root,runtime.storage,runtime.native,runtime.operator_file,runtime.policy_file]
    executables={str(p['executable']) for p in runtime.operator['profiles'].values() if p.get('executable')}
    for profile in getattr(runtime,'policy',{}).get('external_steps',{}).values():
        if profile.get('argv'):executables.add(str(profile['argv'][0]))
    paths.extend(p for p in runtime.protected if Path(p)!=root and str(p) not in executables)
    for profile in runtime.operator['profiles'].values():
        paths.extend(src for src,_ in profile.get('readonly_files',[]))
    for provider,value in runtime.operator.get('native_local',{}).items():
        paths.append(value['config_root'])
        if provider=='claude':paths.append(str(Path(value['home'])/'.claude.json'))
    if binding.get('config_root'):paths.append(binding['config_root'])
    return sorted({str(Path(p)) for p in paths})

# The canonical native store already contains more than 4,096 directories and
# links. Keep a bounded scan without excluding a normal installed plugin tree;
# a lower operating-system descriptor limit still fails closed and releases FDs.
_PROTECTED_FD_LIMIT=32768

def check_protected_hardlinks(paths):
    """Count names inside deny roots using metadata only, with two bounded scans."""
    if not isinstance(paths,(list,tuple)) or not paths or len(paths)>512:
        raise Blocked('受保护目录范围无效；未启动任务。')
    roots=[]
    for value in paths:
        if not isinstance(value,str) or not value.startswith('/') or value=='/' or '..' in Path(value).parts or any(ord(c)<32 or ord(c)==127 for c in value):
            raise Blocked('受保护目录范围无效；未启动任务。')
        path=Path(value)
        try:resolved=path.resolve(strict=False)
        except (OSError,RuntimeError,ValueError):raise Blocked('受保护目录无法核验；未启动任务。') from None
        if resolved!=path:raise Blocked('受保护目录含符号链接，无法确认边界；未启动任务。')
        roots.append(path)
    roots.sort(key=lambda p:(len(p.parts),str(p)))
    unique=[]
    for path in roots:
        if not any(path.is_relative_to(parent) for parent in unique):unique.append(path)
    deadline=time.monotonic()+30
    held=[]
    def onerror(exc):raise Blocked('无法核验受保护目录的硬链接：'+str(exc))
    def identity(s):return (s.st_dev,s.st_ino,s.st_mode)
    def stable(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_ctime_ns)
    def snapshot(anchor=False):
        entries={};groups={};seen=set();count=0
        def add(path,s,parent_identity,dirfd,name,root_path,*,root=False):
            nonlocal count
            count+=1
            if time.monotonic()>deadline or count>200000:
                raise Blocked('受保护目录检查超过时间或文件数量限制；未启动任务。')
            key=str(path);mode=s.st_mode;seen.add(key)
            sensitive=root or stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or s.st_nlink>1
            if sensitive:
                target=None;fd=None
                if anchor:
                    if len(held)>=_PROTECTED_FD_LIMIT:raise Blocked('受保护目录句柄超过限制；未启动任务。')
                    try:fd=os.open(name,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dirfd)
                    except FileNotFoundError:
                        raise Blocked('受保护目录在固定句柄期间变化；未启动任务。') from None
                    held.append(fd)
                    pinned=os.fstat(fd)
                    if (identity(pinned)!=identity(s) or
                            not stat.S_ISDIR(mode) and pinned.st_nlink!=s.st_nlink or
                            not stat.S_ISDIR(mode) and (stat.S_ISLNK(mode) or s.st_nlink>1) and stable(pinned)!=stable(s)):
                        raise Blocked('受保护目录在检查期间变化；未启动任务。')
                if stat.S_ISLNK(mode):
                    target=os.readlink('',dir_fd=fd) if fd is not None else os.readlink(name,dir_fd=dirfd)
                    after=os.fstat(fd) if fd is not None else os.stat(name,dir_fd=dirfd,follow_symlinks=False)
                    if stable(after)!=stable(s):raise Blocked('受保护目录在检查期间变化；未启动任务。')
                entries[key]=(s.st_dev,s.st_ino,mode,None if stat.S_ISDIR(mode) else s.st_nlink,
                              s.st_ctime_ns if (s.st_nlink>1 or stat.S_ISLNK(mode)) and not stat.S_ISDIR(mode) else None,
                              target,str(root_path),fd)
            if not stat.S_ISDIR(mode) and s.st_nlink>1:
                if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):raise Blocked('受保护特殊文件存在硬链接；未启动任务。')
                group=groups.setdefault((s.st_dev,s.st_ino),{'names':set(),'paths':[]})
                group['names'].add((*parent_identity,path.name));group['paths'].append(key)
        for root in unique:
            try:s=os.lstat(root)
            except FileNotFoundError:entries[str(root)]=None;continue
            if stat.S_ISLNK(s.st_mode):raise Blocked('受保护目录含符号链接，无法确认边界；未启动任务。')
            parentfd=os.open(root.parent,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            try:
                parent=os.fstat(parentfd)
                add(root,s,(parent.st_dev,parent.st_ino),parentfd,root.name,root,root=True)
            finally:os.close(parentfd)
            if stat.S_ISDIR(s.st_mode):
                for folder,dirs,files,fd in os.fwalk(root,topdown=True,follow_symlinks=False,onerror=onerror):
                    current=os.fstat(fd);previous=entries.get(str(folder))
                    if not previous or identity(current)!=previous[:3]:raise Blocked('受保护目录在检查期间变化；未启动任务。')
                    for name in dirs+files:
                        try:entry=os.stat(name,dir_fd=fd,follow_symlinks=False)
                        except FileNotFoundError:
                            # SQLite removes its transient WAL/SHM while an
                            # independent connection checkpoints and closes.
                            if name in files and name.endswith(('.sqlite-wal','.sqlite-shm','.db-wal','.db-shm')):continue
                            raise
                        add(Path(folder)/name,entry,(current.st_dev,current.st_ino),fd,name,root)
        return entries,groups,seen
    def resample(anchor=False):
        # A non-WAL single-link file may disappear after readdir. Discard
        # only incomplete scans, including every descriptor they opened.
        for attempt in range(3):
            mark=len(held)
            try:return snapshot(anchor)
            except FileNotFoundError:
                while len(held)>mark:os.close(held.pop())
                # An incomplete first scan cannot establish a new baseline:
                # retrying could conceal replacement of already observed roots
                # or shared inodes. Only S2 may retry with all S1 anchors held.
                if anchor:
                    raise Blocked('无法完成受保护目录初始核验；未启动任务。') from None
                if attempt==2:raise
    try:
        if not all(hasattr(os,k) for k in ('O_PATH','O_NOFOLLOW','O_CLOEXEC','O_DIRECTORY')):
            raise Blocked('无法安全固定受保护目录；未启动任务。')
        first,groups,_=resample(True);second,_,seen=resample()
        # Shared inodes retain exact names, count and ctime. In particular,
        # moving an ancestor cannot conceal a shared name change.
        for group in groups.values():
            if any(first[name][3]!=len(group['names']) for name in group['paths']):
                raise Blocked('受保护文件存在未确认归属的硬链接；未启动任务。')
        matched=set()
        locations={}
        for key,value in second.items():
            if value is not None:locations.setdefault((value[6],value[0],value[1]),[]).append(key)
        for key,value in first.items():
            if value is None:
                if second.get(key,'missing') is not None:raise Blocked('受保护目录在检查期间变化；未启动任务。')
                matched.add(key);continue
            fd=value[7];current=second.get(key)
            if value[3] is not None and value[3]>1:
                st=os.fstat(fd)
                if (identity(st)!=value[:3] or st.st_nlink!=value[3] or st.st_ctime_ns!=value[4] or
                        current is None or current[:7]!=value[:7]):raise Blocked('受保护目录在检查期间变化；未启动任务。')
                matched.add(key);continue
            st=os.fstat(fd)
            if identity(st)!=value[:3]:raise Blocked('受保护目录在检查期间变化；未启动任务。')
            if stat.S_ISLNK(value[2]) and os.readlink('',dir_fd=fd)!=value[5]:
                raise Blocked('受保护目录在检查期间变化；未启动任务。')
            if stat.S_ISLNK(value[2]) and stable(os.fstat(fd))!=stable(st):
                raise Blocked('受保护目录在检查期间变化；未启动任务。')
            if current is not None:
                if (value[3] is not None and st.st_nlink!=value[3] or
                        current[:4]!=value[:4] or current[5:7]!=value[5:7] or
                        stat.S_ISLNK(value[2]) and (current[4]!=value[4] or st.st_ctime_ns!=value[4])):
                    raise Blocked('受保护目录在检查期间变化；未启动任务。')
                matched.add(key);continue
            # Never accept a replacement at the old name, even a single-link
            # ordinary file omitted from the sensitive-entry map.
            if key in seen or key==value[6]:raise Blocked('受保护目录在检查期间变化；未启动任务。')
            if st.st_nlink==0:continue
            candidates=locations.get((value[6],value[0],value[1]),[])
            if len(candidates)!=1:raise Blocked('受保护目录在检查期间变化；未启动任务。')
            moved=candidates[0];other=second[moved]
            if (value[3] is not None and st.st_nlink!=value[3] or
                    other[:4]!=value[:4] or other[5:7]!=value[5:7]):raise Blocked('受保护目录在检查期间变化；未启动任务。')
            matched.add(moved)
        if any(value is None or not stat.S_ISDIR(value[2]) for key,value in second.items() if key not in matched):
            raise Blocked('受保护目录在检查期间变化；未启动任务。')
        if time.monotonic()>deadline:raise Blocked('受保护目录检查超过时间或文件数量限制；未启动任务。')
    except (OSError,NotImplementedError,AttributeError) as exc:
        raise Blocked('无法核验受保护目录的硬链接：'+str(exc)) from None
    finally:
        while held:os.close(held.pop())

def permission_profile_args(paths,write,*,select_default=True):
    if not isinstance(paths,(list,tuple)) or len(paths)>512 or type(write) is not bool:raise Blocked('原生权限保护路径无效')
    clean=[]
    for value in paths:
        if not isinstance(value,str) or not value.startswith('/') or value=='/' or '..' in Path(value).parts or len(value)>4096 or any(ord(c)<32 or ord(c)==127 or 0xd800<=ord(c)<=0xdfff for c in value):
            raise Blocked('原生权限保护路径无效')
        clean.append(value)
    # A nested deny is redundant and Codex's sandbox helper cannot always
    # create a child placeholder after its denied parent has been mounted.
    # Compare lexical components only: never resolve symlinks or widen scope.
    roots=[]
    for path in sorted({Path(value) for value in clean},key=lambda p:(len(p.parts),str(p))):
        if not any(path.is_relative_to(parent) for parent in roots):roots.append(path)
    entries=['"/"="read"', '":workspace_roots"='+_toml('write' if write else 'read')]
    entries.extend(json.dumps(str(path),ensure_ascii=False)+'="deny"' for path in roots)
    filesystem='{'+','.join(entries)+'}'
    if len(filesystem)>100000:raise Blocked('原生权限保护路径超出预算')
    args=['-c','permissions.devflower.filesystem='+filesystem,'-c','permissions.devflower.network.enabled=false']
    if select_default:args+=['-c','default_permissions="devflower"']
    return args

def managed_mcp_args(context):
    from .managed_children import ManagedContext
    if type(context) is not ManagedContext or context.closed.is_set():raise Blocked('受管子任务上下文无效')
    import time
    remaining=int(context.deadline-time.monotonic())
    if remaining<1:raise Blocked('受管子任务启动预算已耗尽')
    script=Path(__file__).resolve().parents[1]/'devflower_managed_child.py'
    args=['-B',str(script),str(context.path)]
    return ['-c','mcp_servers.devflower_managed.command="/usr/bin/python3"',
            '-c','mcp_servers.devflower_managed.args='+json.dumps(args,ensure_ascii=True),
            '-c','mcp_servers.devflower_managed.enabled=true',
            '-c','mcp_servers.devflower_managed.required=true',
            '-c','mcp_servers.devflower_managed.enabled_tools=["delegated_readonly"]',
            '-c','mcp_servers.devflower_managed.startup_timeout_sec='+str(min(remaining,10)),
            '-c','mcp_servers.devflower_managed.tool_timeout_sec='+str(remaining)]

def exec_args(binding,provider_args,*,schema=None):
    read_only=binding['role']!='implementer' or bool(binding.get('admission_probe'))
    ordinary=binding.get('native_interaction')=='ordinary'
    args=[binding['executable'],'exec','--ignore-user-config','--ignore-rules','--strict-config','--json','--skip-git-repo-check',
          '-c','approval_policy="never"']
    if ordinary:
        if not binding.get('native_protected'):raise Blocked('原生权威和认证保护路径未绑定；未启动会话')
        args+=permission_profile_args(binding['native_protected'],not read_only)
    else:
        args+=['-c','sandbox_mode='+_toml('read-only' if read_only else 'workspace-write'),
               '-c','sandbox_workspace_write.exclude_slash_tmp=true','-c','sandbox_workspace_write.exclude_tmpdir_env_var=true',
               '-c','sandbox_workspace_write.writable_roots=[]','-c','sandbox_workspace_write.network_access=false']
    args+=['-c','web_search="disabled"','-c','check_for_update_on_startup=false',*provider_args]
    features=dict(FEATURES)
    if binding.get('admission_probe') or binding.get('no_tools'):
        features.update(shell_tool=False,code_mode_host=False,unified_exec=False)
    for key,value in features.items():args+=['-c','features.'+key+'='+_toml(value)]
    if not isinstance(binding.get('model'),str) or not binding['model'] or binding['model']=='unknown':raise Blocked('原生模型身份不明确')
    if not isinstance(binding.get('effort'),str) or not binding['effort'] or binding['effort']=='unknown':raise Blocked('原生推理强度不明确')
    args+=['--model',binding['model'],'-c','model_reasoning_effort='+_toml(binding['effort'])]
    if schema is not None:args+=['--output-schema',str(schema)]
    return args+['-']
