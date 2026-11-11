"""Candidate-bound test and review evidence for the V2 development loop."""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from orchestration.common import Blocked, canonical, digest, save
from orchestration.gate import gate
from orchestration.privacy import credentials
from orchestration.verification import verify
from studio.graph import Invalid
from studio.session_sources import opened, regular_at
from studio.workspace_read_policy import stamp
from studio.workbench_data import public_data

from .git_workspace import OID, bind_workspace, git_read
from .recovery import frozen_node, read_completion

CONTRACT = 'workspace-verification'
MAX_FILES = 2000
MAX_BYTES = 32 * 1024 * 1024
TEST_PATH = re.compile(r'(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py')
VERDICTS = {'ACCEPT', 'REJECT', 'ACCEPT_WITH_LIMITATIONS'}


def candidate_files(root, destination=None, *, project=None):
    """Bound and pin reads before copying; V1's tree manifest is the digest format."""
    from .project_files import mount_id
    rows = []
    entries = total = 0

    def collect(folder, prefix=(), level=0, view=None):
        nonlocal entries, total
        if level > 16:
            raise Blocked('候选目录层级超出校验限制。')
        initial = stamp(os.fstat(folder))
        with os.scandir(folder) as items:
            for item in items:
                entries += 1
                if entries > MAX_FILES:
                    raise Blocked('候选文件数超出校验限制。')
                if item.name == '.git' or not prefix and item.name == '.devflower':
                    continue
                path = (*prefix, item.name)
                name = '/'.join(path)
                if view is not None:
                    from .project_files import checked_path
                    try:
                        relative = checked_path(name)
                        project.check_scope(relative)
                    except (Invalid, Blocked):
                        continue
                    if view.rules.denied(relative):
                        continue
                if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in name):
                    raise Blocked('候选含不支持的文件名。')
                before = item.stat(follow_symlinks=False)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
                    try:
                        if stamp(os.fstat(child)) != stamp(before):
                            raise Blocked('候选目录在读取期间变化。')
                        if mount_id(child) != root_mount:
                            raise Blocked('候选不能跨挂载读取文件。')
                        collect(child, path, level + 1, view)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or name == '.gitmodules':
                    raise Blocked('候选含不支持的符号链接、硬链接、特殊文件或子模块。')
                total += before.st_size
                if total > MAX_BYTES:
                    raise Blocked('候选文件体积超出校验限制。')
                open_regular = view.regular if view is not None else regular_at
                with open_regular(item.name, folder) as fd:
                    if mount_id(fd) != root_mount:
                        raise Blocked('候选不能跨挂载读取文件。')
                    raw = os.pread(fd, before.st_size + 1, 0)
                    if len(raw) != before.st_size or stamp(os.fstat(fd)) != stamp(before):
                        raise Blocked('候选文件在读取期间变化。')
                if item.name.endswith('.gitattributes') and b'filter=' in raw:
                    raise Blocked('候选含不支持的 Git 文件过滤器。')
                mode = '100755' if before.st_mode & 0o111 else '100644'
                rows.append(dict(path=name, mode=mode, size=len(raw), sha256=digest(raw)))
                if destination:
                    target = destination.joinpath(*path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                    target.chmod(0o755 if mode == '100755' else 0o644)
        if stamp(os.fstat(folder)) != initial:
            raise Blocked('候选目录在读取期间变化。')

    if project is None:
        with opened(root, directory=True) as directory:
            root_mount = mount_id(directory)
            collect(directory)
    else:
        with project.open_view() as view:
            root_mount = view.root_mount
            collect(view.root_fd, view=view)
            view.rules.validate()
    return sorted(rows, key=lambda row: row['path'])


def current_candidate(root, destination=None, *, project=None):
    """A fixed Git HEAD and all non-metadata workspace files, including untracked files."""
    root = Path(root)
    try:
        if git_read(root, ['rev-parse', '--show-toplevel']).rstrip('\n') != str(root):
            raise Blocked('研发校验需要 Git 工作区根目录。')
        # Reserved project metadata must be valid and cannot contain tracked source.
        from orchestration.common import workspace_metadata
        if workspace_metadata(root) and git_read(root, ['ls-files', '--', '.devflower']):
            raise Blocked('项目保留区包含已跟踪源码，无法固定候选。')
        head = git_read(root, ['rev-parse', '--verify', 'HEAD']).rstrip('\n')
        if not OID.fullmatch(head):
            raise Blocked('研发校验需要已固定的 Git HEAD。')
        entries = candidate_files(root, destination, project=project)
        if entries != candidate_files(root, project=project) or head != git_read(root, ['rev-parse', '--verify', 'HEAD']).rstrip('\n'):
            raise Blocked('候选在校验期间变化。')
    except (OSError, ValueError) as exc:
        raise Blocked('无法安全读取固定 Git 候选。') from exc
    return dict(candidate_sha256=digest(canonical(entries)), git_head=head)


def test_path(path):
    if (not isinstance(path, str) or len(path) > 240 or not TEST_PATH.fullmatch(path)
            or set(path.split('/')) & {'.', '..', '.git', '.devflower'}):
        raise Blocked('校验需要授权的相对 Python 路径。')
    return path


def verification_entry_sha(root, path):
    path = test_path(path)
    try:
        with opened(Path(root) / path) as fd:
            initial = os.fstat(fd)
            if not 0 < initial.st_size <= 262144:
                raise Blocked('校验入口超过大小限制或为空。')
            raw = os.pread(fd, initial.st_size + 1, 0)
            if len(raw) != initial.st_size or stamp(initial) != stamp(os.fstat(fd)):
                raise Blocked('校验入口在读取期间变化。')
    except (OSError, ValueError) as exc:
        raise Blocked('无法固定工作区内的校验入口。') from exc
    return digest(raw)


def verification_spec(node):
    path = test_path(node.get('path'))
    entry_sha = node.get('entry_sha256')
    if (not isinstance(entry_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', entry_sha)
            or 'expected_json' not in node):
        raise Blocked('校验需要冻结入口指纹和 JSON 预期值。')
    raw = canonical(node['expected_json'])
    if len(raw) > 8192 or canonical(json.loads(raw, parse_constant=invalid_constant)) != raw:
        raise Blocked('JSON 预期值超出限制。')
    return dict(id=CONTRACT, argv=['/usr/bin/python3', '-I', '-B', path], expected_json=node['expected_json'],
                runner='json-assertion', result_format='controller-json', min_cases=1, max_skipped=0,
                entry_sha256=entry_sha)


def invalid_constant(value):
    raise ValueError('nonfinite JSON')


def evidence_path(rt, run_id, activation):
    return rt.storage / 'flow-artifacts' / run_id / activation / 'development-verification.json'


def require_bound_source(rt, run, source=None):
    source = source if source is not None else run['graph']['development']['source']
    binding = bind_workspace(rt.store, run['workspace'], require_origin=False)
    if source != dict(binding=binding.identity, branch=binding.branch, head=binding.head):
        raise Blocked('当前 Git 分支、HEAD 或工作树绑定与启动时的候选不同。')


def verify_candidate(rt, run_id, activation, node, *, capture_result=False):
    rt.check_operator()
    run = rt.flow.get('run', run_id)
    source = node['source'] if capture_result else None
    project = None
    def cancelled():
        return bool(rt.flow.get('run', run_id).get('cancel_requested'))
    if capture_result:
        from .project_files import ProjectFiles
        project = ProjectFiles(rt.store, run['workspace'])
        if cancelled():
            raise Blocked('工具执行已取消。')
    require_bound_source(rt, run, source)
    root = rt.store.workspace(run['workspace'])['path']
    spec = verification_spec(node)
    contract_hash = digest(canonical(spec))
    path = evidence_path(rt, run_id, activation['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='verify-', dir=path.parent) as temp:
        scratch = Path(temp)
        candidate = scratch / 'candidate'
        candidate.mkdir()
        identity = current_candidate(root, candidate, project=project)
        if verification_entry_sha(candidate, node['path']) != spec['entry_sha256']:
            raise Blocked('校验入口与启动时冻结的文件不同，未运行测试。')
        timeout = 20
        if capture_result and isinstance(run.get('started_at'), (int, float)):
            remaining = run['started_at'] + run['graph']['budget']['seconds'] - time.time()
            if remaining <= 0:
                raise Blocked('运行已达到时间预算，未启动校验进程。')
            timeout = min(timeout, remaining)
        config = dict(verification_isolation='bwrap', policy=dict(
            verification=[spec], limits=dict(step_timeout_seconds=timeout, max_log_bytes=131072)))
        output_options = {'capture_asserted_json': True, 'cancel_check': cancelled} if capture_result else {}
        report, = verify(config, candidate, scratch / 'artifacts', run_id, activation['round'],
                         identity['candidate_sha256'], contract_hash, deny_network=True, **output_options)
    rt.check_operator()
    require_bound_source(rt, run, source)
    if capture_result and cancelled():
        raise Blocked('工具执行已取消。')
    if identity != current_candidate(root, project=project):
        raise Blocked('测试期间工作区发生变化，测试结果不能用于交付。')
    # No argv, full filesystem paths, credentials or candidate-controlled stdout in public results.
    proof = {key: report[key] for key in ('id', 'run_id', 'round', 'acceptance_sha256',
                                        'process', 'counts', 'passed', 'result_sha256')}
    proof.update(identity, activation=activation['id'])
    save(path, proof)
    message = ('实际测试通过。' if proof['passed'] else
               '实际测试失败：进程退出非零、输出与预期 JSON 不符，或触发了执行限制。')
    result = dict(identity, passed=proof['passed'], counts=proof['counts'], exit=proof['process']['exit'],
                  verification_sha256=digest(canonical(proof)), feedback=message)
    if capture_result and 'asserted_json' in report:
        if cancelled():
            raise Blocked('工具执行已取消。')
        from .tool_artifacts import publish
        result['tool_artifact'] = publish(rt, run, activation, report, result['verification_sha256'])
    return dict(answer=message, result=result)


def role_activation(rt, run, activation, ident):
    from .designer_scheduler import activation_key
    prefix, _, _ = activation['node'].rpartition('/')
    name = '/'.join(filter(None, (prefix, ident)))
    row = rt.flow.get('activation', activation_key(run['id'], name, activation['iteration_path']))
    if (row['run'], row['node'], row['iteration_path'], row['state']) != (
            run['id'], name, activation['iteration_path'], 'COMPLETED'):
        raise Blocked('交付角色缺少同一轮次的已完成激活。')
    return row


def independent_review(output):
    raw = output['answer'].strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.+?)\n```', raw, flags=re.DOTALL)
    if fenced:
        raw = fenced[1]
    try:
        def unique(pairs):
            value = dict(pairs)
            if len(value) != len(pairs):
                raise ValueError('duplicate keys')
            return value
        value = json.loads(raw, object_pairs_hook=unique) if raw.startswith('{') else output['result']
        if (set(value) == {'verdict', 'candidate_sha256', 'feedback'}
                and value['verdict'] in VERDICTS and isinstance(value['feedback'], str)
                and 0 < len(value['feedback'].strip()) <= 1800):
            return value
    except (ValueError, TypeError, KeyError):
        pass
    raise Blocked('独立审查缺少格式有效的结论、候选指纹和反馈。')


def delivery_gate(rt, run_id, activation, node):
    rt.check_operator()
    run = rt.flow.get('run', run_id)
    definition = rt.flow.get('definition', run['definition_id'])
    if digest(canonical({key: value for key, value in definition.items() if key != 'id'})) != run['definition_id']:
        raise Blocked('冻结研发工作流身份无法核验。')
    require_bound_source(rt, run)
    root = rt.store.workspace(run['workspace'])['path']
    identity = current_candidate(root)
    verification = role_activation(rt, run, activation, node['verification_node'])
    path = evidence_path(rt, run_id, verification['id'])
    with opened(path) as fd:
        if os.fstat(fd).st_size > 16000:
            raise Blocked('测试证据超过读取上限。')
        proof = json.loads(os.read(fd, 16001))
    proof_hash = digest(canonical(proof))
    spec = verification_spec(frozen_node(definition, verification['node']))
    contract_hash = digest(canonical(spec))
    if (proof_hash != verification['output']['result']['verification_sha256']
            or any(proof.get(key) != value for key, value in identity.items())
            or (proof['run_id'], proof['activation'], proof['round'], proof['acceptance_sha256']) != (
                run_id, verification['id'], activation['round'], contract_hash)):
        raise Blocked('测试证据与当前候选、轮次或冻结测试不匹配。')
    steps = [row for row in rt.flow.list('native-step') if row.get('run') == run_id]
    try:
        completions = [read_completion(rt, run, role_activation(rt, run, activation, node[key]), definition, steps)
                       for key in ('developer_node', 'tester_node', 'reviewer_node')]
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise Blocked('开发、测试或审核缺少可核验的原生进程和请求回执。') from exc
    receipts = [completion['receipt'] for completion in completions]
    if len({receipt['session_id'] for receipt in receipts}) != 3:
        raise Blocked('开发、测试和独立审查必须使用不同的真实会话。')
    verdict, feedback = 'REJECT', ''
    try:
        response = independent_review(completions[-1]['output'])
        if response['candidate_sha256'] != identity['candidate_sha256']:
            raise Blocked('独立审查针对不同候选。')
        proposed = response['verdict']
        feedback = public_data(response['feedback'], credentials(Path.home()))
        report = dict(schema_version=1, run_id=run_id, round=activation['round'],
                      candidate_sha256=identity['candidate_sha256'], acceptance_sha256=contract_hash,
                      verdict='NEEDS_CORRECTION' if proposed == 'REJECT' else 'ACCEPT',
                      contracts=[dict(id=CONTRACT, status='PASS' if proof['passed'] else 'FAIL',
                                      evidence_refs=[CONTRACT])], findings=[],
                      limitations=[feedback] if proposed == 'ACCEPT_WITH_LIMITATIONS' else [])
        gate(run_id, activation['round'], identity['candidate_sha256'], contract_hash, [CONTRACT], [proof],
             dict(receipts[0], report={'status': 'IMPLEMENTED'}), dict(receipts[-1], report=report),
             expected_verification=[spec])
        # Reuse the mechanical evidence checks for both outcomes. A qualified
        # review retains its verdict and cannot exit the loop as accepted.
        verdict = proposed
    except Blocked as refusal:
        feedback = ' '.join(part for part in (feedback, str(refusal)) if part)
    require_bound_source(rt, run)
    if identity != current_candidate(root):
        raise Blocked('独立交付检查期间候选发生变化。')
    return dict(answer=feedback, result=dict(identity, gate_verdict=verdict, feedback=feedback,
                                              verification_sha256=proof_hash))
