"""Freeze a V2 draft into a separate normalized legacy execution graph."""
import copy
from pathlib import Path

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.workbench_graph import validate_graph
from .documents import validate_draft, check_chain

ROOT = Path(__file__).resolve().parents[2]


def source_manifest():
    paths = [p for p in (ROOT / 'tools').rglob('*.py') if 'node_modules' not in p.parts]
    paths += list((ROOT / 'tools/v2/static').glob('*'))
    if any(p.is_symlink() for p in paths):
        raise Invalid('运行源码不能使用符号链接')
    return {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()}


def compile_definition(store, provider, workspace, draft, task='', *, agent_binding=None):
    doc = validate_draft(draft['graph'])
    issues = check_chain(doc)['issues']
    if issues:
        raise Invalid(issues[0]['message'])
    if not isinstance(task, str) or len(task) > 20000:
        raise Invalid('本次任务需要 20000 字符以内的文本')
    bindings, agents, configured = {}, [], {}
    for n in doc['nodes']:
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
                raise Invalid('当前 Codex CLI 不可用。')
            bindings[n['id']] = provider.binding(workspace, n)
        agents.append(dict(id=n['id'], kind='agent', name=n['name'], mode=spec.get('mode', 'Ask'),
                           permission='workspace-write' if spec.get('permission') == 'workspace-write' else 'read',
                           initial_prompt=spec.get('initial_prompt', ''), round_prompt=spec.get('round_prompt', ''),
                           handoff_prompt=spec.get('output_prompt', ''), timeout=spec.get('timeout', 300),
                           subagents='disabled', task_context=True))
    used = {n['id'] for n in agents}
    start = 'v2_start'
    while start in used: start += '_'
    result = 'v2_result'
    while result in used or result == start: result += '_'
    incoming = {e['target'] for e in doc['edges']}; outgoing = {e['source'] for e in doc['edges']}
    first = next(n['id'] for n in agents if n['id'] not in incoming)
    last = next(n['id'] for n in agents if n['id'] not in outgoing)
    edges = [dict(e, transfer='answer', max_chars=16000) for e in copy.deepcopy(doc['edges'])]
    edge_ids = {e['id'] for e in edges}
    def technical(prefix):
        while prefix in edge_ids: prefix += '_'
        edge_ids.add(prefix); return prefix
    edges += [dict(id=technical('v2_input'), source=start, target=first, transfer='result'),
              dict(id=technical('v2_output'), source=last, target=result, transfer='answer')]
    graph = validate_graph(dict(version=3, name=doc['name'], nodes=[dict(id=start, kind='start', name='输入'), *agents,
                          dict(id=result, kind='result', name='结果')], edges=edges,
                          budget=dict(calls=len(agents), seconds=min(14400, sum(n['timeout'] for n in agents) + 60))))
    manifest = source_manifest()
    value = dict(version=1, workspace=workspace, execution_environment_id=store.environment['id'],
                 draft_id=draft['id'], draft_revision=draft['revision'], draft_document=doc,
                 task=task, task_sha256=digest(task.encode()), graph=graph, graph_sha256=digest(canonical(graph)),
                 bindings=bindings, runtime_sources=manifest, runtime_source_sha256=digest(canonical(manifest)))
    if configured:
        value['agent_configs'] = configured
    return dict(value, id=digest(canonical(value)))
