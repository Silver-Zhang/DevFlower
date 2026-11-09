"""Scoped, bounded read-only discovery and transcript projection."""
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import BoundedSemaphore, Lock
import time
import unicodedata

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import _visible, safe_text
from studio.server import Conflict
from studio.session_sources import read_session

from .native_history_config import PROVIDERS, SourceSpec, load_history_config, startup_seed
from .project_files import FileView
from .session_identity import (HANDLE, FixtureSession, LocatorIndex, ProfileRegistry, ReadProfile, SessionLocator,
                               SessionRefused, StorageRootIdentity, display_source, session_path, verified_directory)
from .workspace_select import WorkspaceDatabaseConflict, capture_workspace_proof, require_current

ERRORS = {
    'NATIVE_QUERY_INVALID': (400, '原生会话查询参数无效。'),
    'NATIVE_HISTORY_DISABLED': (404, '本机尚未启用原生会话只读浏览。'),
    'NATIVE_SESSION_UNAVAILABLE': (404, '此工作空间没有可读取的对应会话。'),
    'NATIVE_REVISION_STALE': (409, '会话来源或列表已变化，请刷新会话列表。'),
    'NATIVE_SCAN_BUSY': (409, '会话读取正在进行，请稍后重试。'),
    'NATIVE_VIEW_INVALID': (400, '会话整理参数无效。'),
    'NATIVE_VIEW_CONFLICT': (409, '会话整理状态已变化，请刷新后重试。'),
}
WORKSPACE = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'[0-9a-f]{32}')
UUID_PART = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
UUID_FILE = re.compile(UUID_PART + r'\.jsonl')
CODEX_FILE = re.compile(r'rollout-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9-]+-' + UUID_PART
                        + '(?:_' + UUID_PART + r')?\.jsonl')
LEAVES = {'codex': ('.codex', 'sessions'), 'claude': ('.claude', 'projects')}
EXCLUDED = frozenset(('node_modules', 'venv', '__pycache__'))
ROOT_BOUNDS = (2000, 200, 32 * 1024 * 1024)
SCAN_BOUNDS = (6000, 400, 64 * 1024 * 1024)
FILE_BYTES = 16 * 1024 * 1024
LINE_BYTES = 1024 * 1024
ROOT_LIMIT = 12
NESTED_ENTRIES = 1200
NESTED_DEPTH = 4
SCAN_SECONDS = 5
SNAPSHOT_SECONDS = 300
SNAPSHOT_LIMIT = 4
MAX_MESSAGES = 100
MAX_MESSAGE_CHARS = 16_000
MAX_BODY_BYTES = 128 * 1024


class NativeHistoryError(ValueError):
    def __init__(self, code):
        self.code = code
        self.status, message = ERRORS[code]
        super().__init__(message)


def request_parameters(params, *, transcript=False):
    patterns = {'workspace': WORKSPACE}
    if transcript:
        patterns.update(handle=HANDLE, revision=REVISION)
    if set(params) != set(patterns) or any(
            len(params[key]) != 1 or not pattern.fullmatch(params[key][0]) for key, pattern in patterns.items()):
        raise NativeHistoryError('NATIVE_QUERY_INVALID')
    return {key: values[0] for key, values in params.items()}


class StopScan(Exception):
    pass


@dataclass(slots=True)
class Budget:
    limits: tuple
    deadline: float
    entries: int = 0
    files: int = 0
    size: int = 0

    def time(self):
        if time.monotonic() >= self.deadline:
            raise StopScan('time_budget')

    def entry(self):
        self.time()
        if self.entries >= self.limits[0]:
            raise StopScan('entry_budget')
        self.entries += 1

    def file(self, size):
        self.time()
        if self.files >= self.limits[1]:
            raise StopScan('file_budget')
        if self.size + size > self.limits[2]:
            raise StopScan('byte_budget')
        self.files += 1
        self.size += size


