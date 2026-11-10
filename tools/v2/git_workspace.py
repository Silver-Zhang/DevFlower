"""Read-only Git binding for the exact registered workspace root."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess

from orchestration.common import Blocked, canonical, digest
from studio.session_sources import Refused, opened, regular_at
from studio.workspace_read_policy import stamp
from .session_identity import StorageRootIdentity

GIT = '/usr/bin/git'
OID = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
ENV = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C', GIT_CONFIG_NOSYSTEM='1',
           GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null', GIT_TERMINAL_PROMPT='0',
           GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')


@dataclass(frozen=True, slots=True)
class GitWorkspace:
    workspace: str
    origin: str
    head: str | None
    branch: str | None
    identity: str


def read_metadata(fd, limit):
    before = os.fstat(fd)
    if before.st_size > limit:
        raise Blocked('Git 元数据超过读取上限。')
    raw = os.pread(fd, limit+1, 0)
    if len(raw) != before.st_size or stamp(before) != stamp(os.fstat(fd)):
        raise Blocked('Git 元数据在读取期间变化。')
    return raw


def text_metadata(dirfd, name):
    with regular_at(name, dirfd) as fd:
        raw = read_metadata(fd, 4096)
        value = raw.decode().removesuffix('\n')
        if not value or '\n' in value or '\r' in value or '\0' in value:
            raise Blocked('Git 工作树标识无效。')
        return value


def link_path(value, base):
    return Path(os.path.abspath(base / value))


def git_read(root, args, *, fd=None, optional=False):
    cmd = [GIT, '--no-optional-locks', '-C', str(root), '-c', 'core.hooksPath=/dev/null',
           '-c', 'core.fsmonitor=false', *args]
    result = subprocess.run(cmd, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, pass_fds=() if fd is None else (fd,))
    if optional and result.returncode in (1, 128) and not result.stdout:
        return None
    if result.returncode or len(result.stdout) > 4096:
        raise Blocked('无法读取当前工作空间的 Git 身份。')
    return result.stdout.decode()


def origin_values(root, fd):
    value = git_read(root, ['config','--no-includes','--file',f'/proc/self/fd/{fd}','-z','--get-all',
                            'remote.origin.url'], fd=fd, optional=True)
    if value is None:
        return []
    if not value.endswith('\0'):
        raise Blocked('Git origin 记录无效。')
    return value[:-1].split('\0')


def bind_workspace(store, workspace):
    info = store.workspace(workspace)
    root = Path(info['path'])
    try:
        with opened(root, directory=True) as rootfd:
            root_identity = StorageRootIdentity.from_fd(rootfd)
            if digest(canonical([str(root), root_identity.device, root_identity.inode])) != workspace:
                raise Blocked('工作空间根目录身份已变化。')
            marker = os.stat('.git', dir_fd=rootfd, follow_symlinks=False)
            linked = stat.S_ISREG(marker.st_mode)
            link = text_metadata(rootfd, '.git') if linked else None
            if linked:
                if not link.startswith('gitdir: '):
                    raise Blocked('Git 工作树标识无效。')
                gitdir = link_path(link.removeprefix('gitdir: '), root)
                common = gitdir.parent.parent
                if gitdir.parent.name != 'worktrees' or common.name != '.git':
                    raise Blocked('只支持本机 Git 注册的工作树。')
            elif stat.S_ISDIR(marker.st_mode):
                gitdir = common = root / '.git'
            else:
                raise Blocked('Git 元数据不能是符号链接。')

            with opened(gitdir, directory=True) as gitfd, opened(common, directory=True) as commonfd:
                common_identity = StorageRootIdentity.from_fd(commonfd)
                git_identity = StorageRootIdentity.from_fd(gitfd)
                if linked and (link_path(text_metadata(gitfd, 'gitdir'), gitdir) != root / '.git'
                               or text_metadata(gitfd, 'commondir') != '../..'):
                    raise Blocked('Git 工作树归属未核验。')
                if not linked and (git_identity.device, git_identity.inode) != (marker.st_dev, marker.st_ino):
                    raise Blocked('Git 元数据目录在读取期间变化。')
                with regular_at('config', commonfd) as config:
                    raw = read_metadata(config, 1_000_000)
                    origins = origin_values(root, config)
                    if read_metadata(config, 1_000_000) != raw:
                        raise Blocked('Git 配置在读取期间变化。')
                    current = os.stat('config', dir_fd=commonfd, follow_symlinks=False)
                    if stamp(current) != stamp(os.fstat(config)):
                        raise Blocked('Git 配置已替换。')
                # Git can layer worktree config over the shared repository config.
                try:
                    with regular_at('config.worktree', gitfd) as config:
                        read_metadata(config, 1_000_000)
                        if origin_values(root, config):
                            raise Blocked('工作树单独覆盖了 origin。')
                except FileNotFoundError:
                    pass
                if len(origins) != 1 or not origins[0] or len(origins[0]) > 600:
                    raise Blocked('请在此 Git 仓库配置一个明确的 origin URL。')
                actual = git_read(root, ['rev-parse','--show-toplevel']).removesuffix('\n')
                if actual != str(root):
                    raise Blocked('请打开 Git 仓库根目录。')
                head = git_read(root, ['rev-parse','--verify','HEAD'], optional=True)
                branch = git_read(root, ['symbolic-ref','--short','--quiet','HEAD'], optional=True)
                head = head.removesuffix('\n') if head else None
                branch = branch.removesuffix('\n') if branch else None
                if head is not None and not OID.fullmatch(head):
                    raise Blocked('Git HEAD 身份无法核验。')
                if branch and (len(branch)>300 or any(ord(char)<33 or ord(char)==127 for char in branch)):
                    raise Blocked('Git 分支身份无效。')
                if (stamp(marker) != stamp(os.stat('.git', dir_fd=rootfd, follow_symlinks=False))
                        or linked and text_metadata(rootfd,'.git') != link or store.workspace(workspace) != info):
                    raise Blocked('工作空间 Git 绑定在读取期间变化。')
                fingerprint = digest(canonical([workspace, common_identity.fields(), git_identity.fields(), digest(raw)]))
                return GitWorkspace(workspace, origins[0], head, branch, fingerprint)
    except (OSError, UnicodeError, Refused, subprocess.SubprocessError):
        raise Blocked('请打开含本机 Git 元数据的仓库根目录。') from None
