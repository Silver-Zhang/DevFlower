"""Exact workspace permission profile for opted-in Agent execution."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Callable

from orchestration.common import Blocked, canonical, cli_probe_env, digest
from orchestration.privacy import credentials
from studio.agent_extensions import skill_files, skill_header
from studio.native_codex_image import NativeImage
from studio.session_sources import opened

from .codex_terminals import guarded_workspace, pin_backend_image
from .session_identity import StorageRootIdentity


@dataclass(frozen=True)
class AgentScope:
    executable: str
    image: NativeImage
    options: tuple
    verify: Callable


def scope_options(profile, cwd, executable, access):
    if access not in ('read', 'workspace-write'):
        raise Blocked('Codex Agent 的工作区权限无效。')
    fields = {':root': 'deny', ':minimal': 'read',
              cwd: 'write' if access == 'workspace-write' else 'read', executable: 'read'}
    filesystem = '{' + ','.join(json.dumps(key) + '=' + json.dumps(value)
                                for key, value in fields.items()) + '}'
    return ('-c', f'permissions.{profile}.filesystem={filesystem}',
            '-c', f'permissions.{profile}.network.enabled=false',
            '-c', f'default_permissions="{profile}"',
            '-c', 'notify=[]', '-c', 'features.code_mode_host=false',
            '-c', 'features.default_mode_request_user_input=false',
            '-c', 'shell_environment_policy={inherit="none",include_only=["PATH","LANG"],'
                  'set={PATH="/usr/bin:/bin",LANG="C.UTF-8"}}')


def agent_skill_options(binding, cwd, inventory):
    """Select native Skill files by exact path and pin their safe contents."""
    selected = binding['v2_agent_skills']
    if not isinstance(selected, list) or len(selected) > 24 or not isinstance(inventory, dict):
        raise Blocked('Codex Agent 的原生 Skill 授权无效。')
    home = Path(binding['home'])
    roots = {Path(cwd) / '.agents/skills', home / '.agents/skills', Path(binding['config_root']) / 'skills'}
    known = [value.encode() for value in credentials(home) if value]
    chosen = set()
    for skill in selected:
        value = skill['path']
        path = Path(value)
        if (path.name != 'SKILL.md' or path.parent.parent not in roots or value != value.strip()
                or any(char in value for char in '()\r\n\x00')):
            raise Blocked('Codex Agent 选择的 Skill 路径无效。')
        files = skill_files(path.parent)
        metadata = skill_header(files['SKILL.md'].decode())
        fingerprint = digest(canonical({name: digest(data) for name, data in files.items()}))
        if (metadata['name'] != skill['name'] or fingerprint != skill['sha256']
                or any(needle in data for needle in known for data in files.values())):
            raise Blocked('Codex Agent 选择的 Skill 已变化或含受保护内容。')
        chosen.add(value)
    groups = inventory.get('data')
    if not isinstance(groups, list):
        raise Blocked('Codex CLI 未返回可核验的原生 Skills。')
    groups = [group for group in groups if isinstance(group, dict) and group.get('cwd') == cwd]
    if len(groups) != 1 or not isinstance(groups[0].get('skills'), list):
        raise Blocked('Codex CLI 未返回当前工作区的原生 Skills。')
    paths = set()
    for skill in groups[0]['skills']:
        path = skill.get('path') if isinstance(skill, dict) else None
        if isinstance(path, str) and path.startswith('/') and len(path) <= 4096:
            paths.add(path)
    if len(paths) > 240 or not chosen <= paths:
        raise Blocked('Codex Agent 的原生 Skill 目录已变化或超出核验范围。')
    entries = ','.join('{path='+json.dumps(path)+',enabled='+('true' if path in chosen else 'false')+'}'
                       for path in sorted(paths))
    if len(entries.encode()) > 64000:
        raise Blocked('Codex Agent 的原生 Skill 目录超出核验范围。')
    return ['-c', 'skills.include_instructions=false', '-c', 'skills.bundled.enabled=false',
            '-c', 'skills.config=['+entries+']']


@contextmanager
def prepare_agent_scope(binding, cwd):
    access = binding['v2_agent_scope']
    if (access not in ('read', 'workspace-write') or cwd != binding.get('cwd')
            or binding.get('provider') != 'codex' or not binding.get('_metadata_guard')
            or binding.get('execution_backend') != 'native-local'
            or binding.get('native_interaction') != 'ordinary'
            or binding.get('role') not in ('reviewer', 'implementer')
            or (binding.get('role') == 'implementer') != (access == 'workspace-write')):
        raise Blocked('Codex Agent 的授权范围不匹配。')
    state = SimpleNamespace(state=Path(binding['_metadata_guard']))
    root = Path(cwd)
    if any(root.is_relative_to(protected) or protected.is_relative_to(root)
           for protected in map(Path, binding.get('native_protected', []))):
        raise Blocked('Codex Agent 的工作区与受保护路径重叠。')
    with ExitStack() as held:
        directory = held.enter_context(opened(cwd, directory=True))
        guarded_workspace(state, binding, directory)
        image = held.enter_context(NativeImage(binding['executable'], expected_sha256=binding['_executable_sha256']))
        private = Path(held.enter_context(TemporaryDirectory(prefix='df-agent-scope-', dir='/tmp')))
        executable, _, verify_image = pin_backend_image(private, image, held)
        profile = 'devflower_agent_' + secrets.token_hex(16)
        options = scope_options(profile, cwd, executable, access)

        def verify():
            if StorageRootIdentity.capture(cwd).fields() != binding['cwd_identity']:
                raise Blocked('Codex Agent 的工作区身份已变化。')
            guarded_workspace(state, binding, directory)
            verify_image()

        scratch = private / 'probe'
        scratch.mkdir(mode=0o700)
        (scratch / 'tmp').mkdir(mode=0o700)
        try:
            result = subprocess.run([executable, *options, 'sandbox', '--permission-profile', profile,
                                     '-C', cwd, '--', '/usr/bin/true'], cwd=cwd, env=cli_probe_env(scratch),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=8)
        except (OSError, subprocess.SubprocessError):
            raise Blocked('Codex Agent 的操作系统沙箱未能建立。') from None
        if result.returncode:
            raise Blocked('Codex Agent 的操作系统沙箱未能建立。')
        verify()
        yield AgentScope(executable, image, options, verify)