@dataclass(frozen=True, slots=True)
class Source:
    registry: ProfileRegistry
    profile: ReadProfile
    labels: tuple


@dataclass(frozen=True, slots=True)
class FileRef:
    relative: Path = field(repr=False)
    stamp: tuple
    sha: str


@dataclass(frozen=True, slots=True)
class IndexedRead:
    source: Source
    index: LocatorIndex
    ref: FileRef
    directories: dict = field(repr=False)
    cwd_evidence: tuple = field(repr=False)


@dataclass(frozen=True, slots=True)
class Snapshot:
    revision: str
    expires: float
    workspace_identity: StorageRootIdentity
    reads: dict = field(repr=False)
    handles: frozenset = field(repr=False)


def stamp(meta):
    return meta.st_dev, meta.st_ino, meta.st_mode, meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns


def source_for(spec, seed):
    anchor = StorageRootIdentity.capture(spec.anchor)
    profile = ReadProfile(spec.id, spec.provider, spec.root, StorageRootIdentity.capture(spec.root))
    registry = ProfileRegistry(spec.anchor, anchor, [profile], seed)
    return Source(registry, profile, (display_source(spec.label, spec.provider),))


def merge_sources(candidates, reasons):
    """First source defines the canonical profile, while exact aliases add display labels."""
    physical = {}
    results = {}
    for candidate in candidates:
        profile = candidate.profile
        identity = profile.identity
        inode_key = identity.device, identity.inode
        if physical.setdefault(inode_key, (identity, profile.root)) != (identity, profile.root):
            reasons.add('source_alias')
            continue
        key = profile.provider, identity
        first = results.get(key)
        if first is None:
            results[key] = candidate
        else:
            results[key] = Source(first.registry, first.profile, tuple(sorted(set(first.labels + candidate.labels))))
    if len(results) > ROOT_LIMIT:
        reasons.add('source_budget')
    return list(results.values())[:ROOT_LIMIT]


@contextmanager
def rooted_view(path, identity):
    with verified_directory(path) as fd:
        if StorageRootIdentity.from_fd(fd) != identity:
            raise SessionRefused('ROOT_CHANGED')
        yield FileView(fd, identity.mount_id)
        with verified_directory(path) as current:
            if StorageRootIdentity.from_fd(current) != identity:
                raise SessionRefused('ROOT_CHANGED')


def path_name_valid(name):
    return (len(name) <= 255 and '\\' not in name and
            not any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in name))


def nested_specs(view, workspace_path, home, protected, deadline, reasons):
    budget = Budget((NESTED_ENTRIES, 0, 0), deadline)
    specs = []
    if workspace_path in (Path('/'), home):
        reasons.add('nested_unavailable')
        return specs

    def walk(relative):
        with view.directory(relative) as directory, os.scandir(directory) as entries:
            for entry in entries:
                budget.entry()
                name = entry.name
                child = relative / name
                location = workspace_path / child
                if (not path_name_valid(name) or name in EXCLUDED or location == home
                        or any(location.is_relative_to(path) for path in protected)):
                    continue
                provider = next((key for key, (folder, _) in LEAVES.items() if folder == name), None)
                if not provider and name.startswith('.'):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        continue
                    with view.pin(name, directory) as fd:
                        mode = os.fstat(fd).st_mode
                    if stat.S_ISLNK(mode):
                        reasons.add('nested_unavailable')
                        continue
                    if not stat.S_ISDIR(mode):
                        continue
                    if provider:
                        leaf = child / LEAVES[provider][1]
                        try:
                            with view.directory(leaf) as fd:
                                root_identity = StorageRootIdentity.from_fd(fd)
                        except FileNotFoundError:
                            continue  # Settings-only .codex/.claude is not a history source.
                        profile_id = 'nested-' + digest(canonical([provider, root_identity.fields()]))
                        specs.append(SourceSpec(profile_id, provider, workspace_path,
                                                workspace_path / leaf, '项目嵌套来源'))
                        if len(specs) > ROOT_LIMIT:
                            raise StopScan('source_budget')
                    elif len(child.parts) < NESTED_DEPTH:
                        walk(child)
                    else:
                        reasons.add('nested_depth_budget')
                except (OSError, Blocked, SessionRefused):
                    reasons.add('nested_unavailable')

    try:
        walk(Path('.'))
    except StopScan as exc:
        reasons.add(str(exc))
    except (OSError, Blocked, SessionRefused):
        reasons.add('nested_unavailable')
    return specs


