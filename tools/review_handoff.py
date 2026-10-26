#!/usr/bin/env python3
"""Read-only, offline candidate exchange. Integrity is NOT acceptance or release.

Standalone on Linux/Python 3.10+. Never imports candidate code, invokes git or a
model, reads .git, modifies source, or uploads anything. Run outside the pinned
candidate. Verify bundles without extracting or executing their contents.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import zipfile

MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 10000
MAX_NODES = 20000
MAX_DEPTH = 64
PRIVATE_PARTS = {'.devflower', '.claude', '.codex', '.copilot', '.ssh', '.aws', '.azure', '.gnupg',
                 'node_modules', '.venv', '__pycache__'}
PRIVATE_NAMES = {'auth.json', 'credentials.json', '.credentials.json', 'browser-token',
                 'operator.json', '.env', '.netrc', 'id_rsa', 'id_ed25519'}
SECRET = re.compile(rb'(?:-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'
                    rb'|\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b'
                    rb'|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b)')


class Refused(Exception):
    """Fail closed without printing source bytes or secret values."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def relative(name):
    if (not isinstance(name, str) or not name or len(name) > 4096 or '\\' in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise Refused('Invalid source-relative name')
    name.encode('utf-8', errors='strict')
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or path.as_posix() != name or any(p in ('.', '..', '.git') for p in path.parts):
        raise Refused('Unsafe source-relative name')
    if len(path.parts) > MAX_DEPTH:
        raise Refused('Source depth exceeds budget')
    return path


def privacy_check(name, data):
    parts = relative(name).parts
    if (set(parts) & PRIVATE_PARTS or parts[-1] in PRIVATE_NAMES
            or parts[-1].startswith('.env.') and parts[-1] not in ('.env.example', '.env.sample')
            or parts[-1].endswith(('.jsonl', '.sqlite', '.sqlite3', '.db', '.pem', '.key'))):
        raise Refused('Private/runtime path refused: ' + name)
    if SECRET.search(data):
        raise Refused('Possible credential in source; inspect locally: ' + name)
    # Mirrors source snapshot contracts; no Git filters or submodule semantics.
    if name == '.gitmodules' or name.endswith('.gitattributes') and b'filter=' in data:
        raise Refused('Submodules and content filters are unsupported')


@contextmanager
def directory(path):
    """Pin every absolute ancestor; never follow symlinks, including parents."""
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        raise Refused('An absolute path without .. is required')
    if not all(hasattr(os, x) for x in ('O_PATH', 'O_NOFOLLOW', 'O_DIRECTORY')):
        raise Refused('This implementation requires Linux O_PATH/dir_fd support')
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
    try:
        for part in p.parts[1:]:
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        readable = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            yield readable
        finally:
            os.close(readable)
    finally:
        os.close(fd)


def stamp(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)


def collect(root):
    """All regular source files, including ignored/untracked; only .git omitted."""
    files, total, nodes = {}, 0, 0

    def walk(fd, prefix='', depth=0):
        nonlocal total, nodes
        if depth > MAX_DEPTH:
            raise Refused('Source depth exceeds budget')
        for name in sorted(os.listdir(fd)):
            if name == '.git':
                continue
            nodes += 1
            if nodes > MAX_NODES:
                raise Refused('Source entry budget exceeded')
            rel = prefix + name
            privacy_check(rel, b'')
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if stamp(before) != stamp(os.fstat(child)):
                        raise Refused('Directory changed during capture')
                    walk(child, rel + '/', depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                if len(files) >= MAX_FILES or before.st_size > MAX_FILE or total + before.st_size > MAX_TOTAL:
                    raise Refused('Source size/count budget exceeded')
                f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(f, 'rb') as stream:
                    if stamp(before) != stamp(os.fstat(stream.fileno())):
                        raise Refused('File changed before capture')
                    raw = stream.read(MAX_FILE + 1)
                    after = os.fstat(stream.fileno())
                if (len(raw) != before.st_size or stamp(before) != stamp(after)
                        or stamp(before) != stamp(os.stat(name, dir_fd=fd, follow_symlinks=False))):
                    raise Refused('File changed during capture')
                privacy_check(rel, raw)
                files[rel] = ('100755' if before.st_mode & 0o111 else '100644', raw)
                total += len(raw)
            else:
                raise Refused('Symlink, hardlink or special file refused: ' + rel)
    with directory(root) as fd:
        walk(fd)
    if not files:
        raise Refused('Empty source candidate')
    return files


def manifest(files):
    return [{'path': path, 'mode': mode, 'size': len(data), 'sha256': sha256(data)}
            for path, (mode, data) in sorted(files.items())]


def git_tree(files):
    """Compute a SHA-1 Git tree directly; no repo config, hooks or index writes."""
    def oid(kind, data):
        return hashlib.sha1(kind + b' ' + str(len(data)).encode() + b'\0' + data).digest()
    root = {}
    for path, value in files.items():
        parts, tree = relative(path).parts, root
        for part in parts[:-1]:
            tree = tree.setdefault(part, {})
            if not isinstance(tree, dict):
                raise Refused('File/directory collision')
        if parts[-1] in tree:
            raise Refused('File/directory collision')
        tree[parts[-1]] = value
    def encode(tree):
        result = []
        for name, value in sorted(tree.items(), key=lambda item:
                item[0].encode('utf-8') + (b'/' if isinstance(item[1], dict) else b'')):
            mode, identity = ('40000', encode(value)) if isinstance(value, dict) else (value[0], oid(b'blob', value[1]))
            result.append(mode.encode() + b' ' + name.encode('utf-8') + b'\0' + identity)
        return oid(b'tree', b''.join(result))
    return encode(root).hex()


def receipt(files, expected, tree, count):
    if not re.fullmatch('[0-9a-f]{64}', expected or '') or not re.fullmatch('[0-9a-f]{40}', tree or ''):
        raise Refused('Independent candidate SHA-256 and Git tree SHA-1 pins are required')
    if type(count) is not int or not 1 <= count <= MAX_FILES:
        raise Refused('Independent file-count pin is required')
    actual, actual_tree = sha256(canonical(manifest(files))), git_tree(files)
    if (actual, actual_tree, len(files)) != (expected, tree, count):
        raise Refused('Candidate identity mismatch: sha256=' + actual + ' tree=' + actual_tree + ' files=' + str(len(files)))
    return dict(schema_version=1, purpose='source-review-only', candidate_sha256=actual,
                git_tree_sha1=actual_tree, files=count, integrity='VERIFIED', review='NOT_PERFORMED',
                authority=dict(execution=False, git_delivery=False, stable_promotion=False),
                privacy='Heuristic checks only; manual inspection required before sharing')


def export(root, output, expected, tree, count):
    root, output = Path(root), Path(output)
    if not root.is_absolute() or not output.is_absolute():
        raise Refused('Source and output must be absolute paths')
    if root == Path('/') or root == Path.home():
        raise Refused('Broad source root refused')
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise Refused('Output must be outside the candidate')
    if output.suffix != '.zip' or not output.name:
        raise Refused('Choose a new .zip output file')
    files = collect(root)
    info = receipt(files, expected, tree, count)
    # Compare a second independent snapshot, not a rehash of the captured bytes.
    if manifest(collect(root)) != manifest(files):
        raise Refused('Source changed between snapshots')
    payloads = {'manifest.json': canonical(manifest(files)), 'handoff.json': canonical(info)}
    # Metadata is a ZIP member too. The receiver applies MAX_FILE to every
    # member, so do not report a successful export it must later refuse.
    if any(len(raw) > MAX_FILE for raw in payloads.values()):
        raise Refused('Generated metadata exceeds member budget')
    payloads.update({'source/' + name: raw for name, (_, raw) in files.items()})
    with directory(output.parent) as parent:
        if os.stat('.', dir_fd=parent).st_mode & 0o002:
            raise Refused('Use a private output directory, not a world-writable directory')
        temp = '.review-handoff-' + secrets.token_hex(16) + '.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, raw in payloads.items():
                        entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        entry.create_system = 3
                        mode = int(files[name[7:]][0], 8) if name.startswith('source/') else 0o100600
                        entry.external_attr = mode << 16
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(entry, raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard link installs a completed file atomically without overwriting.
            os.link(temp, output.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.unlink(temp, dir_fd=parent)
    return info


def verify(bundle, expected, tree, count):
    """Never extract the archive or trust approval claims from its metadata."""
    bundle = Path(bundle)
    with directory(bundle.parent) as parent:
        fd = os.open(bundle.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_TOTAL + 8 * MAX_FILE:
                raise Refused('Bundle is not a bounded regular file')
            with zipfile.ZipFile(stream) as archive:
                names, files, metadata, total, source_total = set(), {}, {}, 0, 0
                members = archive.infolist()
                if len({i.filename for i in members}) != len(members):
                    raise Refused('Duplicate bundle member')
                if len(members) > MAX_FILES + 2:
                    raise Refused('Too many bundle members')
                for entry in members:
                    name = entry.filename
                    relative(name)
                    if (name in names or entry.flag_bits & 1 or entry.is_dir()
                            or entry.create_system != 3 or entry.file_size > MAX_FILE
                            or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                        raise Refused('Invalid/duplicate/encrypted bundle member')
                    names.add(name)
                    mode = entry.external_attr >> 16
                    if mode not in (0o100644, 0o100755, 0o100600):
                        raise Refused('Unsupported bundle file mode')
                    total += entry.file_size
                    if total > MAX_TOTAL + 2 * MAX_FILE:
                        raise Refused('Bundle expansion exceeds budget')
                    with archive.open(entry) as item:
                        data = item.read(MAX_FILE + 1)
                    if len(data) != entry.file_size:
                        raise Refused('Truncated or oversized member')
                    if name in ('manifest.json', 'handoff.json'):
                        metadata[name] = data
                    elif name.startswith('source/') and mode in (0o100644, 0o100755):
                        path = name[7:]
                        source_total += len(data)
                        if source_total > MAX_TOTAL:
                            raise Refused('Source expansion exceeds budget')
                        privacy_check(path, data)
                        files[path] = (format(mode, 'o'), data)
                    else:
                        raise Refused('Unexpected bundle member')
            if stamp(before) != stamp(os.fstat(stream.fileno())):
                raise Refused('Bundle changed during verification')
    info = receipt(files, expected, tree, count)
    if (metadata.get('manifest.json') != canonical(manifest(files))
            or metadata.get('handoff.json') != canonical(info)):
        raise Refused('Manifest or handoff receipt differs from verified bytes')
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('export', 'verify'):
        p = sub.add_parser(command)
        p.add_argument('--expect-sha256', required=True)
        p.add_argument('--expect-tree', required=True)
        p.add_argument('--expect-files', required=True, type=int)
        if command == 'export':
            p.add_argument('--source', required=True)
            p.add_argument('--output', required=True)
        else:
            p.add_argument('--bundle', required=True)
    args = parser.parse_args(argv)
    try:
        pins = (args.expect_sha256, args.expect_tree, args.expect_files)
        value = export(args.source, args.output, *pins) if args.command == 'export' else verify(args.bundle, *pins)
        print(canonical(value).decode())
        return 0
    except (Refused, OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        print('BLOCKED: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
