"""Claude local CLI: same canonical HOME/session, no credential overlays."""
from contextlib import nullcontext
from pathlib import Path
import json
import os
import uuid
from orchestration.adapters import CLI,native_errors
from orchestration.common import Blocked,canonical,digest
from orchestration.privacy import credentials,redact
from orchestration.process import execute
from .native_codex_image import sealed_executable
from .native_local import native_environment
from .session_sources import opened
from .agent_sessions import safe_text


def chat_only_args():
    """Native authentication with customizations, hooks and tools disabled."""
    settings = {
        'disableAllHooks': True,
        'permissions': {'defaultMode': 'plan', 'disableAutoMode': 'disable', 'disableBypassPermissionsMode': 'disable'},
        'env': {'CLAUDE_CODE_MAX_RETRIES': '0', 'DISABLE_AUTOUPDATER': '1', 'DISABLE_UPDATES': '1'},
    }
    return ['--safe-mode', '--disable-slash-commands', '--no-chrome', '--tools', '',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--setting-sources', 'user', '--settings', json.dumps(settings, separators=(',', ':'))]


def claude_secrets(binding, environment):
    """Known native Claude credentials used only for in-memory redaction."""
    secret = credentials(binding['home'], [path for path, _ in binding.get('readonly_files', [])])
    configs = [environment]
    if binding.get('chat_only') is True:
        try:
            with opened(Path(binding['config_root']) / 'settings.json') as fd:
                raw = os.read(fd, 200001)
            settings = json.loads(raw) if len(raw) <= 200000 else None
            if isinstance(settings, dict) and isinstance(settings.get('env'), dict):
                configs.append(settings['env'])
        except (OSError, ValueError, Blocked, RecursionError):
            pass
    for values in configs:
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN'):
            value = values.get(name)
            if isinstance(value, str) and value:
                secret.add(value)
        endpoint = values.get('ANTHROPIC_BASE_URL')
        if isinstance(endpoint, str) and endpoint:
            secret.add(endpoint)
        headers = values.get('ANTHROPIC_CUSTOM_HEADERS')
        if isinstance(headers, str):
            for header in headers.splitlines():
                value = header.partition(':')[2].strip()
                if value:
                    secret.add(value)
                    if value.lower().startswith(('bearer ', 'basic ')):
                        secret.add(value.split(maxsplit=1)[-1])
    return secret


class LocalClaude:
    def __init__(self, binding, limits, *, credential_env=None, managed_resume=False):
        self.b, self.limits = binding, limits
        self.credential_env = credential_env or {}
        self.managed_resume = managed_resume

    def start(self,prompt,cwd,artifacts,output_schema=None,store=None,run=None,session=None):
        cwd=str(cwd)
        b=self.b;sid=session or b.get('session_id')
        # Native Claude's shell sandbox does not cover its file tools/hooks.
        # Do not claim a verified workspace-write boundary from a prompt.
        if b['role']=='implementer':raise Blocked('本机 Claude 写入边界尚未完成原生验证；当前仅开放 Ask/Plan，旧隔离配置保留')
        if sid and not isinstance(sid,str):raise Blocked('原生恢复需要明确 UUID')
        requested=sid or b.get('new_session_id') or str(uuid.uuid4())
        chat_only=b.get('chat_only') is True
        if chat_only and sid and (not self.managed_resume or sid != b.get('session_id')
                or not callable(getattr(store, 'check_before_spawn', None))):
            raise Blocked('Claude 续聊需要已核验的 DevFlower 会话及执行前复核。')
        tools=chat_only_args() if chat_only else ['--tools','Read,Glob,Grep','--strict-mcp-config',
            '--mcp-config','{"mcpServers":{}}','--setting-sources','user']
        args=[b['executable'],'-p','--output-format','json','--permission-mode','plan',*tools]
        for key in ('model', 'effort'):
            value = b[key]
            if value is not None or not chat_only:
                args += ['--' + key, value]
        args += ['--resume' if sid else '--session-id', requested]
        if output_schema:
            schema=json.loads(Path(output_schema).read_text());schema.pop('$schema',None)
            args+=['--json-schema',json.dumps(schema)]
        key=store.intent(run,'AGENT_REQUEST',dict(kind='claude',project=b['project'],role=b['role'],
            requested_session=requested,requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        if store and callable(getattr(store,'check_before_spawn',None)):
            store.check_before_spawn()
        before = claude_secrets(b, self.credential_env) if chat_only else set()
        image = (sealed_executable(b['executable'], 'devflower-claude', expected_sha256=b['_executable_sha256'])
                 if chat_only else nullcontext((None, None)))
        with image as (executable_fd, _):
            spawn_options = {} if executable_fd is None else {'executable_fd': executable_fd}
            process,out,err=execute(args,cwd,b['home'],timeout=min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300)),
                cap=self.limits['max_log_bytes'],input_text=prompt,store=store,run=run,isolate=False,backend='native-local',
                env_extra=dict(native_environment(b), CLAUDE_CODE_MAX_RETRIES='0', **self.credential_env),
                context={'provider':'claude','agent_role':b['role'],'transport':'native-local'}, **spawn_options)
        # Same native envelope/session verifier as the legacy CLI adapter.
        try:result=CLI('claude',b,self.limits).collect(process,out)
        except Blocked as exc:
            secret = before | claude_secrets(b, self.credential_env)
            detail=safe_text(redact('; '.join(native_errors('claude',out))[:1200],secret))
            if process.get('timeout'):
                limit=min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300))
                reason=f"Claude 本次请求超时（已用 {process.get('duration','unknown')} 秒，单次上限 {limit} 秒，退出码 {process.get('exit','unknown')}）；所属进程已停止，没有自动重发。请检查中转站连接，或在节点高级设置延长超时后明确提交新任务"
            elif process.get('cancelled'):
                reason='Claude 本次请求已取消，所属进程已停止，没有自动重发'
            elif process.get('overflow'):
                reason='Claude 输出超过本次限制，所属进程已停止，没有自动重发'
            elif process.get('exit'):
                reason=f"Claude 原生请求失败（退出码 {process['exit']}），没有自动重发；请检查 Agent 登录、模型和中转站连接"
            else:
                reason=str(exc)
            if store:store.finish(key,'AGENT_FAILED',process=process,error_category='native-local')
            raise Blocked(reason+((': '+detail) if detail else '')) from None
        if result['session_id']!=requested:raise Blocked('原生 CLI 返回了不同的会话身份')
        secret = before | claude_secrets(b, self.credential_env)
        result['report']=redact(result['report'],secret)
        if store:store.finish(key,'AGENT_COMPLETED',actual_session=requested,transport={'mode':'native-local'})
        result.update(requested_model=b['model'],requested_effort=b['effort'],process=process,
            home=b['home'],cwd=cwd,provider='claude',permission_mode='native-plan/no-tools' if chat_only else 'native-plan/read-tools',
            transport={'mode':'native-local'},raw_report_sha256=digest(canonical(result['report'])),adapter_version='native-local/1')
        return result
