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
    bindings, nodes, configured = {}, [], {}
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
        spec = n
        if 'agent_id' in n:
            if agent_binding is None:
                raise Invalid('Agent 配置执行尚未启用。')
            record, bindings[n['id']] = agent_binding(n)
            configured[n['id']] = record
            spec = record['config']
            if bindings[n['id']].get('transition_from'):
                spec = dict(spec, mode='Agent', permission='workspace-write', initial_prompt='',
                            round_prompt='按本次明确确认的原生计划完成实施，并核验结果。')
        else:
            if provider is None:
                raise Blocked(provider_error or '当前 Codex CLI 不可用。')
            bindings[n['id']] = provider.binding(workspace, n)
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
    graph = dict(version=3, name=doc['name'], nodes=nodes, edges=edges,
                 budget=dict(calls=max(1, len(agents)), seconds=min(14400, sum(n['timeout'] for n in agents) + 60)))
    if not designer:
        graph = validate_graph(graph)
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
