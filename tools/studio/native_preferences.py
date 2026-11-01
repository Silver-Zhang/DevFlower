"""Reuse completed display onboarding, never authentication or workspace trust."""
import json
import os
from pathlib import Path
from .session_sources import opened
from orchestration.common import canonical, Blocked


def inherit_claude_display(binding, native_root):
    home = Path(binding['home'])
    if binding.get('provider') != 'claude' or home == Path.home() or not home.resolve().is_relative_to(Path(native_root).resolve()):
        return False
    sources = [Path(src).parent.parent / '.claude.json' for src, dest in binding.get('readonly_files', [])
               if dest == '.claude/settings.json' and Path(src).parent.name == '.claude']
    if len(sources) != 1 or sources[0] == home / '.claude.json':
        return False
    def read(path):
        if path.is_symlink(): raise Blocked('原生显示偏好不能是符号链接')
        if not path.exists(): return {}
        with opened(path) as fd:
            raw = os.read(fd, 200001)
        if len(raw) > 200000: raise Blocked('原生显示偏好超过读取预算')
        value = json.loads(raw)
        if not isinstance(value, dict): raise Blocked('原生显示偏好格式不受支持')
        return value
    try:
        prior = read(home / '.claude.json')
        source = read(sources[0])
    except (OSError, ValueError):
        return False
    # An explicitly reset onboarding preference belongs to the operator.
    if 'hasCompletedOnboarding' in prior or source.get('hasCompletedOnboarding') is not True:
        return False
    result = dict(prior, hasCompletedOnboarding=True)
    for key in ('theme', 'lastOnboardingVersion'):
        if key not in prior and isinstance(source.get(key), str) and len(source[key]) <= 80:
            result[key] = source[key]
    # No projects/hasTrustDialogAccepted, permissions, hooks, API keys, env,
    # account or tokens are copied. Only a managed HOME under the held lease.
    path = home / '.claude.json'
    temp = home / ('.devflower-display-' + str(os.getpid()))
    with temp.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600);stream.write(canonical(result))
    os.replace(temp, path)
    return True
