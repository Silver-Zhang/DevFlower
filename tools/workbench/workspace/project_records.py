"""Explicit, portable copies of saved workflows kept with the project."""
import errno
import hashlib
import os
from pathlib import Path

from orchestration.common import Blocked, Conflict

from workbench.canvas.draft_transfer import FORMAT, MAX_ARCHIVE_BYTES, canonical_json
from workbench.workspace.project_files import ProjectFiles
from workbench.runs.tool_outputs import publish_link


def write_copy(view, directory, name, raw):
    try:
        with view.regular(name, directory) as existing:
            if os.read(existing, len(raw) + 1) != raw:
                raise Conflict('项目记录位置已有其他内容，请先核对该文件。')
            return
    except FileNotFoundError:
        pass
    with os.fdopen(os.open('.', os.O_WRONLY | os.O_TMPFILE, 0o600, dir_fd=directory), 'wb') as copy:
        copy.write(raw)
        copy.flush()
        os.fsync(copy.fileno())
        status = publish_link(view.root_fd, directory, copy.fileno(), name)
        if status == errno.EEXIST:
            with view.regular(name, directory) as existing:
                if os.read(existing, len(raw) + 1) == raw:
                    return
        if status != 0:
            raise Blocked('无法核验项目副本写入，请检查 .devflower/workflows。')
        os.fsync(directory)


def save_workflow_copy(store, workspace, key, revision):
    row = store.draft(workspace, key)
    if row['revision'] != revision:
        raise Conflict('草稿版本已变化，请重新打开后保存项目副本。')
    document = row['graph']
    sha = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    raw = (canonical_json(dict(format=FORMAT, version=1, document=document, document_sha256=sha)) + '\n').encode()
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise Blocked('工作流档案超过大小上限。')
    relative = Path('.devflower/workflows')
    name = f'{key}-{sha}.workflow.json'
    project = ProjectFiles(store, workspace)
    project.check_scope(relative)
    project.check_scope(relative / '.gitignore')
    project.check_scope(relative / name)
    try:
        with project.open_view() as view:
            for path in (relative.parent, relative):
                with view.directory(path.parent) as parent:
                    if path == relative:
                        info = os.fstat(parent)
                        if info.st_uid != os.getuid() or info.st_mode & 0o022:
                            raise Blocked('已有 .devflower 目录的所有者或权限不符。')
                    try:
                        os.mkdir(path.name, mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
            with view.directory(relative) as directory:
                info = os.fstat(directory)
                if info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise Blocked('项目记录目录 .devflower/workflows 需仅供当前用户读写。')
                write_copy(view, directory, '.gitignore', b'*\n')
                write_copy(view, directory, name, raw)
            with view.directory(relative) as directory, view.regular(name, directory) as stored:
                if os.read(stored, len(raw) + 1) != raw:
                    raise Conflict('项目副本未通过读回核验。')
    except OSError:
        raise Blocked('无法保存项目副本，请核对目录权限和现有记录。') from None
    return dict(workspace=workspace, id=key, revision=revision, path=str(relative / name), document_sha256=sha)
