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
        if binding.get('execution_backend')=='native-local':
            if not binding.get('native_interactive'):
                return [binding['executable'],'--no-daemon','resume',sid,'--model',binding['model'],
                        '--no-alt-screen','--sandbox','read-only','--ask-for-approval','never',
                        '-c','features.multi_agent=false','-c','check_for_update_on_startup=false',
                        '-c','model_reasoning_effort='+json.dumps(binding['effort'])]
            from studio.native_codex_exec import permission_profile_args
            protected=binding.get('native_protected')
            if not protected:raise Blocked('原生终端的权威保护路径未绑定；未启动CLI')
            return [binding['executable'],'--no-daemon','resume',sid,'--model',binding['model'],
                    '--no-alt-screen',*permission_profile_args(protected,True),
                    '-c','model_reasoning_effort='+json.dumps(binding['effort'])]
        # The mandatory OUTER wrapper is read-only. A second native bwrap tries
        # to build writable runtime paths inside it and cannot run read tools.
        # This matches the already-probed external-sandbox Codex execution mode;
        # it does not grant filesystem writes outside HOME/private scratch.
        return [binding['executable'],'resume',sid,'--model',binding['model'],'--no-alt-screen',
                '--sandbox','danger-full-access','--ask-for-approval','never',
                '-c','features.multi_agent=false','-c','check_for_update_on_startup=false',
                '-c','model_reasoning_effort='+json.dumps(binding['effort'])]
    if binding['provider']=='claude':
        if binding.get('execution_backend')=='native-local' and binding.get('native_interactive'):
            # Until per-path Claude tool rules have been mechanically verified,
            # offer conversation without filesystem/shell tools or external MCP.
            return [binding['executable'],'--resume',sid,'--model',binding['model'],'--effort',binding['effort'],
                    '--permission-mode','plan','--tools','',
                    '--settings','{"permissions":{"defaultMode":"plan","disableAutoMode":"disable","disableBypassPermissionsMode":"disable"}}',
                    '--strict-mcp-config','--mcp-config','{"mcpServers":{}}']
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
    p.add_argument('--terminal-id')
    p.add_argument('--assistant-settings')
    a=p.parse_args();rt=WorkbenchRuntime(a.operator,a.operator_sha256,a.candidate_sha256,a.policy,a.policy_sha256,assistant_settings=a.assistant_settings)
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
    record=rt.flow.get('terminal',a.terminal_id) if a.terminal_id else None
    direct=bool(record and record.get('interaction')=='click' and record.get('workspace')==a.workspace and record.get('handle')==a.handle and record.get('session_id')==sid and record.get('state') in ('STARTING','RUNNING'))
    if binding.get('execution_backend')=='native-local' and binding['provider']=='codex' and not (direct and parent):
        raise Blocked('本机 Codex 终端需要工作台内明确接管；旧授权不自动改变。')
    selected=rt.node_binding(a.workspace,dict(profile=binding['runtime_profile'],model=binding['model'],
        effort=binding['effort'],mode='Ask',permission='read',skills=binding.get('skill_ids',[]),subagents=binding.get('subagents','disabled')),admission=not direct,direct=direct,
        direct_environment_version=record.get('execution_environment_version',1) if direct else 2)
    if direct and (record.get('native_sha256') != row['native']['sha256'] or record.get('execution_environment_sha256') != selected['binding'].get('direct_environment_sha256')):
        raise Blocked('原生会话或配置在终端启动后发生变化，未启动CLI')
    binding=dict(binding,skills=selected['binding'].get('skills',[]),subagents=selected['binding'].get('subagents','disabled'),execution_mode='Ask',role='reviewer',isolation='native-local' if binding.get('execution_backend')=='native-local' else 'bwrap',native_interactive=direct)
    if binding.get('execution_backend')=='native-local' and binding['provider']=='codex' and direct:
        from studio.native_codex_exec import protected_paths
        binding['native_protected']=protected_paths(rt,binding)
    from orchestration.native_features import native_args,skill_mounts
    extension_args,extension_env=([],{}) if binding.get('execution_backend')=='native-local' and direct else native_args(binding)
    argv=native_cli_argv(binding,sid)+extension_args
    native_local=binding.get('execution_backend')=='native-local'
    transport=(nullcontext(([],{})) if native_local or binding['provider']=='codex' else claude_transport(binding,timeout=3600))
    from studio.native_local import session_lease
    with source_lease(rt.source_lock(cwd),False),session_lease(binding),tempfile.TemporaryDirectory(prefix='df-terminal-') as scratch,tempfile.TemporaryDirectory(prefix='df-skills-') as pack_root,transport as (extra,_):
        # All runtime/source mounts are readonly; only this native HOME and the
        # private temporary directory are writable. No PTY is attached in HTTP.
        rt.check_operator();_,current=rt.session_center.resolve(a.workspace,a.handle)
        if current['native']['sha256']!=row['native']['sha256']:raise Blocked('原生历史在获取写锁前发生变化；未启动终端')
        if native_local and binding['provider']=='claude':
            from studio.native_local import require_safe_resume
            require_safe_resume(binding,runtime=rt,cwd=cwd,native_sha=current['native']['sha256'])
            if direct:
                try:
                    (Path(scratch)/'tmp').mkdir(mode=0o700,exist_ok=True)
                    check=subprocess.run([binding['executable'],'--help'],cwd=scratch,env=clean_env(scratch),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
                    helptext=check.stdout.decode('utf-8','replace')
                    if check.returncode or any(option not in helptext for option in ('--strict-mcp-config','--mcp-config','--tools','--permission-mode','--settings')):
                        raise Blocked('本机 Claude CLI 不支持所需的终端工具限制；未启动终端。')
                except subprocess.TimeoutExpired:raise Blocked('本机 Claude CLI 工具能力检查超时；未启动终端。') from None
        if native_local and binding['provider']=='codex':
            from studio.native_codex_exec_probe import require_exec_capability
            require_exec_capability(binding['executable'],scratch)
            if direct:
                from studio.native_codex_exec_probe import require_native_write_capability
                require_native_write_capability(binding['executable'],scratch,True)
                rt.check_operator()
                _,after_probe=rt.session_center.resolve(a.workspace,a.handle)
                if after_probe['native']['sha256']!=row['native']['sha256']:
                    raise Blocked('原生历史在终端能力检查期间发生变化；未启动CLI')
                if record.get('source_view') and rt.source_view_hash(cwd,record['source_view'])!=record.get('source_sha256'):
                    raise Blocked('工作区在终端能力检查期间变化；未启动CLI')
                from studio.native_codex_exec import check_protected_hardlinks
                from studio.native_codex_rpc import metadata_guard
                with metadata_guard(rt.storage):
                    check_protected_hardlinks(binding['native_protected'])
        from studio.model_admission import ModelAdmission
        if direct:
            from studio.model_registration import ModelRegistration
            ModelRegistration(rt).require_direct(selected['binding'])
            verification=None
        else:
            verification=ModelAdmission(rt).require(selected['binding'],cwd)
        from studio.native_preferences import inherit_claude_display
        if native_local:
            wrapped=[*argv,*extra]
        else:
            inherit_claude_display(binding,rt.native)
            mounts,masks=skill_mounts(binding,cwd,pack_root)
            wrapped=wrap_native_cli(binding,cwd,home,scratch,[*argv,*extra],mounts,masks)
        env=clean_env(home);env.update(extension_env);env['TMPDIR']=scratch;env['TERM']=os.environ.get('TERM','xterm-256color')
        if native_local:
            from studio.native_local import native_environment
            env.update(native_environment(binding))
        prior=None
        if native_local and binding['provider']=='claude' and record and parent:
            from studio.managed_claude import _identity
            wrapper=process_identity(os.getpid())
            # The HTTP parent publishes its Popen identity immediately after
            # spawning us. Do not race that publication or guess ownership.
            end=time.monotonic()+2
            while time.monotonic()<end:
                record=rt.flow.get('terminal',a.terminal_id)
                published=record.get('process') or {}
                if record.get('state')=='RUNNING' and wrapper and all(published.get(k)==wrapper.get(k) for k in ('pid','start','boot_id','host','pgid','sid')):break
                time.sleep(.02)
            else:raise Blocked('原生终端进程所有权未确认；未启动CLI')
            prior=rt.db.get('managed-claude',_identity(binding,cwd))
        child=subprocess.Popen(wrapped,cwd=cwd,env=env)
        physical=process_identity(child.pid)
        code=child.wait()
        if code==0 and prior and physical and physical.get('pgid')==wrapper['pid'] and physical.get('sid')==wrapper['pid']:
            # The supervisor still holds the native lease here. A durable claim
            # only becomes usable after the parent records this wrapper's own
            # normal exit; a lost/terminated wrapper never grants a resume.
            try:
                from studio.managed_claude import record as record_managed
                rt.check_operator()
                if direct:ModelRegistration(rt).require_direct(selected['binding'])
                snap=rt.native_snapshot(a.workspace,binding)
                sha=snap['expected_native_sha256']
                rt.db.put('managed-terminal',a.terminal_id,dict(prior=prior,wrapper=record['process'],child=physical,argv=argv,exit=code,native_sha256=sha))
                record_managed(rt,binding,cwd,sha,'terminal',a.terminal_id)
            except (Blocked,OSError,ValueError,KeyError):
                print('终端已退出，但原生历史未能封存；后续仅可只读查看。',file=sys.stderr)
        if code and verification:
            ModelAdmission(rt).invalidate_verification(selected['binding'],cwd,verification)
        return code


if __name__=='__main__':
    try:sys.exit(main())
    except (Blocked,OSError,ValueError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
