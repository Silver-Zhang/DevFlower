"""Independent raw HTTP and SQLite proofs for the V2 metadata-only archive."""
from contextlib import closing, contextmanager
import hashlib
from http.client import HTTPConnection, HTTPResponse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from orchestration.common import Blocked
from v2 import run_summary as summary
from v2.native_history_config import startup_seed
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.store import DraftStore
from v2_native_sessions_fixture import snapshot


FORMAT = 'devflower.v2.run-summary'
RUN = 'a' * 32
OTHER = 'b' * 32
PRIVATE = 'PRIVATE_RUN_SUMMARY_FIXTURE'
ROW_LIMIT = 2 * 1024 * 1024
UNAVAILABLE = (404, 'RUN_SUMMARY_UNAVAILABLE', '找不到当前工作空间中的可导出运行摘要。')
INVALID = (409, 'RUN_SUMMARY_RECORD_INVALID', '运行记录或冻结定义无法核验。')
NOT_READY = (409, 'RUN_SUMMARY_NOT_READY', '该运行尚未记录为可导出的状态。')
STALE = (409, 'RUN_SUMMARY_SCOPE_STALE', '当前工作空间暂不能核验，请重新打开后再试。')
BUDGET = (409, 'RUN_SUMMARY_BUDGET', '运行记录超过摘要读取限制。')
REQUEST = (400, 'RUN_SUMMARY_REQUEST_INVALID', '运行摘要请求无效。')


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def graph():
    return {
        'version': 1, 'name': PRIVATE,
        'nodes': [{'id': key, 'name': PRIVATE, 'position': {'x': 0, 'y': i}, 'round_prompt': PRIVATE}
                  for i, key in enumerate(('left', 'right'))],
        'edges': [{'id': 'edge', 'source': 'left', 'target': 'right'}],
    }


class RunSummaryTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='v2-run-summary-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.projects = self.root / 'projects'
        self.a_path, self.b_path = self.projects / 'A', self.projects / 'B'
        self.a_path.mkdir(parents=True)
        self.b_path.mkdir()
        self.store = DraftStore(self.root / 'state', [self.projects])
        self.state = self.store.state
        self.database = self.state / 'v2.sqlite'
        self.a = self.store.open_workspace(str(self.a_path))['id']
        self.b = self.store.open_workspace(str(self.b_path))['id']
        draft = self.store.save(self.a, None, None, graph())
        self.definition = {'workspace': self.a, 'draft_document': draft['graph'], 'task': PRIVATE}
        self.definition_id = self.put_definition(self.definition)
        self.run = dict(id=RUN, definition_id=self.definition_id, workspace=self.a, state='COMPLETED',
                        created_at=1700000000.1259, task=PRIVATE, launch_intent=PRIVATE,
                        worker={'host': PRIVATE, 'pid': 123}, observed_session=PRIVATE)
        self.store.db.put('run', RUN, self.run)
        self.store.db.put('activation', 'fixture', {'run': RUN, 'actual_request': PRIVATE})
        self.store.db.put('native-step', 'fixture', {'run': RUN, 'data': PRIVATE})
        self.store.db.event(RUN, 'fixture-event', content=PRIVATE)
        startup_seed(self.state)

    def put_definition(self, value):
        key = sha(compact(value))
        self.store.db.put('definition', key, dict(value, id=key))
        return key

    def write_raw(self, kind, key, raw):
        with self.store.db.tx() as db:
            db.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (raw, kind, key))

    def durable_state(self):
        data = snapshot(self.state)
        mode, _, _ = data.pop((str(self.state), '.'))
        return self.state.stat().st_ino, mode, data

    @contextmanager
    def service(self, runtime=None):
        server, token = create_server(self.store, runtime=runtime)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def path(self, workspace=None, run=RUN):
        return f'/api/v2/run-summary?workspace={workspace or self.a}&run={run}'

    def get(self, endpoint, path=None, headers=None):
        port, token = endpoint
        with closing(HTTPConnection('127.0.0.1', port, timeout=8)) as conn:
            conn.request('GET', path or self.path(), headers=dict({'Authorization': 'Bearer ' + token}, **(headers or {})))
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()

    def assert_error(self, response, expected):
        status, code, message = expected
        self.assertEqual((response[0], json.loads(response[2])), (status, {'code': code, 'error': message}))

    def expected(self, state='COMPLETED', ms=1700000000125, run=RUN, definition=None, agents=2, edges=1):
        fingerprint = sha(f'{FORMAT}\0{run}\0{definition or self.definition_id}'.encode('ascii'))
        body = dict(agent_count=agents, created_at_ms=ms, edge_count=edges,
                    source_fingerprint=fingerprint, source_recorded_state=state)
        return compact(dict(authority=False, format=FORMAT, snapshot=body, snapshot_sha256=sha(compact(body)), version=1)) + b'\n'

    def test_four_recorded_states_exact_bytes_no_runtime_and_durable_state_unchanged(self):
        with self.service() as endpoint:
            for state in ('COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'):
                with self.subTest(state=state):
                    self.store.db.put('run', RUN, dict(self.run, state=state, persisted_state='RUNNING'))
                    before = self.durable_state()
                    status, headers, raw = self.get(endpoint)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertEqual(raw, self.expected(state))
                    self.assertNotIn(PRIVATE.encode(), raw)
                    self.assertNotIn(RUN.encode(), raw)
                    self.assertNotIn(self.definition_id.encode(), raw)
                    self.assertNotIn(self.a.encode(), raw)
                    self.assertEqual(self.durable_state(), before)

    def test_current_runtime_projection_does_not_replace_recorded_source_state(self):
        # The unrelated leakage sentinels have no valid runtime shape.
        with self.store.db.tx() as db:
            db.execute("DELETE FROM records WHERE kind IN ('activation','native-step')")
        with patch('v2.runtime.CodexProvider', side_effect=Blocked('synthetic fixture')):
            runtime = V2Runtime(self.store)
        with self.service(runtime) as endpoint:
            detail = self.get(endpoint, f'/api/v2/run?workspace={self.a}&id={RUN}')
            self.assertEqual(detail[0], 200)
            self.assertEqual(json.loads(detail[2])['state'], 'RECOVERY_REQUIRED')
            self.assertEqual(json.loads(detail[2])['run']['persisted_state'], 'COMPLETED')
            with patch.object(runtime, 'snapshot', side_effect=AssertionError('archive uses its own source reader')):
                self.assertEqual(self.get(endpoint)[2], self.expected())

    def test_maximum_frozen_graph_counts_and_overflow(self):
        keys = [f'n{i}' for i in range(64)]
        nodes = [dict(id=key, name=PRIVATE, position={'x': 0, 'y': i}) for i, key in enumerate(keys)]
        edges = [dict(id=f'e{i}', source=keys[i % 64], target=keys[(i + 1 + i // 64) % 64])
                 for i in range(129)]
        value = dict(workspace=self.a, draft_document=dict(version=1, name=PRIVATE, nodes=nodes, edges=edges[:128]))
        with self.service() as endpoint:
            key = self.put_definition(value)
            self.store.db.put('run', RUN, dict(self.run, definition_id=key))
            self.assertEqual(self.get(endpoint)[2], self.expected(definition=key, agents=64, edges=128))
            for graph_change in (dict(nodes=nodes + [dict(id='more', name='more', position={'x': 0, 'y': 0})]),
                                 dict(edges=edges)):
                key = self.put_definition(dict(value, draft_document=dict(value['draft_document'], **graph_change)))
                self.store.db.put('run', RUN, dict(self.run, definition_id=key))
                self.assert_error(self.get(endpoint), INVALID)

    def test_request_and_existing_authentication(self):
        with self.service() as endpoint:
            base = '/api/v2/run-summary?'
            bad = ('', 'workspace=' + self.a, f'workspace={self.a}&run={RUN}&run={RUN}',
                   f'workspace={self.a}&run={RUN}&path=elsewhere', f'workspace={self.a}&run=%00',
                   f'workspace={self.a}&run={RUN.upper()}', f'workspace={self.a}&run={RUN}&' + 'x' * 1024)
            for query in bad:
                with self.subTest(query=query[:100]):
                    self.assert_error(self.get(endpoint, base + query), REQUEST)
            self.assertEqual(self.get(endpoint, headers={'Authorization': 'Bearer wrong'})[0], 401)
            self.assertEqual(self.get(endpoint, headers={'Origin': 'http://other.invalid'})[0], 403)
            self.assertEqual(self.get(endpoint, headers={'Host': 'other.invalid'})[0], 403)
            port, token = endpoint
            for framing in ('Content-Length: 1\r\n', 'Transfer-Encoding: chunked\r\n',
                            'Content-Length: 0\r\nContent-Length: 0\r\n'):
                request = (f'GET {self.path()} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                           f'Authorization: Bearer {token}\r\n{framing}\r\n').encode()
                with closing(socket.create_connection(('127.0.0.1', port), timeout=8)) as sock:
                    sock.sendall(request)
                    with closing(HTTPResponse(sock)) as response:
                        response.begin()
                        self.assert_error((response.status, {}, response.read()), REQUEST)

    def test_source_state_time_and_zero_count_boundaries(self):
        with self.service() as endpoint:
            for state in ('QUEUED', 'RUNNING', 'UNKNOWN', None, ['FAILED']):
                self.store.db.put('run', RUN, dict(self.run, state=state))
                self.assert_error(self.get(endpoint), NOT_READY)
            for value, expected in ((None, None), ('12', None), (True, None), (-1, None),
                                    (0, 0), (253402300799.999, 253402300799999),
                                    (253402300800, None), (10**100, None)):
                with self.subTest(created=value):
                    self.store.db.put('run', RUN, dict(self.run, created_at=value))
                    self.assertEqual(self.get(endpoint)[2], self.expected(ms=expected))
            zero = self.put_definition(dict(workspace=self.a, draft_document={
                'version': 1, 'name': 'fixture', 'nodes': [], 'edges': [],
            }))
            self.store.db.put('run', RUN, dict(self.run, definition_id=zero))
            self.assertEqual(self.get(endpoint)[2], self.expected(definition=zero, agents=0, edges=0))

    def test_indistinguishable_missing_cross_scope_and_association(self):
        with self.service() as endpoint:
            self.assert_error(self.get(endpoint, self.path(self.b)), UNAVAILABLE)
            self.assert_error(self.get(endpoint, self.path(run=OTHER)), UNAVAILABLE)
            for value in (dict(self.run, id=OTHER), dict(self.run, definition_id='f' * 64),
                          dict(self.run, definition_id=None),
                          dict(self.run, definition_id=self.put_definition(dict(self.definition, workspace=self.b)))):
                self.store.db.put('run', RUN, value)
                self.assert_error(self.get(endpoint), UNAVAILABLE)

    def test_frozen_digest_invalid_document_and_strict_source_json(self):
        with self.service() as endpoint:
            self.store.db.put('definition', self.definition_id, dict(self.definition, id=self.definition_id, task='changed'))
            self.assert_error(self.get(endpoint), INVALID)
            bad = dict(self.definition, draft_document=dict(self.definition['draft_document'], nodes=[None]))
            invalid_definition = self.put_definition(bad)
            self.store.db.put('run', RUN, dict(self.run, definition_id=invalid_definition))
            self.assert_error(self.get(endpoint), INVALID)
            self.store.db.put('definition', self.definition_id, dict(self.definition, id=self.definition_id))
            valid = compact(self.run)
            for tail in (b'"state":"FAILED"', b'"extra":NaN', b'"extra":Infinity', b'"extra":1e309',
                         b'"extra":"\xff"', b'"extra":"\\ud800"', b'"extra":' + b'[' * 32 + b'0' + b']' * 32):
                with self.subTest(tail=tail[:25]):
                    self.write_raw('run', RUN, valid[:-1] + b',' + tail + b'}')
                    self.assert_error(self.get(endpoint), INVALID)

    def test_only_primary_key_selects_query_only_and_pre_read_blob_budget(self):
        record = compact(self.run)
        self.write_raw('run', RUN, record.ljust(ROW_LIMIT, b' '))
        definitions = compact(dict(self.definition, id=self.definition_id))
        self.write_raw('definition', self.definition_id, definitions.ljust(ROW_LIMIT, b' '))
        opens, reads, traces = [], [], []
        connect = sqlite3.connect

        class Tracking(sqlite3.Connection):
            def blobopen(self, *args, **kwargs):
                self_case.assertEqual(kwargs, {'readonly': True})
                blob = super().blobopen(*args, **kwargs)
                opens.append(len(blob))

                class TrackBlob:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_):
                        blob.close()

                    def __len__(self):
                        return len(blob)

                    def read(self, length):
                        reads.append(length)
                        return blob.read(length)

                return TrackBlob()

        self_case = self

        def tracked(*args, **kwargs):
            self.assertTrue(args[0].endswith('?mode=rw'))
            self.assertEqual(kwargs, {'uri': True, 'timeout': 0})
            conn = connect(*args, **kwargs, factory=Tracking)
            conn.set_trace_callback(traces.append)
            return conn

        with self.service() as endpoint:
            with patch('v2.workspace_select.sqlite3.connect', side_effect=tracked):
                self.assertEqual(self.get(endpoint)[2], self.expected())
            self.assertEqual(opens, [ROW_LIMIT, ROW_LIMIT])
            self.assertEqual(reads, [ROW_LIMIT, ROW_LIMIT])
            self.assertEqual(traces.count('PRAGMA query_only=ON'), 2)
            self.assertEqual(traces.count('PRAGMA query_only'), 2)
            self.assertEqual(traces.count('BEGIN'), 1)
            selects = [line for line in traces if line.startswith('SELECT')]
            self.assertEqual(selects, [
                f"SELECT data FROM records WHERE kind='workspace' AND id='{self.a}'",
                f"SELECT rowid,typeof(data) FROM records WHERE kind='run' AND id='{RUN}'",
                f"SELECT rowid,typeof(data) FROM records WHERE kind='definition' AND id='{self.definition_id}'",
            ])
            self.write_raw('definition', self.definition_id, definitions.ljust(ROW_LIMIT + 1, b' '))
            opens.clear()
            reads.clear()
            with patch('v2.workspace_select.sqlite3.connect', side_effect=tracked):
                self.assert_error(self.get(endpoint), BUDGET)
            self.assertEqual((opens, reads), ([ROW_LIMIT, ROW_LIMIT + 1], [ROW_LIMIT]))
            self.write_raw('run', RUN, record.ljust(ROW_LIMIT + 1, b' '))
            opens.clear()
            reads.clear()
            with patch('v2.workspace_select.sqlite3.connect', side_effect=tracked):
                self.assert_error(self.get(endpoint), BUDGET)
            self.assertEqual((opens, reads), ([ROW_LIMIT + 1], []))

    def test_deadline_rejects_before_or_during_source_read(self):
        original = summary._read_record

        def delayed(*args):
            import time
            time.sleep(0.08)
            return original(*args)

        with self.service() as endpoint:
            with patch.object(summary, 'READ_SECONDS', 0):
                self.assert_error(self.get(endpoint), BUDGET)
            with patch.object(summary, 'READ_SECONDS', 0.05), patch.object(summary, '_read_record', side_effect=delayed):
                self.assert_error(self.get(endpoint), BUDGET)
            self.assertEqual(self.get(endpoint)[2], self.expected())

    def test_missing_main_sidecars_unknown_name_and_active_connection_preserved(self):
        with self.service() as endpoint:
            for name in ('v2.sqlite-wal', 'v2.sqlite-shm', 'v2.sqlite-journal', 'fixture-third-file'):
                path = self.state / name
                path.write_bytes(b'synthetic preserve')
                self.assert_error(self.get(endpoint), STALE)
                self.assertEqual(path.read_bytes(), b'synthetic preserve')
                path.unlink()
            with closing(sqlite3.connect(self.database)) as db:
                db.execute('BEGIN')
                db.execute('SELECT count(*) FROM records').fetchone()
                self.assertTrue((self.state / 'v2.sqlite-shm').exists())
                self.assert_error(self.get(endpoint), STALE)
            self.database.rename(self.root / 'parked')
            self.assert_error(self.get(endpoint), STALE)
            self.assertFalse(self.database.exists())

    def test_same_byte_inode_replacement_after_scope_or_serialization(self):
        scope = self.store.workspace
        archive = summary._archive

        def replace():
            new_file = self.root / 'replacement'
            shutil.copy2(self.database, new_file)
            self.assertNotEqual(self.database.stat().st_ino, new_file.stat().st_ino)
            os.replace(new_file, self.database)

        def after_scope(*args, **kwargs):
            result = scope(*args, **kwargs)
            replace()
            return result

        def after_archive(*args, **kwargs):
            result = archive(*args, **kwargs)
            replace()
            return result

        with self.service() as endpoint:
            original = self.database.read_bytes()
            with patch.object(self.store, 'workspace', side_effect=after_scope):
                self.assert_error(self.get(endpoint), STALE)
            with patch.object(summary, '_archive', side_effect=after_archive):
                self.assert_error(self.get(endpoint), STALE)
            self.assertEqual(self.database.read_bytes(), original)
            self.assertEqual(self.get(endpoint)[2], self.expected())
