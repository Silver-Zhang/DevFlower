#!/usr/bin/env python3
"""Compile a saved canvas into one standalone native-CLI Python file.

Compilation is read-only and uses no model. This is an explicit native execution
contract with its own native CLI permissions. V2 managed execution is separate.
"""
import argparse
from contextlib import closing
from hashlib import sha256
import json
import math
from pathlib import Path
import pprint
import re
import sqlite3
import sys

from workflow_output_contract import ROUTER_TYPES, OutputContractError, native_output_spec, output_type


class ExportError(ValueError):
    pass


class UnsupportedGraph(ExportError):
    pass


NATIVE_AGENT_REQUIRED = {'execution', 'name', 'provider', 'cwd', 'role', 'round_prompt', 'output_prompt'}
NATIVE_AGENT_FIELDS = NATIVE_AGENT_REQUIRED | {'model', 'effort', 'timeout'}
# Explicit legacy conversion still requires --native-config. These are the
# fields whose managed permission/session semantics the exporter understands.
LEGACY_AGENT_FIELDS = {'name', 'role', 'responsibilities', 'background', 'goal', 'template',
                       'provider', 'model', 'model_source', 'effort', 'mode', 'permission',
                       'cwd', 'session', 'skills', 'subagents', 'timeout', 'max_budget_usd',
                       'initial_prompt', 'round_prompt', 'output_prompt', 'native_profile'}
