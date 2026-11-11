"""Explicit trees and deterministic commit intent; local bare remotes only in v0.2-A.

No checkout/index mutation, hooks bypass, merge, force, tag or remote rollback.
Records are generated after confirmed source delivery and never contain code.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import tempfile
import time
from .common import Blocked, canonical, clean_env, digest, git, identity, manifest, tree_hash


def remote_tip(remote, branch):
    # The product cannot point at business repositories without a separately
    # reviewed transport/credential backend. Fixtures must use local bare repos.
    remote = Path(remote)
    if not remote.is_absolute() or not remote.is_dir() or git(remote, 'rev-parse', '--is-bare-repository') != 'true':
        raise Blocked('only explicitly bound local bare remotes supported')
    lines = git(remote, 'ls-remote', str(remote), 'refs/heads/' + branch).splitlines()
    return next((x.split()[0] for x in lines if x.split()[1] == 'refs/heads/' + branch), None)


def checks(target):
    root = Path(target['root']).resolve()
    actual = identity(root)
    if actual['branch'] != target['branch'] or target['branch'] in ('main', 'master'):
        raise Blocked('delivery branch mismatch/protected')
    if target['kind'] == 'records' and not target['branch'].startswith('project/'):
        raise Blocked('workflow delivery requires project branch')
    if target['kind'] == 'source' and not target['branch'].startswith(('flow/', 'work/', 'feature/', 'fix/')):
        raise Blocked('source delivery requires feature branch')
    # Existing changes are never incorporated implicitly, even ignored files.
    if git(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise Blocked('delivery target has existing changes')
    hooks = git(root, 'config', '--get', 'core.hooksPath') if config_exists(root, 'core.hooksPath') else ''
    if hooks:
        raise Blocked('custom hooks require reviewed strategy')
    if any(p.is_file() and not p.name.endswith('.sample') and os.access(p, os.X_OK)
           for p in (Path(actual['common_dir']) / 'hooks').glob('*')):
        raise Blocked('active hooks require reviewed strategy')
    if any(p.name == '.gitattributes' and b'filter=' in p.read_bytes() for p in root.rglob('.gitattributes')):
        raise Blocked('clean filters unsupported')
    configured = git(root, 'remote', 'get-url', 'origin')
    if configured != target['remote']:
        raise Blocked('wrong remote repository identity')
    remote_tip(target['remote'], target['branch'])
    return actual


def config_exists(root, key):
    return subprocess.run(['git', '-C', str(root), 'config', '--get', key],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean_env()).returncode == 0


def make_tree(root, candidate, *, pass_fds=(), git_dir=None):
    """Private index, explicit byte blobs and mode: filters cannot transform source."""
    inherited = {'pass_fds': pass_fds} if pass_fds else {}
    repository = ('--git-dir='+git_dir,) if git_dir is not None else ()
    with tempfile.TemporaryDirectory(prefix='devflower-index-') as tmp:
        env = dict(clean_env(), GIT_INDEX_FILE=str(Path(tmp) / 'index'))
        git(root, *repository, 'read-tree', '--empty', env=env, **inherited)
        for e in manifest(candidate):
            p = Path(candidate) / e['path']
            content = os.fsencode(os.readlink(p)) if e['mode'] == '120000' else p.read_bytes()
            blob = git(root, *repository, 'hash-object', '-w', '--stdin', data=content, **inherited)
            record = f"{e['mode']} {blob}\t{e['path']}".encode() + b'\0'
            git(root, *repository, 'update-index', '-z', '--index-info', data=record, env=env, **inherited)
        return git(root, *repository, 'write-tree', env=env, **inherited)


def committed_manifest_hash(root, tree, *, pass_fds=(), git_dir=None):
    repository = ('--git-dir='+git_dir,) if git_dir is not None else ()
    inherited = {'pass_fds': pass_fds} if pass_fds else {}

    def raw(*args):
        p = subprocess.run(['git', '-C', str(root), *repository, *args], capture_output=True,
                           env=clean_env(), timeout=30, **inherited)
        if p.returncode:
            raise Blocked('cannot verify prospective commit tree')
        return p.stdout
    entries = []
    for record in raw('ls-tree', '-r', '-z', tree).split(b'\0'):
        if not record:
            continue
        meta, path = record.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        if kind != 'blob' or mode not in ('100644', '100755', '120000'):
            raise Blocked('unsupported prospective tree node')
        data = raw('cat-file', 'blob', blob)
        entries.append({'path': os.fsdecode(path), 'mode': mode, 'size': len(data), 'sha256': digest(data)})
    return digest(canonical(sorted(entries, key=lambda x: x['path'])))


def records_tree(root, path, payload):
    """Only the generated receipt changes; ignored local files never enter Git."""
    from .common import relative
    relative(path)
    if not path.startswith('project/') or not path.endswith('.json'):
        raise Blocked('records path must be project JSON receipt')
    base = Path(root).resolve()
    dest = base / path
    for part in (dest, *dest.parents):
        if part == base:
            break
        if part.is_symlink():
            raise Blocked('symlink in generated record receipt path')
    with tempfile.TemporaryDirectory(prefix='devflower-record-index-') as tmp:
        env = dict(clean_env(), GIT_INDEX_FILE=str(Path(tmp) / 'index'))
        git(root, 'read-tree', 'HEAD', env=env)
        blob = git(root, 'hash-object', '-w', '--stdin', data=canonical(payload))
        git(root, 'update-index', '-z', '--index-info', data=(f'100644 {blob}\t{path}').encode() + b'\0', env=env)
        return git(root, 'write-tree', env=env)


def commit(store, run, target, tree):
    root = target['root']
    prior = [o for o in store.operations(run) if o['kind'] == 'COMMIT' and o['data']['root'] == root]
    if prior:
        op, data = prior[-1]['id'], prior[-1]['data']
        if data['tree'] != tree:
            raise Blocked('commit intent tree changed')
    else:
        data = {'root': root, 'tree': tree, 'parent': git(root, 'rev-parse', 'HEAD'),
                'branch': target['branch'], 'timestamp': int(time.time()),
                'local_ref': 'refs/devflower/commits/' + digest(canonical([run, root, target['branch']])),
                'name': git(root, 'config', 'user.name'), 'email': git(root, 'config', 'user.email')}
        op = store.intent(run, 'COMMIT', data)
    stamp = str(data['timestamp']) + ' +0000'
    env = dict(clean_env(), GIT_AUTHOR_NAME=data['name'], GIT_AUTHOR_EMAIL=data['email'],
               GIT_COMMITTER_NAME=data['name'], GIT_COMMITTER_EMAIL=data['email'],
               GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    message = f'feat(devflower): deliver reviewed candidate\n\nDevFlower-Operation: {op}\n'
    sha = git(root, 'commit-tree', tree, '-p', data['parent'], env=env, data=message.encode())
    if git(root, 'rev-parse', sha + '^{tree}') != tree or git(root, 'rev-parse', sha + '^') != data['parent']:
        raise Blocked('actual commit tree/parent mismatch')
    if git(root, 'rev-parse', 'HEAD') != data['parent']:
        raise Blocked('target changed before delivery')
    # Pin before acknowledging. The task branch/index/worktree remain untouched,
    # while a real ref makes commit-only delivery survive reflog expiry and GC.
    ref = data['local_ref']
    def observed():
        symbolic = subprocess.run(['git', '-C', root, 'symbolic-ref', '--quiet', ref],
                                  capture_output=True, env=clean_env())
        if symbolic.returncode == 0:
            raise Blocked('durable delivery reference must be direct')
        p = subprocess.run(['git', '-C', root, 'rev-parse', '--verify', '--quiet', ref],
                           capture_output=True, env=clean_env())
        if p.returncode not in (0, 1):
            raise Blocked('cannot inspect durable delivery reference')
        return p.stdout.decode().strip() if p.returncode == 0 else None
    current = observed()
    if current is None:
        p = subprocess.run(['git', '-C', root, 'update-ref', '--no-deref', ref, sha, '0' * len(sha)],
                           capture_output=True, env=clean_env())
        if p.returncode and observed() != sha:
            raise Blocked('durable delivery reference could not be pinned')
    elif current != sha:
        raise Blocked('durable delivery reference changed; never overwrite it')
    if observed() != sha:
        raise Blocked('durable delivery reference readback mismatch')
    store.finish(op, 'COMMITTED', commit=sha, local_ref=ref)
    return sha


def push(store, run, target, sha, push_runner=None):
    prior = [o for o in store.operations(run) if o['kind'] == 'PUSH' and o['data']['root'] == target['root']]
    if prior:
        op, data = prior[-1]['id'], prior[-1]['data']
        if data['commit'] != sha:
            raise Blocked('push intent changed')
    else:
        data = dict(target, commit=sha)
        op = store.intent(run, 'PUSH', data)
    tip = remote_tip(target['remote'], target['branch'])
    if tip == sha:
        store.finish(op, 'PUSHED', commit=sha, readback=tip)
        return sha
    if tip != target['expected_tip']:
        store.finish(op, 'REMOTE_CONFLICT', observed_tip=tip)
        raise Blocked('remote advanced unexpectedly')
    if tip and subprocess.run(['git', '-C', target['root'], 'merge-base', '--is-ancestor', tip, sha], env=clean_env()).returncode:
        raise Blocked('not fast forward')
    argv = ['git', '-C', target['root'], 'push', target['remote'], sha + ':refs/heads/' + target['branch']]
    try:
        if push_runner:
            push_runner(argv)
        else:
            # A local receive hook is executable project code too. It must not
            # inherit the controller's filesystem authority or credentials.
            from .process import execute
            with tempfile.TemporaryDirectory(prefix='devflower-push-') as tmp:
                execute(argv, target['root'], Path(tmp) / 'home',
                        read=[target['root'], target['remote']], write=[target['remote']], timeout=30,
                        store=store, run=run)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Unknown response is resolved by remote readback, never blind retry.
    tip = remote_tip(target['remote'], target['branch'])
    if tip != sha:
        store.finish(op, 'PUSH_UNKNOWN', observed_tip=tip)
        raise Blocked('push unconfirmed; inspect remote before retry')
    store.finish(op, 'PUSHED', commit=sha, readback=tip)
    return sha


def deliver(store, run, candidate, targets, do_push, expected_candidate=None):
    if not targets or len(targets) > 2 or targets[0]['kind'] != 'source' or len(targets) == 2 and targets[1]['kind'] != 'records':
        raise Blocked('source then optional records required')
    receipts = []
    def check_cancelled():
        if store.get(run)['data'].get('cancel_requested'):
            raise Blocked('run cancelled; further delivery refused')
    for target in targets:
        pending_receipt = None
        try:
            check_cancelled()
            checks(target)
            if target['kind'] == 'source':
                tree = make_tree(target['root'], candidate)
                expected = expected_candidate or tree_hash(candidate)
                if committed_manifest_hash(target['root'], tree) != expected or tree_hash(candidate) != expected:
                    raise Blocked('prospective commit does not match reviewed candidate')
            else:
                tree = records_tree(target['root'], target['receipt_path'], {'run_id': run, 'source': receipts[0]})
            check_cancelled()
            sha = commit(store, run, target, tree)
            operation = next(o for o in reversed(store.operations(run))
                             if o['kind'] == 'COMMIT' and o['data']['root'] == target['root'])
            pending_receipt = {'kind': target['kind'], 'commit': sha, 'tree': tree,
                               'local_ref': operation['data']['local_ref'], 'pushed': False}
            check_cancelled()
            if do_push:
                push(store, run, target, sha)
                pending_receipt['pushed'] = True
            receipts.append(pending_receipt)
            pending_receipt = None
            check_cancelled()
        except Blocked as exc:
            cancelled = store.get(run)['data'].get('cancel_requested', False)
            if cancelled and pending_receipt is not None:
                receipts.append(pending_receipt)
            store.transition(run, 'CANCELLED' if cancelled else 'PARTIAL_DELIVERY' if receipts else 'BLOCKED', receipts=receipts, reason=str(exc))
            raise
    store.transition(run, 'COMPLETED' if do_push else 'LOCAL_COMMITTED', receipts=receipts)
    return receipts
