"""Publish an accepted candidate to an explicitly registered local bare Git repository."""
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from orchestration.common import Blocked, canonical, clean_env, digest, git
from orchestration.delivery import committed_manifest_hash, make_tree
from studio.graph import Invalid
from studio.server import Conflict
from studio.session_sources import opened

from .git_workspace import OID, bind_workspace, git_read
from .local_delivery import delivery_scope, package
from .project_files import mount_id, protected_roots
from .session_identity import StorageRootIdentity, session_path, verified_directory
from .session_lease import LOCAL_FILESYSTEMS, mount_table, physical_path
from .session_operations import operation_id

KIND = 'local-git-delivery'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}')
CONFIG_KEYS = {'core.repositoryformatversion', 'core.filemode', 'core.bare', 'core.logallrefupdates',
               'extensions.objectformat'}


def bare_read(path, args, **options):
    return git_read(path, ['--git-dir=.', *args], **options)


def target_facts(path):
    identities = []
    for directory in (path, path/'objects', path/'refs'):
        with verified_directory(directory) as fd:
            info = os.fstat(fd)
            identity = StorageRootIdentity.from_fd(fd)
        if info.st_uid != os.geteuid() or directory == path and stat.S_IMODE(info.st_mode) != 0o700:
            raise Blocked('交付 Git 仓库需要本机所有者及私有目录权限 0700。')
        identities.append(identity.fields())
    root_mount = identities[0][0]
    if (len({identity[0] for identity in identities}) != 1
            or mount_table().get(root_mount, (None, None, None))[2] not in LOCAL_FILESYSTEMS
            or os.path.lexists(path/'objects/info/alternates') or os.path.lexists(path/'objects/info/http-alternates')):
        raise Blocked('交付 Git 仓库不能使用外部对象目录或跨挂载。')
    def unreadable(error):
        raise Blocked('交付 Git 仓库无法完整检查。') from error

    remaining = 24000
    for folder, directories, files in os.walk(path, followlinks=False, onerror=unreadable):
        for name in (*directories, *files):
            remaining -= 1
            if remaining < 0:
                raise Blocked('交付 Git 仓库超过本机安全扫描上限。')
            fd = os.open(Path(folder)/name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                meta = os.fstat(fd)
                if (meta.st_uid != os.geteuid() or mount_id(fd) != root_mount
                        or not (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1)):
                    raise Blocked('交付 Git 仓库含非私有文件、符号链接或外部挂载。')
            finally:
                os.close(fd)
    with opened(path/'config') as config:
        raw = os.read(config, 16001)
        if len(raw) > 16000 or os.fstat(config).st_nlink != 1:
            raise Blocked('交付 Git 配置无法核验。')
    keys = bare_read(path, ['config', '--no-includes', '--file', str(path/'config'), '--name-only', '--list']).splitlines()
    if (not set(keys) <= CONFIG_KEYS or bare_read(path, ['rev-parse', '--is-bare-repository']).strip() != 'true'
            or bare_read(path, ['rev-parse', '--absolute-git-dir']).strip() != str(path)):
        raise Blocked('交付目标需要独立的本机裸 Git 仓库。')
    object_format = bare_read(path, ['rev-parse', '--show-object-format']).strip()
    if object_format not in ('sha1', 'sha256'):
        raise Blocked('交付 Git 对象格式无法核验。')
    return {'identity': identities, 'config_sha256': digest(raw), 'oid_length': 40 if object_format == 'sha1' else 64}


def require_separate(path, protected):
    table = mount_table()
    here_id = StorageRootIdentity.capture(path)
    here = physical_path(path, here_id, table)
    for root in protected:
        if path.is_relative_to(root) or root.is_relative_to(path):
            raise Invalid('交付仓库须与工作区、原生账户和产品状态分开。')
        if root.is_dir():
            root = root.resolve(strict=True)
            other_id = StorageRootIdentity.capture(root)
            if here_id.device == other_id.device:
                other = physical_path(root, other_id, table)
                if here.is_relative_to(other) or other.is_relative_to(here):
                    raise Invalid('交付仓库不能使用其他授权位置的别名。')


def direct_tip(path, ref, *, fd=None):
    if bare_read(path, ['symbolic-ref', '--quiet', ref], optional=True, fd=fd) is not None:
        raise Conflict('交付分支需要直接 Git 引用。')
    tip = bare_read(path, ['rev-parse', '--verify', '--quiet', ref], optional=True, fd=fd)
    return tip.strip() if tip is not None else None


def materialize(blob, folder):
    with ZipFile(BytesIO(blob)) as archive:
        manifest = json.loads(archive.read('devflower-delivery.json'))
        for entry in manifest['entries']:
            destination = folder / entry['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read('candidate/' + entry['path']))
            destination.chmod(int(entry['mode'], 8) & 0o777)
    return manifest


class GitDelivery:
    def __init__(self, platform, options, *, native_homes=()):
        self.platform, self.store = platform, platform.store
        self.targets = {}
        if len(options) > 8:
            raise Invalid('最多登记八个本机 Git 交付目标。')
        protected = [*protected_roots(self.store), *self.store.roots, *map(Path, native_homes)]
        for option in options:
            name, separator, value = option.partition('=')
            if not separator or not NAME.fullmatch(name) or name in self.targets:
                raise Invalid('交付目标需要唯一的名称与裸 Git 仓库路径。')
            path = session_path(value)
            require_separate(path, protected)
            facts = target_facts(path)
            if any(old['identity'][0][1:] == facts['identity'][0][1:] for old in self.targets.values()):
                raise Invalid('同一裸 Git 仓库只能登记一次。')
            self.targets[name] = dict(facts, path=str(path))
            protected.append(path)

    def target(self, name):
        row = self.targets.get(name) if isinstance(name, str) else None
        if row is None:
            raise Invalid('请选择已登记的本机 Git 交付目标。')
        path = Path(row['path'])
        if dict(target_facts(path), path=str(path)) != row:
            raise Conflict('本机 Git 交付目标已变化。')
        return path, row

    def read(self, workspace, intent_key):
        self.store.workspace(workspace)
        key = operation_id(workspace, intent_key)
        try:
            row = self.store.db.get(KIND, key)
        except Invalid:
            return {'state': 'UNREGISTERED'}
        if row['workspace'] != workspace:
            raise Conflict('交付意图不属于当前工作区。')
        if row['state'] == 'PUBLISHING':
            try:
                path, target = self.target(row['target'])
                with verified_directory(path) as directory:
                    anchored = Path(f'/proc/self/fd/{directory}')
                    if (target == row['target_facts']
                            and StorageRootIdentity.from_fd(directory).fields() == target['identity'][0]
                            and direct_tip(anchored, row['ref'], fd=directory) == row['commit']):
                        self.target(row['target'])
                        row = self.store.db.change(KIND, key, {'state': 'COMPLETED'})
            except (Blocked, OSError, ValueError, subprocess.SubprocessError):
                pass
        state = row['state'] if row['state'] in ('COMPLETED', 'FAILED_BEFORE_PUBLISH') else 'RECOVERY_REQUIRED'
        return dict(state=state, **{key: row[key] for key in ('run', 'target', 'ref', 'candidate_sha256', 'commit')})

    def publish(self, data):
        fields = {'workspace', 'run', 'activation', 'candidate_sha256', 'target', 'intent_key', 'authorization'}
        if set(data) != fields or data['authorization'] != 'confirm-local-git-delivery':
            raise Invalid('发布需要确认候选和已登记的本机 Git 目标。')
        workspace, run, intent = (data[key] for key in ('workspace', 'run', 'intent_key'))
        self.store.workspace(workspace)
        key = operation_id(workspace, intent)
        request_sha = digest(canonical(data))
        try:
            prior = self.store.db.get(KIND, key)
        except Invalid:
            prior = None
        if prior:
            if prior['request_sha256'] != request_sha:
                raise Conflict('交付意图已绑定不同的候选或目标。')
            return self.read(workspace, intent)
        path, target = self.target(data['target'])
        _, scope, status = delivery_scope(self.platform, workspace, run)
        archive = package(self.platform, dict(workspace=workspace, run=run, activation=data['activation'],
            candidate_sha256=data['candidate_sha256'], authorization='confirm-local-candidate-download'))
        parent = status['gate']['git_head']
        if not OID.fullmatch(parent) or len(parent) != target['oid_length']:
            raise Conflict('交付目标的 Git 对象格式与候选不同。')
        bare_read(path, ['cat-file', '-e', parent+'^{commit}'])
        ref = 'refs/heads/work/devflower-' + run
        if direct_tip(path, ref) is not None:
            raise Conflict('交付分支已经存在；不能覆盖。')
        row = dict(id=key, workspace=workspace, run=run, target=data['target'], target_facts=target, ref=ref,
                   candidate_sha256=data['candidate_sha256'], request_sha256=request_sha, commit=None, state='PREPARING')
        with self.store.db.tx() as db:
            if db.execute('SELECT 1 FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone():
                raise Conflict('交付意图已登记，请核对原结果。')
            db.execute('INSERT INTO records VALUES(?,?,?)', (KIND, key, canonical(row).decode()))
        try:
            with (verified_directory(path) as directory,
                  TemporaryDirectory(prefix='delivery-git-', dir=self.store.state/'flow-artifacts'/run) as scratch):
                if StorageRootIdentity.from_fd(directory).fields() != target['identity'][0]:
                    raise Conflict('本机 Git 交付目标目录已替换。')
                anchored = Path(f'/proc/self/fd/{directory}')
                candidate = Path(scratch)
                manifest = materialize(archive, candidate)
                self.target(data['target'])
                tree = make_tree(anchored, candidate, pass_fds=(directory,), git_dir='.')
                if committed_manifest_hash(anchored, tree, pass_fds=(directory,), git_dir='.') != manifest['candidate_sha256']:
                    raise Conflict('交付树与独立审核的候选不同。')
                stamp = str(int(self.store.db.get('run', run)['created_at'])) + ' +0000'
                env = dict(clean_env(), GIT_CONFIG_GLOBAL='/dev/null', GIT_AUTHOR_NAME='DevFlower',
                    GIT_AUTHOR_EMAIL='devflower@localhost', GIT_COMMITTER_NAME='DevFlower',
                    GIT_COMMITTER_EMAIL='devflower@localhost', GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
                message = f'DevFlower verified candidate\n\nCandidate SHA-256: {manifest["candidate_sha256"]}\n'
                commit = git(anchored, '--git-dir=.', '-c', 'commit.gpgSign=false', 'commit-tree', tree, '-p', parent,
                             env=env, data=message.encode(), pass_fds=(directory,))
                if not OID.fullmatch(commit) or bare_read(anchored, ['rev-parse', commit+'^'], fd=directory).strip() != parent:
                    raise Conflict('交付提交的父提交无法核验。')
                _, current, checked = delivery_scope(self.platform, workspace, run)
                if current != scope or checked != status or bind_workspace(self.store, workspace, require_origin=False) != scope:
                    raise Conflict('候选或独立 Gate 在交付前变化。')
                self.target(data['target'])
                self.store.db.change(KIND, key, {'commit': commit, 'state': 'PUBLISHING'}, states={'PREPARING'})
                bare_read(anchored, ['update-ref', '--no-deref', ref, commit, '0' * len(commit)], fd=directory)
        except (OSError, ValueError, Blocked, subprocess.SubprocessError):
            current = self.store.db.get(KIND, key)
            if current['state'] == 'PREPARING':
                self.store.db.change(KIND, key, {'state': 'FAILED_BEFORE_PUBLISH'}, states={'PREPARING'})
                raise
        return self.read(workspace, intent)
