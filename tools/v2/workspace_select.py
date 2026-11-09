"""Guarded same-database workspace lookup for the read-only history service."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import stat

from orchestration.common import Blocked
from studio.graph import Invalid
from studio.server import Conflict

from .native_history_config import SEED_FILE
from .project_files import FileView, mount_id
from .session_identity import verified_directory

MAIN = 'v2.sqlite'
SIDECARS = frozenset((MAIN + '-wal', MAIN + '-shm'))
JOURNAL = MAIN + '-journal'
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
MAX_STATE_NAMES = 4096
STATE_FILES = SIDECARS | {MAIN, JOURNAL, 'v2-service.lock', '.native-metadata-preflight.lock',
                         'flow-artifacts', SEED_FILE}


def state_file_allowed(name):
    return name in STATE_FILES or re.fullmatch(r'[0-9a-f]{32}\.worker\.lock|recovery-[0-9a-f]{64}\.json', name)


@dataclass(frozen=True, slots=True)
class WorkspaceProof:
    root: tuple
    database: tuple
    names: frozenset


def state_identity(directory):
    info = os.fstat(directory)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise Conflict('原生会话读取暂不能核验工作空间状态。')
    return mount_id(directory), info.st_dev, info.st_ino


def state_names(directory):
    # Use a fresh directory description so a prior enumeration cannot alter
    # the offset used by this check.
    fd = os.open('.', DIR_FLAGS, dir_fd=directory)
    try:
        with os.scandir(fd) as entries:
            names = set()
            for entry in entries:
                if len(names) >= MAX_STATE_NAMES:
                    raise Conflict('原生会话读取暂不能核验工作空间状态。')
                names.add(entry.name)
        return names
    finally:
        os.close(fd)


def database_identity(directory, mount):
    view = FileView(directory, mount)
    with view.regular(MAIN, directory) as fd:
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) != 0o600:
            raise Conflict('原生会话读取暂不能核验工作空间状态。')
        header = os.pread(fd, 100, 0)
        if len(header) != 100 or not header.startswith(b'SQLite format 3\0') or header[18:20] != b'\x02\x02':
            raise Conflict('原生会话读取暂不能核验工作空间状态。')
        return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns,
                header[24:28], header[92:96])


def require_current(state, proof):
    with verified_directory(state) as current:
        if (state_identity(current) != proof.root or database_identity(current, proof.root[0]) != proof.database
                or state_names(current) != proof.names):
            raise Conflict('原生会话读取期间工作空间状态已变化。')


def capture_workspace_proof(state):
    try:
        with verified_directory(state) as directory:
            root = state_identity(directory)
            names = frozenset(state_names(directory))
            if (names & (SIDECARS | {JOURNAL}) or MAIN not in names
                    or not all(state_file_allowed(name) for name in names)):
                raise Conflict('原生会话读取暂不能核验工作空间状态。')
            proof = WorkspaceProof(root, database_identity(directory, root[0]), names)
            require_current(state, proof)
            return proof
    except (OSError, ValueError, Blocked):
        raise Conflict('原生会话读取暂不能核验工作空间状态。') from None


def select_existing_workspace(state, key):
    try:
        proof = capture_workspace_proof(state)
        # SQLite accepts a pathname, so a private same-user state directory
        # remains the authority. Recheck it immediately after connecting.
        uri = (Path(state) / MAIN).as_uri() + '?mode=rw'
        db = sqlite3.connect(uri, uri=True, timeout=0)
        try:
            db.execute('PRAGMA query_only=ON')
            if db.execute('PRAGMA query_only').fetchone() != (1,):
                raise Conflict('原生会话读取暂不能核验工作空间状态。')
            require_current(state, proof)
            row = db.execute("SELECT data FROM records WHERE kind='workspace' AND id=?", (key,)).fetchone()
        finally:
            db.close()
            require_current(state, proof)
    except (OSError, ValueError, sqlite3.Error, Blocked):
        raise Conflict('原生会话读取暂不能核验工作空间状态。') from None
    if row is None:
        raise Invalid('找不到此记录，请刷新')
    return row[0]
