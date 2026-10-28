"""Native Codex execution with one canonical thread and native writer locking."""
import json
import re
from contextlib import nullcontext
from pathlib import Path
import time
from orchestration.common import Blocked,canonical,digest
from orchestration.privacy import credentials,redact
from .native_codex_rpc import CodexRPC

def _stderr_diagnostic(raw,secrets):
    """Keep bounded, redacted CLI errors, never protocol/hidden event bodies."""
    from .agent_sessions import safe_text
    text=safe_text(redact(raw.decode('utf-8','replace'),secrets))
    rows=[]
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith(('{','[')) or re.search(r'(?i)(?:reasoning|thinking|chain[ _-]of[ _-]thought|analysis)',line):continue
        if not re.search(r'(?i)(?:\berror\b|\bfailed\b|\bfatal\b|\bdenied\b|\brefused\b|\bunauthorized\b|\btimeout\b|\btimed out\b|\bhttp\b|\bbwrap:|permission|read.only|no such file|not found|\b(?:401|403|429|500|502|503)\b)',line):continue
        # Structured payloads can contain prompts or hidden provider fields.
        line=re.split(r'[\[{]',line,maxsplit=1)[0].strip()
        if line:rows.append(line[:800])
    return '；'.join(rows[-3:])[:1600]


