"""Real V2 launcher/HTTP/Chromium editor checks; no Provider fixtures or inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import re
import subprocess
import sys
import time
import zipfile


def redact_trace(path):
    """Keep all failure events/screenshots; remove disposable bearer capabilities."""
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    record = path.with_suffix('.redaction.json')
    if record.exists():
        previous = json.loads(record.read_text())
        if previous.get('redacted_sha256') == original:
            return previous
    temporary = path.with_suffix('.redacting')
    counts = {'bearer': 0, 'initial_fragment': 0}
    patterns = [(re.compile(rb'Bearer[ \t]+[A-Za-z0-9_.~+/=-]{20,}', re.I), b'Bearer [REDACTED_CAPABILITY]', 'bearer'),
                (re.compile(rb'(http://127\.0\.0\.1:[0-9]+/)#[A-Za-z0-9_-]{20,}'), rb'\1', 'initial_fragment')]
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, 'w') as destination:
        for info in source.infolist():
            data = source.read(info.filename)
            for pattern, replacement, key in patterns:
                data, count = pattern.subn(replacement, data); counts[key] += count
            destination.writestr(info, data)
    temporary.replace(path)
    summary = {'method': 'deterministic uncompressed ZIP entry replacement; steps/results/screenshots retained',
               'original_sha256': original, 'redacted_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'replacements': counts, 'original_capability_values_retained': False}
    record.write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main(driver=None):
    p = argparse.ArgumentParser()
    p.add_argument('--artifacts', required=True)
    p.add_argument('--chromium', required=True)
    p.add_argument('--playwright-module', required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.artifacts).resolve()
    if out.is_relative_to(root) or root.is_relative_to(out):
        p.error('Evidence must remain outside product source')
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    spaces = out / 'workspaces'; spaces.mkdir()
    for name in ['A', 'B']:
        project = spaces / name
        (project / 'nested').mkdir(parents=True)
        (project / 'nested' / '空目录').mkdir()
        (project / 'constructor').mkdir()
        (project / 'constructor' / 'plain.txt').write_text('ordinary directory name', encoding='utf-8')
        (project / 'nested' / 'readme.txt').write_text(f'{name} 项目 <b>字面文本</b>\n', encoding='utf-8')
        (project / 'quick.txt').write_text(f'{name} quick', encoding='utf-8')
        (project / 'bad.bin').write_bytes(b'binary\0')
        (project / '.codex').mkdir()
        (project / '.codex' / 'auth.json').write_text('synthetic read sentinel', encoding='utf-8')
    log = (out / 'server-stderr.txt').open('w')
    server = subprocess.Popen([sys.executable, '-B', str(root / 'tools/devflower_v2.py'),
        '--state-dir', str(out / 'state'), '--allow-root', str(spaces),
        '--codex-cli', str(out / 'no-codex-fixture'), '--no-browser'],
        cwd=root, stdout=subprocess.PIPE, stderr=log, text=True)
    sel = selectors.DefaultSelector(); sel.register(server.stdout, selectors.EVENT_READ)
    try:
        if not sel.select(20): raise RuntimeError('Launcher did not return a private local URL')
        url = server.stdout.readline().strip()
        if not url.startswith('http://127.0.0.1:') or '/#' not in url:
            raise RuntimeError('Launcher exited without a usable local URL')
        env = dict(os.environ, V2_URL=url, V2_WORKSPACES=str(spaces), CHROMIUM=args.chromium,
                   PLAYWRIGHT_MODULE=args.playwright_module)
        command = ['node', str(driver or Path(__file__).with_suffix('.cjs')), str(out)]
        result = subprocess.run(command, cwd=root, env=env, timeout=240)
        print(json.dumps({'browser_exit': result.returncode, 'artifacts': str(out)}), flush=True)
        return result.returncode
    finally:
        sel.close(); server.terminate()
        try: server.wait(timeout=5)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        log.close()
        for trace in out.glob('*.zip'):
            redact_trace(trace)


if __name__ == '__main__': raise SystemExit(main())
