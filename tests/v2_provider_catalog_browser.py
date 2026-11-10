"""Actual Chromium and loopback, with synthetic metadata and Codex fixtures."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v2.copilot_catalog import CopilotCatalog
from v2.copilot_transport import CatalogFault
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.store import DraftStore
from v2_browser import redact_trace
from test_v2_provider_catalog import PROFILE, make_profile


@contextmanager
def service(store, catalog, runtime=None):
    server, token = create_server(store, runtime=runtime, copilot_catalog=catalog)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval':.03}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/#{token}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def main():
    parser = argparse.ArgumentParser()
    for key in ('artifacts', 'chromium', 'playwright-module'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    out = Path(args.artifacts).resolve()
    root = Path(__file__).resolve().parents[1]
    if out.is_relative_to(root) or root.is_relative_to(out):
        parser.error('Evidence must remain outside product source')
    out.mkdir(parents=True, mode=0o700, exist_ok=False)

    with TemporaryDirectory(prefix='df-provider-catalog-browser-') as scratch:
        base = Path(scratch)
        home, projects = base/'fixture-home', base/'projects'
        home.mkdir()
        for key in ('A','B'):
            (projects/key).mkdir(parents=True)
        store = DraftStore(base/'state', [projects])
        empty = DraftStore(base/'empty-state', [projects])
        a, b = [store.open_workspace(str(projects/key))['id'] for key in ('A','B')]
        for workspace in (a,b):
            store.save(workspace, None, None, {
                'version':1,'name':'合成工作流','nodes':[
                    {'id':'agentA','name':'Agent Fixture A','position':{'x':10,'y':80},'provider':'codex',
                     'model':'old-codex-model','effort':'low'},
                    {'id':'agentB','name':'Agent Fixture B','position':{'x':10,'y':265}},
                ],'edges':[],
            })
        config, _ = make_profile(base)
        calls, codex_calls = [], []
        lock = Lock()

        def observe(profile):
            with lock:
                assert profile.profile_id == PROFILE
                calls.append('fixture-only')
                number = len(calls)
            if number == 2:
                raise CatalogFault('COPILOT_TIMEOUT')
            return [{'id':f'fixture-{number}', 'name':'<b>literal model</b>'}]

        class OldCodex:
            def __init__(self, store, executable=None):
                pass

            def cached(self):
                return {'provider':'codex','models':[{'id':'old-codex-model','name':'Old Codex fixture','efforts':['low']}],
                        'inference_verified':False,'stale':False,'error':None,'source':'synthetic old Codex'}

            def discover(self):
                codex_calls.append('fixture-only')
                return self.cached()

        env = dict(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),GH_CONFIG_DIR=str(home/'gh'),
                   CODEX_HOME=str(home/'.codex'),CLAUDE_CONFIG_DIR=str(home/'.claude'),COPILOT_HOME=str(home/'.copilot'))
        with patch.dict(os.environ, env), patch('v2.runtime.CodexProvider', OldCodex):
            catalog = CopilotCatalog.from_path(config,store,transport=observe)
            runtime = V2Runtime(store)
            before = (store.state/'v2.sqlite').read_bytes(), (empty.state/'v2.sqlite').read_bytes()
            error = None
            try:
                with service(store,catalog,runtime) as url, service(empty,CopilotCatalog()) as empty_url:
                    browser_env = dict(os.environ, V2_URL=url, V2_EMPTY_URL=empty_url,
                                       CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module)
                    code = subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(out)],
                                          env=browser_env, cwd=root, timeout=125).returncode
            except subprocess.TimeoutExpired:
                code, error = 124, 'TimeoutExpired'
            finally:
                for trace in out.glob('*.zip'):
                    redact_trace(trace)
        result = out/'results.json'
        data = json.loads(result.read_text()) if result.exists() else {}
        after = (store.state/'v2.sqlite').read_bytes(), (empty.state/'v2.sqlite').read_bytes()
        complete = code==0 and data.get('complete') is True and len(data.get('cases',[]))==7
        summary = dict(complete=complete,cases=len(data.get('cases',[])),browser_exit=code,
                       synthetic_metadata_calls=len(calls),old_codex_calls=len(codex_calls),
                       state_unchanged=before==after,real_model_calls=0,runner_error=error)
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary),flush=True)
        return int(not (complete and before==after and len(calls)==7 and len(codex_calls)==1))


if __name__ == '__main__':
    raise SystemExit(main())
