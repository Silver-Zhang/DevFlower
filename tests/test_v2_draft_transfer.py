"""Raw loopback draft archive format, budget and same-database proof tests."""
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection, HTTPResponse
import json
import math
from pathlib import Path
import random
import shutil
import socket
import sqlite3
import struct
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from v2 import draft_transfer as transfer
from v2.native_history_config import startup_seed
from v2.server import create_server
from v2.store import DraftStore
from v2_native_sessions_fixture import snapshot

FORMAT = 'devflower.v2.workflow-draft'
REQUEST = 'DRAFT_TRANSFER_REQUEST_INVALID'
INVALID = 'DRAFT_TRANSFER_FORMAT_INVALID'
DIGEST = 'DRAFT_TRANSFER_DIGEST_MISMATCH'
DOCUMENT = 'DRAFT_TRANSFER_DOCUMENT_INVALID'
STALE = 'DRAFT_TRANSFER_SCOPE_STALE'


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def archive(document):
    return compact(dict(format=FORMAT, version=1, document=document,
                        document_sha256=hashlib.sha256(compact(document)).hexdigest())) + b'\n'


def graph(name='合成草稿', *, nodes=None, edges=None):
    return {'version': 1, 'name': name, 'nodes': nodes or [], 'edges': edges or []}


def node(i):
    return {'id': f'n{i}', 'name': f'节点 {i}', 'position': {'x': 0, 'y': i}, 'initial_prompt': ''}


def exact_graph_size(size):
    document = graph(nodes=[node(i) for i in range(22)])
    remaining = size - len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode())
    for record in document['nodes']:
        count = min(remaining, 20000)
        record['initial_prompt'] = 'x' * count
        remaining -= count
    assert remaining == 0
    assert len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode()) == size
    return document


class DraftTransferTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='v2-draft-transfer-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.projects = self.root / 'projects'
        self.a_path, self.b_path = self.projects / 'A', self.projects / 'B'
        self.a_path.mkdir(parents=True)
        self.b_path.mkdir()
        self.store = DraftStore(self.root / 'state', [self.projects])
        self.a = self.store.open_workspace(str(self.a_path))['id']
        self.b = self.store.open_workspace(str(self.b_path))['id']
        self.source = self.store.save(self.a, None, None, graph('原草稿'))
        self.store.db.put('run', 'fixture-run', {'workspace': self.a, 'state': 'FAILED'})
        self.store.db.event('fixture-run', 'fixture-event')
        self.store.db.put('native-session-view', 'fixture-view', {'alias': '保持原样'})
        startup_seed(self.store.state)
        self.raw = archive(graph())

    @contextmanager
    def running(self):
        server, token = create_server(self.store)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
        thread.start()
        try:
            yield server.server_port, token
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)

    def path(self, scope=None):
        return '/api/v2/drafts/validate?workspace=' + (scope or self.b)

    def post(self, service, raw=None, *, path=None, headers=None):
        port, token = service
        client = HTTPConnection('127.0.0.1', port, timeout=8)
        try:
            client.request('POST', path or self.path(), self.raw if raw is None else raw,
                           {'Content-Type': 'application/json; charset=utf-8',
                            'Authorization': 'Bearer ' + token, **(headers or {})})
            response = client.getresponse()
            wire = response.read()
            self.assertEqual(int(response.getheader('Content-Length')), len(wire))
            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
            return response.status, json.loads(wire), wire
        finally:
            client.close()

    def wire(self, service, headers, body=b''):
        port, token = service
        text = [f'POST {self.path()} HTTP/1.0', f'Host: 127.0.0.1:{port}', f'Authorization: Bearer {token}', *headers]
        with socket.create_connection(('127.0.0.1', port), timeout=8) as conn:
            conn.sendall(('\r\n'.join(text) + '\r\n\r\n').encode('ascii') + body)
            conn.shutdown(socket.SHUT_WR)
            response = HTTPResponse(conn)
            try:
                response.begin()
                wire = response.read()
                return response.status, json.loads(wire), wire
            finally:
                response.close()

    def error(self, reply, code):
        self.assertEqual((reply[0], reply[1].get('code')), (409 if code == STALE else 400, code))
        self.assertEqual(set(reply[1]), {'code', 'error'})
        for private in (str(self.root), '原草稿', 'PRIVATE_SENTINEL', self.a):
            self.assertNotIn(private.encode(), reply[2])

    def valid(self, reply, body, scope=None):
        self.assertEqual(reply[0], 200, reply[1])
        self.assertEqual(reply[1], {'workspace': scope or self.b, 'valid': True,
                                    'document_sha256': json.loads(body)['document_sha256']})

    def state_files(self):
        return {key: value for key, value in snapshot(self.store.state).items() if key[1] != '.'}

    def test_empty_old_and_unconfigured_graphs_use_existing_validation_without_writes(self):
        documents = [graph('空白'), graph('旧版本无 viewport', nodes=[node(0)]),
                     graph('Unicode 中文 🙂 <b>\n"\\', nodes=[dict(node(0), initial_prompt='换行\n制表\t\x01 PRIVATE_SENTINEL')])]
        before, sources = self.state_files(), snapshot(self.projects)
        with self.running() as service:
            for document in documents:
                body = archive(document)
                response = self.post(service, body)
                self.valid(response, body)
                self.assertNotIn(b'PRIVATE_SENTINEL', response[2])
            self.assertEqual(self.state_files(), before)
            self.assertEqual(snapshot(self.projects), sources)
            self.assertEqual(self.store.drafts(self.a), [self.source])
            self.assertEqual(self.store.drafts(self.b), [])
            self.assertEqual(self.store.db.get('run', 'fixture-run')['state'], 'FAILED')
        self.assertEqual(self.state_files(), before)

    def test_format_rejects_duplicate_keys_encoding_order_and_noncanonical_numbers(self):
        raw = self.raw
        document = archive(graph(nodes=[node(0)]))
        invalid = [
            b'', b'null\n', b'{}\n', raw[:-1], raw + b'\n', raw[:-1] + b'\r\n', b' ' + raw,
            b'\xef\xbb\xbf' + raw, b'\xff' + raw, raw.replace(b',"version":1}\n', b',"version":2}\n'),
            raw.replace(b',"version":1}\n', b',"version":true}\n'),
            raw.replace(b',"version":1}\n', b',"version":1,"version":1}\n'),
            raw.replace(b'"name":', b'"name":"duplicate","name":', 1), raw.replace(b'"name":', b'"name": ', 1),
            raw.replace('合成草稿'.encode(), b'\\u0000'), raw.replace('合成草稿'.encode(), b'\\ud800'),
            raw.replace('合成草稿'.encode(), b'\\udfff'), raw.replace('合成草稿'.encode(), b'\x00'),
            raw.replace(FORMAT.encode(), b'legacy.workflow.archive'), raw.replace(b'"format":', b'"extra":1,"format":'),
            raw.replace('"edges":[],"name":"合成草稿"'.encode(), '"name":"合成草稿","edges":[]'.encode()),
            document.replace(b'"x":0', b'"x":-0'),
            document.replace(b'"x":0', b'"x":0.0'), document.replace(b'"x":0', b'"x":0e0'),
            document.replace(b'"x":0', b'"x":NaN'), document.replace(b'"x":0', b'"x":Infinity'),
            document.replace(b'"x":0', b'"x":' + b'9' * 400), document.replace(b'"x":0', b'"x":' + b'9' * 5000),
            b'{"document":' + b'[' * 2000 + b'null' + b']' * 2000
             + b',"document_sha256":"' + b'0' * 64 + b'","format":"' + FORMAT.encode() + b'","version":1}\n',
        ]
        with self.running() as service:
            self.error(self.post(service, invalid.pop(0)), REQUEST)
            for index, raw in enumerate(invalid):
                with self.subTest(index=index):
                    self.error(self.post(service, raw), INVALID)
            reordered = json.dumps(json.loads(self.raw), ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
            # Start with a known noncanonical insertion order rather than sorting.
            item = json.loads(reordered)
            reordered = json.dumps(dict(version=item.pop('version'), **item),
                                   ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
            self.error(self.post(service, reordered), INVALID)
            wrong = self.raw.replace(json.loads(self.raw)['document_sha256'].encode(), b'0' * 64)
            self.error(self.post(service, wrong), DIGEST)

    def test_exact_transport_and_old_graph_budgets(self):
        maximum = archive(exact_graph_size(400000))
        too_large_graph = archive(exact_graph_size(400001))
        self.assertLess(len(too_large_graph), 420000)
        with self.running() as service:
            self.valid(self.post(service, maximum), maximum)
            self.error(self.post(service, too_large_graph), DOCUMENT)
            edge_nodes = [node(i) for i in range(17)]
            edges = [{'id': f'e{i}', 'source': f'n{left}', 'target': f'n{right}'}
                     for i, (left, right) in enumerate((a, b) for a in range(17) for b in range(a + 1, 17))]
            many_edges = archive(graph(nodes=edge_nodes, edges=edges[:128]))
            self.valid(self.post(service, many_edges), many_edges)
            self.error(self.post(service, archive(graph(nodes=edge_nodes, edges=edges[:129]))), DOCUMENT)
            nodes = [node(i) for i in range(65)]
            most_nodes = archive(graph(nodes=nodes[:64]))
            self.valid(self.post(service, most_nodes), most_nodes)
            self.error(self.post(service, archive(graph(nodes=nodes))), DOCUMENT)
            self.error(self.post(service, self.raw + b' ' * (420000 - len(self.raw))), INVALID)
            self.error(self.post(service, self.raw + b' ' * (420001 - len(self.raw))), REQUEST)

    def test_format_digest_and_document_errors_are_distinct(self):
        unsupported = graph()
        unsupported['execution_authority'] = 'PRIVATE_SENTINEL'
        with self.running() as service:
            self.error(self.post(service, archive(unsupported)), DOCUMENT)
            self.error(self.post(service, archive(None)), DOCUMENT)
            self.error(self.post(service, archive(graph('x' * 161))), DOCUMENT)
            self.error(self.post(service, archive(graph(nodes=[dict(node(0), permission='write')]))), DOCUMENT)

    def test_auth_query_duplicate_lengths_and_incomplete_body(self):
        good_type = 'Content-Type: application/json'
        length = f'Content-Length: {len(self.raw)}'
        with self.running() as service:
            self.assertEqual(self.post(service, headers={'Authorization': 'Bearer bad'})[0], 401)
            self.assertEqual(self.post(service, headers={'Host': 'outside.invalid'})[0], 403)
            self.assertEqual(self.post(service, headers={'Origin': 'https://outside.invalid'})[0], 403)
            for path in ('/api/v2/drafts/validate', self.path() + '&x=1', self.path() + '&',
                         self.path() + '&workspace=' + self.b, self.path() + '&p=' + 'a' * 1024,
                         '/api/v2/drafts/validate?workspace=%FF'):
                self.error(self.post(service, path=path), REQUEST)
            for headers in ([good_type], [good_type, length, length], [good_type, 'Content-Length: +5'],
                            [good_type, 'Content-Length: -1'], [good_type, 'Content-Length: 1.5'],
                            [good_type, 'Content-Length: 1,1'], [good_type, length, 'Transfer-Encoding: chunked'],
                            [good_type, length, 'Transfer-Encoding:'], [good_type, 'Content-Length: 0'],
                            [length], ['Content-Type: text/plain', length]):
                self.error(self.wire(service, headers), REQUEST)
            self.error(self.wire(service, [good_type, length], self.raw[:-1]), REQUEST)
            self.valid(self.wire(service, [good_type, length], self.raw), self.raw)

    def test_scope_no_create_busy_sidecars_unknown_and_workspace_replacement(self):
        original = self.state_files()
        state = self.store.state
        main = state / 'v2.sqlite'
        with self.running() as service:
            self.error(self.post(service, path=self.path('0' * 64)), STALE)
            active = sqlite3.connect(main, timeout=0)
            try:
                active.execute('BEGIN IMMEDIATE')
                wal = state / 'v2.sqlite-wal'
                self.assertTrue(wal.exists())
                self.error(self.post(service), STALE)
                self.assertTrue(wal.exists())
            finally:
                active.close()
            moved = self.root / 'old.sqlite'
            main.rename(moved)
            self.error(self.post(service), STALE)
            self.assertFalse(main.exists())
            moved.rename(main)
            for name in ('v2.sqlite-shm', 'v2.sqlite-wal', 'third.fixture'):
                foreign = state / name
                foreign.write_bytes(b'untouched fixture')
                self.error(self.post(service), STALE)
                self.assertEqual(foreign.read_bytes(), b'untouched fixture')
                foreign.unlink()
            self.b_path.rename(self.projects / 'old-B')
            self.b_path.mkdir()
            self.error(self.post(service), STALE)
        self.assertEqual(self.state_files(), original)

    def test_post_lookup_proof_rejects_same_contents_different_database_inode(self):
        main = self.store.state / 'v2.sqlite'
        backup = self.root / 'replaced.sqlite'
        lookup = self.store.workspace

        def replace_after_lookup(key, *, existing_only):
            self.assertTrue(existing_only)
            row = lookup(key, existing_only=existing_only)
            main.rename(backup)
            shutil.copy2(backup, main)
            return row

        with self.running() as service, patch.object(self.store, 'workspace', side_effect=replace_after_lookup):
            self.error(self.post(service), STALE)
        self.assertEqual(main.read_bytes(), backup.read_bytes())

    def test_ecmascript_number_equivalence_and_real_raw_numeric_archive(self):
        rng = random.Random(4592)
        samples = [0., -0., 1e-6, 1e-7, 1e20, 1e21, 5e-324, -5e-324, math.nextafter(2.0, 0.0)]
        while len(samples) < 10009:
            candidate = struct.unpack('>d', rng.getrandbits(64).to_bytes(8, 'big'))[0]
            if math.isfinite(candidate):
                samples.append(candidate)
        code = ("const a=require('node:fs').readFileSync(0);"
                "for(let i=0;i<a.length;i+=8)console.log(JSON.stringify(a.readDoubleBE(i)));")
        js = subprocess.run(['node', '-e', code], input=b''.join(struct.pack('>d', item) for item in samples),
                            check=True, stdout=subprocess.PIPE, timeout=20).stdout.decode().splitlines()
        self.assertEqual([transfer.ecmascript_number(item) for item in samples], js)
        code = """
        const {createHash}=require('node:crypto');
        const document={edges:[],name:'数字 🙂',nodes:[{id:'n1',name:'坐标',position:{x:-0,y:5e-324}}],
                        version:1,viewport:{x:1e-7,y:1e-6,zoom:1.0000000000000002}};
        const document_sha256=createHash('sha256').update(JSON.stringify(document)).digest('hex');
        console.log(JSON.stringify({document,document_sha256,format:'devflower.v2.workflow-draft',version:1}));
        """
        body = subprocess.run(['node', '-e', code], check=True, stdout=subprocess.PIPE, timeout=10).stdout
        with self.running() as service:
            self.valid(self.post(service, body), body)
            self.error(self.post(service, body.replace(b'1e-7', b'1e-07')), INVALID)
