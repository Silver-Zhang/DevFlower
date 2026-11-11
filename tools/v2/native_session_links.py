"""Project workflow links only from a native record and V2 completion provenance."""
import json
from pathlib import Path
from types import SimpleNamespace

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.session_sources import UUID, scan

from .native_sessions import FILE_BYTES, LINE_BYTES
from .recovery import frozen_node, read_completion


def claim_key(config_root, sid, provider='codex'):
    return digest(canonical([provider, config_root, sid]))


def workflow_use(store, db, chat, config_root, sid, provider, *, creation=False):
    """Verify the one exact run, activation, process and native receipt behind a binding."""
    from .session_identity import StorageRootIdentity
    def get(kind, key):
        row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        if not row:
            raise ValueError()
        return json.loads(row[0])
    def records(kind, run):
        return [json.loads(raw) for (raw,) in db.execute(
            "SELECT data FROM records WHERE kind=? AND json_extract(data, '$.run')=?", (kind, run))]
    run = get('run', chat['run'])
    definition = get('definition', run['definition_id'])
    activations = [row for row in records('activation', run['id']) if row.get('session_binding_id') == chat['id']]
    if len(activations) != 1 or not UUID.fullmatch(sid):
        raise ValueError()
    activation = activations[0]
    workspace = store.workspace(run['workspace'])
    chat_key_fields = [run['id'], chat['node']]
    if definition.get('designer_runtime') == 1:
        from .runtime import activation_binding
        iterations = activation['iteration_path']
        if (run.get('designer_runtime') != 1 or chat.get('activation') != activation['id']
                or not isinstance(iterations, list) or len(iterations) != chat['node'].count('/')
                or any(type(value) is not int or value < 1 for value in iterations)
                or activation['id'] != digest(canonical([*chat_key_fields, iterations]))):
            raise ValueError()
        if iterations:
            chat_key_fields.append(iterations)
        frozen = activation_binding(definition, activation)
    else:
        frozen = definition['bindings'][chat['node']]
    actual = chat['binding']
    cwd = frozen.get('cwd', workspace['path'])
    if (digest(canonical({key: value for key, value in definition.items() if key != 'id'})) != run['definition_id']
            or definition['id'] != run['definition_id'] or definition['workspace'] != workspace['id']
            or run['draft_id'] != definition['draft_id'] or chat['workspace'] != workspace['id']
            or chat['id'] != digest(canonical(chat_key_fields))
            or chat['native_session_id'] != sid
            or activation['run'] != run['id'] or activation['node'] != chat['node']
            or activation['chat_key'] != chat['id'] or activation['observed_session'] != sid
            or frozen['provider'] != provider or frozen['config_root'] != config_root
            or config_root != str(Path(frozen['home']) / ('.' + provider))
            or frozen['project'] != workspace['id'] or not Path(cwd).is_relative_to(workspace['path'])
            or (frozen.get('session_id') is not None if creation else frozen.get('session_id') not in (None, sid))
            or actual != dict(frozen, session_id=sid)):
        raise ValueError()
    scope = chat.get('native_scope')
    if scope is not None and (scope.get('runtime_profile') != 'default-' + provider
            or scope.get('cwd') != cwd or scope.get('cwd_identity') != StorageRootIdentity.capture(cwd).fields()
            or scope.get('root_identity') != StorageRootIdentity.capture(config_root).fields()):
        raise ValueError()
    if (provider == 'claude' or frozen.get('agent_config_id')) and not scope:
        raise ValueError()
    steps = records('native-step', run['id'])
    completion = read_completion(SimpleNamespace(storage=store.state), run, activation, definition, steps)
    receipt = completion['receipt']
    process = next(step for step in steps if step['id'] == receipt['process']['operation_id'])
    if receipt['session_id'] != sid or process['data']['cwd'] != cwd:
        raise ValueError()
    node = frozen_node(definition, chat['node'])
    if node['kind'] != 'agent':
        raise ValueError()
    return run, definition, node, activation, receipt, frozen, cwd


def verified_workflow(store, config_root, sid, provider='codex'):
    """Require the global creation claim and the original verified workflow use."""
    from .session_identity import SessionRefused
    try:
        with store.db.tx(read=True) as db:
            claim_raw = db.execute("SELECT data FROM records WHERE kind='native-session-claim' AND id=?",
                                   (claim_key(config_root, sid, provider),)).fetchone()
            claim = json.loads(claim_raw[0])
            if claim['session_id'] != sid:
                raise ValueError()
            chat_raw = db.execute("SELECT data FROM records WHERE kind='node-session' AND id=?", (claim['binding_id'],)).fetchone()
            chat = json.loads(chat_raw[0])
            run, definition, node, activation, receipt, frozen, cwd = workflow_use(
                store, db, chat, config_root, sid, provider, creation=True)
            history = chat.get('native_history')
            if history is not None and receipt.get('native_history') != {'sha256': history['sha256']}:
                raise ValueError()
        return dict(workspace=run['workspace'], cwd=cwd, home=frozen['home'],
                    group=digest(canonical(['v2-workflow-session-group', run['workspace'], definition['draft_id']])),
                    workflow=safe_text(definition['draft_document']['name'])[:160], agent=safe_text(node['name'])[:160],
                    run=run['id'], activation=activation['id'], node=activation['node'], history=history,
                    native_profile=profile_identity(frozen.get('native_profile')),
                    **({'native_home':frozen['native_home']['id']} if frozen.get('native_home') else {}))
    except (OSError, ValueError, TypeError, KeyError, StopIteration, Blocked, Invalid):
        raise SessionRefused('RECOVERY_REQUIRED') from None


