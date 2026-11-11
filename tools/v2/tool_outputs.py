"""Publish a bounded workflow result to a newly authorized project file."""
import json
import os

from orchestration.common import Blocked, canonical, digest
from orchestration.privacy import redact
from studio.graph import Invalid
from studio.server import Conflict

from .data_handoff import task_literals
from .project_files import ProjectFiles, checked_path
from .run_inspector import RunInspector
from .session_identity import StorageRootIdentity

MAX_OUTPUT_BYTES = 16000


def output_path(value):
    relative = checked_path(value)
    if relative.suffix != '.json':
        raise Invalid('工具输出需要工作区内的新建 .json 文件路径。')
    return relative


def output_bytes(store, value):
    raw = canonical(value) + b'\n'
    if len(raw) > MAX_OUTPUT_BYTES:
        raise Blocked('结构化结果超过工作区输出上限。')
    if RunInspector(store).clean(value) != value or redact(raw.decode(), task_literals()) != raw.decode():
        raise Blocked('结构化结果含受保护内容，未写入文件。')
    return raw


def reserve(rt, run_id, activation, proof):
    with rt.flow.tx() as db:
        run = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()[0])
        current = json.loads(db.execute("SELECT data FROM records WHERE kind='activation' AND id=?",
                                        (activation['id'],)).fetchone()[0])
        if run.get('cancel_requested'):
            raise Blocked('运行已取消，未写入工具输出。')
        if (current['run'] != run_id or current['state'] != 'STARTING'
                or current.get('tool_output_intent') or current.get('effect_uncertain')):
            raise Conflict('此工具输出已有记录，请核对原激活。')
        current.update(effect_uncertain=True, tool_output_intent=proof)
        db.execute("UPDATE records SET data=? WHERE kind='activation' AND id=?",
                   (canonical(current).decode(), activation['id']))


def require_parent(view, relative, parent):
    with view.directory(relative.parent) as current:
        if StorageRootIdentity.from_fd(parent) != StorageRootIdentity.from_fd(current):
            raise Conflict('工具输出目录身份已变化。')
    view.rules.validate()


def write_result(rt, run_id, activation, node, value):
    rt.check_operator()
    run = rt.flow.get('run', run_id)
    project = ProjectFiles(rt.store, run['workspace'])
    relative = output_path(node['path'])
    project.check_scope(relative)
    data = output_bytes(rt.store, value)
    proof = dict(path=relative.as_posix(), sha256=digest(data), bytes=len(data))
    with project.open_view() as view:
        view.check_rules(relative)
        with view.directory(relative.parent) as parent:
            try:
                os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Conflict('工作区输出文件已存在，请选择新路径。')
            with os.fdopen(os.open('.', os.O_WRONLY | os.O_TMPFILE | os.O_NOFOLLOW, 0o600, dir_fd=parent), 'wb') as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                require_parent(view, relative, parent)
                reserve(rt, run_id, activation, proof)
                try:
                    require_parent(view, relative, parent)
                except (Blocked, Conflict, OSError):
                    rt.flow.change('activation', activation['id'], {'effect_uncertain': False}, states={'STARTING'})
                    raise
                try:
                    os.link(f'/proc/self/fd/{temporary.fileno()}', relative.name, dst_dir_fd=parent,
                            follow_symlinks=True)
                except FileExistsError:
                    rt.flow.change('activation', activation['id'], {'effect_uncertain': False}, states={'STARTING'})
                    raise Conflict('工作区输出文件已由其他写者创建。') from None
                os.fsync(parent)
                with view.regular(relative.name, parent) as stored:
                    if (os.fstat(stored).st_ino != os.fstat(temporary.fileno()).st_ino
                            or os.read(stored, MAX_OUTPUT_BYTES + 1) != data):
                        raise Blocked('工作区工具输出无法回读核验。')
                with project.open_view() as current:
                    current.check_rules(relative)
                    require_parent(current, relative, parent)
    return {'answer': '结构化结果已写入工作区。', 'result': {'file': proof}}