NODE_FIELDS = {'id', 'name', 'kind', 'position'}
REVIEW_CONDITION = {'field': 'result.verdict', 'op': 'equals', 'value': 'accept'}
CONTROL_KINDS = ('condition', 'router')
COMPILER_VERSION = 13


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def text(value, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or '\0' in value:
        raise ExportError(label + ' must be text without NUL.')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', value):
        raise ExportError('Invalid node/edge identity.')
    return value


def known_fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ExportError(label + ' contains unsupported fields; no configuration was discarded.')


def validate_timeout(value):
    if value is None:
        return
    try:
        if type(value) is int and value > 0 and math.isfinite(value):
            return
    except OverflowError:
        pass
    raise ExportError('timeout must be a finite positive integer in seconds or null.')


def validate_native_agent(value):
    """Small portable contract shared by storage and the standalone compiler.

    No CLI discovery, account access, tool filtering or managed-session binding.
    Do not normalize saved records: their exact bytes determine their revision.
    """
    known_fields(value, NATIVE_AGENT_FIELDS, 'Native Agent')
    if not NATIVE_AGENT_REQUIRED <= value.keys() or value.get('execution') != 'native-cli':
        raise ExportError('Native Agent requires execution=native-cli and its identity/Prompt fields.')
    if value['provider'] not in ('codex', 'claude'):
        raise ExportError('Native Agent requires Codex or Claude.')
    for key, maximum in (('name', 160), ('cwd', 4096), ('role', 4000),
                         ('round_prompt', 20000), ('output_prompt', 20000)):
        if len(text(value[key], key)) > maximum:
            raise ExportError(key + ' exceeds the saved text limit.')
    if any(not value[key].strip() for key in ('name', 'role', 'cwd')):
        raise ExportError('Native Agent name, role and working directory must not be empty.')
    for key in ('model', 'effort'):
        override = text(value.get(key), key, optional=True)
        if override is not None and (not override.strip() or len(override) > 200):
            raise ExportError(key + ' must be a nonempty override or null for the CLI default.')
    validate_timeout(value.get('timeout'))
    try:
        canonical(value).encode('utf-8')
    except (ValueError, UnicodeError):
        raise ExportError('Native Agent contains invalid JSON or Unicode text.') from None
    return value


def document_agents(document):
    """Yield the Agent nodes from the draft and any fixed Loop bodies."""
    for node in document['nodes']:
        if node.get('kind', 'agent') == 'agent':
            yield node
        elif node.get('kind') == 'loop' and (body := node['config'].get('body')):
            yield from document_agents(body)


def read_saved(database, workspace, draft_id, *, expected_revision=None):
    """One SQLite read transaction; never starts services or writes product data."""
    database = Path(database).resolve(strict=True)
    if not database.is_file():
        raise ExportError('Choose the existing v2.sqlite file.')
    # Existing WAL databases need SQLite to remove its own read sidecars on
    # close. mode=ro leaves -wal/-shm behind and breaks V2's history reader.
    # mode=rw forbids creation of a missing database; query_only forbids all
    # application writes while retaining SQLite's normal WAL housekeeping.
    with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        def record(kind, key):
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
            if not row:
                raise ExportError('Missing ' + kind + ' record: ' + key)
            return json.loads(row[0])
        space, draft = record('workspace', workspace), record('draft', draft_id)
        if space.get('id') != workspace or draft.get('workspace') != workspace or draft.get('id') != draft_id:
            raise ExportError('Workspace/draft identity does not match.')
        if expected_revision is not None and draft.get('revision') != expected_revision:
            raise ExportError('Saved draft revision changed; reopen the workflow before exporting.')
        graph = draft['graph']
        if sha256(canonical(graph).encode()).hexdigest() != draft.get('revision'):
            raise ExportError('Saved draft revision does not match its content.')
        agents = {}
        for node in document_agents(graph):
            if 'agent_id' in node:
                row = record('agent-config', node['agent_id'])
                if row.get('workspace') != workspace:
                    raise ExportError('Agent belongs to another workspace.')
                agents[node['agent_id']] = row
        return graph, agents, space['path']


def control_ports(node):
    config = node.get('config')
    if node['kind'] == 'condition':
        if config != {'expression': REVIEW_CONDITION}:
            raise ExportError('Native Condition requires result.verdict equals "accept".')
        return ('true', 'false')
    known_fields(config, {'rules'}, 'Router configuration')
    rules = config.get('rules')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 16:
        raise ExportError('Native Router requires 1–16 ordered rules.')
    ports = []
    for rule in rules:
        known_fields(rule, {'id', 'label', 'test'}, 'Router rule')
        port = identifier(rule.get('id'))
        if port == 'fallback' or port in ports:
            raise ExportError('Duplicate or reserved Router rule ID: ' + port)
        label = text(rule.get('label'), 'Router rule label')
        if not label.strip() or len(label) > 100:
            raise ExportError('Router rule label must contain 1–100 characters: ' + port)
        test = rule.get('test')
        known_fields(test, {'field', 'op', 'value'}, 'Router rule test')
        if test.get('op') != 'equals':
            raise ExportError('Native Router supports only equals: ' + port)
        field = text(test.get('field'), 'Router rule field')
        if len(field) > 200 or not field.startswith('result.'):
            raise ExportError('Router rule field must be a declared result.* path: ' + port)
        value = test.get('value')
        kind = output_type(value)
        if kind not in ROUTER_TYPES or (kind == 'number' and not -1e100 <= value <= 1e100):
            raise ExportError('Router comparison needs a string, finite number or boolean: ' + port)
        if kind == 'string' and len(text(value, 'Router rule value')) > 2000:
            raise ExportError('Router string comparison exceeds 2000 characters: ' + port)
        ports.append(port)
    return (*ports, 'fallback')


def workflow_paths(document):
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] != 1 or not isinstance(document.get('nodes'), list)
            or not isinstance(document.get('edges'), list) or not document['nodes']):
        raise ExportError('Expected a nonempty version-1 canvas document.')
    known_fields(document, {'version', 'name', 'nodes', 'edges', 'viewport', 'execution'}, 'Workflow')
    name = text(document.get('name'), 'Workflow name')
    if not name.strip():
        raise ExportError('Workflow name is empty.')
    nodes, incoming, outgoing, edge_ids = {}, {}, {}, set()
    for node in document['nodes']:
        if not isinstance(node, dict):
            raise ExportError('Invalid node.')
        ident = identifier(node.get('id'))
        if ident in nodes:
            raise ExportError('Duplicate node: ' + ident)
        kind = node.get('kind', 'agent')
        if kind not in ('start', 'agent', 'output', 'loop', 'tool', *CONTROL_KINDS):
            raise UnsupportedGraph('Export not implemented for node ' + ident + ' (' + str(kind) + '). No graph was flattened.')
        if kind == 'agent':
            allowed = ({'agent_id', 'agent_revision'} if 'agent_id' in node
                       else LEGACY_AGENT_FIELDS | NATIVE_AGENT_FIELDS)
            known_fields(node, NODE_FIELDS | allowed | {'output_contract', 'artifact_paths', 'native_output', 'task_prompt'}, ident)
            if 'agent_revision' in node and 'agent_id' not in node:
                raise ExportError(ident + ': Agent revision requires its saved identity.')
        else:
            known_fields(node, NODE_FIELDS | {'config_version', 'config'}, ident)
            if type(node.get('config_version', 1)) is not int or node.get('config_version', 1) != 1:
                raise ExportError(ident + ': unsupported configuration version.')
        nodes[ident], incoming[ident], outgoing[ident] = node, [], []
    controls = [node for node in nodes.values() if node.get('kind') in CONTROL_KINDS]
    if len(controls) > 1:
        raise UnsupportedGraph('Native export supports a single Condition or Router; multiple control nodes are unsupported.')
    control = controls[0] if controls else None
    control_id = control['id'] if control else None
    branch_ports = control_ports(control) if control else ()
    for edge in document['edges']:
        if not isinstance(edge, dict):
            raise ExportError('Invalid edge.')
        known_fields(edge, {'id', 'source', 'target', 'source_port', 'target_port', 'transfer'}, 'Edge')
        ident = identifier(edge.get('id'))
        source, target = identifier(edge.get('source')), identifier(edge.get('target'))
        if ident in edge_ids or source not in nodes or target not in nodes or source == target:
            raise ExportError('Duplicate or invalid edge: ' + ident)
        source_kind, target_kind = nodes[source].get('kind'), nodes[target].get('kind')
        ports = branch_ports if source == control_id else ('out',)
        if (edge.get('source_port', 'out') not in ports or edge.get('target_port', 'in') != 'in'
                or source_kind == 'output' or target_kind == 'start'):
            raise ExportError('Unsupported port: ' + ident)
        transfer = edge.get('transfer', {})
        envelope = source_kind in ('start', 'tool', 'loop', *CONTROL_KINDS) or target_kind in (*CONTROL_KINDS, 'output', 'tool', 'loop')
        modes = ('answer', 'none', 'envelope') if envelope else ('answer', 'none')
        if target_kind == 'router':
            modes += ('result',)
        if not isinstance(transfer, dict) or set(transfer) - {'mode', 'max_chars'} or transfer.get('mode', 'answer') not in modes:
            raise UnsupportedGraph('Unsupported handoff on ' + ident + '; structured data must not be silently discarded.')
        limit = transfer.get('max_chars')
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ExportError('Invalid handoff limit: ' + ident)
        if (source_kind == 'start' or target_kind in CONTROL_KINDS) and transfer.get('mode') == 'none':
            raise ExportError('Start and control node inputs require a data handoff: ' + ident)
        edge_ids.add(ident)
        incoming[target].append(edge)
        outgoing[source].append(edge)
    targets = {}
    if control:
        label = control['kind'].title()
        targets = {edge.get('source_port', 'out'): edge['target'] for edge in outgoing[control_id]}
        missing = [port for port in branch_ports if port not in targets]
        if missing:
            raise ExportError(label + ' requires a connection from each output port. Missing: ' + ', '.join(missing))
        if len(outgoing[control_id]) != len(branch_ports):
            raise ExportError(label + ' requires exactly one edge per output port.')
    roots = [key for key in nodes if not incoming[key]]
    if len(roots) != 1:
        raise ExportError('Expected one root; the graph is disconnected or cyclic.')
    if any(len(incoming[key]) > 1 or (key != control_id and len(outgoing[key]) > 1) for key in nodes):
        raise UnsupportedGraph('Only Condition or Router may branch; shared nodes and branch merges are unsupported.')
    seen = set()

    def walk(current, stop=None):
        path = []
        while current != stop and current is not None:
            if current in seen:
                raise ExportError('Cycle or shared branch node: ' + current)
            seen.add(current)
            path.append(nodes[current])
            current = outgoing[current][0]['target'] if outgoing[current] else None
        return path, current

    prefix, endpoint = walk(roots[0], control_id)
    branches = {}
    if control:
        if (endpoint != control_id or not prefix or prefix[0].get('kind') != 'start'
                or prefix[-1].get('kind', 'agent') != 'agent'):
            raise ExportError(label + ' requires a Start and a preceding structured Agent.')
        seen.add(control_id)
        for port in branch_ports:
            branch, _ = walk(targets[port], control_id)
            if not branch or branch[-1].get('kind') != 'output':
                raise ExportError(label + ' ' + port + ' must end at its own Output.')
            branches[port] = branch
    if len(seen) != len(nodes):
        raise ExportError('Every node must be reachable from the root; disconnected or cyclic graph.')
    loops = [n for n in nodes.values() if n.get('kind') == 'loop']
    if loops:
        if (len(loops) != 1 or control or prefix[0].get('kind') != 'start'
                or prefix[-1].get('kind') != 'output'):
            raise UnsupportedGraph('Native Loop requires Start → optional Agent/Tool → one Loop → optional Agent/Tool → Output, without Condition or Router.')
        if 'iterations' in nodes and nodes['iterations'].get('kind', 'agent') in ('agent', 'tool'):
            raise ExportError('Outer node ID "iterations" is reserved for Loop evidence.')
    elif not any(n.get('kind', 'agent') in ('agent', 'tool') for n in prefix):
        raise ExportError('Add at least one Agent or Command Tool.')
    return name, prefix, control, branches, incoming


