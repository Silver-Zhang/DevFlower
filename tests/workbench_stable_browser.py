"""Ordinary Chromium + HTTP, SYNTHETIC approvals only; no real model/release acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_workbench_stable import StableFixture, serve, ROOT


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts', required=True)
    p.add_argument('--chromium', required=True)
    p.add_argument('--playwright-module', required=True)
    p.add_argument('--node', default='node')
    p.add_argument('--old-bundle', help='Optional preserved old JS for the isolated branding regression')
    args = p.parse_args(); out = Path(args.artifacts).resolve()
    if out.is_relative_to(ROOT) or ROOT.is_relative_to(out): p.error('Artifacts must be outside source')
    out.mkdir(parents=True, exist_ok=False)
    owner = unittest.TestCase()
    try:
        stable = StableFixture(owner); stable.approval(); stable_rt = stable.launch()
        development = StableFixture(owner); development_rt = development.launch()
        # Saved workspace metadata deliberately lies about release identity. It
        # must never override verified runtime identity in either direction.
        for f, rt, label in ((stable, stable_rt, 'Development'), (development, development_rt, 'Stable')):
            rt.db.put('workspace', 'fixture-workspace', dict(id='fixture-workspace', path=str(f.workspace),
                execution_allowed=False, execution_reason='SYNTHETIC browser fixture; no execution',
                environment=label, stable_approved=label=='Stable'))
        if args.old_bundle:
            import studio.workbench_server as server
            old_static = out/'preserved-old-static'; old_static.mkdir()
            for name in ('workbench.html', 'workbench.css'):
                (old_static/name).write_bytes((server.STATIC/name).read_bytes())
            (old_static/'workbench.js').write_bytes(Path(args.old_bundle).read_bytes())
            static_patch = patch.object(server, 'STATIC', old_static); static_patch.start()
            owner.addCleanup(static_patch.stop)
        su, st = serve(owner, stable_rt); du, dt = serve(owner, development_rt)
        # A test-only endpoint can mutate the fixture file, never a product API.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Tamper(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                stable.record.write_bytes(stable.record.read_bytes()+b' ')
                self.send_response(200); self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Tamper)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        owner.addCleanup(thread.join); owner.addCleanup(server.server_close); owner.addCleanup(server.shutdown)
        access = dict(stable=dict(url=su, token=st), development=dict(url=du, token=dt),
            tamper='http://127.0.0.1:'+str(server.server_port), candidate=stable.candidate,
            fixture='SYNTHETIC approval, not a release acceptance')
        (out/'access.json').write_text(json.dumps(access)); (out/'access.json').chmod(0o600)
        return subprocess.run([args.node, str(Path(__file__).with_suffix('.cjs')), str(out)],
            env=dict(os.environ, CHROMIUM=args.chromium, PLAYWRIGHT_MODULE=args.playwright_module), timeout=120).returncode
    finally:
        owner.doCleanups()


if __name__ == '__main__': raise SystemExit(main())
