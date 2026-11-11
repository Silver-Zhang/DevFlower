"""Frozen task context, public field contracts, and scoped artifact references."""
import json
import os
import re
from pathlib import Path

from orchestration.common import Blocked, canonical, digest
from orchestration.privacy import redact
from studio.data_contracts import check, normalize
from studio.graph import Invalid
from studio.local_claude_adapter import claude_secrets
from studio.server import Conflict
from studio.workbench_data import public_data
from studio.workbench_graph import field, transfer

from .claude_session_profile import startup_environment
from .project_files import MAX_BYTES, checked_path, read_project
from .run_inspector import PRIVATE, RunInspector

MAX_FILES = 3
MAX_ARTIFACTS = 8
SHA = re.compile(r'[0-9a-f]{64}')
STRUCTURED = '请将结果按声明的公开字段组织，遵守字段类型和必需标记。'


def relative_file(value):
    path = checked_path(value)
    if not path.parts or path.as_posix() != value:
        raise Invalid('请选择明确的工作区相对文件路径。')
    return value


def file_paths(values, limit):
    if not isinstance(values, list) or len(values) > limit:
        raise Invalid(f'最多选择 {limit} 个工作区文件。')
    paths = [relative_file(value) for value in values]
    if len(paths) != len(set(paths)):
        raise Invalid('工作区文件路径不能重复。')
    return paths


def task_selection(values):
    if not isinstance(values, list) or len(values) > MAX_FILES:
        raise Invalid('每次运行最多附加 3 个明确选中的项目文件。')
    refs = []
    for row in values:
        if (not isinstance(row, dict) or set(row) != {'path','sha256'}
                or not isinstance(row['sha256'], str) or not SHA.fullmatch(row['sha256'])):
            raise Invalid('任务文件需要预览时核验的路径和摘要。')
        refs.append(dict(path=relative_file(row['path']),sha256=row['sha256']))
    file_paths([row['path'] for row in refs], MAX_FILES)
    return refs


def task_literals():
    home = Path.home()
    return claude_secrets(dict(home=str(home),config_root=str(home/'.claude'),chat_only=True),
                          startup_environment(os.environ))


def task_file(store, workspace, path):
    relative_file(path)
    item = read_project(store, workspace, path, preview=True)
    view = RunInspector(store)
    literals = task_literals()
    text = view.text(redact(item['text'], literals))
    if view.text(redact(path, literals)) != path:
        raise Invalid('所选文件名包含受保护内容。')
    raw = text.encode()
    if len(raw)>MAX_BYTES:
        raise Invalid('任务文件的可发送文本超过 16000 字节。')
    return dict(item,text=text,sha256=digest(raw),bytes=len(raw))


def freeze_task_files(store, workspace, refs):
    result = []
    for ref in refs:
        item = task_file(store, workspace, ref['path'])
        if item['sha256'] != ref['sha256']:
            raise Conflict('所选任务文件已变化，请重新预览并确认。')
        result.append({key:item[key] for key in ('path','sha256','bytes','text')})
    return result


def require_task_files_public(store, files):
    view = RunInspector(store)
    literals = task_literals()
    for row in files:
        if (any(view.text(redact(row[key],literals))!=row[key] for key in ('path','text'))
                or digest(row['text'].encode())!=row['sha256']):
            raise Blocked('冻结任务文件与当前公开发送边界不一致，未发送本次请求。')


def task_file_refs(files):
    return [{key:row[key] for key in ('path','sha256','bytes')} for row in files]


def field_contract(rows):
    if rows is not None and (not isinstance(rows,list) or len(rows)>32 or any(
            not isinstance(row,dict) or not isinstance(row.get('field'),str)
            or not isinstance(row.get('type','any'),str) for row in rows)):
        raise Invalid('数据合同需要公开字段路径、类型与必需标记，最多 32 项。')
    spec = normalize(rows, [])
    for row in spec:
        path = row['field']
        if not isinstance(path,str) or len(path)>200:
            raise Invalid('字段路径最多 200 个字符。')
        field({},path)
        if path.split('.')[0]=='artifacts' or any(part.casefold() in PRIVATE for part in path.split('.')):
            raise Invalid('产物引用或私有字段不能作为普通字段合同。')
    return spec


def select_fields(output, spec, max_chars):
    value = public_data(output)
    try:
        check(value,spec)
    except OverflowError as error:
        raise Invalid('交接字段的数值超过可核验范围。') from error
    selected = {}
    for row in sorted(spec, key=lambda item:item['field'].count('.')):
        path = row['field']
        parts = path.split('.')
        source, target = value, selected
        for part in parts:
            if not isinstance(source,dict) or part not in source:
                break
            source = source[part]
        else:
            for part in parts[:-1]:
                target = target.setdefault(part,{})
            target[parts[-1]] = field(value,path)
    return transfer(selected,dict(transfer='result',max_chars=max_chars),with_truncation=True)


