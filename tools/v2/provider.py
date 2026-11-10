"""One explicitly bound installed Codex Provider; metadata never starts a turn."""
from contextlib import nullcontext
import os
from pathlib import Path
import shutil
import re
import subprocess
import time

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.native_codex_image import NativeImage, resolve
from studio.native_codex_rpc import CodexRPC, metadata_guard
from studio.model_catalog import codex_models
from studio.model_registration import direct_environment_identity

ROOT = Path(__file__).resolve().parents[2]


def public_provider_error(error):
    """Native RPC/relay text is untrusted, including URLs and reflected secrets."""
    text = str(error).lower()
    if '未找到 codex cli' in text:
        return '未找到 Codex CLI；编辑和保存仍可使用。请在原生客户端安装并登录后重启本机服务'
    if 'codex_home' in text:
        return '当前 CODEX_HOME 与本用户原生配置根不同；编辑和保存仍可使用，请核对原生账户配置后重启服务'
    if re.search(r'401|403|auth|credential|login|认证|登录|账户', text):
        return 'Codex 认证或账户配置未通过，请在原生客户端核对登录和访问权限；未自动重试'
    if re.search(r'429|quota|rate.limit|额度', text):
        return 'Codex 返回额度或速率限制，请核对账户状态后再明确启动；未自动重试'
    if re.search(r'timeout|timed.out|connect|network|超时|连接', text):
        return 'Codex 服务连接未完成，请检查本机服务与网络后重试；未自动重试'
    if re.search(r'effort|unsupported|invalid.parameter|模型|强度', text):
        return 'Codex 未确认所选模型或推理参数，请刷新真实目录并核对选择；未自动重试'
    if re.search(r'receipt|回执|schema|结构化|会话|session', text):
        return 'Codex 回执或会话证据未确认，请保留本次运行并核验；未自动重发'
    if re.search(r'硬链接|hardlink', text):
        return 'Codex 保护目录的硬链接核验未通过，请检查保护范围并保留本次运行；未自动放宽权限或重发'
    if re.search(r'沙箱|权限|工具|sandbox|capability|tool', text):
        return 'Codex 权限或工具能力核验未通过，请检查已安装版本与本机支持；未自动放宽权限或重发'
    return 'Codex 操作未完成，请检查原生客户端、配置与权限；原始错误内容未公开，未自动重试'


