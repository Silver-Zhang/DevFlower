"""Private, same-transaction writers for V2 worker lifecycle observations."""
import json
import math
import re
import time

from orchestration.common import Blocked, canonical, digest

ERROR = '工作进程启动证据不一致，未继续进入执行'
FAILURE_REASON = '工作进程启动未确认，未自动重试'
IDENTITY_FIELDS = ('host', 'boot_id', 'pid', 'start', 'pgid', 'sid')
INTERNAL_FIELDS = (
    'worker_lifecycle_version', 'worker_authorized_seq', 'worker_bound_seq',
    'worker_entered_seq', 'worker_entered_at',
)


def _require(condition):
    if not condition:
        raise Blocked(ERROR)


def versioned(row):
    if 'worker_lifecycle_version' not in row:
        _require(not any(key in row for key in INTERNAL_FIELDS[1:]))
        return False
    version = row['worker_lifecycle_version']
    _require(type(version) is int and version == 1)
    return True


def complete_identity(identity):
    return (isinstance(identity, dict)
            and all(isinstance(identity.get(key), str) and identity[key] for key in ('host', 'boot_id'))
            and isinstance(identity.get('start'), str) and re.fullmatch(r'[0-9]+', identity['start']) is not None
            and all(type(identity.get(key)) is int and identity[key] > 0 for key in ('pid', 'pgid', 'sid')))


def _matches(saved, observed):
    return (complete_identity(saved) and complete_identity(observed)
            and all(saved[key] == observed[key] for key in IDENTITY_FIELDS))


def _event(row, kind, cause, payload, at):
    run_id = row['id']
    event_id = digest(f'devflower.v2.{kind}.v1\0{run_id}'.encode('ascii'))
    return dict(kind=kind, event_version=1, event_id=event_id, run=run_id,
                definition_id=row['definition_id'], aggregate_kind='run', aggregate_id=run_id,
                cause=cause, payload=payload, at=at)


def _cause(event):
    return dict(kind=event['kind'], event_id=event['event_id'])


def _insert_event(db, event):
    return db.execute('INSERT INTO events(run,data) VALUES(?,?)',
                      (event['run'], canonical(event).decode())).lastrowid


def _read_record(db, kind, key):
    stored = db.execute('SELECT typeof(data), CAST(data AS BLOB) FROM records WHERE kind=? AND id=?',
                        (kind, key)).fetchone()
    _require(stored is not None and stored[0] == 'text')
    try:
        row = json.loads(stored[1])
        _require(isinstance(row, dict) and canonical(row) == stored[1])
    except (TypeError, ValueError, RecursionError):
        raise Blocked(ERROR) from None
    return row


def _save_run(db, row):
    db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?",
               (canonical(row).decode(), row['id']))


def _time(value):
    return type(value) in (float, int) and 0 < value <= 2**53 and math.isfinite(value)


def _read_fact(db, seq, expected):
    _require(type(seq) is int and 0 < seq < 2**63)
    stored = db.execute('SELECT run, typeof(data), CAST(data AS BLOB) FROM events WHERE seq=?',
                        (seq,)).fetchone()
    _require(stored == (expected['run'], 'text', canonical(expected)))
    return expected


