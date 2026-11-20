"""Compile an untrusted assistant reply into a validated V2 draft preview."""
import json
import re

from studio.agent_sessions import safe_text
from studio.graph import Invalid

from .canvas_structure import EDGE_EXTENSION, ID, TYPES, fields, node_kind, text
from .development_graph import contains_development
from .documents import validate_draft
from .native_history_config import unique_pairs
from .workflow_templates import loop_example, router_example

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
OPERATION_INSTRUCTIONS = """每条修改包含 op 与 id。可用操作：
add-node 增加 kind、name、可选 config。
update-node 只接受 name 或要合并的 config。remove-node 移除节点及相邻边。
connect 使用新的边 id，并填写 source、target、可选 source_port、target_port、transfer。
remove-edge 删除指定边。ID 以字母开头，只使用字母、数字、下划线和连字符。
在已有连线上插入节点时，先移除原连线，保留两端所用端口，并明确填写两条新连线的端口。
路由输出使用规则 id 或 fallback。validation 的 config.rules 为 JSON 条件文本，例如 {"field":"result.passed","op":"equals","value":true}。
"""
PORTS = '\n'.join(
    f"{kind}: {', '.join(TYPES[kind]['inputs']) or '无'} → {', '.join(TYPES[kind]['outputs']) or '无'}"
    for kind in sorted(NODE_KINDS)
)
PROPOSAL_INSTRUCTIONS = """只返回一个 JSON 对象，包含 summary（简短中文说明）和 operations（1–20 条有序修改）。
add-node 的 kind 为 start、agent、condition、router、merge、loop、approval、validation 或 output。
已有节点和端口使用提供的身份。新 Agent 只能是未绑定草稿，已有 Agent 只可改名。
保留现有工具、Agent 权限、循环子图及人工审批。含已有审批的图只提议改名。
""" + OPERATION_INSTRUCTIONS + '\n新增节点端口（输入 → 输出）：\n' + PORTS


def native_examples():
    router = router_example()
    agent, control = router['nodes'][1:3]
    classifier = {key: agent[key] for key in ('task_prompt', 'native_output', 'output_contract')}
    classifier['profile'] = 'Classifier'
    loop = loop_example()['nodes'][1]['config']
    # The native proposal uses profile aliases and retains the exact template graph shape.
    for node in loop['body']['nodes']:
        if node['kind'] == 'agent':
            settings = {key: node.pop(key) for key in ('task_prompt', 'native_output', 'artifact_paths') if key in node}
            node['config'] = dict(settings, profile={'Agent': 'Producer', 'Review': 'Reviewer'}[node['id']])
    return dict(router_agent_config=classifier, router_config=control['config'], loop_config=loop)


