"""Operator-only Copilot metadata identity and sealed input snapshots."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from orchestration.common import Blocked
from studio.session_sources import opened

from .native_history_config import unique_pairs
from .project_files import mount_id


PROFILE_ID = re.compile(r'[0-9a-f]{16,48}')
SHA = re.compile(r'[0-9a-f]{64}')
LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
FIELDS = {
    'version', 'profile_id', 'github_host', 'github_login',
    'github_hosts_file', 'github_hosts_sha256', 'gh_executable', 'gh_sha256',
    'copilot_executable', 'copilot_sha256',
}
LIMITS = {'config': 8192, 'hosts': 64 * 1024, 'image': 512 * 1024 * 1024}
ROOT = Path(__file__).resolve().parents[2]
MOUNT_ESCAPE = re.compile(rb'\\([0-7]{3})')
MAX_MOUNT_INFO = 4 * 1024 * 1024


class IdentityChanged(ValueError):
    pass


def _absolute(value):
    if (not isinstance(value, str) or not value.startswith('/') or
            any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise IdentityChanged()
    path = Path(value)
    if str(path) != value or value.startswith('//') or '..' in path.parts or not path.name:
        raise IdentityChanged()
    return path


def _stamp(info, mount):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, mount)


def _check_type(info, kind):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= LIMITS[kind]:
        raise IdentityChanged()
    mode = stat.S_IMODE(info.st_mode)
    if kind == 'image':
        if info.st_uid not in (0, os.geteuid()) or mode & 0o7022 or not mode & 0o111:
            raise IdentityChanged()
    elif info.st_uid != os.geteuid() or mode != 0o600:
        raise IdentityChanged()


@contextmanager
def _pin(path, kind):
    with opened(path.parent, directory=True) as directory:
        fd = os.open(path.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(fd)
            _check_type(info, kind)
            mount = mount_id(fd)
            if kind == 'hosts' and mount != mount_id(directory):
                raise IdentityChanged()
            yield fd, _stamp(info, mount)
        finally:
            os.close(fd)


@dataclass(frozen=True, slots=True)
class RegisteredFile:
    path: Path = field(repr=False)
    kind: str
    identity: tuple = field(repr=False)
    digest: str | None = field(repr=False, default=None)

    @classmethod
    def capture(cls, path, kind, digest=None):
        with _pin(path, kind) as (_, identity):
            return cls(path, kind, identity, digest)

    def verify(self):
        with _pin(self.path, self.kind) as (_, identity):
            if identity != self.identity:
                raise IdentityChanged()

    @contextmanager
    def reader(self):
        with _pin(self.path, self.kind) as (pin, identity):
            if identity != self.identity:
                raise IdentityChanged()
            fd = os.open(f'/proc/self/fd/{pin}', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                if _stamp(os.fstat(fd), mount_id(fd)) != self.identity:
                    raise IdentityChanged()
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    yield stream
                if _stamp(os.fstat(fd), mount_id(fd)) != self.identity:
                    raise IdentityChanged()
                self.verify()
            finally:
                os.close(fd)

    @contextmanager
    def sealed(self, deadline):
        fd = os.memfd_create('devflower-catalog-input', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            checksum = hashlib.sha256()
            expected = self.identity[5]
            with self.reader() as source, os.fdopen(os.dup(fd), 'wb') as dest:
                remaining = expected
                prefix = b''
                while remaining:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise IdentityChanged()
                    if not prefix:
                        prefix = chunk[:20]
                    checksum.update(chunk)
                    dest.write(chunk)
                    remaining -= len(chunk)
                if source.read(1) or checksum.hexdigest() != self.digest:
                    raise IdentityChanged()
            if self.kind == 'image' and not _elf(prefix):
                raise IdentityChanged()
            os.lseek(fd, 0, os.SEEK_SET)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW |
                        fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            yield fd
        finally:
            os.close(fd)


def _elf(prefix):
    if (len(prefix) < 20 or not prefix.startswith(b'\x7fELF') or
            prefix[4] not in (1, 2) or prefix[5] not in (1, 2) or prefix[6] != 1):
        return False
    order = 'little' if prefix[5] == 1 else 'big'
    return int.from_bytes(prefix[16:18], order) in (2, 3)


@dataclass(frozen=True, slots=True)
class CopilotProfile:
    profile_id: str
    login: str = field(repr=False)
    registration: RegisteredFile = field(repr=False)
    hosts: RegisteredFile = field(repr=False)
    gh: RegisteredFile = field(repr=False)
    copilot: RegisteredFile = field(repr=False)
    denied_roots: tuple = field(repr=False)
    denied_credentials: tuple = field(repr=False)

    def verify(self):
        for source in (self.registration, self.hosts, self.gh, self.copilot):
            source.verify()
        _verify_hosts_scope(self.hosts, self.denied_roots, self.denied_credentials)


def _protected_paths(store, native_sessions):
    home = Path.home()
    github = [home / '.config/gh', home / '.gh']
    xdg = os.environ.get('XDG_CONFIG_HOME')
    if xdg and Path(xdg).is_absolute():
        github.append(Path(xdg) / 'gh')
    gh = os.environ.get('GH_CONFIG_DIR')
    if gh and Path(gh).is_absolute():
        github.append(Path(gh))
    forbidden = [ROOT, store.state, *store.roots, home / '.codex',
                 home / '.claude', home / '.claude.json', home / '.copilot', *github]
    for name in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'COPILOT_HOME'):
        root = os.environ.get(name)
        if root and Path(root).is_absolute():
            forbidden.append(Path(root))
    if native_sessions is not None:
        forbidden.extend(spec.root for spec in native_sessions.config.profiles)
    return (tuple(Path(os.path.normpath(root)) for root in forbidden),
            tuple(root / 'hosts.yml' for root in github))


def _mount_path(raw):
    return Path(os.fsdecode(MOUNT_ESCAPE.sub(lambda match: bytes((int(match[1], 8),)), raw)))


def _mounts():
    with open('/proc/self/mountinfo', 'rb') as stream:
        raw = stream.read(MAX_MOUNT_INFO + 1)
    if len(raw) > MAX_MOUNT_INFO:
        raise IdentityChanged()
    mounts = {}
    for line in raw.splitlines():
        fields = line.split(b' ', 6)
        if len(fields) != 7:
            raise IdentityChanged()
        mounts[int(fields[0])] = (_mount_path(fields[3]), _mount_path(fields[4]))
    return mounts


def _coordinate(fd, mounts):
    mount = mounts.get(mount_id(fd))
    resolved = Path(os.readlink(f'/proc/self/fd/{fd}'))
    if mount is None or not resolved.is_relative_to(mount[1]):
        raise IdentityChanged()
    device = os.fstat(fd).st_dev
    return device, mount[0] / resolved.relative_to(mount[1])


def _protected_coordinate(path, mounts):
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except (FileNotFoundError, NotADirectoryError):
        return None
    try:
        info = os.fstat(fd)
        return (info.st_dev, info.st_ino), _coordinate(fd, mounts)
    finally:
        os.close(fd)


def _verify_hosts_scope(hosts, roots, credential_paths):
    # Compare filesystem coordinates behind mount aliases, including bind
    # mounts of a nested directory. No protected file contents are opened.
    mounts = _mounts()
    with _pin(hosts.path, 'hosts') as (fd, stamp):
        if stamp != hosts.identity:
            raise IdentityChanged()
        device, actual = _coordinate(fd, mounts)
    for path in credential_paths:
        protected = _protected_coordinate(path, mounts)
        if protected is not None and protected[0] == hosts.identity[:2]:
            raise IdentityChanged()
    for path in roots:
        protected = _protected_coordinate(path, mounts)
        if protected is None:
            continue
        root_device, root = protected[1]
        if device == root_device and actual.is_relative_to(root):
            raise IdentityChanged()


def load_profile(path, store, native_sessions=None):
    try:
        registration = RegisteredFile.capture(_absolute(path), 'config')
        with registration.reader() as stream:
            raw = stream.read(LIMITS['config'] + 1)
        if len(raw) != registration.identity[5]:
            raise IdentityChanged()
        values = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
        if (not isinstance(values, dict) or set(values) != FIELDS or
                type(values['version']) is not int or values['version'] != 1 or
                values['github_host'] != 'github.com' or
                not isinstance(values['profile_id'], str) or not PROFILE_ID.fullmatch(values['profile_id']) or
                not isinstance(values['github_login'], str) or not LOGIN.fullmatch(values['github_login'])):
            raise IdentityChanged()
        paths = (_absolute(values['github_hosts_file']), _absolute(values['gh_executable']),
                 _absolute(values['copilot_executable']))
        digests = (values['github_hosts_sha256'], values['gh_sha256'], values['copilot_sha256'])
        denied_roots, denied_credentials = _protected_paths(store, native_sessions)
        if (paths[0].name != 'hosts.yml' or any(paths[0].is_relative_to(root) for root in denied_roots) or
                any(not isinstance(digest, str) or not SHA.fullmatch(digest) for digest in digests)):
            raise IdentityChanged()
        files = [RegisteredFile.capture(path, kind, digest) for path, kind, digest in
                 zip(paths, ('hosts', 'image', 'image'), digests, strict=True)]
        profile = CopilotProfile(values['profile_id'], values['github_login'], registration, *files,
                                 denied_roots, denied_credentials)
        profile.verify()
        return profile
    except (OSError, ValueError, TypeError, Blocked, RecursionError):
        raise IdentityChanged() from None