def profile_identity(profile):
    return {key:profile[key] for key in ('id','sha256')} if profile else None


def require_creation_profile(store, config_root, sid, provider, requested=None):
    from .session_identity import SessionRefused
    try:
        store.db.get('native-session-claim',claim_key(config_root,sid,provider))
    except Invalid:
        if requested:
            raise SessionRefused('PROFILE_REFUSED') from None
        return
    if verified_workflow(store,config_root,sid,provider).get('native_profile')!=profile_identity(requested):
        raise SessionRefused('PROFILE_REFUSED')


def claude_workflow_history(store, db, binding, locator):
    """Project verified managed Claude workflow receipts into the standalone history comparison."""
    sid = locator.native_uuid
    root = binding['config_root']
    rows = db.execute("""
        SELECT data FROM records WHERE kind='node-session'
        AND json_extract(data,'$.binding.provider')='claude'
        AND json_extract(data,'$.binding.config_root')=?
        AND json_extract(data,'$.workspace')=?
        AND coalesce(json_extract(data,'$.native_session_id'),json_extract(data,'$.binding.session_id'),
                     json_extract(data,'$.binding.new_session_id'))=? LIMIT 81
    """, (root, locator.workspace_id, sid)).fetchall()
    if len(rows) > 80:
        return (), True
    receipts = []
    for (raw,) in rows:
        chat = json.loads(raw)
        if chat.get('native_session_id') != sid or not chat.get('native_history'):
            continue
        try:
            run, _, _, _, receipt, frozen, _ = workflow_use(store, db, chat, root, sid, 'claude')
            history = chat['native_history']
            if (receipt.get('native_history') != {'sha256': history['sha256']}
                    or history['history_identity'] != locator.root_identity.fields()):
                raise ValueError()
            creation = frozen.get('session_id') is None and frozen.get('new_session_id') == sid
            if creation:
                claim = db.execute("SELECT data FROM records WHERE kind='native-session-claim' AND id=?",
                                   (claim_key(root, sid, 'claude'),)).fetchone()
                if not claim or json.loads(claim[0]) != {'binding_id': chat['id'], 'session_id': sid}:
                    raise ValueError()
            receipts.append(dict(created=run['created_at'], is_creation=creation, binding=frozen,
                                 relative=history['relative'], native_after_sha256=history['sha256']))
        except (OSError, ValueError, TypeError, KeyError, StopIteration, Blocked, Invalid):
            return (), True
    return receipts, False


def workflow_records(store, workspace, sources, budget):
    sources = {item.profile.provider: item for item in sources
               if item.profile.id == 'default-' + item.profile.provider}
    if not sources:
        return
    with store.db.tx(read=True) as db:
        rows = db.execute("SELECT data FROM records WHERE kind='node-session' AND json_extract(data,'$.workspace')=?"
                          " ORDER BY rowid DESC LIMIT 80", (workspace,)).fetchall()
    candidates, seen = {}, set()
    for (raw,) in rows:
        budget.time()
        chat = json.loads(raw)
        sid = chat.get('native_session_id')
        binding = chat.get('binding', {})
        provider = binding.get('provider')
        source = sources.get(provider)
        if source is None or not isinstance(sid, str) or not UUID.fullmatch(sid):
            continue
        root = source.profile.root.parent
        if binding.get('config_root') != str(root) or (provider, sid) in seen:
            continue
        seen.add((provider, sid))
        try:
            verified = verified_workflow(store, str(root), sid, provider)
        except ValueError:
            continue
        if verified['workspace'] == workspace and Path(verified['home']) / ('.' + provider) == root:
            history = verified['history']
            if history:
                if history['history_identity'] == source.profile.identity.fields():
                    yield source, verified['cwd'], sid, history['relative'], verified
            else:
                candidates.setdefault(provider, {})[sid] = verified
    for provider, candidates_by_sid in candidates.items():
        source = sources[provider]
        # File names narrow the search. The receipt UUID must match parsed transcript metadata.
        found, coverage = scan(source.profile.root, provider, session_ids=candidates_by_sid, include_paths=True,
                               limits={'entries': 6000, 'files': 80, 'bytes': 32 * 1024 * 1024,
                                       'file_bytes': FILE_BYTES, 'line_bytes': LINE_BYTES})
        if coverage['status'] != 'complete':
            continue
        for item in found:
            sid = item['session_id']
            if sid in candidates_by_sid:
                verified = candidates_by_sid[sid]
                yield source, verified['cwd'], sid, str(Path(item['path']).relative_to(source.profile.root)), verified
