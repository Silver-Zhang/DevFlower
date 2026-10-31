"""A native history GET observes canonical data without copying it into a project."""
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.request import Request, build_opener, ProxyHandler
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1] / 'tools')]
from test_workbench_runtime import fixture, structured_session
from studio.workbench_server import create_server


class HistoryObservationTests(unittest.TestCase):
    def test_repeated_history_reads_do_not_write_project_or_native_history(self):
        base, runtime = fixture(self, admission=True)
        native, session = structured_session(base, runtime, 'readonly-history', 'claude')
        files = runtime.project_files(base.project['id'])
        native_before = native.read_bytes()
        before = {str(p):p.read_bytes() for p in (base.source/'.devflower').rglob('*') if p.is_file()}
        server, token = create_server(runtime, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        opener = build_opener(ProxyHandler({}))
        for _ in range(3):
            url = f'http://127.0.0.1:{server.server_port}/api/history?workspace={base.project["id"]}&handle={session["handle"]}'
            with opener.open(Request(url, headers={'Authorization':'Bearer '+token}),timeout=8) as response:
                value=json.load(response)
            self.assertEqual(value['handle'], session['handle'])
            self.assertTrue(value['messages'])
        self.assertEqual(files.archives('session'), [])
        self.assertEqual(native.read_bytes(), native_before)
        after = {str(p):p.read_bytes() for p in (base.source/'.devflower').rglob('*') if p.is_file()}
        self.assertEqual(after, before)


if __name__=='__main__': unittest.main()