def compile_agent(node, records, root, transfer, producer):
    ident = node['id']
    if 'agent_id' in node:
        row = records.get(node['agent_id'])
        if not row or row.get('id') != node['agent_id'] or row.get('revision') != node.get('agent_revision'):
            raise ExportError(ident + ': missing or stale saved Agent revision.')
        config = row['config']
    else:
        config = node
    if not isinstance(config, dict):
        raise ExportError(ident + ': invalid Agent configuration.')
    if 'agent_id' in node:
        known_fields(config, LEGACY_AGENT_FIELDS | NATIVE_AGENT_FIELDS, ident)
    if 'execution' in config:
        portable = {key: value for key, value in config.items() if key not in NODE_FIELDS - {'name'}}
        # Node presentation metadata is separate from inline Agent settings.
        portable = {key: value for key, value in portable.items() if key not in ('output_contract', 'artifact_paths', 'native_output', 'task_prompt')}
        validate_native_agent(portable)
    provider = config.get('provider')
    if provider not in ('codex', 'claude'):
        raise ExportError(ident + ': configure Codex or Claude.')
    if config.get('mode') == 'Plan' or config.get('native_profile') or config.get('skills'):
        raise ExportError(ident + ': Plan transition, named profiles and selected Skill handles need explicit portable mapping; not exported.')
    if config.get('session', {'kind': 'new'}) != {'kind': 'new'}:
        raise ExportError(ident + ': existing managed sessions cannot be resumed by an independent script.')
    cwd = Path(text(config.get('cwd') or str(root), 'Working directory'))
    try:
        relative = (cwd if cwd.is_absolute() else root / cwd).resolve().relative_to(root).as_posix()
    except ValueError:
        raise ExportError(ident + ': working directory is outside the exported project root.') from None
    timeout = config.get('timeout')
    validate_timeout(timeout)
    budget = config.get('max_budget_usd')
    if budget is not None and (provider != 'claude' or not isinstance(budget, str)
                               or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', budget) or float(budget) <= 0):
        raise ExportError(ident + ': unsupported native budget.')
    identity = '\n'.join(text(config.get(key, ''), key) for key in ('role','responsibilities','background','goal'))
    agent = dict(id=ident, name=text(node.get('name', ident), 'Node name'), provider=provider,
                 model=text(config.get('model'), 'Model', optional=True),
                 effort=text(config.get('effort'), 'Effort', optional=True), cwd=relative,
                 role=identity.strip(), initial_prompt=text(config.get('initial_prompt',''), 'Initial prompt'),
                 round_prompt=text(config.get('round_prompt',''), 'Node prompt'),
                 output_prompt=text(config.get('output_prompt',''), 'Delivery prompt'),
                 timeout=timeout, max_budget_usd=budget)
    if 'agent_id' in node:
        agent.update(profile_id=node['agent_id'], profile_revision=node['agent_revision'])
    if 'task_prompt' in node:
        task_prompt = text(node['task_prompt'], ident + ': node task')
        if len(task_prompt) > 20000:
            raise ExportError(ident + ': node task exceeds the saved text limit.')
        if task_prompt.strip():
            agent['task_prompt'] = task_prompt
    if 'native_output' in node:
        if config.get('execution') != 'native-cli':
            raise ExportError(ident + ': native output requires an explicit native-cli Agent.')
        try:
            spec = native_output_spec(node)
        except OutputContractError as error:
            raise ExportError(ident + ': ' + str(error)) from None
        if any(not (Path(root) / path).is_relative_to(root / relative) for path in spec['allowed_paths']):
            raise ExportError(ident + ': declared artifacts must be inside the producing Agent directory.')
        if spec['kind'] == 'review':
            upstream = producer.get('native_output', {})
            if not upstream.get('allowed_paths') or transfer.get('mode') == 'none':
                raise ExportError(ident + ': review requires a handoff from a structured producer with declared artifacts.')
        agent['native_output'] = spec
    elif node.get('output_contract') or node.get('artifact_paths'):
        raise ExportError(ident + ': enable native output to export structured fields or artifact paths.')
    return agent


def compile_router(node, source):
    spec = source.get('native_output', {})
    if spec.get('kind') != 'result':
        raise ExportError('Router requires a direct handoff from a structured result Agent v1.')
    fields = {rule['field']: rule['type'] for rule in spec['fields']}
    rules = []
    for rule in node['config']['rules']:
        test = rule['test']
        kind = fields.get(test['field'])
        if kind not in ROUTER_TYPES:
            raise ExportError('Router field must be declared as string, number or boolean: ' + test['field'])
        if output_type(test['value']) != kind:
            raise ExportError('Router comparison type does not match declared field: ' + test['field'])
        rules.append(dict(id=rule['id'], label=rule['label'], field=test['field'], type=kind, value=test['value']))
    return rules


def compile_start(node):
    config = node.get('config', {})
    known_fields(config, {'instructions', 'initial_data'}, 'Start configuration')
    instructions = text(config.get('instructions', ''), 'Start instructions')
    raw_data = text(config.get('initial_data', ''), 'Start initial data')
    data = 'Initial data\n' + canonical(json.loads(raw_data)) if raw_data.strip() else ''
    return '\n\n'.join(value for value in (instructions, data) if value)


def compile_output(node, transfer):
    config = node.get('config', {})
    if (not isinstance(config, dict) or config.get('format', 'text') != 'text'
            or config.get('instructions') or set(config) - {'format', 'instructions'}):
        raise UnsupportedGraph('This export supports plain final-answer Output only.')
    if transfer.get('mode') == 'none':
        raise UnsupportedGraph('Output requires data; control-only Output is not implemented.')
    return transfer.get('max_chars')


def compile_command(tool):
    command = tool.get('config')
    if isinstance(command, dict):
        # Newly created canvas Tool nodes carry empty defaults from other Tool modes.
        inactive = {'path', 'expected_json', 'developer_node', 'tester_node', 'reviewer_node', 'verification_node'}
        command = {key: value for key, value in command.items() if key not in inactive or value != ''}
    known_fields(command, {'tool', 'argv', 'cwd', 'timeout', 'permission'}, 'Command Tool configuration')
    if command.get('tool') != 'command' or command.get('permission', 'none') != 'none':
        raise UnsupportedGraph('Native Tool requires command and uses the current user environment.')
    argv = command.get('argv')
    if (not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and '\0' not in arg for arg in argv)
            or not argv[0].strip()):
        raise ExportError('Command Tool argv requires a nonempty JSON array of strings.')
    cwd = Path(text(command.get('cwd', '.'), 'Command Tool cwd'))
    if cwd.is_absolute() or '..' in cwd.parts:
        raise ExportError('Command Tool cwd must be workspace-relative.')
    timeout = command.get('timeout', 300)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ExportError('Command Tool timeout must be 1–3600 seconds.')
    return dict(id=tool['id'], name=text(tool.get('name', tool['id']), 'Node name'),
                argv=argv, cwd=cwd.as_posix(), timeout=timeout)


