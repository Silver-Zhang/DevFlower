"""Conservative presentation from visible native terminal text, not authority."""
import re
from orchestration.common import digest


def _visible_layout(text):
    """Preserve absolute cursor row changes while joining same-row fragments."""
    parts=[];row=None;offset=0
    for match in re.finditer(r'\x1b\[[0-?]*[ -/]*[@-~]',text):
        chunk=text[offset:match.start()];parts.append(chunk)
        if '\n' in chunk or '\r' in chunk:row=None
        move=re.fullmatch(r'\x1b\[(\d*)(?:;(\d*))?H',match.group())
        if move:
            next_row=int(move.group(1) or 1)
            if row is None or next_row!=row:
                parts.append('\n')
            row=next_row
        offset=match.end()
    parts.append(text[offset:])
    return ''.join(parts)


def terminal_phase(provider, text, alive):
    clean = _visible_layout(text[-24000:])
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
    if provider=='codex' and last('Resuming session')>=0:
        resume=last('Resuming session')
        # A placeholder can appear during restoration. Only a later, actual
        # prompt line followed by the CLI's model/effort/cwd footer is evidence
        # of the restored input screen; earlier scrollback is not evidence.
        prompt=re.compile(r'(?:^|[\r\n])[ \t│┃╭╰]*[❯>›][ \t]*Ask\s*Codex[^\r\n]*',re.I)
        candidates=[m for m in prompt.finditer(clean) if m.start()>resume]
        if candidates:
            tail=clean[candidates[-1].end():candidates[-1].end()+1200]
            footer=re.search(r'(?:^|[\r\n])[ \t│┃]*[^\s│┃·|][^\r\n│┃·|]{0,119}?[ \t]*(?:[·|][ \t]*|[ \t]+)(?:none|minimal|low|medium|high|xhigh|max|ultra|default)[ \t]*[·|][ \t]*(?:\d+%[ \t]*(?:context[ \t]*)?left[ \t]*[·|][ \t]*)?(?:~(?:/[^\s│┃]*)?|/[^\s│┃]+)',tail,re.I)
            remainder=tail[footer.end():] if footer else ''
            if (footer and at<candidates[-1].start() and
                    re.fullmatch(r'[\s│┃╭╰─]*(?:\d+%\s+(?:context\s+)?left[\s│┃╭╰─]*)?',remainder,re.I)):
                return dict(phase='ready',label='已进入原生 Agent 会话')
        return dict(phase='resuming',label='正在恢复原生会话')
    if provider=='codex' and last('Ask Codex')>at:
        return dict(phase='ready',label='已进入原生 Agent 会话')
    # Claude can render its plan-mode footer without /effort (for example
    # after resuming a session). Require both the actual empty input prompt
    # and its later footer; quoted output or an earlier footer is not ready.
    plan_footer = (provider == 'claude' and last_prompt >= 0 and
                   last('plan mode on (shift+tab to cycle)') > last_prompt)
    if last_prompt >= 0 and (plan_footer or '/effort' in clean[last_prompt:] or 'for shortcuts' in clean[last_prompt:] or 'Ask Codex' in clean):
        return dict(phase='ready',label='已进入原生 Agent 会话')
    return dict(phase='output' if clean.strip() else 'starting',label='原生工具已输出，等待输入状态确认' if clean.strip() else '正在启动原生 Agent')
