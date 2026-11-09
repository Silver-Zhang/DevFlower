"""Bounded read-only views of registered Linux workspaces."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import unicodedata

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.server import Conflict
from studio.session_sources import Refused, opened
from studio.workspace_read_policy import IgnoreRules, safe_parts, stamp

MAX_ENTRIES = 512
MAX_BYTES = 16_000
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK


def mount_id(fd):
    with open(f'/proc/self/fdinfo/{fd}', encoding='ascii') as info:
        for line in info.read(4096).splitlines():
            key, _, value = line.partition(':')
            if key == 'mnt_id' and value.strip().isdigit():
                return int(value)
    raise Blocked('无法确认项目文件的挂载范围')


def checked_path(value):
    if (not isinstance(value, str) or len(value) > 4096 or value.startswith('/')
            or '\0' in value or '\\' in value or '..' in value.split('/')):
        raise Invalid('项目文件路径须为工作空间内的相对路径')
    path = safe_parts(value)
    if len(path.parts) > 32:
        raise Invalid('项目文件路径层级超限')
    if any(safe_text(part) != part or any(unicodedata.category(char) in ('Cc', 'Cf') for char in part)
           for part in path.parts):
        raise Blocked('只允许查看工作空间内的普通文件与目录')
    return path


def protected_roots(store):
    home = Path.home()
    paths = [store.state, Path(__file__).resolve().parents[2], home / '.codex',
             home / '.claude', home / '.claude.json', home / '.copilot']
    for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'COPILOT_HOME', 'GH_CONFIG_DIR', 'DEVFLOWER_SETTINGS_HOME'):
        value = os.environ.get(key)
        if value and Path(value).is_absolute():
            try:
                paths.extend((Path(value), Path(value).resolve()))
            except (OSError, RuntimeError):
                raise Blocked('无法确认受保护位置，项目文件未读取') from None
    return paths


class ProjectFiles:
    def __init__(self, store, workspace):
        self.workspace = workspace
        self.root = Path(store.workspace(workspace)['path'])
        self.protected = protected_roots(store)
        self.check_scope(Path('.'))

    def check_scope(self, relative):
        if any((self.root / relative).is_relative_to(root) for root in self.protected):
            raise Blocked('受保护位置不能作为项目文件查看')

    def require_root(self, fd):
        meta = os.fstat(fd)
        identity = digest(canonical([str(self.root), meta.st_dev, meta.st_ino]))
        if identity != self.workspace:
            raise Conflict('工作空间文件夹身份已变化，请重新打开')
        return mount_id(fd)

    @contextmanager
    def open_view(self):
        with opened(self.root, directory=True) as fd:
            root_mount = self.require_root(fd)
            yield FileView(fd, root_mount)
            # Check the registered pathname again before returning data, even
            # if it moved before the first ignore-rule snapshot was taken.
            with opened(self.root, directory=True) as current:
                if self.require_root(current) != root_mount:
                    raise Conflict('工作空间挂载已变化，请重新打开')


class FileView:
    def __init__(self, root_fd, root_mount):
        self.root_fd = root_fd
        self.root_mount = root_mount
        self.rules = IgnoreRules(self.directory, file_opener=self.regular)

    def check_mount(self, fd):
        if mount_id(fd) != self.root_mount:
            raise Blocked('不能跨挂载读取项目文件')

    @contextmanager
    def directory(self, relative):
        fd = os.dup(self.root_fd)
        try:
            for part in relative.parts:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                self.check_mount(fd)
            if not os.fstat(fd).st_mode & 0o444:
                raise PermissionError()
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def pin(self, name, dir_fd):
        fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            self.check_mount(fd)
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def regular(self, name, dir_fd):
        with self.pin(name, dir_fd) as pin:
            before = os.fstat(pin)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Refused('not_private_regular')
            if not before.st_mode & 0o444:
                raise PermissionError()
            # Reopen the already verified inode instead of the mutable name.
            fd = os.open(f'/proc/self/fd/{pin}', os.O_RDONLY | os.O_NONBLOCK)
            try:
                self.check_mount(fd)
                self.require_same(before, os.fstat(fd))
                yield fd
                self.require_same(before, os.fstat(fd))
                with self.pin(name, dir_fd) as current:
                    self.require_same(before, os.fstat(current))
            finally:
                os.close(fd)

    @staticmethod
    def require_same(before, after):
        if stamp(before) != stamp(after) or after.st_nlink != 1:
            raise Blocked('文件在读取期间变化，请刷新后重试')

    def check_rules(self, relative):
        if self.rules.denied(relative):
            raise Blocked('项目忽略规则拒绝查看此位置')


def read_project(store, workspace, path, *, preview=False):
    relative = checked_path(path)
    if preview and not relative.parts:
        raise Invalid('请选择要预览的文件')
    project = ProjectFiles(store, workspace)
    project.check_scope(relative)
    try:
        with project.open_view() as view:
            view.check_rules(relative)
            if preview:
                result = preview_file(view, relative)
            else:
                view.rules.remember(relative)
                result = list_files(project, view, relative)
            view.rules.validate()
    except PermissionError:
        raise Blocked('没有读取所选位置的权限，请检查本机读取权限') from None
    except (FileNotFoundError, NotADirectoryError, Refused):
        raise Invalid('所选位置不存在或不属于可读取的普通文件或目录') from None
    except OSError:
        raise Blocked('无法安全读取所选位置，请刷新后重试') from None
    public_path = relative.as_posix() if relative.parts else ''
    return {'workspace': workspace, 'path': public_path, **result}


def list_files(project, view, relative):
    rows = []
    truncated = False
    with view.directory(relative) as directory, os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index == MAX_ENTRIES:
                truncated = True
                break
            child = relative / entry.name
            try:
                checked_path(child.as_posix())
                project.check_scope(child)
                with view.pin(entry.name, directory) as fd:
                    meta = os.fstat(fd)
            except (Blocked, Invalid, OSError):
                continue
            is_directory = stat.S_ISDIR(meta.st_mode)
            if not is_directory and not (stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1):
                continue
            if view.rules.denied(child):
                continue
            rows.append({'name': entry.name, 'path': child.as_posix(), 'directory': is_directory})
    return {'entries': sorted(rows, key=lambda row: (not row['directory'], row['name'])), 'truncated': truncated}


def preview_file(view, relative):
    with view.directory(relative.parent) as directory, view.regular(relative.name, directory) as fd:
        if os.fstat(fd).st_size > MAX_BYTES:
            raise Invalid(f'文件超过 {MAX_BYTES} 字节，无法预览')
        # Buffered read handles short OS reads and still enforces the byte cap.
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Invalid(f'文件超过 {MAX_BYTES} 字节，无法预览')
        if b'\0' in raw:
            raise Invalid('二进制文件无法预览')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise Invalid('只支持 UTF-8 文本预览') from None
    return {'text': safe_text(text), 'readonly': True}
