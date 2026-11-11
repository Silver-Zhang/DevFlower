"""Operator-approved native named settings for model and effort selection."""
import json
import os
from pathlib import Path
import re
import tomllib

from orchestration.common import Blocked, digest
from studio.graph import Invalid
from studio.agent_sessions import safe_text
from studio.session_sources import opened

from .claude_native_catalog import model_id
from .native_history_config import private_bytes, unique_pairs
from .project_files import FileView, mount_id
from .session_identity import StorageRootIdentity, session_path, verified_directory

PROFILE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')
EFFORT = re.compile(r'[a-z][a-z0-9_-]{0,23}')
LIMIT = 64*1024


def approved_paths(options, home):
    if len(options)>16:
        raise Invalid('最多登记 16 个原生命名配置。')
    result = {}
    for option in options:
        provider,_,value = option.partition(':')
        name,separator,file = value.partition('=')
        if (provider not in ('codex','claude') or not PROFILE_ID.fullmatch(name)
                or name=='default' or (provider,name) in result):
            raise Invalid('原生命名配置的 Provider 或名称无效。')
        root = Path(home) / ('.'+provider)
        if provider=='codex' and not separator:
            path = root / (name+'.config.toml')
        elif provider=='claude' and separator:
            path = session_path(file)
            if path.name in ('.credentials.json','settings.local.json') or path==root/'settings.json' or not path.is_relative_to(root) or path.suffix!='.json':
                raise Invalid('Claude 命名配置需要原生配置目录中的独立 JSON 设置文件。')
        else:
            raise Invalid('Codex 使用已登记名称，Claude 使用已登记名称和独立设置路径。')
        result[provider,name] = str(path)
    return result


def read_profile(provider, name, path, root):
    expected = Path(root)
    path = Path(path)
    if (provider not in ('codex','claude') or not PROFILE_ID.fullmatch(name)
            or not path.is_relative_to(expected)
            or provider=='codex' and path!=expected/(name+'.config.toml')
            or provider=='claude' and (path.name in ('.credentials.json','settings.local.json')
                                       or path==expected/'settings.json' or path.suffix!='.json')):
        raise Blocked('原生命名配置的来源无法核验。')
    try:
        root_id = StorageRootIdentity.capture(expected)
        with verified_directory(path.parent) as directory:
            identity = StorageRootIdentity.from_fd(directory)
            if identity.mount_id!=root_id.mount_id or os.fstat(directory).st_uid!=os.geteuid():
                raise ValueError()
            raw = private_bytes(FileView(directory,identity.mount_id),directory,path.name,LIMIT)
        with opened(path.parent,directory=True) as current:
            if StorageRootIdentity.from_fd(current)!=identity or StorageRootIdentity.capture(expected)!=root_id:
                raise ValueError()
        if provider=='codex':
            config = tomllib.loads(raw.decode('utf-8'))
            if set(config)-{'model','model_reasoning_effort'}:
                raise ValueError()
            model, effort = config.get('model'),config.get('model_reasoning_effort')
        else:
            config = json.loads(raw,object_pairs_hook=unique_pairs)
            if not isinstance(config,dict) or set(config)-{'model','effortLevel','env'}:
                raise ValueError()
            env = config.get('env',{})
            if not isinstance(env,dict) or set(env)-{'ANTHROPIC_MODEL','CLAUDE_CODE_EFFORT_LEVEL'}:
                raise ValueError()
            model, effort = env.get('ANTHROPIC_MODEL',config.get('model')),env.get('CLAUDE_CODE_EFFORT_LEVEL',config.get('effortLevel'))
        from .data_handoff import task_literals
        if (model is None and effort is None or model is not None and (
                not model_id(model,task_literals()) or safe_text(model)!=model)
                or effort is not None and (not isinstance(effort,str) or not EFFORT.fullmatch(effort) or effort=='ultra')):
            raise ValueError()
        return dict(id=name,provider=provider,path=str(path),sha256=digest(raw),model=model,effort=effort)
    except (OSError,ValueError,TypeError,RecursionError,Blocked):
        raise Blocked('原生命名配置不可用。请核对私有权限及模型、推理强度设置。') from None


def require_frozen_profile(binding):
    profile = binding.get('native_profile')
    if profile and read_profile(binding['provider'],profile['id'],profile['path'],binding['config_root'])!=profile:
        raise Blocked('原生命名配置已变化，未发送任务。')


def public_profile(profile):
    return {key:profile[key] for key in ('id','provider','model','effort')}
