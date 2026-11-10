"""Workspace-scoped Agent configuration. Saving an Agent never launches a CLI."""
import json
from itertools import islice
from pathlib import Path
import re
import secrets

from orchestration.common import Blocked, canonical, digest
from orchestration.privacy import credentials, redact
from studio.agent_extensions import skill_files, skill_header
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.model_registration import profile_identity
from studio.native_codex_image import NativeImage
from studio.native_codex_rpc import CodexRPC, metadata_guard
from studio.server import Conflict
from studio.session_sources import opened

from .cli_providers import EFFORT, model_id
from .native_sessions import HANDLE, REVISION
from .session_identity import StorageRootIdentity

KIND = 'agent-config'
KEY = re.compile(r'[0-9a-f]{32}')
HASH = re.compile(r'[0-9a-f]{64}')
PROVIDERS = ('codex', 'claude')
ROLES = ('planner', 'developer', 'reviewer', 'custom')
PERMISSIONS = ('none', 'workspace-read', 'workspace-write', 'controlled-command', 'native', 'manual')
TEXT = {'name': 160, 'role': 160, 'responsibilities': 4000, 'background': 4000, 'goal': 4000,
        'initial_prompt': 20000, 'round_prompt': 20000, 'output_prompt': 20000}
FIELDS = {*TEXT, 'template', 'provider', 'model', 'model_source', 'effort', 'mode', 'permission',
          'cwd', 'session', 'skills', 'subagents', 'timeout', 'max_budget_usd'}


def matches(regex, value):
    return isinstance(value, str) and regex.fullmatch(value)


