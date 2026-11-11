"""Claude local CLI with canonical native history and scoped Agent tools."""
from contextlib import nullcontext
from pathlib import Path
import json
import os
import time
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


def claude_args(binding, requested, *, resume=False, output_schema=None):
    chat_only = binding.get('chat_only') is True
    scoped = binding.get('v2_claude_scope') is True
    read_only = binding.get('v2_claude_read_only') is True
    if scoped:
        allowed = ['Read','Glob','Grep'] if read_only else ['Read','Glob','Grep','Write','Edit','Bash']
        if binding.get('v2_agent_skills') and not read_only:
            allowed.append('Skill')
        tools = ['--tools', ','.join(allowed), '--verbose']
    else:
        tools = chat_only_args() if chat_only else ['--tools', 'Read,Glob,Grep', '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', 'user']
    mode = binding.get('agent_permission_mode', 'plan')
    if (mode not in (('acceptEdits',) if scoped and not read_only else ('manual','plan'))
            or 'agent_permission_mode' in binding and not chat_only):
        raise Blocked('Claude 工作模式与已核验工具范围不匹配。')
    args = [binding['executable'], '-p', '--output-format', 'stream-json' if scoped else 'json',
            '--permission-mode', mode, *tools]
    if 'agent_permission_mode' in binding and not scoped:
        args += ['--permission-prompts', 'none']
    for key in ('model', 'effort'):
        value = binding[key]
        if value is not None or not chat_only:
            args += ['--' + key, value]
    budget = binding.get('max_budget_usd')
    if budget is not None:
        if not chat_only or not isinstance(budget, str):
            raise Blocked('Claude 原生费用上限无效。')
        args += ['--max-budget-usd', budget]
    args += ['--resume' if resume else '--session-id', requested]
    if output_schema:
        schema = json.loads(Path(output_schema).read_text())
        schema.pop('$schema', None)
        args += ['--json-schema', json.dumps(schema)]
    return args


def scoped_terminal(raw, expected, session):
    try:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        initial = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
        if (len(initial) != 1 or initial[0].get('session_id') != session
                or not expected <= set(initial[0].get('skills', []))):
            raise ValueError
        terminal = events[-1]
        if terminal.get('type') != 'result':
            raise ValueError
        return json.dumps(terminal)
    except (ValueError, TypeError, IndexError, AttributeError):
        raise Blocked('Claude 未返回匹配的原生会话、Skill 清单和终止回执。') from None


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
        scoped=b.get('v2_claude_scope') is True
        if chat_only and sid and (not self.managed_resume or sid != b.get('session_id')
                or not callable(getattr(store, 'check_before_spawn', None))):
            raise Blocked('Claude 续聊需要已核验的 DevFlower 会话及执行前复核。')
        args=claude_args(b, requested, resume=bool(sid), output_schema=output_schema)
        key=store.intent(run,'AGENT_REQUEST',dict(kind='claude',project=b['project'],role=b['role'],
            requested_session=requested,requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        before = claude_secrets(b, self.credential_env) if chat_only else set()
        transport = 'native-workspace-scope' if scoped else 'native-local'

        def launch(argv, env, **options):
            if store and callable(getattr(store,'check_before_spawn',None)):
                store.check_before_spawn()
            timeout = min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300))
            if 'run_deadline_at' in self.limits:
                timeout = min(timeout,self.limits['run_deadline_at']-time.time())
                if timeout<=0:
                    raise Blocked('运行已达到时间预算')
            return execute(argv,cwd,b['home'],timeout=timeout,
                cap=self.limits['max_log_bytes'],input_text=prompt,store=store,run=run,isolate=False,backend='native-local',
                env_extra=env,context={'provider':'claude','agent_role':b['role'],'transport':transport}, **options)

        if scoped:
            from v2.claude_agent_scope import prepare_scope
            with prepare_scope(b, cwd, requested, bool(sid), self.credential_env) as scope:
                process,out,err=launch([*scope.prefix, *args[1:]], scope.environment,
                                      pass_fds=scope.fds, process_attest=scope.attest)
                scope.publish_history()
                if not any(process[key] for key in ('exit', 'timeout', 'overflow', 'cancelled')):
                    out = scoped_terminal(out, scope.skills, requested)
        else:
            image = (sealed_executable(b['executable'], 'devflower-claude', expected_sha256=b['_executable_sha256'])
                     if chat_only else nullcontext((None, None)))
            with image as (executable_fd, _):
                options = {} if executable_fd is None else {'executable_fd': executable_fd}
                process,out,err=launch(args, dict(native_environment(b), CLAUDE_CODE_MAX_RETRIES='0',
                                                 **self.credential_env), **options)
        if chat_only and store and callable(getattr(store,'native_limit_event',None)):
            try:
                value = json.loads(out.splitlines()[-1] if scoped else out)
                if (isinstance(value,dict) and value.get('type')=='result'
                        and value.get('session_id')==requested and value.get('is_error') is True
                        and value.get('subtype')=='error_max_budget_usd'):
                    store.native_limit_event('error_max_budget_usd')
            except (ValueError,IndexError,TypeError):
                pass
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
        if store:
            terminal = {'claude_terminal': {'type': 'result', 'subtype': 'success', 'session_id': requested}
                        } if 'agent_permission_mode' in b else {}
            store.finish(key, 'AGENT_COMPLETED', actual_session=requested, transport={'mode': transport}, **terminal)
        result.update(requested_model=b['model'],requested_effort=b['effort'],process=process,
            home=b['home'],cwd=cwd,provider='claude',
            permission_mode=(('native-read/workspace-scope' if b.get('v2_claude_read_only') else
                'native-acceptEdits/workspace-scope') if scoped else
                ('native-' + b.get('agent_permission_mode', 'plan') + '/no-tools') if chat_only else 'native-plan/read-tools'),
            transport={'mode':transport},raw_report_sha256=digest(canonical(result['report'])),adapter_version='native-local/1')
        return result
