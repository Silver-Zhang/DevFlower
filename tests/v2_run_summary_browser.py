"""Real Chromium download and offline reading using isolated synthetic V2 state."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import Blocked, canonical, digest
from v2.native_history_config import startup_seed
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.store import DraftStore
from v2_browser import redact_trace
from v2_native_sessions_fixture import snapshot


RUNS = ('1' * 32, '2' * 32, '3' * 32)


@contextmanager
def service(store, *, with_runtime):
    runtime = None
    if with_runtime:
        with patch('v2.runtime.CodexProvider', side_effect=Blocked('synthetic fixture without a Provider')):
            runtime = V2Runtime(store)
    server, token = create_server(store, runtime=runtime)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.03}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/#{token}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def prepare(base):
    projects = base / 'projects'
    for folder in ('A', 'B'):
        path = projects / folder
        path.mkdir(parents=True)
        (path / 'synthetic.txt').write_text('workspace unchanged', encoding='utf-8')
    empty_projects = base / 'empty-projects'
    empty_projects.mkdir()
    source = DraftStore(base / 'state', [projects])
    empty = DraftStore(base / 'empty-state', [empty_projects])
    a, b = (source.open_workspace(str(projects / folder))['id'] for folder in ('A', 'B'))
    startup_seed(source.state)
    definitions = []
    for i, (run, workspace, title) in enumerate(zip(RUNS, (a, a, b), ('第一合成运行', '第二合成运行', '其他工作区运行'))):
        graph = {
            'version': 1, 'name': title,
            'nodes': [{'id': 'fixture', 'name': 'PRIVATE_SUMMARY_FIXTURE', 'position': {'x': 0, 'y': 0},
                       'initial_prompt': 'PRIVATE_SUMMARY_FIXTURE'}],
            'edges': [],
        }
        draft = source.save(workspace, None, None, graph)
        definition = dict(workspace=workspace, draft_id=draft['id'], draft_revision=draft['revision'],
                          draft_document=draft['graph'], graph={'nodes': [], 'edges': []}, task='PRIVATE_SUMMARY_FIXTURE')
        definition_id = digest(canonical(definition))
        definitions.append(definition_id)
        source.db.put('definition', definition_id, dict(definition, id=definition_id))
        source.db.put('run', run, dict(id=run, workspace=workspace, definition_id=definition_id,
                                     draft_id=draft['id'], draft_revision=draft['revision'], state='FAILED',
                                     created_at=1700000000.125 + i, actual_calls=0, launch_intent='PRIVATE_SUMMARY_FIXTURE'))
        source.db.event(run, 'fixture-only', content='PRIVATE_SUMMARY_FIXTURE')
    return source, empty, projects, empty_projects, {'a': a, 'b': b, 'runs': RUNS, 'definitions': definitions}


def durable(store):
    state = store.state
    proof = snapshot(state)
    del proof[(str(state), '.')]
    with store.db.tx(read=True) as db:
        records = tuple(db.execute(sql).fetchall() for sql in (
            'SELECT kind,id,data FROM records ORDER BY kind,id',
            'SELECT seq,run,data FROM events ORDER BY seq',
            'SELECT * FROM launch_intents ORDER BY workspace,intent_key',
        ))
    # Fixture actors may change and restore a run row. Compare durable logical
    # records, seed, state names and filesystem bytes outside the SQLite main.
    key = (str(state), 'v2.sqlite')
    _, _, raw = proof.pop(key)
    return records, proof, raw


def main():
    parser = argparse.ArgumentParser()
    for flag in ('artifacts', 'chromium', 'playwright-module'):
        parser.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.artifacts).resolve()
    if out.is_relative_to(root) or root.is_relative_to(out):
        parser.error('Evidence must remain outside product source')
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    with TemporaryDirectory(prefix='v2-run-summary-browser-') as scratch:
        base = Path(scratch)
        source, empty, projects, empty_projects, fixture = prepare(base)
        original = durable(source)
        offline_original = durable(empty)
        roots = snapshot(projects, empty_projects)
        browser_home = base / 'browser-home'
        browser_home.mkdir()
        error = None
        rc = 1
        try:
            with service(source, with_runtime=True) as source_url, service(empty, with_runtime=False) as offline_url:
                env = dict(os.environ, HOME=str(browser_home), XDG_CONFIG_HOME=str(browser_home / 'config'),
                           XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=source_url, V2_OFFLINE_URL=offline_url,
                           V2_SUMMARY_FIXTURE=json.dumps(fixture), V2_SUMMARY_DB=str(source.state / 'v2.sqlite'),
                           V2_SUMMARY_PROJECTS=str(projects), CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module)
                rc = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out)],
                                    cwd=root, env=env, timeout=150).returncode
        except subprocess.TimeoutExpired:
            error = 'TimeoutExpired'
            rc = 124
        finally:
            for trace in out.glob('*.zip'):
                redact_trace(trace)
        result_path = out / 'results.json'
        observed = json.loads(result_path.read_text()) if result_path.exists() else {}
        source_unchanged = durable(source)[:2] == original[:2]
        offline_unchanged = durable(empty) == offline_original
        roots_unchanged = snapshot(projects, empty_projects) == roots
        complete = rc == 0 and observed.get('complete') is True and len(observed.get('cases', [])) == 8
        summary = dict(complete=complete, cases=len(observed.get('cases', [])), browser_exit=rc,
                       source_records_and_seed_unchanged=source_unchanged, offline_state_unchanged=offline_unchanged,
                       workspace_roots_unchanged=roots_unchanged, real_model_calls=0, runner_error=error)
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary), flush=True)
        return int(not (complete and source_unchanged and offline_unchanged and roots_unchanged))


if __name__ == '__main__':
    raise SystemExit(main())
