"""New no-tool assistant turns over the native session operation journal."""
from pathlib import Path

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from .agent_execution import history_source
from .session_identity import SessionRefused, StorageRootIdentity
from .session_operations import SessionOperations, operation_id


class AssistantOperations(SessionOperations):
    authority = 'explicit-assistant-send'

    def __init__(self, store, registry):
        self.store, self.registry = store, registry
        self.initialize_journal(store)

    def config(self, workspace, selection):
        provider, name, model, effort = (selection[key] for key in ('provider', 'native_profile', 'model', 'effort'))
        adapter = self.registry.adapter(provider, name)
        profile = self.registry.selected(provider, name)
        isolated = profile and profile.get('kind') == 'isolated-home'
        row = self.registry.catalog_row(provider, adapter, name if isolated else None)
        if row['stale'] or not row['modes'].get('Ask', {}).get('enabled'):
            raise Invalid('请先刷新所选 CLI 的模型目录并核对当前版本的无工具调用能力。')
        discovered = {option['id']: option for option in row['models']}
        default = row['default_model'] or {}
        configured = profile if profile and not isolated else {}
        selected = model or configured.get('model')
        if selected and selected not in discovered and selected not in (configured.get('model'), default.get('id')):
            raise Invalid('所选模型未在当前 CLI 目录或配置中发现。请刷新并选择可用的模型。')
        if not selected and provider == 'codex':
            selected = default.get('id')
        choice = discovered.get(selected or default.get('id'), {})
        available_efforts = choice.get('efforts') or (
            row['efforts'] if provider == 'claude' else
            [value for value in (configured.get('effort') if selected == configured.get('model') else None,
                                 row['default_effort'] if selected == default.get('id') else None) if value])
        if effort is not None and effort not in available_efforts:
            raise Invalid('当前 CLI 未报告所选模型与推理强度组合。')
        agent = dict(mode='Ask', permission='none', native_profile=name, model=selected, effort=effort,
                     model_source='catalog' if selected in discovered else 'native_default', timeout=300, skills=[])
        binding = adapter.binding(workspace, agent)
        if provider == 'codex' and (binding.get('no_tools') is not True or binding.get('v2_agent_scope')):
            raise Invalid('助手需要无工具 Codex 绑定。')
        if provider == 'claude' and (binding.get('chat_only') is not True or binding.get('v2_claude_scope')):
            raise Invalid('助手需要无工具 Claude 绑定。')
        cwd = self.store.workspace(workspace)['path']
        identity = StorageRootIdentity.capture(cwd)
        if digest(canonical([cwd, identity.device, identity.inode])) != workspace:
            raise SessionRefused('LOCATOR_REFUSED')
        root = binding['config_root']
        binding.update(project=workspace, cwd=cwd, cwd_identity=identity.fields(), session_id=None,
                       root_identity=StorageRootIdentity.capture(root).fields(),
                       local_lock_root=str(Path(root) / '.devflower-writer-locks'),
                       runtime_profile='default-' + provider, conversation_format='text')
        if provider == 'codex':
            binding.update(v2_agent_skills=[], v2_development_untrusted=True)
        shown = dict(selection, requested_model=binding['model'], requested_effort=binding['effort'],
                     discovered_default=default.get('id'), source=(
                         'named_profile' if configured.get('model') and model is None else
                         (default.get('source') or 'native_default') if model is None else
                         choice.get('source') or default.get('source') or 'named_profile'),
                     cli_version=row['version'], permission='none')
        # A catalog refresh alone must not invalidate a preview of an identical binding.
        frozen = {key: value for key, value in binding.items() if key != 'catalog_at'}
        shown['binding_sha256'] = digest(canonical(frozen))
        return binding, shown

    def send_bound(self, binding, intent, prompt, request_sha):
        workspace = binding['project']
        prior = self._get(operation_id(workspace, intent))
        if prior:
            if prior.get('authorization') != self.authority or prior['request_sha256'] != request_sha:
                raise Conflict('同一发送意图对应不同请求。')
            return self._public(prior)
        return self.reserve(workspace, intent, prompt, binding, None, None, None, None, request_sha)

    def remember_claude_secrets(self, binding):
        # The native adapter already redacts reports using the frozen HOME.
        # Assistant turns do not contribute credentials to Session Center.
        pass

    def require_binding(self, binding):
        self.require_scope(binding)
        adapter = self.registry.adapter_for(binding)
        if binding['provider'] == 'codex':
            adapter.provider.require_account(binding)
        adapter.require_local(binding)

    def transport(self, binding, limits, *, managed_resume):
        return self.registry.adapter_for(binding).transport(binding, limits)

    def history_source(self, binding):
        return history_source(self.store, binding)
