#!/usr/bin/env python3
"""Advanced read-only native resume in the original managed environment."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal
import threading
import time
sys.dont_write_bytecode=True
from orchestration.common import Blocked,clean_env,digest,workspace_metadata
from orchestration.store import lease, process_identity
from orchestration.sandbox import runtime_paths
from orchestration.provider_transport import claude_transport
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_scheduler import source_lease



def bind_parent_lifetime(parent, expected_start=None):
    """Watch the server process, not its short-lived HTTP request thread.

    Linux PDEATHSIG follows the creating thread's lifetime. A request thread may
    end immediately after Popen; pin the real server identity instead, and stop
    only this terminal's separately created process group if it disappears.
    """
    owner=process_identity(parent)
    if (not owner or owner['state']=='Z' or os.getppid()!=parent or
            expected_start is not None and owner['start']!=expected_start or
            os.getpgrp()!=os.getpid() or os.getsid(0)!=os.getpid()):
        raise Blocked('原生终端监督器身份未确认，未启动CLI')
    identity={k:owner[k] for k in ('pid','start','boot_id')}
    def watch():
        while True:
            current=process_identity(parent)
            if (not current or current['state']=='Z' or
                    any(current[k]!=v for k,v in identity.items())):
                os.killpg(os.getpgrp(),signal.SIGTERM)
                return
            time.sleep(.2)
    threading.Thread(target=watch,daemon=True,name='native-terminal-owner').start()



def native_cli_argv(binding,sid):
    if binding['provider']=='codex':
        # The mandatory OUTER wrapper is read-only. A second native bwrap tries
        # to build writable runtime paths inside it and cannot run read tools.
        # This matches the already-probed external-sandbox Codex execution mode;
        # it does not grant filesystem writes outside HOME/private scratch.
        return [binding['executable'],'resume',sid,'--model',binding['model'],'--no-alt-screen',
                '--sandbox','danger-full-access','--ask-for-approval','never',
                '-c','features.multi_agent=false','-c','check_for_update_on_startup=false',
                '-c','model_reasoning_effort='+json.dumps(binding['effort'])]
    if binding['provider']=='claude':
        return [binding['executable'],'--resume',sid,'--model',binding['model'],'--effort',binding['effort'],
                '--permission-mode','plan','--tools','Read,Glob,Grep','--setting-sources','user',
                '--settings','{"permissions":{"defaultMode":"plan","disableAutoMode":"disable","disableBypassPermissionsMode":"disable"}}',
                '--strict-mcp-config','--mcp-config','{"mcpServers":{}}']
    raise Blocked('此 Provider 尚不支持原生终端')


def wrap_native_cli(binding,cwd,home,scratch,argv,readonly_dirs=(),hidden_paths=()):
    """Internal fixed native CLI only; HTTP never supplies argv or mount paths."""
    home=Path(home)
    wrapped=['/usr/bin/bwrap','--die-with-parent','--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts','--cap-drop','ALL','--proc','/proc','--dev','/dev','--tmpfs','/tmp']
    paths=sorted(set([str(cwd),*binding['read_paths'],*[x for x in runtime_paths() if not x.startswith(('/proc/','/dev/'))]]),key=lambda x:(len(Path(x).parts),x))
    for path in paths:
        if Path(path).exists():wrapped+=['--ro-bind',str(Path(path).resolve()),path]
    wrapped+=['--bind',str(home),str(home),'--bind',str(scratch),str(scratch)]
    for source,relative in binding.get('readonly_files',[]):
        dest=home/relative
        if dest.is_symlink() or not dest.resolve().is_relative_to(home.resolve()):raise Blocked('凭据挂载路径不安全')
        dest.parent.mkdir(parents=True,exist_ok=True);wrapped+=['--ro-bind',source,str(dest)]
    for source,relative in readonly_dirs:
        dest=home/relative
        if relative not in ('.agents/skills','.codex/skills','.claude/skills') or not dest.resolve().is_relative_to(home.resolve()) or dest.is_symlink():raise Blocked('Skill 挂载位置不安全')
        dest.mkdir(parents=True,exist_ok=True);wrapped+=['--ro-bind',source,str(dest)]
    for path in hidden_paths:wrapped+=['--tmpfs',path,'--remount-ro',path]
    metadata=workspace_metadata(cwd,check_git=True)
    if metadata:wrapped+=['--tmpfs',metadata,'--remount-ro',metadata]
    return wrapped+['--remount-ro','/','--remount-ro','/tmp','--chdir',str(cwd),'--',*argv]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('operator','operator-sha256','candidate-sha256','policy','policy-sha256','workspace','handle'):p.add_argument('--'+key,required=True)
    a=p.parse_args();rt=WorkbenchRuntime(a.operator,a.operator_sha256,a.candidate_sha256,a.policy,a.policy_sha256)
    parent=os.environ.get('DEVFLOWER_TERMINAL_PARENT_PID')
    if parent:
        # Popen created a new session with this PTY on stdin. Do this in the
        # child main thread, not an unsafe preexec hook in the HTTP server.
        import fcntl,termios
        fcntl.ioctl(sys.stdin.fileno(),termios.TIOCSCTTY,0)
        bind_parent_lifetime(int(parent),os.environ.get('DEVFLOWER_TERMINAL_PARENT_START'))
    if not sys.stdin.isatty() or not sys.stdout.isatty():raise Blocked('请在本机交互终端打开；浏览器不会启动第二个会话写入者')
    rt.check_operator();project,row=rt.session_center.resolve(a.workspace,a.handle)
    binding,reason=rt.session_center._binding(row,project)
    if not binding or WorkbenchSessions(rt).busy(binding,row['native']['session_id']):raise Blocked(reason or '会话占用状态未确认')
    cwd=row['native']['cwd'];home=Path(binding['home']);sid=row['native']['session_id']
    selected=rt.node_binding(a.workspace,dict(profile=binding['runtime_profile'],model=binding['model'],
        effort=binding['effort'],mode='Ask',permission='read',skills=binding.get('skill_ids',[]),subagents=binding.get('subagents','disabled')))
    binding=dict(binding,skills=selected['binding'].get('skills',[]),subagents=selected['binding'].get('subagents','disabled'),execution_mode='Ask',role='reviewer',isolation='bwrap')
    from orchestration.native_features import native_args,skill_mounts
    extension_args,extension_env=native_args(binding)
    argv=native_cli_argv(binding,sid)+extension_args
    transport=(nullcontext(([],{})) if binding['provider']=='codex' else claude_transport(binding,timeout=3600))
    with source_lease(rt.source_lock(cwd),False),lease(home.parent/(digest(str(home.resolve()).encode())+'.session.lock')),tempfile.TemporaryDirectory(prefix='df-terminal-') as scratch,tempfile.TemporaryDirectory(prefix='df-skills-') as pack_root,transport as (extra,_):
        # All runtime/source mounts are readonly; only this native HOME and the
        # private temporary directory are writable. No PTY is attached in HTTP.
        rt.check_operator();rt.session_center.resolve(a.workspace,a.handle)
        from studio.model_admission import ModelAdmission
        verification=ModelAdmission(rt).require(selected['binding'],cwd)
        from studio.native_preferences import inherit_claude_display
        inherit_claude_display(binding,rt.native)
        mounts,masks=skill_mounts(binding,cwd,pack_root)
        wrapped=wrap_native_cli(binding,cwd,home,scratch,[*argv,*extra],mounts,masks)
        env=clean_env(home);env.update(extension_env);env['TMPDIR']=scratch;env['TERM']=os.environ.get('TERM','xterm-256color')
        code=subprocess.call(wrapped,cwd=cwd,env=env)
        if code:
            ModelAdmission(rt).invalidate_verification(selected['binding'],cwd,verification)
        return code


if __name__=='__main__':
    try:sys.exit(main())
    except (Blocked,OSError,ValueError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
