"""Bounded, read-only projections of existing V2 run evidence."""
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs

from orchestration.common import canonical, digest
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_data import PRIVATE_FIELDS

RUN = re.compile(r'[0-9a-f]{32}')
IDENT = re.compile(r'[0-9a-f]{64}')
UINT = re.compile(r'0|[1-9][0-9]{0,18}')
EVENT_PAGE = 40
EVENT_BYTES = 256_000
PAYLOAD_PAGE = 8000
RECORD_BYTES = 8_000_000
EXPORT_BYTES = 1_500_000
INLINE_BYTES = 32_000
END = {'COMPLETED', 'FAILED', 'CANCELLED', 'SKIPPED'}
RUN_FIELDS = ('id', 'workspace', 'definition_id', 'draft_id', 'draft_revision', 'launch_intent',
              'created_at', 'started_at', 'finished_at', 'state', 'persisted_state', 'reason', 'calls',
              'actual_calls', 'designer_runtime', 'paused', 'waiting_approval', 'resume_available',
              'cancel_requested', 'provider_outcome', 'local_status', 'worker_generation', 'partial_failures')
NODE_FIELDS = ('id', 'run', 'node', 'name', 'kind', 'state', 'iteration', 'iteration_path', 'round', 'attempt',
               'started_at', 'finished_at', 'reason', 'waiting', 'selected_port', 'merge_policy',
               'merge_activations', 'loop_round', 'loop_exit', 'question', 'policy', 'approval_deadline',
               'request_id', 'session_binding_id', 'observed_session', 'native_turn_id', 'actual_request_sha256',
               'input_sent_sha256', 'input_sent_bytes', 'input_sent_at', 'input_sent_operation_id',
               'initialization_included', 'receipt_verified', 'terminal_failure_verified', 'recovery_evidence',
               'provider_outcome', 'provider', 'model', 'effort', 'error_class', 'error_category', 'visible_error', 'output_instructions')
EVENT_FIELDS = ('kind', 'state', 'at', 'event_id', 'definition_id', 'workspace', 'node', 'activation', 'iteration_path',
                'iteration', 'attempt', 'edge', 'handoff_id', 'source', 'target_activation', 'target_node',
                'source_round', 'target_round', 'content_sha256', 'source_content_sha256', 'received_at',
                'fields', 'port', 'loop_round', 'loop_exit', 'merge_policy', 'merge_activations', 'groups',
                'action', 'reason', 'error_class', 'decision', 'actor', 'input_sent_sha256', 'input_sent_bytes')
INPUT_FIELDS = ('edge', 'source', 'iteration', 'iteration_path', 'activation', 'source_name', 'target_name',
                'source_round', 'target_round', 'target_activation', 'target_node', 'content_sha256',
                'source_content_sha256', 'fields', 'handoff_id', 'received_at', 'content', 'truncation')
RECEIPT_FIELDS = ('session_id', 'native_turn_id', 'requested_model', 'requested_effort', 'resolved_model',
                  'resolved_effort', 'terminal', 'transport', 'raw_report_sha256')
PROCESS_FIELDS = ('operation_id', 'exit', 'duration', 'timeout', 'cancelled', 'overflow', 'stdout_sha256',
                  'stderr_sha256', 'input_sent_bytes', 'input_sent_sha256')
OWNER_FIELDS = ('pid', 'pgid', 'sid', 'start', 'host', 'boot_id', 'state')
TOOL_FIELDS = ('event', 'id', 'tool', 'command', 'output', 'status', 'exit_code', 'at')
PAYLOAD_FIELDS = {'output', 'actual_request', 'request_sections', 'inputs', 'native_tool_activity',
                  'decision', 'loop_results'}
HANDOFFS = {'handoff-ready', 'handoff-received', 'handoff-sent'}
WORKER_EVENTS = {
    'run-authorized': ('state', 'created_at'),
    'worker-bound': ('launch_state', 'worker_published_at'),
    'worker-entered': ('worker_entered_at',),
    'worker-resume-authorized': ('generation',),
    'local-worker-launch-failed': ('stage', 'has_child', 'state', 'reason_code', 'finished_at'),
}
PRIVATE = PRIVATE_FIELDS | {'env', 'argv', 'command_line', 'native_history', 'native_child', 'readonly_files',
                            'config_root', 'runtime_sources', 'runtime_source', 'executable', 'storage',
                            'private_state', 'api_token', 'stderr', 'stdout', 'raw_stderr', 'raw_stdout'}


