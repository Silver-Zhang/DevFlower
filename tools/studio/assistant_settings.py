"""Host-owned assistant connection, shared across installations and workspaces.

Conversation history stays in its workspace. This file contains credentials and
must never become a workspace artifact or a native Agent mount.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
from orchestration.common import Blocked, canonical
from .session_sources import clean_path, opened

DEFAULT_CONNECTION = dict(protocol='openai', base_url='https://api.deepseek.com',
                          model='deepseek-flash', effort='high')


def settings_root():
    override = os.environ.get('DEVFLOWER_SETTINGS_HOME')
    return Path(clean_path(override or str(Path(os.environ.get('XDG_CONFIG_HOME') or
                                            Path.home()/'.config')/'devflower')))


class ConnectionStore:
    filename = 'devflower-agent-connection.json'

    def __init__(self, root):
        self.root = Path(clean_path(str(root)))
        self.path = self.root/self.filename

    @contextmanager
    def directory(self):
        # mkdir/open one component at a time: never follow a symlink, including
        # an ancestor swapped between checking and writing the private file.
        fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nxt
            meta = os.fstat(fd)
            if meta.st_uid != os.getuid() or meta.st_mode & 0o077:
                raise Blocked('助手设置目录必须为当前用户私有目录（权限 0700）')
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def private(fd):
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
            raise Blocked('助手设置必须是当前用户的私有普通文件')

    def read(self):
        with opened(self.path) as fd:
            self.private(fd)
            raw = os.read(fd, 100001)
        if len(raw) > 100000: raise Blocked('助手模型连接配置过大')
        return json.loads(raw)

    @contextmanager
    def locked(self):
        with self.directory() as directory:
            fd = os.open('.connection.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            try:
                self.private(fd)
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: raise Blocked('助手连接正在保存，请稍后重试') from None
                yield directory
            finally: os.close(fd)

    def write(self, value, directory):
        temporary = '.connection-'+secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.filename, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass
