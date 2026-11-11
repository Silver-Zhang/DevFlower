"""Startup-only admission of independent, existing native Agent homes."""
import json
import os
from pathlib import Path
import stat
import tomllib

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid

from .claude_session_profile import CLI_ENV, legacy_endpoint_valid
from .native_agent_profiles import PROFILE_ID
from .native_history_config import unique_pairs
from .project_files import DIR_FLAGS, FileView, mount_id, protected_roots
from .session_identity import StorageRootIdentity, session_path, verified_directory

STORE = {'codex': ('.codex', 'sessions'), 'claude': ('.claude', 'projects')}
FILES = {'codex': ('config.toml', 'auth.json'), 'claude': ('settings.json', '.credentials.json')}


def overlaps(one, other):
    return one.is_relative_to(other) or other.is_relative_to(one)


def inode(path):
    try:
        identity = StorageRootIdentity.capture(path)
        return identity.device, identity.inode
    except (OSError, ValueError, Blocked):
        return None


def directory_alias(home, protected):
    own = inode(home)
    physical_parents = {inode(path) for path in (home, *home.parents)} - {None}
    if own is None or own == inode(Path.home()):
        return True
    for path in protected:
        if inode(path) in physical_parents or own in {inode(parent) for parent in path.parents}:
            return True
    return False


def private_directory(path):
    with verified_directory(path) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Blocked('独立原生 HOME 和配置目录需要当前用户的私有权限 0700。')
        return StorageRootIdentity.from_fd(fd)


def safe_home_file(view, directory, name):
    try:
        with view.regular(name, directory) as fd:
            meta = os.fstat(fd)
            if (meta.st_uid != os.geteuid() or meta.st_mode & 0o022 or
                    not 0 < meta.st_size <= 512000):
                raise ValueError()
            raw = os.pread(fd, meta.st_size + 1, 0)
            if len(raw) != meta.st_size:
                raise ValueError()
            return raw
    except FileNotFoundError:
        return None


def verify_storage(home, root, provider, root_id):
    # Native clients open their database and history files by name. Check these
    # before the native process so a symlink cannot redirect an approved HOME.
    recursive = {'sessions','thread-writer-locks'} if provider=='codex' else {'projects','sessions','backups'}
    remaining = 12000

    def entry(directory, name, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 20:
            raise ValueError()
        try:
            fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            if name.endswith(('-wal','-shm')) or name=='.claude.json':
                return
            raise
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or mount_id(fd) != root_id.mount_id:
                raise ValueError()
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                return
            if not stat.S_ISDIR(info.st_mode) or name=='.claude.json':
                raise ValueError()
            if depth or name in recursive:
                child = os.open(name,DIR_FLAGS,dir_fd=directory)
                try:
                    if not os.path.samestat(info,os.fstat(child)) or mount_id(child)!=root_id.mount_id:
                        raise ValueError()
                    with os.scandir(child) as items:
                        for item in items:
                            entry(child,item.name,depth+1)
                finally:
                    os.close(child)
        finally:
            os.close(fd)

    with verified_directory(root) as directory, os.scandir(directory) as items:
        if StorageRootIdentity.from_fd(directory)!=root_id:
            raise ValueError()
        for item in items:
            entry(directory,item.name,0)
    if provider=='claude':
        with verified_directory(home) as directory:
            entry(directory,'.claude.json',0)


def observe(provider, name, home):
    config_dir, history = STORE[provider]
    managed = Path('/etc/codex' if provider=='codex' else '/etc/claude-code')
    if os.path.lexists(managed):
        raise Blocked('本机托管 CLI 配置尚未核验独立账户隔离。')
    root = home / config_dir
    home_id, root_id = private_directory(home), private_directory(root)
    if home_id.mount_id != root_id.mount_id:
        raise Blocked('独立原生配置根不能跨挂载。')
    history_path = root / history
    if os.path.lexists(history_path):
        with verified_directory(history_path) as fd:
            if StorageRootIdentity.from_fd(fd).mount_id != root_id.mount_id or os.fstat(fd).st_uid != os.geteuid():
                raise Blocked('独立原生历史根不属于登记的 HOME。')
    try:
        verify_storage(home,root,provider,root_id)
        with verified_directory(root) as fd:
            view = FileView(fd, root_id.mount_id)
            if StorageRootIdentity.from_fd(fd) != root_id:
                raise ValueError()
            config, auth = [safe_home_file(view, fd, item) for item in FILES[provider]]
        if provider == 'codex':
            value = tomllib.loads(config.decode()) if config else {}
            if (os.path.lexists(root/'.env') or value.get('cli_auth_credentials_store') not in (None,'file')
                    or value.get('sqlite_home') not in (None, str(root))):
                raise ValueError()
        else:
            value = json.loads(config, object_pairs_hook=unique_pairs) if config else {}
            env = value.get('env', {})
            if (not isinstance(value, dict) or set(value)-{'model','effortLevel','env'}
                    or not isinstance(env, dict) or set(env)-set(CLI_ENV) or 'NODE_EXTRA_CA_CERTS' in env
                    or any(not isinstance(item, str) or '\0' in item for item in env.values())
                    or 'ANTHROPIC_BASE_URL' in env and not legacy_endpoint_valid(env['ANTHROPIC_BASE_URL'])
                    or not auth and not any(env.get(key) for key in (
                        'ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_OAUTH_TOKEN'))):
                raise ValueError()
        if auth is not None and not isinstance(json.loads(auth, object_pairs_hook=unique_pairs), dict):
            raise ValueError()
        if private_directory(home) != home_id or private_directory(root) != root_id:
            raise ValueError()
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, Blocked):
        raise Blocked('独立原生 HOME 的配置、账户文件或路径未通过核验。') from None
    return dict(id=name, provider=provider, kind='isolated-home', home=str(home), config_root=str(root),
                home_identity=home_id.fields(), root_identity=root_id.fields(),
                sha256=digest(canonical([digest(raw) if raw else None for raw in (config,auth)])))


