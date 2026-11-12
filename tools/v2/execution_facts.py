"""Public execution diagnostics from recorded effects and bounded native facts."""
import re

from orchestration.common import Blocked

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
