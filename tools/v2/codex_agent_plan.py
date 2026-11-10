"""A read-only native Codex collaboration-mode turn for a frozen V2 Agent."""
from contextlib import ExitStack
import json
from pathlib import Path
import os
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from orchestration.common import Blocked, canonical, clean_env, digest
from orchestration.privacy import credentials, redact
from studio.agent_sessions import safe_text
from studio.native_codex_exec import FEATURES, provider_overrides
from studio.native_codex_image import NativeImage
from studio.native_codex_rpc import CodexRPC
from studio.session_sources import UUID, opened

from .codex_terminals import BACKEND_SUPERVISOR, backend_args, configured_mcp, guarded_workspace, pin_backend_image, reap
from .codex_terminals import safe_backend_config, wait_owned
from .session_identity import SessionRefused


def native_mode(binding, mode):
    return {'mode': mode, 'settings': {'model': binding['model'], 'reasoning_effort': binding['effort'],
                                      'developer_instructions': None}}


def scope_matches(result, binding, profile):
    return (isinstance(result, dict) and result.get('cwd') == binding['cwd']
            and result.get('approvalPolicy') == 'never' and result.get('model') == binding['model']
            and isinstance(result.get('activePermissionProfile'), dict)
            and result['activePermissionProfile'].get('id') == profile
            and result['activePermissionProfile'].get('extends') is None)


def await_mode_settings(rpc, binding, profile, sid, expected_mode):
    while True:
        event = rpc.next_event()
        if event is None:
            continue
        if event.get('method') and 'id' in event:
            raise Blocked('Codex 原生 Plan 请求了当前入口未授权的交互。')
        if event.get('method') != 'thread/settings/updated':
            continue
        value = event.get('params', {})
        settings = value.get('threadSettings', {})
        collaboration = settings.get('collaborationMode', {})
        actual = collaboration.get('settings', {})
        if (value.get('threadId') != sid or not scope_matches(settings, binding, profile)
                or collaboration.get('mode') != expected_mode or actual.get('model') != binding['model']
                or actual.get('reasoning_effort') != binding['effort']):
            raise Blocked('Codex CLI 未确认当前会话的原生 Plan 权限。')
        return


def require_effective_config(rpc, binding, startup_args, *, read_only):
    config = rpc.request('config/read', {'cwd': binding['cwd'], 'includeLayers': False}).get('config')
    features = config.get('features') if isinstance(config, dict) else None
    disabled = {key for key, value in FEATURES.items() if not value}
    disabled.update(('worktrees', 'code_mode_host', 'default_mode_request_user_input'))
    if read_only:
        disabled.update(('shell_tool', 'unified_exec'))
    expected_provider = {arg for arg in startup_args[1::2] if not arg.startswith('mcp_servers.')}
    if (not isinstance(features, dict) or any(features.get(name) is not False for name in disabled)
            or config.get('notify') != [] or config.get('agents')
            or config.get('sqlite_home') not in (None, binding['config_root'])
            or any(server.get('enabled') is not False for server in configured_mcp(config).values())
            or set(provider_overrides(config)[1::2]) != expected_provider):
        raise Blocked('Codex App Server 未确认固定 Provider 与禁用工作区外扩展；未启动会话。')


