"""Project workflow links only from a native record and V2 completion provenance."""
import json
from pathlib import Path
from types import SimpleNamespace

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.session_sources import UUID, scan

from .native_sessions import FILE_BYTES, LINE_BYTES
from .recovery import read_completion


def claim_key(config_root, sid):
    return digest(canonical(['codex', config_root, sid]))


def verified_workflow(store, config_root, sid):
    """Require the claim, frozen definition, node, activation and actual process cwd."""
    from .session_identity import SessionRefused, StorageRootIdentity
    try:
        with store.db.tx(read=True) as db:
            def get(kind, key):
                return json.loads(db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()[0])
            claim = get('native-session-claim', claim_key(config_root, sid))
            chat = get('node-session', claim['binding_id'])
            run = get('run', chat['run'])
            definition = get('definition', run['definition_id'])
            def records(kind):
                return [json.loads(raw) for (raw,) in db.execute(
                    "SELECT data FROM records WHERE kind=? AND json_extract(data, '$.run')=?", (kind, run['id']))]
            activations = [row for row in records('activation') if row.get('session_binding_id') == chat['id']]
            steps = records('native-step')
        if len(activations) != 1 or not UUID.fullmatch(sid):
            raise ValueError()
        activation = activations[0]
        workspace = store.workspace(run['workspace'])
        frozen = definition['bindings'][chat['node']]
        actual = chat['binding']
        if (digest(canonical({key: value for key, value in definition.items() if key != 'id'})) != run['definition_id']
                or definition['id'] != run['definition_id'] or definition['workspace'] != workspace['id']
                or run['draft_id'] != definition['draft_id'] or chat['workspace'] != workspace['id']
                or chat['id'] != digest(canonical([run['id'], chat['node']]))
                or chat['native_session_id'] != sid or claim['session_id'] != sid
                or activation['run'] != run['id'] or activation['node'] != chat['node']
                or activation['chat_key'] != chat['id'] or activation['observed_session'] != sid
                or frozen['provider'] != 'codex' or frozen['config_root'] != config_root
                or config_root != str(Path(frozen['home']) / '.codex')
                or frozen['project'] != workspace['id'] or frozen.get('session_id') is not None
                or actual != dict(frozen, session_id=sid)):
            raise ValueError()
        scope = chat.get('native_scope')
        if scope is not None and (scope.get('runtime_profile') != 'default-codex'
                or scope.get('cwd') != workspace['path']
                or scope.get('cwd_identity') != StorageRootIdentity.capture(workspace['path']).fields()
                or scope.get('root_identity') != StorageRootIdentity.capture(config_root).fields()):
            raise ValueError()
        completion = read_completion(SimpleNamespace(storage=store.state), run, activation, definition, steps)
        receipt = completion['receipt']
        process = next(step for step in steps if step['id'] == receipt['process']['operation_id'])
        if receipt['session_id'] != sid or process['data']['cwd'] != workspace['path']:
            raise ValueError()
        node = next(node for node in definition['graph']['nodes'] if node['id'] == chat['node'] and node['kind'] == 'agent')
        return dict(workspace=workspace['id'], cwd=workspace['path'], home=frozen['home'],
                    group=digest(canonical(['v2-workflow-session-group', workspace['id'], definition['draft_id']])),
                    workflow=safe_text(definition['draft_document']['name'])[:160], agent=safe_text(node['name'])[:160],
                    run=run['id'], activation=activation['id'], node=node['id'])
    except (OSError, ValueError, TypeError, KeyError, StopIteration, Blocked, Invalid):
        raise SessionRefused('RECOVERY_REQUIRED') from None


def workflow_records(store, workspace, sources, budget):
    source = next((item for item in sources if item.profile.id == 'default-codex'), None)
    if source is None:
        return
    with store.db.tx(read=True) as db:
        rows = db.execute("SELECT data FROM records WHERE kind='node-session' AND json_extract(data,'$.workspace')=?"
                          " ORDER BY rowid DESC LIMIT 80", (workspace,)).fetchall()
    candidates = {}
    for (raw,) in rows:
        budget.time()
        chat = json.loads(raw)
        sid = chat.get('native_session_id')
        root = str(source.profile.root.parent)
        if not isinstance(sid, str) or not UUID.fullmatch(sid) or chat.get('binding', {}).get('config_root') != root:
            continue
        try:
            verified = verified_workflow(store, root, sid)
        except ValueError:
            continue
        if verified['workspace'] == workspace and Path(verified['home']) / '.codex' == source.profile.root.parent:
            candidates[sid] = verified
    if not candidates:
        return
    # File names narrow the bounded search. The provider UUID still comes
    # from the completion receipt and must match parsed transcript metadata.
    found, coverage = scan(source.profile.root, 'codex', session_ids=candidates, include_paths=True,
                           limits={'entries': 6000, 'files': 80, 'bytes': 32 * 1024 * 1024,
                                   'file_bytes': FILE_BYTES, 'line_bytes': LINE_BYTES})
    if coverage['status'] != 'complete':
        return
    for item in found:
        sid = item['session_id']
        if sid not in candidates:
            continue
        verified = candidates[sid]
        yield source, verified['cwd'], sid, str(Path(item['path']).relative_to(source.profile.root)), verified
