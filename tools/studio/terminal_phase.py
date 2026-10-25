"""Conservative presentation from visible native terminal text, not authority."""
import re
from orchestration.common import digest


def terminal_phase(provider, text, alive):
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text[-24000:])
    if not alive: return dict(phase='closed', label='终端已结束')
    markers = [('theme', '首次设置：选择终端配色', ('Choose the text style', 'Select a theme')),
               ('trust', '等待你确认工作区信任', ('Do you trust the files', 'trust this folder', 'trust this workspace', 'Yes, I trust this folder')),
               ('permissions', '等待原生权限设置', ('Make auto mode your default permission mode?', 'Yes, set auto mode as my default permission mode')),
               ('authentication', '等待原生工具认证', ('Select login method', 'Choose a login method', 'Please run /login'))]
    # Ink emits many words separated by cursor commands rather than spaces.
    # Match both normal text and that actual PTY representation without granting
    # trust or treating a menu selection marker as a ready command prompt.
    def last(phrase):
        matches=list(re.finditer(r'\s*'.join(re.escape(p) for p in phrase.split()),clean,re.I))
        return matches[-1].start() if matches else -1
    found = [(max(last(p) for p in phrases), phase, label) for phase,label,phrases in markers]
    update=last('Update now (runs')
    if provider=='codex' and update>=0 and last('Skip until next version')>update:
        found.append((update,'update','原生工具正在提示升级'))
    at, phase, label = max(found)
    # A later actual CLI prompt supersedes a prior setup screen in scrollback.
    # Ink redraws in place with CR-only lines after dismissing a modal.
    # A CR-delimited real prompt must supersede that modal in scrollback too.
    prompts = list(re.finditer(r'(?:^|[\r\n])[^\S\r\n]*[❯>›][^\S\r\n]*(?:[\r\n]|$)', clean))
    last_prompt = prompts[-1].start() if prompts else -1
    if provider=='codex':last_prompt=max(last_prompt,last('Ask Codex'))
    if at >= 0 and at > last_prompt:
        return dict(phase=phase, label=label, phase_sha256=digest(clean.encode()))
    if provider=='codex' and last('Ask Codex')>at:
        return dict(phase='ready',label='已进入原生 Agent 会话')
    if last_prompt >= 0 and ('/effort' in clean[last_prompt:] or 'for shortcuts' in clean[last_prompt:] or 'Ask Codex' in clean):
        return dict(phase='ready',label='已进入原生 Agent 会话')
    return dict(phase='output' if clean.strip() else 'starting',label='原生工具已输出，等待输入状态确认' if clean.strip() else '正在启动原生 Agent')
