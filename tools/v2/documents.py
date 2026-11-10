"""Bounded draft structure, deliberately independent of execution validation."""
import copy
import json
import re

from studio.graph import Invalid

from .canvas_structure import (EDGE_EXTENSION, ID, TRANSFER_MODES, TYPES, config_issues, connection_message,
                               fields, node_kind, number, text, validate_config)

MAX_BYTES = 400000
def validate_draft(document, *, depth=0):
    if depth > 2:
        raise Invalid('循环子流程最多嵌套两层。')
    fields(document, {'version', 'name', 'nodes', 'edges', 'viewport'}, {'version', 'name', 'nodes', 'edges'})
    if type(document['version']) is not int or document['version'] != 1:
        raise Invalid('请选择 V2 草稿格式 version 1；旧工作流不会自动迁移')
    try:
        raw = json.dumps(document, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Invalid('草稿包含不支持的 JSON 数据') from None
    if len(raw) > MAX_BYTES:
        raise Invalid('草稿超过 400000 字节预算')
    text(document['name'], 160, '工作流名称')
    if not document['name'].strip():
        raise Invalid('请填写工作流名称')
    if not isinstance(document['nodes'], list) or len(document['nodes']) > 64:
        raise Invalid('草稿最多保存 64 个节点')
    if not isinstance(document['edges'], list) or len(document['edges']) > 128:
        raise Invalid('草稿连线数量超限')
    nodes, edges, pairs = {}, set(), set()
    typed = any(isinstance(node, dict) and 'kind' in node for node in document['nodes'])
    typed = typed or any(isinstance(edge, dict) and EDGE_EXTENSION & edge.keys() for edge in document['edges'])
    for node in document['nodes']:
        if not isinstance(node, dict):
            raise Invalid('节点必须是对象。')
        kind = node_kind(node)
        if not isinstance(kind, str) or kind not in TYPES:
            raise Invalid('节点类型无效或尚未支持。')
        reference = 'agent_id' in node or 'agent_revision' in node
        required = {'id', 'name', 'position'}
        if kind == 'agent':
            if reference:
                config = {'agent_id', 'agent_revision'}
            elif 'kind' in node:
                config = set()
            else:
                config = {'provider', 'model', 'effort', 'initial_prompt', 'round_prompt',
                          'output_prompt', 'permission', 'timeout'}
        else:
            config = {'config_version', 'config'}
            required |= config
        fields(node, {'id', 'name', 'position', 'kind'} | config, required)
        if not isinstance(node['id'], str) or not ID.fullmatch(node['id']) or node['id'] in nodes:
            raise Invalid('节点身份无效或重复')
        nodes[node['id']] = node
        text(node['name'], 160, '节点名称')
        fields(node['position'], {'x', 'y'}, {'x', 'y'})
        for value in node['position'].values():
            number(value, -100000, 100000)
        if kind != 'agent':
            validate_config(node)
            if kind == 'loop' and 'body' in node['config']:
                validate_draft(node['config']['body'], depth=depth + 1)
            continue
        if reference:
            if any(not isinstance(node.get(key), str) or not re.fullmatch(r'[0-9a-f]{' + str(length) + '}', node[key])
                   for key, length in (('agent_id', 32), ('agent_revision', 64))):
                raise Invalid('工作流 Agent 引用需要完整配置身份与版本')
            continue
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
    previous = []
    for edge in document['edges']:
        fields(edge, {'id', 'source', 'target'} | EDGE_EXTENSION, {'id', 'source', 'target'})
        if not isinstance(edge['id'], str) or not ID.fullmatch(edge['id']) or edge['id'] in edges:
            raise Invalid('连线身份无效或重复')
        edges.add(edge['id'])
        if not isinstance(edge['source'], str) or not isinstance(edge['target'], str):
            raise Invalid('连线端点需要节点身份。')
        for name in ('source_port', 'target_port'):
            if name in edge and (not isinstance(edge[name], str) or not ID.fullmatch(edge[name])):
                raise Invalid('连线端口身份无效。')
        if 'transfer' in edge:
            transfer = edge['transfer']
            fields(transfer, {'mode', 'max_chars'}, {'mode', 'max_chars'})
            if (not isinstance(transfer['mode'], str) or transfer['mode'] not in TRANSFER_MODES
                    or type(transfer['max_chars']) is not int or not 1 <= transfer['max_chars'] <= 20000):
                raise Invalid('连线交接模式或长度上限无效。')
        if typed:
            if problem := connection_message(edge, nodes, previous):
                raise Invalid(problem)
            previous.append(edge)
        else:
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
    """Pure draft diagnostics, independent of Provider admission."""
    from .designer_graph import graph_issues, is_designer_graph
    doc = validate_draft(document)
    if is_designer_graph(doc):
        return {'issues': graph_issues(doc), 'execution_available': False,
                'message': '检查结构、端口与节点配置。检查不会调用模型。'}
    issues = []
    if not doc['nodes']:
        issues.append({'message': '添加至少一个 Agent 后再检查执行配置'})
    for node in doc['nodes']:
        if node.get('kind') == 'agent' and 'agent_id' not in node:
            issues.append(dict(node_id=node['id'], code='AGENT_REFERENCE_REQUIRED',
                               message=node['name'] + '：请在 Agent 管理选择已保存的配置版本。'))
        for field, message in config_issues(node):
            issues.append(dict(node_id=node['id'], field=field, message=node['name'] + '：' + message))
    incoming = {n['id']: [] for n in doc['nodes']}
    outgoing = {n['id']: [] for n in doc['nodes']}
    for edge in doc['edges']:
        incoming[edge['target']].append(edge['source'])
        outgoing[edge['source']].append(edge['target'])
    for node in doc['nodes']:
        if 'agent_id' not in node and 'kind' not in node:
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
