"""Workspace-scoped reversible presentation preferences for native sessions."""
from dataclasses import dataclass
import hashlib
import json
import os
import sqlite3
from threading import Lock
import unicodedata

from orchestration.common import Blocked, canonical, digest
from studio.agent_sessions import safe_text
from studio.server import Conflict

from .native_history_config import unique_pairs
from .native_sessions import (
    HANDLE, REVISION, WORKSPACE, NativeHistoryError, project_messages, require_file, stamp,
)
from .session_identity import SessionRefused, StorageRootIdentity
from .workspace_select import existing_database_uri, existing_read_connection, require_current

KIND = 'native-session-view'
MAX_REVISION = (1 << 53) - 1
MAX_VIEWS = 400
MAX_ACTION_BYTES = 8192
ACTIONS = ('rename', 'hide', 'unhide', 'archive', 'trash', 'restore')
COLLECTIONS = ('active', 'archived', 'trash')
VIEW_FIELDS = {'handle', 'alias', 'hidden', 'collection', 'view_revision'}
BIDI = frozenset('\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')


def invalid():
    return NativeHistoryError('NATIVE_VIEW_INVALID')


def conflict():
    return NativeHistoryError('NATIVE_VIEW_CONFLICT')


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def list_parameters(params):
    if (set(params) != {'workspace', 'revision'} or len(params['workspace']) != 1 or len(params['revision']) != 1
            or not matches(WORKSPACE, params['workspace'][0]) or not matches(REVISION, params['revision'][0])):
        raise invalid()
    return params['workspace'][0], params['revision'][0]


def safe_alias(value):
    if (not isinstance(value, str) or '/' in value or '\\' in value
            or any(unicodedata.category(char) in ('Cc', 'Cs') or char in BIDI for char in value)):
        raise invalid()
    alias = value.strip()
    if not 1 <= len(alias) <= 160 or safe_text(alias) != alias:
        raise invalid()
    return alias


@dataclass(frozen=True, slots=True)
class ViewAction:
    workspace: str
    handle: str
    revision: str
    expected_view_revision: int | None
    action: str
    alias: str | None


def action_parameters(raw):
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
    except (ValueError, TypeError, RecursionError):
        raise invalid() from None
    if not isinstance(data, dict):
        raise invalid()
    action = data.get('action')
    required = {'workspace', 'handle', 'revision', 'expected_view_revision', 'action'}
    if action == 'rename':
        required.add('alias')
    if (action not in ACTIONS or set(data) != required or not matches(WORKSPACE, data['workspace'])
            or not matches(HANDLE, data['handle']) or not matches(REVISION, data['revision'])):
        raise invalid()
    expected = data['expected_view_revision']
    if expected is not None and (type(expected) is not int or not 1 <= expected <= MAX_REVISION):
        raise invalid()
    alias = safe_alias(data['alias']) if action == 'rename' else None
    return ViewAction(data['workspace'], data['handle'], data['revision'], expected, action, alias)


def view_id(workspace, handle):
    return digest(canonical(['v2-native-session-view-v1', workspace, handle]))


def default_view(handle):
    return {'handle': handle, 'alias': None, 'hidden': False, 'collection': 'active', 'view_revision': None}


def parse_view(raw, workspace, handle):
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        if (not isinstance(value, dict) or set(value) != VIEW_FIELDS | {'workspace'} or value['workspace'] != workspace
                or value['handle'] != handle or type(value['hidden']) is not bool or value['collection'] not in COLLECTIONS
                or type(value['view_revision']) is not int or not 1 <= value['view_revision'] <= MAX_REVISION
                or value['alias'] is not None and safe_alias(value['alias']) != value['alias']):
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise conflict() from None
    return {key: value[key] for key in VIEW_FIELDS}


