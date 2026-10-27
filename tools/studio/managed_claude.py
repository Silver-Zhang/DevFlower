"""Evidence for product-owned Claude sessions, never an external CLI lock.

Only private runtime journals and native transcript fingerprints establish this
claim. The lease coordinates DevFlower writers; arbitrary external CLI clients
are not controlled by it.
"""
from orchestration.common import Blocked,canonical,digest
from .graph import Invalid


def _identity(binding,cwd):
    return digest(canonical([binding['provider'],binding['home'],binding['config_root'],cwd,binding['project'],binding['session_id']]))


def record(runtime,binding,cwd,native_sha,kind,origin):
    if binding.get('execution_backend')!='native-local' or binding.get('provider')!='claude':return
    if not binding.get('session_id') or not isinstance(native_sha,str) or len(native_sha)!=64:raise Blocked('受管会话缺少原生历史身份')
    runtime.db.put('managed-claude',_identity(binding,cwd),dict(workspace=binding['project'],home=binding['home'],config_root=binding['config_root'],cwd=cwd,session_id=binding['session_id'],native_sha256=native_sha,kind=kind,origin=origin))


def require_managed(runtime,binding,cwd,native_sha,*,_claim=None,_depth=0):
    if binding.get('execution_backend')!='native-local' or binding.get('provider')!='claude' or not binding.get('session_id'):return
    try:
        if _depth>64:raise ValueError()
        claim=_claim if _claim is not None else runtime.db.get('managed-claude',_identity(binding,cwd))
        if any(claim.get(k)!=v for k,v in dict(workspace=binding['project'],home=binding['home'],config_root=binding['config_root'],cwd=cwd,session_id=binding['session_id'],native_sha256=native_sha).items()):raise ValueError()
        kind=claim['kind'];origin=claim['origin']
        if kind=='terminal':
            evidence=runtime.db.get('managed-terminal',origin)
            terminal=runtime.flow.get('terminal',origin)
            if terminal.get('state')!='COMPLETED' or terminal.get('exit')!=0:raise ValueError()
            if any(terminal.get(k)!=v for k,v in dict(workspace=binding['project'],cwd=cwd,session_id=binding['session_id'],provider='claude',process=evidence.get('wrapper')).items()):raise ValueError()
            child=evidence.get('child') or {};wrapper=evidence.get('wrapper') or {};argv=evidence.get('argv') or []
            if evidence.get('exit')!=0 or evidence.get('native_sha256')!=native_sha or not child.get('pid') or not child.get('start') or child.get('boot_id')!=wrapper.get('boot_id') or child.get('host')!=wrapper.get('host') or child.get('pgid')!=wrapper.get('pid') or child.get('sid')!=wrapper.get('pid'):raise ValueError()
            from devflower_native_terminal import native_cli_argv
            if argv!=native_cli_argv(binding,binding['session_id']):raise ValueError()
            prior=evidence['prior'];require_managed(runtime,binding,cwd,prior['native_sha256'],_claim=prior,_depth=_depth+1)
            return
        if kind=='session':
            op=runtime.db.get('session-operation',origin);steps=runtime.db.listing('session-step',filters={'operation':origin},limit=None)
            if op.get('state')!='COMPLETED' or op.get('workspace')!=binding['project'] or op.get('cwd')!=cwd:raise ValueError()
            receipt=op.get('receipt') or {};process=op.get('process_result') or {}
            if receipt.get('session_id')!=binding['session_id'] or receipt.get('home')!=binding['home'] or receipt.get('cwd')!=cwd or receipt.get('provider')!='claude':raise ValueError()
            if op.get('native_session')!=binding['session_id'] or op.get('requested_session')!=binding['session_id']:raise ValueError()
            prior=op.get('binding') or {}
            if any(prior.get(k)!=binding.get(k) for k in ('provider','home','config_root','executable','runtime_profile')):raise ValueError()
        elif kind=='workflow':
            op=runtime.flow.get('activation',origin);steps=[x for x in runtime.flow.list('native-step') if x.get('activation')==origin]
            if op.get('state')!='COMPLETED' or not op.get('completion_sha256'):raise ValueError()
            receipt=op.get('receipt') or {};process=op.get('process_result') or {}
            if receipt.get('session_id')!=binding['session_id']:raise ValueError()
        else:raise ValueError()
        physical=process.get('process') or {}
        if process.get('exit')!=0 or any(process.get(k) for k in ('timeout','cancelled','overflow')) or not physical.get('pid') or not physical.get('start'):raise ValueError()
        if not physical.get('boot_id') or not physical.get('host') or physical.get('pgid')!=physical.get('pid') or physical.get('sid')!=physical.get('pid'):raise ValueError()
        if receipt.get('process')!=process:raise ValueError()
        if receipt.get('permission_mode')!='native-plan/read-tools':raise ValueError()
        if receipt.get('requested_model')!=binding.get('model') or receipt.get('requested_effort')!=binding.get('effort'):raise ValueError()
        requests=[x for x in steps if x.get('kind')=='AGENT_REQUEST' and x.get('status')=='AGENT_COMPLETED' and x.get('data',{}).get('requested_session')==binding['session_id'] and x['data'].get('actual_session')==binding['session_id']]
        processes=[x for x in steps if x.get('kind')=='PROCESS' and x.get('status')=='EXITED' and x.get('data',{}).get('operation_id')==process.get('operation_id') and x['data'].get('process')==physical and x['data'].get('cwd')==cwd and x['data'].get('context',{}).get('provider')=='claude']
        if not requests or not processes:raise ValueError()
        def matches_argv(row):
            args=row['data'].get('argv') or []
            return bool(args and args[0]==binding.get('executable') and any(args[i] in ('--session-id','--resume') and args[i+1]==binding['session_id'] for i in range(len(args)-1)))
        if not any(matches_argv(row) for row in processes):raise ValueError()
    except (Invalid,KeyError,IndexError,TypeError,ValueError):
        raise Blocked('此 Claude 会话缺少可核验的受管回执或原生历史已变化，只能只读查看；外部 CLI 不受工作台锁控制。') from None
