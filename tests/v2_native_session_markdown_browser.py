"""Real V2/Chromium Markdown checks on isolated synthetic native histories."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from v2.native_history_config import startup_seed
from v2.store import DraftStore
from v2_browser import redact_trace
from v2_native_sessions_fixture import SID, codex, codex_text, fixtures, records, snapshot


RICH = """# Safe Markdown heading

First **strong fixture**, *emphasis fixture*, ~~deleted fixture~~ and `<script>inline()</script>`.

> Quoted fixture

3. Third fixture
4. Fourth fixture

- [x] Checked fixture
- [ ] Unchecked fixture

---

| First heading | Second heading | Third heading |
| :--- | :---: | ---: |
| First wide table fixture text | Second wide table fixture text | Third wide table fixture text |

Code with an inert language label:

```html user-class
<iframe src="https://outside.invalid/code">code fixture</iframe>
```

Footnote fixture[^fixture].

[^fixture]: Footnote body.
"""
LINKS = """[Remote label](https://outside.invalid/link "ignored title") [JS label](javascript:alert%281%29)
[Data label](data:text/plain,danger) [VB label](vbscript:fixture) [File label](file:///fixture) [Blob label](blob:https://outside.invalid/blob)
[Relative label](/fixture) <https://outside.invalid/autolink>
![Remote image alternative](https://outside.invalid/image.png "ignored image title")
"""
RAW = """**Raw HTML retains this syntax**

<script>window.__markdownExecuted = true; fetch("https://outside.invalid/raw-script")</script>
<img src="https://outside.invalid/raw-image" onerror="window.__markdownExecuted=true">
<iframe src="https://outside.invalid/raw-frame">frame text</iframe>
<object data="https://outside.invalid/object"></object><embed src="https://outside.invalid/embed">
<svg onload="window.__markdownExecuted=true"><a href="javascript:alert(1)">svg text</a></svg>
<form action="https://outside.invalid/form"><input name="raw-input"></form>
<style>@import url("https://outside.invalid/stylesheet");</style>
<div class="user-raw-class" id="user-raw-id" data-raw="fixture" aria-label="user label" style="color:red">HTML text</div>"""
SAME_PAGE = 'x' * 15992 + ' **JOINTSPLIT**\n\n' + 'Same-page tail.'
CROSS_PAGE = '**CROSS_PAGE_MATCH**\n\n' + '🙂' * 48_050
LONG_WINDOW = '**WINDOW_TRUNC_MARKER**\n\n' + 'w' * 16_040
EXACT_PREFIX = '**EXACT_LIMIT**\n\n'
EXACT_LIMIT = EXACT_PREFIX + '🙂' * (16_000 - len(EXACT_PREFIX))
CLAUDE = '## Claude fixture heading\n\n**Claude strong** with `Claude code`.'
DUPLICATE = '**DUPLICATE_MARKDOWN**'


def fixture(base, out):
    data = fixtures(base)
    codex(data['codex_root'], data['a'], rows=[
        codex_text('assistant', CROSS_PAGE), codex_text('assistant', SAME_PAGE),
        codex_text('assistant', RICH), codex_text('user', LINKS), codex_text('assistant', RAW),
        codex_text('user', DUPLICATE), codex_text('user', DUPLICATE),
        codex_text('assistant', LONG_WINDOW), codex_text('assistant', EXACT_LIMIT),
        codex_text('assistant', 'PRIVATE_MARKDOWN_MARKER', channel='analysis'),
        codex_text('assistant', 'PRIVATE_TOOL_MARKER', recipient='functions.synthetic'),
        codex_text('assistant', 'Authorization: ghp_SYNTHETIC_MD_CREDENTIAL'),
    ])
    claude = data['claude_root'] / 'synthetic-project' / (SID + '.jsonl')
    records(claude, [
        {'type': 'user', 'cwd': str(data['a']), 'sessionId': SID, 'timestamp': '2026-11-10T12:31:00Z',
         'message': {'role': 'user', 'content': CLAUDE}},
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': EXACT_LIMIT}},
    ])
    state = base / 'state'
    store = DraftStore(state, [data['projects']])
    for name in ('a', 'b'):
        store.open_workspace(str(data[name]))
    startup_seed(state)
    (out / 'synthetic-text.json').write_text(json.dumps({
        'rich': RICH.strip(), 'links': LINKS.strip(), 'raw': RAW.strip(), 'same': SAME_PAGE,
        'longWindow': LONG_WINDOW[:16_000], 'exactLimit': EXACT_LIMIT,
        'claude': CLAUDE, 'duplicate': DUPLICATE,
    }, ensure_ascii=False), encoding='utf-8')
    return data, state


def run(root, data, state, args, out):
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
                       XDG_CACHE_HOME=str(browser_home / 'cache'), V2_URL=url,
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
    with TemporaryDirectory(prefix='v2-native-markdown-browser-') as scratch:
        data, state = fixture(Path(scratch), out)
        sources = data['projects'], data['home'], data['isolated']
        original = snapshot(*sources)
        state_files = tuple(state / name for name in ('v2.sqlite', 'native-history.seed'))
        before_state = tuple(path.read_bytes() for path in state_files)
        error = None
        try:
            browser_exit = run(root, data, state, args, out)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
            browser_exit = 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        current = snapshot(*sources)
        differences = sorted('/'.join((Path(source).name, relative))
                             for source, relative in original.keys() | current.keys()
                             if original.get((source, relative)) != current.get((source, relative)))
        unchanged = tuple(path.read_bytes() for path in state_files) == before_state
        results_path = out / 'results.json'
        results = json.loads(results_path.read_text()) if results_path.exists() else {}
        cases = results.get('cases', [])
        intervals = results.get('zeroTraffic', [])
        zero = bool(intervals) and all(item['count'] == 0 for item in intervals)
        external = results.get('externalAttempts', [])
        complete = browser_exit == 0 and len(cases) == 8 and all(item['status'] == 'PASS' for item in cases)
        result = {'complete': complete, 'cases': len(cases), 'browser_exit': browser_exit,
                  'native_tree_unchanged': not differences, 'native_differences': differences,
                  'durable_state_unchanged': unchanged, 'zero_request_intervals': intervals,
                  'external_attempts': len(external), 'real_model_calls': 0, 'runner_error': error}
        (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return browser_exit or int(not complete or not unchanged or bool(differences) or not zero or bool(external))


if __name__ == '__main__':
    raise SystemExit(main())
