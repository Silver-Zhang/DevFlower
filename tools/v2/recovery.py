"""Read-only evidence projection and identity-checked local process observation.

Never starts a worker, resumes a session, changes a frozen definition or replays
an input. Completion recovery reuses the existing terminal association rule.
"""
import copy
import json
import os
import re
import time
from pathlib import Path

from orchestration.common import Blocked,canonical,digest
from orchestration.process import group_alive
from orchestration.store import process_identity
from studio.session_sources import opened
from studio.workbench_data import public_data

IDENTITY=('host','boot_id','pid','start','pgid','sid')
END={'COMPLETED','FAILED','CANCELLED','SKIPPED','RECOVERY_REQUIRED'}


def observe(record,*,group=False):
    """alive/dead/unknown; PID or a foreign-host record never grants ownership."""
    if record is None:return 'absent'
    me=process_identity(os.getpid())
    if (not isinstance(record,dict) or not me or not set(IDENTITY)<=set(record)
        or any(type(record[k]) is not int or record[k]<=0 for k in ('pid','pgid','sid'))
        or not isinstance(record['start'],str) or not record['start'].isdigit()
        or not isinstance(record['boot_id'],str) or not record['boot_id']
        or record['host']!=me['host']):return 'unknown'
    if record['boot_id']!=me['boot_id']:return 'dead'
    if group and (record['pid']!=record['pgid'] or record['pid']!=record['sid']):return 'unknown'
    current=process_identity(record['pid'])
    if current and any(current.get(k)!=record.get(k) for k in IDENTITY):return 'unknown'
    if group:return 'alive' if group_alive(record) else 'dead'
    return 'alive' if current and current.get('state')!='Z' else 'dead'


def owned_group(activation,steps):
    """Return only a PROCESS associated with this exact activation/operation."""
    record=activation.get('process')
    if not record:return None
    matches=[s for s in steps if s.get('kind')=='PROCESS' and s.get('run')==activation['run']
             and s.get('activation')==activation['id'] and s.get('data',{}).get('process')==record]
    if len(matches)!=1:raise Blocked('原生进程与节点的归属未确认；未发送终止信号')
    step=matches[0];operation=activation.get('input_sent_operation_id')
    if operation and operation!=step['id']:raise Blocked('原生发送操作与进程归属不同；未发送终止信号')
    if step.get('data',{}).get('operation_id') not in (None,step['id']):raise Blocked('原生进程操作身份不同；未发送终止信号')
    if observe(record,group=True)=='unknown':raise Blocked('原生进程宿主、启动或进程组身份未确认；未发送终止信号')
    return record


def read_completion(rt,run,node,definition,steps):
    """Bounded regular artifact plus original request/process/terminal evidence."""
    from .runtime import verify_completion
    path=rt.storage/'flow-artifacts'/run['id']/node['id']/'completion.json'
    with opened(path) as fd:
        size=os.fstat(fd).st_size
        if size>2_000_000:raise ValueError('completion budget')
        value=json.loads(os.read(fd,size+1))
    if not node.get('completion_sha256'):
        with opened(path.with_name('completion-proof.json')) as fd:
            if os.fstat(fd).st_size>1024:raise ValueError('completion proof budget')
            proof=json.loads(os.read(fd,1025))
        if proof!=dict(version=1,run=run['id'],activation=node['id'],request_sha256=node.get('actual_request_sha256'),
                       completion_sha256=digest(canonical(value))):raise ValueError('completion proof identity')
    if (not isinstance(value,dict) or value.get('run')!=run['id'] or value.get('activation')!=node['id']
        or value.get('request_id')!=node.get('request_id') or not node.get('request_id')
        or value.get('request_sha256')!=node.get('actual_request_sha256')
        or node.get('completion_sha256') not in (None,digest(canonical(value)))):raise ValueError('completion identity')
    binding=definition['bindings'][node['node']]
    if (not isinstance(value.get('receipt'),dict) or not isinstance(value.get('output'),dict)
        or not isinstance(binding,dict) or not isinstance(node.get('process_result'),dict)
        or binding.get('provider') == 'codex' and (not isinstance(node.get('native_terminal_events'),list)
        or any(not isinstance(event,dict) for event in node['native_terminal_events']))):raise ValueError('completion evidence types')
    receipt=copy.deepcopy(value['receipt']);output=value['output']
    if receipt.get('native_turn_id')=='unknown':receipt['native_turn_id']=None
    if (not isinstance(output,dict) or set(output)!={'answer','summary','result','evidence','issues','next','artifacts'}
        or not isinstance(output['result'],dict) or output['artifacts']!=[] or public_data(output)!=output):raise ValueError('completion public output')
    receipt['report']=dict(reply=output['answer'],summary=output['summary'],accepted=False,
        result_json=json.dumps(output['result']),artifacts=[],evidence=output['evidence'],issues=output['issues'],next=output['next'])
    verify_completion(node,receipt,binding)
    process=receipt['process'];step=next(s for s in steps if s['id']==process['operation_id'])
    if observe(process.get('process'))=='alive':raise ValueError('terminal process still alive')
    if (step.get('kind')!='PROCESS' or step.get('run')!=run['id'] or step.get('activation')!=node['id']
        or step.get('status')!='EXITED' or any(step['data'].get(k)!=process.get(k)
             for k in ('process','operation_id','exit','timeout','cancelled','overflow'))):raise ValueError('completion process')
    return value


