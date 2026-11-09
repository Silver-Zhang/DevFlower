"""HTTP-level verification of bounded visible pages and snapshot-bound cursors."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlencode

from test_v2_session_identity import fixture_tree, index_for, registry_for
from v2 import native_session_pages as pages, native_sessions as history
from v2.native_history_config import load_history_config, startup_seed
from v2.native_session_views import KIND, NativeSessionViews, action_parameters
from v2.server import create_server
from v2.session_identity import StorageRootIdentity
from v2.session_lease import IDLE_VERIFIED, SessionLeaseAuthority
from v2.store import DraftStore
from v2_native_session_pages_fixture import EARLIEST, LITERAL, LONG, PRIVATE, REPEATED, SECRET, page_fixtures
from v2_native_sessions_fixture import SID, snapshot


class SessionPageTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-session-pages-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data, self.expected = page_fixtures(self.root)
        self.store = DraftStore(self.root / 'state', [self.data['projects']])
        self.a = self.store.open_workspace(str(self.data['a']))['id']
        self.b = self.store.open_workspace(str(self.data['b']))['id']
        self.seed = startup_seed(self.store.state)
        self.reader = self.make_reader()
        self.pages = pages.NativeSessionPages(self.reader)
        self.listing = self.reader.listing(self.a)
        self.scope = self.default_scope(self.listing, 'codex')

    def make_reader(self):
        return history.NativeSessions(self.store, load_history_config(self.data['config']), self.seed,
                                      home=self.data['home'], fixture_redactions=(LITERAL,))

    @staticmethod
    def default_scope(listing, provider):
        row = next(row for row in listing['sessions']
                   if row['provider'] == provider and row['source_labels'] == ('默认来源',))
        return {'workspace': listing['workspace'], 'handle': row['handle'], 'revision': listing['revision']}

    @contextmanager
    def running(self, *, reader=True, service=None):
        instance = self.pages if service is None else service
        with patch('v2.server.NativeSessionPages', return_value=instance):
            server, token = create_server(self.store, native_sessions=instance.reader if reader else None)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
        thread.start()

        def request(path, *, payload=None, raw=None, method=None, headers=None):
            request_headers = {'Authorization': 'Bearer ' + token, **(headers or {})}
            if payload is not None:
                raw = json.dumps(payload).encode()
            if raw is not None:
                request_headers.setdefault('Content-Type', 'application/json; charset=utf-8')
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                client.request(method or ('GET' if raw is None else 'POST'), '/api/v2/' + path, raw, request_headers)
                response = client.getresponse()
                body = response.read()
                if response.status == 200:
                    self.assertEqual(int(response.getheader('Content-Length')), len(body))
                    self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                return response.status, json.loads(body), body
            finally:
                client.close()

        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def browse(self, request, scope=None, **fields):
        return request('native-session-page?' + urlencode({**(scope or self.scope), **fields}))

    def search(self, request, query, scope=None, **fields):
        return request('native-session-search', payload={**(scope or self.scope), 'query': query, **fields})

    def error(self, reply, code, status=409):
        self.assertEqual((reply[0], reply[1].get('code')), (status, code))
        self.assertEqual(set(reply[1]), {'code', 'error'})
        for forbidden in (str(self.root), SID, SECRET, PRIVATE, LITERAL):
            self.assertNotIn(forbidden, str(reply[1]))

    def take_pages(self, request, mode, scope=None, query=None):
        scope = scope or self.scope
        sequence, cursor, observed = [], None, set()
        for _ in range(25):
            extra = {'cursor': cursor} if cursor is not None else {}
            status, result, raw = (self.browse(request, scope, **extra) if mode == 'browse'
                                   else self.search(request, query, scope, **extra))
            self.assertEqual(status, 200, result)
            self.assertLessEqual(len(raw), 131072)
            self.assertEqual(set(result), set(scope) | {'mode', 'messages', 'next_cursor', 'has_more', 'coverage', 'readonly'})
            self.assertEqual({key: result[key] for key in scope}, scope)
            self.assertEqual((result['mode'], result['coverage'], result['readonly']), (mode, 'verified_complete', True))
            self.assertLessEqual(len(result['messages']), 50)
            for part in result['messages']:
                self.assertEqual(set(part), {'role', 'text', 'part_index', 'part_count'})
                self.assertLessEqual(len(part['text']), 16000)
            cursor = result['next_cursor']
            self.assertEqual(result['has_more'], cursor is not None)
            sequence.append(result['messages'])
            if cursor is None:
                return sequence
            self.assertRegex(cursor, r'^[0-9a-f]{32}$')
            self.assertNotIn(cursor, observed)
            observed.add(cursor)
        self.fail('fixture required more than 25 pages')

    def reassemble(self, parts):
        messages, pieces = [], []
        role, count = None, None
        for part in parts:
            if not pieces:
                role, count = part['role'], part['part_count']
            self.assertEqual((part['role'], part['part_count'], part['part_index']), (role, count, len(pieces) + 1))
            pieces.append(part['text'])
            if len(pieces) == count:
                messages.append((role, ''.join(pieces)))
                pieces = []
        self.assertEqual(pieces, [])
        return messages

    def test_both_providers_reassemble_all_pages_and_actual_utf8_envelopes(self):
        with self.running() as request:
            for provider in ('codex', 'claude'):
                scope = self.default_scope(self.listing, provider)
                sequence = self.take_pages(request, 'browse', scope)
                self.assertGreaterEqual(len(sequence), 3)
                joined = self.reassemble(part for page in reversed(sequence) for part in page)
                self.assertEqual(joined, self.expected[provider])
                if provider == 'codex':
                    self.assertEqual(sum(text == REPEATED for _, text in joined), 2)
                    self.assertNotIn(EARLIEST, [part['text'] for part in sequence[0]])
                    self.assertIn(EARLIEST, [part['text'] for part in sequence[-1]])
            old = request('native-session?' + urlencode(self.scope))
            self.assertEqual(old[0], 200)
            self.assertEqual(set(old[1]), set(self.scope) | {'messages', 'truncated', 'readonly'})

    def test_search_uses_complete_sanitized_messages_and_increasing_order(self):
        with self.running() as request:
            early = self.take_pages(request, 'search', query='STRASSE')
            self.assertEqual(self.reassemble(part for page in early for part in page), [('user', EARLIEST)])
            matches = self.take_pages(request, 'search', query='crossboundary')
            self.assertGreaterEqual(len(matches), 3)
            self.assertEqual(self.reassemble(part for page in matches for part in page), [
                ('assistant', LONG), ('user', 'CrossBoundary CrossBoundary later match'),
            ])
            self.assertEqual(matches[0][0]['part_index'], 1)
            self.assertGreater(matches[1][0]['part_index'], 1)
            for query in (PRIVATE, SECRET, LITERAL, ' first-only ', '  first-only ', 'EARLIEST Straße  first-only'):
                result = self.search(request, query)
                self.assertEqual(result[0], 200)
                self.assertEqual(bool(result[1]['messages']), query == ' first-only ')
                self.assertEqual(result[1]['coverage'], 'verified_complete')
                self.assertNotIn('query', result[1])
                self.assertNotIn(SECRET.encode(), result[2])
                self.assertNotIn(PRIVATE.encode(), result[2])
                self.assertNotIn(LITERAL.encode(), result[2])
            claude_scope = self.default_scope(self.listing, 'claude')
            self.assertEqual(self.search(request, PRIVATE, claude_scope)[1]['messages'], [])

    def test_strict_http_shapes_authentication_and_disabled(self):
        valid_page = 'native-session-page?' + urlencode(self.scope)
        with self.running() as request:
            for path in ('native-session-page', valid_page + '&handle=' + self.scope['handle'],
                         valid_page + '&workspace=%FF', valid_page + '&extra=1', valid_page + '&',
                         valid_page + '&cursor=' + 'A' * 1024):
                self.error(request(path), 'NATIVE_PAGE_INVALID', 400)
            self.error(request(valid_page + '&cursor='), 'NATIVE_CURSOR_INVALID')
            for query in ('', '  ', '\u200e', 'has\ncontrol', '\ud800', 'a' * 201, 'ﬃ' * 140, 5, None):
                self.error(self.search(request, query), 'NATIVE_SEARCH_INVALID', 400)
            self.error(self.search(request, 'x', cursor=None), 'NATIVE_CURSOR_INVALID')
            raw = json.dumps({**self.scope, 'query': 'x'}).encode()
            for invalid in (b'{}', b'[]', b'\xff', b'{' + b'a' * 4096,
                            raw[:-1] + b',"query":"x"}', raw[:-1] + b',"cursor":NaN}'):
                self.error(request('native-session-search', raw=invalid), 'NATIVE_SEARCH_INVALID', 400)
            self.error(request('native-session-search?', raw=raw), 'NATIVE_SEARCH_INVALID', 400)
            self.error(request('native-session-search', raw=raw, headers={'Content-Type': 'text/plain'}),
                       'NATIVE_SEARCH_INVALID', 400)
            self.assertEqual(request(valid_page, headers={'Authorization': 'Bearer invalid'})[0], 401)
            self.assertEqual(request('native-session-search', raw=raw, headers={'Host': 'invalid'})[0], 403)
            self.assertEqual(request('native-session-search', raw=raw, headers={'Origin': 'https://test.invalid'})[0], 403)
        with self.running(reader=False) as request:
            self.error(self.browse(request), 'NATIVE_HISTORY_DISABLED', 404)
            self.error(self.search(request, 'x'), 'NATIVE_HISTORY_DISABLED', 404)

    def test_cursor_binding_replay_and_expiration(self):
        second_workspace = self.default_scope(self.reader.listing(self.b), 'codex')
        second_handle = self.default_scope(self.listing, 'claude')
        with self.running() as request:
            first = self.search(request, 'crossboundary')[1]
            cursor = first['next_cursor']
            follow = self.search(request, 'CROSSBOUNDARY', cursor=cursor)
            self.assertEqual(follow[0], 200)
            self.assertIsNotNone(follow[1]['next_cursor'])
            cache_before = {key: replace(item) for key, item in self.pages.cursors.items()}
            self.assertEqual(self.search(request, 'crossboundary', cursor=cursor), follow)
            self.assertEqual(self.pages.cursors, cache_before)
            self.assertLessEqual(self.pages.cursors[cursor].expires, self.reader.snapshots[self.a].expires)
            self.error(self.browse(request, cursor=cursor), 'NATIVE_CURSOR_INVALID')
            self.error(self.search(request, 'different', cursor=cursor), 'NATIVE_CURSOR_INVALID')
            self.error(self.search(request, 'crossboundary', second_handle, cursor=cursor), 'NATIVE_CURSOR_INVALID')
            self.error(self.search(request, 'crossboundary', second_workspace, cursor=cursor), 'NATIVE_CURSOR_INVALID')
            self.error(self.browse(request, cursor='f' * 32), 'NATIVE_CURSOR_INVALID')
            self.error(self.browse(request, {**self.scope, 'workspace': self.b}, cursor='malformed'),
                       'NATIVE_SESSION_UNAVAILABLE', 404)
            self.pages.cursors[cursor].expires = time.monotonic() - 1
            self.error(self.search(request, 'crossboundary', cursor=cursor), 'NATIVE_CURSOR_INVALID')
            self.reader.snapshots[self.a] = replace(self.reader.snapshots[self.a], expires=time.monotonic() - 1)
            self.error(self.browse(request, cursor='malformed'), 'NATIVE_REVISION_STALE')

    def test_cursor_eviction_replacement_and_restart_never_reissue(self):
        with self.running() as request, patch.object(pages, 'SNAPSHOT_CURSOR_LIMIT', 2):
            first = self.browse(request)[1]['next_cursor']
            second = self.browse(request, cursor=first)[1]['next_cursor']
            with self.reader.guard:
                del self.pages.cursors[second]  # Defend against an unavailable cached continuation.
            self.error(self.browse(request, cursor=first), 'NATIVE_CURSOR_INVALID')
            self.assertEqual(list(self.pages.cursors), [first])
            self.browse(request)
            self.browse(request)
            self.assertEqual(len(self.pages.cursors), 2)
            self.error(self.browse(request, cursor=first), 'NATIVE_CURSOR_INVALID')
            replacement = self.default_scope(self.reader.listing(self.a), 'codex')
            self.error(self.browse(request, cursor=first), 'NATIVE_REVISION_STALE')
            self.error(self.browse(request, replacement, cursor=first), 'NATIVE_CURSOR_INVALID')
        fresh = self.make_reader()
        fresh_scope = self.default_scope(fresh.listing(self.a), 'codex')
        with self.running(service=pages.NativeSessionPages(fresh)) as request:
            self.error(self.browse(request, fresh_scope, cursor=first), 'NATIVE_CURSOR_INVALID')

    def test_global_cursor_budget_across_workspaces(self):
        b_scope = self.default_scope(self.reader.listing(self.b), 'codex')
        with self.running() as request, patch.object(pages, 'CURSOR_LIMIT', 2):
            expired = self.browse(request)[1]['next_cursor']
            other = self.browse(request, b_scope)[1]['next_cursor']
            live = self.browse(request)[1]['next_cursor']
            self.assertEqual(list(self.pages.cursors), [other, live])
            self.error(self.browse(request, cursor=expired), 'NATIVE_CURSOR_INVALID')
            self.assertEqual(self.browse(request, b_scope, cursor=other)[0], 200)

    def test_same_cursor_concurrent_replay_publishes_one_continuation(self):
        with self.running() as request, ThreadPoolExecutor(max_workers=2) as executor:
            cursor = self.browse(request)[1]['next_cursor']
            barrier = Barrier(2)
            project = pages.project_page

            def synchronized(*args):
                barrier.wait(4)
                return project(*args)

            with patch.object(pages, 'project_page', side_effect=synchronized):
                first, second = executor.map(lambda _: self.browse(request, cursor=cursor), (0, 1))
            self.assertEqual(first[0], 200, first[1])
            self.assertEqual(first, second)
            self.assertEqual(list(self.pages.cursors), [cursor, first[1]['next_cursor']])
            saved = {key: replace(item) for key, item in self.pages.cursors.items()}
            self.assertEqual(self.browse(request, cursor=cursor), first)
            self.assertEqual(self.pages.cursors, saved)

    def test_four_slots_shared_and_budget_refusal_is_complete(self):
        entered = Barrier(5)
        release = Event()
        project = pages.project_page

        def hold(*args):
            entered.wait(5)
            if not release.wait(6):
                raise AssertionError('fixture did not release read slots')
            return project(*args)

        with self.running() as request, ThreadPoolExecutor(max_workers=4) as executor:
            with patch.object(pages, 'project_page', side_effect=hold):
                pending = [executor.submit(self.browse, request) for _ in range(4)]
                try:
                    entered.wait(5)
                    self.error(self.browse(request), 'NATIVE_SCAN_BUSY')
                    self.error(request('native-session?' + urlencode(self.scope)), 'NATIVE_SCAN_BUSY')
                finally:
                    release.set()
                for item in pending:
                    self.assertEqual(item.result()[0], 200)
            prior = list(self.pages.cursors)
            for limit in ('SEARCH_MESSAGES', 'SEARCH_PARTS', 'SEARCH_CHARS', 'SEARCH_MATCHES'):
                with patch.object(pages, limit, 1):
                    self.error(self.search(request, 'crossboundary'), 'NATIVE_VISIBLE_BUDGET')
                self.assertEqual(list(self.pages.cursors), prior)
            project_message = pages.visible_message
            clock = SimpleNamespace(now=time.monotonic())
            def advance(*args):
                clock.now += pages.READ_SECONDS + 1
                return project_message(*args)
            with (patch.object(pages, 'time', SimpleNamespace(monotonic=lambda: clock.now)),
                  patch.object(pages, 'visible_message', side_effect=advance)):
                self.error(self.search(request, 'no match'), 'NATIVE_VISIBLE_BUDGET')
            self.assertEqual(list(self.pages.cursors), prior)

    def test_failed_final_budget_does_not_publish_or_evict(self):
        with self.running() as request, patch.object(pages, 'SNAPSHOT_CURSOR_LIMIT', 2):
            self.browse(request)
            self.browse(request)
            prior = {key: replace(item) for key, item in self.pages.cursors.items()}
            wire = pages.wire_bytes
            clock = SimpleNamespace(now=time.monotonic())

            def time_after_json(value):
                result = wire(value)
                if isinstance(value, dict) and value.get('messages'):
                    clock.now += pages.READ_SECONDS + 1
                return result

            with (patch.object(pages, 'time', SimpleNamespace(monotonic=lambda: clock.now)),
                  patch.object(pages, 'wire_bytes', side_effect=time_after_json)):
                self.error(self.browse(request), 'NATIVE_VISIBLE_BUDGET')
            self.assertEqual(self.pages.cursors, prior)

    def test_file_line_and_wire_budget_errors_never_return_empty_success(self):
        with self.running() as request:
            for module, name, limit in ((pages, 'FILE_BYTES', 1), (history, 'LINE_BYTES', 1000),
                                        (pages, 'MAX_BODY_BYTES', 200)):
                with patch.object(module, name, limit):
                    self.error(self.browse(request), 'NATIVE_VISIBLE_BUDGET')
            self.assertEqual(list(self.pages.cursors), [])

    def test_modified_source_inside_and_beyond_the_current_page_refuses(self):
        with self.running() as request:
            cursor = self.browse(request)[1]['next_cursor']
            original = self.data['main'].read_bytes()
            project_message = pages.visible_message
            changed_during_read = False

            def append_during_read(*args):
                nonlocal changed_during_read
                if not changed_during_read:
                    with self.data['main'].open('ab') as native:
                        native.write(b'\n')
                    changed_during_read = True
                return project_message(*args)

            with patch.object(pages, 'visible_message', side_effect=append_during_read):
                self.error(self.browse(request, cursor=cursor), 'NATIVE_REVISION_STALE')
            self.assertTrue(changed_during_read)
            self.data['main'].write_bytes(original)
            self.scope = self.default_scope(self.reader.listing(self.a), 'codex')
            cursor = self.browse(request)[1]['next_cursor']
            changed = original.replace(b'CODEX_VISIBLE_165', b'CODEX_CHANGED_165', 1)
            self.assertNotEqual(changed, original)
            self.data['main'].write_bytes(changed)
            self.error(self.browse(request, cursor=cursor), 'NATIVE_REVISION_STALE')
            self.data['main'].write_bytes(original)
            self.scope = self.default_scope(self.reader.listing(self.a), 'codex')
            ref = self.reader.snapshots[self.a].reads[self.scope['handle']].ref
            cursor = self.browse(request)[1]['next_cursor']
            changed = original.replace(b'CODEX_VISIBLE_001', b'CODEX_CHANGED_001', 1)
            self.assertNotEqual(changed, original)
            self.data['main'].write_bytes(changed)
            # Hold metadata evidence constant to test the byte digest itself.
            with (patch.object(pages, 'stamp', return_value=ref.stamp),
                  patch.object(pages, 'require_file'), patch.object(history, 'require_file')):
                self.error(self.browse(request, cursor=cursor), 'NATIVE_REVISION_STALE')

    def test_durable_unrelated_write_during_projection_refuses_without_cursor(self):
        project = pages.project_page

        def unrelated_write(*args):
            result = project(*args)
            self.store.db.put('run', 'synthetic-concurrent-change', {'workspace': self.b})
            return result

        with self.running() as request, patch.object(pages, 'project_page', side_effect=unrelated_write):
            self.error(self.browse(request), 'NATIVE_REVISION_STALE')
            self.assertEqual(list(self.pages.cursors), [])
        self.assertEqual(self.store.db.get('run', 'synthetic-concurrent-change'), {'workspace': self.b})

    def state_files(self):
        return {key: value for key, value in snapshot(self.store.state).items() if key[1] != '.'}

    def test_reads_preserve_durable_state_native_bytes_and_reject_busy_missing_or_third_file(self):
        self.store.save(self.a, None, None, {'version': 1, 'name': '合成草稿', 'nodes': [], 'edges': []})
        self.store.db.put('run', 'synthetic-run', {'workspace': self.a, 'state': 'UNKNOWN'})
        self.store.db.event('synthetic-run', 'synthetic-event')
        self.store.db.put('native-session-claim', 'synthetic-claim', {'session': SID})
        views = NativeSessionViews(self.reader)
        views.change(action_parameters(json.dumps({
            **self.scope, 'action': 'rename', 'alias': '合成别名', 'expected_view_revision': None,
        }).encode()))
        self.assertEqual(len(self.store.db.list(KIND)), 1)
        lease_base = self.root / 'isolated-lease-fixture'
        lease_base.mkdir()
        anchor, alpha, beta, cwd, other = fixture_tree(lease_base)
        registry = registry_for(anchor, alpha, beta)
        index = index_for(registry, cwd, other)
        lease_root = lease_base / 'authority'
        lease_root.mkdir(mode=0o700)
        authority = SessionLeaseAuthority.provision(lease_root, StorageRootIdentity.capture(lease_root), registry)
        handle = next(iter(index.handles('A')))
        with authority.acquire(index, 'A', handle, 'fixture-op', 'fixture-intent', lambda _: IDLE_VERIFIED) as lease:
            lease.check_before_fixture()
            lease.record_outcome(completed=True)
        original_lease = snapshot(lease_root)
        state = self.state_files()
        native = snapshot(self.data['projects'], self.data['home'], self.data['isolated'])
        main = self.store.state / 'v2.sqlite'
        with self.running() as request:
            self.assertEqual(self.browse(request)[0], 200)
            self.assertEqual(self.search(request, 'nonmatching')[0], 200)
            self.assertEqual(self.state_files(), state)
            self.assertEqual(snapshot(self.data['projects'], self.data['home'], self.data['isolated']), native)
            writer = sqlite3.connect(main, timeout=0)
            try:
                writer.execute('BEGIN IMMEDIATE')
                self.error(self.browse(request), 'NATIVE_REVISION_STALE')
                self.error(self.search(request, 'nonmatching'), 'NATIVE_REVISION_STALE')
                self.assertTrue((self.store.state / 'v2.sqlite-wal').exists())
            finally:
                writer.close()
            self.assertEqual(self.state_files(), state)
            outside = self.root / 'removed.sqlite'
            main.rename(outside)
            self.error(self.browse(request), 'NATIVE_REVISION_STALE')
            self.error(self.search(request, 'nonmatching'), 'NATIVE_REVISION_STALE')
            self.assertFalse(main.exists())
            outside.rename(main)
            for name in ('v2.sqlite-wal', 'unapproved-third-file'):
                foreign = self.store.state / name
                foreign.write_bytes(b'unchanged fixture')
                self.error(self.browse(request), 'NATIVE_REVISION_STALE')
                self.error(self.search(request, 'nonmatching'), 'NATIVE_REVISION_STALE')
                self.assertEqual(foreign.read_bytes(), b'unchanged fixture')
                foreign.unlink()
        self.assertEqual(self.state_files(), state)
        self.assertEqual(snapshot(lease_root), original_lease)
