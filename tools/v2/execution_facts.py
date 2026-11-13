"""Public execution diagnostics from recorded effects and bounded native facts."""
import copy
import re
import uuid

from orchestration.common import Blocked, digest
from orchestration.contracts import validate
from studio.workbench_data import REPLY

from .session_identity import SessionRefused

PROCESS_FIELDS = ('exit','duration','timeout','cancelled','overflow')
NATIVE_BUDGET_EXCEEDED = 'error_max_budget_usd'


class PlanInputRequired(Blocked):
    """The associated native Plan turn requested a user response."""


def process_facts(process):
    return {key:process[key] for key in PROCESS_FIELDS if key in process} if isinstance(process,dict) else {}


def failure_fact(provider, error, *, process=None, uncertain=False, sent=False, terminal_failed=False, native_limit=None):
    name = {'codex':'Codex','claude':'Claude'}.get(provider,'原生 CLI')
    process = process_facts(process)
    text = str(error).casefold()
    if isinstance(error,SessionRefused):
        code, reason = error.code, str(error)
        action = '刷新原会话并核对来源、历史和当前占用。'
    elif isinstance(error, PlanInputRequired):
        code, reason = 'PLAN_INPUT_REQUIRED', 'Codex 原生 Plan 在本次回合中请求补充信息。'
        action = '本次未回答或重发。请核对原会话；如需继续，可补全任务信息后明确启动新 Plan。'
    elif native_limit==NATIVE_BUDGET_EXCEEDED:
        code, reason = 'NATIVE_USD_LIMIT', 'Claude CLI 报告已达到本次美元预算上限。'
        action = '查看原回执与已报告费用；需要新任务时先核对预算。'
    elif process.get('timeout'):
        code, reason = 'LOCAL_TIMEOUT', f'{name} 本机进程达到等待上限。'
        action = '查看本次发送与终结证据；新任务可在 Agent 配置调整超时。'
    elif process.get('cancelled'):
        code, reason = 'CANCELLED', '本机进程收到取消请求。'
        action = '核对本次运行的终结证据。'
    elif process.get('overflow'):
        code, reason = 'OUTPUT_LIMIT', '原生 CLI 输出超过本机记录上限。'
        action = '核对原请求并缩小新任务的输出范围。'
    elif '运行已达到时间预算' in text or '调用预算已用尽' in text:
        code, reason = 'RUN_BUDGET', '工作流已达到冻结的调用次数或时间预算。'
        action = '查看运行预算，调整草稿配置后显式启动新任务。'
    elif re.search(r'硬链接|hardlink|保护目录',text):
        code, reason = 'FILESYSTEM_POLICY', f'{name} 的受保护文件或工作区边界核验失败。'
        action = '检查工作区、硬链接、挂载与受保护目录。'
    elif re.search(r'配置已变化|配置在预检期间变化|映像已变化|profile|config changed',text):
        code, reason = 'CONFIG_CHANGED', f'{name} 的冻结配置或 CLI 映像已变化。'
        action = '刷新原生目录，核对当前配置并重新选择。'
    elif re.search(r'401|403|auth|credential|login|认证|登录|账户',text):
        code, reason = 'AUTHENTICATION', f'{name} 的认证或账户检查失败。'
        action = '在对应原生客户端核对登录与访问权限。'
    elif re.search(r'429|quota|rate.limit|额度',text):
        code, reason = 'PROVIDER_QUOTA', f'{name} 报告额度或速率限制。'
        action = '检查原生账户的额度和服务状态。'
    elif re.search(r'timeout|timed.out|connect|network|超时|连接',text):
        code, reason = 'CONNECTION', f'{name} 的服务连接未完成。'
        action = '检查本机网络和原生服务设置。'
    elif re.search(r'强度|effort|model_reasoning|invalid.parameter|模型',text):
        code, reason = 'MODEL_PARAMETERS', f'{name} 未确认本次模型或推理参数。'
        action = '刷新真实模型目录，核对模型及推理强度。'
    elif re.search(r'sandbox|capability|权限|沙箱|工具|unsupported',text):
        code, reason = 'CAPABILITY', f'{name} 的工具或权限能力核验失败。'
        action = '核对原生 CLI 版本和本机隔离能力。'
    elif re.search(r'receipt|session|回执|会话',text):
        code, reason = 'RECEIPT', f'{name} 的会话或终结回执核验失败。'
        action = '在运行检查器核对当前请求和原生会话。'
    else:
        code = 'NATIVE_EXECUTION' if uncertain or terminal_failed else 'PREFLIGHT'
        reason = f'{name} 执行未完成。' if uncertain or terminal_failed else f'{name} 本机预检未通过。'
        action = '查看原生客户端及当前工作区的配置，再核对本次记录。'
    effect = 'terminal-failed' if terminal_failed else 'unknown' if uncertain else 'not-sent'
    return dict(code=code,reason=reason,action=action,effect=effect,input_recorded=bool(sent),process=process)


