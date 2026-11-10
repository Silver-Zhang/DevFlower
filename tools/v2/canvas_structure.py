"""Phase 4 document types and edge policy. No execution or Provider authority."""
import json
import math
import re
from pathlib import Path

from studio.graph import Invalid

SCHEMA_PATH = Path(__file__).with_name('canvas_types.json')
TYPES = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
ROUTE_OPERATORS = {'equals', 'not_equals', 'contains', 'greater', 'less', 'exists'}
TRANSFER_MODES = {'answer': '最终回答', 'summary': '摘要', 'result': '结构化结果', 'none': '仅控制顺序'}
DEFAULT_TRANSFER = {'mode': 'answer', 'max_chars': 16000}
EDGE_EXTENSION = {'source_port', 'target_port', 'transfer'}
ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise Invalid('草稿字段缺失或不受支持')


def text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise Invalid(label + '的类型或长度无效')


def number(value, low, high):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise Invalid('画布坐标或缩放无效')


def node_kind(node):
    return node.get('kind', 'agent')


def node_ports(node):
    spec = TYPES[node_kind(node)]
    outputs = dict(spec['outputs'])
    if node_kind(node) == 'router':
        outputs.update((rule['id'], rule['label']) for rule in node['config'].get('rules', []))
    return spec['inputs'], outputs


def endpoints(edge):
    return edge['source'], edge.get('source_port', 'out'), edge['target'], edge.get('target_port', 'in')


def connection_message(edge, nodes, previous):
    source, source_port, target, target_port = endpoints(edge)
    if source not in nodes or target not in nodes:
        return '连线端点缺失，请选择画布中的节点。'
    if source == target:
        return '节点不能连接到自身。'
    if source_port not in node_ports(nodes[source])[1]:
        return '源节点没有所选输出端口。'
    if target_port not in node_ports(nodes[target])[0]:
        return '目标节点没有所选输入端口。'
    if any(endpoints(old) == (source, source_port, target, target_port) for old in previous):
        return '这些端口之间已有连线。'
    if node_kind(nodes[target]) != 'merge' and any(endpoints(old)[2:] == (target, target_port) for old in previous):
        return '此输入端口只接收一条连线。需要多个上游时请使用汇合节点。'
    if node_kind(nodes[source]) not in ('start', 'agent') and any(
            endpoints(old)[:2] == (source, source_port) for old in previous):
        return '此输出端口只允许一条连线，请选择其他分支端口。'
    outgoing = {}
    for old in previous:
        outgoing.setdefault(old['source'], []).append(old['target'])
    seen, pending = set(), [target]
    while pending:
        node = pending.pop()
        if node == source:
            return '这条连线会形成循环。请用循环节点配置迭代。'
        if node not in seen:
            seen.add(node)
            pending.extend(outgoing.get(node, []))
    return None


def validate_predicate(test):
    fields(test, {'field', 'op', 'value'}, {'field', 'op', 'value'})
    text(test['field'], 200, '判定字段')
    if not isinstance(test['op'], str) or test['op'] not in ROUTE_OPERATORS:
        raise Invalid('判定运算符无效。')
    if isinstance(test['value'], str):
        text(test['value'], 2000, '比较值')
    elif type(test['value']) is not bool:
        number(test['value'], -1e100, 1e100)


