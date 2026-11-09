"""Read-only V2 history, scoped snapshots and same-state DB guard tests."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlencode

from v2 import native_sessions, session_identity, workspace_select
from v2.native_history_config import SEED_FILE, load_history_config, startup_seed
from v2.native_sessions import NativeHistoryError, NativeSessions
from v2.server import create_server
from v2.store import DraftStore
from studio.graph import Invalid
from v2_native_sessions_fixture import OTHER, SID, codex, codex_text, fixtures, snapshot


class NativeSessionTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-native-sessions-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.fixture = fixtures(self.base)
        self.store = DraftStore(self.base / 'state', [self.fixture['projects']])
        self.a, self.b, self.empty = [self.store.open_workspace(str(self.fixture[key]))['id']
                                      for key in ('a', 'b', 'empty')]
        self.seed = startup_seed(self.store.state)
        self.service = self.reader()

    def reader(self, store=None):
        return NativeSessions(store or self.store, load_history_config(self.fixture['config']),
                              self.seed, home=self.fixture['home'])

    def refused(self, code, fn, *args):
        with self.assertRaises(NativeHistoryError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.base), str(caught.exception))
        self.assertNotIn(SID, str(caught.exception))

    @contextmanager
    def running(self, reader):
        server, token = create_server(self.store, native_sessions=reader)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, *, authorized=True, method='GET'):
            headers = {'Authorization': 'Bearer ' + token} if authorized else {}
            if method == 'POST':
                headers['Content-Type'] = 'application/json'
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request(method, '/api/v2/' + path, '{}' if method == 'POST' else None, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def row(self, listing, provider='codex', label='默认来源'):
        return next(row for row in listing['sessions'] if row['provider'] == provider and label in row['source_labels'])

    def read(self, listing, row):
        return self.service.read(listing['workspace'], row['handle'], listing['revision'])

    def state_files(self):
        # Transient SQLite sidecars can change the parent directory mtime.
        return {key: value for key, value in snapshot(self.store.state).items() if key[1] != '.'}

    def test_http_default_nested_isolated_visible_and_fixture_state_unchanged(self):
        native = (self.fixture['projects'], self.fixture['home'], self.fixture['isolated'])
        before, state_before = snapshot(*native), self.state_files()
        with self.running(self.service) as request:
            code, listing = request('native-sessions?' + urlencode({'workspace': self.a}))
            self.assertEqual(code, 200)
            self.assertEqual(set(listing), {'workspace', 'revision', 'coverage', 'sessions'})
            self.assertEqual(listing['coverage'], {'state': 'complete', 'reasons': []})
            self.assertEqual(len(listing['sessions']), 4)
            all_messages = []
            for row in listing['sessions']:
                self.assertEqual(set(row), {'handle', 'provider', 'source_labels', 'updated_at', 'access'})
                self.assertEqual(row['access'], 'readable')
                code, history = request('native-session?' + urlencode({
                    'workspace': self.a, 'handle': row['handle'], 'revision': listing['revision']}))
                self.assertEqual(code, 200)
                self.assertEqual(set(history), {'workspace', 'revision', 'handle', 'messages', 'truncated', 'readonly'})
                self.assertTrue(history['readonly'])
                self.assertTrue(all(set(message) == {'role', 'text'} for message in history['messages']))
                all_messages += [message['text'] for message in history['messages']]
            visible = '\n'.join(all_messages)
            for marker in ('DEFAULT_CODEX_A <b>plain</b>', 'DEFAULT_CODEX_FINAL', 'DEFAULT_CLAUDE_A',
                           'NESTED_CLAUDE_A', 'ISOLATED_CODEX_A', '[REDACTED]'):
                self.assertIn(marker, visible)
            for marker in ('PRIVATE_', 'DONT_SHOW', 'DEFAULT_CODEX_B', 'AUTH_SENTINEL', str(self.base)):
                self.assertNotIn(marker, visible + str(listing))
            code, other = request('native-sessions?workspace=' + self.b)
            self.assertEqual((code, len(other['sessions'])), (200, 1))
            wrong = {'workspace': self.b, 'handle': listing['sessions'][0]['handle'], 'revision': other['revision']}
            self.assertEqual(request('native-session?' + urlencode(wrong))[1]['code'], 'NATIVE_SESSION_UNAVAILABLE')
            wrong['revision'] = listing['revision']
            self.assertEqual(request('native-session?' + urlencode(wrong))[0], 404)
            self.assertEqual(request('native-sessions?workspace=' + self.empty)[1]['sessions'], [])
        self.assertEqual(snapshot(*native), before)
        self.assertEqual(self.state_files(), state_before)

    def test_http_auth_disabled_strict_queries_and_get_only(self):
        with self.running(None) as request:
            self.assertFalse(request('status')[1]['native_history_available'])
            route = 'native-sessions?workspace=' + self.a
            self.assertEqual(request(route)[1]['code'], 'NATIVE_HISTORY_DISABLED')
            self.assertEqual(request(route, authorized=False)[0], 401)
            self.assertEqual(request(route, method='POST')[0], 404)
            for bad in ('native-sessions', route + '&workspace=' + self.a, route + '&root=/tmp',
                        'native-sessions?workspace=%FF', 'native-session?workspace=' + self.a):
                with self.subTest(bad=bad):
                    code, value = request(bad)
                    self.assertEqual((code, value['code']), (400, 'NATIVE_QUERY_INVALID'))

    def test_restart_preserves_handles_and_disabled_seeded_state_remains_usable(self):
        old = self.service.listing(self.a)
        draft = self.store.save(self.a, None, None, {'version': 1, 'name': 'Seeded draft', 'nodes': [], 'edges': []})
        with self.store.db.tx() as db:
            db.execute('INSERT INTO records VALUES(?,?,?)', ('run', 'synthetic', json.dumps({'workspace': self.a})))
        restarted = DraftStore(self.store.state, [self.fixture['projects']])
        self.assertEqual(startup_seed(restarted.state), self.seed)
        service = self.reader(restarted)
        listing = service.listing(self.a)
        self.assertEqual([row['handle'] for row in old['sessions']], [row['handle'] for row in listing['sessions']])
        self.assertNotEqual(old['revision'], listing['revision'])
        self.refused('NATIVE_REVISION_STALE', service.read, self.a, old['sessions'][0]['handle'], old['revision'])
        with self.running(None) as request:
            self.assertEqual(request('native-sessions?workspace=' + self.a)[1]['code'], 'NATIVE_HISTORY_DISABLED')
            self.assertEqual(request('draft?workspace=' + self.a + '&id=' + draft['id'])[1]['graph']['name'], 'Seeded draft')
        self.assertEqual(restarted.db.get('run', 'synthetic'), {'workspace': self.a})
        seed = restarted.state / SEED_FILE
        self.assertEqual((seed.stat().st_size, seed.stat().st_mode & 0o777), (32, 0o600))

    def test_conflict_from_another_workspace_and_mixed_cwd_are_not_readable(self):
        codex(self.fixture['codex_root'], self.fixture['b'], suffix='_' + OTHER,
              rows=[codex_text('user', 'CONFLICT_BODY')])
        listing = self.service.listing(self.a)
        row = self.row(listing)
        self.assertEqual(row['access'], 'conflict')
        self.assertIn('identity_conflict', listing['coverage']['reasons'])
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.read, listing, row)
        self.assertNotIn('CONFLICT_BODY', str(listing))
        codex(self.fixture['isolated'], self.fixture['a'], rows=[
            {'type': 'turn_context', 'payload': {'cwd': str(self.fixture['b'])}},
            codex_text('user', 'WRONG_CWD_BODY')])
        listing = self.service.listing(self.a)
        row = self.row(listing, label='隔离来源 reviewer')
        self.assertEqual(row['access'], 'unverified')
        self.refused('NATIVE_SESSION_UNAVAILABLE', self.read, listing, row)

    def test_budget_partial_and_bad_leaf_do_not_authorize_ambiguous_root(self):
        codex(self.fixture['codex_root'], self.fixture['a'], OTHER, rows=[codex_text('user', 'ANOTHER_A')])
        with patch.object(native_sessions, 'ROOT_BOUNDS', (2000, 1, 32 * 1024 * 1024)):
            listing = self.service.listing(self.a)
        self.assertIn('file_budget', listing['coverage']['reasons'])
        self.assertTrue(all(row['access'] == 'unverified' for row in listing['sessions']
                            if row['provider'] == 'codex' and row['source_labels'] == ('默认来源',)))
        hard = self.fixture['main'].with_name(self.fixture['main'].name.replace('.jsonl', '_' + OTHER + '.jsonl'))
        os.link(self.fixture['main'], hard)
        listing = self.service.listing(self.a)
        self.assertEqual(listing['coverage']['state'], 'partial')
        self.assertFalse(any(row['access'] == 'readable' for row in listing['sessions']
                             if row['provider'] == 'codex' and row['source_labels'] == ('默认来源',)))
        self.assertEqual(self.row(listing, 'claude')['access'], 'readable')

    def test_symlink_fifo_and_single_file_line_budgets_fail_closed(self):
        root = self.fixture['main']
        unusual = root.with_name(root.name.replace('.jsonl', '_' + OTHER + '.jsonl'))
        unusual.symlink_to(root)
        for replace_type in (False, True):
            if replace_type:
                unusual.unlink()
                os.mkfifo(unusual)
            listing = self.service.listing(self.a)
            self.assertEqual(self.row(listing)['access'], 'unverified')
            self.assertIn('source_unavailable', listing['coverage']['reasons'])
        unusual.unlink()
        for bound in ('FILE_BYTES', 'LINE_BYTES'):
            with patch.object(native_sessions, bound, 64):
                listing = self.service.listing(self.a)
                self.assertIn('transcript_unavailable', listing['coverage']['reasons'])
                self.assertFalse(any(row['access'] == 'readable' for row in listing['sessions']))

    def test_projection_deadline_cannot_authorize_an_unfinished_source(self):
        monotonic = native_sessions.time.monotonic
        checked = native_sessions.checked_cwd
        expired = False

        def advance(*args):
            nonlocal expired
            evidence = checked(*args)
            expired = True
            return evidence

        with (patch.object(native_sessions, 'checked_cwd', side_effect=advance),
              patch.object(native_sessions.time, 'monotonic', side_effect=lambda: monotonic() + (10 if expired else 0))):
            listing = self.service.listing(self.a)
        self.assertIn('time_budget', listing['coverage']['reasons'])
        self.assertFalse(any(row['access'] == 'readable' for row in listing['sessions']))

    def test_default_mount_alias_is_refused_before_reading_body(self):
        real_mount = session_identity.mount_id

        def aliased(fd):
            path = os.readlink(f'/proc/self/fd/{fd}')
            return real_mount(fd) + int(path == str(self.fixture['codex_root']))

        with patch.object(session_identity, 'mount_id', side_effect=aliased):
            reader = self.reader()
            listing = reader.listing(self.a)
        self.assertIn('source_unavailable', listing['coverage']['reasons'])
        self.assertFalse(any(row['provider'] == 'codex' and row['source_labels'] == ('默认来源',)
                             for row in listing['sessions']))

    def test_file_mutation_duplicate_creation_and_cwd_rebind_require_refresh(self):
        listing = self.service.listing(self.a)
        row = self.row(listing)
        before = self.fixture['main'].stat()
        body = self.fixture['main'].read_bytes().replace(b'DEFAULT_CODEX_A', b'REPLACED_CODEX_A')
        self.assertNotEqual(body, self.fixture['main'].read_bytes())
        self.fixture['main'].write_bytes(body)
        os.utime(self.fixture['main'], ns=(before.st_atime_ns, before.st_mtime_ns))
        self.refused('NATIVE_REVISION_STALE', self.read, listing, row)
        listing = self.service.listing(self.a)
        row = self.row(listing)
        codex(self.fixture['codex_root'], self.fixture['b'], suffix='_' + OTHER)
        self.refused('NATIVE_REVISION_STALE', self.read, listing, row)
        listing = self.service.listing(self.a)
        nested = self.row(listing, 'claude', '项目嵌套来源')
        self.fixture['cwd'].rename(self.fixture['a'] / 'old-module')
        self.fixture['cwd'].symlink_to(self.fixture['b'], target_is_directory=True)
        self.refused('NATIVE_REVISION_STALE', self.read, listing, nested)

    def test_message_window_bounds_literal_redaction_and_unicode_refusal(self):
        reader = NativeSessions(self.store, load_history_config(self.fixture['config']), self.seed,
                                home=self.fixture['home'], fixture_redactions=('ONLY_THIS_FIXTURE',))
        codex(self.fixture['codex_root'], self.fixture['a'], rows=[
            *(codex_text('user', 'x' * 2000) for _ in range(110)),
            codex_text('assistant', 'ONLY_THIS_FIXTURE ' + '界' * 18000),
        ])
        listing = reader.listing(self.a)
        row = self.row(listing)
        response = reader.read(self.a, row['handle'], listing['revision'])
        self.assertTrue(response['truncated'])
        self.assertLessEqual(len(response['messages']), 100)
        self.assertLessEqual(max(len(message['text']) for message in response['messages']), 16000)
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False).encode()), 128 * 1024)
        self.assertNotIn('ONLY_THIS_FIXTURE', str(response))
        self.assertIn('[REDACTED]', str(response))
        codex(self.fixture['codex_root'], self.fixture['a'], rows=[codex_text('user', '\ud800')])
        listing = reader.listing(self.a)
        row = self.row(listing)
        self.refused('NATIVE_SESSION_UNAVAILABLE', reader.read, self.a, row['handle'], listing['revision'])

    def test_expired_replaced_and_inflight_revisions_cannot_be_used(self):
        listing = self.service.listing(self.a)
        row = self.row(listing)
        with patch.object(native_sessions.time, 'monotonic', return_value=self.service.snapshots[self.a].expires):
            self.refused('NATIVE_REVISION_STALE', self.read, listing, row)
        listing = self.service.listing(self.a)
        row = self.row(listing)
        seen, release = Event(), Event()
        real = native_sessions.project_messages
        outcomes = []

        def hold(*args):
            result = real(*args)
            seen.set()
            if not release.wait(10):
                raise AssertionError('release missing')
            return result

        def read():
            try:
                self.read(listing, row)
            except NativeHistoryError as error:
                outcomes.append(error.code)

        with patch.object(native_sessions, 'project_messages', side_effect=hold):
            thread = Thread(target=read)
            thread.start()
            try:
                self.assertTrue(seen.wait(10))
                newer = self.service.listing(self.a)
            finally:
                release.set()
                thread.join(10)
        self.assertEqual(outcomes, ['NATIVE_REVISION_STALE'])
        self.assertNotEqual(listing['revision'], newer['revision'])

    def test_snapshot_cache_is_bounded_and_duplicate_scan_refuses(self):
        listing = self.service.listing(self.a)
        for n in range(4):
            project = self.fixture['projects'] / f'cache-{n}'
            project.mkdir()
            key = self.store.open_workspace(str(project))['id']
            self.service.listing(key)
        self.assertEqual(len(self.service.snapshots), 4)
        self.refused('NATIVE_REVISION_STALE', self.read, listing, self.row(listing))
        started, release = Event(), Event()
        actual_scan = self.service.scan

        def wait(*args):
            started.set()
            if not release.wait(10):
                raise AssertionError('no release from test')
            return actual_scan(*args)

        with patch.object(self.service, 'scan', side_effect=wait):
            thread = Thread(target=self.service.listing, args=(self.a,))
            thread.start()
            try:
                self.assertTrue(started.wait(10))
                self.refused('NATIVE_SCAN_BUSY', self.service.listing, self.a)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())

    def test_configuration_requires_private_regular_file(self):
        config = self.fixture['config']
        config.chmod(0o644)
        with self.assertRaises(Invalid):
            load_history_config(config)
        config.chmod(0o600)
        alias = self.base / 'linked.json'
        alias.symlink_to(config)
        with self.assertRaises(Invalid):
            load_history_config(alias)

    def test_missing_main_no_create_and_preexisting_sidecars_are_untouched(self):
        main = self.store.state / 'v2.sqlite'
        moved = self.base / 'main.saved'
        main.rename(moved)
        self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)
        self.assertFalse(main.exists())
        moved.rename(main)
        with sqlite3.connect(main) as active:
            active.execute('SELECT data FROM records').fetchall()
            self.assertTrue((self.store.state / 'v2.sqlite-wal').is_file())
            before = snapshot(self.store.state)
            self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)
            self.assertEqual(snapshot(self.store.state), before)
        active.close()

    def test_third_file_and_main_replacement_after_connect_are_refused(self):
        connect = sqlite3.connect
        state = self.store.state
        main = state / 'v2.sqlite'
        unknown = state / 'unexpected.tmp'

        def add_third(*args, **kwargs):
            db = connect(*args, **kwargs)
            unknown.write_bytes(b'test sentinel')
            return db

        with patch.object(workspace_select.sqlite3, 'connect', side_effect=add_third):
            self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)
        self.assertEqual(unknown.read_bytes(), b'test sentinel')
        self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)
        unknown.unlink()

        def replace_main(*args, **kwargs):
            db = connect(*args, **kwargs)
            old = self.base / 'old-main'
            main.rename(old)
            shutil.copyfile(old, main)
            main.chmod(0o600)
            return db

        with patch.object(workspace_select.sqlite3, 'connect', side_effect=replace_main):
            self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)

    def test_list_and_read_detect_durable_state_changes_during_projection(self):
        listing = self.service.listing(self.a)
        actual_scan = self.service.scan

        def mutate(*args):
            result = actual_scan(*args)
            self.store.db.put('test-fixture', 'unrelated', {'marker': True})
            return result

        with patch.object(self.service, 'scan', side_effect=mutate):
            self.refused('NATIVE_REVISION_STALE', self.service.listing, self.a)
        self.refused('NATIVE_REVISION_STALE', self.read, listing, self.row(listing))
        listing = self.service.listing(self.a)
        actual_read = native_sessions.project_messages

        def introduce_third(*args):
            response = actual_read(*args)
            (self.store.state / 'unknown-projection-file').touch()
            return response

        with patch.object(native_sessions, 'project_messages', side_effect=introduce_third):
            self.refused('NATIVE_REVISION_STALE', self.read, listing, self.row(listing))
        self.assertTrue((self.store.state / 'unknown-projection-file').exists())
