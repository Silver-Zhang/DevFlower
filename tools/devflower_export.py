#!/usr/bin/env python3
"""Compile a saved sequential canvas into one standalone native-CLI Python file.

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


def chain(document):
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
        if kind not in ('start', 'agent', 'output'):
            raise ExportError('Export not implemented for node ' + ident + ' (' + str(kind) + '). No graph was flattened.')
        if kind == 'agent':
            allowed = ({'agent_id', 'agent_revision'} if 'agent_id' in node
                       else LEGACY_AGENT_FIELDS | NATIVE_AGENT_FIELDS)
            known_fields(node, NODE_FIELDS | allowed | {'output_contract', 'artifact_paths'}, ident)
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
        if edge.get('source_port', 'out') != 'out' or edge.get('target_port', 'in') != 'in':
            raise ExportError('Unsupported port: ' + ident)
        transfer = edge.get('transfer', {})
        terminal = nodes[source].get('kind') == 'start' or nodes[target].get('kind') == 'output'
        modes = ('answer', 'none', 'envelope') if terminal else ('answer', 'none')
        if not isinstance(transfer, dict) or set(transfer) - {'mode', 'max_chars'} or transfer.get('mode', 'answer') not in modes:
            raise ExportError('Unsupported handoff on ' + ident + '; structured data must not be silently discarded.')
        limit = transfer.get('max_chars')
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ExportError('Invalid handoff limit: ' + ident)
        if nodes[source].get('kind') == 'start' and transfer.get('mode') == 'none':
            raise ExportError('Control-only Start is not supported by this export.')
        edge_ids.add(ident)
        incoming[target].append(edge)
        outgoing[source].append(edge)
    roots = [key for key in nodes if not incoming[key]]
    if len(roots) != 1 or any(len(incoming[key]) > 1 or len(outgoing[key]) > 1 for key in nodes):
        raise ExportError('This first compiler supports one sequential chain, not fan-out/merge.')
    ordered, seen, current = [], set(), roots[0]
    while current is not None:
        if current in seen:
            raise ExportError('Cycle detected.')
        seen.add(current)
        ordered.append(nodes[current])
        current = outgoing[current][0]['target'] if outgoing[current] else None
    if len(seen) != len(nodes):
        raise ExportError('Disconnected or cyclic graph.')
    for index, node in enumerate(ordered):
        if node.get('kind') == 'start' and index != 0 or node.get('kind') == 'output' and index != len(ordered)-1:
            raise ExportError('Start/Output must be endpoints.')
    if not any(n.get('kind', 'agent') == 'agent' for n in ordered):
        raise ExportError('Add at least one Agent.')
    return name, ordered, incoming


def compile_workflow(document, agents=None, workspace='.', *, native_config=False):
    """Pure code generation except reading the bundled fixed runtime template."""
    if native_config is not True:
        raise ExportError('Explicit --native-config required: exported calls use native CLI permissions, not V2 sandbox policy.')
    name, ordered, incoming = chain(document)
    records, normalized, calls, initial = agents or {}, [], [], ''
    final_limit = None
    root = Path(workspace).resolve()
    previous = None
    for node in ordered:
        kind, ident = node.get('kind', 'agent'), node['id']
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
            if not isinstance(config, dict) or config.get('format', 'text') != 'text' or config.get('instructions') or set(config) - {'format', 'instructions'}:
                raise ExportError('This export supports plain final-answer Output only.')
            transfer = incoming[ident][0].get('transfer', {})
            if transfer.get('mode') == 'none':
                raise ExportError('Output requires data; control-only Output is not implemented.')
            final_limit = transfer.get('max_chars')
            continue
        if node.get('output_contract') or node.get('artifact_paths'):
            raise ExportError(ident + ': structured contracts/artifact transfer are not yet exported.')
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
            portable = {key: value for key, value in portable.items() if key not in ('output_contract', 'artifact_paths')}
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
        index = len(normalized)
        normalized.append(agent)
        transfer = incoming[ident][0].get('transfer', {}) if incoming[ident] else {}
        if previous is None and transfer.get('max_chars') is not None:
            calls.append('    if len(task) > ' + repr(transfer['max_chars']) + ':\n'
                         "        raise WorkflowError('Saved Start handoff limit exceeded; no request sent.')")
        source = 'None' if previous is None or transfer.get('mode') == 'none' else 'results[' + repr(previous) + ']'
        calls.append('    results[' + repr(ident) + '] = invoke_agent(context, WORKFLOW[\'agents\'][' + str(index)
                     + '], task, ' + source + ', ' + repr(transfer.get('max_chars')) + ')')
        previous = ident
    manifest = dict(version=1, compiler_version=3, name=name, policy='native-cli (not the V2 managed sandbox)',
                    input=initial, agents=normalized,
                    source_sha256=sha256(canonical(document).encode()).hexdigest())
    runtime = Path(__file__).with_name('workflow_script_runtime.py').read_text(encoding='utf-8')
    source = '#!/usr/bin/env python3\n# Generated by DevFlower native-script compiler v3.\n'
    source += '# Native CLI settings/permissions apply. Review before execution. No GUI/service dependency.\n'
    source += runtime + '\n\nWORKFLOW = ' + pprint.pformat(manifest, width=100, sort_dicts=False) + '\n\n'
    source += 'def run_workflow(context, task):\n    results = {}\n' + '\n'.join(calls)
    if final_limit is not None:
        source += '\n    if len(results[' + repr(previous) + "][\'answer\']) > " + repr(final_limit) + ':\n'
        source += "        raise WorkflowError('Saved Output limit exceeded; full answer retained in node logs.')\n"
    source += '\n    return results[' + repr(previous) + ']\n\n\n'
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
