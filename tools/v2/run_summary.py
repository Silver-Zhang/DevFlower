"""Portable run summaries from bounded, scoped reads of frozen V2 records."""
import hashlib
import json
import math
import re
import sqlite3
import time
from urllib.parse import parse_qs

from orchestration.common import Blocked, canonical
from studio.server import Conflict

from .documents import validate_draft
from .draft_transfer import canonical_json
from .workspace_select import capture_workspace_proof, existing_read_connection, require_current

FORMAT = 'devflower.v2.run-summary'
STATES = frozenset(('COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'))
MAX_ARCHIVE_BYTES = 8192
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_CREATED_AT_MS = 253402300799999
MAX_DEPTH = 32
READ_SECONDS = 2
RUN_ID = re.compile(r'[0-9a-f]{32}')
HEX_64 = re.compile(r'[0-9a-f]{64}')
SURROGATE = re.compile(r'[\ud800-\udfff]')
ERRORS = {
    'RUN_SUMMARY_REQUEST_INVALID': (400, '运行摘要请求无效。'),
    'RUN_SUMMARY_UNAVAILABLE': (404, '找不到当前工作空间中的可导出运行摘要。'),
    'RUN_SUMMARY_NOT_READY': (409, '该运行尚未记录为可导出的状态。'),
    'RUN_SUMMARY_SCOPE_STALE': (409, '当前工作空间暂不能核验，请重新打开后再试。'),
    'RUN_SUMMARY_RECORD_INVALID': (409, '运行记录或冻结定义无法核验。'),
    'RUN_SUMMARY_BUDGET': (409, '运行记录超过摘要读取限制。'),
}


class RunSummaryError(Exception):
    def __init__(self, code):
        self.code = code
        self.status, message = ERRORS[code]
        super().__init__(message)


def request_parameters(query, headers):
    try:
        lengths = headers.get_all('Content-Length', [])
        if (len(query.encode('utf-8')) > 1024 or headers.get_all('Transfer-Encoding', [])
                or lengths not in ([], ['0'])):
            raise ValueError()
        fields = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=2)
        if set(fields) != {'workspace', 'run'} or any(len(values) != 1 for values in fields.values()):
            raise ValueError()
        workspace, run = fields['workspace'][0], fields['run'][0]
        if not HEX_64.fullmatch(workspace) or not RUN_ID.fullmatch(run):
            raise ValueError()
    except ValueError:
        raise RunSummaryError('RUN_SUMMARY_REQUEST_INVALID') from None
    return workspace, run


def _check_time(deadline):
    if time.monotonic() >= deadline:
        raise RunSummaryError('RUN_SUMMARY_BUDGET')


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError()
    return result


def _reject_constant(_):
    raise ValueError()


def _check_value(value, deadline, depth=0):
    _check_time(deadline)
    if isinstance(value, (dict, list)):
        if depth == MAX_DEPTH:
            raise ValueError()
        if isinstance(value, dict):
            if any(SURROGATE.search(key) for key in value):
                raise ValueError()
            value = value.values()
        for item in value:
            _check_value(item, deadline, depth + 1)
    elif isinstance(value, str) and SURROGATE.search(value):
        raise ValueError()
    elif type(value) is float and not math.isfinite(value):
        raise ValueError()


def _read_record(db, kind, key, deadline):
    _check_time(deadline)
    row = db.execute('SELECT rowid,typeof(data) FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
    if row is None:
        raise RunSummaryError('RUN_SUMMARY_UNAVAILABLE')
    rowid, kind_of_data = row
    if kind_of_data not in ('text', 'blob'):
        raise RunSummaryError('RUN_SUMMARY_RECORD_INVALID')
    # sqlite3_blob_bytes is metadata only. Never materialize the body in SQL.
    with db.blobopen('records', 'data', rowid, readonly=True) as blob:
        length = len(blob)
        if length > MAX_RECORD_BYTES:
            raise RunSummaryError('RUN_SUMMARY_BUDGET')
        _check_time(deadline)
        raw = blob.read(length)
    _check_time(deadline)
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError()
    _check_value(value, deadline)
    return value


def _frozen_records(db, workspace, run_id, deadline):
    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
    try:
        db.execute('BEGIN')
        run = _read_record(db, 'run', run_id, deadline)
        if run.get('id') != run_id or run.get('workspace') != workspace:
            raise RunSummaryError('RUN_SUMMARY_UNAVAILABLE')
        definition_id = run.get('definition_id')
        if definition_id is None:
            raise RunSummaryError('RUN_SUMMARY_UNAVAILABLE')
        if not isinstance(definition_id, str) or not HEX_64.fullmatch(definition_id):
            raise ValueError()
        definition = _read_record(db, 'definition', definition_id, deadline)
        if definition.get('workspace') != workspace:
            raise RunSummaryError('RUN_SUMMARY_UNAVAILABLE')
        if definition.get('id') != definition_id:
            raise ValueError()
        return run, definition
    except sqlite3.Error:
        _check_time(deadline)
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        _check_time(deadline)
        raise RunSummaryError('RUN_SUMMARY_RECORD_INVALID') from None
    finally:
        db.set_progress_handler(None, 0)


def _created_at_ms(value):
    if type(value) in (int, float) and 0 <= value <= (MAX_CREATED_AT_MS + 1) // 1000:
        millis = math.floor(value * 1000)
        if millis <= MAX_CREATED_AT_MS:
            return millis
    return None


def _archive(run, definition, deadline):
    try:
        definition_id = definition['id']
        frozen = {key: value for key, value in definition.items() if key != 'id'}
        if hashlib.sha256(canonical(frozen)).hexdigest() != definition_id:
            raise ValueError()
        _check_time(deadline)
        graph = validate_draft(definition['draft_document'])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        _check_time(deadline)
        raise RunSummaryError('RUN_SUMMARY_RECORD_INVALID') from None
    state = run.get('state')
    if not isinstance(state, str) or state not in STATES:
        raise RunSummaryError('RUN_SUMMARY_NOT_READY')
    fingerprint = hashlib.sha256((FORMAT + '\0' + run['id'] + '\0' + definition_id).encode('ascii')).hexdigest()
    snapshot = dict(agent_count=len(graph['nodes']), edge_count=len(graph['edges']),
                    created_at_ms=_created_at_ms(run.get('created_at')), source_fingerprint=fingerprint,
                    source_recorded_state=state)
    sha = hashlib.sha256(canonical_json(snapshot).encode('utf-8')).hexdigest()
    value = dict(authority=False, format=FORMAT, snapshot=snapshot, snapshot_sha256=sha, version=1)
    raw = (canonical_json(value) + '\n').encode('utf-8')
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise RunSummaryError('RUN_SUMMARY_BUDGET')
    return raw


def read_run_summary(store, workspace, run_id):
    deadline = time.monotonic() + READ_SECONDS
    try:
        proof = capture_workspace_proof(store.state)
        _check_time(deadline)
        if store.workspace(workspace, existing_only=True).get('id') != workspace:
            raise ValueError()
        _check_time(deadline)
        try:
            with existing_read_connection(store.state, proof) as db:
                run, definition = _frozen_records(db, workspace, run_id, deadline)
            _check_time(deadline)
            return _archive(run, definition, deadline)
        finally:
            # The original proof must survive projection as well as both DB reads.
            require_current(store.state, proof)
            _check_time(deadline)
    except (ValueError, TypeError, KeyError, OSError, Conflict, Blocked):
        _check_time(deadline)
        raise RunSummaryError('RUN_SUMMARY_SCOPE_STALE') from None
