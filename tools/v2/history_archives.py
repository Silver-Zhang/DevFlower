"""Portable, non-authoritative run history, separate from executable records."""
import hmac
import json
import re
import time

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from .native_history_config import unique_pairs
from .run_inspector import (EVENT_FIELDS, EXPORT_BYTES, IDENT, INPUT_FIELDS, NODE_FIELDS, RUN, RUN_FIELDS,
                            RunInspector, take)

FORMAT = 'devflower.v2.run-history'
KIND = 'run-history-archive'
MAX_BYTES = EXPORT_BYTES + 8192
LIMIT = 100
DETAIL_FIELDS = {'format', 'version', 'evidence', 'run', 'definition', 'through_event', 'activations', 'events', 'coverage'}


def object_id(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value)


def archive_detail(raw):
    try:
        envelope = json.loads(raw, object_pairs_hook=unique_pairs)
        if (not isinstance(envelope, dict) or set(envelope) != {'format','version','authority','source_workspace','detail','sha256'}
                or envelope['format'] != FORMAT or type(envelope['version']) is not int or envelope['version'] != 1
                or envelope['authority'] is not False or not object_id(envelope['source_workspace'], IDENT)
                or not object_id(envelope['sha256'], IDENT) or canonical(envelope) != raw):
            raise ValueError()
        detail = envelope['detail']
        if (not isinstance(detail, dict) or set(detail) != DETAIL_FIELDS
                or detail['format'] != 'devflower.v2.run-detail' or type(detail['version']) is not int or detail['version'] != 1
                or detail['evidence'] != 'persisted_public_records'):
            raise ValueError()
        run, definition = detail['run'], detail['definition']
        if (not isinstance(run, dict) or not isinstance(definition, dict)
                or not object_id(run.get('id'), RUN) or not object_id(definition.get('id'), IDENT)
                or not object_id(run.get('persisted_state'), re.compile(r'[A-Z_]{1,40}'))
                or run.get('definition_id') != definition['id'] or run.get('workspace') != envelope['source_workspace']
                or definition.get('workspace') != envelope['source_workspace']
                or type(detail['through_event']) is not int or detail['through_event'] < 0
                or not isinstance(detail['coverage'], dict)):
            raise ValueError()
        nodes, events = detail['activations'], detail['events']
        if (not isinstance(nodes, list) or len(nodes) > 512 or not isinstance(events, list) or len(events) > 250
                or any(not isinstance(row, dict) or row.get('run') != run['id'] or not object_id(row.get('id'), IDENT) for row in nodes)
                or any(any(row.get(key) is not None and not isinstance(row[key], str)
                           for key in ('name','node','state')) or
                       ('iteration_path' in row and (not isinstance(row['iteration_path'], list)
                        or len(row['iteration_path']) > 2
                        or any(type(part) is not int or part < 1 for part in row['iteration_path'])))
                       for row in nodes)
                or len({row['id'] for row in nodes}) != len(nodes)):
            raise ValueError()
        prior = 0
        for event in events:
            if (not isinstance(event, dict) or type(event.get('seq')) is not int
                    or not prior < event['seq'] <= detail['through_event']
                    or any(event.get(key) is not None and not isinstance(event[key], str) for key in ('kind','node'))):
                raise ValueError()
            prior = event['seq']
        # Reject NaN/Infinity even when present in a foreign JSON file.
        json.dumps(detail, allow_nan=False)
    except (ValueError, RecursionError, TypeError, UnicodeError, OverflowError):
        raise Invalid('请选择格式有效的 DevFlower 运行历史归档。') from None
    body = {key: value for key, value in envelope.items() if key != 'sha256'}
    if not hmac.compare_digest(envelope['sha256'], digest(canonical(body))):
        raise Invalid('运行历史归档校验失败。')
    return envelope


class HistoryArchives:
    def __init__(self, store, inspector=None):
        self.store = store
        self.inspector = inspector or RunInspector(store)

    def export(self, workspace, run):
        detail = json.loads(self.inspector.export(workspace, run))
        value = dict(format=FORMAT, version=1, authority=False, source_workspace=workspace, detail=detail)
        raw = canonical(dict(value, sha256=digest(canonical(value))))
        if len(raw) > MAX_BYTES:
            raise Invalid('运行历史归档超过文件大小上限。')
        return raw

    def visible(self, detail):
        result = take(detail, DETAIL_FIELDS)
        result['run'] = take(detail['run'], (*RUN_FIELDS, 'execution_budget', 'output', 'output_omitted'))
        result['definition'] = take(detail['definition'], (
            'id','workspace','draft_id','draft_revision','graph_sha256','designer_runtime','task_files'))
        result['activations'] = []
        for row in detail['activations']:
            value = take(row, (*NODE_FIELDS, 'output','actual_request','request_sections','native_tool_activity',
                              'decision','loop_results','paged_fields','inputs','receipt_recorded_as_verified'))
            value.pop('receipt_verified', None)
            if isinstance(value.get('inputs'), list):
                value['inputs'] = [take(item, INPUT_FIELDS) for item in value['inputs']]
            if 'receipt' in row:
                value['receipt'] = self.inspector.receipt(row['receipt'])
            result['activations'].append(value)
        result['events'] = [take(row, (*EVENT_FIELDS, 'seq','scope','run','content','content_omitted','payload','source_activation'))
                            for row in detail['events']]
        from .data_handoff import task_literals
        from orchestration.privacy import redact
        return redact(self.inspector.clean(result), task_literals())

    def import_file(self, workspace, raw):
        self.store.workspace(workspace)
        if not 0 < len(raw) <= MAX_BYTES:
            raise Invalid('运行历史归档超过文件大小上限。')
        envelope = archive_detail(raw)
        detail = self.visible(envelope['detail'])
        ident = digest(canonical([workspace, envelope['sha256']]))
        row = dict(id=ident, workspace=workspace, sha256=envelope['sha256'], authority=False,
                   source_workspace=envelope['source_workspace'], source_run=detail['run']['id'],
                   source_state=detail['run'].get('persisted_state'), imported_at=time.time(), detail=detail)
        encoded = canonical(row)
        if len(encoded) > MAX_BYTES:
            raise Invalid('公开历史归档超过存储预算。')
        with self.store.db.tx() as db:
            prior = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, ident)).fetchone()
            if prior:
                return self.metadata(json.loads(prior[0]))
            count, = db.execute("""SELECT count(*) FROM records WHERE kind=?
                AND json_extract(data,'$.workspace')=?""", (KIND, workspace)).fetchone()
            if count >= LIMIT:
                raise Conflict('当前工作区已达到历史归档数量上限。')
            db.execute('INSERT INTO records VALUES(?,?,?)', (KIND, ident, encoded.decode()))
        return self.metadata(row)

    @staticmethod
    def metadata(row):
        return {key: value for key, value in row.items() if key != 'detail'}

    def listing(self, workspace):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as db:
            records = db.execute("""
                SELECT json_remove(data,'$.detail') FROM records WHERE kind=?
                    AND json_extract(data,'$.workspace')=? ORDER BY rowid DESC LIMIT ?
            """, (KIND, workspace, LIMIT)).fetchall()
        return {'archives': [json.loads(raw) for raw, in records]}

    def read(self, workspace, ident):
        self.store.workspace(workspace)
        if not object_id(ident, IDENT):
            raise Invalid('请选择明确的历史归档。')
        row = self.store.db.get(KIND, ident)
        if row.get('workspace') != workspace or row.get('authority') is not False:
            raise Conflict('历史归档不属于当前工作区。')
        return dict(row,detail=self.visible(row['detail']))