NATIVE_INSTRUCTIONS = """根据用户的意思只返回一个 JSON 对象。若用户问答或询问运行原因，返回 {"answer":"Markdown 回答"}，
运行事实引用所给 Run ID、节点、轮次和真实事件。证据未覆盖之处明确说明；绝不发起执行或重放。
若用户明确要求创建或修改当前原生工作流，返回 summary、operations，可选 name（画布名称）与 profiles（拟新增的原生 Agent）。
只使用本次当前草稿身份；以前问答仅用于理解指代。保留用户未要求变更的节点和配置。
summary 为不超过 1000 字符的单个字符串，
禁止数组、对象或 Markdown 分段。仅改工作流名称时用 name 和空的 operations 数组。
修改已有节点时用 update-node 和此节点的原 id；add-node 必须选当前图中及本次操作内都未使用的节点 id。
调整已有 Loop 的轮次时只更新该 Loop 的 config.max_iterations，保留 body 及其他配置。
单 Tool 可以编译，无通用 Start 要求。以提供的实际编译检查为准；修改后是否可编译由新检查确定。遵循下面的操作、ID 与端口规则，但原生模式允许下列额外配置：
- Agent 的 config 可含 task_prompt、native_output、output_contract、artifact_paths，以及一个 profile。
- 优先复用列表中职责合适的原生 Agent，profile 为其 Agent ID。需要新配置时，profiles 提供 id（别名）、
  name、role、provider（codex 或 claude），profile 引用别名。优先遵循用户指定的 Provider，否则使用默认 Codex。
  用节点 task_prompt 写具体任务；勿创建强制的开发/测试/审核团队。
- Tool 支持 config={"tool":"command","argv":["python3","-m","unittest"],"cwd":".","timeout":300,"permission":"none"}。
  add-node 的 config 只填写这些节点参数。编辑器自动添加 config_version，不要在操作或 config 中填写它。
  只有用户要求的命令或验证步骤才提议命令；命令作为数据供用户审核，不会立即执行。
- 普通边省略 transfer，使用默认交接。不能填写 transfer:"default"。自定义交接请提供支持的 transfer 对象。
  Router 直接接收结构化结果 Agent 的 result.* 已声明字段，
  每条规则为 {"id":"High","label":"…","test":{"field":"result.data.score","op":"equals","value":true}}，
  每规则和 fallback 各连接至独立 Output。output_contract 必须是数组，例如 [{"field":"result.data.score","type":"boolean","required":true}]。
- 候选审核 Condition 的编译形状为 Start → 产物 Agent → 审核 Agent → Condition。
  当用户的任务明确指定交付 report.txt 时，产物 Agent 的 config 为 {"native_output":{"version":1,"kind":"result"},
  "artifact_paths":["report.txt"],"task_prompt":"生成并申报 report.txt"}；产物列表必须非空，路径应来自实际任务要求。
  产物 Agent 用普通数据交接直接连接到 config 含 {"native_output":{"version":1,"kind":"review"}} 的审核 Agent。
  审核 Agent 直接连到 Condition；config.expression 直接嵌入对象 {"field":"result.verdict","op":"equals","value":"accept"}，
  禁止再编码为字符串。true 和 false 端口都要连接，每条分支都要到达不同的 Output。
- Loop config 可含 max_iterations、exit_condition 与完整的 version 1 body（name/nodes/edges）。body 节点使用
  id/name/kind/position，Agent 的 config 同上；其他节点使用 config_version=1 和 config。
  原生 Loop 为 Start → 产物 Agent → 可选命令 Tool → 审核 Agent → Output，exit_condition 是
  {"field":"result.verdict","op":"equals","value":"accept"}。产物 Agent 要声明真实工作区相对路径和 native_output
  kind=result，并在 task_prompt 要求生成该产物。审核 Agent 使用 native_output kind=review。
- 当前原生脚本编译器不支持并行 Agent、Merge / 分支汇合、嵌套控制节点，以及同一图里串联或混用多个 Condition、Router、Loop。
  任一层已有 Condition 或 Router 时，在该工作流新增 Loop 只能形成设计草稿。不要删除现有控制结构来改变这一限制。
  这些结构可保存为设计草稿。若用户要求其中之一，明确说明当前原生 Python 不可编译或执行；保留其设计，由编译器报告限制。
- 没有当前草稿时只回答，提示可返回来源草稿后再请求修改。
原有 Agent 权限、已有人工审批和受保护 Gate 继续保留。
""" + OPERATION_INSTRUCTIONS + '\n可用节点端口（输入 → 输出）：\n' + PORTS + (
    '\ntool: in → out\n原生配置示例（示例中的 profile 为别名，请声明新配置或替换成已给出的配置 ID）：\n'
    + json.dumps(native_examples(), ensure_ascii=False, separators=(',', ':'))
)


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


def _parse(reply, native=False):
    result = parse_envelope(reply)
    required = {'summary', 'operations'}
    fields(result, required | ({'profiles', 'name'} if native else set()), required)
    if not isinstance(result['summary'], str) or not 0 < len(result['summary'].strip()) <= 1000:
        raise Invalid('summary：请用一个简短字符串说明修改（最多 1000 字符）。')
    text(result['summary'], 1000, 'summary')
    minimum = 0 if native and 'name' in result and not result.get('profiles') else 1
    if not isinstance(result['operations'], list) or not minimum <= len(result['operations']) <= MAX_OPERATIONS:
        raise Invalid(f'operations：请提供 {minimum}–20 条有序修改；仅改工作流名称时使用 name。')
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


def _config(operation, kind, native):
    config = operation.get('config', {})
    if not isinstance(config, dict):
        raise Invalid('节点配置必须是对象。')
    if not native and kind in ('agent', 'tool') and config:
        raise Invalid('助手提案不能更改 Agent 绑定或工具配置。')
    if kind == 'loop' and 'body' in config:
        if not native:
            raise Invalid('循环子流程需要在画布中明确选择。')
        native_loop_body(config['body'])
    if kind == 'approval' and config.get('policy', 'single-reviewer') != 'single-reviewer':
        raise Invalid('提案中的人工审批必须采用本机单审核者策略。')
    return config


def _name(value):
    text(value, 160, '节点名称')
    name = safe_text(value).strip()
    if not name:
        raise Invalid('请填写提案节点的名称。')
    return name