def compile_loop(loop, records, root):
    config = loop.get('config')
    known_fields(config, {'body', 'exit_condition', 'max_iterations'}, 'Loop configuration')
    if config.get('exit_condition') != REVIEW_CONDITION:
        raise ExportError('Native Loop requires result.verdict equals "accept".')
    maximum = config.get('max_iterations', 3)
    if type(maximum) is not int or not 1 <= maximum <= 5:
        raise ExportError('Native Loop supports 1–5 iterations.')
    _, body, control, _, edges = workflow_paths(config.get('body'))
    kinds = [n.get('kind', 'agent') for n in body]
    if control or kinds not in (['start', 'agent', 'agent', 'output'], ['start', 'agent', 'tool', 'agent', 'output']):
        raise UnsupportedGraph('Native Loop body requires Start → Developer → optional command Tool → Reviewer → Output.')
    body_start, developer = body[:2]
    reviewer, body_output = body[-2:]
    if not all('agent_id' in node for node in (developer, reviewer)):
        raise ExportError('Native Loop requires both Agents to reference saved versions.')
    input_edge, review_edge, result_edge = [edges[node['id']][0].get('transfer', {})
                                            for node in (developer, reviewer, body_output)]
    first = compile_agent(developer, records, root, input_edge, {})
    second = compile_agent(reviewer, records, root, review_edge, first)
    if not first.get('native_output', {}).get('allowed_paths') or second.get('native_output', {}).get('kind') != 'review':
        raise ExportError('Native Loop requires a declared candidate producer and a candidate Reviewer v1.')
    options = dict(node=loop['id'], name=text(loop.get('name', loop['id']), 'Node name'),
                   agents=[first, second], max_iterations=maximum, body_input=compile_start(body_start),
                   task_limit=input_edge.get('max_chars'), review_limit=review_edge.get('max_chars'),
                   body_output_limit=compile_output(body_output, result_edge))
    if len(body) == 5:
        tool = body[2]
        validation_edge = edges[tool['id']][0].get('transfer', {})
        if validation_edge.get('mode') == 'none':
            raise ExportError('Loop Tool requires a candidate handoff.')
        options['validation'] = dict(compile_command(tool), limit=validation_edge.get('max_chars'))
    return options


