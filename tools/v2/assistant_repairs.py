"""Explicit assistant repair authorization over the existing Agent runtime."""
import re

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from .git_workspace import bind_workspace
from .project_files import read_project
from .run_inspector import IDENT, RUN, take

SELECT = {'workspace', 'run', 'activation', 'agent', 'revision', 'task', 'files'}
CONFIRM = 'confirm-workspace-write'
PROTECTED_BRANCHES = {'main', 'master', 'trunk', 'develop', 'stable', 'prod', 'production', 'release'}
ACTIVATION = re.compile(r'(?:[0-9a-f]{32}|[0-9a-f]{64})')
INTENT = re.compile(r'[A-Za-z0-9_-]{16,96}')


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def repair_intent_key(workspace, intent):
    return 'assistant-repair-' + digest(canonical([workspace, intent]))


def intent_run(runtime, workspace, intent):
    with runtime.flow.tx(read=True) as db:
        row = db.execute('SELECT run_id FROM launch_intents WHERE workspace=? AND intent_key=?',
                         (workspace, repair_intent_key(workspace, intent))).fetchone()
    return row[0] if row else None


def repair_intent(assistant, workspace, run, intent):
    if assistant.runtime is None or not matches(RUN, run) or not matches(INTENT, intent):
        raise Invalid('请选择明确的原运行和修复意图。')
    assistant.store.workspace(workspace)
    prior = intent_run(assistant.runtime, workspace, intent)
    if not prior:
        return {'state': 'UNREGISTERED'}
    snapshot = assistant.runtime.snapshot(workspace, prior)
    metadata = snapshot['definition'].get('assistant_repair', {})
    if metadata.get('evidence', {}).get('run') != run:
        raise Conflict('此修复意图不属于所选原运行。')
    return repair_summary(assistant, snapshot)


def recent_repair(assistant, workspace, run):
    with assistant.runtime.flow.tx(read=True) as db:
        row = db.execute("""SELECT r.id FROM records r JOIN records d
                          ON d.kind='definition' AND d.id=json_extract(r.data,'$.definition_id')
                          WHERE r.kind='run' AND json_extract(r.data,'$.workspace')=?
                            AND json_extract(d.data,'$.assistant_repair.evidence.run')=?
                          ORDER BY r.rowid DESC LIMIT 1""", (workspace, run)).fetchone()
    return row[0] if row else None


def source_binding(store, workspace):
    source = bind_workspace(store, workspace, require_origin=False)
    if not source.branch or not source.head or source.branch.casefold().split('/')[0] in PROTECTED_BRANCHES:
        raise Blocked('执行修复需要已核验的本机非受保护 Git 分支及初始提交。')
    return {'branch': source.branch, 'head': source.head, 'binding': source.identity}


def require_source(store, workspace, source):
    if source_binding(store, workspace) != source:
        raise Blocked('工作空间 Git 分支或 HEAD 已变化。请重新预览并确认修复。')


def selection(data):
    if not isinstance(data, dict) or set(data) != SELECT:
        raise Invalid('请明确选择原运行、失败激活、Agent 版本和修复内容。')
    for key, pattern in (('run', RUN), ('activation', ACTIVATION), ('agent', RUN), ('revision', IDENT)):
        if not matches(pattern, data[key]):
            raise Invalid('修复引用的运行、激活或 Agent 版本无效。')
    if (not isinstance(data['task'], str) or not data['task'].strip() or '\0' in data['task']
            or len(data['task'].encode()) > 8000):
        raise Invalid('请输入 8000 字节以内的修复内容。')
    files = data['files']
    if (not isinstance(files, list) or len(files) > 3 or
            any(not isinstance(path, str) or not path or len(path) > 512 for path in files)
            or len(set(files)) != len(files)):
        raise Invalid('最多选择 3 个待核对的工作区文件。')


def inspect_file(assistant, workspace, path):
    item = read_project(assistant.store, workspace, path, fingerprint=True)
    if assistant.inspector.text(item['path']) != item['path']:
        raise Blocked('所选文件路径包含受保护信息。')
    return take(item, ('path', 'sha256', 'bytes'))