class CodexProvider:
    def __init__(self, store, executable=None):
        self.store = store
        selected = executable or shutil.which('codex')
        if not selected:
            raise Blocked('未找到 Codex CLI；编辑和保存仍可使用，请在原生客户端安装并登录后重启')
        self.image = resolve(selected)
        self.home = str(Path.home())
        self.config_root = os.environ.get('CODEX_HOME') or str(Path(self.home) / '.codex')
        if self.config_root != str(Path(self.home) / '.codex'):
            raise Blocked('当前 CODEX_HOME 不是本用户原生配置根；不会复制认证或静默改用其他账户')
        self.version = subprocess.check_output([self.image['path'], '--version'], timeout=10, text=True).strip()[:160]

    def base(self, model, effort):
        return dict(provider='codex', executable=self.image['path'], home=self.home, config_root=self.config_root,
                    execution_backend='native-local', native_interaction='ordinary', project='v2',
                    role='reviewer', execution_mode='Ask', session_id=None, readonly_files=[], read_paths=[],
                    skills=[], subagents='disabled', no_tools=True,
                    direct_environment_version=3, direct_environment_scope='native-codex-ordinary',
                    native_protected=[str(ROOT), str(self.store.state), self.config_root,
                                      str(Path(self.home) / '.claude'), str(Path(self.home) / '.claude.json')],
                    model=model, effort=effort, _metadata_guard=str(self.store.state),
                    _executable_sha256=self.image['sha256'])

    def cached(self):
        try:
            row = self.store.db.get('provider-catalog', 'codex')
        except Invalid:
            return {'provider': 'codex', 'models': [], 'source': None, 'at': None, 'stale': True,
                    'error': '尚未发现模型；请点击刷新模型目录', 'inference_verified': False,
                    'version': self.version, 'executable_sha256': self.image['sha256']}
        result = {k: v for k, v in row.items() if k not in ('account_sha256', 'environment_sha256')}
        result['stale'] = bool(row.get('error') or time.time() - row.get('at', 0) > 300 or row.get('executable_sha256') != self.image['sha256'])
        return result

    def discover(self, *, agent_defaults=False):
        previous = self.cached()
        defaults = {}
        try:
            options = {'include_defaults': True} if agent_defaults else {}
            with metadata_guard(self.store.state):
                image = NativeImage(self.image['path'], expected_sha256=self.image['sha256']) if agent_defaults else nullcontext()
                with image as pinned, CodexRPC(self.image['path'], self.home, self.config_root, str(self.store.state),
                        timeout=25, **({'executable_image': pinned} if pinned is not None else {})) as rpc:
                    rows = codex_models(lambda binding, cwd, method, params: rpc.request(method, params),
                                        self.base('metadata', 'low'), self.home, self.store.state, **options)
                    account = rpc.request('account/read', {'refreshToken': False})
                    config = rpc.request('config/read', {'cwd': str(self.store.state), 'includeLayers': False})['config'] if agent_defaults else {}
                    modes = rpc.request('collaborationMode/list', {}).get('data', []) if agent_defaults else []
            authenticated = bool(account.get('account')) or account.get('requiresOpenaiAuth') is False
            if not authenticated:
                raise Blocked('Codex 未报告可用登录状态，请在原生客户端完成登录；不会切换账户或发送费用探针')
            binding = self.base(rows[0]['id'], rows[0]['efforts'][0] if rows[0]['efforts'] else 'low')
            value = dict(id='codex', provider='codex', models=rows, source='Codex app-server model/list',
                         at=time.time(), error=None, authenticated=True, inference_verified=False,
                         account_sha256=digest(canonical(account)), environment_sha256=direct_environment_identity(binding),
                         version=self.version, executable_sha256=self.image['sha256'])
            if agent_defaults:
                catalog_defaults = {row['id']: (row.pop('catalog_default'), row.pop('default_effort')) for row in rows}
                defaults = {'model': config.get('model'), 'effort': config.get('model_reasoning_effort'),
                            'catalog': catalog_defaults, 'plan': any(
                                isinstance(mode, dict) and mode.get('mode') == 'plan' for mode in modes)}
        except (Blocked, Invalid, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            # Do not serialize account/config/exception payloads into browser errors.
            value = dict(previous, id='codex', error=public_provider_error(exc), attempted_at=time.time(), stale=True)
        self.store.db.put('provider-catalog', 'codex', value)
        return (self.cached(), defaults) if agent_defaults else self.cached()

    def binding(self, workspace, node, *, manual_model=False):
        row = self.store.db.get('provider-catalog', 'codex')
        if row.get('error') or time.time() - row.get('at', 0) > 300:
            raise Invalid('模型目录未发现、失败或已过期，请先刷新模型目录')
        model = next((m for m in row['models'] if m['id'] == node.get('model')), None)
        efforts = (model.get('efforts', []) if model else
                   {effort for candidate in row['models'] for effort in candidate['efforts']} if manual_model else ())
        if node.get('provider') != 'codex' or node.get('effort') not in efforts:
            raise Invalid('保存的 Provider、模型或推理强度不在当前发现目录中；原配置保留，请重新选择')
        if node['effort'] == 'ultra':
            raise Invalid('本期不开放自动委派推理强度，请选择当前目录中的其他强度')
        binding = self.base(node['model'], node['effort'])
        binding.update(project=workspace, timeout_seconds=node.get('timeout', 300),
                       account_sha256=row['account_sha256'], catalog_at=row['at'],
                       direct_environment_sha256=row['environment_sha256'])
        self.require_local(binding)
        return binding

    def require_local(self, binding):
        if resolve(binding['executable'])['sha256'] != binding['_executable_sha256']:
            raise Blocked('原生 CLI 映像已变化，未发送任务；请重新发现配置')
        if direct_environment_identity(binding) != binding['direct_environment_sha256']:
            raise Blocked('原生 Provider 配置已变化，未发送任务；请刷新目录并重新启动')

    def require_account(self, binding):
        self.require_local(binding)
        try:
            with metadata_guard(self.store.state):
                with CodexRPC(binding['executable'], self.home, self.config_root, str(self.store.state), timeout=20) as rpc:
                    account = rpc.request('account/read', {'refreshToken': False})
        except (Blocked, OSError, ValueError, KeyError) as exc:
            raise Blocked(public_provider_error(exc)) from None
        if digest(canonical(account)) != binding['account_sha256']:
            raise Blocked('原生账户绑定已变化，未发送任务；请重新发现并明确启动，不会切换账户')
