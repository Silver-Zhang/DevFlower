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

from workflow_output_contract import OutputContractError, native_output_spec


class ExportError(ValueError):
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
        for node in graph['nodes']:
            if 'agent_id' in node:
                row = record('agent-config', node['agent_id'])
                if row.get('workspace') != workspace:
                    raise ExportError('Agent belongs to another workspace.')
                agents[node['agent_id']] = row
        return graph, agents, space['path']


def workflow_paths(document):
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] != 1 or not isinstance(document.get('nodes'), list)
            or not isinstance(document.get('edges'), list) or not document['nodes']):
        raise ExportError('Expected a nonempty version-1 canvas document.')
    known_fields(document, {'version', 'name', 'nodes', 'edges', 'viewport'}, 'Workflow')
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
        if kind not in ('start', 'agent', 'output', 'condition'):
            raise ExportError('Export not implemented for node ' + ident + ' (' + str(kind) + '). No graph was flattened.')
        if kind == 'agent':
            allowed = ({'agent_id', 'agent_revision'} if 'agent_id' in node
                       else LEGACY_AGENT_FIELDS | NATIVE_AGENT_FIELDS)
            known_fields(node, NODE_FIELDS | allowed | {'output_contract', 'artifact_paths', 'native_output'}, ident)
            if 'agent_revision' in node and 'agent_id' not in node:
                raise ExportError(ident + ': Agent revision requires its saved identity.')
        else:
            known_fields(node, NODE_FIELDS | {'config_version', 'config'}, ident)
            if type(node.get('config_version', 1)) is not int or node.get('config_version', 1) != 1:
                raise ExportError(ident + ': unsupported configuration version.')
        nodes[ident], incoming[ident], outgoing[ident] = node, [], []
    for edge in document['edges']:
        if not isinstance(edge, dict):
            raise ExportError('Invalid edge.')
        known_fields(edge, {'id', 'source', 'target', 'source_port', 'target_port', 'transfer'}, 'Edge')
        ident = identifier(edge.get('id'))
        source, target = edge.get('source'), edge.get('target')
        if ident in edge_ids or source not in nodes or target not in nodes or source == target:
            raise ExportError('Duplicate or invalid edge: ' + ident)
        source_kind, target_kind = nodes[source].get('kind'), nodes[target].get('kind')
        ports = ('true', 'false') if source_kind == 'condition' else ('out',)
        if (edge.get('source_port', 'out') not in ports or edge.get('target_port', 'in') != 'in'
                or source_kind == 'output' or target_kind == 'start'):
            raise ExportError('Unsupported port: ' + ident)
        transfer = edge.get('transfer', {})
        envelope = source_kind in ('start', 'condition') or target_kind in ('condition', 'output')
        modes = ('answer', 'none', 'envelope') if envelope else ('answer', 'none')
        if not isinstance(transfer, dict) or set(transfer) - {'mode', 'max_chars'} or transfer.get('mode', 'answer') not in modes:
            raise ExportError('Unsupported handoff on ' + ident + '; structured data must not be silently discarded.')
        limit = transfer.get('max_chars')
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ExportError('Invalid handoff limit: ' + ident)
        if (source_kind == 'start' or target_kind == 'condition') and transfer.get('mode') == 'none':
            raise ExportError('Start and Condition input require a data handoff: ' + ident)
        edge_ids.add(ident)
        incoming[target].append(edge)
        outgoing[source].append(edge)
    conditions = [key for key, node in nodes.items() if node.get('kind') == 'condition']
    if len(conditions) > 1:
        raise ExportError('Native export supports at most one Condition.')
    condition = conditions[0] if conditions else None
    roots = [key for key in nodes if not incoming[key]]
    if len(roots) != 1:
        raise ExportError('Expected one root; the graph is disconnected or cyclic.')
    if any(len(incoming[key]) > 1 or (key != condition and len(outgoing[key]) > 1) for key in nodes):
        raise ExportError('Only Condition may branch; shared nodes and branch merges are unsupported.')
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

    prefix, endpoint = walk(roots[0], condition)
    branches = {}
    if condition is not None:
        if (endpoint != condition or not prefix or prefix[0].get('kind') != 'start'
                or prefix[-1].get('kind', 'agent') != 'agent'):
            raise ExportError('Condition requires a Start and a preceding candidate Reviewer.')
        targets = {edge['source_port']: edge['target'] for edge in outgoing[condition]}
        if len(outgoing[condition]) != 2 or targets.keys() != {'true', 'false'}:
            raise ExportError('Condition requires exactly one true and one false output edge.')
        if nodes[condition].get('config') != {'expression': REVIEW_CONDITION}:
            raise ExportError('Native Condition requires result.verdict equals "accept".')
        seen.add(condition)
        for port in ('true', 'false'):
            branch, _ = walk(targets[port], condition)
            if not branch or branch[-1].get('kind') != 'output':
                raise ExportError('Condition ' + port + ' must end at its own Output.')
            branches[port] = branch
    if len(seen) != len(nodes):
        raise ExportError('Every node must be reachable from the root; disconnected or cyclic graph.')
    if not any(n.get('kind', 'agent') == 'agent' for n in prefix):
        raise ExportError('Add at least one Agent.')
    return name, prefix, condition, branches, incoming


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
        portable = {key: value for key, value in portable.items() if key not in ('output_contract', 'artifact_paths', 'native_output')}
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
                raise ExportError(ident + ': review requires a direct handoff from a structured producer with declared artifacts.')
        agent['native_output'] = spec
    elif node.get('output_contract') or node.get('artifact_paths'):
        raise ExportError(ident + ': enable native output to export structured fields or artifact paths.')
    return agent