def proposal(base_document, reply_text, *, native_profiles=None):
    """Preserve the base and validate the final graph before presenting a diff."""
    native = native_profiles is not None
    value = _parse(reply_text, native)
    candidate = validate_draft(base_document)
    protected = _has_approval(candidate) or contains_development(candidate)
    if 'name' in value:
        if protected:
            raise Invalid('受保护流程请直接在画布上改名。')
        candidate['name'] = _name(value['name'])
    used_nodes = {node['id'] for node in candidate['nodes']}
    used_edges = {edge['id'] for edge in candidate['edges']}
    for index, operation in enumerate(value['operations'], 1):
        try:
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
                if ident in used_nodes:
                    raise Invalid('add-node 的 id 已使用。请用 update-node 修改已有节点，或为新增节点选择唯一 id。')
                if not isinstance(kind, str) or kind not in NODE_KINDS | ({'tool'} if native else set()):
                    raise Invalid('add-node 的 kind 不受支持，请使用提案允许的节点类型。')
                config = _config(operation, kind, native)
                node = dict(id=ident, name=_name(operation['name']), kind=kind, position=_position(nodes))
                if kind != 'agent':
                    if kind == 'tool' and config.get('tool') != 'command':
                        raise Invalid('原生助手可提议命令 Tool。其他工具可在画布中编辑。')
                    node.update(config_version=1, config=config)
                elif native:
                    node.update(native_agent_fields(config, native_profiles))
                nodes.append(node)
                used_nodes.add(ident)
            elif op in ('update-node', 'remove-node'):
                node = next((row for row in nodes if row['id'] == ident), None)
                if node is None:
                    raise Invalid('提案引用了当前草稿中不存在的节点。')
                kind = node_kind(node)
                if op == 'remove-node':
                    if kind == 'approval' or kind == 'tool' and (not native or node['config'].get('tool') != 'command'):
                        raise Invalid('工具与人工审批节点需要在画布中明确调整。')
                    nodes.remove(node)
                    candidate['edges'] = [edge for edge in edges if ident not in (edge['source'], edge['target'])]
                else:
                    if 'name' in operation:
                        node['name'] = _name(operation['name'])
                    config = _config(operation, kind, native)
                    if config:
                        if kind == 'approval':
                            raise Invalid('人工审批配置需要在画布中明确调整。')
                        if native and kind == 'agent':
                            bind = native_agent_fields(config, native_profiles)
                            if 'agent_id' in bind:
                                for key in ('provider', 'model', 'effort', 'initial_prompt', 'round_prompt',
                                            'output_prompt', 'permission', 'timeout'):
                                    node.pop(key, None)
                            node.update(bind)
                        else:
                            if kind == 'tool' and (node['config'].get('tool') != 'command' or config.get('tool', 'command') != 'command'):
                                raise Invalid('已有非命令 Tool 需要在画布中调整。')
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

        except Invalid as error:
            ident = operation.get('id') if isinstance(operation, dict) else None
            label = ' · ' + ident if isinstance(ident, str) and ID.fullmatch(ident) else ''
            raise Invalid(f'operations[{index}]{label}：{error}') from None

    if native:
        resolve_loop_profiles(candidate, native_profiles)
    try:
        document = validate_draft(candidate)
    except Invalid as error:
        raise Invalid(f'operations 应用后的结构无效：{error}。请修正相关节点与连线并保留其余有效操作。') from None
    if document == base_document:
        raise Invalid('提案没有产生可预览的草稿变更。')
    return {'summary': safe_text(value['summary']).strip(), 'document': document}


def native_agent_fields(config, profiles):
    fields(config, {'profile', 'task_prompt', 'native_output', 'output_contract', 'artifact_paths'})
    patch = dict(config)
    if isinstance(patch.get('output_contract'), dict):
        wrapper = patch['output_contract']
        fields(wrapper, {'fields'}, {'fields'})
        patch['output_contract'] = wrapper['fields']
    if 'profile' in patch:
        alias = patch.pop('profile')
        if not isinstance(alias, str) or alias not in profiles:
            raise Invalid('提案引用了当前已选范围中不存在的原生 Agent。')
        spec = profiles[alias]
        patch.update(agent_id=spec['id'], agent_revision=spec['revision'])
    return patch


def resolve_loop_profiles(document, profiles, depth=0):
    if depth > 2 or not isinstance(document, dict) or not isinstance(document.get('nodes'), list):
        raise Invalid('循环提案需要有效的画布，最多嵌套两层。')
    for node in document['nodes']:
        if not isinstance(node, dict):
            raise Invalid('循环节点无效。')
        kind = node_kind(node)
        if kind == 'agent' and 'config' in node:
            node.update(native_agent_fields(node.pop('config'), profiles))
        elif kind == 'loop' and isinstance(node.get('config'), dict) and 'body' in node['config']:
            resolve_loop_profiles(node['config']['body'], profiles, depth + 1)


def native_loop_body(document, depth=0):
    if depth > 2 or not isinstance(document, dict) or not isinstance(document.get('nodes'), list):
        raise Invalid('循环提案需要有效的画布，最多嵌套两层。')
    for node in document['nodes']:
        if not isinstance(node, dict):
            raise Invalid('循环节点无效。')
        kind, config = node_kind(node), node.get('config')
        if kind == 'approval' or kind == 'tool' and (not isinstance(config, dict) or config.get('tool') != 'command'):
            raise Invalid('循环提案只能加入原生命令 Tool；人工审批与受管工具请在画布中配置。')
        if kind == 'agent' and node.keys() - {'id', 'name', 'kind', 'position', 'config'}:
            raise Invalid('循环中的 Agent 只能引用本次提供的原生配置。')
        if kind == 'loop' and isinstance(config, dict) and 'body' in config:
            native_loop_body(config['body'], depth + 1)