def has_output_contract(node):
    return bool(node.get('output_contract') or node.get('artifact_paths'))


def output_instruction(node, provider):
    suffix = ('最终可见回复必须仅为一个 JSON 对象，包含 result 对象和 artifacts 路径数组。'
              if provider=='claude' else '在 result_json 中填写 JSON 对象，在 artifacts 中报告路径。')
    return STRUCTURED + suffix + '\n' + canonical(dict(fields=node.get('output_contract',[]),
        allowed_artifact_paths=node.get('artifact_paths',[]))).decode()


def strict_object(raw):
    def unique(pairs):
        value = dict(pairs)
        if len(value)!=len(pairs):
            raise ValueError('duplicate')
        return value
    def invalid_constant(_):
        raise ValueError('nonfinite')
    if not isinstance(raw,str):
        raise Invalid('结构化输出需要 JSON 文本。')
    try:
        if len(raw.encode())>80000:
            raise Invalid('结构化输出超过 80000 字节。')
        value = json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
        if not isinstance(value,dict):
            raise ValueError()
        # Reject JSON exponents that became infinities and unsupported Unicode.
        json.dumps(value,allow_nan=False,ensure_ascii=False).encode()
    except Invalid:
        raise
    except (ValueError,TypeError,RecursionError) as error:
        raise Invalid('实际回复中的结构化数据需要是有效 JSON 对象。') from error
    return value


def artifact_scope(root, cwd, relative):
    if not (Path(root) / relative).is_relative_to(cwd):
        raise Invalid('产物路径不在该 Agent 固定工作目录内。')


def observed_artifacts(store, workspace, cwd, paths, allowed, provenance):
    paths = file_paths(paths, MAX_ARTIFACTS)
    if set(paths)-set(allowed):
        raise Invalid('实际回复声明了未获准的产物路径。')
    root = store.workspace(workspace)['path']
    view = RunInspector(store)
    literals = task_literals()
    result = []
    for path in paths:
        artifact_scope(root,cwd,path)
        if view.text(redact(path,literals))!=path:
            raise Invalid('产物路径包含受保护内容。')
        proof = read_project(store,workspace,path,fingerprint=True)
        result.append(dict(path=path,sha256=proof['sha256'],bytes=proof['bytes'],**provenance))
    return result


def structured_output(store, run, row, node, binding, cwd, receipt, output):
    provider = binding['provider']
    try:
        if provider=='claude':
            raw = receipt['report']['reply'].strip()
            lines = raw.splitlines()
            if len(lines)>=3 and lines[0] in ('```json','```') and lines[-1]=='```':
                raw = '\n'.join(lines[1:-1])
            report = strict_object(raw)
            if set(report)!={'result','artifacts'} or not isinstance(report['result'],dict):
                raise Invalid('Claude 可见回复需要 result 对象和 artifacts 数组。')
            structured, paths = report['result'], report['artifacts']
        else:
            structured = strict_object(receipt['report']['result_json'])
            paths = receipt['report']['artifacts']
        view = RunInspector(store)
        output['result'] = view.clean(public_data(structured,task_literals()))
        check(output,node.get('output_contract',[]))
        output['artifacts'] = observed_artifacts(store,run['workspace'],cwd,paths,node.get('artifact_paths',[]),
            dict(workspace=run['workspace'],run=run['id'],node=row['node'],activation=row['id'],
                 provider=provider,session_id=receipt['session_id']))
        return {'state':'PASSED'}
    except (Invalid,Blocked,Conflict,OverflowError) as error:
        return {'state':'FAILED','reason':RunInspector(store).text(str(error))}


def verify_artifacts(store, workspace, run, source, artifacts, cwd=None):
    if not isinstance(artifacts,list) or not artifacts:
        raise Invalid('上游没有提供已核验的产物引用。')
    root = store.workspace(workspace)['path']
    for item in artifacts:
        receipt = source.get('receipt',{})
        if (not source.get('receipt_verified') or source.get('state')!='COMPLETED'
                or (item['workspace'],item['run'],item['activation'],item['node'],item['provider'],item['session_id']) != (
                    workspace,run,source['id'],source['node'],source.get('provider'),receipt.get('session_id'))):
            raise Invalid('产物来源与上游已核验激活不匹配。')
        if cwd is not None:
            artifact_scope(root,cwd,item['path'])
        proof = read_project(store,workspace,item['path'],fingerprint=True)
        if any(item[key]!=proof[key] for key in ('sha256','bytes')):
            raise Conflict('上游产物自记录后已变化，已停止本次交接。')
