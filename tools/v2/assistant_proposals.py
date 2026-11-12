"""Compile an untrusted assistant reply into a validated V2 draft preview."""
import json
import re

from studio.agent_sessions import safe_text
from studio.graph import Invalid

from .canvas_structure import EDGE_EXTENSION, ID, fields, node_kind, text
from .development_graph import contains_development
from .documents import validate_draft
from .native_history_config import unique_pairs

MAX_REPLY_BYTES = 60_000
MAX_OPERATIONS = 20
NODE_KINDS = {'start', 'agent', 'condition', 'router', 'merge', 'loop', 'approval', 'validation', 'output'}
OP_FIELDS = {
    'add-node': ({'kind', 'name', 'config'}, {'kind', 'name'}),
    'update-node': ({'name', 'config'}, set()),
    'remove-node': (set(), set()),
    'connect': ({'source', 'target'} | EDGE_EXTENSION, {'source', 'target'}),
    'remove-edge': (set(), set()),
}
PROPOSAL_INSTRUCTIONS = """只返回一个 JSON 对象，包含 summary（简短中文说明）和 operations（1–20 条有序修改）。
每条修改包含 op 与 id。可用操作：
add-node 增加 kind、name、可选 config。kind 为 start、agent、condition、router、merge、loop、approval、validation 或 output。
update-node 只接受 name 或要合并的 config。remove-node 移除节点及相邻边。
connect 使用新的边 id，并填写 source、target、可选 source_port、target_port、transfer。
remove-edge 删除指定边。ID 以字母开头，只使用字母、数字、下划线和连字符。
已有节点和端口使用提供的身份。新 Agent 只能是未绑定草稿，已有 Agent 只可改名。
保留现有工具、Agent 权限、循环子图及人工审批。含已有审批的图只提议改名。"""


def _reject_constant(_):
    raise ValueError()


def parse_envelope(reply):
    try:
        if not isinstance(reply, str) or len(reply.encode('utf-8')) > MAX_REPLY_BYTES:
            raise ValueError()
        raw = reply.strip()
        fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', raw, flags=re.S | re.I)
        result = json.loads(fenced[1] if fenced else raw, object_pairs_hook=unique_pairs,
                            parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Invalid('助手没有返回有效且有界的 JSON 提案。') from None
    return result


def _parse(reply):
    result = parse_envelope(reply)
    fields(result, {'summary', 'operations'}, {'summary', 'operations'})
    text(result['summary'], 1000, '提案说明')
    if not result['summary'].strip() or not isinstance(result['operations'], list) or not (
            1 <= len(result['operations']) <= MAX_OPERATIONS):
        raise Invalid('提案需要简短说明和 1–20 条修改。')
    return result


def _has_approval(graph):
    return any(node_kind(node) == 'approval' or
               node_kind(node) == 'loop' and _has_approval(node['config'].get('body') or {'nodes': []})
               for node in graph['nodes'])


def _position(nodes):
    for slot in range(128):
        x, y = 80 + slot % 4 * 300, 120 + slot // 4 * 210
        if all(abs(node['position']['x'] - x) >= 240 or abs(node['position']['y'] - y) >= 160
               for node in nodes):
            return {'x': x, 'y': y}
    raise Invalid('没有可用的新增节点位置。')


def _config(operation, kind):
    config = operation.get('config', {})
    if not isinstance(config, dict):
        raise Invalid('节点配置必须是对象。')
    if kind in ('agent', 'tool') and config:
        raise Invalid('助手提案不能更改 Agent 绑定或工具配置。')
    if kind == 'loop' and 'body' in config:
        raise Invalid('循环子流程需要在画布中明确选择。')
    if kind == 'approval' and config.get('policy', 'single-reviewer') != 'single-reviewer':
        raise Invalid('提案中的人工审批必须采用本机单审核者策略。')
    return config


def _name(value):
    text(value, 160, '节点名称')
    name = safe_text(value).strip()
    if not name:
        raise Invalid('请填写提案节点的名称。')
    return name


def proposal(base_document, reply_text):
    """Preserve the base and validate the final graph before presenting a diff."""
    value = _parse(reply_text)
    candidate = validate_draft(base_document)
    protected = _has_approval(candidate) or contains_development(candidate)
    used_nodes = {node['id'] for node in candidate['nodes']}
    used_edges = {edge['id'] for edge in candidate['edges']}
    for operation in value['operations']:
        op = operation.get('op') if isinstance(operation, dict) else None
        if not isinstance(op, str) or op not in OP_FIELDS:
            raise Invalid('助手提案包含不支持的修改操作。')
        allowed, required = OP_FIELDS[op]
        fields(operation, {'op', 'id'} | allowed, {'op', 'id'} | required)
        ident = operation['id']
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise Invalid('提案节点或连线身份无效。')
        if protected and (op != 'update-node' or 'config' in operation):
            raise Invalid('已有人工审批或受保护交付 Gate 的流程只允许助手改名。请在画布中明确调整结构或配置。')

        nodes, edges = candidate['nodes'], candidate['edges']
        if op == 'add-node':
            kind = operation['kind']
            if ident in used_nodes or not isinstance(kind, str) or kind not in NODE_KINDS:
                raise Invalid('新增节点的身份已使用，或其类型需要在画布中明确配置。')
            config = _config(operation, kind)
            node = dict(id=ident, name=_name(operation['name']), kind=kind, position=_position(nodes))
            if kind != 'agent':
                node.update(config_version=1, config=config)
            nodes.append(node)
            used_nodes.add(ident)
        elif op in ('update-node', 'remove-node'):
            node = next((row for row in nodes if row['id'] == ident), None)
            if node is None:
                raise Invalid('提案引用了当前草稿中不存在的节点。')
            kind = node_kind(node)
            if op == 'remove-node':
                if kind in ('tool', 'approval'):
                    raise Invalid('工具与人工审批节点需要在画布中明确调整。')
                nodes.remove(node)
                candidate['edges'] = [edge for edge in edges if ident not in (edge['source'], edge['target'])]
            else:
                if 'name' in operation:
                    node['name'] = _name(operation['name'])
                config = _config(operation, kind)
                if config:
                    if kind == 'approval':
                        raise Invalid('人工审批配置需要在画布中明确调整。')
                    node['config'].update(config)
        elif op == 'connect':
            if ident in used_edges:
                raise Invalid('新增连线身份已使用。')
            edge = {key: value for key, value in operation.items() if key != 'op'}
            edge.setdefault('source_port', 'out')
            edge.setdefault('target_port', 'in')
            edges.append(edge)
            used_edges.add(ident)
        else:
            edge = next((row for row in edges if row['id'] == ident), None)
            if edge is None:
                raise Invalid('提案引用了当前草稿中不存在的连线。')
            edges.remove(edge)

    document = validate_draft(candidate)
    if document == base_document:
        raise Invalid('提案没有产生可预览的草稿变更。')
    return {'summary': safe_text(value['summary']).strip(), 'document': document}
