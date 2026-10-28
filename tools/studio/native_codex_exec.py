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
    def onerror(exc):raise Blocked('无法核验受保护目录的硬链接：'+str(exc))
    def snapshot():
        entries={};groups={};count=0
        def add(path,s,parent_identity,*,root=False):
            nonlocal count
            count+=1
            if time.monotonic()>deadline or count>200000:
                raise Blocked('受保护目录检查超过时间或文件数量限制；未启动任务。')
            key=str(path);mode=s.st_mode
            # Contents can change normally; link metadata of shared inodes must
            # remain stable, including an unlink/relink with the same count.
            if root or stat.S_ISDIR(mode) or stat.S_ISLNK(mode) or s.st_nlink>1:
                entries[key]=(s.st_dev,s.st_ino,mode,None if stat.S_ISDIR(mode) else s.st_nlink,s.st_ctime_ns if s.st_nlink>1 and not stat.S_ISDIR(mode) else None)
            if not stat.S_ISDIR(mode) and s.st_nlink>1:
                if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):raise Blocked('受保护特殊文件存在硬链接；未启动任务。')
                group=groups.setdefault((s.st_dev,s.st_ino),{'names':set(),'paths':[]})
                group['names'].add((*parent_identity,path.name));group['paths'].append(key)
        for root in unique:
            try:s=os.lstat(root)
            except FileNotFoundError:entries[str(root)]=None;continue
            if stat.S_ISLNK(s.st_mode):raise Blocked('受保护目录含符号链接，无法确认边界；未启动任务。')
            parent=os.stat(root.parent,follow_symlinks=False)
            add(root,s,(parent.st_dev,parent.st_ino),root=True)
            if stat.S_ISDIR(s.st_mode):
                for folder,dirs,files,fd in os.fwalk(root,topdown=True,follow_symlinks=False,onerror=onerror):
                    current=os.fstat(fd);previous=entries.get(str(folder))
                    if not previous or (current.st_dev,current.st_ino)!=(previous[0],previous[1]):raise Blocked('受保护目录在检查期间变化；未启动任务。')
                    for name in dirs+files:
                        try:entry=os.stat(name,dir_fd=fd,follow_symlinks=False)
                        except FileNotFoundError:
                            # SQLite removes its transient WAL/SHM while an
                            # independent connection checkpoints and closes.
                            if name in files and name.endswith(('.sqlite-wal','.sqlite-shm','.db-wal','.db-shm')):continue
                            raise
                        add(Path(folder)/name,entry,(current.st_dev,current.st_ino))
        return entries,groups
    def resample():
        # A non-WAL single-link file may also disappear after readdir. Retry
        # only that incomplete scan, preserving any completed earlier scan.
        for attempt in range(3):
            try:return snapshot()
            except FileNotFoundError:
                if attempt==2:raise
    try:
        first,groups=resample();second,_=resample()
        if first!=second:raise Blocked('受保护目录在检查期间变化；未启动任务。')
        for group in groups.values():
            if any(first[name][3]!=len(group['names']) for name in group['paths']):
                raise Blocked('受保护文件存在未确认归属的硬链接；未启动任务。')
    except OSError as exc:
        raise Blocked('无法核验受保护目录的硬链接：'+str(exc)) from None

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
