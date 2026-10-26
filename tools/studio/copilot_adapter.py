"""Copilot workflow execution: explicit native sessions, context-only tools.

The host resolves one operator-pinned GitHub CLI credential in memory. The
credential store and the user's HOME are never mounted into an Agent.
"""
from pathlib import Path
import json, os, stat, subprocess, sys, tempfile, uuid
from orchestration.common import Blocked, canonical, digest, clean_env
from orchestration.process import execute
from .agent_sessions import safe_text


def credential(profile):
    auth=profile.get('auth',{})
    if set(auth)!={'kind','executable','config_file'} or auth.get('kind')!='github-cli':
        raise Blocked('Copilot 需要操作者固定登记的 GitHub CLI 认证来源')
    from .session_sources import opened
    # No symlinks, no browser-supplied command/arguments/environment.
    with opened(auth['config_file']):pass
    with opened(auth['executable']):pass
    try:
        # Workers intentionally inherit no HOME. gh must never fall back to cwd
        # (which can be a frozen candidate), or inherit another GH_TOKEN.
        with tempfile.TemporaryDirectory(prefix='devflower-github-broker-') as directory:
            env=dict(clean_env(directory),GH_CONFIG_DIR=str(Path(auth['config_file']).parent),
                     XDG_STATE_HOME=directory+'/state',XDG_CACHE_HOME=directory+'/cache',XDG_CONFIG_HOME=directory+'/config')
            runtime=Path('/run/user')/str(os.getuid());bus=runtime/'bus'
            try:
                info=bus.stat()
                if stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid():
                    env.update(XDG_RUNTIME_DIR=str(runtime),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(bus))
            except FileNotFoundError:pass
            result=subprocess.run([auth['executable'],'auth','token','--hostname','github.com'],
                env=env,capture_output=True,timeout=10,cwd=directory)
        token=result.stdout.decode().strip()
        if result.returncode or not token or len(token)>4096 or any(c.isspace() for c in token):raise ValueError()
        return token
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise Blocked('Copilot 无法取得已批准 GitHub 登录；没有复制或覆盖全局认证') from None


def invoke(binding,cwd,artifacts,request,timeout=120,cap=2_000_000,store=None,run=None):
    if binding.get('isolation','bwrap')!='bwrap':raise Blocked('Copilot 只允许强制隔离执行')
    root=Path(__file__).resolve().parents[1];secret=credential(binding)
    try:
        process,out,_=execute([sys.executable,'-B',str(root/'devflower_copilot.py')],cwd,binding['home'],
            read=[str(root),str(cwd),str(artifacts),*binding['read_paths']],write=[],timeout=timeout,cap=cap,
            input_text=json.dumps(dict(request,executable=binding['executable'])),store=store,run=run,
            backend='bwrap',readonly_paths=[str(Path(cwd)/'.git')],
            env_extra={'COPILOT_GITHUB_TOKEN':secret,'COPILOT_HOME':str(Path(binding['home'])/'.copilot'),
                       'COPILOT_DISABLE_KEYTAR':'1'},context={'provider':'copilot','agent_role':binding.get('role','metadata')})
        out=out.replace(secret.encode(),b'[REDACTED]')
        if process['exit'] or process['timeout'] or process['overflow'] or process['cancelled']:
            detail=''
            try:detail=safe_text(str(json.loads(out).get('error','')))[:700]
            except (ValueError,AttributeError):pass
            raise Blocked('Copilot 原生请求未完成；exit='+str(process['exit'])+'；未自动重发'+('；'+detail if detail else ''))
        value=json.loads(out.replace(secret.encode(),b'[REDACTED]'))
        if not isinstance(value,dict) or value.get('error'):raise Blocked(safe_text(str(value.get('error','Copilot 响应无效'))))
        return value,process
    finally:secret=None


class CopilotAdapter:
    def __init__(self,binding,limits):self.binding=binding;self.limits=limits
    def start(self,prompt,cwd,artifacts,output_schema=None,store=None,run=None,session=None):
        b=self.binding
        if b.get('role')=='implementer' or b.get('execution_mode','Ask') not in ('Ask','Plan') or b.get('tools'):
            raise Blocked('Copilot 当前仅支持提供上下文的 Ask/Plan；写入和原生工具尚未开放')
        if b['effort']!='default':raise Blocked('当前 Copilot 接口未验证推理强度设置；请选择由 Copilot 决定')
        sid=session or b.get('session_id');resume=bool(sid);sid=sid or str(uuid.uuid4())
        if str(uuid.UUID(sid))!=sid:raise Blocked('Copilot 会话必须是明确 UUID')
        schema=json.loads(Path(output_schema).read_text()) if output_schema else None
        if schema and b.get('conversation_format')!='text':
            prompt+='\n仅返回符合下列 JSON Schema 的结果对象：\n'+json.dumps(schema,ensure_ascii=False)
        op=store.intent(run,'AGENT_REQUEST',dict(kind='copilot',project=b['project'],role=b['role'],requested_session=sid,
            requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        request=dict(mode='run',model=b['model'],session_id=sid,resume=resume,prompt=prompt,output_schema=schema,
                     conversation_format=b.get('conversation_format','json'))
        try:
            value,process=invoke(b,cwd,artifacts,request,timeout=min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',3600)),
                cap=self.limits['max_log_bytes'],store=store,run=run)
            if value.get('session_id')!=sid:raise Blocked('Copilot 原生回执与绑定会话不一致')
            if value.get('provider_prompt_sha256')!=digest(prompt.encode()):raise Blocked('Copilot 实际请求内容与记录不一致')
            report=value['report']
            if not isinstance(report,dict):raise Blocked('Copilot 未返回结构化结果')
        except (Blocked,ValueError,KeyError) as exc:
            if store:store.finish(op,'AGENT_FAILED',error_category='copilot-native')
            if isinstance(exc,Blocked):raise
            raise Blocked('Copilot 原生回执格式无效') from None
        if store:store.finish(op,'AGENT_COMPLETED',actual_session=sid)
        return dict(session_id=sid,resolved_model=value.get('resolved_model','unknown'),resolved_effort='unknown',
            requested_model=b['model'],requested_effort=b['effort'],report=report,raw_report_sha256=digest(canonical(report)),
            usage=value.get('usage'),process=process,cwd=str(cwd),home=b['home'],provider='copilot',adapter_version=1,
            permission_mode='external-bwrap/context-only/no-native-tools',transport=dict(mode='copilot-headless-rpc',protocol=3),
            provider_prompt_sha256=value['provider_prompt_sha256'],cli_version=value.get('cli_version','unknown'))
