"""Atomic reservation for a future managed child; this does not spawn one.

Only the host-owned Journal can supply the run and activation. The bridge,
process lifecycle and session publication must be completed before enabling
this capability in the catalog or dispatching any reserved slot.
"""
import json
import time
import uuid
from orchestration.common import Blocked,canonical,digest
from orchestration.store import alive

_PROCESS_FIELDS=('host','boot_id','pid','start','pgid','sid')

def _record(db,kind,key):
    row=db.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,key)).fetchone()
    if not row:raise Blocked('托管子任务的运行身份不可用；未启动子任务')
    try:value=json.loads(row[0])
    except (ValueError,TypeError):raise Blocked('托管子任务记录无法核验；不会重发') from None
    if not isinstance(value,dict):raise Blocked('托管子任务记录无法核验；不会重发')
    return value

def paired_intent(db,value):
    intent=_record(db,'native-step',value['id']);data=intent.get('data')
    states={'RESERVED','STARTING','COMPLETED','FAILED','RECOVERY_REQUIRED'}
    if (intent.get('id')!=value['id'] or intent.get('run')!=value.get('run') or
        intent.get('activation')!=value.get('activation') or intent.get('kind')!='MANAGED_CHILD_DISPATCH' or
        value.get('state') not in states or intent.get('status')!=value['state'] or not isinstance(data,dict) or
        data.get('slot')!=value.get('slot') or data.get('task_sha256')!=value.get('task_sha256')):
        raise Blocked('子任务预留与执行意图不一致；不会重建或重发')
    return intent

def transition(db,value,state):
    intent=paired_intent(db,value)
    value['state']=state;intent['status']=state
    db.execute("UPDATE records SET data=? WHERE kind='managed-child' AND id=?",(canonical(value).decode(),value['id']))
    db.execute("UPDATE records SET data=? WHERE kind='native-step' AND id=?",(canonical(intent).decode(),value['id']))

def reserve(journal,slot,task,parent,*,with_created=False):
    try:size=len(task.encode('utf-8')) if isinstance(task,str) else 0
    except UnicodeEncodeError:size=0
    if type(slot) is not int or slot not in (1,2) or not isinstance(task,str) or not task.strip() or not size or size>8192:
        raise Blocked('只读子任务需要槽位 1 或 2 及不超过 8192 字节的任务文本')
    if not isinstance(parent,dict) or any(k not in parent for k in _PROCESS_FIELDS) or not alive(parent):
        raise Blocked('父进程身份不可确认；未启动子任务')
    scheduler=journal.s;run_id=scheduler.run;activation_id=journal.key
    key=digest(canonical([run_id,activation_id,slot]));task_sha=digest(task.encode('utf-8'))
    with scheduler.db.tx() as db:
        run=_record(db,'run',run_id);activation=_record(db,'activation',activation_id)
        if run.get('state')!='RUNNING' or run.get('cancel_requested') or run.get('interaction')!='click':
            raise Blocked('父运行未处于可使用子任务的状态')
        created=run.get('created_at');seconds=run.get('graph',{}).get('budget',{}).get('seconds',3600)
        if type(created) not in (int,float) or type(seconds) not in (int,float) or seconds<=0 or not 0<=time.time()-created<seconds:
            raise Blocked('父运行时间预算不可用或已用完；未启动子任务')
        if activation.get('run')!=run_id or activation.get('state')!='RUNNING' or activation.get('kind')!='agent':
            raise Blocked('子任务不属于当前运行节点')
        source=activation.get('source_at_request')
        if not isinstance(source,dict) or not isinstance(source.get('sha256'),str) or not source['sha256']:
            raise Blocked('父节点源码身份未确认；未启动子任务')
        process=activation.get('process') or {}
        if any(process.get(k)!=parent[k] for k in _PROCESS_FIELDS) or not alive(parent):
            raise Blocked('父进程已变化；未启动子任务')
        session=activation.get('observed_session')
        try:
            if str(uuid.UUID(session))!=session:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise Blocked('父原生会话身份未确认') from None
        chat=_record(db,'node-session',activation.get('chat_key'))
        binding=chat.get('binding') or {}
        if (chat.get('run')!=run_id or chat.get('workspace')!=run.get('workspace') or chat.get('node')!=activation.get('node') or
            binding.get('provider')!='codex' or binding.get('execution_backend')!='native-local' or
            binding.get('subagents')!='readonly' or binding.get('role')!='reviewer' or binding.get('execution_mode') not in ('Ask','Plan') or binding.get('session_id')!=session or
            any(not isinstance(binding.get(k),str) or not binding[k] or binding[k]=='unknown' for k in ('model','effort','home','config_root','runtime_profile'))):
            raise Blocked('当前节点没有已冻结的本机只读子任务绑定')
        for name in ('candidate_sha256','graph_sha256'):
            if not isinstance(run.get(name),str) or not run[name]:raise Blocked('子任务缺少冻结运行身份')
        binding_sha=digest(canonical(binding))
        existing=db.execute("SELECT data FROM records WHERE kind='managed-child' AND id=?",(key,)).fetchone()
        if existing:
            value=json.loads(existing[0])
            paired_intent(db,value)
            if value.get('task_sha256')!=task_sha:raise Blocked('此子任务槽位已绑定其他内容，不能重发')
            if value.get('parent_session')!=session or any(value['parent_process'].get(k)!=parent[k] for k in _PROCESS_FIELDS):
                raise Blocked('此子任务槽位属于其他父进程或会话')
            if (value.get('candidate_sha256')!=run['candidate_sha256'] or value.get('graph_sha256')!=run['graph_sha256'] or
                value.get('binding_sha256')!=binding_sha or value.get('node')!=activation.get('node') or
                value.get('iteration_path')!=activation.get('iteration_path',[]) or value.get('source_at_request')!=source):
                raise Blocked('此子任务的冻结身份已变化')
            return (value,False) if with_created else value
        budget=run.get('graph',{}).get('budget',{}).get('calls',32)
        calls=run.get('calls',0);checks=run.get('connection_checks',0)
        if type(budget) is not int or type(calls) is not int or type(checks) is not int or calls<0 or checks<0 or calls+checks>=budget:
            raise Blocked('已达到本次运行的模型调用预算；未启动子任务')
        value=dict(id=key,run=run_id,activation=activation_id,node=activation['node'],iteration_path=activation.get('iteration_path',[]),
                   slot=slot,task_sha256=task_sha,parent_session=session,parent_process={k:parent[k] for k in _PROCESS_FIELDS},
                   candidate_sha256=run['candidate_sha256'],graph_sha256=run['graph_sha256'],binding_sha256=binding_sha,source_at_request=source,
                   state='RESERVED',usage='unknown')
        intent=dict(id=key,run=run_id,activation=activation_id,kind='MANAGED_CHILD_DISPATCH',status='RESERVED',data=dict(slot=slot,task_sha256=task_sha))
        run['calls']=calls+1
        db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?",(canonical(run).decode(),run_id))
        db.execute("INSERT INTO records VALUES('managed-child',?,?)",(key,canonical(value).decode()))
        db.execute("INSERT INTO records VALUES('native-step',?,?)",(key,canonical(intent).decode()))
        return (value,True) if with_created else value
