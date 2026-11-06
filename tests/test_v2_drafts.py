"""Independent malformed fixtures, persistence/CAS races and real V2 HTTP; no Provider."""
import concurrent.futures
import copy
import http.client
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from studio.graph import Invalid
from studio.server import Conflict
from v2.documents import check_chain, validate_draft
from v2.store import DraftStore
from v2.server import create_server
from orchestration.store import lease
from v2_browser import redact_trace


def blank():
    return {'version': 1, 'name': 'fixture', 'nodes': [], 'edges': []}


def chain():
    return {'version': 1, 'name': 'independent two Agent fixture', 'nodes': [
        {'id': 'Alpha', 'name': 'A', 'position': {'x': 10, 'y': 20}, 'provider': 'codex',
         'model': 'unverified-explicit-model', 'effort': 'low', 'initial_prompt': 'init A',
         'round_prompt': 'marker A', 'output_prompt': 'text', 'permission': 'read', 'timeout': 60},
        {'id': 'Beta', 'name': 'B', 'position': {'x': 400, 'y': 20}, 'round_prompt': ''}],
        'edges': [{'id': 'edge1', 'source': 'Alpha', 'target': 'Beta'}]}


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.spaces = self.root / 'spaces'; self.spaces.mkdir()
        self.a = self.spaces / 'A'; self.b = self.spaces / 'B'; self.a.mkdir(); self.b.mkdir()
        self.store = DraftStore(self.root / 'state', [self.spaces])
        self.wa = self.store.open_workspace(str(self.a))['id']; self.wb = self.store.open_workspace(str(self.b))['id']

    def test_blank_and_partial_are_persisted_unchanged_across_restart(self):
        for doc in [blank(), chain()]:
            saved = self.store.save(self.wa, None, None, doc)
            reopened = DraftStore(self.root / 'state', [self.spaces]).draft(self.wa, saved['id'])
            self.assertEqual(reopened['graph'], doc)
            self.assertEqual({n['id'] for n in reopened['graph']['nodes']}, {n['id'] for n in doc['nodes']})
        self.assertEqual(list(self.a.iterdir()), [])  # No workspace-local second store.

    def test_rejects_foreign_ownership_and_unknown_workspace(self):
        saved = self.store.save(self.wa, None, None, blank())
        with self.assertRaises(Conflict): self.store.draft(self.wb, saved['id'])
        with self.assertRaises(Conflict): self.store.save(self.wb, saved['id'], saved['revision'], blank())
        with self.assertRaises(Invalid): self.store.save('unknown', None, None, blank())

    def test_concurrent_edit_cas_preserves_one_winner(self):
        saved = self.store.save(self.wa, None, None, blank()); barrier = threading.Barrier(2)
        def write(name):
            barrier.wait(); doc = dict(blank(), name=name)
            try: return self.store.save(self.wa, saved['id'], saved['revision'], doc)['graph']['name']
            except Conflict: return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, ['candidate-one', 'candidate-two']))
        self.assertEqual(results.count('conflict'), 1)
        self.assertIn(self.store.draft(self.wa, saved['id'])['graph']['name'], results)

    def test_launch_intent_is_unique_durable_and_payload_bound(self):
        barrier = threading.Barrier(2)
        def reserve(_):
            barrier.wait(); return self.store.reserve_launch(self.wa, 'independent-launch-key', {'draft': 'snapshot'}, 'a' * 64)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(reserve, range(2)))
        self.assertEqual(len({r['run_id'] for r in rows}), 1)
        self.assertEqual(sum(r['created'] for r in rows), 1)
        restarted = DraftStore(self.root / 'state', [self.spaces])
        self.assertEqual(restarted.reserve_launch(self.wa, 'independent-launch-key', {'draft': 'snapshot'}, 'a' * 64)['run_id'], rows[0]['run_id'])
        with self.assertRaises(Conflict): restarted.reserve_launch(self.wa, 'independent-launch-key', {'draft': 'changed'}, 'a' * 64)

    def test_workspace_folder_replacement_is_not_adopted(self):
        self.a.rename(self.spaces / 'old-A'); self.a.mkdir()
        with self.assertRaises(Conflict): self.store.workspace(self.wa)

    def test_outside_hidden_symlink_and_legacy_state_are_rejected(self):
        for path in [self.root, self.spaces / '.codex']:
            if not path.exists(): path.mkdir()
            with self.assertRaises(Invalid): self.store.open_workspace(str(path))
        alias = self.spaces / 'alias'; alias.symlink_to(self.a, target_is_directory=True)
        with self.assertRaises(Invalid): self.store.open_workspace(str(alias))
        legacy = self.root / 'legacy'; legacy.mkdir(mode=0o700); (legacy / 'workbench.sqlite').touch()
        with self.assertRaises(Invalid): DraftStore(legacy, [self.spaces])

    def test_structural_adversaries_and_size_budget(self):
        mutations = [lambda d: d.update(version=True), lambda d: d.update(authorization={'approved': True}),
                     lambda d: d['nodes'][0].update(permission='workspace-write'),
                     lambda d: d['nodes'][0].update(timeout=True),
                     lambda d: d['nodes'][0]['position'].update(x=float('nan')),
                     lambda d: d['nodes'][0].update(round_prompt='a' * 20001),
                     lambda d: d['nodes'].append(copy.deepcopy(d['nodes'][0])),
                     lambda d: d['edges'][0].update(target='missing')]
        for mutate in mutations:
            doc = chain(); mutate(doc)
            with self.subTest(doc=str(doc)[:80]), self.assertRaises(Invalid): validate_draft(doc)
        doc = chain(); doc['nodes'] *= 33
        with self.assertRaises(Invalid): validate_draft(doc)
        doc = blank()
        doc['nodes'] = [{'id': 'n' + str(i), 'name': 'large', 'position': {'x': 0, 'y': 0},
                         'initial_prompt': 'x' * 20000} for i in range(24)]
        with self.assertRaises(Invalid): validate_draft(doc)

    def test_second_service_refuses_live_state_lease_without_traceback(self):
        root = Path(__file__).resolve().parents[1]
        with lease(self.store.state / 'v2-service.lock'):
            result = subprocess.run([sys.executable, '-B', str(root / 'tools/devflower_v2.py'),
                '--state-dir', str(self.store.state), '--allow-root', str(self.spaces), '--no-browser'],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('http://', result.stdout)

    def test_duplicate_edge_ids_on_different_valid_pairs_are_rejected(self):
        doc = chain(); doc['nodes'].append({'id': 'Gamma', 'name': 'C', 'position': {'x': 800, 'y': 20}})
        doc['edges'].append({'id': 'edge1', 'source': 'Beta', 'target': 'Gamma'})
        with self.assertRaises(Invalid): validate_draft(doc)

    def test_execution_shape_diagnostics_do_not_block_saving(self):
        doc = chain(); doc['edges'].append({'id': 'reverse', 'source': 'Beta', 'target': 'Alpha'})
        self.assertEqual(validate_draft(doc), doc)
        self.assertTrue(check_chain(doc)['issues'])
        self.assertFalse(check_chain(doc)['execution_available'])


class HTTPTests(unittest.TestCase):
    # Use only the inherited fixture, not a second copy of all domain tests.
    def setUp(self):
        DraftTests.setUp(self); self.server, self.token = create_server(self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, path, body=None, token=True, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Authorization': 'Bearer ' + self.token} if token else {}
        if origin: headers['Origin'] = origin
        if body is not None: headers['Content-Type'] = 'application/json'
        connection.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse(); result = response.read(); connection.close()
        return response.status, json.loads(result) if result.startswith((b'{', b'[')) else result

    def test_http_save_reopen_and_cas_conflict(self):
        code, saved = self.request('/api/v2/drafts/save', {'workspace': self.wa, 'id': None, 'expected': None, 'document': blank()})
        self.assertEqual(code, 200)
        code, row = self.request('/api/v2/draft?workspace=' + self.wa + '&id=' + saved['id'])
        self.assertEqual((code, row['graph']), (200, blank()))
        changed = dict(blank(), name='updated')
        self.assertEqual(self.request('/api/v2/drafts/save', {'workspace': self.wa, 'id': saved['id'], 'expected': None, 'document': changed})[0], 409)
        self.assertEqual(self.request('/api/v2/draft?workspace=' + self.wb + '&id=' + saved['id'])[0], 409)

    def test_http_auth_origin_and_no_execution_routes(self):
        self.assertEqual(self.request('/api/v2/status', token=False)[0], 401)
        self.assertEqual(self.request('/api/v2/status', origin='https://foreign.invalid')[0], 403)
        self.assertEqual(self.request('/api/v2/start', {'anything': 'never execute'})[0], 404)
        self.assertEqual(self.request('/api/start', {'anything': 'legacy not mounted'})[0], 404)
        self.assertEqual(self.request('/api/v2/status')[1]['execution_available'], False)

    def test_http_invalid_structures_and_outside_folder(self):
        self.assertEqual(self.request('/api/v2/drafts/save', {'workspace': self.wa, 'id': None, 'expected': None, 'document': []})[0], 400)
        self.assertEqual(self.request('/api/v2/workspaces/open', {'path': str(self.root)})[0], 400)

    def test_http_malformed_workspace_identity_is_a_bounded_400(self):
        for key in [[], {}, None, 42, '', 'a' * 63, 'z' * 64]:
            with self.subTest(key=key):
                code, body = self.request('/api/v2/drafts/save', {'workspace': key, 'id': None, 'expected': None, 'document': blank()})
                self.assertEqual(code, 400)
                self.assertIn('身份', body['error'])
        self.assertEqual(self.request('/api/v2/status')[0], 200)

    def test_http_database_unavailable_returns_recoverable_503(self):
        with self.store.db.tx() as db: db.execute('DROP TABLE records')
        code, body = self.request('/api/v2/status')
        self.assertEqual(code, 503)
        self.assertIn('当前草稿仍保留在编辑器', body['error'])


class EvidenceTests(unittest.TestCase):
    def test_trace_redaction_preserves_failure_steps_and_original_hash(self):
        with tempfile.TemporaryDirectory() as root:
            trace = Path(root) / 'trace.zip'
            marker = 'synthetic-capability-value-1234567890'
            with zipfile.ZipFile(trace, 'w') as archive:
                archive.writestr('trace.network', json.dumps({'header': 'Bearer ' + marker,
                    'url': 'http://127.0.0.1:12345/#' + marker, 'status': 'FAILED'}))
                archive.writestr('resources/screenshot.png', b'synthetic-image-bytes')
            original = hashlib.sha256(trace.read_bytes()).hexdigest()
            result = redact_trace(trace)
            self.assertEqual(result['original_sha256'], original)
            with zipfile.ZipFile(trace) as archive:
                self.assertNotIn(marker.encode(), archive.read('trace.network'))
                self.assertIn(b'FAILED', archive.read('trace.network'))
                self.assertEqual(archive.read('resources/screenshot.png'), b'synthetic-image-bytes')
            self.assertEqual(redact_trace(trace), result)


if __name__ == '__main__': unittest.main()