def take(value, fields):
    return {field: value[field] for field in fields if field in value} if isinstance(value, dict) else {}


def parameters(query, headers, route):
    required = {'workspace', 'run'}
    allowed = required | {'activation', 'after'} if route == 'events' else required
    if route == 'payload':
        required |= {'activation', 'field'}
        allowed = required | {'offset', 'sha256', 'edge'}
    try:
        if (len(query) > 1024 or headers.get_all('Transfer-Encoding', [])
                or headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise ValueError()
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=7)
        if not required <= values.keys() <= allowed or any(len(v) != 1 for v in values.values()):
            raise ValueError()
        value = {k: v[0] for k, v in values.items()}
        if (not IDENT.fullmatch(value['workspace']) or not RUN.fullmatch(value['run'])
                or any(k in value and not IDENT.fullmatch(value[k]) for k in ('activation', 'sha256'))
                or 'field' in value and value['field'] not in PAYLOAD_FIELDS
                or 'edge' in value and (value['field'] != 'inputs' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value['edge']))):
            raise ValueError()
        for key in ('after', 'offset'):
            if key in value:
                if not UINT.fullmatch(value[key]) or int(value[key]) >= 2**63:
                    raise ValueError()
                value[key] = int(value[key])
        if value.get('offset') and not value.get('sha256'):
            raise ValueError()
        return value
    except ValueError:
        raise Invalid('运行检查需要明确的工作区、运行和有界游标。') from None


