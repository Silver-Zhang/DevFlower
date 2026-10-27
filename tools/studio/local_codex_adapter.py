"""Native Codex execution with one canonical thread and native writer locking."""
import json
from pathlib import Path
import time
from orchestration.common import Blocked,canonical,digest
from orchestration.privacy import credentials,redact
from .native_codex_rpc import CodexRPC


class LocalCodex:
    def __init__(self,binding,limits):self.b,self.limits=binding,limits

    def start(self,prompt,cwd,artifacts,output_schema=None,store=None,run=None,session=None):
        cwd=str(cwd)
        b=self.b;sid=session or b.get('session_id');started=time.monotonic()
        secrets_found=credentials(b['home'],[p for p,_ in b.get('readonly_files',[])])
        request_op=store.intent(run,'AGENT_REQUEST',dict(kind='codex',project=b['project'],role=b['role'],
            requested_session=sid,requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        cancelled=lambda:bool(store and store.get(run)['data'].get('cancel_requested'))
        result=None;answer='';rpc=None;op=None;error=None;completed=False
        try:
            with CodexRPC(b['executable'],b['home'],b['config_root'],cwd,
                    min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300)),cancelled,
                    cap=self.limits['max_log_bytes']) as rpc:
                op=store.intent(run,'PROCESS',dict(backend='native-local',provider='codex',cwd=cwd,
                    context={'provider':'codex','transport':'native-local'})) if store else None
                if store:
                    if hasattr(store,'process_started'):store.process_started=True
                    store.finish(op,'RUNNING',process=rpc.identity)
                read_only=b['role']!='implementer' or b.get('admission_probe')
                config=dict(model_reasoning_effort=b['effort'])
                if b.get('admission_probe') or b.get('no_tools'):
                    config.update({'features.shell_tool':False,'features.unified_exec':False,
                                   'features.browser_use':False,'features.view_image':False})
                common=dict(model=b['model'],cwd=cwd,sandbox='read-only' if read_only else 'workspace-write',
                            approvalPolicy='never',config=config)
                if sid:
                    value=rpc.request('thread/resume',dict(common,threadId=sid,excludeTurns=True))
                else:
                    value=rpc.request('thread/start',dict(common,ephemeral=False,threadSource='devflower'))
                actual=value['thread']['id']
                if sid and actual!=sid:raise Blocked('原生恢复返回了不同会话')
                sid=actual
                if not session and not b.get('session_id'):
                    rpc.request('thread/name/set',dict(threadId=sid,name='DevFlower · '+str(b.get('responsibility','Agent'))[:65]))
                if store:store.finish(request_op,'AGENT_SESSION',actual_session=sid,resolved_model='unknown')
                params=dict(threadId=sid,input=[dict(type='text',text=prompt)],model=b['model'],effort=b['effort'])
                if output_schema:params['outputSchema']=json.loads(Path(output_schema).read_text())
                if b.get('expected_native_turn_id'):
                    prior=rpc.request('thread/read',dict(threadId=sid,includeTurns=True))['thread'].get('turns',[])
                    if not prior or prior[-1]['id']!=b['expected_native_turn_id']:
                        raise Blocked('原生会话已在另一端继续，请刷新并确认本轮输入；未发送任务')
                turn=rpc.request('turn/start',params)['turn']['id']
                if store:store.finish(request_op,'AGENT_SESSION',actual_session=sid,native_turn_id=turn)
                if store and callable(getattr(store,'input_sent',None)):
                    store.input_sent(op,len(prompt.encode()),digest(prompt.encode()))
                while True:
                    event=rpc.next_event()
                    if not event:continue
                    name,p=event.get('method'),event.get('params',{})
                    if p.get('threadId') not in (None,sid):raise Blocked('原生事件跨越了当前会话')
                    if p.get('turnId') not in (None,turn):continue
                    if name=='item/completed' and p.get('item',{}).get('type')=='agentMessage':
                        item=p['item']
                        if item.get('phase')=='final_answer' or not answer:answer=item.get('text','')
                    if name=='turn/completed' and p['turn']['id']==turn:
                        if p['turn']['status']!='completed':raise Blocked('原生回合没有完成：'+str(p['turn'].get('error')))
                        completed=True;break
                rpc.request('thread/unsubscribe',dict(threadId=sid))
        except BaseException as exc:
            error=exc
        code=rpc.process.returncode if rpc else None
        process=dict(exit=code,timeout=bool(error and '预算' in str(error)),
            overflow=bool(rpc and rpc.overflow),cancelled=cancelled(),duration=round(time.monotonic()-started,3),
            process=rpc.identity if rpc else None,backend='native-local')
        if store and op:store.finish(op,'EXITED',**process)
        if error:
            if store:store.finish(request_op,'AGENT_FAILED',error_category='native-local',process=process)
            if isinstance(error,Exception):raise Blocked(redact(str(error),secrets_found)) from None
            raise error
        if not completed or code!=0:raise Blocked('原生服务没有正常完成并退出；未伪造成功回执')
        if b.get('conversation_format')=='text':report=dict(reply=answer)
        else:
            try:report=json.loads(answer)
            except ValueError:raise Blocked('原生回答没有符合本次结构化合同') from None
        report=redact(report,secrets_found)
        if store:store.finish(request_op,'AGENT_COMPLETED',actual_session=sid,transport={'mode':'native-local'})
        return dict(report=report,session_id=sid,native_turn_id=turn,requested_model=b['model'],requested_effort=b['effort'],
            resolved_model='unknown',resolved_effort='unknown',permission_mode='native-read-only' if read_only else 'native-workspace-write',
            process=process,raw_report_sha256=digest(canonical(report)),provider='codex',home=b['home'],cwd=cwd,
            transport={'mode':'native-local'},adapter_version='native-local/1',usage={})
