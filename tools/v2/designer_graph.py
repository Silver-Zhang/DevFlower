"""Normalize V2 canvas documents for the typed scheduler."""
from orchestration.common import Blocked
from studio.graph import Invalid
from studio.workbench_graph import field, predicate, transfer
from studio.workbench_data import public_data, public_field

from .canvas_structure import DEFAULT_TRANSFER, config_issues, node_kind, node_ports
from .data_handoff import field_contract, has_output_contract, select_fields

SUPPORTED = {'start', 'agent', 'output', 'condition', 'router', 'validation', 'merge', 'loop', 'approval', 'tool'}


def is_designer_graph(document):
    return (any(node_kind(node) != 'agent' or has_output_contract(node) for node in document['nodes'])
            or any(edge.get('transfer', DEFAULT_TRANSFER) != DEFAULT_TRANSFER for edge in document['edges']))


def document_nodes(document, prefix=''):
    for node in document['nodes']:
        path = prefix + node['id']
        yield path, node
        if node_kind(node) == 'loop' and node['config'].get('body'):
            yield from document_nodes(node['config']['body'], path + '/')


def graph_issues(document):
    nodes = document['nodes']
    edges = document['edges']
    issues = []
    for node in nodes:
        kind = node_kind(node)
        if kind not in SUPPORTED:
            issues.append(dict(node_id=node['id'], code='RUNTIME_NODE_UNAVAILABLE',
                               message=node['name'] + '：此节点的执行尚未接入。'))
        if not node['name'].strip():
            issues.append(dict(node_id=node['id'], message='请填写节点名称。'))
        if kind == 'agent' and 'agent_id' not in node:
            if 'kind' in node:
                issues.append(dict(node_id=node['id'], code='AGENT_REFERENCE_REQUIRED',
                                   message=node['name'] + '：请在 Agent 管理选择已保存的配置版本。'))
            elif any(not node.get(field, '').strip() for field in ('provider', 'model', 'effort', 'round_prompt')):
                issues.append(dict(node_id=node['id'], message=node['name'] + '：请完成 Provider、模型和提示词配置。'))
        issues.extend(dict(node_id=node['id'], field=field, message=node['name'] + '：' + problem)
                      for field, problem in config_issues(node))
        if kind == 'loop' and (body := node['config'].get('body')):
            for issue in graph_issues(body):
                path = node['id'] + '/' + issue['node_id'] if issue.get('node_id') else node['id']
                issues.append(dict(issue, node_id=path, message=node['name'] + ' / ' + issue['message']))
        if kind == 'approval' and node['config'].get('policy', 'single-reviewer') != 'single-reviewer':
            issues.append(dict(node_id=node['id'], message=node['name'] + '：当前仅支持本机单审核者，无法核验多人审批。'))
        if kind == 'tool':
            config = node['config']
            try:
                from .development_graph import CHECK, TOOLS, tool_options
                if config.get('tool') in TOOLS | {CHECK}:
                    tool_options(node)
                else:
                    if config.get('tool') != 'read-file' or config.get('permission') != 'workspace-read':
                        raise Invalid('仅支持固定工作区文件的只读预览。')
                    from .project_files import checked_path
                    if not checked_path(config.get('path', '')).parts:
                        raise Invalid('请指定固定的工作区相对文件路径。')
            except (Blocked, Invalid, ValueError) as failure:
                issues.append(dict(node_id=node['id'], code='RUNTIME_TOOL_UNAVAILABLE',
                                   message=node['name'] + '：' + str(failure)))
    explicit = any(node_kind(node) != 'agent' for node in nodes)
    incoming = {node['id']: [] for node in nodes}
    outgoing = {node['id']: [] for node in nodes}
    for edge in edges:
        incoming[edge['target']].append(edge['source'])
        outgoing[edge['source']].append(edge['target'])
    for node in nodes:
        kind = node_kind(node)
        if kind == 'merge' and len(incoming[node['id']]) < 2:
            issues.append(dict(node_id=node['id'], message=node['name'] + '：汇合需要至少两个上游端口连接。'))
        if kind not in ('condition', 'router', 'validation', 'approval'):
            continue
        connected = {edge.get('source_port', 'out') for edge in edges if edge['source'] == node['id']}
        optional = {'router': {'fallback'}, 'approval': {'rejected'}}.get(kind, set())
        required = set(node_ports(node)[1]) - optional
        for port in sorted(required - connected):
            issues.append(dict(node_id=node['id'], message=node['name'] + '：请连接分支“' + node_ports(node)[1][port] + '”。'))
    starts = [node['id'] for node in nodes if node_kind(node) == 'start'] if explicit else [
        node for node, upstream in incoming.items() if not upstream]
    outputs = [node['id'] for node in nodes if node_kind(node) == 'output'] if explicit else [
        node for node, downstream in outgoing.items() if not downstream]
    if len(starts) != 1 or not outputs:
        issues.append(dict(code='RUNTIME_GRAPH_BOUNDARY', message='执行需要一个开始节点和至少一个输出节点。'))
        return issues
    if not explicit and (len(outputs) != 1 or any(len(paths) > 1 for paths in (*incoming.values(), *outgoing.values()))):
        issues.append(dict(code='RUNTIME_IMPLICIT_CHAIN', message='分支工作流需要显式的开始、汇合与输出节点。'))

    def reached(first, links):
        seen, pending = set(), list(first)
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(links[current])
        return seen

    connected = reached(starts, outgoing) & reached(outputs, incoming)
    for node in nodes:
        if node['id'] not in connected:
            issues.append(dict(node_id=node['id'], code='RUNTIME_GRAPH_UNCONNECTED',
                               message=node['name'] + '：请连接到开始和输出节点之间。'))
    return issues