def require_file(view, ref):
    with view.directory(ref.relative.parent) as directory, view.pin(ref.relative.name, directory) as fd:
        if stamp(os.fstat(fd)) != ref.stamp:
            raise SessionRefused('ROOT_CHANGED')


def require_directories(view, directories, deadline=None):
    for path, expected in directories.items():
        if deadline is not None and time.monotonic() >= deadline:
            raise StopScan('time_budget')
        with view.directory(path) as fd:
            if stamp(os.fstat(fd)) != expected:
                raise SessionRefused('ROOT_CHANGED')


def plausible_kind(provider, relative):
    depth = len(relative.parts)
    name = relative.name
    if provider == 'codex':
        if depth <= 3 and name.isascii() and name.isdigit():
            return 'directory'
        if depth == 4 and CODEX_FILE.fullmatch(name):
            return 'file'
    elif depth == 1 and not name.startswith('.'):
        return 'directory'
    elif depth == 2 and UUID_FILE.fullmatch(name):
        return 'file'
    return None


def scan_source(source, total):
    reasons = set()
    records, directories = [], {}
    local = Budget(ROOT_BOUNDS, total.deadline)
    provider = source.profile.provider

    def walk(view, relative):
        with view.directory(relative) as directory:
            directories[relative] = stamp(os.fstat(directory))
            with os.scandir(directory) as entries:
                for entry in entries:
                    total.entry()
                    local.entry()
                    child = relative / entry.name
                    kind = plausible_kind(provider, child)
                    if kind is None:
                        if entry.name.endswith('.jsonl'):
                            reasons.add('unsupported_layout')
                        continue
                    if not path_name_valid(entry.name):
                        reasons.add('source_unavailable')
                        continue
                    try:
                        if kind == 'directory' and not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                            continue
                        with view.pin(entry.name, directory) as pin:
                            initial = os.fstat(pin)
                        if kind == 'directory':
                            if stat.S_ISDIR(initial.st_mode):
                                walk(view, child)
                            elif stat.S_ISLNK(initial.st_mode):
                                reasons.add('source_unavailable')
                            continue
                        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                            reasons.add('source_unavailable')
                            continue
                        with view.regular(entry.name, directory) as fd:
                            before = os.fstat(fd)
                            local.file(before.st_size)
                            total.file(before.st_size)
                            meta = read_session(fd, provider, entry.name,
                                                {'file_bytes': FILE_BYTES, 'line_bytes': LINE_BYTES}, initial_stat=before)
                        total.time()
                        ref = FileRef(child, stamp(before), meta['sha256'])
                        require_file(view, ref)
                        records.append((ref, meta))
                    except StopScan:
                        raise
                    except (OSError, ValueError, TypeError, Blocked, RecursionError):
                        reasons.add('transcript_unavailable')

    try:
        source.registry.verify(source.profile.id)
        with rooted_view(source.profile.root, source.profile.identity) as view:
            try:
                walk(view, Path('.'))
                require_directories(view, directories, total.deadline)
            except StopScan as exc:
                reasons.add(str(exc))
        source.registry.verify(source.profile.id)
    except (OSError, ValueError, Blocked):
        reasons.add('source_unavailable')
    return records, directories, reasons


def checked_cwd(view, workspace_path, value):
    path = session_path(value, 'LOCATOR_REFUSED')
    if not path.is_relative_to(workspace_path):
        return None
    relative = path.relative_to(workspace_path)
    try:
        with view.directory(relative) as fd:
            return relative, StorageRootIdentity.from_fd(fd)
    except (OSError, ValueError, Blocked):
        return None


