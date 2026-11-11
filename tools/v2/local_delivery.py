"""Explicit local export of the exact candidate accepted by the independent Gate."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.server import Conflict
from studio.workspace_read_policy import SENSITIVE

from .data_handoff import task_literals
from .development_evidence import candidate_files, current_candidate
from .git_workspace import bind_workspace
from .project_files import protected_roots
from .run_inspector import IDENT, RUN


def delivery_scope(platform, workspace, run):
    if not isinstance(run, str) or not RUN.fullmatch(run):
        raise Invalid('请选择已完成的研发运行。')
    store = platform.store
    root = Path(store.workspace(workspace)['path'])
    if any(root.is_relative_to(path) or path.is_relative_to(root) for path in protected_roots(store)):
        raise Blocked('工作区与受保护位置重叠，未导出。')
    scope = bind_workspace(store, workspace, require_origin=False)
    return root, scope, platform.delivery(scope, run_id=run)


def preview(platform, workspace, run):
    _, _, status = delivery_scope(platform, workspace, run)
    return status


def package(platform, data):
    if set(data) != {'workspace', 'run', 'activation', 'candidate_sha256', 'authorization'}:
        raise Invalid('导出需要明确确认本次候选。')
    if (data['authorization'] != 'confirm-local-candidate-download'
            or not all(isinstance(data[key], str) and IDENT.fullmatch(data[key])
                       for key in ('activation','candidate_sha256'))):
        raise Invalid('请确认当前 Gate 和候选摘要后再导出。')
    workspace, run = data['workspace'], data['run']
    root, scope, status = delivery_scope(platform, workspace, run)
    if (status['state'] != 'LOCAL_GATE_ACCEPTED' or status['run'] != run
            or status['gate_activation'] != data['activation']
            or status['gate']['candidate_sha256'] != data['candidate_sha256']):
        raise Conflict('当前候选与已确认 Gate 不一致，未生成交付包。')
    with TemporaryDirectory(prefix='delivery-', dir=platform.store.state/'flow-artifacts'/run) as scratch:
        candidate = Path(scratch)/'candidate'
        candidate.mkdir()
        identity = current_candidate(root, candidate)
        entries = candidate_files(candidate)
        if (identity['candidate_sha256'] != data['candidate_sha256']
                or identity['git_head'] != status['gate']['git_head']
                or digest(canonical(entries)) != identity['candidate_sha256']):
            raise Conflict('候选在生成交付包期间变化。')
        manifest = dict(format='devflower.v2.local-delivery', version=1, workspace=workspace, run=run,
                        gate_activation=data['activation'], verification_sha256=status['gate']['verification_sha256'],
                        **identity, entries=entries)
        buffer = BytesIO()
        literals = [item.encode() for item in task_literals()]
        with ZipFile(buffer, 'w', compression=ZIP_STORED) as archive:
            archive.writestr('devflower-delivery.json', canonical(manifest))
            for entry in entries:
                name = entry['path']
                parts = Path(name).parts
                if ('\\' in name or ':' in name or any(SENSITIVE.search(part)
                        or part.startswith('.env') or part in {'.codex','.claude','.copilot','.devflower'} for part in parts)):
                    raise Blocked('候选含受保护或不适合移交的文件名，未导出。')
                raw = (candidate/name).read_bytes()
                if digest(raw) != entry['sha256'] or any(literal in raw for literal in literals):
                    raise Blocked('候选文件未通过摘要或受保护内容检查，未导出。')
                member = ZipInfo('candidate/'+name)
                member.create_system = 3
                member.external_attr = int(entry['mode'], 8) << 16
                archive.writestr(member, raw)
        if (scope != bind_workspace(platform.store, workspace, require_origin=False)
                or identity != current_candidate(root)):
            raise Conflict('工作区在生成交付包期间变化。')
    return buffer.getvalue()
