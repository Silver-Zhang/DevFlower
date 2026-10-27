"""Host-approved local native roots and per-conversation ownership.

Local mode shares canonical Provider state; it is not a history migration.
Legacy dedicated homes keep their original execution and discovery semantics.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import time

from orchestration.common import Blocked, canonical, digest
from orchestration.store import lease
from .session_sources import clean_path, opened, UUID


def configured(runtime, provider):
    value = runtime.operator.get('native_local', {}).get(provider)
    if value is None: return None
    if provider not in ('codex','claude') or not isinstance(value,dict) or set(value) != {'home','config_root'}:
        raise Blocked('本机原生配置需要明确的 Provider、HOME 与会话根')
    home, root = map(clean_path, (value['home'], value['config_root']))
    if root != str(Path(home)/('.'+provider)):
        raise Blocked('本机原生会话根必须与 HOME 绑定；不进行复制或目录迁移')
    for path in (home, root):
        with opened(path,directory=True) as fd:
            if os.fstat(fd).st_uid != os.getuid(): raise Blocked('原生根目录不属于当前本机用户')
    return dict(home=home, config_root=root, execution_backend='native-local',
                local_lock_root=str(runtime.storage/'native-session-locks'))


def bind(runtime, profile, workspace):
    value = configured(runtime, profile['provider'])
    if value is None: return None
    return dict(profile, **value, session_id=None, isolation='native-local', external_sandbox=False,
                project=workspace, readonly_files=profile.get('readonly_files',[]))


def preferred_profile(runtime,provider):
    name='default-'+provider
    if name in runtime.operator['profiles'] and runtime.operator['profiles'][name]['provider']==provider:
        return name,runtime.operator['profiles'][name]
    choices=[(k,p) for k,p in runtime.operator['profiles'].items() if p['provider']==provider]
    if len(choices)!=1:raise Blocked('本机原生环境需要明确的默认 Agent 配置，不能猜测 Profile')
    return choices[0]


def key(binding, sid=None):
    return digest(canonical([binding['provider'],binding.get('config_root'),
                            binding['home'],sid if sid is not None else binding.get('session_id')]))


def profile_key(binding):
    return key(binding) if binding.get('execution_backend')=='native-local' else digest(binding['home'].encode())


@contextmanager
def session_lease(binding):
    if binding.get('execution_backend') != 'native-local':
        home=Path(binding['home'])
        with lease(home.parent/(digest(str(home.resolve()).encode())+'.session.lock')): yield
        return
    root=Path(binding['local_lock_root']);root.mkdir(mode=0o700,exist_ok=True)
    # A new conversation has no common identity yet. An independent nonce keeps
    # unrelated conversations in the same HOME from excluding each other.
    sid=binding.get('session_id') or 'new:'+secrets.token_hex(16)
    with lease(root/(key(binding,sid)+'.lock')): yield


def native_busy(binding, sid):
    if not sid: return False
    if not UUID.fullmatch(sid): raise Blocked('需要明确原生会话 UUID')
    if binding['provider']=='codex':
        # This is the native store's own lock, also used by Desktop. No file is
        # created, replaced or unlocked on another process's behalf.
        path=Path(binding['config_root'])/'thread-writer-locks'/(sid+'.lock')
        try: fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
        except FileNotFoundError:return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise Blocked('原生写锁类型未确认')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
            fcntl.flock(fd,fcntl.LOCK_UN);return False
        finally:os.close(fd)
    # Claude's supported CLI is inspected for this exact UUID, never all CLI
    # processes sharing the user's HOME. The workflow lock adds own-client CAS.
    for path in Path('/proc').glob('[0-9]*'):
        try:
            args=(path/'cmdline').read_bytes().split(b'\0')
            if sid.encode() in args:
                from orchestration.store import process_identity
                p=process_identity(int(path.name))
                if p and p['state']!='Z':return True
        except (OSError,ValueError):continue
    return False


def own_busy(runtime, binding, sid):
    for row in runtime.db.listing('session-operation',filters={'binding.home':binding['home']},limit=None):
        current=row.get('native_session') or row.get('requested_session') or row['binding'].get('session_id')
        if current==sid and row['state'] not in ('COMPLETED','FAILED','CANCELLED'):
            return True
    root=Path(binding['local_lock_root']);path=root/(key(binding,sid)+'.lock')
    if path.exists():
        try:
            with lease(path):pass
        except Blocked:return True
    return native_busy(binding,sid)


def codex_history(binding, cwd, sid, limit=200, query='', *, turn_id=None):
    from .native_codex_rpc import metadata
    value=metadata(binding,cwd,'thread/read',dict(threadId=sid,includeTurns=True))['thread']
    if value['id']!=sid or value['cwd']!=cwd:raise Blocked('原生对话与当前工作区身份不一致')
    rows=[]
    for turn in value.get('turns',[]):
        if turn_id and turn['id']!=turn_id:continue
        for item in turn.get('items',[]):
            if item['type']=='userMessage':
                text='\n'.join(v.get('text','') for v in item.get('content',[]) if v.get('type')=='text')
                role='user'
            elif item['type']=='agentMessage':text=item.get('text','');role='assistant'
            else:continue  # Reasoning is never reconstructed from native data.
            if not query or query.casefold() in text.casefold():
                rows.append(dict(role=role,text=text,turn_id=turn['id'],native_item=item['id']))
    return rows[-limit:]


def profiles(runtime, project, existing):
    """Scope canonical-store discovery to the opened workspace, not all history."""
    result=list(existing)
    for provider in runtime.operator.get('native_local',{}):
        cfg=configured(runtime,provider)
        _,profile=preferred_profile(runtime,provider)
        binding=dict(profile,**cfg)
        cache=getattr(runtime,'native_local_index',None)
        if cache is None:runtime.native_local_index={};cache=runtime.native_local_index
        cache_key=(provider,project['path']);prior=cache.get(cache_key)
        if not prior or time.monotonic()-prior[0]>2:
            if provider=='codex':
                from .native_codex_rpc import metadata
                try:
                    values=metadata(binding,project['path'],'thread/list',dict(cwd=project['path'],limit=200,
                        sourceKinds=['cli','vscode','exec','appServer'],sortKey='updated_at'))['data']
                    ids=[v['id'] for v in values if v.get('cwd')==project['path']]
                    runtime.native_local_errors={k:v for k,v in getattr(runtime,'native_local_errors',{}).items() if k!=cache_key}
                except (Blocked,OSError,ValueError,KeyError,TypeError):
                    if not hasattr(runtime,'native_local_errors'):runtime.native_local_errors={}
                    runtime.native_local_errors[cache_key]='原生目录查询失败；当前仅显示已知会话，覆盖范围不完整。请检查原生工具并刷新。'
                    ids=list(prior[1]) if prior else []
            else:
                import re
                folder=re.sub(r'[^a-zA-Z0-9]','-',project['path'])
                root=Path(cfg['config_root'])/'projects'/folder
                ids=[p.stem for p in root.glob('*.jsonl') if UUID.fullmatch(p.stem)][:200]
                # Exact managed native IDs handle Provider directory encodings.
                ids += [r['binding']['session_id'] for r in runtime.db.listing('chat-profile',filters={'workspace':project['id']},limit=None)
                        if r['binding'].get('provider')==provider and r['binding'].get('session_id')]
            ids=list(dict.fromkeys(ids))[:200];cache[cache_key]=(time.monotonic(),ids)
        else:ids=list(prior[1])
        ids=list(dict.fromkeys([*ids,*[r['binding']['session_id']
            for r in runtime.db.listing('chat-profile',filters={'workspace':project['id']},limit=None)
            if r['binding'].get('provider')==provider and r['binding'].get('home')==cfg['home'] and r['binding'].get('session_id')]]))[:200]
        # A second unfiltered profile of the same canonical root could read
        # unrelated conversations or produce duplicate handles.
        result=[p for p in result if not (p['provider']==provider and p.get('home')==cfg['home'])]
        if ids:result.append(dict(provider=provider,home=cfg['home'],config_root=cfg['config_root'],
            workspace=project['path'],original_project_root=project['path'],session_ids=ids))
    return result