def require_cwds(view, evidence):
    for relative, expected in evidence:
        with view.directory(relative) as fd:
            if StorageRootIdentity.from_fd(fd) != expected:
                raise SessionRefused('ROOT_CHANGED')


def indexed_rows(source, records, directories, reasons, workspace, path, view, total):
    # Every parsed candidate participates in conflict detection, even when the
    # other candidate belongs to a different workspace and cannot be returned.
    counts = Counter(meta['session_id'] for _, meta in records)
    rows, reads, fixtures, candidates, approved = {}, {}, [], {}, set()
    source_incomplete = bool(reasons)
    try:
        for ref, meta in records:
            total.time()
            original = Path(meta['cwd'])
            if not original.is_relative_to(path):
                continue
            if not checked_cwd(view, path, original):
                reasons.add('cwd_unverified')
                continue
            evidence = []
            for cwd in meta['cwds']:
                total.time()
                evidence.append(checked_cwd(view, path, cwd))
            total.time()
            if None in evidence:
                reasons.add('cwd_unverified')
            candidates[(ref, meta['session_id'])] = (meta, tuple(evidence))
    except StopScan:
        reasons.add('time_budget')
        source_incomplete = True
    for (ref, sid), (meta, evidence) in candidates.items():
        profile = source.profile
        approved.add(meta['cwd'])
        locator = SessionLocator(profile.provider, profile.id, profile.identity, meta['cwd'], workspace, sid)
        handle = source.registry.handle(locator)
        access = 'conflict' if counts[sid] > 1 else 'unverified' if source_incomplete or None in evidence else 'readable'
        source_id = digest(canonical([str(ref.relative), ref.stamp[:2]]))
        fixtures.append(FixtureSession(profile.id, meta['cwd'], workspace, sid, source_id, verified=access == 'readable'))
        rows[handle] = {'handle': handle, 'provider': profile.provider, 'source_labels': source.labels,
                        'updated_at': meta['updated'] if access != 'conflict' else None, 'access': access}
        if access == 'readable':
            reads[handle] = (ref, evidence)
    index = LocatorIndex(source.registry, {workspace: approved}, fixtures)
    return rows, {handle: IndexedRead(source, index, ref, directories, evidence)
                  for handle, (ref, evidence) in reads.items()}


def bounded_lines(fd, length, checksum):
    pending = b''
    while length:
        chunk = os.read(fd, min(length, 64 * 1024))
        if not chunk:
            raise ValueError()
        length -= len(chunk)
        checksum.update(chunk)
        parts = (pending + chunk).split(b'\n')
        pending = parts.pop()
        if len(pending) >= LINE_BYTES:
            raise ValueError()
        for line in parts:
            if len(line) >= LINE_BYTES:
                raise ValueError()
            yield line
    if pending:
        raise ValueError()


def visible_message(obj, provider, literals):
    if provider == 'codex' and isinstance(obj, dict):
        for message in (obj, obj.get('payload')):
            if isinstance(message, dict) and (message.get('channel') not in (None, 'final')
                                              or message.get('recipient') not in (None, 'all')
                                              or message.get('phase') not in (None, 'final_answer')):
                return None
    message = _visible(obj, provider)
    if message is None:
        return None
    text = message['text']
    text.encode('utf-8')  # JSON can contain escaped surrogate code points.
    text = ''.join(char for char in text if char in '\n\t' or unicodedata.category(char) not in ('Cc', 'Cf'))
    for literal in literals:
        text = text.replace(literal, '[REDACTED]')
    # Recheck after removing controls, which might join a credential's pieces.
    text = safe_text(text).strip()
    return {'role': message['role'], 'text': text} if text else None


