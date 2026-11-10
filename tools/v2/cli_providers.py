"""Installed CLI adapters for the Agent Manager. Discovery never starts inference."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
import uuid

from orchestration.common import Blocked, cli_probe_env
from orchestration.privacy import credentials
from studio.graph import Invalid
from studio.local_claude_adapter import LocalClaude, claude_args, claude_secrets
from studio.local_codex_adapter import LocalCodex
from studio.model_registration import profile_identity
from studio.native_codex_exec import exec_args, provider_overrides, resume_exec_args
from studio.native_codex_image import NativeImage, binary_digest, sealed_executable
from studio.session_sources import opened

from .claude_session_profile import discover_cli, environment_identity, startup_environment
from .native_history_config import unique_pairs
from .session_identity import StorageRootIdentity
from .session_lease import CODEX_COORDINATION_VERSION

CATALOG = 'agent-provider-catalog'
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}')
EFFORT = re.compile(r'[a-z][a-z0-9_-]{0,23}')
ALIASES = {'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'haiku', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'sonnet',
           'ANTHROPIC_DEFAULT_OPUS_MODEL': 'opus', 'ANTHROPIC_DEFAULT_FABLE_MODEL': 'fable'}
READ_ERRORS = (Blocked, Invalid, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError)


def model_id(value, literals=()):
    return (value if isinstance(value, str) and MODEL.fullmatch(value) and '://' not in value
            and not any(secret and secret in value for secret in literals) else None)


def model_option(value, source, *, efforts=(), default_effort=None):
    return {'id': value, 'name': value, 'source': source, 'efforts': list(efforts),
            'default_effort': default_effort, 'inference_verified': False}


def mode(enabled, native_args, detail):
    return dict(enabled=enabled, native_args=native_args, detail=detail)


def empty(provider, label, *, error=None):
    return dict(provider=provider, label=label, version=None, installed=None, authenticated=None,
                models=[], efforts=[], default_model=None, default_effort=None, modes={}, error=error,
                inference_verified=False, native_budget=False, refreshed_at=None)


def cli_read(path, fd, args, cwd, env):
    result = subprocess.run([path, *args], cwd=cwd, env=env, executable=f'/proc/self/fd/{fd}',
                            pass_fds=(fd,), stdin=subprocess.DEVNULL, capture_output=True, timeout=12)
    if len(result.stdout) > 300_000:
        raise Blocked('CLI 元数据超过读取上限。')
    return result


class CodexCLI:
    provider_id = 'codex'
    label = 'Codex CLI'

    def __init__(self, provider, error):
        self.provider = provider
        self.error = error

    def observe(self):
        if self.provider is None:
            return dict(empty(self.provider_id, self.label, error=self.error), installed=False)
        native = self.provider
        row = dict(empty(self.provider_id, self.label), installed=True, version=native.version)
        try:
            with TemporaryDirectory(prefix='df-codex-agent-help-') as scratch:
                with NativeImage(native.image['path'], expected_sha256=native.image['sha256']) as image:
                    probe = cli_read(image.path, image.fd, ['exec', '--help'], scratch, cli_probe_env(scratch))
            ask = native.version == CODEX_COORDINATION_VERSION and probe.returncode == 0 and all(
                flag in probe.stdout for flag in (b'--json', b'--model', b'--ignore-user-config', b'--strict-config'))
            row['modes'] = {
                'Ask': mode(ask, ['exec', '--json'], '仅对话，禁止模型工具。'),
                'Plan': mode(False, [], '此 Codex 工作流尚未核验原生 Plan 入口。'),
                'Agent': mode(False, ['exec', '--sandbox', 'workspace-write'], '工作区工具隔离尚未启用。'),
            }
            catalog, defaults = native.discover(agent_defaults=True)
            known = credentials(native.home)
            for item in catalog['models']:
                name = model_id(item['id'], known)
                if not name:
                    continue
                efforts = [value for value in item['efforts'] if isinstance(value, str) and EFFORT.fullmatch(value)
                           and value != 'ultra' and value not in known]
                is_default, default_effort = defaults.get('catalog', {}).get(name, (False, None))
                row['models'].append(model_option(name, 'cli_catalog', efforts=efforts,
                                                  default_effort=default_effort if default_effort in efforts else None))
                if is_default and row['default_model'] is None:
                    row['default_model'] = {'id': name, 'source': 'cli_catalog'}
            row['efforts'] = list(dict.fromkeys(effort for item in row['models'] for effort in item['efforts']))
            configured = model_id(defaults.get('model'), known)
            if configured:
                row['default_model'] = {'id': configured, 'source': 'native_config'}
            selected_effort = defaults.get('effort')
            if selected_effort in row['efforts']:
                row['default_effort'] = selected_effort
            elif row['default_model']:
                row['default_effort'] = next((item['default_effort'] for item in row['models']
                    if item['id'] == row['default_model']['id']), None)
            row.update(authenticated=True if catalog.get('authenticated') and not catalog.get('error') else None,
                       catalog_available=bool(row['models']) and not catalog.get('error'),
                       error=catalog.get('error'), refreshed_at=time.time())
        except READ_ERRORS:
            row['error'] = '无法读取 Codex CLI 元数据。请核对本机版本与配置。'
        return row

    def binding(self, workspace, agent):
        if not self.provider or self.provider.version != CODEX_COORDINATION_VERSION or agent['mode'] != 'Ask':
            raise Invalid('当前 Codex Agent 仅开放已核验的 Ask 对话。')
        if not model_id(agent['model']):
            raise Invalid('Codex 需要选择目录模型或输入明确的自定义模型。')
        node = dict(provider=self.provider_id, model=agent['model'], effort=agent['effort'], timeout=agent['timeout'])
        return self.provider.binding(workspace, node, manual_model=agent.get('model_source') == 'manual')

    def command(self, binding, *, config=None, session_id=None, resume=False, output_schema=None):
        if resume != (session_id is not None):
            raise Invalid('Codex 新建会话的 UUID 由 CLI 返回；续聊需要明确 UUID。')
        args = exec_args(binding, provider_overrides(config), schema=output_schema)
        return resume_exec_args(args, session_id) if resume else args

    def require_local(self, binding):
        if not self.provider:
            raise Blocked('已冻结的 Codex CLI 当前不可用。')
        self.provider.require_local(binding)

    def transport(self, binding, limits, *, managed_resume=False):
        return LocalCodex(binding, limits)


class ClaudeCLI:
    provider_id = 'claude'
    label = 'Claude Code'

    def __init__(self, store):
        self.store = store

    def profile(self, *, allow_unsupported=False):
        if os.environ.get('CLAUDE_CONFIG_DIR'):
            raise Invalid('当前 Agent 仅支持默认 Claude HOME 配置。')
        home = Path.home()
        root = home / '.claude'
        return dict(discover_cli(details=True, allow_unsupported=allow_unsupported),
                    provider=self.provider_id, home=str(home), config_root=str(root),
                    runtime_profile='default-claude', execution_backend='native-local',
                    local_lock_root=str(root / '.devflower-writer-locks'), role='reviewer', chat_only=True,
                    readonly_files=[])

    def observe(self):
        row = dict(empty(self.provider_id, self.label), installed=bool(shutil.which('claude')))
        try:
            profile = self.profile(allow_unsupported=True)
            row.update(installed=True, version=profile['cli_version'], refreshed_at=time.time())
            if not profile['supported']:
                row['error'] = '当前 Claude Code 版本的隔离与启动参数尚未核验。'
                return row
            row.update(efforts=profile['efforts'],
                       native_budget=profile['native_budget'], refreshed_at=time.time())
            row['modes'] = {name: mode(value in profile['permission_modes'], ['--permission-mode', value, '--tools', ''], detail)
                            for name, value, detail in (
                                ('Ask', 'manual', '仅对话，禁止模型工具。'),
                                ('Plan', 'plan', '使用原生 Plan 权限模式，禁止模型工具。'))}
            row['modes']['Agent'] = mode(False, [], '尚未核验 Claude 文件工具的工作区写入边界。')
            env = startup_environment(os.environ)
            root = Path(profile['config_root'])
            settings = {}
            try:
                with opened(root / 'settings.json') as fd:
                    settings = json.loads(os.read(fd, 200001), object_pairs_hook=unique_pairs)
                if not isinstance(settings, dict):
                    raise Invalid('Claude 配置无效。')
            except FileNotFoundError:
                pass
            configured_env = settings.get('env') or {}
            if not isinstance(configured_env, dict):
                raise Invalid('Claude 环境配置无效。')
            known = claude_secrets(profile, env)
            choices = {value: model_option(value, 'cli_alias') for value in profile['aliases']}
            for key, alias in ALIASES.items():
                value = model_id(env.get(key) or configured_env.get(key), known)
                if value:
                    choices[alias] = dict(model_option(alias, 'configured_alias'), configured_model=value)
                    choices.setdefault(value, model_option(value, 'native_config'))
            configured = {value for raw in (env.get('ANTHROPIC_MODEL'), configured_env.get('ANTHROPIC_MODEL'),
                                           settings.get('model')) if (value := model_id(raw, known))}
            for value in sorted(configured):
                choices[value] = model_option(value, 'native_config')
            if len(configured) == 1:
                row['default_model'] = {'id': next(iter(configured)), 'source': 'native_config'}
            levels = {value for value in (env.get('CLAUDE_CODE_EFFORT_LEVEL'),
                                          configured_env.get('CLAUDE_CODE_EFFORT_LEVEL'), settings.get('effortLevel'))
                      if isinstance(value, str) and value in row['efforts']}
            if len(levels) == 1:
                row['default_effort'] = next(iter(levels))
            row.update(models=list(choices.values()), catalog_available=False,
                       model_note='已安装 CLI 未提供模型目录命令。原生配置与别名可见，手动输入的模型需实际执行才能核验。')
            with sealed_executable(profile['executable'], 'devflower-claude',
                                   expected_sha256=profile['_executable_sha256']) as (fd, _):
                result = cli_read(profile['executable'], fd, ['--safe-mode', '--setting-sources', 'user', 'auth',
                                  'status', '--json'], self.store.state, dict(cli_probe_env(profile['home']), **env))
            auth = json.loads(result.stdout, object_pairs_hook=unique_pairs)
            if result.returncode in (0, 1) and type(auth.get('loggedIn')) is bool:
                row['authenticated'] = auth['loggedIn']
        except READ_ERRORS:
            row['error'] = '无法读取 Claude Code 元数据。请核对安装版本与本机配置。'
        return row

    def binding(self, workspace, agent):
        profile = self.profile()
        native = {'Ask': 'manual', 'Plan': 'plan'}.get(agent['mode'])
        if not native or native not in profile['permission_modes']:
            raise Invalid('此 Claude 工作模式尚未核验。')
        model, effort, budget = agent['model'], agent['effort'], agent.get('max_budget_usd')
        if model is not None and not model_id(model):
            raise Invalid('Claude 模型格式无效。')
        if effort is not None and effort not in profile['efforts']:
            raise Invalid('当前 Claude CLI 未提供所选推理强度。')
        if budget is not None and (not profile['native_budget'] or not isinstance(budget, str)
                                   or not re.fullmatch(r'[0-9]{1,4}(?:\.[0-9]{1,4})?', budget) or float(budget) <= 0):
            raise Invalid('Claude 原生费用上限无效。')
        env = startup_environment(os.environ)
        binding = {key: value for key, value in profile.items()
                   if key not in ('supported', 'efforts', 'aliases', 'permission_modes', 'native_budget')}
        binding.update(project=workspace, model=model, effort=effort, session_id=None,
                       timeout_seconds=agent['timeout'], execution_mode=agent['mode'],
                       agent_permission_mode=native, max_budget_usd=budget,
                       cli_environment_sha256=environment_identity(env),
                       root_identity=StorageRootIdentity.capture(binding['config_root']).fields())
        binding['direct_environment_sha256'] = profile_identity(binding)
        return binding

    def require_local(self, binding):
        with opened(binding['executable']) as fd:
            image = binary_digest(fd)
        if (image != binding['_executable_sha256'] or profile_identity(binding) != binding['direct_environment_sha256']
                or StorageRootIdentity.capture(binding['config_root']).fields() != binding['root_identity']
                or environment_identity(startup_environment(os.environ)) != binding['cli_environment_sha256']):
            raise Blocked('Claude 原生 CLI 或配置已变化，未发送任务。')

    def command(self, binding, *, config=None, session_id=None, resume=False, output_schema=None):
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Invalid('Claude 启动需要明确的原生 UUID。') from None
        return claude_args(binding, session_id, resume=resume, output_schema=output_schema)

    def transport(self, binding, limits, *, managed_resume=False):
        self.require_local(binding)
        return LocalClaude(binding, limits, credential_env=startup_environment(os.environ), managed_resume=managed_resume)


class ProviderRegistry:
    def __init__(self, store, codex=None, codex_error=None):
        self.store = store
        self.adapters = {adapter.provider_id: adapter for adapter in (CodexCLI(codex, codex_error), ClaudeCLI(store))}

    def adapter(self, provider):
        if not isinstance(provider, str) or provider not in self.adapters:
            raise Invalid('当前只支持 Codex CLI 与 Claude Code。')
        return self.adapters[provider]

    def discover(self, provider):
        adapter = self.adapter(provider)
        self.store.db.put(CATALOG, provider, adapter.observe())
        return self.listing()

    def listing(self):
        rows = []
        for name, adapter in self.adapters.items():
            try:
                row = self.store.db.get(CATALOG, name)
            except Invalid:
                row = empty(name, adapter.label)
            refreshed = row.get('refreshed_at')
            rows.append(dict(row, stale=bool(not refreshed or row.get('error') or time.time() - refreshed > 300)))
        return {'providers': rows}
