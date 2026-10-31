"""Shared OS PTY tests. These do not stand in for actual Provider evidence."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1] / 'tools')]
import test_native_terminals as native_tests
from studio.native_attach import AttachClient, receive
from orchestration.common import Blocked
from studio.server import Conflict


class NativeAttachTests(unittest.TestCase):
    setUp = native_tests.NativeTerminalProtocolTests.setUp
    prepare = native_tests.NativeTerminalProtocolTests.prepare
    launch = native_tests.NativeTerminalProtocolTests.launch
    wait = native_tests.NativeTerminalProtocolTests.wait
    def connect(self):
        self.manager.share(self.workspace, self.key)
        descriptor = self.manager.entries[self.key]['attach'].path
        client = AttachClient(descriptor)
        self.addCleanup(client.close)
        return client

    def test_same_pty_two_observers_and_bidirectional_takeover(self):
        self.launch()
        before = self.manager.entries[self.key]['proc'].pid
        a, b = self.connect(), self.connect()
        self.assertEqual(a.hello['session_id'], self.session['session_id'])
        self.assertEqual(a.request('observe')['output'], b.request('observe')['output'])
        row = a.request('claim', generation=0)
        self.assertEqual(row['input_generation'], 1)
        with self.assertRaises(Conflict): self.manager.input(self.workspace, self.key, 1, 'stale-web')
        a.request('input', generation=1, sequence=1, text='local-input\n')
        self.wait(lambda: 'Got:local-input' in b.request('observe')['output'])
        row = self.manager.claim(self.workspace, self.key, 1)
        with self.assertRaises(Blocked): a.request('input', generation=1, sequence=2, text='stale-local')
        self.manager.input(self.workspace, self.key, 2, 'web-input\n', generation=row['input_generation'])
        self.wait(lambda: 'Got:web-input' in a.request('observe')['output'])
        text = a.request('observe')['output']
        self.assertNotIn('stale-web', text); self.assertNotIn('stale-local', text)
        self.assertEqual(self.manager.entries[self.key]['proc'].pid, before)

    def test_competing_claims_have_one_winner(self):
        self.launch()
        clients = [self.connect(), self.connect()]
        barrier = threading.Barrier(2); results = []
        def run(client):
            barrier.wait()
            try: client.request('claim', generation=0); results.append('claimed')
            except Blocked: results.append('refused')
        threads = [threading.Thread(target=run, args=(client,)) for client in clients]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertCountEqual(results, ['claimed', 'refused'])

    def test_detach_disables_input_without_stopping_or_replaying(self):
        self.launch(); client = self.connect()
        client.request('claim', generation=0)
        client.request('input', generation=1, sequence=1, text='once\n')
        client.close()
        self.wait(lambda: self.manager.snapshot(self.workspace, self.key)['input_owner'] == 'unclaimed')
        self.assertTrue(self.manager.snapshot(self.workspace, self.key)['process_alive'])
        fresh = self.connect(); row = fresh.request('observe')
        self.assertEqual(row['input_sequence'], 1)
        self.assertEqual(row['output'].count('Got:once'), 1)
        with self.assertRaises(Conflict): self.manager.input(self.workspace, self.key, 2, 'no-autoclaim', generation=row['input_generation'])
        row = self.manager.claim(self.workspace, self.key, row['input_generation'])
        self.assertEqual(row['input_owner'], 'workbench')

    def test_old_generation_cannot_replay_even_known_sequence(self):
        self.launch(); self.manager.input(self.workspace, self.key, 1, 'once')
        client = self.connect(); client.request('claim', generation=0)
        with self.assertRaises(Conflict): self.manager.input(self.workspace, self.key, 1, 'once')
        with self.assertRaises(Blocked): client.request('resize', generation=0, cols=80, rows=24)
        self.assertEqual(client.request('observe')['input_sequence'], 1)

    def test_observation_never_claims_and_cross_workspace_is_rejected(self):
        self.launch(); client = self.connect()
        self.assertEqual(client.request('observe')['input_owner'], 'workbench')
        self.assertFalse(self.manager.share(self.workspace, self.key)['desktop_connected'])
        with self.assertRaises(Blocked): self.manager.share('foreign', self.key)
        with self.assertRaises(Blocked): client.request('shell', argv=['sh'])

    def test_private_descriptor_and_wrong_session_are_rejected(self):
        self.launch(); self.connect()
        server = self.manager.entries[self.key]['attach']
        self.assertEqual(server.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(server.path.stat().st_mode & 0o777, 0o600)
        os.chmod(server.path, 0o644)
        with self.assertRaises(Blocked): AttachClient(server.path)
        os.chmod(server.path, 0o600)
        value = json.loads(server.path.read_text())
        for change in ({'session_id': 'other'}, {'token': 'wrong'}):
            self.assertEqual(value['version'], 2)
            sock = socket.socket(socket.AF_UNIX); sock.settimeout(2); sock.connect('\0'+value['socket'])
            with sock.makefile('rwb') as stream:
                hello = {k: value[k] for k in ('token', 'terminal', 'workspace', 'session_id')}; hello.update(change)
                stream.write(json.dumps(hello).encode()+b'\n'); stream.flush()
                with self.assertRaises(Blocked): receive(stream)
            sock.close()

    def test_close_removes_only_ephemeral_attach_files(self):
        self.launch(); self.connect(); server = self.manager.entries[self.key]['attach']
        self.manager.close(self.workspace, self.key)
        self.assertEqual(server.cleanup_info['status'],'retained_private_metadata')
        self.assertEqual(server.path.read_bytes(),b'')
        self.assertEqual(server.root.stat().st_mode & 0o777,0o700)
        self.assertEqual([p.name for p in server.root.iterdir()],['attach.json'])
        self.assertEqual(server.root.stat().st_uid,os.getuid())
        with self.assertRaises(Blocked): self.manager.share(self.workspace, self.key)

    def test_real_http_rejects_stale_browser_after_local_claim(self):
        from studio.workbench_server import create_server
        from urllib.request import Request, build_opener, ProxyHandler
        from urllib.error import HTTPError
        server, token = create_server(self.rt, 0)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.manager = server.native_terminals
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.launch()
        opener = build_opener(ProxyHandler({}))
        def post(path, data, authorized=True):
            request = Request('http://127.0.0.1:'+str(server.server_port)+path,
                data=json.dumps(data).encode(), headers={'Content-Type':'application/json',
                'Authorization':'Bearer '+(token if authorized else 'wrong')})
            return json.load(opener.open(request, timeout=5))
        data = dict(workspace=self.workspace, id=self.key)
        self.assertEqual(post('/api/terminal/share', data)['session_id'], self.session['session_id'])
        client = self.connect(); client.request('claim', generation=0)
        with self.assertRaises(HTTPError) as error:
            post('/api/terminal/input', dict(data, sequence=1, text='stale', generation=0))
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            post('/api/terminal/claim', dict(data, generation=1), authorized=False)
        self.assertEqual(error.exception.code, 403)
        row = post('/api/terminal/claim', dict(data, generation=1))
        self.assertEqual(row['input_generation'], 2)
        self.assertTrue(post('/api/terminal/input', dict(data, sequence=1, text='http-once', generation=2))['accepted'])


if __name__ == '__main__': unittest.main()
