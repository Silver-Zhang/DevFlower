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

from orchestration.common import Blocked, canonical, cli_probe_env, digest
from orchestration.privacy import credentials
from studio.graph import Invalid
from studio.local_claude_adapter import LocalClaude, claude_args, claude_secrets
from studio.local_codex_adapter import LocalCodex
from studio.model_registration import profile_identity
from studio.native_codex_exec import exec_args, provider_overrides, resume_exec_args
from studio.native_codex_image import NativeImage, binary_digest, sealed_executable
from studio.session_sources import opened

from .claude_session_profile import discover_cli, environment_identity, startup_environment
from .claude_native_catalog import model_id, observe_native_catalog
from .claude_agent_scope import scope_supported, scoped_environment
from .native_history_config import unique_pairs
from .native_agent_homes import NativeAgentHomes
from .native_agent_profiles import approved_paths, public_profile, read_profile, require_frozen_profile
from .provider import CodexProvider
from .session_identity import StorageRootIdentity
from .session_lease import CODEX_COORDINATION_VERSION

CATALOG = 'agent-provider-catalog'
EFFORT = re.compile(r'[a-z][a-z0-9_-]{0,23}')
ALIASES = {'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'haiku', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'sonnet',
           'ANTHROPIC_DEFAULT_OPUS_MODEL': 'opus', 'ANTHROPIC_DEFAULT_FABLE_MODEL': 'fable'}