def transition(view, action):
    if view['view_revision'] != action.expected_view_revision:
        raise conflict()
    requested = dict(view)
    if action.action == 'rename':
        requested['alias'] = action.alias
    elif action.action in ('hide', 'unhide'):
        requested['hidden'] = action.action == 'hide'
    elif action.action == 'archive':
        if view['collection'] == 'trash':
            raise conflict()
        requested['collection'] = 'archived'
    elif action.action == 'trash':
        requested['collection'] = 'trash'
    elif action.action == 'restore':
        requested['collection'] = 'active'
    if requested == view:
        return view
    revision = (view['view_revision'] or 0) + 1
    if revision > MAX_REVISION:
        raise conflict()
    return dict(requested, view_revision=revision)


def verify_bytes(view, ref):
    checksum = hashlib.sha256()
    with view.directory(ref.relative.parent) as directory, view.regular(ref.relative.name, directory) as fd:
        if stamp(os.fstat(fd)) != ref.stamp:
            raise SessionRefused('ROOT_CHANGED')
        remaining = ref.stamp[4]
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise SessionRefused('ROOT_CHANGED')
            checksum.update(chunk)
            remaining -= len(chunk)
    if checksum.hexdigest() != ref.sha:
        raise SessionRefused('ROOT_CHANGED')
    require_file(view, ref)


class NativeSessionViews:
    def __init__(self, reader):
        self.reader = reader
        self.state = reader.store.state
        self._mutation_gate = Lock()

    def _scope(self, workspace, revision):
        path, identity, proof = self.reader.workspace(workspace, database_error='NATIVE_VIEW_CONFLICT')
        with self.reader.guard:
            snapshot = self.reader._snapshot(workspace, revision)
        if identity != snapshot.workspace_identity:
            raise NativeHistoryError('NATIVE_REVISION_STALE')
        return path, identity, proof, snapshot

    def listing(self, workspace, revision):
        path, identity, proof, snapshot = self._scope(workspace, revision)
        if len(snapshot.handles) > MAX_VIEWS:
            raise conflict()
        ids = {view_id(workspace, handle): handle for handle in sorted(snapshot.handles)}
        views = []
        try:
            if ids:
                placeholders = ','.join('?' for _ in ids)
                with existing_read_connection(self.state, proof) as db:
                    records = db.execute(f'SELECT id,data FROM records WHERE kind=? AND id IN ({placeholders})',
                                         (KIND, *ids)).fetchall()
                views = [parse_view(raw, workspace, ids[key]) for key, raw in records]
            try:
                if StorageRootIdentity.capture(path) != identity:
                    raise SessionRefused('ROOT_CHANGED')
            except (OSError, ValueError, Blocked):
                raise NativeHistoryError('NATIVE_REVISION_STALE') from None
            require_current(self.state, proof)
        except NativeHistoryError:
            raise
        except (Conflict, OSError, ValueError, Blocked, sqlite3.Error):
            raise conflict() from None
        with self.reader.guard:
            self.reader._snapshot(workspace, revision)
        return {'workspace': workspace, 'revision': revision, 'views': views}

    def _verify(self, action, path, identity, record, *, readable=False):
        try:
            with self.reader.verified_record(action.workspace, action.handle, path, identity, record) as (view, locator):
                if readable:
                    project_messages(view, record.ref, locator.provider, self.reader.literals)
                else:
                    verify_bytes(view, record.ref)
        except UnicodeError:
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE') from None
        except (OSError, ValueError, TypeError, Blocked, RecursionError):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None

    def change(self, action):
        # Even the scope SELECT can create temporary SQLite sidecars. Start
        # excluding sibling mutations before that SELECT can overlap a commit.
        if not self._mutation_gate.acquire(blocking=False):
            raise NativeHistoryError('NATIVE_VIEW_CONFLICT', message='另一项会话整理正在提交，请稍后刷新核对。')
        try:
            path, identity, proof, snapshot = self._scope(action.workspace, action.revision)
            record = snapshot.reads.get(action.handle)
            if record is None:
                raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE')
            if not self.reader.read_slots.acquire(blocking=False):
                raise conflict()
            try:
                self._verify(action, path, identity, record, readable=True)
                view = self._commit(action, path, identity, proof, record)
            finally:
                self.reader.read_slots.release()
        finally:
            self._mutation_gate.release()
        return {'workspace': action.workspace, 'revision': action.revision, 'view': view}

    def _commit(self, action, path, identity, proof, record):
        key = view_id(action.workspace, action.handle)
        committed = False
        try:
            require_current(self.state, proof)
            db = sqlite3.connect(existing_database_uri(self.state), uri=True, timeout=0)
            try:
                require_current(self.state, proof)
                db.execute('BEGIN IMMEDIATE')
                require_current(self.state, proof, transaction=True)
                stored = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone()
                previous = parse_view(stored[0], action.workspace, action.handle) if stored else default_view(action.handle)
                updated = transition(previous, action)
                self._verify(action, path, identity, record)
                # Serialize the final commit with publication of a replacement
                # in-memory snapshot. The native file itself remains unlocked.
                with self.reader.guard:
                    self.reader._snapshot(action.workspace, action.revision)
                    require_current(self.state, proof, transaction=True)
                    if updated != previous:
                        data = canonical(dict(updated, workspace=action.workspace)).decode()
                        if stored:
                            result = db.execute('UPDATE records SET data=? WHERE kind=? AND id=? AND data=?',
                                                (data, KIND, key, stored[0]))
                        else:
                            result = db.execute('INSERT INTO records(kind,id,data) VALUES(?,?,?)', (KIND, key, data))
                        if result.rowcount != 1:
                            raise conflict()
                        db.commit()
                        committed = True
                    else:
                        db.rollback()
            finally:
                db.close()
                require_current(self.state, proof, committed=committed)
            return updated
        except NativeHistoryError:
            raise
        except (Conflict, OSError, ValueError, Blocked, sqlite3.Error):
            raise conflict() from None


