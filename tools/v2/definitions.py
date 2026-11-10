"""Freeze a V2 draft into a separate normalized legacy execution graph."""
from pathlib import Path

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.workbench_graph import validate_graph
from .canvas_structure import SCHEMA_PATH, json_input, node_kind, validation_expression
from .designer_graph import edge_spec, is_designer_graph
from .documents import validate_draft, check_chain

ROOT = Path(__file__).resolve().parents[2]


def source_manifest():
    paths = [p for p in (ROOT / 'tools').rglob('*.py') if 'node_modules' not in p.parts]
    paths += list((ROOT / 'tools/v2/static').glob('*')) + [SCHEMA_PATH]
    if any(p.is_symlink() for p in paths):
        raise Invalid('运行源码不能使用符号链接')
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()}


def compile_definition(store, provider, workspace, draft, task='', *, agent_binding=None, provider_error=None):
    doc = validate_draft(draft['graph'])
    issues = check_chain(doc)['issues']
    if issues:
        raise Invalid(issues[0]['message'])
    if not isinstance(task, str) or len(task) > 20000:
        raise Invalid('本次任务需要 20000 字符以内的文本')
    designer = is_designer_graph(doc)
    bindings, configured = {}, {}

    def bind(node, prefix):
        path = prefix + node['id']
        if 'agent_id' in node:
            if agent_binding is None:
                raise Invalid('Agent 配置执行尚未启用。')
            record, binding = agent_binding(node)
            if prefix and binding.get('session_id'):
                raise Invalid('循环 Agent 必须使用新会话，不能重复绑定已有原生会话。')
            bindings[path] = binding
            configured[path] = record
            config = record['config']
            if binding.get('transition_from'):
                return dict(config, mode='Agent', permission='workspace-write', initial_prompt='',
                            round_prompt='按本次明确确认的原生计划完成实施，并核验结果。')
            return config
        if provider is None:
            raise Blocked(provider_error or '当前 Codex CLI 不可用。')
        bindings[path] = provider.binding(workspace, node)
        return node

    graph = normalize_graph(doc, bind, designer=designer)
    manifest = source_manifest()
    value = dict(version=1, workspace=workspace, execution_environment_id=store.environment['id'],
                 draft_id=draft['id'], draft_revision=draft['revision'], draft_document=doc,
                 task=task, task_sha256=digest(task.encode()), graph=graph, graph_sha256=digest(canonical(graph)),
                 bindings=bindings, runtime_sources=manifest, runtime_source_sha256=digest(canonical(manifest)))
    if designer:
        value['designer_runtime'] = 1
    if configured:
        value['agent_configs'] = configured
    return dict(value, id=digest(canonical(value)))


def graph_budget(nodes):
    calls, seconds, activations = 0, 0, len(nodes)
    for node in nodes:
        kind = node['kind']
        if kind == 'loop':
            child_calls, child_seconds, child_count = graph_budget(node['body']['nodes'])
            rounds = node['max_rounds']
            calls += rounds * child_calls
            seconds += rounds * child_seconds
            activations += rounds * child_count
        elif kind in ('agent', 'human'):
            calls += kind == 'agent'
            seconds += node['timeout']
    return calls, seconds, activations


def normalize_graph(doc, bind, *, designer, prefix=''):
    nodes = []
    for n in doc['nodes']:
        kind = node_kind(n)
        if kind == 'start':
            config = n['config']
            initial = config.get('initial_data', '')
            start = dict(id=n['id'], name=n['name'], kind='start', input_instructions=config.get('instructions', ''))
            if initial.strip():
                start['initial_data'] = json_input(initial)
            nodes.append(start)
            continue
        if kind == 'output':
            nodes.append(dict(id=n['id'], name=n['name'], kind='result', format=n['config'].get('format', 'text'),
                              output_instructions=n['config'].get('instructions', '')))
            continue
        if kind == 'merge':
            nodes.append(dict(id=n['id'], name=n['name'], kind='merge', strategy=n['config'].get('strategy', 'all')))
            continue
        if kind in ('condition', 'validation'):
            expression = n['config']['expression'] if kind == 'condition' else validation_expression(n['config'])
            nodes.append(dict(id=n['id'], name=n['name'], kind=kind, expression=expression))
            continue
        if kind == 'router':
            routes = [dict(label=rule['id'], test=rule['test']) for rule in n['config']['rules']]
            fallback = 'fallback' if any(edge['source'] == n['id'] and edge.get('source_port') == 'fallback'
                                         for edge in doc['edges']) else None
            nodes.append(dict(id=n['id'], name=n['name'], kind='router', routes=routes, default_route=fallback))
            continue
        if kind == 'loop':
            config = n['config']
            body = normalize_graph(config['body'], bind, designer=True, prefix=prefix + n['id'] + '/')
            nodes.append(dict(id=n['id'], name=n['name'], kind='loop', body=body,
                              max_rounds=config.get('max_iterations', 3), until=config['exit_condition']))
            continue
        if kind == 'approval':
            config = n['config']
            nodes.append(dict(id=n['id'], name=n['name'], kind='human', question=config['instructions'],
                              policy=config.get('policy', 'single-reviewer'), timeout=config.get('timeout', 300)))
            continue
        if kind == 'tool':
            nodes.append(dict(id=n['id'], name=n['name'], kind='tool', path=n['config']['path']))
            continue
        spec = bind(n, prefix)
        nodes.append(dict(id=n['id'], kind='agent', name=n['name'], mode=spec.get('mode', 'Ask'),
                           permission='workspace-write' if spec.get('permission') == 'workspace-write' else 'read',
                           initial_prompt=spec.get('initial_prompt', ''), round_prompt=spec.get('round_prompt', ''),
                           handoff_prompt=spec.get('output_prompt', ''), timeout=spec.get('timeout', 300),
                           subagents='disabled', task_context=True))
    agents = [node for node in nodes if node['kind'] == 'agent']
    used = {n['id'] for n in nodes}
    original = {n['id']: n for n in doc['nodes']}
    edges = [edge_spec(e, original) if designer else dict(
        id=e['id'], source=e['source'], target=e['target'], transfer='answer', max_chars=16000)
             for e in doc['edges']]
    edge_ids = {e['id'] for e in edges}
    def technical(prefix, taken):
        while prefix in taken:
            prefix += '_'
        taken.add(prefix)
        return prefix
    if all(n['kind'] == 'agent' for n in nodes):
        start = technical('v2_start', used)
        result = technical('v2_result', used)
        incoming = {e['target'] for e in edges}
        outgoing = {e['source'] for e in edges}
        first = next(n['id'] for n in agents if n['id'] not in incoming)
        last = next(n['id'] for n in agents if n['id'] not in outgoing)
        edges += [dict(id=technical('v2_input', edge_ids), source=start, target=first,
                       transfer='envelope' if designer else 'result'),
                  dict(id=technical('v2_output', edge_ids), source=last, target=result, transfer='answer')]
        if designer:
            for edge in edges[-2:]:
                edge.update(source_port='out', target_port='in', max_chars=16000)
        nodes = [dict(id=start, kind='start', name='输入'), *nodes, dict(id=result, kind='result', name='结果')]
    calls, seconds, activations = graph_budget(nodes)
    if designer and (calls > 100 or activations > 512):
        raise Invalid('展开后最多允许 100 次 Agent 调用和 512 个节点激活。')
    graph = dict(version=3, name=doc['name'], nodes=nodes, edges=edges,
                 budget=dict(calls=max(1, calls), seconds=min(14400, seconds + 60)))
    return graph if designer else validate_graph(graph)