READ_ERRORS = (Blocked, Invalid, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError)


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

    def __init__(self, provider, error, profiles=None):
        self.provider = provider
        self.error = error
        self.profiles = profiles or {}

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
            scoped = ask and not native.native_home and os.access('/usr/bin/bwrap', os.X_OK)
            row['modes'] = {
                'Ask': mode(ask, ['exec', '--json'], '可选择仅对话，或允许模型读取当前工作区。'),
                'Plan': mode(False, ['app-server', 'collaborationMode=plan'], '原生 Plan 只规划。转入 Agent 需要另行确认。'),
                'Agent': mode(scoped, ['exec', '-c', 'default_permissions=<scoped-profile>'],
                              '使用原生工作区沙箱执行模型工具。请选择工作区读写权限。'),
            }
            row['permissions'] = ['none', 'workspace-read', 'workspace-write'] if scoped else ['none']
            catalog, defaults = native.discover(agent_defaults=True)
            row['modes']['Plan']['enabled'] = scoped and defaults.get('plan') is True
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
            if native.native_home:
                row['native_home_sha256'] = native.native_home['sha256']
            row.update(authenticated=True if catalog.get('authenticated') and not catalog.get('error') else None,
                       catalog_available=bool(row['models']) and not catalog.get('error'),
                       error=catalog.get('error'), refreshed_at=time.time())
        except READ_ERRORS:
            row['error'] = '无法读取 Codex CLI 元数据。请核对本机版本与配置。'
        return row

    def binding(self, workspace, agent):
        access = {('Ask', 'none'): None, ('Ask', 'workspace-read'): 'read', ('Plan', 'none'): None,
                  ('Agent', 'workspace-write'): 'workspace-write'}
        mode_permission = agent['mode'], agent['permission']
        if not self.provider or self.provider.version != CODEX_COORDINATION_VERSION or mode_permission not in access:
            raise Invalid('Codex 支持 Ask、仅规划的 Plan 和获准工作区读写的 Agent。')
        scope = access[mode_permission]
        isolated = self.provider.native_home
        if isolated and (mode_permission != ('Ask','none') or agent.get('skills')):
            raise Invalid('独立 Codex HOME 仅支持不开放文件或命令工具的 Ask。')
        named = (selected_profile(self.provider_id,self.provider.config_root,self.profiles,agent['native_profile'])
                 if agent.get('native_profile') and not isolated else None)
        plan = agent['mode'] == 'Plan'
        if named and plan:
            raise Invalid('原生 Plan 使用默认 Codex 配置。')
        if plan:
            observed = self.provider.store.db.get(CATALOG, self.provider_id)
            if not observed.get('modes', {}).get('Plan', {}).get('enabled') or agent.get('skills'):
                raise Invalid('请核验 Codex 原生 Plan，且为本次 Plan 关闭 Skills。')
        if scope and not os.access('/usr/bin/bwrap', os.X_OK):
            raise Invalid('本机 Bubblewrap 不可用，未开放工作区工具。')
        model, effort = agent['model'],agent['effort']
        if named:
            model, effort = model or named['model'],effort or named['effort']
        if model is None or effort is None:
            row = self.provider.store.db.get(CATALOG, self.provider.catalog_key)
            default = (row.get('default_model') or {}).get('id')
            model = model or default
            effort = effort or (row.get('default_effort') if model == default else next(
                (item['default_effort'] for item in row['models'] if item['id'] == model), None))
        if not model_id(model):
            raise Invalid('Codex 需要选择目录模型或输入明确的自定义模型。')
        node = dict(provider=self.provider_id, model=model, effort=effort, timeout=agent['timeout'])
        binding = self.provider.binding(workspace, node, manual_model=agent.get('model_source') in ('manual', 'native_default'))
        if scope:
            from .codex_code_mode import companion_identity
            binding.update(v2_agent_scope=scope, no_tools=False,
                           role='implementer' if scope == 'workspace-write' else 'reviewer',
                           v2_codex_code_host_sha256=companion_identity(binding['executable']))
        if plan:
            binding['v2_agent_plan'] = True
        if named:
            binding['native_profile'] = named
        if named or isolated:
            binding['v2_development_untrusted'] = True
        return binding

    def command(self, binding, *, config=None, session_id=None, resume=False, output_schema=None):
        if resume != (session_id is not None):
            raise Invalid('Codex 新建会话的 UUID 由 CLI 返回；续聊需要明确 UUID。')
        if binding.get('v2_agent_plan') or binding.get('transition_from'):
            raise Blocked('原生 Plan 与 Agent 转入需要已核验的 Codex App Server。')
        if binding.get('v2_agent_scope'):
            raise Blocked('工作区工具命令必须经实际沙箱与 CLI 映像核验后构建。')
        args = exec_args(binding, provider_overrides(config), schema=output_schema)
        return resume_exec_args(args, session_id) if resume else args

    def require_local(self, binding):
        if not self.provider:
            raise Blocked('已冻结的 Codex CLI 当前不可用。')
        self.provider.require_local(binding)
        require_frozen_profile(binding)

    def transport(self, binding, limits, *, managed_resume=False):
        require_frozen_profile(binding)
        if binding.get('v2_agent_plan') or binding.get('transition_from'):
            from .codex_agent_plan import CodexPlan
            return CodexPlan(binding, limits)
        return LocalCodex(binding, limits)


def selected_profile(provider, root, profiles, name):
    if name is None:
        return None
    path = profiles.get((provider,name))
    if not path:
        raise Invalid('此原生命名配置未由本机启动参数登记。')
    return read_profile(provider,name,path,root)