def validate_config(node):
    kind = node_kind(node)
    if type(node.get('config_version')) is not int or node['config_version'] != 1:
        raise Invalid('请选择节点配置 version 1。')
    config, spec = node['config'], TYPES[kind]['fields']
    fields(config, set(spec) | ({'rules'} if kind == 'router' else set()))
    for key, value in config.items():
        if kind == 'router' and key == 'rules':
            if not isinstance(value, list) or len(value) > 16:
                raise Invalid('路由最多支持 16 条规则。')
            ids = set()
            for rule in value:
                fields(rule, {'id', 'label', 'test'}, {'id', 'label', 'test'})
                rule_id = rule['id']
                if (not isinstance(rule_id, str) or not ID.fullmatch(rule_id)
                        or rule_id == 'fallback' or rule_id in ids):
                    raise Invalid('路由分支身份无效或重复。')
                ids.add(rule_id)
                text(rule['label'], 100, '路由分支名称')
                validate_predicate(rule['test'])
            continue
        setting = spec[key]
        if setting['type'] == 'text':
            text(value, 20000, setting['label'])
        elif setting['type'] == 'predicate':
            validate_predicate(value)
        elif setting['type'] == 'integer':
            if type(value) is not int:
                raise Invalid(setting['label'] + '需要整数。')
            number(value, setting['min'], setting['max'])
        elif not isinstance(value, str) or value not in setting['options']:
            raise Invalid(setting['label'] + '无效。')


def reject_constant(value):
    raise ValueError(value)


def config_issues(node):
    kind = node_kind(node)
    config = node.get('config', {})
    issues = []
    for key, setting in TYPES[kind]['fields'].items():
        if setting['type'] == 'predicate':
            test = config.get(key)
            if not test or not test['field'].strip():
                issues.append((key, '请填写' + setting['label'] + '的判定字段。'))
            elif test['op'] in ('greater', 'less') and type(test['value']) not in (int, float):
                issues.append((key, '数值比较需要数字比较值。'))
        elif setting.get('required') and not config.get(key, '').strip():
            issues.append((key, '请填写' + setting['label'] + '。'))
    if kind == 'start' and config.get('initial_data', '').strip():
        try:
            json.loads(config['initial_data'], parse_constant=reject_constant)
        except (ValueError, RecursionError):
            issues.append(('initial_data', '初始数据需要有效的 JSON。'))
    if kind == 'router':
        rules = config.get('rules', [])
        if not rules:
            issues.append(('rules', '请添加至少一条路由规则。'))
        for rule in rules:
            test = rule['test']
            if not rule['label'].strip() or not test['field'].strip():
                issues.append(('rules', '请填写路由分支名称与字段。'))
                break
            if test['op'] in ('greater', 'less') and type(test['value']) not in (int, float):
                issues.append(('rules', '数值比较需要数字比较值。'))
                break
    if kind == 'tool' and config.get('tool'):
        scopes = ('workspace-read', 'workspace-write') if config['tool'] == 'read-file' else ('workspace-write',)
        if config.get('permission', 'none') not in scopes:
            issues.append(('permission', '选择与工具一致的工作区权限。'))
    return issues


def port_issues(document):
    connected_in, connected_out = {}, set()
    for edge in document['edges']:
        source, source_port, target, target_port = endpoints(edge)
        connected_out.add((source, source_port))
        connected_in[(target, target_port)] = connected_in.get((target, target_port), 0) + 1
    issues = []
    for node in document['nodes']:
        kind = node_kind(node)
        if kind == 'agent':
            continue
        ident = node['id']
        inputs, outputs = node_ports(node)
        for port, label in inputs.items():
            if connected_in.get((ident, port), 0) < (2 if kind == 'merge' else 1):
                issues.append(dict(node_id=ident, message=node['name'] + '：输入端口“' + label + '”缺少上游连线。'))
        for port, label in outputs.items():
            if (ident, port) not in connected_out:
                issues.append(dict(node_id=ident, message=node['name'] + '：输出端口“' + label + '”尚未连接。'))
    return issues


def runtime_issues(document):
    issues = [dict(node_id=node['id'], code='PHASE5_NODE', message=node['name'] + '：' + TYPES[node_kind(node)]['label'] +
                   '已可编辑；执行由 Phase 5 接入。')
              for node in document['nodes'] if node_kind(node) != 'agent']
    for edge in document['edges']:
        if edge.get('transfer', DEFAULT_TRANSFER) != DEFAULT_TRANSFER:
            issues.append(dict(edge_id=edge['id'], code='PHASE5_TRANSFER', message='自定义交接已可编辑；执行由 Phase 5 接入。'))
    return issues
