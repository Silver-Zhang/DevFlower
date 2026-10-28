"""Verify original pinned config before allowing model-only preference drift.

The baseline is kept in memory and passed to owned workers via an anonymous
file descriptor, never persisted or substituted for the operator's pin.
"""
import os,stat,tomllib
from contextlib import contextmanager
from pathlib import Path
from orchestration.common import digest
from .session_sources import opened

ENV='DEVFLOWER_PINNED_CONFIG_FD'
LIMIT=200000

def canonical_path(operator):
    local=operator.get('native_local',{}).get('codex',{})
    if not isinstance(local,dict) or not local.get('config_root'):return None
    return str(Path(local['config_root'])/'config.toml')

def original(operator):
    path=canonical_path(operator);pin=operator.get('dependencies',{}).get(path)
    if not path or not pin:return None
    descriptor=os.environ.get(ENV)
    if descriptor:
        try:
            fd=int(descriptor);info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>LIMIT:return None
            raw=os.pread(fd,LIMIT+1,0)
        except (ValueError,OSError):return None
        finally:
            os.environ.pop(ENV,None)
            try:os.close(fd)
            except (OSError,UnboundLocalError):pass
    else:
        try:
            with opened(path) as fd:raw=os.read(fd,LIMIT+1)
        except (OSError,ValueError):return None
    if len(raw)>LIMIT or digest(raw)!=pin:return None
    try:parsed=tomllib.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeDecodeError):return None
    return dict(path=path,pin=pin,raw=raw,security=security_fields(parsed))

def security_fields(parsed):
    value=dict(parsed)
    value.pop('model',None);value.pop('model_reasoning_effort',None)
    return value

def preference_only(baseline,path):
    if not baseline or path!=baseline['path']:return False
    try:
        with opened(path) as fd:raw=os.read(fd,LIMIT+1)
        if len(raw)>LIMIT:return False
        parsed=tomllib.loads(raw.decode('utf-8'))
        if any(k in parsed and (not isinstance(parsed[k],str) or not parsed[k]) for k in ('model','model_reasoning_effort')):return False
        return security_fields(parsed)==baseline['security']
    except (OSError,ValueError,UnicodeDecodeError):return False

@contextmanager
def worker_baseline(runtime,env):
    # Owned controller workers must protect exactly the same host settings as
    # the server, even when clean_env drops HOME/XDG launch preferences. Native
    # Agent adapters subsequently construct their own clean environment.
    settings=getattr(runtime,'assistant_settings',None)
    if settings is not None:env['DEVFLOWER_SETTINGS_HOME']=str(settings)
    baseline=getattr(runtime,'preference_baseline',None)
    if not baseline:
        yield ()
        return
    fd=os.memfd_create('devflower-config-baseline',os.MFD_CLOEXEC)
    try:
        os.write(fd,baseline['raw']);os.lseek(fd,0,os.SEEK_SET);env[ENV]=str(fd)
        yield (fd,)
    finally:
        env.pop(ENV,None);os.close(fd)
