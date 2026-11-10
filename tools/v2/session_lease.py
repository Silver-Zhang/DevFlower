"""Kernel-backed session gates and the persistent fixture lease protocol."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import threading

from orchestration.common import Blocked, canonical
from orchestration.store import process_identity

from .project_files import mount_id
from .session_identity import (
    HANDLE, LocatorIndex, ProfileRegistry, SessionRefused, StorageRootIdentity,
    identifier, session_path, verified_directory,
)

IDLE_VERIFIED = 'IDLE_VERIFIED'
BUSY = 'BUSY'
UNKNOWN = 'UNKNOWN'
EXTERNAL_STATES = (IDLE_VERIFIED, BUSY, UNKNOWN)
PHASES = ('PREPARED', 'CHECKED', 'CLOSED', 'RECOVERY_REQUIRED')
OWNER_FIELDS = ('host', 'boot_id', 'pid', 'start')
RECEIPT_FIELDS = frozenset(('version', 'lease_key', 'locator_handle', 'operation_id',
                            'intent_id', 'owner', 'external_state', 'phase'))
LOCAL_FILESYSTEMS = frozenset(('ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'f2fs', 'tmpfs', 'ramfs', 'overlay', 'zfs'))
MAX_INTENTS = 256
MAX_BOOK_BYTES = 256 * 1024
AUTHORITY_FILE = 'authority.json'
ENROLLMENT_PENDING = 'authority.pending'


def valid_owner(record):
    return (isinstance(record, dict) and set(record) == set(OWNER_FIELDS)
            and all(isinstance(record[key], str) and 0 < len(record[key]) <= 255
                    for key in ('host', 'boot_id', 'start'))
            and record['start'].isascii() and record['start'].isdigit()
            and type(record['pid']) is int and record['pid'] > 0)


def current_owner():
    pid = os.getpid()
    try:
        if os.readlink('/proc/self') != str(pid):
            raise SessionRefused('OWNER_UNKNOWN')
        record = process_identity(pid)
    except OSError:
        raise SessionRefused('OWNER_UNKNOWN') from None
    owner = {key: record.get(key) for key in OWNER_FIELDS} if isinstance(record, dict) else None
    if (not valid_owner(owner) or owner['pid'] != pid
            or record.get('state') not in ('R', 'S', 'D', 'T', 't', 'I', 'P', 'W')):
        raise SessionRefused('OWNER_UNKNOWN')
    return owner


def require_owner(owner):
    if owner['pid'] != os.getpid() or current_owner() != owner:
        raise SessionRefused('OWNER_UNKNOWN')


def observed_state(observer, locator):
    try:
        value = observer(locator)
    except Exception:
        return UNKNOWN
    return value if type(value) is str and value in EXTERNAL_STATES else UNKNOWN


def require_idle(state):
    if state != IDLE_VERIFIED:
        raise SessionRefused('EXTERNAL_BUSY' if state == BUSY else 'EXTERNAL_UNKNOWN')


def mount_table():
    with open('/proc/self/mountinfo', encoding='utf-8', errors='surrogateescape') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise SessionRefused('AUTHORITY_REFUSED')
    table = {}
    for line in raw.rstrip('\n').split('\n'):
        left, separator, right = line.partition(' - ')
        fields, info = left.split(), right.split()
        if not separator or len(fields) < 6 or len(info) < 3:
            raise SessionRefused('AUTHORITY_REFUSED')
        table[int(fields[0])] = (decode_mount_path(fields[3]), decode_mount_path(fields[4]), info[0])
    return table


def decode_mount_path(value):
    return Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value))


def physical_path(path, identity, table):
    root, target, _ = table[identity.mount_id]
    return root / path.relative_to(target)


def file_stat(fd, expected_mount, code):
    meta = os.fstat(fd)
    if (not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o600
            or meta.st_uid != os.geteuid() or meta.st_nlink != 1 or mount_id(fd) != expected_mount):
        raise SessionRefused(code)
    return meta


def inode(meta):
    return [meta.st_dev, meta.st_ino]


def stamp(meta):
    return (*inode(meta), meta.st_size, meta.st_ctime_ns, meta.st_mtime_ns)


@contextmanager
def pin_file(directory, name):
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
    try:
        yield fd
    finally:
        os.close(fd)


def require_file_name(directory, name, expected_mount, expected, code):
    with pin_file(directory, name) as fd:
        if stamp(file_stat(fd, expected_mount, code)) != stamp(expected):
            raise SessionRefused(code)


def read_private_json(directory, name, expected_mount, limit, code):
    with pin_file(directory, name) as pin:
        before = file_stat(pin, expected_mount, code)
        if not 0 < before.st_size <= limit:
            raise SessionRefused(code)
        fd = os.open(f'/proc/self/fd/{pin}', os.O_RDONLY | os.O_NONBLOCK)
        try:
            if stamp(file_stat(fd, expected_mount, code)) != stamp(before):
                raise SessionRefused(code)
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(limit + 1)
            if len(raw) != before.st_size or stamp(file_stat(fd, expected_mount, code)) != stamp(before):
                raise SessionRefused(code)
        finally:
            os.close(fd)
        require_file_name(directory, name, expected_mount, before, code)
    try:
        value = json.loads(raw, parse_constant=lambda _: None)
        # Enforce the canonical representation, including rejection of duplicate keys.
        if canonical(value) + b'\n' != raw:
            raise SessionRefused(code)
        return value, before
    except (ValueError, TypeError, RecursionError):
        raise SessionRefused(code) from None


def exists(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def exclusive_file(directory, name):
    return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)


def write_json(fd, value):
    with os.fdopen(os.dup(fd), 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
    os.fsync(fd)


class SessionLeaseAuthority:
    """One operator-provisioned directory and registry for all participating processes."""
    def __init__(self, path, identity, registry):
        self._configure(path, identity, registry)
        with self._directory(current_owner()):
            pass

    def _configure(self, path, identity, registry):
        if type(identity) is not StorageRootIdentity or type(registry) is not ProfileRegistry:
            raise SessionRefused('AUTHORITY_REFUSED')
        self._path = session_path(path, 'AUTHORITY_REFUSED')
        self._identity = identity
        self._registry = registry
        self._mount = identity.mount_id

    @classmethod
    def provision(cls, path, identity, registry):
        """Explicit fixture setup. Never replace an existing enrollment or recover state."""
        authority = cls.__new__(cls)
        authority._configure(path, identity, registry)
        owner = current_owner()
        with authority._directory(owner, unbound=True) as directory:
            expected = authority._enrollment(owner)
            try:
                existing, _ = read_private_json(directory, AUTHORITY_FILE, authority._mount, 4096, 'AUTHORITY_REFUSED')
            except FileNotFoundError:
                with os.scandir(directory) as entries:
                    if next(entries, None) is not None:
                        raise SessionRefused('AUTHORITY_REFUSED')
                marker = exclusive_file(directory, ENROLLMENT_PENDING)
                try:
                    marker_meta = file_stat(marker, authority._mount, 'AUTHORITY_REFUSED')
                    os.fsync(marker)
                    os.fsync(directory)
                    fd = exclusive_file(directory, AUTHORITY_FILE)
                    try:
                        file_stat(fd, authority._mount, 'AUTHORITY_REFUSED')
                        authority._check_directory(directory, owner, unbound=True)
                        write_json(fd, expected)
                        require_file_name(directory, AUTHORITY_FILE, authority._mount,
                                          file_stat(fd, authority._mount, 'AUTHORITY_REFUSED'), 'AUTHORITY_REFUSED')
                        os.fsync(directory)
                    finally:
                        os.close(fd)
                    require_file_name(directory, ENROLLMENT_PENDING, authority._mount, marker_meta, 'AUTHORITY_REFUSED')
                    os.unlink(ENROLLMENT_PENDING, dir_fd=directory)
                finally:
                    os.close(marker)
            else:
                if canonical(existing) != canonical(expected):
                    raise SessionRefused('AUTHORITY_REFUSED')
            authority._check_directory(directory, owner)
        return authority

    def _enrollment(self, owner):
        namespace = os.stat('/proc/self/ns/mnt')
        if namespace.st_ino <= 0:
            raise SessionRefused('AUTHORITY_REFUSED')
        return dict(version=1, authority=self._identity.fields(), registry=self._registry.fingerprint,
                    host=owner['host'], boot_id=owner['boot_id'], mount_namespace=[namespace.st_dev, namespace.st_ino])

    @contextmanager
    def _directory(self, owner, *, unbound=False):
        with verified_directory(self._path, 'AUTHORITY_REFUSED') as directory:
            self._check_directory(directory, owner, unbound=unbound)
            yield directory

    def _check_directory(self, directory, owner, *, unbound=False):
        require_owner(owner)
        try:
            meta = os.fstat(directory)
            if (StorageRootIdentity.from_fd(directory) != self._identity or stat.S_IMODE(meta.st_mode) != 0o700
                    or meta.st_uid != os.geteuid()):
                raise SessionRefused('AUTHORITY_REFUSED')
            table = mount_table()
            if table[self._mount][2] not in LOCAL_FILESYSTEMS:
                raise SessionRefused('AUTHORITY_REFUSED')
            here = physical_path(self._path, self._identity, table)
            for profile in self._registry.profiles:
                root = profile.root
                if self._path.is_relative_to(root) or root.is_relative_to(self._path):
                    raise SessionRefused('AUTHORITY_REFUSED')
                if profile.identity.device == self._identity.device:
                    there = physical_path(root, profile.identity, table)
                    if here.is_relative_to(there) or there.is_relative_to(here):
                        raise SessionRefused('AUTHORITY_REFUSED')
            if not unbound:
                if exists(directory, ENROLLMENT_PENDING):
                    raise SessionRefused('AUTHORITY_REFUSED')
                actual, _ = read_private_json(directory, AUTHORITY_FILE, self._mount, 4096, 'AUTHORITY_REFUSED')
                if canonical(actual) != canonical(self._enrollment(owner)):
                    raise SessionRefused('AUTHORITY_REFUSED')
            with verified_directory(self._path, 'AUTHORITY_REFUSED') as current:
                current_meta = os.fstat(current)
                if (StorageRootIdentity.from_fd(current) != self._identity or stat.S_IMODE(current_meta.st_mode) != 0o700
                        or current_meta.st_uid != os.geteuid()):
                    raise SessionRefused('AUTHORITY_REFUSED')
        except (OSError, ValueError, KeyError, Blocked) as error:
            if isinstance(error, SessionRefused):
                raise
            raise SessionRefused('AUTHORITY_REFUSED') from None

    def _resolve(self, index, workspace, handle):
        if type(index) is not LocatorIndex or index.registry.fingerprint != self._registry.fingerprint:
            raise SessionRefused('LOCATOR_REFUSED')
        return index.resolve(workspace, handle)

    def _open_existing_lock(self, directory, name):
        with pin_file(directory, name) as pin:
            before = file_stat(pin, self._mount, 'AUTHORITY_REFUSED')
            fd = os.open(f'/proc/self/fd/{pin}', os.O_RDONLY | os.O_NONBLOCK)
            try:
                if stamp(file_stat(fd, self._mount, 'AUTHORITY_REFUSED')) != stamp(before):
                    raise SessionRefused('AUTHORITY_REFUSED')
                return fd
            except BaseException:
                os.close(fd)
                raise

    def _create_locked_name(self, directory, name):
        # Hold the anonymous inode's flock before its name can be observed.
        fd = os.open('.', os.O_RDWR | os.O_TMPFILE | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            meta = os.fstat(fd)
            if (not stat.S_ISREG(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o600
                    or meta.st_uid != os.geteuid() or meta.st_nlink != 0 or mount_id(fd) != self._mount):
                raise SessionRefused('AUTHORITY_REFUSED')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.link(f'/proc/self/fd/{fd}', name, dst_dir_fd=directory, follow_symlinks=True)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def _lock(self, directory, key):
        name = key + '.lock'
        created = False
        try:
            fd = self._open_existing_lock(directory, name)
        except FileNotFoundError:
            try:
                fd = self._create_locked_name(directory, name)
                created = True
            except FileExistsError:
                fd = self._open_existing_lock(directory, name)
        try:
            if not created:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SessionRefused('LEASE_HELD') from None
            self._check_lock(directory, fd, key)
            if created:
                try:
                    os.fsync(fd)
                    os.fsync(directory)
                except OSError:
                    raise SessionRefused('RECOVERY_REQUIRED') from None
            yield fd, created
        finally:
            # Never LOCK_UN an open file description that a fork may share.
            os.close(fd)

    def _check_lock(self, directory, fd, key):
        meta = file_stat(fd, self._mount, 'AUTHORITY_REFUSED')
        if meta.st_size:
            raise SessionRefused('AUTHORITY_REFUSED')
        require_file_name(directory, key + '.lock', self._mount, meta, 'AUTHORITY_REFUSED')
        return inode(meta)

    def _book(self, directory, key, lock_inode, *, created=False):
        try:
            if exists(directory, key + '.pending') or exists(directory, key + '.write'):
                raise SessionRefused('RECOVERY_REQUIRED')
            try:
                book, previous = read_private_json(directory, key + '.json', self._mount,
                                                   MAX_BOOK_BYTES, 'RECOVERY_REQUIRED')
            except FileNotFoundError:
                if not created:
                    raise SessionRefused('RECOVERY_REQUIRED') from None
                return {'version': 1, 'lock': lock_inode, 'receipts': []}, None
            if created or not valid_book(book, key, lock_inode):
                raise SessionRefused('RECOVERY_REQUIRED')
            return book, previous
        except (OSError, Blocked):
            raise SessionRefused('RECOVERY_REQUIRED') from None

    def _publish(self, directory, fd, key, book, previous, owner):
        if len(canonical(book)) + 1 > MAX_BOOK_BYTES:
            raise SessionRefused('RECOVERY_REQUIRED')
        marker_name, staged_name, book_name = key + '.pending', key + '.write', key + '.json'
        self._check_directory(directory, owner)
        self._check_lock(directory, fd, key)
        try:
            marker = exclusive_file(directory, marker_name)
            try:
                marker_meta = file_stat(marker, self._mount, 'RECOVERY_REQUIRED')
                os.fsync(marker)
                os.fsync(directory)
                staged = exclusive_file(directory, staged_name)
                try:
                    file_stat(staged, self._mount, 'RECOVERY_REQUIRED')
                    write_json(staged, book)
                    staged_meta = file_stat(staged, self._mount, 'RECOVERY_REQUIRED')
                    self._check_directory(directory, owner)
                    self._check_lock(directory, fd, key)
                    require_file_name(directory, marker_name, self._mount, marker_meta, 'RECOVERY_REQUIRED')
                    require_file_name(directory, staged_name, self._mount, staged_meta, 'RECOVERY_REQUIRED')
                    if previous is None:
                        if exists(directory, book_name):
                            raise SessionRefused('RECOVERY_REQUIRED')
                    else:
                        require_file_name(directory, book_name, self._mount, previous, 'RECOVERY_REQUIRED')
                    os.replace(staged_name, book_name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                    require_file_name(directory, book_name, self._mount,
                                      file_stat(staged, self._mount, 'RECOVERY_REQUIRED'), 'RECOVERY_REQUIRED')
                    # A surviving marker blocks reuse even if a failed publication
                    # left a seemingly CLOSED ledger at the destination.
                    require_file_name(directory, marker_name, self._mount, marker_meta, 'RECOVERY_REQUIRED')
                    os.unlink(marker_name, dir_fd=directory)
                finally:
                    os.close(staged)
            finally:
                os.close(marker)
        except (OSError, Blocked):
            raise SessionRefused('RECOVERY_REQUIRED') from None

    def acquire(self, index, workspace_id, handle, operation_id, intent_id, observe_external):
        if not identifier(operation_id) or not identifier(intent_id):
            raise SessionRefused('LOCATOR_REFUSED')
        owner = current_owner()
        locator = self._resolve(index, workspace_id, handle)
        key = locator.lease_key
        with self._directory(owner) as directory:
            state = observed_state(observe_external, locator)
            require_idle(state)
            self._check_directory(directory, owner)
            with self._lock(directory, key) as (fd, created):
                self._check_directory(directory, owner)
                if self._resolve(index, workspace_id, handle) != locator:
                    raise SessionRefused('LOCATOR_REFUSED')
                lock_inode = self._check_lock(directory, fd, key)
                book, previous = self._book(directory, key, lock_inode, created=created)
                rows = book['receipts']
                if any(row['intent_id'] == intent_id for row in rows):
                    raise SessionRefused('INTENT_EXISTS')
                if len(rows) >= MAX_INTENTS or (rows and rows[-1]['phase'] != 'CLOSED'):
                    raise SessionRefused('RECOVERY_REQUIRED')
                receipt = dict(version=1, lease_key=key, locator_handle=handle, operation_id=operation_id,
                               intent_id=intent_id, owner=owner, external_state=state, phase='PREPARED')
                rows.append(receipt)
                self._publish(directory, fd, key, book, previous, owner)
                self._check_directory(directory, owner)
                self._check_lock(directory, fd, key)
                if self._resolve(index, workspace_id, handle) != locator:
                    raise SessionRefused('LOCATOR_REFUSED')
                held_directory = os.dup(directory)
                try:
                    held_fd = os.dup(fd)
                except OSError:
                    os.close(held_directory)
                    raise SessionRefused('RECOVERY_REQUIRED') from None
                return SessionLease(self, held_directory, held_fd, index, locator, receipt, observe_external)


def valid_book(book, key, lock_inode):
    if (not isinstance(book, dict) or set(book) != {'version', 'lock', 'receipts'}
            or type(book['version']) is not int or book['version'] != 1 or book['lock'] != lock_inode
            or any(type(value) is not int for value in book['lock'])
            or not isinstance(book['receipts'], list) or not 1 <= len(book['receipts']) <= MAX_INTENTS):
        return False
    seen = set()
    for index, row in enumerate(book['receipts']):
        if (not isinstance(row, dict) or set(row) != RECEIPT_FIELDS or type(row['version']) is not int
                or row['version'] != 1 or row['lease_key'] != key or not isinstance(row['locator_handle'], str)
                or not HANDLE.fullmatch(row['locator_handle']) or not valid_owner(row['owner'])
                or not identifier(row['operation_id']) or not identifier(row['intent_id'])
                or row['intent_id'] in seen or row['phase'] not in PHASES or row['external_state'] not in EXTERNAL_STATES
                or (row['phase'] != 'RECOVERY_REQUIRED' and row['external_state'] != IDLE_VERIFIED)
                or (index < len(book['receipts']) - 1 and row['phase'] != 'CLOSED')):
            return False
        seen.add(row['intent_id'])
    return True


class SessionLease:
    def __init__(self, authority, directory, fd, index, locator, receipt, observer):
        self._authority = authority
        self._directory = directory
        self._fd = fd
        self._index = index
        self._locator = locator
        self._receipt = receipt
        self._observer = observer
        self._fixture_permitted = False
        self._serial = threading.Lock()

    @property
    def receipt(self):
        return deepcopy(self._receipt)

    def _require_process(self):
        # A forked process must refuse before touching a possibly inherited
        # Python thread lock, even if the parent was holding that lock at fork.
        require_owner(self._receipt['owner'])

    def _update(self, phase, external_state):
        if self._fd is None:
            raise SessionRefused('OWNER_UNKNOWN')
        owner = self._receipt['owner']
        key = self._locator.lease_key
        authority = self._authority
        authority._check_directory(self._directory, owner)
        if authority._resolve(self._index, self._locator.workspace_id, self._receipt['locator_handle']) != self._locator:
            raise SessionRefused('LOCATOR_REFUSED')
        lock_inode = authority._check_lock(self._directory, self._fd, key)
        book, previous = authority._book(self._directory, key, lock_inode)
        if book['receipts'][-1] != self._receipt:
            raise SessionRefused('RECOVERY_REQUIRED')
        receipt = dict(self._receipt, phase=phase, external_state=external_state)
        book['receipts'][-1] = receipt
        authority._publish(self._directory, self._fd, key, book, previous, owner)
        self._receipt = receipt
        return self.receipt

    def check_before_fixture(self):
        self._require_process()
        with self._serial:
            if self._fd is None or self._receipt['phase'] != 'PREPARED':
                raise SessionRefused('RECOVERY_REQUIRED')
            state = observed_state(self._observer, self._locator)
            receipt = self._update('CHECKED' if state == IDLE_VERIFIED else 'RECOVERY_REQUIRED', state)
            require_idle(state)
            owner, handle = self._receipt['owner'], self._receipt['locator_handle']
            self._authority._check_directory(self._directory, owner)
            self._authority._check_lock(self._directory, self._fd, self._locator.lease_key)
            if self._authority._resolve(self._index, self._locator.workspace_id, handle) != self._locator:
                raise SessionRefused('LOCATOR_REFUSED')
            self._fixture_permitted = True
            return receipt

    def record_outcome(self, *, completed):
        self._require_process()
        with self._serial:
            if type(completed) is not bool or not self._fixture_permitted or self._receipt['phase'] != 'CHECKED':
                raise SessionRefused('RECOVERY_REQUIRED')
            return self._update('CLOSED' if completed else 'RECOVERY_REQUIRED', self._receipt['external_state'])

    def _close_fds(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)
            os.close(self._directory)

    def close(self):
        if self._receipt['owner']['pid'] != os.getpid():
            self._close_fds()
            return
        with self._serial:
            self._close_fds()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@contextmanager
def production_session_gate(binding, locator, *, target=None):
    """Use the same native-root kernel gate across participating DevFlower clients.

    Read locators alone grant no authority. The caller holds a frozen operation,
    checks its recovery journal inside this gate, and uses the native writer
    implementation for Codex external concurrency.
    """
    from studio.native_local import session_lease
    from studio.session_sources import UUID

    sid = target or binding.get('session_id')
    if sid is not None and (not isinstance(sid, str) or not UUID.fullmatch(sid)):
        raise SessionRefused('LOCATOR_REFUSED')
    root = session_path(binding['config_root'])
    home = session_path(binding['home'])
    if (binding.get('execution_backend') != 'native-local'
            or root != home / ('.' + binding['provider'])
            or binding.get('local_lock_root') != str(root / '.devflower-writer-locks')
            or StorageRootIdentity.capture(root).fields() != binding['root_identity']):
        raise SessionRefused('ROOT_CHANGED')
    if locator is not None and (locator.provider, locator.native_uuid, locator.original_cwd, locator.workspace_id) != (
            binding['provider'], sid, binding['cwd'], binding['project']):
        raise SessionRefused('LOCATOR_REFUSED')
    # Retain the V1 lock namespace so separate Development states and old
    # participating V1 paths serialize on the same provider UUID.
    with verified_directory(root) as root_fd, verified_directory(home) as home_fd:
        root_mount = StorageRootIdentity.from_fd(root_fd).mount_id
        if (any(os.fstat(fd).st_uid != os.geteuid() for fd in (root_fd, home_fd))
                or mount_table().get(root_mount, (None, None, None))[2] not in LOCAL_FILESYSTEMS):
            raise SessionRefused('PROFILE_REFUSED')
        with ExitStack() as held:
            try:
                held.enter_context(session_lease(dict(binding, session_id=sid)))
            except Blocked as error:
                if str(error) == 'live lease held':
                    raise SessionRefused('LEASE_HELD') from None
                raise
            if StorageRootIdentity.capture(root).fields() != binding['root_identity']:
                raise SessionRefused('ROOT_CHANGED')
            yield


CODEX_COORDINATION_VERSION = 'codex-cli 0.163.0'


@contextmanager
def codex_presentation_guard(config_root, session_ids):
    """Guard the exact Codex 0.163 writer file lifecycle without creating native files.

    Native writers take .coordination.lock around per-UUID file creation and
    removal. Holding it makes an absent per-UUID lock meaningful, while any
    existing per-UUID lock is checked without waiting for its current writer.
    """
    from studio.session_sources import UUID

    session_ids = tuple(sorted(set(session_ids)))
    if not session_ids or any(not isinstance(sid, str) or not UUID.fullmatch(sid) for sid in session_ids):
        raise SessionRefused('LOCATOR_REFUSED')
    root = Path(config_root) / 'thread-writer-locks'
    with verified_directory(root, 'EXTERNAL_UNKNOWN') as directory, ExitStack() as held:
        expected = StorageRootIdentity.from_fd(directory)
        if mount_table().get(expected.mount_id, (None, None, None))[2] not in LOCAL_FILESYSTEMS:
            raise SessionRefused('EXTERNAL_UNKNOWN')
        locks = {}
        def lock(name, *, optional=False):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                if optional:
                    locks[name] = None
                    return
                raise SessionRefused('EXTERNAL_UNKNOWN') from None
            held.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or mount_id(fd) != expected.mount_id):
                raise SessionRefused('EXTERNAL_UNKNOWN')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SessionRefused('EXTERNAL_BUSY') from None
            locks[name] = inode(info)

        lock('.coordination.lock')
        for sid in session_ids:
            lock(sid + '.lock', optional=True)

        def check():
            with verified_directory(root, 'EXTERNAL_UNKNOWN') as current:
                if StorageRootIdentity.from_fd(current) != expected:
                    raise SessionRefused('EXTERNAL_UNKNOWN')
            for name, identity in locks.items():
                try:
                    actual = inode(os.stat(name, dir_fd=directory, follow_symlinks=False))
                except FileNotFoundError:
                    actual = None
                if actual != identity:
                    raise SessionRefused('EXTERNAL_UNKNOWN')

        check()
        yield check