class CodexPlan:
    def __init__(self, binding, limits):
        self.binding, self.limits = binding, limits

    def start(self, prompt, cwd, artifacts, schema, journal, run):
        b = self.binding
        plan_only = b.get('v2_agent_plan') is True
        transition = b.get('transition_from')
        if (str(cwd) != b['cwd'] or b.get('v2_agent_skills') or plan_only == bool(transition)
                or plan_only and (not b.get('no_tools') or b.get('role') != 'reviewer' or schema is not None)
                or transition and (b.get('role') != 'implementer' or b.get('no_tools') or schema is None
                                   or b.get('v2_agent_scope') != 'workspace-write'
                                   or transition.get('authorization') != 'confirm-workspace-write')):
            raise Blocked('Codex 原生模式与本次授权不一致。')
        mode = 'plan' if plan_only else 'default'
        transport = 'native-codex-plan' if plan_only else 'native-codex-confirmed-agent'
        runtime = journal.s.rt
        timeout = min(self.limits['step_timeout_seconds'], b['timeout_seconds'])
        began = time.monotonic()
        cancelled = lambda: bool(journal.get(run)['data'].get('cancel_requested'))
        sid = b['session_id']
        literals = credentials(b['home'])
        request = journal.intent(run, 'AGENT_REQUEST', dict(kind='codex', project=b['project'], role=b['role'],
            requested_session=sid, requested_model=b['model'], requested_effort=b['effort'], collaboration_mode=mode))
        journal.visible_request(prompt)
        process = identity = op = rpc = None
        turn = plan = answer = terminal = None
        error = None
        with ExitStack() as held:
            try:
                image = held.enter_context(NativeImage(b['executable'], expected_sha256=b['_executable_sha256']))
                provider_args = safe_backend_config(runtime.store, b, image, disable_extensions=True)
                directory = held.enter_context(opened(cwd, directory=True))
                guarded_workspace(runtime.store, b, directory)
                root = Path(held.enter_context(TemporaryDirectory(prefix='df-codex-plan-', dir='/tmp')))
                scratch = root / 'tmp'
                scratch.mkdir(mode=0o700)
                executable, fd, verify_image = pin_backend_image(root, image, held)
                profile = 'devflower_plan_' + secrets.token_hex(16)
                argv = [sys.executable, '-I', '-B', '-c', BACKEND_SUPERVISOR, str(os.getpid()), str(fd),
                        *backend_args(b, provider_args, executable, profile, read_only=plan_only)]
                journal.check_before_spawn()
                verify_image()
                guarded_workspace(runtime.store, b, directory)
                remaining = timeout - (time.monotonic() - began)
                if cancelled() or remaining <= 0:
                    raise Blocked('Codex 原生 Plan 已取消或达到等待上限。')
                op = journal.intent(run, 'PROCESS', dict(backend='native-local', provider='codex', cwd=str(cwd),
                    context={'provider': 'codex', 'transport': transport}))
                process = subprocess.Popen(argv, cwd=cwd, env=dict(clean_env(b['home']), CODEX_HOME=b['config_root'],
                    TMPDIR=str(scratch)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True, pass_fds=(fd,))
                identity = wait_owned(process, fd)
                journal.finish(op, 'RUNNING', process=identity)
                with CodexRPC.attach(process, cwd, remaining, cancelled, self.limits['max_log_bytes']) as rpc:
                    available = rpc.request('collaborationMode/list', {}).get('data', [])
                    if not any(isinstance(row, dict) and row.get('mode') == mode for row in available):
                        raise Blocked('已安装 Codex CLI 未确认请求的原生模式。')
                    require_effective_config(rpc, b, provider_args, read_only=plan_only)
                    runtime.provider.require_local(b)
                    journal.check_before_spawn()
                    guarded_workspace(runtime.store, b, directory)
                    common = dict(cwd=str(cwd), model=b['model'], approvalPolicy='never', permissions=profile,
                                  runtimeWorkspaceRoots=[str(cwd)])
                    if sid:
                        response = rpc._request('thread/resume', dict(common, threadId=sid, excludeTurns=True))
                    else:
                        response = rpc._request('thread/start', dict(common, ephemeral=False, threadSource='devflower'))
                    thread = response.get('thread') or {}
                    actual = thread.get('id')
                    if (not scope_matches(response, b, profile) or response.get('runtimeWorkspaceRoots') != [str(cwd)]
                            or thread.get('cwd') != str(cwd) or not isinstance(actual, str) or not UUID.fullmatch(actual)
                            or sid and (actual != sid or thread.get('path') != str(Path(b['config_root']) / 'sessions'
                                                                                  / b['resume_proof']['relative']))):
                        raise SessionRefused('LOCATOR_REFUSED')
                    sid = actual
                    journal.finish(request, 'AGENT_SESSION', actual_session=sid)
                    collaboration = native_mode(b, mode)
                    rpc._request('thread/settings/update', {'threadId': sid, 'collaborationMode': collaboration})
                    await_mode_settings(rpc, b, profile, sid, mode)
                    runtime.provider.require_local(b)
                    verify_image()
                    guarded_workspace(runtime.store, b, directory)
                    if cancelled():
                        raise Blocked('Codex 原生运行在发送输入前已取消。')
                    params = dict(common, threadId=sid, collaborationMode=collaboration,
                                  input=[{'type': 'text', 'text': prompt, 'text_elements': []}])
                    if schema:
                        params['outputSchema'] = json.loads(Path(schema).read_text())
                    response = rpc._request('turn/start', params, on_sent=lambda: journal.input_sent(
                        op, len(prompt.encode()), digest(prompt.encode())))
                    turn = response.get('turn', {}).get('id')
                    if not isinstance(turn, str) or not UUID.fullmatch(turn):
                        raise Blocked('Codex 原生 Plan 未返回当前回合身份。')
                    while True:
                        event = rpc.next_event()
                        if event is None:
                            continue
                        method, params = event.get('method'), event.get('params', {})
                        if method and 'id' in event:
                            raise Blocked('Codex 原生 Plan 请求进一步交互。本次未自动回答。')
                        if params.get('threadId') != sid:
                            continue
                        if method == 'item/completed' and params.get('turnId') == turn:
                            item = params.get('item', {})
                            if plan_only and item.get('type') == 'plan' and isinstance(item.get('text'), str):
                                plan = item['text']
                            elif item.get('type') == 'agentMessage' and isinstance(item.get('text'), str):
                                answer = item['text']
                            elif transition and item.get('type') == 'commandExecution':
                                if item.get('source') not in ('agent', 'unifiedExecStartup', 'unifiedExecInteraction'):
                                    raise Blocked('Codex 原生 Agent 出现未经批准的命令入口。')
                                code = item.get('exitCode')
                                journal.native_tool_event(dict(event=method, id=str(item.get('id', ''))[:128],
                                    command=safe_text(redact(item.get('command') or '', literals))[:4000],
                                    output=safe_text(redact(item.get('aggregatedOutput') or '', literals))[:8000],
                                    status=str(item.get('status', ''))[:80], exit_code=code if type(code) is int else None))
                            elif transition and item.get('type') in ('fileChange', 'functionCallOutput'):
                                pass
                            elif item.get('type') not in ('userMessage', 'reasoning', 'contextCompaction'):
                                raise Blocked('Codex 原生 Plan 出现未获准的工具活动。')
                        if method == 'turn/completed':
                            actual_turn = params.get('turn', {})
                            if actual_turn.get('id') != turn:
                                raise SessionRefused('LOCATOR_REFUSED')
                            terminal = 'turn.completed' if actual_turn.get('status') == 'completed' else 'turn.failed'
                            journal.native_terminal_event(dict(type=terminal, session_id=sid, native_turn_id=turn))
                            break
                    rpc._request('thread/unsubscribe', {'threadId': sid})
            except (Blocked, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as failure:
                error = failure
            finally:
                reap(process, identity)
        if op and process is not None:
            result = dict(operation_id=op, process=identity, exit=process.returncode, timeout=bool(
                rpc and time.monotonic() > rpc.deadline), cancelled=cancelled(), overflow=bool(rpc and rpc.overflow),
                duration=round(time.monotonic() - began, 3))
            journal.finish(op, 'EXITED', **result)
        else:
            result = None
        if error or terminal != 'turn.completed' or not result or result['exit'] != 0 or not (plan or answer):
            journal.finish(request, 'AGENT_FAILED', error_category=transport)
            if isinstance(error, SessionRefused):
                raise error
            raise Blocked('Codex 原生 Plan 未完成或结果未确认。未自动重发。') from None
        text = safe_text(redact(plan or answer, literals))
        if plan_only:
            report = {'reply': text}
            evidence = {'native_plan': {'mode': 'plan', 'finalized': bool(plan and text), 'sha256': digest(text.encode())}}
        else:
            report = redact(json.loads(answer), literals)
            evidence = {'native_agent_transition': {'mode': 'default', 'plan_sha256': transition['plan_sha256']}}
        journal.finish(request, 'AGENT_COMPLETED', actual_session=sid, native_turn_id=turn,
                       transport={'mode': transport}, **evidence)
        return dict(report=report, session_id=sid, native_turn_id=turn,
                    requested_model=b['model'], requested_effort=b['effort'], resolved_model='unknown',
                    resolved_effort='unknown', process=result, home=b['home'], cwd=str(cwd), provider='codex',
                    transport={'mode': transport},
                    raw_report_sha256=digest(canonical(report)), **evidence)