def project_messages(view, ref, provider, literals):
    checksum = hashlib.sha256()
    messages, size, truncated = deque(), 0, False
    with view.directory(ref.relative.parent) as directory, view.regular(ref.relative.name, directory) as fd:
        if stamp(os.fstat(fd)) != ref.stamp:
            raise SessionRefused('ROOT_CHANGED')
        for line in bounded_lines(fd, ref.stamp[4], checksum):
            message = visible_message(json.loads(line.decode('utf-8')), provider, literals)
            if message is None:
                continue
            if len(message['text']) > MAX_MESSAGE_CHARS:
                message['text'] = message['text'][:MAX_MESSAGE_CHARS]
                truncated = True
            weight = len(canonical(message)) + 1
            messages.append((message, weight))
            size += weight
            while len(messages) > MAX_MESSAGES or size > MAX_BODY_BYTES - 1024:
                _, weight = messages.popleft()
                size -= weight
                truncated = True
    if checksum.hexdigest() != ref.sha:
        raise SessionRefused('ROOT_CHANGED')
    require_file(view, ref)
    return [message for message, _ in messages], truncated


class NativeSessions:
    def __init__(self, store, config, seed, *, home=None, fixture_redactions=()):
        self.store = store
        self.config = config
        self.seed = seed
        self.home = Path.home() if home is None else Path(home)
        self.literals = tuple(value for value in fixture_redactions if isinstance(value, str) and value)
        self.protected = (store.state, Path(__file__).resolve().parents[2])
        self.snapshots = OrderedDict()
        self.scanning = set()
        self.guard = Lock()
        self.read_slots = BoundedSemaphore(4)
        specs = list(config.profiles)
        if config.discover_defaults:
            defaults = []
            for provider in PROVIDERS:
                root = self.home.joinpath(*LEAVES[provider])
                defaults.append(SourceSpec('default-' + provider, provider, self.home, root, '默认来源'))
            specs = defaults + specs
        self.static = []
        self.startup_reasons = set()
        for spec in specs:
            try:
                self.static.append(self.register(spec))
            except (OSError, ValueError, Blocked):
                self.startup_reasons.add('source_unavailable')

    @classmethod
    def from_path(cls, store, path):
        config = load_history_config(path)
        return cls(store, config, startup_seed(store.state))

    def register(self, spec):
        if any(spec.root.is_relative_to(path) or path.is_relative_to(spec.root) for path in self.protected):
            raise SessionRefused('PROFILE_REFUSED')
        if spec.root in (Path('/'), self.home):
            raise SessionRefused('PROFILE_REFUSED')
        return source_for(spec, self.seed)

    def workspace(self, key, *, database_error='NATIVE_REVISION_STALE'):
        try:
            proof = capture_workspace_proof(self.store.state)
            path = Path(self.store.workspace(key, existing_only=True)['path'])
            identity = StorageRootIdentity.capture(path)
            if digest(canonical([str(path), identity.device, identity.inode])) != key:
                raise Conflict('workspace changed')
            require_current(self.store.state, proof)
            return path, identity, proof
        except WorkspaceDatabaseConflict:
            raise NativeHistoryError(database_error) from None
        except Conflict:
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None
        except (OSError, ValueError, Blocked):
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE') from None

    def listing(self, workspace):
        path, identity, proof = self.workspace(workspace)
        with self.guard:
            if workspace in self.scanning or len(self.scanning) >= 2:
                raise NativeHistoryError('NATIVE_SCAN_BUSY')
            self.scanning.add(workspace)
            self.snapshots.pop(workspace, None)
        try:
            result, snapshot = self.scan(workspace, path, identity)
            self.require_proof(proof)
            with self.guard:
                self.snapshots[workspace] = snapshot
                while len(self.snapshots) > SNAPSHOT_LIMIT:
                    self.snapshots.popitem(last=False)
            return result
        finally:
            with self.guard:
                self.scanning.remove(workspace)

    def require_proof(self, proof):
        try:
            require_current(self.store.state, proof)
        except (Conflict, OSError, ValueError, Blocked):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None

    def scan(self, workspace, path, identity):
        reasons = set(self.startup_reasons)
        candidates = list(self.static)
        total = Budget(SCAN_BOUNDS, time.monotonic() + SCAN_SECONDS)
        rows, reads = {}, {}
        try:
            with rooted_view(path, identity) as view:
                if self.config.discover_nested:
                    for spec in nested_specs(view, path, self.home, self.protected, total.deadline, reasons):
                        try:
                            total.time()
                            candidates.append(self.register(spec))
                        except StopScan:
                            reasons.add('time_budget')
                            break
                        except (OSError, ValueError, Blocked):
                            reasons.add('nested_unavailable')
                sources = merge_sources(candidates, reasons)
                for source in sources:
                    try:
                        total.time()
                    except StopScan:
                        reasons.add('time_budget')
                        break
                    records, directories, source_reasons = scan_source(source, total)
                    scanned, accessible = indexed_rows(source, records, directories, source_reasons,
                                                       workspace, path, view, total)
                    rows.update(scanned)
                    reads.update(accessible)
                    reasons.update(source_reasons)
                    if any(row['access'] == 'conflict' for row in scanned.values()):
                        reasons.add('identity_conflict')
        except (OSError, ValueError, Blocked):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None
        if not candidates and not self.config.discover_nested:
            reasons.add('no_sources')
        if not rows and not candidates and reasons:
            state = 'unavailable'
        else:
            state = 'partial' if reasons else 'complete'
        revision = secrets.token_hex(16)
        sessions = sorted(rows.values(), key=lambda row: row['updated_at'] or '', reverse=True)
        result = {'workspace': workspace, 'revision': revision,
                  'coverage': {'state': state, 'reasons': sorted(reasons)}, 'sessions': sessions}
        return result, Snapshot(revision, time.monotonic() + SNAPSHOT_SECONDS, identity, reads, frozenset(rows))

    def _snapshot(self, workspace, revision):
        snapshot = self.snapshots.get(workspace)
        if snapshot is None or snapshot.revision != revision or snapshot.expires <= time.monotonic():
            if any(other.revision == revision and other.expires > time.monotonic()
                   for key, other in self.snapshots.items() if key != workspace):
                raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE')
            raise NativeHistoryError('NATIVE_REVISION_STALE')
        return snapshot

    @contextmanager
    def verified_record(self, workspace, handle, path, identity, record):
        locator = record.index.resolve(workspace, handle)
        with rooted_view(path, identity) as scope, rooted_view(record.source.profile.root, locator.root_identity) as view:
            require_cwds(scope, record.cwd_evidence)
            require_directories(view, record.directories)
            yield view, locator
            require_directories(view, record.directories)
            require_cwds(scope, record.cwd_evidence)
            require_file(view, record.ref)
        record.source.registry.verify(locator.profile_id)

    def read(self, workspace, handle, revision):
        path, identity, proof = self.workspace(workspace)
        with self.guard:
            snapshot = self._snapshot(workspace, revision)
            record = snapshot.reads.get(handle)
        if record is None:
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE')
        if identity != snapshot.workspace_identity:
            raise NativeHistoryError('NATIVE_REVISION_STALE')
        if not self.read_slots.acquire(blocking=False):
            raise NativeHistoryError('NATIVE_SCAN_BUSY')
        try:
            with self.verified_record(workspace, handle, path, identity, record) as (view, locator):
                messages, truncated = project_messages(view, record.ref, locator.provider, self.literals)
            self.require_proof(proof)
            with self.guard:
                self._snapshot(workspace, revision)
        except NativeHistoryError:
            raise
        except UnicodeError:
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE') from None
        except (OSError, ValueError, TypeError, Blocked, RecursionError):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None
        finally:
            self.read_slots.release()
        return {'workspace': workspace, 'revision': revision, 'handle': handle, 'messages': messages,
                'truncated': truncated, 'readonly': True}
