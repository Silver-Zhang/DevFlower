"""Genuine loopback V2 activity read boundaries on synthetic provider files."""
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
from v2 import native_session_activity as activity, native_sessions as history
from v2.native_history_config import load_history_config, startup_seed
from v2.native_session_views import NativeSessionViews, action_parameters
from v2.server import create_server
from v2.session_identity import StorageRootIdentity
from v2.session_lease import IDLE_VERIFIED, SessionLeaseAuthority
from v2.store import DraftStore
from v2_native_session_activity_fixture import (
    A, B, BAD_NAME, RAW_ARG, RAW_RESULT, SECRET, activity_fixtures, call, claude_event, use,
)
from v2_native_sessions_fixture import SID, codex, records, snapshot

UTC_A = '2020-01-02T00:00:00Z'
FIELDS = {'category', 'observation', 'started_at', 'returned_at'}


def item(category, observation, start=None, end=None):
    return dict(category=category, observation=observation, started_at=start, returned_at=end)


class SessionActivityTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-session-activity-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = activity_fixtures(self.root)
        self.store = DraftStore(self.root / 'state', [self.data['projects']])
        self.a = self.store.open_workspace(str(self.data['a']))['id']
        self.b = self.store.open_workspace(str(self.data['b']))['id']
        seed = startup_seed(self.store.state)
        self.reader = history.NativeSessions(self.store, load_history_config(self.data['config']), seed, home=self.data['home'])
        self.service = activity.NativeSessionActivity(self.reader)
        self.listing = self.reader.listing(self.a)
        self.scope = self.find_scope(self.listing, 'codex')

    @staticmethod
    def find_scope(listing, provider, label='默认来源'):
        row = next(row for row in listing['sessions'] if row['provider'] == provider and label in row['source_labels'])
        return dict(workspace=listing['workspace'], revision=listing['revision'], handle=row['handle'])

    @contextmanager
    def running(self, *, enabled=True):
        with patch('v2.server.NativeSessionActivity', return_value=self.service):
            server, token = create_server(self.store, native_sessions=self.reader if enabled else None)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
        thread.start()

        def request(path, *, headers=None, method='GET', body=None):
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                client.request(method, '/api/v2/' + path, body, {'Authorization': 'Bearer ' + token, **(headers or {})})
                response = client.getresponse()
                raw = response.read()
                if response.status == 200:
                    self.assertEqual(len(raw), int(response.getheader('Content-Length')))
                    self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                return response.status, json.loads(raw), raw
            finally:
                client.close()

        try:
            yield request
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def get(self, request, scope=None):
        return request('native-session-activity?' + urlencode(scope or self.scope))

    def success(self, response, scope=None, *, partial=False):
        status, result, raw = response
        self.assertEqual(status, 200, result)
        scope = scope or self.scope
        self.assertEqual(set(result), set(scope) | {'activity', 'coverage', 'readonly'})
        self.assertEqual({key: result[key] for key in scope}, scope)
        self.assertEqual(result['coverage'], {'state': 'partial' if partial else 'complete',
                                             'reasons': ['display_limit'] if partial else []})
        self.assertIs(result['readonly'], True)
        self.assertTrue(all(set(row) == FIELDS for row in result['activity']))
        self.assertLessEqual(len(raw), 131072)
        for value in (RAW_ARG, RAW_RESULT, SECRET, BAD_NAME, SID, str(self.root), 'public-pair', 'private-mcp', 'CLAUDE_TOOL'):
            self.assertNotIn(value.encode(), raw)
        return result['activity']

    def refused(self, response, code, status=409):
        self.assertEqual((response[0], response[1].get('code')), (status, code))
        self.assertEqual(set(response[1]), {'code', 'error'})
        for value in (RAW_ARG, RAW_RESULT, SECRET, SID, str(self.root)):
            self.assertNotIn(value.encode(), response[2])

    def rescan(self):
        self.listing = self.reader.listing(self.a)
        self.scope = self.find_scope(self.listing, 'codex')

    def test_codex_all_observations_time_order_and_hidden_relationships(self):
        with self.running() as request:
            result = self.success(self.get(request))
            self.assertEqual(result, [
                item('读取', 'return_observed', UTC_A, B),
                item('编辑', 'start_only'),
                item('其他', 'return_only', end=UTC_A),
                item('搜索', 'unpaired', B),
                item('其他', 'unpaired', end=UTC_A),
                item('其他', 'ambiguous'),
                item('其他', 'ambiguous'),
                item('其他', 'ambiguous'),
                item('其他', 'ambiguous'),
                item('其他', 'start_only'),
                item('命令', 'start_only'),
                item('其他', 'return_only', end=B),
            ])
            old = request('native-session?' + urlencode(self.scope))
            self.assertEqual(set(old[1]), set(self.scope) | {'messages', 'truncated', 'readonly'})

    def test_claude_role_envelopes_source_blocks_and_session_boundary(self):
        scope = self.find_scope(self.listing, 'claude')
        nested = self.find_scope(self.listing, 'claude', '项目嵌套来源')
        other = self.find_scope(self.reader.listing(self.b), 'codex')
        with self.running() as request:
            result = self.success(self.get(request, scope), scope)
            self.assertEqual(result, [
                item('读取', 'return_observed', UTC_A, B),
                item('编辑', 'start_only', UTC_A),
                item('搜索', 'start_only'),
                item('命令', 'start_only'),
                item('其他', 'return_only', end=UTC_A),
                item('其他', 'unpaired', B),
                item('其他', 'ambiguous'),
                item('其他', 'ambiguous'),
                item('其他', 'return_only', end=B),
            ])
            self.assertEqual(self.success(self.get(request, nested), nested), [])
            self.assertEqual(self.success(self.get(request, other), other), [])

    def test_exact_name_allowlists_and_category_defaults_are_provider_specific(self):
        codex_names = [('read_file', '读取'), ('grep', '搜索'), ('rg', '搜索'), ('glob', '搜索'),
                       ('list_files', '搜索'), ('apply_patch', '编辑'), ('functions.apply_patch', '编辑'),
                       ('exec_command', '命令'), ('shell_command', '命令'), ('functions.exec_command', '命令'),
                       ('Read', '其他'), ('Grep', '其他')]
        claude_names = [('Read', '读取'), ('Grep', '搜索'), ('Glob', '搜索'), ('Edit', '编辑'),
                        ('Write', '编辑'), ('MultiEdit', '编辑'), ('NotebookEdit', '编辑'), ('Bash', '命令'),
                        ('read_file', '其他'), ('bash', '其他')]
        codex(self.data['codex_root'], self.data['a'], rows=[
            call('function_call', f'key-{index}', name) for index, (name, _) in enumerate(codex_names)
        ])
        records(self.data['claude_main'], [
            {'type': 'user', 'cwd': str(self.data['a']), 'sessionId': SID},
            claude_event('assistant', [use(f'key-{index}', name) for index, (name, _) in enumerate(claude_names)]),
        ])
        self.rescan()
        claude_scope = self.find_scope(self.listing, 'claude')
        with self.running() as request:
            self.assertEqual(self.success(self.get(request)), [item(category, 'start_only') for _, category in codex_names])
            self.assertEqual(self.success(self.get(request, claude_scope), claude_scope),
                             [item(category, 'start_only') for _, category in claude_names])

    def test_child_source_is_verified_empty_even_if_marker_arrives_late(self):
        rows = [call('function_call', f'earlier-{i}', 'exec_command', A) for i in range(2049)]
        rows.append({'type': 'session_meta', 'payload': {'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'private'}}}}})
        codex(self.data['codex_root'], self.data['a'], rows=rows)
        self.rescan()
        with self.running() as request:
            self.assertEqual(self.success(self.get(request)), [])

    def test_latest_display_limit_and_2048_final_event_budget(self):
        recent = self.find_scope(self.listing, 'codex', '隔离来源 reviewer')
        with self.running() as request:
            result = self.success(self.get(request, recent), recent, partial=True)
            self.assertEqual(result, [item('命令', 'start_only', UTC_A)] * 100)
            public = [call('function_call', 'same-id', 'exec_command', A) for _ in range(2048)]
            early = [call('function_call_output', f'private-{i}') for i in range(2050)]
            private = [call('function_call', f'private-{i}', 'mcp__synthetic') for i in range(2050)]
            codex(self.data['codex_root'], self.data['a'], rows=early + public + private)
            self.rescan()
            self.assertEqual(self.success(self.get(request)), [item('其他', 'ambiguous')])
            codex(self.data['codex_root'], self.data['a'], rows=early + public + private + public[:1])
            self.rescan()
            self.refused(self.get(request), 'NATIVE_ACTIVITY_BUDGET')

    def test_query_auth_workspace_revision_and_disabled(self):
        path = 'native-session-activity?' + urlencode(self.scope)
        with self.running() as request:
            for query in ('native-session-activity', path + '&', path + '&extra=1',
                          path + '&workspace=%FF', path + '&handle=' + self.scope['handle'], path + '&x=' + 'a' * 1024):
                self.refused(request(query), 'NATIVE_ACTIVITY_INVALID', 400)
            self.assertEqual(request(path, headers={'Authorization': 'Bearer invalid'})[0], 401)
            self.assertEqual(request(path, headers={'Host': 'invalid'})[0], 403)
            self.assertEqual(request(path, headers={'Origin': 'https://outside.invalid'})[0], 403)
            self.assertEqual(request(path, method='POST', body=b'{}', headers={'Content-Type': 'application/json'})[0], 404)
            other = self.find_scope(self.reader.listing(self.b), 'codex')
            self.refused(self.get(request, {**other, 'handle': self.scope['handle']}), 'NATIVE_SESSION_UNAVAILABLE', 404)
            self.refused(self.get(request, {**self.scope, 'workspace': self.b}), 'NATIVE_SESSION_UNAVAILABLE', 404)
            self.reader.snapshots[self.a] = replace(self.reader.snapshots[self.a], expires=time.monotonic() - 1)
            self.refused(self.get(request), 'NATIVE_REVISION_STALE')
        with self.running(enabled=False) as request:
            self.refused(self.get(request), 'NATIVE_HISTORY_DISABLED', 404)

    def test_four_shared_read_slots_and_cooperative_budget(self):
        entered, release = Barrier(5), Event()
        project = activity.project_activity

        def hold(*args):
            entered.wait(5)
            if not release.wait(6):
                raise AssertionError('fixture did not release reads')
            return project(*args)

        with self.running() as request, ThreadPoolExecutor(max_workers=4) as pool:
            with patch.object(activity, 'project_activity', side_effect=hold):
                pending = [pool.submit(self.get, request) for _ in range(4)]
                try:
                    entered.wait(5)
                    self.refused(self.get(request), 'NATIVE_SCAN_BUSY')
                    self.refused(request('native-session?' + urlencode(self.scope)), 'NATIVE_SCAN_BUSY')
                finally:
                    release.set()
                for response in pending:
                    self.assertEqual(response.result()[0], 200)
            for module, name, limit in ((activity, 'FILE_BYTES', 1), (history, 'LINE_BYTES', 10),
                                        (activity, 'MAX_BODY_BYTES', 100)):
                with patch.object(module, name, limit):
                    self.refused(self.get(request), 'NATIVE_ACTIVITY_BUDGET')
            original = activity.activity_blocks
            clock = SimpleNamespace(now=time.monotonic())

            def timeout(*args):
                clock.now += activity.READ_SECONDS + 1
                yield from original(*args)

            with (patch.object(activity, 'time', SimpleNamespace(monotonic=lambda: clock.now)),
                  patch.object(activity, 'activity_blocks', side_effect=timeout)):
                self.refused(self.get(request), 'NATIVE_ACTIVITY_BUDGET')

    def test_full_raw_digest_outside_display_and_real_append_during_projection(self):
        scope = self.find_scope(self.listing, 'codex', '隔离来源 reviewer')
        original = self.data['many'].read_bytes()
        ref = self.reader.snapshots[self.a].reads[scope['handle']].ref
        with self.running() as request:
            changed = original.replace(b'count-0', b'count-X', 1)
            self.assertNotEqual(original, changed)
            self.data['many'].write_bytes(changed)
            with (patch.object(activity, 'stamp', return_value=ref.stamp),
                  patch.object(activity, 'require_file'), patch.object(history, 'require_file')):
                self.refused(self.get(request, scope), 'NATIVE_REVISION_STALE')
            self.data['many'].write_bytes(original)
            self.rescan()
            scope = self.find_scope(self.listing, 'codex', '隔离来源 reviewer')
            blocks = activity.activity_blocks
            changed_during_read = False

            def mutate(*args):
                nonlocal changed_during_read
                if not changed_during_read:
                    with self.data['many'].open('ab') as stream:
                        stream.write(b'\n')
                    changed_during_read = True
                yield from blocks(*args)

            with patch.object(activity, 'activity_blocks', side_effect=mutate):
                self.refused(self.get(request, scope), 'NATIVE_REVISION_STALE')
            self.assertTrue(changed_during_read)

    def test_durable_database_change_during_read_refuses(self):
        project = activity.project_activity

        def unrelated_change(*args):
            result = project(*args)
            self.store.db.put('run', 'synthetic-concurrent-change', {'workspace': self.b})
            return result

        with self.running() as request, patch.object(activity, 'project_activity', side_effect=unrelated_change):
            self.refused(self.get(request), 'NATIVE_REVISION_STALE')
        self.assertEqual(self.store.db.get('run', 'synthetic-concurrent-change'), {'workspace': self.b})

    def test_read_preserves_native_db_seed_and_lease_and_refuses_busy_missing_third_file(self):
        self.store.save(self.a, None, None, {'version': 1, 'name': '合成草稿', 'nodes': [], 'edges': []})
        self.store.db.put('run', 'synthetic-run', {'workspace': self.a, 'state': 'UNKNOWN'})
        self.store.db.event('synthetic-run', 'synthetic-event')
        NativeSessionViews(self.reader).change(action_parameters(json.dumps({
            **self.scope, 'action': 'rename', 'alias': '合成别名', 'expected_view_revision': None,
        }).encode()))
        lease_base = self.root / 'lease-fixture'
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
        native_roots = self.data['projects'], self.data['home'], self.data['isolated'], lease_root
        native_before = snapshot(*native_roots)
        state_files = lambda: {key: value for key, value in snapshot(self.store.state).items() if key[1] != '.'}
        state_before = state_files()
        main = self.store.state / 'v2.sqlite'
        with self.running() as request:
            self.assertEqual(len(self.success(self.get(request))), 12)
            self.assertEqual(state_files(), state_before)
            writer = sqlite3.connect(main, timeout=0)
            try:
                writer.execute('BEGIN IMMEDIATE')
                wal = self.store.state / 'v2.sqlite-wal'
                self.assertTrue(wal.exists())
                self.refused(self.get(request), 'NATIVE_REVISION_STALE')
                self.assertTrue(wal.exists())
            finally:
                writer.close()
            saved = self.root / 'missing-main.fixture'
            main.rename(saved)
            self.refused(self.get(request), 'NATIVE_REVISION_STALE')
            self.assertFalse(main.exists())
            saved.rename(main)
            for name in ('unexpected-file', 'v2.sqlite-wal'):
                foreign = self.store.state / name
                foreign.write_bytes(b'unchanged fixture')
                self.refused(self.get(request), 'NATIVE_REVISION_STALE')
                self.assertEqual(foreign.read_bytes(), b'unchanged fixture')
                foreign.unlink()
        self.assertEqual(state_files(), state_before)
        self.assertEqual(snapshot(*native_roots), native_before)
