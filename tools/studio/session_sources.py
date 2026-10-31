"""Bounded, read-only native metadata readers. Never return conversation bodies."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.a-zA-Z0-9]+)?')
LIMITS = dict(depth=5, entries=2000, files=200, bytes=32*1024*1024,
              file_bytes=16*1024*1024, line_bytes=1024*1024)


class Refused(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)



class SnapshotReader(io.RawIOBase):
    """Limit kernel reads, including BufferedReader read-ahead, to one snapshot."""
    def __init__(self, fd, size):
        super().__init__()
        self.fd = fd
        self.remaining = size

    def readable(self):
        return True

    def readinto(self, buffer):
        if not self.remaining:
            return 0
        data = os.read(self.fd, min(len(buffer), self.remaining))
        buffer[:len(data)] = data
        self.remaining -= len(data)
        return len(data)


@contextmanager
def regular_at(name, dir_fd=None):
    """Pin metadata without opening a device, then read only that regular inode."""
    pin = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        meta = os.fstat(pin)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise Refused('not_private_regular')
        if not meta.st_mode & 0o444:
            raise PermissionError()
        # Linux /proc/self/fd reopens the pinned inode, not the mutable pathname.
        fd = os.open('/proc/self/fd/'+str(pin), os.O_RDONLY | os.O_NONBLOCK)
        try:
            actual = os.fstat(fd)
            if ((meta.st_dev, meta.st_ino) != (actual.st_dev, actual.st_ino)
                    or not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1):
                raise Refused('not_private_regular')
            yield fd
        finally:
            os.close(fd)
    finally:
        os.close(pin)


def clean_path(value):
    if not isinstance(value, str) or len(value) > 4096 or not value.startswith('/'):
        raise Refused('invalid_path')
    if any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value) or '..' in Path(value).parts:
        raise Refused('invalid_path')
    return os.path.normpath(value)


@contextmanager
def opened(path, directory=False):
    """Resolve every component with openat/O_NOFOLLOW; never follow symlinks."""
    path = clean_path(str(path))
    # Ancestors are lookup handles, not directory listings. Requesting READ_DIR
    # on / (or an unrelated parent) breaks a workspace-only Landlock allowlist.
    # O_PATH grants no read access; the final inode still needs a real read open.
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(path).parts[1:]
        for n, part in enumerate(parts):
            if n == len(parts)-1 and not directory:
                with regular_at(part, dir_fd=fd) as file_fd:
                    yield file_fd
                return
            flags = os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd); fd = nxt
        if not directory:
            raise Refused('not_regular')
        if not os.fstat(fd).st_mode & 0o444:
            raise PermissionError()
        # Reopen the pinned directory, so replacing a component after lookup
        # cannot redirect the listing. This open enforces DAC and Landlock.
        nxt = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        os.close(fd); fd = nxt
        yield fd
    finally:
        os.close(fd)


def timestamp(value):
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT[0-9:.]+(?:Z|[+-]\d\d:\d\d)', value):
        return value[:40]
    return None


def read_session(fd, provider, filename, limits, initial_stat=None, expected_session=None):
    st = initial_stat if initial_stat is not None else os.fstat(fd)
    if st.st_size > limits['file_bytes']:
        raise Refused('oversized')
    sid = cwd = version = created = updated = None
    cwds = []
    seen = 0; changed_cwd = False; sha = hashlib.sha256()
    # The caller owns fd. Buffered read-ahead is bounded at the raw IO layer.
    with io.BufferedReader(SnapshotReader(fd, st.st_size)) as stream:
        # A concurrently growing transcript cannot extend this scan's read budget.
        # fstat below rejects growth/truncation after reading the initial snapshot.
        while seen < st.st_size:
            line = stream.readline(min(limits['line_bytes']+1, st.st_size-seen))
            if not line:
                break
            seen += len(line); sha.update(line)
            if len(line) > limits['line_bytes']:
                raise Refused('oversized_line')
            if not line.endswith(b'\n'):
                raise Refused('partial_write')
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeError, RecursionError):
                raise Refused('corrupt_jsonl') from None
            if not isinstance(obj, dict):
                raise Refused('invalid_metadata')
            meta = obj.get('payload') if provider == 'codex' and obj.get('type') in ('session_meta', 'turn_context') else obj if provider=='claude' else None
            if provider=='copilot' and obj.get('type')=='session.start':
                data=obj.get('data',{});context=data.get('context',{})
                meta=dict(sessionId=data.get('sessionId'),cwd=context.get('cwd'),version=data.get('copilotVersion'))
            if isinstance(meta, dict):
                identity = meta.get('id') if provider == 'codex' and obj.get('type') == 'session_meta' else meta.get('sessionId') if provider in ('claude','copilot') else None
                if identity is not None:
                    if not isinstance(identity, str) or not UUID.fullmatch(identity) or sid and identity != sid:
                        raise Refused('identity_conflict')
                    sid = identity
                if meta.get('cwd') is not None:
                    value = clean_path(meta['cwd'])
                    if cwd and cwd != value:
                        changed_cwd = True
                    cwd = cwd or value
                    if value not in cwds:
                        if len(cwds) >= 32:
                            raise Refused('cwd_budget')
                        cwds.append(value)
                v = meta.get('cli_version' if provider == 'codex' else 'version')
                if isinstance(v, str) and VERSION.fullmatch(v):
                    version = v
            t = timestamp(obj.get('timestamp'))
            if t:
                created = created or t; updated = t
    after = os.fstat(fd)
    if (st.st_ino, st.st_size, st.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or seen != st.st_size:
        raise Refused('changed_during_scan')
    if not sid or not cwd:
        raise Refused('unsupported_format')
    codex_name = re.fullmatch(r'rollout-\d{4}-\d\d-\d\dT[0-9-]+-('+UUID.pattern+r')(?:_'+UUID.pattern+r')?\.jsonl', filename)
    if provider=='copilot' and (filename!='events.jsonl' or sid!=expected_session):raise Refused('identity_conflict')
    if provider == 'claude' and filename != sid+'.jsonl' or provider == 'codex' and (not codex_name or codex_name[1] != sid):
        raise Refused('identity_conflict')
    return dict(session_id=sid, cwd=cwd, cwds=cwds, cwd_changed=changed_cwd, version=version,
                created=created, updated=updated, sha256=sha.hexdigest())


def scan(root, provider, limits=None, session_ids=None, *, include_paths=False):
    limits = dict(LIMITS, **(limits or {}))
    coverage = dict(status='complete', entries=0, files=0, bytes=0, accepted=0, rejected={},
                    limits=limits)
    rows = []
    if provider not in ('codex', 'claude','copilot'):
        coverage['status'] = 'unsupported'
        return rows, coverage

    def reject(reason):
        coverage['rejected'][reason] = coverage['rejected'].get(reason, 0)+1

    def walk(fd, relative, depth):
        if depth > limits['depth']:
            reject('depth_budget'); return
        with os.scandir(fd) as entries:
            for ent in entries:
                if coverage['entries'] >= limits['entries']:
                    reject('entry_budget'); return
                coverage['entries'] += 1
                try:
                    # Read via directory descriptors; no TOCTOU path reopen.
                    st = ent.stat(follow_symlinks=False)
                    if stat.S_ISLNK(st.st_mode):
                        reject('symlink'); continue
                    if not st.st_mode & 0o444:
                        reject('permission_denied'); continue
                    rel = relative+[ent.name]
                    clean_path('/'+'/'.join(rel))  # Reject undecodable filenames before descent/serialization.
                    if stat.S_ISDIR(st.st_mode):
                        child = os.open(ent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, rel, depth+1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(st.st_mode) and ent.name.endswith('.jsonl'):
                        if session_ids is not None and not any(sid in ent.name for sid in session_ids):
                            continue  # Explicit operator filter: do not read unrelated transcripts.
                        if coverage['files'] >= limits['files']:
                            reject('file_budget'); return
                        if coverage['bytes']+st.st_size > limits['bytes']:
                            reject('byte_budget'); continue
                        coverage['files'] += 1
                        # Provider-specific main-session layout; no subagent transcripts.
                        shape = (provider=='copilot' and len(rel)==2 and UUID.fullmatch(rel[0]) and ent.name=='events.jsonl') or (provider == 'claude' and len(rel) == 2 and UUID.fullmatch(ent.name[:-6])) or (
                            provider == 'codex' and len(rel) == 4 and all(re.fullmatch(r'\d+', x) for x in rel[:3]) and ent.name.startswith('rollout-'))
                        if not shape:
                            reject('unsupported_layout'); continue
                        with regular_at(ent.name, dir_fd=fd) as child:
                            actual = os.fstat(child)
                            if not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
                                raise Refused('not_private_regular')
                            if coverage['bytes']+actual.st_size > limits['bytes']:
                                raise Refused('byte_budget')
                            coverage['bytes'] += actual.st_size
                            row = read_session(child, provider, ent.name, limits, initial_stat=actual,expected_session=rel[0] if provider=='copilot' else None)
                            row['file_key'] = hashlib.sha256('/'.join(rel).encode()).hexdigest()
                            if include_paths:
                                # Internal locator only. Never include this in an HTTP projection.
                                row['path'] = str(Path(root).joinpath(*rel))
                            rows.append(row); coverage['accepted'] += 1
                    elif stat.S_ISREG(st.st_mode):
                        reject('unscanned_file')
                    else:
                        reject('special_file')  # No data-stream open for FIFO/socket/device entries.
                except PermissionError:
                    reject('permission_denied')
                except Refused as exc:
                    reject(exc.reason)
                except OSError:
                    reject('unavailable')
    try:
        with opened(root, directory=True) as fd:
            walk(fd, [], 0)
        if coverage['rejected']:
            coverage['status'] = 'partial'
        elif not coverage['files']:
            coverage['status'] = 'not_found'
    except FileNotFoundError:
        coverage['status'] = 'not_found'
    except PermissionError:
        coverage['status'] = 'permission_denied'
    except (OSError, Refused):
        coverage['status'] = 'refused'
    return rows, coverage