def _authorized_run(db, run_id):
    _require(isinstance(run_id, str) and re.fullmatch(r'[0-9a-f]{32}', run_id))
    row = _read_record(db, 'run', run_id)
    _require(versioned(row) and row.get('id') == row.get('run') == run_id
             and row.get('state') == 'QUEUED' and _time(row.get('created_at')))
    _require('worker_entered_seq' not in row and 'worker_entered_at' not in row)

    definition_id = row.get('definition_id')
    _require(isinstance(definition_id, str) and re.fullmatch(r'[0-9a-f]{64}', definition_id))
    definition = _read_record(db, 'definition', definition_id)
    fields = ('workspace', 'draft_id', 'draft_revision', 'graph', 'graph_sha256', 'task')
    _require(definition.get('id') == definition_id and all(key in row and key in definition for key in fields))
    workspace, intent = row['workspace'], row.get('launch_intent')
    _require(isinstance(workspace, str) and isinstance(intent, str))
    _require(canonical([row[key] for key in fields]) == canonical([definition[key] for key in fields]))
    _require(digest(canonical({k: v for k, v in definition.items() if k != 'id'})) == definition_id)
    payload_sha = digest(canonical(dict(workspace=workspace, draft_id=row['draft_id'],
                                        revision=row['draft_revision'], task=row['task'])))
    reservation = db.execute(
        'SELECT payload_sha,run_id,definition_id,state,created FROM launch_intents '
        'WHERE workspace=? AND intent_key=?', (workspace, intent)).fetchone()
    _require(reservation is not None and reservation[:4] == (payload_sha, run_id, definition_id, 'RESERVED')
             and _time(reservation[4]))
    expected = _event(row, 'run-authorized', dict(kind='launch_intent', reservation_run_id=run_id),
                      dict(state='QUEUED', created_at=row['created_at']), reservation[4])
    return row, _read_fact(db, row.get('worker_authorized_seq'), expected)


def _unpublished(row):
    return all(key not in row for key in ('worker', 'worker_bound_seq', 'worker_published_at'))


def _bound(db, row, authorized, identity):
    _require(row.get('launch_state') == 'STARTED' and _matches(row.get('worker'), identity)
             and _time(row.get('worker_published_at')))
    at = row['worker_published_at']
    expected = _event(row, 'worker-bound', _cause(authorized),
                      dict(launch_state='STARTED', worker_published_at=at), at)
    return _read_fact(db, row.get('worker_bound_seq'), expected)


def publish_worker(flow, run_id, definition_id, identity):
    with flow.tx() as db:
        row, authorized = _authorized_run(db, run_id)
        _require(row['definition_id'] == definition_id and complete_identity(identity)
                 and row.get('launch_state') == 'SPAWNING' and _unpublished(row))
        at = time.time()
        event = _event(row, 'worker-bound', _cause(authorized),
                       dict(launch_state='STARTED', worker_published_at=at), at)
        seq = _insert_event(db, event)
        row.update(worker=identity, launch_state='STARTED', worker_published_at=at, worker_bound_seq=seq)
        _save_run(db, row)


def enter_worker(flow, run_id, identity):
    with flow.tx() as db:
        row, authorized = _authorized_run(db, run_id)
        bound = _bound(db, row, authorized, identity)
        at = time.time()
        event = _event(row, 'worker-entered', _cause(bound), dict(worker_entered_at=at), at)
        row.update(worker_entered_seq=_insert_event(db, event), worker_entered_at=at)
        _save_run(db, row)


def record_launch_failure(flow, run_id, definition_id, stage, identity):
    with flow.tx() as db:
        row, authorized = _authorized_run(db, run_id)
        _require(row['definition_id'] == definition_id)
        upstream = authorized
        if stage == 'go':
            upstream = _bound(db, row, authorized, identity)
        else:
            _require(_unpublished(row))
            if stage == 'before_spawn':
                _require(identity is None and ('launch_state' not in row or row['launch_state'] == 'SPAWNING'))
            else:
                _require(stage in ('identity', 'publication') and row.get('launch_state') == 'SPAWNING')
                _require(complete_identity(identity) == (stage == 'publication'))
        at = time.time()
        has_child = stage != 'before_spawn'
        state = 'RECOVERY_REQUIRED' if has_child else 'FAILED'
        event = _event(row, 'local-worker-launch-failed', _cause(upstream),
                       dict(stage=stage, has_child=has_child, state=state,
                            reason_code='WORKER_LAUNCH_UNCONFIRMED', finished_at=at), at)
        _insert_event(db, event)
        row.update(state=state, reason=FAILURE_REASON, finished_at=at)
        _save_run(db, row)
