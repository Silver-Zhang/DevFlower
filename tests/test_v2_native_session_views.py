"""Local view state is scoped, reversible and CAS-protected."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier, Thread
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlencode

from v2 import native_session_views, workspace_select
from v2.native_history_config import load_history_config, startup_seed
from v2.native_session_views import KIND, NativeSessionViews, action_parameters, view_id
from v2.native_sessions import NativeHistoryError, NativeSessions
from v2.server import create_server
from v2.store import DraftStore
from v2_native_sessions_fixture import OTHER, SID, codex, codex_text, fixtures, snapshot


class SessionViewTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-session-views-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = fixtures(self.root)
        self.store = DraftStore(self.root / 'state', [self.data['projects']])
        self.a = self.store.open_workspace(str(self.data['a']))['id']
        self.b = self.store.open_workspace(str(self.data['b']))['id']
        self.seed = startup_seed(self.store.state)
        self.reader = self.make_reader(self.store)
        self.views = NativeSessionViews(self.reader)
        self.listing = self.reader.listing(self.a)
        self.handle = self.default_handle(self.listing)

    def make_reader(self, store):
        return NativeSessions(store, load_history_config(self.data['config']), self.seed, home=self.data['home'])

    @staticmethod
    def default_handle(listing):
        return next(row['handle'] for row in listing['sessions']
                    if row['provider'] == 'codex' and row['source_labels'] == ('默认来源',))

    def payload(self, action, expected=None, **fields):
        return dict(workspace=self.a, handle=self.handle, revision=self.listing['revision'],
                    expected_view_revision=expected, action=action, **fields)

    def change(self, action, expected=None, **fields):
        return self.views.change(action_parameters(json.dumps(self.payload(action, expected, **fields)).encode()))['view']

    def view_query(self, *, workspace=None, revision=None):
        return 'native-session-views?' + urlencode({'workspace': workspace or self.a,
                                                      'revision': revision or self.listing['revision']})

    def refused(self, code, action, *args, **kwargs):
        with self.assertRaises(NativeHistoryError) as caught:
            action(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertNotIn(SID, str(caught.exception))

    def state_files(self):
        return {key: value for key, value in snapshot(self.store.state).items() if key[1] != '.'}

    @contextmanager
    def running(self, service):
        server, token = create_server(self.store, native_sessions=service)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, *, payload=None, raw=None, authorized=True, origin=None):
            headers = {'Authorization': 'Bearer ' + token} if authorized else {}
            if origin:
                headers['Origin'] = origin
            if payload is not None:
                raw = json.dumps(payload).encode()
            if raw is not None:
                headers['Content-Type'] = 'application/json'
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=8)
            try:
                client.request('POST' if raw is not None else 'GET', '/api/v2/' + path, raw, headers)
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()

        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def test_get_and_same_target_are_zero_durable_writes(self):
        original = self.state_files()
        self.assertEqual(self.views.listing(self.a, self.listing['revision'])['views'], [])
        self.assertEqual(self.change('unhide')['view_revision'], None)
        self.assertEqual(self.change('restore')['view_revision'], None)
        self.assertEqual(self.state_files(), original)
        self.assertEqual(self.store.db.list(KIND), [])
        renamed = self.change('rename', alias='  合成会话 <b>文字  ')
        self.assertEqual(renamed['alias'], '合成会话 <b>文字')
        original = self.state_files()
        self.assertEqual(self.change('rename', 1, alias=renamed['alias']), renamed)
        self.assertEqual(self.views.listing(self.a, self.listing['revision'])['views'], [renamed])
        self.refused('NATIVE_VIEW_CONFLICT', self.change, 'rename', alias='旧版本')
        self.assertEqual(self.state_files(), original)
        self.assertEqual(len(self.store.db.list(KIND)), 1)

    def test_state_transitions_keep_hidden_orthogonal_and_revision_monotonic(self):
        expected = None
        for index, (action, hidden, collection) in enumerate((
                ('hide', True, 'active'), ('archive', True, 'archived'), ('trash', True, 'trash'),
                ('restore', True, 'active'), ('unhide', False, 'active'),
                ('archive', False, 'archived'), ('restore', False, 'active'),
                ('trash', False, 'trash')), 1):
            with self.subTest(action=action):
                result = self.change(action, expected)
                self.assertEqual((result['hidden'], result['collection'], result['view_revision']),
                                 (hidden, collection, index))
                self.assertEqual(self.change(action, index), result)
                expected = index
        self.refused('NATIVE_VIEW_CONFLICT', self.change, 'archive', expected)
        self.assertEqual(self.change('restore', expected)['collection'], 'active')

    def test_http_contract_auth_alias_validation_and_disabled_service(self):
        route = 'native-session-view'
        with self.running(self.reader) as request:
            self.assertEqual(request(self.view_query(), authorized=False)[0], 401)
            self.assertEqual(request(self.view_query(), origin='https://untrusted.invalid')[0], 403)
            for query in ('native-session-views', self.view_query() + '&revision=' + self.listing['revision'],
                          self.view_query() + '&root=/tmp', 'native-session-views?workspace=%FF&revision=x'):
                self.assertEqual(request(query)[1]['code'], 'NATIVE_VIEW_INVALID')
            for value in ('', ' ' * 4, 'a' * 161, 'literal/path', 'a\\b', 'tab\t', 'nul\0',
                          'bidi\u202e', 'ghp_SYNTHETIC_EXAMPLE1234', '\ud800'):
                with self.subTest(alias=repr(value)):
                    code, reply = request(route, payload=self.payload('rename', alias=value))
                    self.assertEqual((code, reply['code']), (400, 'NATIVE_VIEW_INVALID'))
            for bad in (self.payload('hide', True), self.payload('hide', 1 << 53),
                        self.payload('delete'), self.payload('hide', alias='extra')):
                self.assertEqual(request(route, payload=bad)[1]['code'], 'NATIVE_VIEW_INVALID')
            duplicate = json.dumps(self.payload('hide')).replace('"action":', '"action":"archive","action":')
            self.assertEqual(request(route, raw=duplicate.encode())[1]['code'], 'NATIVE_VIEW_INVALID')
            self.assertEqual(request(route, raw=b'not-json')[1]['code'], 'NATIVE_VIEW_INVALID')
            self.assertEqual(request(route + '?operation=hide', payload=self.payload('hide'))[1]['code'],
                             'NATIVE_VIEW_INVALID')
            self.assertEqual(request(route)[0], 404)
            self.assertEqual(request(self.view_query(), payload={})[0], 404)
            self.assertEqual(request(route, payload=self.payload('rename', alias='😀' * 160))[0], 200)
        with self.running(None) as request:
            self.assertEqual(request(self.view_query())[1]['code'], 'NATIVE_HISTORY_DISABLED')
            self.assertEqual(request(route, payload=self.payload('hide'))[1]['code'], 'NATIVE_HISTORY_DISABLED')

    def test_cross_workspace_stale_and_conflicted_source_are_refused(self):
        renamed = self.change('rename', alias='唯一别名')
        listing_b = self.reader.listing(self.b)
        self.assertEqual(self.views.listing(self.b, listing_b['revision'])['views'], [])
        action = dict(self.payload('hide', 1), workspace=self.b, revision=listing_b['revision'])
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.views.change, action_parameters(json.dumps(action).encode()))
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.views.listing, self.b, self.listing['revision'])
        old_revision = self.listing['revision']
        self.listing = self.reader.listing(self.a)
        self.refused('NATIVE_REVISION_STALE', self.views.listing, self.a, old_revision)
        codex(self.data['codex_root'], self.data['b'], suffix='_' + OTHER)
        self.refused('NATIVE_REVISION_STALE', self.change, 'hide', 1)
        self.listing = self.reader.listing(self.a)
        row = next(row for row in self.listing['sessions'] if row['handle'] == self.handle)
        self.assertEqual(row['access'], 'conflict')
        self.assertEqual(self.views.listing(self.a, self.listing['revision'])['views'], [renamed])
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.change, 'hide', 1)

    def test_missing_source_retains_private_view_and_new_physical_root_has_new_handle(self):
        previous = self.change('rename', alias='仅原始存储')
        root = self.data['codex_root']
        moved = root.parent / 'saved-original'
        root.rename(moved)
        missing = self.reader.listing(self.a)
        self.assertEqual(self.views.listing(self.a, missing['revision'])['views'], [])
        self.assertEqual(len(self.store.db.list(KIND)), 1)
        codex(root, self.data['a'], rows=[codex_text('user', '新物理来源')])
        restarted = self.make_reader(DraftStore(self.store.state, [self.data['projects']]))
        new = restarted.listing(self.a)
        self.assertNotEqual(self.default_handle(new), self.handle)
        self.assertEqual(NativeSessionViews(restarted).listing(self.a, new['revision'])['views'], [])
        shutil.rmtree(root)
        moved.rename(root)
        original = self.reader.listing(self.a)
        self.assertEqual(self.default_handle(original), self.handle)
        self.assertEqual(self.views.listing(self.a, original['revision'])['views'], [previous])

    def test_view_commit_only_touches_target_and_survives_restart(self):
        draft = self.store.save(self.a, None, None, {'version': 1, 'name': '保留的草稿', 'nodes': [], 'edges': []})
        self.store.db.put('run', 'fixture-run', {'workspace': self.a, 'state': 'UNKNOWN'})
        self.store.db.put('native-session-claim', 'fixture-claim', {'session': SID})
        native_before = snapshot(self.data['projects'], self.data['home'], self.data['isolated'])

        def unrelated():
            with self.store.db.tx(read=True) as db:
                return tuple(db.execute(query).fetchall() for query in (
                    "SELECT kind,id,data FROM records WHERE kind<>'native-session-view' ORDER BY kind,id",
                    'SELECT * FROM events', 'SELECT * FROM drafts', 'SELECT * FROM launch_intents'))

        before = unrelated()
        expected = self.change('hide')
        self.assertEqual(unrelated(), before)
        with self.store.db.tx(read=True) as db:
            stored = db.execute('SELECT id,data FROM records WHERE kind=?', (KIND,)).fetchall()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][0], view_id(self.a, self.handle))
        self.assertEqual(json.loads(stored[0][1]), dict(expected, workspace=self.a))
        self.assertEqual(snapshot(self.data['projects'], self.data['home'], self.data['isolated']), native_before)
        self.assertNotIn('lease', ' '.join(path.name for path in self.store.state.iterdir()))
        restarted_store = DraftStore(self.store.state, [self.data['projects']])
        restarted = self.make_reader(restarted_store)
        listing = restarted.listing(self.a)
        self.assertEqual(NativeSessionViews(restarted).listing(self.a, listing['revision'])['views'], [expected])
        self.assertEqual(restarted_store.draft(self.a, draft['id']), draft)
        isolated = next(row['handle'] for row in listing['sessions'] if '隔离来源 reviewer' in row['source_labels'])
        self.assertNotEqual(isolated, self.handle)
        self.assertFalse(any(row['handle'] == isolated for row in self.store.db.list(KIND)))

    def test_simultaneous_http_cas_allows_one_winner_per_revision(self):
        with self.running(self.reader) as request, ThreadPoolExecutor(max_workers=2) as pool:
            revision = None
            for round_index in range(12):
                gate = Barrier(2)

                def submit(label):
                    gate.wait(5)
                    return request('native-session-view',
                                   payload=self.payload('rename', revision, alias=f'竞争 {round_index} {label}'))

                results = list(pool.map(submit, ('A', 'B')))
                self.assertEqual(sorted(status for status, _ in results), [200, 409])
                self.assertEqual(next(body for status, body in results if status == 409)['code'], 'NATIVE_VIEW_CONFLICT')
                revision = next(body['view']['view_revision'] for status, body in results if status == 200)
                self.assertEqual(revision, round_index + 1)
        self.assertEqual(len(self.store.db.list(KIND)), 1)

    def test_database_missing_busy_third_file_and_after_connect_replacement_refuse(self):
        database = self.store.state / 'v2.sqlite'
        moved = self.root / 'moved.sqlite'
        database.rename(moved)
        self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
        self.refused('NATIVE_VIEW_CONFLICT', self.views.listing, self.a, self.listing['revision'])
        self.assertFalse(database.exists())
        moved.rename(database)
        writer = sqlite3.connect(database, timeout=0)
        try:
            writer.execute('BEGIN IMMEDIATE')
            self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
            self.refused('NATIVE_VIEW_CONFLICT', self.views.listing, self.a, self.listing['revision'])
            self.assertTrue((self.store.state / 'v2.sqlite-wal').exists())
        finally:
            writer.close()
        connect = sqlite3.connect
        third = self.store.state / 'unknown.tmp'

        def inject(*args, **kwargs):
            db = connect(*args, **kwargs)
            third.write_text('keep', encoding='utf-8')
            return db

        with patch.object(workspace_select.sqlite3, 'connect', side_effect=inject):
            self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
        self.assertEqual(third.read_text(), 'keep')
        third.unlink()

        def replace(*args, **kwargs):
            db = connect(*args, **kwargs)
            database.rename(moved)
            shutil.copyfile(moved, database)
            database.chmod(0o600)
            return db

        with patch.object(workspace_select.sqlite3, 'connect', side_effect=replace):
            self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
        self.assertEqual(self.store.db.list(KIND), [])

    def test_get_refuses_durable_database_or_workspace_root_change_during_projection(self):
        view = self.change('rename', alias='现有本地别名')
        parse = native_session_views.parse_view

        def write_unrelated(*args):
            result = parse(*args)
            self.store.db.put('run', 'unrelated-change', {'workspace': self.b})
            return result

        with patch.object(native_session_views, 'parse_view', side_effect=write_unrelated):
            self.refused('NATIVE_VIEW_CONFLICT', self.views.listing, self.a, self.listing['revision'])
        self.assertEqual(self.store.db.get('run', 'unrelated-change'), {'workspace': self.b})
        self.assertEqual(self.views.listing(self.a, self.listing['revision'])['views'], [view])
        state_before = self.state_files()
        workspace = self.data['a']
        moved = workspace.with_name('original-workspace')

        def replace_workspace(*args):
            result = parse(*args)
            workspace.rename(moved)
            workspace.mkdir()
            return result

        with patch.object(native_session_views, 'parse_view', side_effect=replace_workspace):
            self.refused('NATIVE_REVISION_STALE', self.views.listing, self.a, self.listing['revision'])
        self.assertEqual(self.state_files(), state_before)

    def test_snapshot_replacement_and_invalid_visible_body_do_not_write(self):
        verify = self.views._verify

        def rescan(*args, **kwargs):
            result = verify(*args, **kwargs)
            if kwargs.get('readable'):
                self.reader.listing(self.a)
            return result

        with patch.object(self.views, '_verify', side_effect=rescan):
            self.refused('NATIVE_REVISION_STALE', self.change, 'hide')
        self.assertEqual(self.store.db.list(KIND), [])
        codex(self.data['codex_root'], self.data['a'], rows=[codex_text('user', '\ud800')])
        self.listing = self.reader.listing(self.a)
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.change, 'hide')
        self.assertEqual(self.store.db.list(KIND), [])

    def test_uncertain_commit_returns_conflict_without_repeating_write(self):
        original = native_session_views.require_current
        calls = []

        def uncertain(state, proof, **flags):
            result = original(state, proof, **flags)
            if flags.get('committed'):
                calls.append(True)
                raise OSError('fixture failure after commit')
            return result

        with patch.object(native_session_views, 'require_current', side_effect=uncertain):
            self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
        self.assertEqual(calls, [True])
        view, = self.views.listing(self.a, self.listing['revision'])['views']
        self.assertEqual((view['hidden'], view['view_revision']), (True, 1))
        self.refused('NATIVE_VIEW_CONFLICT', self.change, 'hide')
        self.assertEqual(self.change('hide', 1), view)
