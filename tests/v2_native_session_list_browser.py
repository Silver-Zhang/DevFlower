"""Browser list discovery on isolated Codex and Claude fixtures."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v2.native_history_config import load_history_config, startup_seed
from v2.native_session_views import NativeSessionViews, action_parameters
from v2.native_sessions import NativeSessions
from v2.store import DraftStore
from v2_browser import redact_trace
from v2_native_sessions_fixture import codex_text, records, snapshot


def fixture(base):
    projects = base / 'projects'
    home = base / 'fake-home'
    isolated = base / 'approved' / 'native'
    codex = home / '.codex' / 'sessions'
    claude = home / '.claude' / 'projects'
    state = base / 'state'
    for name in ('A', 'B'):
        (projects / name).mkdir(parents=True)
    configured = projects/'A'/'configured'/'.claude'
    configured.mkdir(parents=True)
    (configured/'settings.json').write_text('{"model":"PRIVATE_SETTINGS_FIXTURE"}')
    aliases = {}

    def add(provider, source, workspace, number, timestamp, alias=None, action=None):
        sid = f'{number:08x}-1111-4222-8333-000000000000'
        cwd = str(projects / workspace)
        clock = {'timestamp': timestamp} if timestamp is not None else {}
        if provider == 'codex':
            target = source / '2026' / '12' / '20' / f'rollout-2026-12-20T12-00-00-{sid}.jsonl'
            rows = [{'type': 'session_meta', 'payload': {'id': sid, 'cwd': cwd}, **clock},
                    codex_text('user', f'SYNTHETIC_{workspace}_{number}')]
        else:
            target = source / 'fixture' / f'{sid}.jsonl'
            rows = [{'type': 'user', 'cwd': cwd, 'sessionId': sid, **clock,
                     'message': {'role': 'user', 'content': f'SYNTHETIC_{workspace}_{number}'}}]
        records(target, rows)
        if alias is not None or action is not None:
            aliases[target] = alias, action

    add('codex', codex, 'A', 1, '2026-12-20T08:00:00+08:00', '任务 10 ＱＡ <b>')
    add('codex', codex, 'A', 2, '2026-12-20T01:00:00-08:00', '任务 2 ＱＡ <b>')
    add('codex', codex, 'A', 3, '2026-12-19T12:00:00Z', '同名 10')
    add('codex', isolated, 'A', 3, '2026-12-19T12:00:00Z', '同名 10')
    add('codex', codex, 'A', 4, '2026-12-17T12:00:00Z')
    add('codex', codex, 'A', 5, '2026-12-13T12:00:00Z', '正好七天')
    add('codex', codex, 'A', 6, '2026-12-20T13:00:00Z', '未来默认')
    add('codex', codex, 'A', 7, '2026-99-99T00:00:00Z', '非法默认')
    add('codex', codex, 'A', 8, '2026-12-01T10:00:00Z', 'HIDDEN_ALIAS_SENTINEL', 'hide')
    add('codex', codex, 'A', 9, '2026-12-01T11:00:00Z', 'ARCHIVED_ALIAS_SENTINEL', 'archive')
    add('codex', codex, 'A', 10, '2026-12-01T12:00:00Z', 'TRASH_ALIAS_SENTINEL', 'trash')
    add('claude', claude, 'A', 11, None, '无时间')
    add('claude', claude, 'A', 12, '2026-12-19T12:00:01Z', '当天边界')
    add('claude', claude, 'A', 13, '2026-12-01T12:00:00Z', '协作 <b>ß')
    add('claude', claude, 'A', 14, '2026-12-14T12:00:00Z')
    add('codex', codex, 'B', 15, '2026-12-20T05:00:00Z', '乙工作区标记')
    config = base / 'native-history.json'
    config.write_text(json.dumps({'version': 1, 'discover_defaults': True, 'discover_nested': True, 'profiles': [
        {'id': 'qa-source', 'provider': 'codex', 'anchor': str(isolated.parent), 'root': str(isolated)},
    ]}), encoding='utf-8')
    config.chmod(0o600)
    store = DraftStore(state, [projects])
    reader = NativeSessions(store, load_history_config(config), startup_seed(state), home=home)
    views = NativeSessionViews(reader)
    for name in ('A', 'B'):
        workspace = store.open_workspace(str(projects / name))['id']
        revision = reader.listing(workspace)['revision']
        for handle, record in reader.snapshots[workspace].reads.items():
            file = record.source.profile.root / record.ref.relative
            if file not in aliases:
                continue
            alias, action = aliases[file]
            scope = dict(workspace=workspace, revision=revision, handle=handle)
            prior = None
            if alias is not None:
                result = views.change(action_parameters(json.dumps({
                    **scope, 'action': 'rename', 'alias': alias, 'expected_view_revision': None,
                }).encode()))
                prior = result['view']['view_revision']
            if action is not None:
                views.change(action_parameters(json.dumps({
                    **scope, 'action': action, 'expected_view_revision': prior,
                }).encode()))
    return projects, home, isolated, state, config


def run(root, paths, args, out):
    projects, home, _, state, config = paths
    command = [sys.executable, '-B', str(root / 'tools/devflower_v2.py'), '--state-dir', str(state),
               '--allow-root', str(projects), '--codex-cli', str(state.parent / 'no-fixture-cli'),
               '--native-history-config', str(config), '--no-browser']
    env = dict(os.environ, HOME=str(home), CODEX_HOME=str(home / '.codex'), CLAUDE_CONFIG_DIR=str(home / '.claude'))
    with (out / 'server.log').open('w') as errors:
        server = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(server.stdout, selectors.EVENT_READ)
                if not poller.select(20):
                    raise RuntimeError('synthetic service did not start')
                url = server.stdout.readline().strip()
            if not url.startswith('http://127.0.0.1:') or '/#' not in url:
                raise RuntimeError('synthetic service address is unavailable')
            browser_home = state.parent / 'browser-home'
            browser_home.mkdir()
            browser_env = dict(os.environ, HOME=str(browser_home), XDG_CONFIG_HOME=str(browser_home / 'config'),
                               XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=url,
                               CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module)
            return subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out)],
                                  cwd=root, env=browser_env, timeout=120).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            for trace in out.glob('*.zip'):
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
    with TemporaryDirectory(prefix='v2-native-list-browser-') as scratch:
        paths = fixture(Path(scratch))
        sources = paths[:3]
        original = snapshot(*sources)
        state_files = tuple(paths[3] / name for name in ('v2.sqlite', 'native-history.seed'))
        state_before = tuple(path.read_bytes() for path in state_files)
        error = None
        try:
            browser_exit = run(root, paths, args, out)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
            browser_exit = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        current = snapshot(*sources)
        differences = sorted('/'.join((Path(source).name, relative))
                             for source, relative in original.keys() | current.keys()
                             if original.get((source, relative)) != current.get((source, relative)))
        unchanged = tuple(path.read_bytes() for path in state_files) == state_before
        results_path = out / 'results.json'
        results = json.loads(results_path.read_text()) if results_path.exists() else {}
        cases = results.get('cases', [])
        intervals = results.get('zeroTraffic', [])
        zero = bool(intervals) and all(item['count'] == 0 for item in intervals)
        complete = browser_exit == 0 and len(cases) == 8 and all(item['status'] == 'PASS' for item in cases)
        result = {'complete': complete, 'cases': len(cases), 'browser_exit': browser_exit,
                  'native_tree_unchanged': not differences, 'native_differences': differences,
                  'durable_state_unchanged': unchanged, 'zero_request_intervals': intervals,
                  'real_model_calls': 0, 'runner_error': error}
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return browser_exit or int(not complete or not unchanged or bool(differences) or not zero)


if __name__ == '__main__':
    raise SystemExit(main())