class ClaudeCLI:
    provider_id = 'claude'
    label = 'Claude Code'

    def __init__(self, store, profiles=None, *, native_home=None):
        self.store = store
        self.profiles = profiles or {}
        self.native_home = native_home
        self.catalog_key = 'claude:' + native_home['id'] if native_home else 'claude'

    def credential_environment(self):
        return {} if self.native_home else startup_environment(os.environ)

    def profile(self, *, allow_unsupported=False):
        if not self.native_home and os.environ.get('CLAUDE_CONFIG_DIR'):
            raise Invalid('当前 Agent 仅支持默认 Claude HOME 配置。')
        home = Path(self.native_home['home']) if self.native_home else Path.home()
        root = home / '.claude'
        return dict(discover_cli(details=True, allow_unsupported=allow_unsupported),
                    provider=self.provider_id, home=str(home), config_root=str(root),
                    runtime_profile='default-claude', execution_backend='native-local',
                    local_lock_root=str(root / '.devflower-writer-locks'), role='reviewer', chat_only=True,
                    readonly_files=[], **({'native_home':self.native_home} if self.native_home else {}))

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
            env = self.credential_environment()
            scoped = not self.native_home and 'acceptEdits' in profile['permission_modes'] and scope_supported()
            if scoped:
                try:
                    scoped_environment(profile, env)
                except Blocked:
                    scoped = False
            row['modes']['Agent'] = mode(scoped, ['--restricted', '--permission-mode', 'acceptEdits'],
                                        '仅在已授权工作区使用文件工具和受控 Bash。' if scoped else
                                        '需要已核验的 Linux 隔离和本机 Claude API 认证配置。')
            row['permissions'] = ['none','workspace-read','workspace-write'] if scoped else ['none']
            if self.native_home:
                for name in ('Plan','Agent'):
                    row['modes'][name] = mode(False, [], '独立 Claude HOME 当前只支持无工具 Ask。')
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
            menu_default = None
            try:
                native = observe_native_catalog(profile, self.store.state, env)
                menu_default = native['menu_default_model']
                for item in native['models']:
                    if item['id'] != 'default':
                        choices[item['id']] = dict(model_option(item['id'], 'cli_catalog', efforts=item['efforts']),
                                                   resolved_model=item['resolved_model'])
                row['catalog_available'] = True
            except Blocked:
                row['catalog_available'] = False
            for key, alias in ALIASES.items():
                value = model_id(env.get(key) or configured_env.get(key), known)
                if value:
                    choice = choices.get(alias, model_option(alias, 'cli_alias'))
                    choices[alias] = dict(choice, source='configured_alias', configured_model=value)
                    choices.setdefault(value, model_option(value, 'native_config'))
            configured = model_id(env.get('ANTHROPIC_MODEL') or configured_env.get('ANTHROPIC_MODEL') or settings.get('model'), known)
            if configured:
                choices[configured] = dict(choices.get(configured, model_option(configured, 'native_config')), source='native_config')
                row['default_model'] = {'id': configured, 'source': 'native_config'}
            elif menu_default:
                row['default_model'] = {'id': menu_default, 'source': 'cli_catalog'}
            effort = env.get('CLAUDE_CODE_EFFORT_LEVEL') or configured_env.get('CLAUDE_CODE_EFFORT_LEVEL') or settings.get('effortLevel')
            if effort in row['efforts']:
                row['default_effort'] = effort
            row.update(models=list(choices.values()), model_note=(
                'CLI 原生目录和配置不代表模型可调用。' if row['catalog_available'] else
                'CLI 原生目录尚未确认。当前显示本机配置和已核验的别名。'))
            with sealed_executable(profile['executable'], 'devflower-claude',
                                   expected_sha256=profile['_executable_sha256']) as (fd, _):
                result = cli_read(profile['executable'], fd, ['--safe-mode', '--setting-sources', 'user', 'auth',
                                  'status', '--json'], self.store.state, dict(cli_probe_env(profile['home']), **env))
            auth = json.loads(result.stdout, object_pairs_hook=unique_pairs)
            if result.returncode in (0, 1) and type(auth.get('loggedIn')) is bool:
                row['authenticated'] = auth['loggedIn']
            if self.native_home:
                if (row['authenticated'] is not True or auth.get('configDirectory') != str(root)
                        or auth.get('projectsDirectory') != str(root/'projects')):
                    raise Blocked('CLI 未确认已登记 Claude HOME 的账户和历史根。')
                row.update(native_home_sha256=self.native_home['sha256'],
                           executable_sha256=profile['_executable_sha256'],
                           account_sha256=digest(canonical(auth)))
        except READ_ERRORS:
            row['error'] = '无法读取 Claude Code 元数据。请核对安装版本与本机配置。'
        return row

    def binding(self, workspace, agent):
        profile = self.profile()
        modes = {('Ask','none'):'manual',('Plan','none'):'plan',('Ask','workspace-read'):'manual',
                 ('Plan','workspace-read'):'plan',('Agent','workspace-write'):'acceptEdits'}
        permission = agent.get('permission','none')
        native = modes.get((agent['mode'],permission))
        if self.native_home and ((agent['mode'],permission) != ('Ask','none') or agent.get('skills')):
            raise Invalid('独立 Claude HOME 当前只支持无工具 Ask。')
        if not native or native not in profile['permission_modes']:
            raise Invalid('此 Claude 工作模式尚未核验。')
        scoped = permission in ('workspace-read','workspace-write')
        if scoped and not scope_supported():
            raise Invalid('本机 Bubblewrap 不可用，未开放 Claude 文件和命令工具。')
        named = (selected_profile(self.provider_id,profile['config_root'],self.profiles,agent.get('native_profile'))
                 if not self.native_home else None)
        model, effort, budget = agent['model'],agent['effort'],agent.get('max_budget_usd')
        if named:
            model, effort = model or named['model'],effort or named['effort']
        if model is not None and not model_id(model):
            raise Invalid('Claude 模型格式无效。')
        if effort is not None and effort not in profile['efforts']:
            raise Invalid('当前 Claude CLI 未提供所选推理强度。')
        if model is not None and effort is not None and agent.get('model_source') == 'catalog':
            try:
                catalog = self.store.db.get(CATALOG, self.catalog_key)
            except Invalid:
                catalog = {}
            known = next((item['efforts'] for item in catalog.get('models', []) if item['id'] == model), [])
            if known and effort not in known:
                raise Invalid('Claude 原生目录未报告所选模型与推理强度组合。')
        if budget is not None and (not profile['native_budget'] or not isinstance(budget, str)
                                   or not re.fullmatch(r'[0-9]{1,4}(?:\.[0-9]{1,4})?', budget) or float(budget) <= 0):
            raise Invalid('Claude 原生费用上限无效。')
        env = self.credential_environment()
        if self.native_home:
            catalog = self.store.db.get(CATALOG, self.catalog_key)
            if (catalog.get('authenticated') is not True or catalog.get('error') or not catalog.get('catalog_available')
                    or time.time()-catalog.get('refreshed_at',0)>300
                    or catalog.get('native_home_sha256') != self.native_home['sha256']
                    or catalog.get('executable_sha256') != profile['_executable_sha256']):
                raise Invalid('请先刷新当前独立 Claude HOME 的目录和账户。')
        binding = {key: value for key, value in profile.items()
                   if key not in ('supported', 'efforts', 'aliases', 'permission_modes', 'native_budget')}
        binding.update(project=workspace, model=model, effort=effort, session_id=None,
                       timeout_seconds=agent['timeout'], execution_mode=agent['mode'],
                       agent_permission_mode=native, max_budget_usd=budget,
                       conversation_format='text',
                       cli_environment_sha256=environment_identity(env),
                       root_identity=StorageRootIdentity.capture(binding['config_root']).fields())
        if scoped:
            binding.update(v2_claude_scope=True, _metadata_guard=str(self.store.state))
            if permission=='workspace-read':
                binding['v2_claude_read_only'] = True
            scoped_environment(binding, env)
        binding['direct_environment_sha256'] = profile_identity(binding)
        if named:
            binding['native_profile'] = named
        if self.native_home:
            binding['account_sha256'] = catalog['account_sha256']
        return binding

    def require_local(self, binding):
        with opened(binding['executable']) as fd:
            image = binary_digest(fd)
        if (image != binding['_executable_sha256'] or profile_identity(binding) != binding['direct_environment_sha256']
                or StorageRootIdentity.capture(binding['config_root']).fields() != binding['root_identity']
                or environment_identity(self.credential_environment()) != binding['cli_environment_sha256']):
            raise Blocked('Claude 原生 CLI 或配置已变化，未发送任务。')
        require_frozen_profile(binding)

    def command(self, binding, *, config=None, session_id=None, resume=False, output_schema=None):
        try:
            if str(uuid.UUID(session_id)) != session_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise Invalid('Claude 启动需要明确的原生 UUID。') from None
        if binding.get('v2_claude_scope'):
            raise Blocked('Claude 工作区工具需要已核验的隔离运行环境。')
        return claude_args(binding, session_id, resume=resume, output_schema=output_schema)

    def transport(self, binding, limits, *, managed_resume=False):
        self.require_local(binding)
        return LocalClaude(binding, limits, credential_env=self.credential_environment(), managed_resume=managed_resume)