class NativeAgentHomes:
    def __init__(self, store, options, named):
        self.entries = {}
        if len(options) > 12:
            raise Invalid('最多登记 12 个独立原生 HOME。')
        protected = [*protected_roots(store), *store.roots]
        for option in options:
            provider, _, rest = option.partition(':')
            name, separator, path = rest.partition('=')
            key = provider,name
            if (provider not in STORE or not PROFILE_ID.fullmatch(name) or name == 'default' or not separator
                    or key in named or key in self.entries):
                raise Invalid('独立原生 HOME 需要唯一的 Provider 和 Profile 名称。')
            home = session_path(path)
            if any(overlaps(home, other) for other in protected) or directory_alias(home,protected):
                raise Invalid('独立原生 HOME 必须与工作区、产品状态及现有账户根分离。')
            entry = observe(provider, name, home)
            if any(old['home_identity'][1:] == entry['home_identity'][1:] or
                   old['root_identity'][1:] == entry['root_identity'][1:] for old in self.entries.values()):
                raise Invalid('独立原生 HOME 不能登记同一实际目录的别名。')
            self.entries[key] = entry
            protected.append(home)

    def get(self, provider, name):
        return self.entries.get((provider,name))

    def require(self, provider, name):
        saved = self.get(provider,name)
        if saved is None or observe(provider,name,Path(saved['home'])) != saved:
            raise Blocked('独立原生 HOME 的配置或账户已变化，请核对后重新登记。')
        return saved

    def require_binding(self, binding):
        profile = binding.get('native_home')
        if profile:
            saved = self.require(binding['provider'],profile['id'])
            if (profile != saved or binding['home'] != saved['home']
                    or binding['config_root'] != saved['config_root']
                    or binding.get('root_identity', saved['root_identity']) != saved['root_identity']):
                raise Blocked('冻结 Agent 的独立原生 HOME 无法核验。')

    def worker_arguments(self, bindings):
        seen = set()
        args = []
        for binding in bindings:
            profile = binding.get('native_home')
            if not profile:
                continue
            self.require_binding(binding)
            key = binding['provider'], profile['id']
            if key not in seen:
                seen.add(key)
                args += ['--native-agent-home',key[0]+':'+key[1]+'='+profile['home']]
        return args