class RunInspector:
    def __init__(self, store):
        self.store = store
        home = Path.home()
        self.paths = sorted({str(store.state), *(str(home / folder) for folder in ('.codex', '.claude', '.claude.json')),
                             *(value for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR') if
                               (value := os.environ.get(key)) and value.startswith('/') and len(value) > 1)},
                            key=len, reverse=True)
        self.secrets = sorted({value for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                                                 'CLAUDE_CODE_OAUTH_TOKEN', 'DEEPSEEK_API_KEY')
                               if (value := os.environ.get(key)) and len(value) >= 8}, key=len, reverse=True)

    def text(self, value):
        value = safe_text(value)
        for item in (*self.paths, *self.secrets):
            value = value.replace(item, '[已隐藏]')
        return value

    def clean(self, value, depth=0):
        if depth >= 36:
            return '[超过公开数据层级上限]'
        if isinstance(value, dict):
            return {self.text(key): self.clean(item, depth+1) for key, item in value.items()
                    if key.casefold() not in PRIVATE}
        if isinstance(value, list):
            return [self.clean(item, depth+1) for item in value]
        return self.text(value) if isinstance(value, str) else value

    def receipt(self, value):
        result = take(value, RECEIPT_FIELDS)
        if isinstance(value, dict):
            if isinstance(value.get('transport'), dict):
                result['transport'] = take(value['transport'], ('mode',))
            if isinstance(value.get('process'), dict):
                result['process'] = take(value['process'], PROCESS_FIELDS)
            if isinstance(value.get('native_plan'), dict):
                result['native_plan'] = take(value['native_plan'], ('finalized', 'sha256', 'item_id', 'mode'))
            if isinstance(value.get('native_agent_transition'), dict):
                result['native_agent_transition'] = take(value['native_agent_transition'],
                                                         ('plan_sha256', 'session_id', 'authorization', 'mode'))
            if isinstance(value.get('usage'), dict):
                result['usage'] = self.usage(value['usage'])
        return self.clean(result)

    def usage(self, value):
        known = ('semantics', 'source', 'input_tokens', 'output_tokens', 'cached_input_tokens',
                 'cache_read_input_tokens', 'cache_creation_input_tokens', 'total_cost_usd', 'cost_usd')
        result = take(value, known)
        if isinstance(value.get('usage'), dict):
            result['usage'] = take(value['usage'], known)
        return result

    def payload_value(self, row, field):
        value = row[field]
        if field == 'inputs' and isinstance(value, list):
            value = [take(item, INPUT_FIELDS) for item in value]
        elif field == 'native_tool_activity' and isinstance(value, list):
            value = [take(item, TOOL_FIELDS) for item in value]
        elif field == 'request_sections' and isinstance(value, list):
            value = [take(item, ('title', 'text')) for item in value]
        return self.clean(value)

    @staticmethod
    def serialize(value):
        return value if isinstance(value, str) else canonical(value).decode()

    def bounded(self, value, limit=INLINE_BYTES):
        encoded = self.serialize(value)
        size = len(encoded.encode())
        if size <= limit:
            return value, None
        return None, dict(bytes=size, chars=len(encoded), sha256=digest(encoded.encode()))

    def node(self, row, *, compatibility=False, limit=INLINE_BYTES):
        value = self.clean(take(row, NODE_FIELDS))
        if compatibility and isinstance(row.get('process'), dict):
            value['process'] = self.clean(take(row['process'], OWNER_FIELDS))
        for field in ('receipt', 'failure_receipt'):
            if isinstance(row.get(field), dict):
                value[field] = self.receipt(row[field])
        if isinstance(row.get('approval_decision'), dict):
            value['approval_decision'] = self.clean(take(row['approval_decision'],
                ('actor', 'decision', 'at', 'activation', 'run', 'workspace', 'definition_id')))
        omitted = {}
        for field in sorted(PAYLOAD_FIELDS & row.keys()):
            item, fact = self.bounded(self.payload_value(row, field), limit)
            if fact:
                omitted[field] = fact
            else:
                value[field] = item
        if omitted:
            value['paged_fields'] = omitted
        return value

    def snapshot(self, value):
        run = self.clean(take(value['run'], RUN_FIELDS))
        if isinstance(value['run'].get('worker'), dict):
            run['worker'] = self.clean(take(value['run']['worker'], OWNER_FIELDS))
        if 'output' in value['run']:
            item, fact = self.bounded(self.clean(value['run']['output']))
            run.update({'output_omitted': fact} if fact else {'output': item})
        definition = value['definition']
        public_def = self.clean(take(definition, ('id', 'draft_id', 'draft_revision', 'workspace', 'draft_document',
                                                 'graph', 'graph_sha256', 'designer_runtime')))
        if isinstance(definition.get('agent_configs'), dict):
            # The existing Plan confirmation needs only the approved working directory.
            public_def['agent_configs'] = self.clean({path: {'config': take(row.get('config'), ('cwd',))}
                for path, row in definition['agent_configs'].items() if isinstance(row, dict)})
        nodes = {row['id']: row for row in value['nodes']}
        events = [self.event(event, value['run']['id'], nodes, compatibility=True) for event in value['events'][-100:]]
        limit = min(INLINE_BYTES, max(3000, 256000 // max(len(nodes), 1)))
        return dict(take(value, ('state', 'provider_outcome', 'worker_alive', 'worker_observation')),
                    run=run, definition=public_def, nodes=[self.node(row, compatibility=True, limit=limit) for row in nodes.values()],
                    events=events)

    def event(self, row, run, nodes, *, include_content=False, compatibility=False):
        worker_fields = WORKER_EVENTS.get(row.get('kind'))
        if compatibility and worker_fields is not None and row.get('event_version') == 1:
            # Existing diagnostics consume this stable event envelope.
            value = take(row, ('seq', 'kind', 'at', 'event_version', 'event_id', 'run',
                               'definition_id', 'aggregate_kind', 'aggregate_id'))
            value['cause'] = take(row.get('cause'), ('kind', 'event_id', 'reservation_run_id'))
            value['payload'] = take(row.get('payload'), worker_fields)
            return self.clean(value)
        value = self.clean(take(row, EVENT_FIELDS))
        value.update(seq=row['seq'], run=run)
        if row.get('kind') in HANDOFFS:
            source = nodes.get(row.get('activation'))
            target = nodes.get(row.get('target_activation'))
            if (source and target and row.get('run') == run and source['node'] == row.get('source')
                    and target['node'] == row.get('node') and row.get('target_round', []) == target.get('iteration_path', [])
                    and row.get('source_round', []) == source.get('iteration_path', [])):
                value.update(scope='activation', activation=source['id'] if compatibility else target['id'],
                             source_activation=source['id'], iteration_path=target.get('iteration_path', []))
            else:
                value.update(scope='unassociated')
                if not compatibility:
                    value['activation'] = None
            if 'content' in row:
                item, fact = self.bounded(self.clean(row['content']), INLINE_BYTES if include_content else 3000)
                value.update({'content_omitted': fact} if fact else {'content': item})
        else:
            node = nodes.get(row.get('activation'))
            if (node and row.get('run') == run and row.get('node') == node['node']
                    and row.get('iteration_path', []) == node.get('iteration_path', [])):
                value.update(scope='activation', iteration_path=node.get('iteration_path', []))
            else:
                value.update(scope='unassociated' if row.get('node') or row.get('activation') else 'run',
                             activation=None)
        cause = row.get('cause')
        if isinstance(cause, dict):
            value['causation'] = self.clean(take(cause, ('kind', 'event_id')))
        return value

    def authorize(self, tx, workspace, run):
        raw = tx.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run,)).fetchone()
        saved = json.loads(raw[0]) if raw else {}
        if saved.get('id') != run or saved.get('workspace') != workspace:
            raise Conflict('找不到当前工作空间中的运行详情。')
        return saved

    def activation_rows(self, tx, run):
        rows = tx.execute("""SELECT id, json_extract(data, '$.node'), json_extract(data, '$.iteration_path')
                           FROM records WHERE kind='activation' AND json_extract(data, '$.run')=? LIMIT 513""", (run,))
        return {key: dict(id=key, node=path, iteration_path=json.loads(rounds) if rounds else [])
                for key, path, rounds in rows}

    def events(self, workspace, run, activation=None, after=0):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as tx:
            self.authorize(tx, workspace, run)
            nodes = self.activation_rows(tx, run)
            if activation is not None and activation not in nodes:
                raise Conflict('该激活不属于当前运行。')
            rows = tx.execute("""SELECT seq, CASE WHEN length(CAST(data AS BLOB))<=? THEN data ELSE NULL END
                               FROM events WHERE run=? AND seq>? ORDER BY seq LIMIT ?""",
                              (EVENT_BYTES, run, after, EVENT_PAGE + 1)).fetchall()
        events = []
        for seq, raw in rows[:EVENT_PAGE]:
            row = json.loads(raw) if raw else {'kind': 'event-oversized'}
            item = self.event(dict(row, seq=seq), run, nodes)
            if activation is None or activation in (item.get('activation'), item.get('source_activation')):
                events.append(item)
        return dict(run=run, activation=activation, events=events,
                    after=rows[min(len(rows), EVENT_PAGE)-1][0] if rows else after, more=len(rows)>EVENT_PAGE)

    def payload(self, workspace, run, activation, field, offset=0, sha256=None, edge=None):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as tx:
            self.authorize(tx, workspace, run)
            raw = tx.execute("""SELECT rowid, json_extract(data, '$.run') FROM records
                                WHERE kind='activation' AND id=?""", (activation,)).fetchone()
            if not raw or raw[1] != run:
                raise Conflict('该激活不属于当前运行。')
            with tx.blobopen('records', 'data', raw[0], readonly=True) as blob:
                if len(blob) > RECORD_BYTES:
                    raise Conflict('激活记录超过安全读取上限。')
                row = json.loads(blob.read())
        if field not in row:
            raise Conflict('此激活未记录所选数据。')
        value = self.payload_value(row, field)
        if edge is not None:
            value = [item for item in value if item.get('edge') == edge and item.get('target_activation') == activation] if isinstance(value, list) else []
            if not value:
                raise Conflict('此激活没有所选连线的交接记录。')
        text = self.serialize(value)
        identity = digest(text.encode())
        if sha256 is not None and sha256 != identity:
            raise Conflict('数据在读取期间变化。请从第一页重新读取。')
        if offset > len(text):
            raise Invalid('数据游标超出当前记录。')
        part = text[offset:offset+PAYLOAD_PAGE]
        next_offset = offset + len(part)
        return dict(run=run, activation=activation, field=field, offset=offset, text=part, sha256=identity,
                    format='text' if isinstance(value, str) else 'json', chars=len(text), bytes=len(text.encode()),
                    previous_offset=max(0, offset-PAYLOAD_PAGE) if offset else None,
                    next_offset=next_offset if next_offset < len(text) else None,
                    evidence='persisted_public_record',
                    input_sent_sha256=row.get('input_sent_sha256') if field=='actual_request' else None)

    def export(self, workspace, run):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as tx:
            saved = self.authorize(tx, workspace, run)
            nodes = self.activation_rows(tx, run)
            max_seq, count = tx.execute('SELECT max(seq), count(*) FROM events WHERE run=?', (run,)).fetchone()
            frozen = tx.execute("SELECT data FROM records WHERE kind='definition' AND id=?",
                                (saved.get('definition_id'),)).fetchone()
            definition = json.loads(frozen[0]) if frozen else {}
            if (definition.get('workspace') != workspace or definition.get('id') != saved.get('definition_id')
                    or definition['id'] != digest(canonical({key: value for key, value in definition.items() if key != 'id'}))):
                raise Conflict('冻结定义身份无法核验。')
            run_summary = self.clean(take(saved, RUN_FIELDS))
            run_summary['persisted_state'] = run_summary.pop('state')
            output, output_omitted = self.bounded(self.clean(saved.get('output')), 32000)
            if output_omitted:
                run_summary['output_omitted'] = output_omitted
            elif 'output' in saved:
                run_summary['output'] = output
            result = dict(format='devflower.v2.run-detail', version=1, evidence='persisted_public_records', run=run_summary,
                          definition=self.clean(take(definition, ('id', 'workspace', 'draft_id', 'draft_revision',
                                                                   'graph_sha256', 'designer_runtime'))),
                          through_event=max_seq or 0, activations=[], events=[])
            budget = EXPORT_BYTES - len(canonical(result)) - 4096
            omitted_fields = 0
            for key in nodes:
                rowid, = tx.execute("SELECT rowid FROM records WHERE kind='activation' AND id=?", (key,)).fetchone()
                with tx.blobopen('records', 'data', rowid, readonly=True) as blob:
                    if len(blob) > RECORD_BYTES:
                        continue
                    row = json.loads(blob.read())
                item = self.node(row, limit=12000)
                if 'receipt_verified' in item:
                    item['receipt_recorded_as_verified'] = item.pop('receipt_verified')
                size = len(canonical(item)) + 1
                if size > budget:
                    break
                budget -= size
                omitted_fields += len(item.get('paged_fields', {}))
                result['activations'].append(item)
            event_omissions = 0
            unassociated = 0
            for seq, raw in tx.execute("""SELECT seq, CASE WHEN length(CAST(data AS BLOB))<=? THEN data END
                                         FROM events WHERE run=? ORDER BY seq LIMIT 250""", (EVENT_BYTES, run)):
                item = self.event(dict(json.loads(raw) if raw else {'kind': 'event-oversized'}, seq=seq),
                                  run, nodes, include_content=True)
                size = len(canonical(item)) + 1
                if size > budget:
                    break
                budget -= size
                event_omissions += int('content_omitted' in item or not raw)
                unassociated += int(item['scope'] == 'unassociated')
                result['events'].append(item)
        missing_activations = len(nodes) - len(result['activations'])
        missing_events = count - len(result['events'])
        result['coverage'] = dict(terminal_recorded=saved['state'] in END,
                                  omitted_activations=missing_activations, omitted_activation_payloads=omitted_fields,
                                  omitted_events=missing_events, omitted_event_payloads=event_omissions,
                                  included_unassociated_events=unassociated,
                                  causal_associations_complete=not (unassociated or missing_events),
                                  omitted_run_output=bool(output_omitted),
                                  complete_public_snapshot=not (missing_activations or omitted_fields
                                      or missing_events or event_omissions or output_omitted))
        return canonical(result)