class ProviderRegistry:
    def __init__(self, store, codex=None, codex_error=None, *, native_profiles=(), native_homes=(), executable=None):
        self.store = store
        self.profiles = approved_paths(native_profiles,Path.home())
        self.homes = NativeAgentHomes(store,native_homes,self.profiles)
        self.executable = executable
        self.isolated = {}
        self.adapters = {adapter.provider_id:adapter for adapter in (
            CodexCLI(codex,codex_error,self.profiles),ClaudeCLI(store,self.profiles))}

    def selected(self, provider, name):
        if self.homes.get(provider,name):
            return self.homes.require(provider,name)
        return selected_profile(provider,str(Path.home()/('.'+provider)),self.profiles,name)

    def adapter(self, provider, name=None):
        if not isinstance(provider, str) or provider not in self.adapters:
            raise Invalid('当前只支持 Codex CLI 与 Claude Code。')
        if not self.homes.get(provider,name):
            return self.adapters[provider]
        home = self.homes.require(provider,name)
        key = provider,name
        if key not in self.isolated:
            self.isolated[key] = (CodexCLI(CodexProvider(self.store,self.executable,native_home=home),None)
                                  if provider=='codex' else ClaudeCLI(self.store,native_home=home))
        return self.isolated[key]

    def adapter_for(self, binding):
        self.homes.require_binding(binding)
        return self.adapter(binding['provider'],(binding.get('native_home') or {}).get('id'))

    def discover(self, provider, name=None):
        if name is not None and (not isinstance(provider,str) or not isinstance(name,str) or not self.homes.get(provider,name)):
            raise Invalid('请选择已登记的独立原生 HOME。')
        adapter = self.adapter(provider,name)
        key = self.catalog_key(provider,name)
        self.store.db.put(CATALOG, key, adapter.observe())
        return self.listing()

    @staticmethod
    def catalog_key(provider, name=None):
        return provider+':'+name if name else provider

    def catalog_row(self, provider, adapter, name=None):
        try:
            row = self.store.db.get(CATALOG, self.catalog_key(provider,name))
        except Invalid:
            row = empty(provider, adapter.label)
        refreshed = row.get('refreshed_at')
        stale = bool(not refreshed or row.get('error') or time.time()-refreshed>300)
        if name is not None:
            stale |= row.get('native_home_sha256') != self.homes.get(provider,name)['sha256']
        return dict({key:value for key,value in row.items() if key not in (
                    'native_home_sha256','account_sha256','executable_sha256')}, stale=stale)

    def listing(self):
        rows = [self.catalog_row(provider,adapter) for provider,adapter in self.adapters.items()]
        profiles = []
        for provider,name in self.profiles:
            try:
                profiles.append(dict(public_profile(self.selected(provider,name)),available=True))
            except READ_ERRORS:
                profiles.append(dict(id=name,provider=provider,available=False))
        for provider,name in self.homes.entries:
            available = True
            try:
                self.homes.require(provider,name)
            except READ_ERRORS:
                available = False
            catalog = self.catalog_row(provider,self.adapters[provider],name)
            profiles.append(dict(id=name,provider=provider,kind='isolated-home',available=available,catalog=catalog,
                                 model=(catalog.get('default_model') or {}).get('id'),effort=catalog.get('default_effort')))
        return {'providers':rows,'profiles':profiles}
