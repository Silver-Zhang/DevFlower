"""Canonical identities and complete byte-preserving source snapshots."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from urllib.parse import urlparse


class Blocked(Exception):
    """A required precondition has not been proved."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def controller_hash():
    base = Path(__file__).parent
    root = base.parents[1]
    paths = list(base.glob('*.py')) + [base.parent / 'devflower.py'] + list((root / 'docs/design/v0.2-a/schemas').glob('*.json'))
    return digest(canonical({str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(paths)}))


def clean_env(home=None):
    # Never inherit SSH sockets, GIT_*, provider tokens, loader injection or Python paths.
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}
    if home:
        env.update(HOME=str(home), TMPDIR=str(Path(home) / 'tmp'))
    for key in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy'):
        value = os.environ.get(key)
        if value and (key.lower() == 'no_proxy' or not urlparse(value).username and not urlparse(value).password):
            env[key] = value
    return env


def git(root, *args, env=None, data=None):
    result = subprocess.run(['git', '-C', str(root), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env or clean_env(), timeout=30)
    if result.returncode:
        raise Blocked('git failed: ' + args[0])
    return result.stdout.decode().strip()


def identity(root):
    root = Path(root).resolve()
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise Blocked('not repository root')
    common = Path(git(root, 'rev-parse', '--git-common-dir'))
    common = common.resolve() if common.is_absolute() else (root / common).resolve()
    return {'root': str(root), 'head': git(root, 'rev-parse', 'HEAD'), 'common_dir': str(common),
            'branch': git(root, 'symbolic-ref', '--short', 'HEAD'),
            'git_dir': str(Path(git(root, 'rev-parse', '--absolute-git-dir')).resolve())}


def relative(value):
    p = Path(value)
    if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] == '.git':
        raise Blocked('unsafe relative path')
    return p.as_posix()


def included(path, excludes):
    return not any(path == x or path.startswith(x.rstrip('/') + '/') for x in excludes)


def manifest(root, excludes=()):
    """Do not follow symlinks; includes ignored and untracked files unless approved excluded."""
    root = Path(root)
    out = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != '.git' and included(
            (Path(folder) / d).relative_to(root).as_posix(), excludes))
        links = [d for d in dirs if (Path(folder) / d).is_symlink()]
        dirs[:] = [d for d in dirs if d not in links]
        for name in sorted(files + links):
            if name == '.git':
                continue
            p = Path(folder) / name
            rel = p.relative_to(root).as_posix()
            if not included(rel, excludes):
                continue
            s = p.lstat()
            if stat.S_ISLNK(s.st_mode):
                data, mode = os.fsencode(os.readlink(p)), '120000'
            elif stat.S_ISREG(s.st_mode):
                fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    before = os.fstat(stream.fileno())
                    data = stream.read()
                    after = os.fstat(stream.fileno())
                stable = lambda v: (v.st_ino, v.st_mtime_ns, v.st_size, v.st_mode)
                if stable(s) != stable(after) or stable(before) != stable(after):
                    raise Blocked('source changed during snapshot')
                mode = '100755' if s.st_mode & 0o111 else '100644'
            else:
                raise Blocked('unsupported source node: ' + rel)
            if rel == '.gitmodules' or (rel.endswith('.gitattributes') and b'filter=' in data):
                raise Blocked('submodule/LFS/clean filters not supported')
            out.append({'path': rel, 'mode': mode, 'size': len(data), 'sha256': digest(data)})
    return sorted(out, key=lambda x: x['path'])


def tree_hash(root, excludes=()):
    return digest(canonical(manifest(root, excludes)))


def workspace_metadata(root, *, check_git=False):
    """Reserved local project data, never source or Agent input.

    This is deliberately NOT an exclusion in manifest/tree_hash: whole-program
    review and release fingerprints must still include every candidate file.
    """
    path = Path(root) / '.devflower'
    if not os.path.lexists(path): return None
    if path.is_symlink() or not path.is_dir(): raise Blocked('项目 .devflower 必须是普通目录')
    marker = path / 'project.json'
    fd = os.open(marker, os.O_PATH | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > 4096:
            raise Blocked('项目元数据标识无效')
        with open('/proc/self/fd/' + str(fd), 'rb') as stream: value = json.loads(stream.read(4097))
    finally: os.close(fd)
    if value != dict(version=1, kind='devflower-project', authority=False):
        raise Blocked('未识别的 .devflower 不会从源码身份中排除')
    # Reserved data cannot hide tracked executable source from the source hash.
    if check_git and (Path(root) / '.git').exists() and git(root, 'ls-files', '--', '.devflower'):
        raise Blocked('.devflower 是本地记录保留区，不能包含已跟踪的源码')
    return str(path)


def workspace_hash(root):
    return tree_hash(root, excludes=('.devflower',) if workspace_metadata(root) else ())


def snapshot(source, dest, excludes=(), baseline=None):
    source, dest = Path(source), Path(dest)
    if workspace_metadata(source) and included('.devflower', excludes):
        raise Blocked('源码快照须明确排除 .devflower 项目记录；不能将对话历史当源码发送')
    entries = manifest(source, excludes)
    dest.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        src, dst = source / entry['path'], dest / entry['path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        if entry['mode'] == '120000':
            dst.symlink_to(os.readlink(src))
        else:
            dst.write_bytes(src.read_bytes())
            dst.chmod(0o755 if entry['mode'] == '100755' else 0o644)
    if entries != manifest(source, excludes) or entries != manifest(dest):
        raise Blocked('snapshot content changed')
    return {'sha256': digest(canonical(entries)), 'entries': entries,
            'deleted': sorted({x['path'] for x in baseline or []} - {x['path'] for x in entries})}


def scope_check(base, current, allowed):
    a, b = ({x['path']: x for x in entries} for entries in (base, current))
    changed = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
    if any(not any(p == x or p.startswith(x.rstrip('/') + '/') for x in allowed) for p in changed):
        raise Blocked('source changes outside approved scope')
    return changed
