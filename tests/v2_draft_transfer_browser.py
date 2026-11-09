"""Real Chromium portable drafts on a restarted, synthetic-only V2 service."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from orchestration.common import canonical, digest
from v2.native_history_config import startup_seed
from v2.store import DraftStore
from v2_browser import redact_trace
from v2_native_sessions_fixture import snapshot


def prepare(base, out):
    projects = base / 'projects'
    for name in ('A', 'B'):
        folder = projects / name
        folder.mkdir(parents=True)
        (folder / 'unchanged.txt').write_text('isolated fixture only', encoding='utf-8')
    state = base / 'state'
    store = DraftStore(state, [projects])
    a, b = (store.open_workspace(str(projects / name))['id'] for name in ('A', 'B'))
    source = store.save(a, None, None, {
        'version': 1, 'name': '移交流程 🙂 <b>原样</b>',
        'nodes': [
            {'id': 'source', 'name': '节点 中文', 'position': {'x': -0., 'y': 5e-324},
             'provider': '', 'permission': 'read', 'timeout': 10,
             'initial_prompt': '原样换行\n引号" 反斜杠\\🙂\t\u2028\u2029\x01' + '长文' * 9000},
            {'id': 'target', 'name': '未配置节点', 'position': {'x': 0.000001, 'y': 1e-7}},
        ],
        'edges': [{'id': 'edge', 'source': 'source', 'target': 'target'}],
        'viewport': {'x': -0., 'y': 0.000001, 'zoom': 1.0000000000000002},
    })
    a_spare = store.save(a, None, None, {'version': 1, 'name': '同域备用草稿', 'nodes': [], 'edges': []})
    b_original = store.save(b, None, None, {'version': 1, 'name': '目标原草稿', 'nodes': [], 'edges': []})
    b_edit = store.save(b, None, None, {'version': 1, 'name': '目标编辑夹具', 'nodes': [], 'edges': []})
    definition = {'draft_document': source['graph'], 'graph': {'nodes': []}}
    definition_id = digest(canonical(definition))
    store.db.put('definition', definition_id, dict(definition, id=definition_id))
    run_id = '1' * 32
    store.db.put('run', run_id, {'id': run_id, 'workspace': a, 'definition_id': definition_id,
                                'draft_id': source['id'], 'draft_revision': source['revision'],
                                'state': 'FAILED', 'created_at': 1, 'actual_calls': 0})
    store.db.event(run_id, 'fixture-only')
    store.db.put('native-session-view', 'fixture-view', {'alias': '合成偏好'})
    startup_seed(state)
    fixture = dict(a=a, b=b, source=source, aSpare=a_spare, bOriginal=b_original, bEdit=b_edit)
    (out / 'synthetic-fixture.json').write_text(json.dumps(fixture, ensure_ascii=False), encoding='utf-8')
    return projects, state, store, fixture


def unrelated(store):
    with store.db.tx(read=True) as db:
        return tuple(db.execute(query).fetchall() for query in (
            "SELECT kind,id,data FROM records WHERE kind<>'draft' ORDER BY kind,id",
            'SELECT * FROM events ORDER BY seq', 'SELECT * FROM launch_intents ORDER BY workspace,intent_key',
        ))


def run(root, projects, state, args, out, stage, port=0):
    command = [sys.executable, '-B', str(root / 'tools/devflower_v2.py'), '--state-dir', str(state),
               '--allow-root', str(projects), '--codex-cli', str(state.parent / 'no-fixture-cli'),
               '--port', str(port), '--no-browser']
    service_home = state.parent / 'service-home'
    service_home.mkdir(exist_ok=True)
    service_env = dict(os.environ, HOME=str(service_home), CODEX_HOME=str(service_home / '.codex'),
                       CLAUDE_CONFIG_DIR=str(service_home / '.claude'))
    with (out / f'{stage}-server.log').open('w') as errors:
        server = subprocess.Popen(command, cwd=root, env=service_env, stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(server.stdout, selectors.EVENT_READ)
                if not poller.select(20):
                    raise RuntimeError('synthetic service did not start')
                url = server.stdout.readline().strip()
            if not url.startswith('http://127.0.0.1:') or '/#' not in url:
                raise RuntimeError('synthetic service did not supply its address')
            browser_home = state.parent / 'browser-home'
            browser_home.mkdir(exist_ok=True)
            env = dict(os.environ, HOME=str(browser_home), XDG_CONFIG_HOME=str(browser_home / 'config'),
                       XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=url, V2_FIXTURE_STATE=str(state),
                       CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module)
            rc = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out), stage],
                                cwd=root, env=env, timeout=150).returncode
            return rc, urlsplit(url).port
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            for trace in out.glob(f'{stage}-*.zip'):
                redact_trace(trace)


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
    with TemporaryDirectory(prefix='v2-draft-transfer-browser-') as scratch:
        projects, state, store, fixture = prepare(Path(scratch), out)
        before = snapshot(projects)
        other_before = unrelated(store)
        seed = (state / 'native-history.seed').read_bytes()
        initial, restart, error = 1, None, None
        try:
            initial, port = run(root, projects, state, args, out, 'initial')
            if initial == 0:
                restart, _ = run(root, projects, state, args, out, 'restart', port)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
            initial = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        cases = []
        for stage in ('initial', 'restart'):
            raw = out / f'{stage}-results.json'
            if raw.exists():
                cases.extend(json.loads(raw.read_text())['cases'])
        a, b = fixture['a'], fixture['b']
        rows = store.drafts(a) + store.drafts(b)
        before_ids = {fixture[key]['id'] for key in ('source', 'aSpare', 'bOriginal', 'bEdit')}
        copies = [row for row in rows if row['id'] not in before_ids]
        original_drafts = (store.draft(a, fixture['source']['id']) == fixture['source']
                           and store.draft(b, fixture['bOriginal']['id']) == fixture['bOriginal'])
        copied_once = len(copies) == 1 and copies[0]['workspace'] == b and copies[0]['graph'] == fixture['source']['graph']
        unchanged = before == snapshot(projects) and unrelated(store) == other_before and (state / 'native-history.seed').read_bytes() == seed
        complete = initial == 0 and restart == 0 and len(cases) == 9 and all(item['status'] == 'PASS' for item in cases)
        result = {'complete': complete, 'cases': len(cases), 'initial_exit': initial, 'restart_exit': restart,
                  'original_drafts_unchanged': original_drafts, 'explicit_copy_once': copied_once,
                  'unrelated_state_and_sources_unchanged': unchanged, 'real_model_calls': 0, 'runner_error': error}
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return int(not (complete and original_drafts and copied_once and unchanged))


if __name__ == '__main__':
    raise SystemExit(main())