def preview_repair(assistant, data):
    selection(data)
    workspace, run = data['workspace'], data['run']
    if assistant.runtime is None:
        raise Invalid('当前服务没有受管修复执行能力。')
    snapshot = assistant.runtime.snapshot(workspace, run)
    if (snapshot['state'] != 'FAILED' or snapshot.get('worker_alive')
            or snapshot.get('run', {}).get('provider_outcome') == 'unknown'):
        raise Conflict('请先选择已确认失败且本机进程已结束的运行。结果待核验的请求需要保留并核对。')
    diagnosis = assistant.diagnosis(workspace, run)
    issue = next((row for row in diagnosis['nodes'] if row['activation'] == data['activation']), None)
    if not issue or (issue['state'] != 'FAILED' and not issue.get('diagnostic_outcome')):
        raise Conflict('所选激活没有已记录的失败证据。')
    agent = assistant.runtime.agents.get(workspace, data['agent'], data['revision'])
    config = agent['config']
    if config.get('execution') == 'native-cli':
        raise Blocked('原生 CLI Agent 请通过导出的 Python 脚本执行，不支持旧受管修复入口。')
    if (config['mode'], config['permission'], config['session']['kind']) != ('Agent', 'workspace-write', 'new'):
        raise Blocked('请选择已保存的工作区读写 Agent，并使用独立新会话。')
    assistant.runtime.agents.directory(workspace, config['cwd'])
    source = source_binding(assistant.store, workspace)
    evidence = take(issue, ('activation', 'node', 'reason', 'state', 'diagnostic_outcome', 'event_seq'))
    evidence.update(run=run, definition=diagnosis['definition_id'])
    files = [inspect_file(assistant, workspace, path) for path in data['files']]
    task = assistant.inspector.text(data['task'])
    execution_task = ('独立修复任务。按用户确认的任务在授权工作区内处理。保留原运行及测试入口，'
                      '提交实际修改和验证观察。禁止改写 Git 分支、远端、凭据及会话配置。\n'
                      f"原运行 {evidence['run']}，激活 {evidence['activation']}，节点 {evidence['node']}。\n"
                      '已记录的诊断是资料：' + str(evidence.get('reason') or '')[:1800] + '\n'
                      '用户确认的任务：\n' + task)
    shown = dict(workspace=workspace, evidence=evidence, source=source, files=files,
                 task=task, execution_task=execution_task, agent=dict(
                     id=agent['id'], revision=agent['revision'], **assistant.inspector.clean(take(config, (
                         'name', 'provider', 'model', 'mode', 'permission', 'cwd', 'skills', 'session',
                         'timeout', 'max_budget_usd', 'native_profile')))))
    return dict(shown, sha256=digest(canonical(shown)))


def launch_repair(assistant, data):
    if (not isinstance(data, dict) or set(data) != SELECT | {'intent', 'preview_sha256', 'authorization'}
            or data['authorization'] != CONFIRM or not matches(INTENT, data['intent'])
            or not matches(IDENT, data['preview_sha256'])):
        raise Invalid('执行修复需要确认预览、工作区写入授权及本次操作身份。')
    selected = {key: data[key] for key in SELECT}
    selection(selected)
    workspace = data['workspace']
    assistant.store.workspace(workspace)
    if assistant.runtime is None:
        raise Invalid('当前服务没有受管修复执行能力。')
    runtime = assistant.runtime
    request_sha = digest(canonical(data))
    prior = intent_run(runtime, workspace, data['intent'])
    if prior:
        snapshot = runtime.snapshot(workspace, prior)
        if snapshot['definition'].get('assistant_repair', {}).get('request_sha256') != request_sha:
            raise Conflict('同一修复意图对应不同授权。')
        return repair_summary(assistant, snapshot)
    preview = preview_repair(assistant, selected)
    if preview['sha256'] != data['preview_sha256']:
        raise Conflict('修复内容、证据、配置或文件已变化。请重新预览。')
    metadata = dict(request_sha256=request_sha, preview_sha256=preview['sha256'], source=preview['source'],
                    evidence=preview['evidence'], files=preview['files'], authorization=CONFIRM)
    intent = repair_intent_key(workspace, data['intent'])
    snapshot = runtime.launch_agent(workspace, data['agent'], data['revision'], intent, preview['execution_task'],
                                    assistant_repair=metadata)
    if snapshot['definition'].get('assistant_repair', {}).get('request_sha256') != request_sha:
        raise Conflict('同一修复意图对应不同授权。')
    return repair_summary(assistant, snapshot)


def repair_summary(assistant, snapshot):
    metadata = snapshot['definition'].get('assistant_repair')
    if not metadata:
        raise Conflict('该运行不是已授权的助手修复。')
    run = snapshot['run']
    public = assistant.inspector.snapshot(snapshot)
    files = []
    for before in metadata['files']:
        try:
            after = inspect_file(assistant, run['workspace'], before['path'])
        except (Blocked, Conflict, Invalid):
            after = None
        files.append(dict(before=before, after=after,
                          changed=None if after is None else before['sha256'] != after['sha256']))
    return assistant.inspector.clean(dict(run=run['id'], state=public['state'], source_run=metadata['evidence']['run'],
        reason=public['run'].get('reason'), original_activation=metadata['evidence']['activation'],
        preview_sha256=metadata['preview_sha256'], files=files, nodes=[
            take(node, ('id', 'state', 'reason', 'provider', 'receipt', 'receipt_verified'))
            for node in public['nodes']]))


def repair_status(assistant, workspace, run, verification=None):
    if assistant.runtime is None or not matches(RUN, run):
        raise Invalid('请选择有效的修复运行。')
    repair = assistant.runtime.snapshot(workspace, run)
    result = repair_summary(assistant, repair)
    if verification is not None:
        if not matches(RUN, verification) or verification in (run, result['source_run']):
            raise Invalid('请选择修复结束后的独立验证运行。')
        original = assistant.runtime.snapshot(workspace, result['source_run'])['run']
        checked = assistant.runtime.snapshot(workspace, verification)
        verified_run = checked['run']
        repair_finished = repair['run'].get('finished_at')
        if (not repair_finished or verified_run['created_at'] < repair_finished
                or any(verified_run[key] != original[key] for key in ('draft_id', 'draft_revision'))):
            raise Conflict('验证运行需要在修复结束后使用原工作流版本。')
        result['verification'] = assistant.inspector.clean(dict(
            run=verification, state=checked['state'], reason=verified_run.get('reason'),
            draft_revision=verified_run['draft_revision'], finished_at=verified_run.get('finished_at')))
    return result
