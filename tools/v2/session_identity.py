"""Private, immutable identities for an explicitly registered fixture index."""
from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
import unicodedata

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import safe_text
from studio.session_sources import UUID, Refused, clean_path, opened

from .project_files import mount_id


MESSAGES = {
    'PROFILE_REFUSED': '会话来源未批准或无法安全打开。',
    'ROOT_CHANGED': '会话来源身份已变化，请重新核验来源。',
    'LOCATOR_REFUSED': '会话归属未核实或存在冲突。',
    'AUTHORITY_REFUSED': '会话锁目录身份或权限未确认。',
    'LEASE_HELD': '此会话已被受管进程占用。',
    'EXTERNAL_BUSY': '已检测到其他会话写入者。',
    'EXTERNAL_UNKNOWN': '无法核实会话写入状态。',
    'OWNER_UNKNOWN': '无法核实当前持锁进程身份。',
    'RECOVERY_REQUIRED': '之前的会话操作待人工核验。',
    'INTENT_EXISTS': '此操作已登记，需核验原记录。',
}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}')
HANDLE = re.compile(r'v1\.[0-9a-f]{64}')
PROVIDERS = frozenset(('codex', 'claude', 'copilot'))


class SessionRefused(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES[code])


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def session_path(value, code='PROFILE_REFUSED'):
    try:
        if not isinstance(value, (str, Path)):
            raise Refused('invalid_path')
        return Path(clean_path(str(value)))
    except (Refused, ValueError):
        raise SessionRefused(code) from None


@dataclass(frozen=True, slots=True)
class StorageRootIdentity:
    mount_id: int
    device: int
    inode: int

    def __post_init__(self):
        if (type(self.mount_id) is not int or self.mount_id <= 0 or type(self.device) is not int
                or self.device < 0 or type(self.inode) is not int or self.inode <= 0):
            raise SessionRefused('PROFILE_REFUSED')

    def fields(self):
        return [self.mount_id, self.device, self.inode]

    @classmethod
    def from_fd(cls, fd):
        meta = os.fstat(fd)
        if not stat.S_ISDIR(meta.st_mode):
            raise SessionRefused('PROFILE_REFUSED')
        return cls(mount_id(fd), meta.st_dev, meta.st_ino)

    @classmethod
    def capture(cls, path):
        """Capture a fixture directory's identity for an operator's fixed registration."""
        with verified_directory(session_path(path)) as fd:
            return cls.from_fd(fd)


@contextmanager
def verified_directory(path, code='PROFILE_REFUSED'):
    try:
        with opened(path, directory=True) as fd:
            yield fd
    except (OSError, Refused, Blocked):
        raise SessionRefused(code) from None


@dataclass(frozen=True, slots=True)
class ReadProfile:
    id: str
    provider: str
    root: Path = field(repr=False)
    identity: StorageRootIdentity

    def __post_init__(self):
        if (not identifier(self.id) or not isinstance(self.provider, str) or self.provider not in PROVIDERS
                or not isinstance(self.identity, StorageRootIdentity)):
            raise SessionRefused('PROFILE_REFUSED')
        object.__setattr__(self, 'root', session_path(self.root))


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    anchor: Path = field(repr=False)
    anchor_identity: StorageRootIdentity
    profiles: tuple
    handle_seed: bytes = field(repr=False)
    _by_id: object = field(init=False, repr=False)
    fingerprint: str = field(init=False)

    def __post_init__(self):
        if (not isinstance(self.anchor_identity, StorageRootIdentity)
                or type(self.handle_seed) is not bytes or len(self.handle_seed) != 32):
            raise SessionRefused('PROFILE_REFUSED')
        object.__setattr__(self, 'anchor', session_path(self.anchor))
        try:
            profiles = tuple(self.profiles)
        except TypeError:
            raise SessionRefused('PROFILE_REFUSED') from None
        if (not profiles or any(type(item) is not ReadProfile for item in profiles)
                or len({item.id for item in profiles}) != len(profiles)):
            raise SessionRefused('PROFILE_REFUSED')
        profiles = tuple(sorted(profiles, key=lambda item: item.id))
        canonical_profiles = {}
        physical = {}
        by_id = {}
        for item in profiles:
            self._verify(item, 'PROFILE_REFUSED')
            inode_key = item.identity.device, item.identity.inode
            if physical.setdefault(inode_key, (item.root, item.identity)) != (item.root, item.identity):
                raise SessionRefused('PROFILE_REFUSED')
            by_id[item.id] = canonical_profiles.setdefault((item.provider, item.identity), item)
        object.__setattr__(self, 'profiles', profiles)
        object.__setattr__(self, '_by_id', MappingProxyType(by_id))
        object.__setattr__(self, 'fingerprint', digest(canonical([
            'v2-native-profile-registry-v1', str(self.anchor), self.anchor_identity.fields(),
            [[item.id, item.provider, str(item.root), item.identity.fields()] for item in profiles],
            digest(self.handle_seed),
        ])))

    def _verify(self, profile, code):
        if not profile.root.is_relative_to(self.anchor):
            raise SessionRefused(code)
        try:
            with verified_directory(self.anchor, code) as anchor_fd:
                if StorageRootIdentity.from_fd(anchor_fd) != self.anchor_identity:
                    raise SessionRefused(code)
                fd = os.dup(anchor_fd)
                try:
                    for part in profile.root.relative_to(self.anchor).parts:
                        next_fd = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        os.close(fd)
                        fd = next_fd
                        if mount_id(fd) != self.anchor_identity.mount_id:
                            raise SessionRefused(code)
                    if StorageRootIdentity.from_fd(fd) != profile.identity:
                        raise SessionRefused(code)
                finally:
                    os.close(fd)
            # Verify both registered names again in case a path was replaced during traversal.
            with verified_directory(profile.root, code) as fd, verified_directory(self.anchor, code) as anchor_fd:
                if (StorageRootIdentity.from_fd(fd) != profile.identity
                        or StorageRootIdentity.from_fd(anchor_fd) != self.anchor_identity):
                    raise SessionRefused(code)
        except (OSError, Refused, Blocked, SessionRefused):
            raise SessionRefused(code) from None

    def profile(self, profile_id):
        if not identifier(profile_id) or profile_id not in self._by_id:
            raise SessionRefused('PROFILE_REFUSED')
        return self._by_id[profile_id]

    def verify(self, profile_id):
        profile = self.profile(profile_id)
        self._verify(profile, 'ROOT_CHANGED')
        return profile

    def handle(self, locator):
        return 'v1.' + hmac.new(self.handle_seed, locator.canonical_bytes(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionLocator:
    provider: str
    profile_id: str
    root_identity: StorageRootIdentity
    original_cwd: str = field(repr=False)
    workspace_id: str
    native_uuid: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.provider, str) or self.provider not in PROVIDERS or not identifier(self.profile_id)
                or not isinstance(self.root_identity, StorageRootIdentity) or not identifier(self.workspace_id)
                or not isinstance(self.native_uuid, str) or not UUID.fullmatch(self.native_uuid)):
            raise SessionRefused('LOCATOR_REFUSED')
        object.__setattr__(self, 'original_cwd', str(session_path(self.original_cwd, 'LOCATOR_REFUSED')))

    def canonical_bytes(self):
        return canonical(['v2-session-locator-v1', self.provider, self.profile_id, self.root_identity.fields(),
                          self.original_cwd, self.workspace_id, self.native_uuid])

    @property
    def lease_key(self):
        return digest(canonical(['v2-native-lease-v1', self.root_identity.fields(), self.provider, self.native_uuid]))


