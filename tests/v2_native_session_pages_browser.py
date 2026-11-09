"""Real Chromium and HTTP pagination on isolated synthetic history."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory

from v2_browser import redact_trace
from v2_native_session_pages_fixture import page_fixtures
from v2_native_sessions_fixture import snapshot


def run(root, state, data, args, out):
    command = [sys.executable, '-B', str(root / 'tools/devflower_v2.py'), '--state-dir', str(state),
               '--allow-root', str(data['projects']), '--codex-cli', str(state.parent / 'no-fixture-cli'),
               '--native-history-config', str(data['config']), '--no-browser']
    service_env = dict(os.environ, HOME=str(data['home']), CODEX_HOME=str(data['home'] / '.codex'),
                       CLAUDE_CONFIG_DIR=str(data['home'] / '.claude'))
    with (out / 'server.log').open('w') as errors:
        server = subprocess.Popen(command, cwd=root, env=service_env, stdout=subprocess.PIPE, stderr=errors, text=True)
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
            env = dict(os.environ, HOME=str(browser_home), XDG_CONFIG_HOME=str(browser_home / 'config'),
                       XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=url, V2_WORKSPACES=str(data['projects']),
                       CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module)
            return subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out)],
                                  cwd=root, env=env, timeout=120).returncode
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
    with TemporaryDirectory(prefix='v2-native-pages-browser-') as scratch:
        base = Path(scratch)
        data, _ = page_fixtures(base)
        sources = data['projects'], data['home'], data['isolated']
        original = snapshot(*sources)
        error = None
        try:
            browser_exit = run(root, base / 'state', data, args, out)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
            browser_exit = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        current = snapshot(*sources)
        differences = sorted('/'.join((Path(source).name, relative))
                             for source, relative in original.keys() | current.keys()
                             if original.get((source, relative)) != current.get((source, relative)))
        results_path = out / 'results.json'
        cases = json.loads(results_path.read_text())['cases'] if results_path.exists() else []
        complete = browser_exit == 0 and len(cases) == 6 and all(item['status'] == 'PASS' for item in cases)
        result = {'complete': complete, 'cases': len(cases), 'browser_exit': browser_exit,
                  'native_tree_unchanged': not differences, 'native_differences': differences,
                  'real_model_calls': 0, 'runner_error': error}
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return browser_exit or int(not complete or bool(differences))


if __name__ == '__main__':
    raise SystemExit(main())