def edge_spec(edge, nodes):
    source, target = edge['source'], edge['target']
    mode = edge.get('transfer', {}).get('mode')
    if mode is None:
        mode = 'answer' if node_kind(nodes[source]) == node_kind(nodes[target]) == 'agent' else 'envelope'
    value = dict(id=edge['id'], source=source, target=target,
                 source_port=edge.get('source_port', 'out'), target_port=edge.get('target_port', 'in'),
                 transfer=mode, max_chars=edge.get('transfer', DEFAULT_TRANSFER)['max_chars'])
    if mode=='fields':
        value['fields'] = field_contract(edge['transfer']['fields'])
    return value


def handoff(output, edge, *, control):
    mode = edge['transfer']
    if mode == 'none':
        return None, {}
    if mode == 'envelope':
        spec = dict(edge, transfer='result')
    elif mode == 'result':
        spec = dict(edge, transfer='field', field='result')
    elif mode == 'artifacts':
        spec = dict(edge, transfer='field', field='artifacts')
    else:
        spec = edge
    if mode == 'fields':
        content, truncated = select_fields(output, edge['fields'], edge['max_chars'])
    else:
        content, truncated = transfer(output, spec, with_truncation=True)
    if mode in ('fields','artifacts') and truncated:
        raise Invalid('选定字段或产物引用超过交接上限，未发送不完整数据。')
    if not truncated:
        return content, {}
    proof = {key: content[key] for key in ('original_chars', 'budget')}
    if control:
        field = 'summary' if mode == 'summary' else 'answer'
        preview = output.get(field) if mode != 'result' else None
        if isinstance(preview, str):
            preview = public_data(preview)[:proof['budget']]
        else:
            preview = content['text']
        return content, {'truncation': proof, 'answer_preview': preview}
    return content, {'truncation': proof}


def input_value(inputs, seed):
    data = [item for item in inputs if item['fields'] != 'none']
    if not data:
        return seed if not inputs else {'answer': '', 'result': {}}
    if len(data) > 1:
        return {'answer': '', 'result': {'inputs': data}, 'sources': data}
    item, = data
    value = item['content']
    if 'truncation' in item:
        return {'answer': item.get('answer_preview', value['text']), 'result': {'truncation': item['truncation']}}
    if item['fields'] == 'envelope' and isinstance(value, dict):
        return value
    if item['fields'] == 'fields':
        return {'answer':'','result':{},**value}
    if item['fields'] == 'artifacts':
        return {'answer':'','result':{},'artifacts':value}
    if item['fields'] == 'summary':
        return {'answer': value, 'summary': value, 'result': {}}
    if item['fields'] == 'result':
        return {'answer': '', 'result': value}
    return {'answer': value, 'result': {}}


def output_node(node, upstream):
    if node.get('format') == 'json':
        from orchestration.common import canonical
        value = public_data(upstream.get('result', {}))
        return {'answer': canonical(value).decode(), 'result': value, 'format': 'json'}
    return public_data(upstream)


def condition(value, expression):
    details = []
    def evaluate(test):
        if 'all' in test:
            return all(evaluate(item) for item in test['all'])
        if 'any' in test:
            return any(evaluate(item) for item in test['any'])
        if 'not' in test:
            return not evaluate(test['not'])
        path = public_field(test['field'])
        field({}, path)
        actual = value
        present = True
        for key in path.split('.'):
            if not isinstance(actual, dict) or key not in actual:
                present = False
                break
            actual = actual[key]
        if not present or test['op'] == 'exists':
            matched = present
        else:
            matched = predicate(value, test)
        details.append(dict(field=path, op=test['op'], matched=matched,
                            presence='missing' if not present else 'null' if actual is None else 'present'))
        return matched
    matched = evaluate(expression)
    return dict(matched=matched, tests=details)


def select_branch(node, upstream):
    kind = node['kind']
    if kind == 'router':
        from studio.routing import fallback, rule_route
        decision = rule_route(upstream, node, compare=lambda value, test: condition(value, test)['matched'])
        decision = decision or fallback(node, '没有规则命中')
        return decision['label'], decision, 'route-selected'
    decision = condition(upstream, node['expression'])
    if kind == 'condition':
        return 'true' if decision['matched'] else 'false', decision, 'condition-decided'
    return 'pass' if decision['matched'] else 'fail', decision, 'validation-decided'