@dataclass(frozen=True, slots=True)
class FixtureSession:
    profile_id: str
    original_cwd: str = field(repr=False)
    workspace_id: str
    native_uuid: str = field(repr=False)
    source_id: str = field(repr=False)
    source_label: str = field(default='', repr=False)
    verified: bool = True


def display_source(label, fallback):
    fallback = safe_text(fallback)
    if not isinstance(label, str) or len(label) > 160 or '/' in label or '\\' in label:
        return fallback
    clean = ''.join(char for char in label if unicodedata.category(char) not in ('Cc', 'Cf', 'Cs'))
    return safe_text(clean).strip() or fallback


class LocatorIndex:
    """In-memory fixture provenance. Historical workflow claims have no constructor here."""
    def __init__(self, registry, workspace_cwds, fixtures):
        if type(registry) is not ProfileRegistry:
            raise SessionRefused('PROFILE_REFUSED')
        if not isinstance(workspace_cwds, Mapping):
            raise SessionRefused('LOCATOR_REFUSED')
        self.registry = registry
        scopes = {}
        for workspace, cwds in workspace_cwds.items():
            if not identifier(workspace) or isinstance(cwds, (str, Path)):
                raise SessionRefused('LOCATOR_REFUSED')
            try:
                scopes[workspace] = frozenset(str(session_path(cwd, 'LOCATOR_REFUSED')) for cwd in cwds)
            except TypeError:
                raise SessionRefused('LOCATOR_REFUSED') from None
        rows = {workspace: {} for workspace in scopes}
        sources = defaultdict(set)
        members = defaultdict(set)
        unresolved = set()
        for fixture in fixtures:
            if (type(fixture) is not FixtureSession or not identifier(fixture.source_id)
                    or type(fixture.verified) is not bool):
                raise SessionRefused('LOCATOR_REFUSED')
            profile = registry.profile(fixture.profile_id)
            locator = SessionLocator(profile.provider, profile.id, profile.identity, fixture.original_cwd,
                                     fixture.workspace_id, fixture.native_uuid)
            key = locator.lease_key
            members[key].add((locator.original_cwd, fixture.source_id))
            if not fixture.verified:
                unresolved.add(key)
            if locator.original_cwd not in scopes.get(locator.workspace_id, ()):
                unresolved.add(key)
                continue
            handle = registry.handle(locator)
            rows[locator.workspace_id][handle] = locator
            sources[handle].add(display_source(fixture.source_label, fixture.profile_id))
        self._rows = MappingProxyType({key: MappingProxyType(value) for key, value in rows.items()})
        self._conflicts = frozenset(unresolved | {key for key, members in members.items() if len(members) != 1})
        self._sources = MappingProxyType({key: tuple(sorted(labels)) for key, labels in sources.items()})

    def handles(self, workspace_id):
        return tuple(sorted(self._workspace(workspace_id)))

    def _workspace(self, workspace_id):
        if not identifier(workspace_id) or workspace_id not in self._rows:
            raise SessionRefused('LOCATOR_REFUSED')
        return self._rows[workspace_id]

    def resolve(self, workspace_id, handle):
        if not isinstance(handle, str) or not HANDLE.fullmatch(handle):
            raise SessionRefused('LOCATOR_REFUSED')
        locator = self._workspace(workspace_id).get(handle)
        if locator is None or locator.lease_key in self._conflicts or self.registry.handle(locator) != handle:
            raise SessionRefused('LOCATOR_REFUSED')
        profile = self.registry.verify(locator.profile_id)
        if (profile.provider, profile.identity) != (locator.provider, locator.root_identity):
            raise SessionRefused('LOCATOR_REFUSED')
        return locator

    def source_labels(self, workspace_id, handle):
        self.resolve(workspace_id, handle)
        return self._sources[handle]
