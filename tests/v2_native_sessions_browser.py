"""Real V2 launcher/HTTP/Chromium on synthetic opt-in native history roots."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory

from v2_browser import redact_trace
from v2_native_sessions_fixture import fixtures, snapshot


def run(root, state, data, args, out, enabled):
    name = 'enabled' if enabled else 'disabled'
    command = [sys.executable, '-B', str(root / 'tools/devflower_v2.py'), '--state-dir', str(state),
               '--allow-root', str(data['projects']), '--codex-cli', str(state.parent / 'no-fixture-cli'), '--no-browser']
    if enabled:
        command.extend(('--native-history-config', str(data['config'])))
    env = dict(os.environ, HOME=str(data['home']), CODEX_HOME=str(data['home'] / '.codex'),
               CLAUDE_CONFIG_DIR=str(data['home'] / '.claude'), XDG_CONFIG_HOME=str(data['home'] / 'config'))
    with (out / f'server-{name}.log').open('w') as errors:
        server = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=errors, text=True)
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(server.stdout, selectors.EVENT_READ)
                if not poller.select(20):
                    raise RuntimeError('synthetic service did not start')
                url = server.stdout.readline().strip()
            if not url.startswith('http://127.0.0.1:') or '/#' not in url:
                raise RuntimeError('synthetic service did not provide its private address')
            # Chromium and Playwright may create preferences/cache entries.
            # Give them a home separate from the native source fixtures.
            browser_home = state.parent / 'browser-home'
            browser_home.mkdir(exist_ok=True)
            browser_env = dict(os.environ, HOME=str(browser_home), XDG_CONFIG_HOME=str(browser_home / 'config'),
                               XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=url,
                               V2_WORKSPACES=str(data['projects']), CHROMIUM=args.chromium,
                               PLAYWRIGHT_MODULE=args.playwright_module)
            return subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out), name],
                                  cwd=root, env=browser_env, timeout=100).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            for trace in out.glob(f'{name}*.zip'):
                redact_trace(trace)


def main():
    parser = argparse.ArgumentParser()
    for field in ('artifacts', 'chromium', 'playwright-module'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.artifacts).resolve()
    if out.is_relative_to(root) or root.is_relative_to(out):
        parser.error('Evidence must remain outside product source')
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    with TemporaryDirectory(prefix='v2-native-browser-fixture-') as scratch:
        base = Path(scratch)
        data = fixtures(base)
        state = base / 'state'
        original = snapshot(data['projects'], data['home'], data['isolated'])
        enabled = run(root, state, data, args, out, True)
        seeded = (state / 'native-history.seed').read_bytes()
        disabled = run(root, state, data, args, out, False)
        current = snapshot(data['projects'], data['home'], data['isolated'])
        differences = sorted('/'.join((Path(root).name, relative)) for root, relative in original.keys() | current.keys()
                             if original.get((root, relative)) != current.get((root, relative)))
        unchanged = not differences
        stable_seed = (state / 'native-history.seed').read_bytes() == seeded and len(seeded) == 32
        result = {'enabled_exit': enabled, 'disabled_exit': disabled,
                  'native_tree_unchanged': unchanged, 'native_differences': differences,
                  'seed_unchanged_on_disabled_restart': stable_seed,
                  'real_model_calls': 0}
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return int(bool(enabled or disabled or not unchanged or not stable_seed))


if __name__ == '__main__':
    raise SystemExit(main())