def validate_agent(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise Invalid('Agent 配置字段缺失或不受支持。')
    for key, limit in TEXT.items():
        if not isinstance(value[key], str) or len(value[key]) > limit or '\0' in value[key]:
            raise Invalid('Agent 文本类型或长度无效。')
    if not value['name'].strip() or not value['role'].strip():
        raise Invalid('请填写 Agent 名称和角色。')
    if (value['template'] not in ROLES or value['provider'] not in PROVIDERS
            or value['mode'] not in ('Ask', 'Plan', 'Agent') or value['permission'] not in PERMISSIONS
            or value['subagents'] not in ('disabled', 'native')):
        raise Invalid('Agent 角色模板、Provider、工作模式或工具权限无效。')
    source, model = value['model_source'], value['model']
    if source not in ('native_default', 'catalog', 'configured', 'manual') or (
            model is not None if source == 'native_default' else not model_id(model)):
        raise Invalid('Agent 模型选择无效。')
    if value['effort'] is not None and not matches(EFFORT, value['effort']):
        raise Invalid('推理强度无效。')
    if not isinstance(value['cwd'], str) or len(value['cwd']) > 4096 or '\0' in value['cwd']:
        raise Invalid('工作目录无效。')
    session = value['session']
    if not isinstance(session, dict) or (session != {'kind': 'new'} and (
            set(session) != {'kind', 'handle'} or session['kind'] != 'existing' or not matches(HANDLE, session['handle']))):
        raise Invalid('请选择新建会话或一个已核验的已有会话。')
    skills = value['skills']
    if (not isinstance(skills, list) or len(skills) > 8 or any(not matches(HASH, item) for item in skills)
            or len(set(skills)) != len(skills)):
        raise Invalid('最多选择八个已核验的 Skill。')
    if type(value['timeout']) is not int or not 10 <= value['timeout'] <= 3600:
        raise Invalid('超时需为 10–3600 秒。')
    budget = value['max_budget_usd']
    if budget is not None and (not isinstance(budget, str) or not re.fullmatch(r'[0-9]{1,4}(?:\.[0-9]{1,4})?', budget)
                               or float(budget) <= 0):
        raise Invalid('预算需为有效的正数美元金额。')
    try:
        if len(canonical(value)) > 180_000:
            raise Invalid('Agent 配置超过保存上限。')
    except UnicodeError:
        raise Invalid('Agent 配置包含无效文本。') from None
    return value


class AgentRecords:
    def __init__(self, store, providers=None, session_operations=None):
        self.store, self.providers, self.session_operations = store, providers, session_operations

    def directory(self, workspace, value):
        root = Path(self.store.workspace(workspace)['path'])
        cwd, _ = self.store.directory(value)
        if not cwd.is_relative_to(root) or str(cwd) != value:
            raise Invalid('Agent 工作目录须为当前工作空间内的实际目录。')
        return cwd

    def list(self, workspace):
        self.store.workspace(workspace)
        return {'agents': [row for row in self.store.db.list(KIND) if row['workspace'] == workspace]}

    def get(self, workspace, key, revision=None):
        self.store.workspace(workspace)
        if not matches(KEY, key):
            raise Invalid('Agent 身份无效。')
        row = self.store.db.get(KIND, key)
        if row['workspace'] != workspace or revision is not None and row['revision'] != revision:
            raise Conflict('Agent 工作空间或版本已变化，请重新选择。')
        return row

    def reference_issues(self, workspace, nodes):
        issues = []
        for node in nodes:
            if 'agent_id' in node:
                try:
                    self.get(workspace, node['agent_id'], node['agent_revision'])
                except (Invalid, Conflict):
                    issues.append(dict(node_id=node['id'], code='AGENT_REFERENCE_UNRESOLVED',
                        message=node['name'] + '：此 Agent 配置版本在当前工作空间不可用，请重新选择。'))
        return issues

    def verify_session(self, workspace, config, revision):
        operations = self.session_operations
        if operations is None or not matches(REVISION, revision):
            raise Invalid('请从当前工作空间的原生会话列表选择已有会话。')
        locator, record = operations.resolve(workspace, config['session']['handle'], revision)
        self.require_session_scope(workspace, config, locator, record)
        return locator, record

    def require_session_scope(self, workspace, config, locator, record):
        operations = self.session_operations
        provider = config['provider']
        profile = operations.profiles.get(provider)
        if (not profile or locator.provider != provider or locator.original_cwd != config['cwd']
                or record.source.profile != profile[0].profile):
            raise Conflict('已有会话的 Provider、配置根或原工作目录与 Agent 不一致。')
        if provider == 'claude':
            binding = dict(profile[1], project=workspace, cwd=config['cwd'],
                           cwd_identity=StorageRootIdentity.capture(config['cwd']).fields(),
                           direct_environment_sha256=profile_identity(profile[1]))
            operations.require_claude_history(binding, locator, record)

    def sessions(self, workspace, provider, cwd):
        self.directory(workspace, cwd)
        operations = self.session_operations
        if operations is None or provider not in operations.profiles:
            raise Invalid('此 Provider 尚未启用原生会话绑定。')
        reader = operations.reader
        listing = reader.listing(workspace)
        with reader.guard:
            snapshot = reader._snapshot(workspace, listing['revision'])
        rows = []
        for item in listing['sessions']:
            handle = item['handle']
            record = snapshot.reads.get(handle)
            if record is None or item['provider'] != provider:
                continue
            try:
                locator = record.index.resolve(workspace, handle)
                self.require_session_scope(workspace, {'provider': provider, 'cwd': cwd}, locator, record)
            except (ValueError, Conflict, Blocked, OSError):
                continue
            rows.append({key: item[key] for key in ('handle', 'provider', 'updated_at')})
        return {'sessions': rows, 'revision': listing['revision']}

    def save(self, workspace, key, expected, value, session_revision=None):
        config = validate_agent(value)
        self.directory(workspace, config['cwd'])
        if (key is None and expected is not None or key is not None and not matches(KEY, key)
                or expected is not None and not matches(HASH, expected)):
            raise Invalid('保存 Agent 需要当前配置版本。')
        if key is not None and expected is None:
            try:
                prior = self.get(workspace, key)
            except Invalid:
                pass
            else:
                if prior['revision'] == digest(canonical([key, None, config])):
                    return prior
                raise Conflict('此 Agent 保存意图已经使用。')
        if config['session']['kind'] == 'existing':
            if session_revision is not None or expected is None:
                self.verify_session(workspace, config, session_revision)
            else:
                previous = self.get(workspace, key, expected)['config']
                if any(previous[field] != config[field] for field in ('provider', 'cwd', 'session')):
                    raise Invalid('已有会话绑定已变化，请重新选择并核验。')
        elif session_revision is not None:
            raise Invalid('新会话不需要已有会话版本。')
        if config['skills']:
            available = self.skills(workspace, config['provider'], config['cwd'])
            if not available['selectable'] or not set(config['skills']) <= {row['id'] for row in available['skills']}:
                raise Conflict('Skill 来源或文件已变化，请重新发现并选择。')
        key = key or secrets.token_hex(16)
        revision = digest(canonical([key, expected, config]))
        result = dict(id=key, workspace=workspace, revision=revision, config=config)
        with self.store.db.tx() as db:
            previous = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone()
            if previous:
                prior = json.loads(previous[0])
                if expected is None and prior['workspace'] == workspace and prior['revision'] == revision:
                    return prior
                if prior['workspace'] != workspace or prior['revision'] != expected:
                    raise Conflict('Agent 已在另一处更改，请重新读取。')
            elif expected is not None:
                raise Conflict('Agent 已删除或不存在。')
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (KIND, key, canonical(result).decode()))
        return result

    def remove(self, workspace, key, expected):
        self.store.workspace(workspace)
        if not matches(KEY, key) or not matches(HASH, expected):
            raise Invalid('删除 Agent 需要当前配置版本。')
        with self.store.db.tx() as db:
            raw = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone()
            prior = json.loads(raw[0]) if raw else {}
            if prior.get('workspace') != workspace or prior.get('revision') != expected:
                raise Conflict('Agent 已在另一处更改，请重新读取。')
            db.execute('DELETE FROM records WHERE kind=? AND id=?', (KIND, key))
        return {'removed': key}

    def skills(self, workspace, provider, cwd, *, authority=False):
        path = self.directory(workspace, cwd)
        if provider not in PROVIDERS:
            raise Invalid('Skill Provider 不受支持。')
        home = Path.home()
        candidates = []
        roots = [(path / '.claude/skills', 'workspace'), (home / '.claude/skills', 'user')]
        if provider == 'codex':
            native = self.providers.adapter('codex').provider if self.providers else None
            if not native:
                return {'skills': [], 'selectable': False, 'source': 'cli_unavailable'}
            home = Path(native.home)
            roots = [(path / '.agents/skills', 'workspace'), (home / '.agents/skills', 'user'),
                     (Path(native.config_root) / 'skills', 'user')]
            with metadata_guard(self.store.state) as timeout:
                with NativeImage(native.image['path'], expected_sha256=native.image['sha256']) as image:
                    with CodexRPC(image.path, native.home, native.config_root, str(self.store.state),
                                  timeout=min(20, timeout), executable_image=image) as rpc:
                        result = rpc.request('skills/list', {'cwds': [cwd], 'forceReload': True})
            for entry in result.get('data', []):
                if isinstance(entry, dict) and entry.get('cwd') == cwd:
                    candidates.extend(Path(skill['path']) for skill in entry.get('skills', [])
                                      if isinstance(skill, dict) and skill.get('enabled') is True
                                      and not skill.get('pluginId') and isinstance(skill.get('path'), str))
        else:
            for root, _ in roots:
                try:
                    with opened(root, directory=True):
                        candidates.extend(folder / 'SKILL.md' for folder in sorted(islice(root.iterdir(), 100)))
                except (OSError, Blocked):
                    continue
        rows, seen = [], set()
        own_skills = Path(__file__).resolve().parents[2] / '.agents/skills'
        known = credentials(home)
        for file in candidates[:240]:
            folder = file.parent
            match = next(((root, scope) for root, scope in roots if folder.parent == root), None)
            if file.name != 'SKILL.md' or match is None or match[0] == own_skills or file in seen:
                continue
            seen.add(file)
            try:
                files = skill_files(folder)
                sha = digest(canonical({name: digest(data) for name, data in files.items()}))
                metadata = skill_header(files['SKILL.md'].decode())
                name, description = metadata['name'], safe_text(redact(metadata['description'], known))
                key = digest(canonical([workspace, provider, str(path), str(file), sha]))
            except (OSError, Blocked, ValueError, Invalid, UnicodeError):
                continue
            row = dict(id=key, name=name, description=description, scope=match[1])
            if provider == 'claude':
                if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', folder.name):
                    continue
                row['native_name'] = 'df-' + match[1] + ':' + folder.name
            if authority:
                row.update(path=str(file), sha256=sha)
            rows.append(row)
        return {'skills': rows, 'selectable': True,
                'source': 'native_cli' if provider == 'codex' else 'native_directories'}