class WorkflowSessionTrash:
    """V1 batch preview/member recheck over V2's reversible view records."""
    def __init__(self, views):
        self.views = views
        self.reader = views.reader
        self.db = self.reader.store.db

    def _known_members(self, db, workspace, group):
        runs = db.execute("SELECT data FROM records WHERE kind='run' AND json_extract(data,'$.workspace')=? LIMIT 401",
                          (workspace,)).fetchall()
        if len(runs) > 400:
            raise conflict()
        group_runs = set()
        for (raw,) in runs:
            run = json.loads(raw)
            if digest(canonical(['v2-workflow-session-group', workspace, run['draft_id']])) == group:
                if run['state'] not in ('COMPLETED', 'FAILED', 'CANCELLED'):
                    raise SessionRefused('RECOVERY_REQUIRED' if run['state'] == 'RECOVERY_REQUIRED' else 'LEASE_HELD')
                group_runs.add(run['id'])
        sessions = db.execute("""
            SELECT chat.data, run.data FROM records chat
            JOIN records run ON run.kind='run' AND run.id=json_extract(chat.data,'$.run')
            WHERE chat.kind='node-session' AND json_extract(chat.data,'$.workspace')=?
            LIMIT 401
        """, (workspace,)).fetchall()
        if len(sessions) > 400:
            raise conflict()
        known = {}
        for chat_raw, run_raw in sessions:
            chat, run = json.loads(chat_raw), json.loads(run_raw)
            if run['id'] not in group_runs or run['workspace'] != workspace:
                continue
            sid = chat.get('native_session_id')
            if sid:
                known[sid] = run['id']
        return known

    def _members(self, db, workspace, group, snapshot):
        known = self._known_members(db, workspace, group)
        actual = {}
        members = []
        for handle, link in sorted(snapshot.links.items()):
            if link['group'] != group:
                continue
            record = snapshot.reads[handle]
            locator = record.index.resolve(workspace, handle)
            if locator.provider != 'codex':
                raise NativeHistoryError('NATIVE_VIEW_CONFLICT', message='包含 Claude 的工作流请逐条整理会话。')
            actual[locator.native_uuid] = link['run']
            raw = db.execute('SELECT data FROM records WHERE kind=? AND id=?',
                             (KIND, view_id(workspace, handle))).fetchone()
            view = parse_view(raw[0], workspace, handle) if raw else default_view(handle)
            if view['collection'] == 'archived':
                raise NativeHistoryError('NATIVE_VIEW_CONFLICT', message='工作流含归档会话，请先逐条核对。')
            if view['collection'] != 'trash':
                members.append((handle, locator, record, view))
        if (not members or actual != known or len(members) > 80
                or sum(record.ref.stamp[4] for _, _, record, _ in members) > 32 * 1024 * 1024):
            raise NativeHistoryError('NATIVE_VIEW_CONFLICT', message='工作流会话成员未完整核验，未进行批量整理。')
        return members

    @staticmethod
    def _fingerprint(members):
        return digest(canonical([[handle, locator.lease_key, record.ref.sha, view]
                                 for handle, locator, record, view in members]))

    def request(self, data):
        from contextlib import ExitStack
        from .session_lease import codex_presentation_guard, production_session_gate

        required = {'workspace', 'revision', 'group'}
        if (set(data) not in (required, required | {'expected'})
                or not matches(WORKSPACE, data['workspace']) or not matches(REVISION, data['revision'])
                or not matches(WORKSPACE, data['group'])
                or 'expected' in data and not matches(WORKSPACE, data['expected'])):
            raise invalid()
        workspace, revision, group = (data[key] for key in ('workspace', 'revision', 'group'))
        if not self.views._mutation_gate.acquire(blocking=False):
            raise conflict()
        try:
            path, identity, proof, snapshot = self.views._scope(workspace, revision)
            with self.db.tx(read=True) as db:
                members = self._members(db, workspace, group, snapshot)
            fingerprint = self._fingerprint(members)
            if 'expected' not in data:
                self.reader.require_proof(proof)
                return {'workspace': workspace, 'group': group, 'count': len(members), 'expected': fingerprint}
            if data['expected'] != fingerprint:
                raise conflict()
            with ExitStack() as stack:
                roots = {}
                for _, locator, record, _ in members:
                    root = record.source.profile.root.parent
                    binding = dict(provider='codex', home=str(root.parent), config_root=str(root),
                                   execution_backend='native-local', local_lock_root=str(root / '.devflower-writer-locks'),
                                   root_identity=StorageRootIdentity.capture(root).fields(), project=workspace,
                                   cwd=locator.original_cwd, session_id=locator.native_uuid)
                    stack.enter_context(production_session_gate(binding, locator))
                    roots.setdefault(root, []).append(locator.native_uuid)
                _, fresh = self.reader.scan(workspace, path, identity)
                checks = [stack.enter_context(codex_presentation_guard(root, sessions)) for root, sessions in roots.items()]
                with self.db.tx() as db:
                    if self._fingerprint(self._members(db, workspace, group, fresh)) != fingerprint:
                        raise conflict()
                    for handle, _, record, previous in members:
                        action = ViewAction(workspace, handle, revision, previous['view_revision'], 'trash', None)
                        self.views._verify(action, path, identity, record)
                        updated = transition(previous, action)
                        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (
                            KIND, view_id(workspace, handle), canonical(dict(updated, workspace=workspace)).decode()))
                    with self.reader.guard:
                        self.reader._snapshot(workspace, revision)
                        require_current(self.reader.store.state, proof, transaction=True)
                        for check in checks:
                            check()
                        db.commit()
            return {'workspace': workspace, 'group': group, 'count': len(members), 'views': [
                transition(view, ViewAction(workspace, handle, revision, view['view_revision'], 'trash', None))
                for handle, _, _, view in members]}
        finally:
            self.views._mutation_gate.release()