def compile_workflow(document, agents=None, workspace='.', *, native_config=False):
    """Pure code generation except reading the bundled fixed runtime template."""
    if native_config is not True:
        raise ExportError('Explicit --native-config required: exported calls use native CLI permissions, not V2 sandbox policy.')
    name, prefix, control, branches, incoming = workflow_paths(document)
    records, normalized, calls, initial = agents or {}, {}, [], ''
    tools, producers, steps = {}, {}, []
    root = Path(workspace).resolve()
    manifest = dict(version=1, compiler_version=COMPILER_VERSION, name=name, policy='native-cli (not the V2 managed sandbox)',
                    source_sha256=sha256(canonical(document).encode()).hexdigest())

    def compile_path(path, previous=None, *, indent='    '):
        nonlocal initial
        for node in path:
            kind, ident = node.get('kind', 'agent'), node['id']
            transfer = incoming[ident][0].get('transfer', {}) if incoming[ident] else {}
            limit = transfer.get('max_chars')
            if kind == 'start':
                initial = compile_start(node)
                continue
            if kind == 'output':
                return previous, compile_output(node, transfer)
            if previous is None and limit is not None:
                calls.append(indent + f'if len(task) > {limit}:\n'
                             + indent + "    raise WorkflowError('Saved Start handoff limit exceeded; no request sent.')")
            handoff = previous is not None and transfer.get('mode') != 'none'
            source = f'results[{previous!r}]' if handoff else 'None'
            # A candidate handoff follows a data edge, not a control decision.
            direct = handoff and incoming[ident][0]['source'] == previous
            producer = producers.get(previous, {}) if direct else {}
            steps.append(ident)
            if kind == 'tool':
                tools[ident] = compile_command(node)
                producers[ident] = producer
                call = f"invoke_tool(context, WORKFLOW['tools'][{ident!r}], {source}, {limit!r})"
            elif kind == 'loop':
                loop = manifest['loop'] = compile_loop(node, records, root)
                producers[ident] = loop['agents'][0]
                call = f"run_loop(context, WORKFLOW['loop'], task, {source}, {limit!r})"
            else:
                agent = compile_agent(node, records, root, transfer, producer)
                index = len(normalized)
                normalized[ident] = producers[ident] = agent
                call = f"invoke_agent(context, WORKFLOW['agents'][{index}], task, {source}, {limit!r})"
            calls.append(indent + f'results[{ident!r}] = ' + call)
            previous = ident
        return previous, None

    def finish_path(previous, limit, indent):
        if limit is not None:
            calls.append(indent + f"if len(results[{previous!r}]['answer']) > {limit}:\n"
                         + indent + "    raise WorkflowError('Saved Output limit exceeded; full answer retained in node logs.')")
        calls.append(indent + f'return results[{previous!r}]')

    upstream, final_limit = compile_path(prefix)
    if not control:
        finish_path(upstream, final_limit, '    ')
    if control:
        node_id, kind = control['id'], control['kind']
        decision = dict(node=node_id, branches={port: [node['id'] for node in path] for port, path in branches.items()})
        manifest[kind] = decision
        limit = incoming[node_id][0].get('transfer', {}).get('max_chars')
        if kind == 'condition':
            if normalized[upstream].get('native_output', {}).get('kind') != 'review':
                raise ExportError('Condition requires a direct handoff from a candidate Reviewer v1.')
            producer = incoming[upstream][0]['source']
            decision['reviewer'] = upstream
            calls.append(f"    if evaluate_condition(context, WORKFLOW['condition'], results[{upstream!r}], "
                         f"results[{producer!r}]['candidate'], {limit!r}):")
            finish_path(*compile_path(branches['true'], upstream, indent='        '), '        ')
            calls.append('    else:')
            finish_path(*compile_path(branches['false'], upstream, indent='        '), '        ')
        else:
            decision.update(source_node=upstream, rules=compile_router(control, normalized[upstream]))
            calls.append(f"    port = evaluate_router(context, WORKFLOW['router'], results[{upstream!r}], {limit!r})")
            for index, (port, path) in enumerate(branches.items()):
                if port == 'fallback':
                    calls.append('    else:')
                else:
                    keyword = 'if' if index == 0 else 'elif'
                    calls.append(f'    {keyword} port == {port!r}:')
                finish_path(*compile_path(path, upstream, indent='        '), '        ')
    manifest['input'] = initial
    manifest['agents'] = list(normalized.values())
    if tools:
        manifest['tools'] = tools
    if tools or 'loop' in manifest:
        manifest['steps'] = steps
    runtime = Path(__file__).with_name('workflow_script_runtime.py').read_text(encoding='utf-8')
    source = f'#!/usr/bin/env python3\n# Generated by DevFlower native-script compiler v{COMPILER_VERSION}.\n'
    source += '# Native CLI settings/permissions apply. Review before execution. No GUI/service dependency.\n'
    contract = Path(__file__).with_name('workflow_output_contract.py').read_text(encoding='utf-8')
    runtime, replacements = re.subn(r'^from workflow_output_contract import \(.*\n[^\n]+# EMBED_OUTPUT_CONTRACT$',
                                    lambda _: contract, runtime, flags=re.MULTILINE)
    if replacements != 1:
        raise ExportError('Workflow output contract template marker is missing or duplicated.')
    source += runtime + '\n\nWORKFLOW = ' + pprint.pformat(manifest, width=100, sort_dicts=False) + '\n\n'
    source += 'def run_workflow(context, task):\n    results = {}\n' + '\n'.join(calls) + '\n\n\n'
    source += "if __name__ == '__main__':\n    raise SystemExit(script_main(WORKFLOW, run_workflow))\n"
    compile(source, '<workflow>', 'exec')
    slug = re.sub(r'[^\w-]+', '_', name, flags=re.UNICODE).strip('_-')[:64] or 'workflow'
    filename = slug + '_' + sha256(source.encode()).hexdigest()[:8] + '.py'
    return filename, source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--database', type=Path, help='existing v2.sqlite; read-only snapshot of saved canvas and Agent records')
    group.add_argument('--document', type=Path, help='raw version-1 canvas JSON (not the portable archive envelope)')
    parser.add_argument('--workspace-id')
    parser.add_argument('--draft-id')
    parser.add_argument('--workspace', type=Path, default=Path.cwd(), help='project root for raw documents')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--native-config', action='store_true', help='explicitly use native CLI policy instead of V2 sandbox/Skill filtering')
    args = parser.parse_args(argv)
    try:
        if args.database:
            if not args.workspace_id or not args.draft_id:
                raise ExportError('--database requires --workspace-id and --draft-id.')
            document, records, root = read_saved(args.database, args.workspace_id, args.draft_id)
        else:
            if args.workspace_id or args.draft_id:
                raise ExportError('Workspace/draft IDs apply only to --database.')
            document, records, root = json.loads(args.document.read_text(encoding='utf-8')), {}, args.workspace
        filename, source = compile_workflow(document, records, root, native_config=args.native_config)
        directory = args.output_dir.resolve(strict=True)
        if not directory.is_dir():
            raise ExportError('Choose an existing output directory.')
        target = directory / filename
        # Never overwrite an independently edited script or follow a symlink.
        import os
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
            stream.write(source)
        print(target)
        print('Native policy: original V2 permission/Skill/subagent filters are NOT exported; native CLI configuration applies.', file=sys.stderr)
        return 0
    except (ExportError, OSError, ValueError, TypeError, KeyError, sqlite3.Error, RecursionError) as error:
        print('Export refused: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
