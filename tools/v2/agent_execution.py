"""Bind a saved Agent revision to V2's native execution boundary."""
from pathlib import Path
import re
import uuid

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.native_codex_image import NativeImage
from studio.native_codex_rpc import CodexRPC, metadata_guard
from studio.native_local import native_busy

from .native_history_config import SourceSpec, startup_seed
from .native_sessions import prove_file, source_for
from .session_identity import SessionRefused, StorageRootIdentity

LEAF = {'codex': 'sessions', 'claude': 'projects'}


def freeze_agent(runtime, workspace, node):
    row = runtime.agents.get(workspace, node['agent_id'], node['agent_revision'])
    config = row['config']
    if config.get('execution') == 'native-cli':
        raise Invalid('此 Agent 使用原生 CLI 脚本合同。请从原生工作流画布启动或导出 Python。')
    provider = config['provider']
    if not config['round_prompt'].strip():
        raise Invalid('请在 Agent 配置填写每轮提示词。')
    permission = config['permission']
    if (config['subagents'] != 'disabled' or permission not in ('none', 'workspace-read', 'workspace-write')
            or config['max_budget_usd'] is not None and provider != 'claude'):
        raise Invalid('所选 Provider 的工具权限、子 Agent 或预算尚未开放。')
    approved = runtime.agents.session_operations
    home = runtime.agents.independent_home(provider,config.get('native_profile'))
    if row.get('native_home_pin') != (digest(canonical(home)) if home else None):
        raise Invalid('已保存 Agent 的独立账户绑定已变化，请核对 Profile 后重新保存并绑定。')
    if provider == 'claude' and not home and (approved is None or provider not in approved.profiles):
        raise Invalid('启动 Claude Agent 需要本机明确启用 Claude 原生写入。')
    cwd = runtime.agents.directory(workspace, config['cwd'])
    adapter = runtime.cli_providers.adapter(provider,config.get('native_profile')) if home else runtime.cli_providers.adapter(provider)
    binding = adapter.binding(workspace, config)
    if provider == 'claude' and not home:
        permitted = approved.profiles['claude'][1]
        if any(binding[key] != permitted[key] for key in ('home', 'config_root', '_executable_sha256', 'root_identity',
                                                         'cli_environment_sha256')):
            raise SessionRefused('ROOT_CHANGED')
    root = Path(binding['config_root'])
    binding.update(agent_config_id=row['id'], agent_config_revision=row['revision'], cwd=str(cwd),
                   cwd_identity=StorageRootIdentity.capture(cwd).fields(),
                   root_identity=StorageRootIdentity.capture(root).fields(),
                   runtime_profile='default-' + provider, local_lock_root=str(root / '.devflower-writer-locks'),
                   execution_mode=config['mode'])
    if config['session']['kind'] == 'existing':
        if home:
            listing = runtime.agents.sessions(workspace,provider,str(cwd),home['id'])
        elif approved is None:
            raise Invalid('绑定已有会话需要已启用的原生会话写入。')
        else:
            listing = approved.reader.listing(workspace)
        locator, record = runtime.agents.verify_session(workspace, config, listing['revision'])
        binding.update(session_id=locator.native_uuid, resume_proof=dict(
            relative=str(record.ref.relative), sha256=record.ref.sha, history_identity=locator.root_identity.fields()))
    elif provider == 'claude':
        binding['new_session_id'] = str(uuid.uuid4())
    if provider == 'codex' or binding.get('v2_claude_scope'):
        binding['v2_agent_skills'] = []
    if config['skills']:
        if provider=='claude' and (not binding.get('v2_claude_scope') or binding.get('v2_claude_read_only')):
            raise Invalid('Claude Skills 仅用于获准工作区读写的 Agent 模式。')
        catalog = runtime.agents.skills(workspace, provider, str(cwd), authority=True)
        indexed = {row['id']: row for row in catalog['skills']}
        if not set(config['skills']) <= indexed.keys():
            raise Invalid('选定 Skills 已变化，请重新发现并保存。')
        fields = ('id', 'name', 'path', 'sha256', 'native_name') if provider == 'claude' else ('id', 'name', 'path', 'sha256')
        binding['v2_agent_skills'] = [{key: indexed[ident][key] for key in fields}
                                     for ident in config['skills']]
    return row, binding


def history_source(store, binding):
    provider = binding['provider']
    root = Path(binding['config_root']) / LEAF[provider]
    profile = binding if binding.get('kind')=='isolated-home' else binding.get('native_home')
    name = 'agent-'+provider+'-'+profile['id'] if profile else 'default-'+provider
    spec = SourceSpec(name, provider, Path(binding['home']), root, '独立来源' if profile else '默认来源')
    return source_for(spec, startup_seed(store.state))


def require_agent_scope(runtime, binding):
    workspace, cwd = binding['project'], binding['cwd']
    runtime.agents.directory(workspace, cwd)
    if (StorageRootIdentity.capture(cwd).fields() != binding['cwd_identity']
            or StorageRootIdentity.capture(binding['config_root']).fields() != binding['root_identity']):
        raise SessionRefused('ROOT_CHANGED')


def require_agent_history(runtime, binding):
    proof = binding.get('resume_proof')
    if proof is None:
        return
    source = history_source(runtime.store, binding)
    if source.profile.identity.fields() != proof['history_identity']:
        raise SessionRefused('ROOT_CHANGED')
    _, ref, _ = prove_file(source, binding['project'], binding['cwd'], binding['session_id'], proof['relative'])
    if ref.sha != proof['sha256']:
        raise SessionRefused('MANAGED_HISTORY_CHANGED' if binding['provider'] == 'claude' else 'LOCATOR_REFUSED')
    if binding['provider'] == 'codex' and native_busy(binding, binding['session_id']):
        raise SessionRefused('EXTERNAL_BUSY')


def prove_agent_history(store, binding, sid):
    source = history_source(store, binding)
    if binding['provider'] == 'claude':
        folder = re.sub('[^a-zA-Z0-9]', '-', binding['cwd'])
        relative = Path(folder) / (sid + '.jsonl')
    else:
        with metadata_guard(store.state, timeout=20) as timeout:
            with NativeImage(binding['executable'], expected_sha256=binding['_executable_sha256']) as image:
                with CodexRPC(image.path, binding['home'], binding['config_root'], binding['cwd'],
                              timeout=timeout, executable_image=image, **({'file_auth_only':True} if binding.get('native_home') else {})) as rpc:
                    thread = rpc.request('thread/read', {'threadId': sid, 'includeTurns': False})['thread']
        if thread.get('id') != sid or thread.get('cwd') != binding['cwd'] or not isinstance(thread.get('path'), str):
            raise SessionRefused('LOCATOR_REFUSED')
        relative = Path(thread['path']).relative_to(source.profile.root)
    _, ref, _ = prove_file(source, binding['project'], binding['cwd'], sid, relative)
    return {'relative': str(ref.relative), 'sha256': ref.sha, 'history_identity': source.profile.identity.fields()}