def terminal_correlated(activation, process, binding, terminal_type, session_id, native_turn_id):
    """One association rule for success and definite native terminal failure."""
    events = activation.get('native_terminal_events', [])
    saved = activation.get('process_result') or {}
    return bool(isinstance(process, dict) and len(events) == 1 and events[0].get('type') == terminal_type and
        session_id and session_id == activation.get('observed_session') == events[0].get('session_id') and
        events[0].get('native_turn_id') == native_turn_id and
        process.get('process') == activation.get('process') and
        process.get('operation_id') and process['operation_id'] == activation.get('input_sent_operation_id') and
        all(saved.get(k) == process.get(k) for k in ('process','operation_id','exit','timeout','cancelled','overflow')) and
        activation.get('input_sent_sha256') and activation['input_sent_sha256'] == activation.get('actual_request_sha256') and
        activation.get('executed_image_sha256') == binding['_executable_sha256'])


def verify_completion(activation, receipt, binding):
    process = receipt.get('process')
    if binding.get('provider') == 'claude':
        sid = binding.get('session_id') or binding.get('new_session_id')
        native = activation.get('claude_terminal')
        saved = activation.get('process_result') or {}
        history = receipt.get('native_history')
        if (not isinstance(process, dict) or not sid or native != {'type': 'result', 'subtype': 'success', 'session_id': sid}
                or sid != receipt.get('session_id') or sid != activation.get('observed_session')
                or not isinstance(history, dict) or not re.fullmatch(r'[0-9a-f]{64}', history.get('sha256', ''))
                or process.get('exit') != 0 or any(process.get(key) for key in ('timeout', 'cancelled', 'overflow'))
                or process.get('process') != activation.get('process') or not process.get('operation_id')
                or process['operation_id'] != activation.get('input_sent_operation_id')
                or any(saved.get(key) != process.get(key) for key in ('process', 'operation_id', 'exit', 'timeout', 'cancelled', 'overflow'))
                or not activation.get('input_sent_sha256') or activation['input_sent_sha256'] != activation.get('actual_request_sha256')
                or activation.get('executed_image_sha256') != binding['_executable_sha256']
                or receipt.get('requested_model') not in (binding['model'], 'unknown' if binding['model'] is None else binding['model'])
                or receipt.get('requested_effort') not in (binding['effort'], 'unknown' if binding['effort'] is None else binding['effort'])):
            raise Blocked('Claude 终结、原生记录、输入或进程证据未确认；未重发。')
        validate(receipt.get('report'), REPLY)
        return
    if (not terminal_correlated(activation, process, binding, 'turn.completed', receipt.get('session_id'), receipt.get('native_turn_id')) or
            process.get('exit') != 0 or
            any(process.get(k) for k in ('timeout', 'cancelled', 'overflow')) or
            receipt.get('requested_model') != binding['model'] or receipt.get('requested_effort') != binding['effort'] or
            receipt.get('resolved_model') not in (None, 'unknown', binding['model']) or
            receipt.get('resolved_effort') not in (None, 'unknown', binding['effort'])):
        raise Blocked('原生终结回执、会话、请求或进程证据缺失/矛盾；结果未确认且未重发')
    validate(receipt.get('report'), REPLY)
    if binding.get('v2_agent_plan'):
        plan = receipt.get('native_plan')
        if (not isinstance(plan, dict) or plan != activation.get('native_plan') or plan.get('mode') != 'plan'
                or type(plan.get('finalized')) is not bool
                or plan.get('sha256') != digest(receipt['report']['reply'].encode())):
            raise Blocked('Codex 原生 Plan 回执无法核验。')
    if binding.get('transition_from'):
        expected = {'mode': 'default', 'plan_sha256': binding['transition_from']['plan_sha256']}
        if receipt.get('native_agent_transition') != expected or activation.get('native_agent_transition') != expected:
            raise Blocked('Codex 原生 Agent 切换回执无法核验。')


def activation_binding(definition, activation):
    binding = copy.deepcopy(definition['bindings'][activation['node']])
    if definition.get('designer_runtime') == 1 and activation.get('iteration_path') and binding.get('new_session_id'):
        binding['new_session_id'] = str(uuid.uuid5(uuid.UUID(binding['new_session_id']), activation['id']))
    return binding
