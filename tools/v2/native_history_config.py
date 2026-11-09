"""Startup-only opt-in configuration for the V2 native history reader."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import stat

from orchestration.common import Blocked
from studio.graph import Invalid

from .project_files import FileView, mount_id
from .session_identity import StorageRootIdentity, identifier, session_path, verified_directory

SEED_FILE = 'native-history.seed'
CONFIG_BYTES = 64 * 1024
PROVIDERS = ('codex', 'claude')


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    provider: str
    anchor: Path = field(repr=False)
    root: Path = field(repr=False)
    label: str


@dataclass(frozen=True, slots=True)
class HistoryConfig:
    discover_defaults: bool
    discover_nested: bool
    profiles: tuple


def private_bytes(view, directory, name, limit):
    with view.regular(name, directory) as fd:
        meta = os.fstat(fd)
        if (meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600
                or not 0 < meta.st_size <= limit):
            raise Invalid('会话读取配置或身份文件需要本机用户私有权限 0600。')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        if len(data) != meta.st_size:
            raise Invalid('会话读取配置或身份文件已变化。')
        return data


def load_history_config(value):
    try:
        path = session_path(value)
        with verified_directory(path.parent) as directory:
            identity = StorageRootIdentity.from_fd(directory)
            raw = private_bytes(FileView(directory, identity.mount_id), directory, path.name, CONFIG_BYTES)
        with verified_directory(path.parent) as current:
            if StorageRootIdentity.from_fd(current) != identity:
                raise ValueError()
        config = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
        if (not isinstance(config, dict) or set(config) - {'version', 'discover_defaults', 'discover_nested', 'profiles'}
                or type(config.get('version')) is not int or config['version'] != 1):
            raise ValueError()
        defaults = config.get('discover_defaults', False)
        nested = config.get('discover_nested', False)
        profiles = config.get('profiles', [])
        if type(defaults) is not bool or type(nested) is not bool or not isinstance(profiles, list) or len(profiles) > 12:
            raise ValueError()
        specs = []
        used = {'default-codex', 'default-claude'}
        for item in profiles:
            if (not isinstance(item, dict) or set(item) != {'id', 'provider', 'anchor', 'root'}
                    or not identifier(item['id']) or item['id'] in used or item['provider'] not in PROVIDERS):
                raise ValueError()
            anchor, root = session_path(item['anchor']), session_path(item['root'])
            if not root.is_relative_to(anchor):
                raise ValueError()
            used.add(item['id'])
            specs.append(SourceSpec(item['id'], item['provider'], anchor, root, '隔离来源 ' + item['id']))
        return HistoryConfig(defaults, nested, tuple(sorted(specs, key=lambda spec: spec.id)))
    except (ValueError, TypeError, OSError, Blocked, RecursionError):
        raise Invalid('原生会话只读配置无效，请核对路径、格式与私有权限。') from None


def unique_pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError()
    return result


def startup_seed(state):
    try:
        with verified_directory(state) as directory:
            meta = os.fstat(directory)
            if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o700:
                raise ValueError()
            view = FileView(directory, mount_id(directory))
            try:
                data = private_bytes(view, directory, SEED_FILE, 32)
            except FileNotFoundError:
                with os.fdopen(os.open('.', os.O_WRONLY | os.O_TMPFILE | os.O_NOFOLLOW, 0o600, dir_fd=directory),
                               'wb') as stream:
                    data = secrets.token_bytes(32)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    try:
                        os.link(f'/proc/self/fd/{stream.fileno()}', SEED_FILE, dst_dir_fd=directory, follow_symlinks=True)
                    except FileExistsError:
                        data = private_bytes(view, directory, SEED_FILE, 32)
                    os.fsync(directory)
            if len(data) != 32:
                raise ValueError()
            with verified_directory(state) as current:
                current_meta = os.fstat(current)
                if ((current_meta.st_dev, current_meta.st_ino, mount_id(current))
                        != (meta.st_dev, meta.st_ino, view.root_mount)):
                    raise ValueError()
            return data
    except (OSError, ValueError, Blocked):
        raise Invalid('无法安全加载原生会话读取身份，请核对私有状态目录。') from None
