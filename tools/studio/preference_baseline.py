"""Verify original pinned config before allowing model-only preference drift.

The baseline is kept in memory and passed to owned workers via an anonymous
file descriptor, never persisted or substituted for the operator's pin.
"""
import datetime,os,stat,tomllib
from contextlib import contextmanager
from pathlib import Path
from orchestration.common import canonical,digest
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
    try:security=security_fields(parsed,desktop_detail=True)
    except ValueError:return None
    return dict(path=path,pin=pin,raw=raw,security=security)

def security_fields(parsed, *, desktop_detail=False):
    value=dict(parsed)
    for key in ('model','model_reasoning_effort'):
        if key in value and (not isinstance(value[key],str) or not value[key].strip()):raise ValueError('invalid preference')
        value.pop(key,None)
    if desktop_detail and 'desktop' in value:
        desktop=value['desktop']
        if not isinstance(desktop,dict):raise ValueError('invalid desktop table')
        desktop=dict(desktop)
        if 'conversationDetailMode' in desktop:
            if desktop['conversationDetailMode'] not in ('STEPS_PROSE','STEPS_COMMANDS','STEPS_EXECUTION'):
                raise ValueError('invalid conversation detail mode')
            desktop.pop('conversationDetailMode')
        if desktop:value['desktop']=desktop
        else:value.pop('desktop')
    return value

def security_identity(value):
    # Preserve TOML types: Python considers True equal to 1 and 1 equal to 1.0.
    def typed(item):
        if isinstance(item,dict):return ['table',[[k,typed(v)] for k,v in sorted(item.items())]]
        if isinstance(item,list):return ['array',[typed(v) for v in item]]
        if isinstance(item,(datetime.datetime,datetime.date,datetime.time)):return [type(item).__name__,item.isoformat()]
        if isinstance(item,float):return ['float',item.hex()]
        return [type(item).__name__,item]
    return digest(canonical(typed(value)))

def preference_only(baseline,path):
    if not baseline or path!=baseline['path']:return False
    try:
        with opened(path) as fd:raw=os.read(fd,LIMIT+1)
        if len(raw)>LIMIT:return False
        parsed=tomllib.loads(raw.decode('utf-8'))
        return security_identity(security_fields(parsed,desktop_detail=True))==security_identity(baseline['security'])
    except (OSError,ValueError,UnicodeDecodeError,RecursionError,TypeError):return False

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