def compile_workflow(document, agents=None, workspace='.', *, native_config=False):
    """Pure code generation except reading the bundled fixed runtime template."""
    if native_config is not True:
        raise ExportError('Explicit --native-config required: exported calls use native CLI permissions, not V2 sandbox policy.')
    name, prefix, condition, branches, incoming = workflow_paths(document)
    records, normalized, calls, initial = agents or {}, {}, [], ''
    root = Path(workspace).resolve()

    def compile_path(path, previous=None, *, indent='    '):
        nonlocal initial
        for node in path:
            kind, ident = node.get('kind', 'agent'), node['id']
            transfer = incoming[ident][0].get('transfer', {}) if incoming[ident] else {}
            limit = transfer.get('max_chars')
            if kind == 'start':
                config = node.get('config', {})
                if not isinstance(config, dict) or set(config) - {'instructions', 'initial_data'}:
                    raise ExportError('Unsupported Start configuration.')
                instructions = text(config.get('instructions', ''), 'Start instructions')
                raw_data = text(config.get('initial_data', ''), 'Start initial data')
                data = 'Initial data\n' + canonical(json.loads(raw_data)) if raw_data.strip() else ''
                initial = '\n\n'.join(value for value in (instructions, data) if value)
                continue
            if kind == 'output':
                config = node.get('config', {})
                if (not isinstance(config, dict) or config.get('format', 'text') != 'text'
                        or config.get('instructions') or set(config) - {'format', 'instructions'}):
                    raise ExportError('This export supports plain final-answer Output only.')
                if transfer.get('mode') == 'none':
                    raise ExportError('Output requires data; control-only Output is not implemented.')
                return previous, limit
            actual_source = incoming[ident][0]['source'] if incoming[ident] else None
            agent = compile_agent(node, records, root, transfer, normalized.get(actual_source, {}))
            index = len(normalized)
            normalized[ident] = agent
            if previous is None and limit is not None:
                calls.append(indent + f'if len(task) > {limit}:\n'
                             + indent + "    raise WorkflowError('Saved Start handoff limit exceeded; no request sent.')")
            source = 'None' if previous is None or transfer.get('mode') == 'none' else f'results[{previous!r}]'
            calls.append(indent + f"results[{ident!r}] = invoke_agent(context, WORKFLOW['agents'][{index}], "
                         f"task, {source}, {limit!r})")
            previous = ident
        return previous, None

    def finish_path(previous, limit, indent):
        if limit is not None:
            calls.append(indent + f"if len(results[{previous!r}]['answer']) > {limit}:\n"
                         + indent + "    raise WorkflowError('Saved Output limit exceeded; full answer retained in node logs.')")
        calls.append(indent + f'return results[{previous!r}]')

    reviewer, final_limit = compile_path(prefix)
    manifest = dict(version=1, compiler_version=5, name=name, policy='native-cli (not the V2 managed sandbox)',
                    input=initial, source_sha256=sha256(canonical(document).encode()).hexdigest())
    if condition is None:
        finish_path(reviewer, final_limit, '    ')
    else:
        if normalized[reviewer].get('native_output', {}).get('kind') != 'review':
            raise ExportError('Condition requires a direct handoff from a candidate Reviewer v1.')
        producer = incoming[reviewer][0]['source']
        manifest['condition'] = dict(node=condition, reviewer=reviewer,
                                     branches={port: [node['id'] for node in path] for port, path in branches.items()})
        limit = incoming[condition][0].get('transfer', {}).get('max_chars')
        calls.append(f"    if evaluate_condition(context, WORKFLOW['condition'], results[{reviewer!r}], "
                     f"results[{producer!r}]['candidate'], {limit!r}):")
        finish_path(*compile_path(branches['true'], reviewer, indent='        '), '        ')
        calls.append('    else:')
        finish_path(*compile_path(branches['false'], reviewer, indent='        '), '        ')
    manifest['agents'] = list(normalized.values())
    runtime = Path(__file__).with_name('workflow_script_runtime.py').read_text(encoding='utf-8')
    source = '#!/usr/bin/env python3\n# Generated by DevFlower native-script compiler v5.\n'
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