def project(rt,run,definition,nodes,steps):
    """One derived projection used by list/detail/nodes, never a scheduler resume."""
    from .runtime import terminal_correlated
    worker=observe(run.get('worker'));projected=[];outcomes=[];proofs={};bad_definition=False
    try:
        from studio.workbench_failures import recovery_notice
        interrupted=bool(recovery_notice(rt,run['id']))
    except (OSError,Blocked,ValueError,KeyError,TypeError):interrupted=True
    try:bad_definition=digest(canonical({k:v for k,v in definition.items() if k!='id'}))!=run['definition_id'] or definition['id']!=run['definition_id']
    except (TypeError,KeyError,ValueError):bad_definition=True
    for source in nodes:
        node=copy.deepcopy(source)
        if node['kind']!='agent':projected.append(node);continue
        forced_unknown=bool(node.get('control_stop_unconfirmed'))
        outcome='unknown' if forced_unknown else 'pending';completion=None
        try:
            if bad_definition or forced_unknown:raise ValueError('unconfirmed control stop or definition identity')
            completion=read_completion(rt,run,node,definition,steps)
        except (OSError,Blocked,ValueError,KeyError,TypeError,StopIteration):
            if node.get('receipt_verified') or node['state']=='COMPLETED':outcome='unknown'
        if completion is not None:
            outcome='completed';proofs[node['node']]=completion['output']
            node.update(state='COMPLETED',output=completion['output'],receipt=completion['receipt'],receipt_verified=True,
                        recovery_evidence='validated_completion',effect_uncertain=False)
        elif node.get('terminal_failure_verified') and not forced_unknown:
            try:
                failure=node['failure_receipt'];proc=failure['process'];binding=definition['bindings'][node['node']]
                if (not isinstance(proc,dict) or not isinstance(node.get('process_result'),dict)
                    or not isinstance(node.get('native_terminal_events'),list)
                    or any(not isinstance(event,dict) for event in node['native_terminal_events'])):raise ValueError('failure evidence types')
                turn=failure.get('native_turn_id');turn=None if turn=='unknown' else turn
                step=next(s for s in steps if s['id']==proc['operation_id'])
                if (not bad_definition and terminal_correlated(node,proc,binding,'turn.failed',failure['session_id'],turn)
                    and type(proc['exit']) is int and proc['exit']!=0 and not proc.get('timeout') and not proc.get('overflow')
                    and observe(proc.get('process'))!='alive'
                    and step['run']==run['id'] and step['activation']==node['id'] and step['status']=='EXITED'
                    and all(step['data'].get(k)==proc.get(k) for k in ('process','operation_id','exit','timeout','cancelled','overflow'))):outcome='failed'
                else:outcome='unknown'
            except (KeyError,TypeError,ValueError,StopIteration):outcome='unknown'
        elif outcome!='unknown':
            sent=bool(node.get('input_sent_sha256') or node.get('effect_uncertain') or node.get('process') or node.get('observed_session'))
            try:owned_group(node,steps)
            except Blocked:outcome='unknown'
            if node['state']=='RECOVERY_REQUIRED' or (sent and (worker!='alive' or node['state'] in END)):outcome='unknown'
            elif outcome!='unknown' and not sent and not node.get('observed_session'):outcome='not_sent'
        if outcome=='unknown':node.update(state='RECOVERY_REQUIRED',persisted_receipt_verified=bool(source.get('receipt_verified')),
            receipt_verified=False,terminal_failure_verified=False,reason='请求或终结证据未完整确认；停止本机调度不代表 Provider 未执行，未自动重发。')
        elif outcome=='failed':node.update(state='FAILED',reason=node.get('reason') or '原生终结失败已关联核验；已保留失败回执，未自动重发。')
        elif worker=='dead' and node['state'] not in END and outcome=='not_sent':node.update(state='CANCELLED' if run.get('cancel_requested') else 'SKIPPED',reason='原运行器已结束；没有请求发送证据，未继续执行。')
        node['provider_outcome']=outcome;outcomes.append(outcome);projected.append(node)
    expected=[n for n in definition.get('graph',{}).get('nodes',[]) if n.get('kind')=='agent']
    all_complete=bool(expected) and len(proofs)==len(expected) and not bad_definition
    unassociated=any(s.get('kind')=='PROCESS' and (s.get('data',{}).get('process') or s.get('status') in ('RUNNING','EXITED','RECOVERY_REQUIRED')) and
        (not s.get('data',{}).get('process') or not any(n.get('process')==s['data']['process'] and n['id']==s.get('activation')
         and n.get('input_sent_operation_id') in (None,s['id']) for n in nodes)) for s in steps)
    duplicate=any(sum(n.get('kind')=='agent' and n.get('node')==e['id'] for n in nodes)>1 for e in expected)
    uncertain=bad_definition or interrupted or unassociated or duplicate or 'unknown' in outcomes
    state=run['state'];reason=run.get('reason')
    if uncertain:
        state='RECOVERY_REQUIRED';provider='unknown';reason='存在未确认的 Provider 结果；已保留请求与进程证据，未自动重发。'
    elif all_complete:
        state='COMPLETED';provider='completed'
        if state!=run['state']:reason='全部 Agent 终结与结果文件已核验；未恢复调度或重发。'
    elif worker=='alive' and state not in END:
        state='CANCEL_REQUESTED' if run.get('cancel_requested') else state;provider='pending'
        if run.get('cancel_requested'):reason='已请求停止后续调度，正在等待原运行器停止自有进程；尚未确认 Provider 结果。'
    elif state in ('FAILED','CANCELLED'):
        provider='failed' if 'failed' in outcomes else 'completed_partial' if 'completed' in outcomes else 'not_sent'
    elif worker=='dead':
        state='CANCELLED' if run.get('cancel_requested') else 'FAILED'
        provider='failed' if 'failed' in outcomes else 'completed_partial' if 'completed' in outcomes else 'not_sent'
        reason='原运行器已结束；已完成回执保留，后续节点未执行，也未重发。'
    elif time.time()-run['created_at']>3 or worker=='unknown':
        state='RECOVERY_REQUIRED';provider='unknown';reason='原运行器完整身份未确认；不接管、不发信号、不重发。'
    else:provider='pending'
    if state=='RECOVERY_REQUIRED':
        projected=[dict(n,state='RECOVERY_REQUIRED') if n['state'] not in END else n for n in projected]
    public={k:v for k,v in run.items() if k!='graph'}
    public.update(state=state,persisted_state=run['state'],reason=reason,provider_outcome=provider,
                  local_status='running' if worker=='alive' else 'stopped' if worker=='dead' else 'identity_unknown')
    return dict(run=public,state=state,nodes=[{k:v for k,v in n.items() if k!='process_result'} for n in projected],
                worker_alive=worker=='alive',worker_observation=worker,provider_outcome=provider)