class LocalCodex:
    def __init__(self,binding,limits):self.b,self.limits=binding,limits

    def start(self,prompt,cwd,artifacts,output_schema=None,store=None,run=None,session=None):
        version=self.b.get('direct_environment_version',1)
        if type(version) is not int or version not in (1,2,3):
            raise Blocked('不支持的原生环境指纹版本')
        if version in (2,3) and self.b.get('native_interaction')!='ordinary':
            raise Blocked('此原生环境指纹仅适用于普通直接操作；未发送任务')
        if self.b.get('native_interaction')=='ordinary':
            if self.b.get('require_atomic_resume') and (session or self.b.get('session_id')):
                raise Blocked('此请求需要原子历史比较；原生 Codex 不支持，未发送任务')
            return self._start_new(prompt,cwd,artifacts,output_schema,store,run,session=session)
        if not (session or self.b.get('session_id')):
            return self._start_new(prompt,cwd,artifacts,output_schema,store,run)
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

    def _start_new(self,prompt,cwd,artifacts,output_schema,store,run,session=None):
        # A new thread has no earlier turn to race. Existing threads remain on
        # the guarded RPC path: exec cannot atomically compare the prior turn.
        from orchestration.adapters import CLI,native_errors
        from orchestration.process import execute
        from .native_codex_exec import exec_args,provider_overrides
        from .native_codex_exec_probe import require_exec_capability,require_sandbox_capability,require_native_write_capability
        from .native_local import native_environment
        from .agent_sessions import safe_text
        import uuid
        b=self.b;cwd=str(cwd);artifacts=Path(artifacts)
        sid=session or b.get('session_id');ordinary=b.get('native_interaction')=='ordinary'
        if b.get('direct_environment_version') in (2,3) and not ordinary:
            raise Blocked('此原生环境指纹仅适用于普通直接操作；未发送任务')
        if b.get('expected_native_turn_id') and not (ordinary and sid):raise Blocked('新会话不能带旧回合身份；请刷新')
        if b.get('admission_probe') or b.get('no_tools') and not ordinary:
            raise Blocked('本机 Codex 的无工具模式仍暴露询问工具；该入口尚未验证，未调用模型。')
        if b.get('skills') or b.get('subagents','disabled')!='disabled':raise Blocked('本机 Codex 尚未验证所选扩展的隔离')
        if not artifacts.is_dir():raise Blocked('本次原生产物目录不可用')
        timeout=min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300));deadline=time.monotonic()+timeout
        cancelled=lambda:bool(store and store.get(run)['data'].get('cancel_requested'))
        def remaining():
            if cancelled():raise Blocked('原生调用已取消；未自动重发')
            left=deadline-time.monotonic()
            if left<=0:raise Blocked('原生调用达到本次等待预算；未自动重发')
            return left
        from .model_registration import direct_environment_identity
        environment=direct_environment_identity(b)
        if b.get('direct_environment_sha256') and environment!=b['direct_environment_sha256']:
            raise Blocked('原生模型配置已变化，请刷新后重试')
        # Read only the effective provider/relay settings. Tool configuration is
        # never copied, and unknown credential-bearing provider fields fail closed.
        from .native_codex_rpc import metadata_guard
        gate=b.get('_metadata_guard')
        with metadata_guard(gate,min(remaining(),30),cancelled) if gate else nullcontext(min(remaining(),30)) as wait:
            with CodexRPC(b['executable'],b['home'],b['config_root'],cwd,min(remaining(),wait,30),cancelled) as probe:
                config=probe.request('config/read',dict(cwd=cwd,includeLayers=False)).get('config')
        provider_args=provider_overrides(config)
        # Probe fixtures are created under protected storage. Serialize their
        # complete lifetime with metadata preflights, never the real request.
        with metadata_guard(gate,min(remaining(),30),cancelled) if gate else nullcontext():
            if b.get('no_tools'):
                require_exec_capability(b['executable'],artifacts,timeout=remaining(),cancelled=cancelled,no_tools=True,ordinary_profile=ordinary)
            else:
                require_exec_capability(b['executable'],artifacts,timeout=remaining(),cancelled=cancelled,ordinary_profile=ordinary)
            read_only=b['role']!='implementer' or bool(b.get('admission_probe'))
            capability=require_native_write_capability if ordinary else require_sandbox_capability
            capability(b['executable'],artifacts,not read_only,timeout=remaining(),cancelled=cancelled)
        if direct_environment_identity(b)!=environment:raise Blocked('原生模型配置在预检期间变化；未发送请求')
        if store and callable(getattr(store,'check_before_spawn',None)):store.check_before_spawn()
        if ordinary:
            from .native_codex_exec import check_protected_hardlinks
            with metadata_guard(gate,min(remaining(),30),cancelled) if gate else nullcontext():
                check_protected_hardlinks(b.get('native_protected',[]))
        remaining()
        argv=exec_args(b,provider_args,schema=output_schema)
        if sid:
            try:
                if str(uuid.UUID(sid))!=sid:raise ValueError()
            except (ValueError,TypeError,AttributeError):raise Blocked('原生会话身份无效；未发送任务') from None
            argv=[argv[0],'exec','resume',*argv[2:-1],sid,'-']
        secrets_found=credentials(b['home'],[p for p,_ in b.get('readonly_files',[])])
        op=store.intent(run,'AGENT_REQUEST',dict(kind='codex',project=b['project'],role=b['role'],
            requested_session=sid,requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        pending=bytearray();observed=None;observed_turn=None
        def observe(chunk):
            nonlocal observed,observed_turn
            pending.extend(chunk)
            if len(pending)>self.limits['max_log_bytes']:raise Blocked('原生事件超过输出预算')
            while b'\n' in pending:
                line,_,rest=pending.partition(b'\n');pending[:]=rest
                try:event=json.loads(line)
                except (ValueError,TypeError):continue
                if not isinstance(event,dict):continue
                if event.get('type')=='thread.started':
                    sid=event.get('thread_id')
                    try:
                        if not isinstance(sid,str) or str(uuid.UUID(sid))!=sid or observed is not None:raise ValueError()
                    except ValueError:raise Blocked('原生会话身份无效或重复') from None
                    observed=sid
                    if store:store.finish(op,'AGENT_SESSION',actual_session=sid,resolved_model='unknown')
                if event.get('type')=='turn.completed':
                    turn=event.get('turn_id')
                    if turn is None and isinstance(event.get('turn'),dict):turn=event['turn'].get('id')
                    if turn is not None:
                        try:
                            if not isinstance(turn,str) or str(uuid.UUID(turn))!=turn or observed_turn is not None:raise ValueError()
                        except ValueError:raise Blocked('原生回合身份无效或重复') from None
                        observed_turn=turn
                item=event.get('item')
                if (store and callable(getattr(store,'native_tool_event',None)) and
                    event.get('type') in ('item.started','item.updated','item.completed') and
                    isinstance(item,dict) and item.get('type')=='command_execution'):
                    def visible(value,limit):
                        return safe_text(redact(value,secrets_found))[:limit] if isinstance(value,str) else ''
                    code=item.get('exit_code')
                    row=dict(event=event['type'],id=visible(item.get('id'),128),
                             command=visible(item.get('command'),4000),
                             output=visible(item.get('aggregated_output'),8000),
                             status=visible(item.get('status'),80),exit_code=code if type(code) is int else None)
                    store.native_tool_event(row)
        process=None
        try:
            process,out,err=execute(argv,cwd,b['home'],timeout=remaining(),cap=self.limits['max_log_bytes'],
                input_text=prompt,store=store,run=run,isolate=False,backend='native-local',
                env_extra=native_environment(b),context={'provider':'codex','transport':'native-exec'},on_stdout=observe)
            if process['exit']!=0 or any(process.get(k) for k in ('timeout','cancelled','overflow')):
                messages=[]
                for message in native_errors('codex',out):
                    text=safe_text(redact(message,secrets_found))
                    if re.search(r'(?i)(?:reasoning|thinking|chain[ _-]of[ _-]thought|analysis)',text):continue
                    messages.append(re.split(r'[\[{]',text,maxsplit=1)[0].strip())
                detail='；'.join(messages)[:1200]
                # A capped pipe may end mid-secret; do not expose a partial line.
                stderr=err.rpartition(b'\n')[0] if process.get('overflow') and not err.endswith(b'\n') else err
                diagnostic=_stderr_diagnostic(stderr,secrets_found)
                details='；'.join(x for x in (detail,diagnostic) if x)
                raise Blocked('原生 Codex 回合未完成（退出码 '+str(process['exit'])+'）；没有自动重发'+((': '+details) if details else ''))
            result=CLI('codex',b,self.limits).collect(process,out)
            if not observed or result['session_id']!=observed or sid and observed!=sid:raise Blocked('原生回执与会话身份不匹配')
            result['report']=redact(result['report'],secrets_found)
            if store:store.finish(op,'AGENT_COMPLETED',actual_session=observed,native_turn_id=observed_turn,transport={'mode':'native-exec'})
            result.update(requested_model=b['model'],requested_effort=b['effort'],native_turn_id=observed_turn,
                home=b['home'],cwd=cwd,provider='codex',permission_mode='native-read-only' if read_only else 'native-workspace-write',
                transport={'mode':'native-exec','history':'native-latest-under-cli-lock' if sid else 'new', 'atomic_expected_turn':False},raw_report_sha256=digest(canonical(result['report'])),adapter_version='native-exec/1')
            return result
        except BaseException as exc:
            if store:store.finish(op,'AGENT_FAILED',error_category='native-exec',**({'process':process} if process is not None else {}))
            if isinstance(exc,Exception):raise Blocked(redact(str(exc),secrets_found)) from None
            raise
