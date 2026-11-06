"""Bounded draft structure, deliberately independent of execution validation."""
import copy
import json
import math
import re

from studio.graph import Invalid

MAX_BYTES = 400000
ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise Invalid('草稿字段缺失或不受支持')


def text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise Invalid(label + '的类型或长度无效')


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise Invalid('画布坐标或缩放无效')


def validate_draft(document):
    fields(document, {'version', 'name', 'nodes', 'edges', 'viewport'}, {'version', 'name', 'nodes', 'edges'})
    if type(document['version']) is not int or document['version'] != 1:
        raise Invalid('请选择 V2 草稿格式 version 1；旧工作流不会自动迁移')
    try:
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise Invalid('草稿包含不支持的 JSON 数据') from None
    if len(raw) > MAX_BYTES:
        raise Invalid('草稿超过 400000 字节预算')
    text(document['name'], 160, '工作流名称')
    if not document['name'].strip():
        raise Invalid('请填写工作流名称')
    if not isinstance(document['nodes'], list) or len(document['nodes']) > 64:
        raise Invalid('草稿最多保存 64 个 Agent')
    if not isinstance(document['edges'], list) or len(document['edges']) > 128:
        raise Invalid('草稿连线数量超限')
    nodes, edges, pairs = set(), set(), set()
    for node in document['nodes']:
        fields(node, {'id', 'name', 'position', 'provider', 'model', 'effort', 'initial_prompt',
                      'round_prompt', 'output_prompt', 'permission', 'timeout'}, {'id', 'name', 'position'})
        if not isinstance(node['id'], str) or not ID.fullmatch(node['id']) or node['id'] in nodes:
            raise Invalid('Agent 身份无效或重复')
        nodes.add(node['id'])
        text(node['name'], 160, 'Agent 名称')
        fields(node['position'], {'x', 'y'}, {'x', 'y'})
        for value in node['position'].values():
            number(value, -100000, 100000)
        for key in ('provider', 'model', 'effort'):
            if key in node:
                text(node[key], 200, key)
        if node.get('provider', '') not in ('', 'codex'):
            raise Invalid('本期仅支持 Codex；未配置可留空')
        for key in ('initial_prompt', 'round_prompt', 'output_prompt'):
            if key in node:
                text(node[key], 20000, key)
        if node.get('permission', 'read') != 'read':
            raise Invalid('当前阶段仅支持只读权限')
        if 'timeout' in node and (type(node['timeout']) is not int or not 10 <= node['timeout'] <= 3600):
            raise Invalid('超时需要 10–3600 秒的整数')
    for edge in document['edges']:
        fields(edge, {'id', 'source', 'target'}, {'id', 'source', 'target'})
        if not isinstance(edge['id'], str) or not ID.fullmatch(edge['id']) or edge['id'] in edges:
            raise Invalid('连线身份无效或重复')
        edges.add(edge['id'])
        if not isinstance(edge['source'], str) or not isinstance(edge['target'], str):
            raise Invalid('连线端点需要 Agent 身份')
        pair = edge['source'], edge['target']
        if pair[0] not in nodes or pair[1] not in nodes or pair[0] == pair[1] or pair in pairs:
            raise Invalid('连线端点缺失、自连接或重复')
        pairs.add(pair)
    if 'viewport' in document:
        fields(document['viewport'], {'x', 'y', 'zoom'}, {'x', 'y', 'zoom'})
        number(document['viewport']['x'], -100000, 100000)
        number(document['viewport']['y'], -100000, 100000)
        number(document['viewport']['zoom'], .2, 2)
    # No normalization, implicit technical nodes, Provider lookup or execution check.
    return copy.deepcopy(document)


def check_chain(document):
    """Pure draft diagnostics. M1 does not freeze, launch or call a Provider."""
    doc = validate_draft(document)
    issues = []
    if not doc['nodes']:
        issues.append({'message': '添加至少一个 Agent 后再检查执行配置'})
    incoming = {n['id']: [] for n in doc['nodes']}
    outgoing = {n['id']: [] for n in doc['nodes']}
    for edge in doc['edges']:
        incoming[edge['target']].append(edge['source'])
        outgoing[edge['source']].append(edge['target'])
    for node in doc['nodes']:
        for key, label in [('provider', 'Provider'), ('model', '模型'), ('effort', '推理强度'), ('round_prompt', '每轮提示词')]:
            if not node.get(key, '').strip():
                issues.append({'node_id': node['id'], 'field': key, 'message': node['name'] + '：请配置' + label})
        if len(incoming[node['id']]) > 1 or len(outgoing[node['id']]) > 1:
            issues.append({'node_id': node['id'], 'message': '本期仅支持顺序 Agent 链，不能分支或汇合'})
    roots = [key for key in incoming if not incoming[key]]
    seen, pending = set(), list(roots)
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        pending.extend(outgoing[key])
    if doc['nodes'] and (len(roots) != 1 or len(seen) != len(doc['nodes']) or len(doc['edges']) != len(doc['nodes']) - 1):
        issues.append({'message': '将所有 Agent 连接成一条无环顺序链；不完整草稿仍可保存'})
    return {'issues': issues, 'execution_available': False,
            'message': 'M1 编辑与保存阶段；真实执行将在 M2 接入。配置检查不会调用模型。'}
