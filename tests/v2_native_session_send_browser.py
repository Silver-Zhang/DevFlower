"""Real V2 HTTP and Chromium against synthetic session-send results."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v2.native_history_config import HistoryConfig
from v2.native_sessions import NativeSessions
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.store import DraftStore
from v2_execution_browser import SyntheticProvider
from v2_native_sessions_fixture import codex_text, records


class SyntheticSessionOperations:
    """The synthetic transport stops at HTTP results and fixture-native records."""
    def __init__(self, reader, root, workspace, cwd):
        self.reader, self.workspace, self.cwd = reader, workspace, cwd
        self.sid = str(uuid.uuid4())
        self.file = root / '2026' / '11' / '10' / f'rollout-2026-11-10T12-00-00-{self.sid}.jsonl'
        self.turns = [{'type': 'session_meta', 'payload': {'id': self.sid, 'cwd': cwd}}]
        self.receipts = {}
        self.handle = None

    def status(self):
        return ['codex']

    def add_continue_eligibility(self, listing):
        for row in listing['sessions']:
            row['can_request_continue'] = row['access'] == 'readable' and row['handle'] == self.handle

    def operation(self, workspace, intent_key):
        assert workspace == self.workspace
        return self.receipts[intent_key]

    def send(self, data):
        assert set(data) == {'workspace', 'provider', 'handle', 'revision', 'intent_key', 'prompt', 'model', 'effort'}
        assert data['workspace'] == self.workspace and data['provider'] == 'codex'
        assert data['model'] == 'fixture-model' and data['effort'] == 'low'
        key = data['intent_key']
        assert key not in self.receipts
        if self.handle is None:
            assert data['handle'] is None and data['revision'] is None
        else:
            assert data['handle'] == self.handle and data['revision']
        self.turns += [codex_text('user', data['prompt']), codex_text('assistant', 'BROWSER_FIXTURE_ANSWER')]
        records(self.file, self.turns)
        listing = self.reader.listing(self.workspace)
        row, = listing['sessions']
        assert row['access'] == 'readable'
        if self.handle is not None:
            assert self.handle == row['handle']
        self.handle = row['handle']
        result = dict(workspace=self.workspace, provider='codex', intent_key=key, state='COMPLETED', handle=self.handle)
        self.receipts[key] = result
        return result


def main():
    parser = argparse.ArgumentParser()
    for field in ('artifacts', 'chromium', 'playwright-module'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    out = Path(args.artifacts).resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    with TemporaryDirectory(prefix='v2-native-session-send-') as folder:
        base = Path(folder)
        home = base / 'native-home'
        root = home / '.codex' / 'sessions'
        root.mkdir(parents=True)
        spaces = base / 'spaces'
        cwd = spaces / 'A'
        cwd.mkdir(parents=True)
        store = DraftStore(base / 'state', [spaces])
        workspace = store.open_workspace(str(cwd))['id']
        reader = NativeSessions(store, HistoryConfig(True, False, ()), b'p' * 32, home=home)
        operations = SyntheticSessionOperations(reader, root, workspace, str(cwd))
        with patch('v2.runtime.CodexProvider', SyntheticProvider):
            runtime = V2Runtime(store)
        server, token = create_server(store, runtime=runtime, native_sessions=reader, session_operations=operations)
        Thread(target=server.serve_forever, daemon=True).start()
        browser_home = base / 'browser-home'
        browser_home.mkdir()
        env = dict(os.environ, V2_URL=f'http://127.0.0.1:{server.server_port}/#{token}', HOME=str(browser_home),
                   CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module)
        try:
            result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out)],
                                    env=env, timeout=120, check=False)
            print(f'synthetic browser exit={result.returncode}, effects={len(operations.receipts)}, real model calls=0')
            return int(bool(result.returncode or len(operations.receipts) != 2))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
