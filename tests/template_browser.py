"""Ordinary Chromium/HTTP optional-template regressions. Synthetic fixture only."""
import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import test_usable
from studio.usable_graph import template
from studio.usable_server import handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--artifacts', required=True)
    parser.add_argument('--node', default='node')
    parser.add_argument('--playwright-module', default='playwright')
    args = parser.parse_args()
    out = Path(args.artifacts).resolve();root = Path(__file__).resolve().parents[1]
    if out.is_relative_to(root) or root.is_relative_to(out):parser.error('Artifacts must be outside checkout')
    out.mkdir(mode=0o700)
    fixture = test_usable.RuntimeTests();fixture.setUp();runtime = fixture.runtime
    old = template('C');old['version'] = 1;old['name'] = 'Legacy immutable C'
    saved = runtime.db.save_draft(old,workspace=fixture.project['id'])
    with runtime.db.db() as db:
        original_revision=db.execute('SELECT data FROM revisions WHERE id=? AND revision=?',(saved['id'],saved['revision'])).fetchone()[0]
    token = 'template-browser-fixture-not-an-operator-authorization'
    server = ThreadingHTTPServer(('127.0.0.1',0),handler(runtime,token))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    (out/'access.json').write_text(json.dumps(dict(url=f'http://127.0.0.1:{server.server_port}/',token=token,
                                                  source=str(fixture.source),workspace=fixture.project['id'],legacy=saved)))
    try:
        result=subprocess.run([args.node,str(Path(__file__).with_suffix('.cjs')),str(out)],timeout=180,
            env=dict(os.environ,CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module))
        with runtime.db.db() as db:
            retained=db.execute('SELECT data FROM revisions WHERE id=? AND revision=?',(saved['id'],saved['revision'])).fetchone()
        if not retained or retained[0]!=original_revision:raise AssertionError('Legacy fixture revision was modified')
        (out/'legacy-revision-integrity.json').write_text(json.dumps(dict(original_revision=saved['revision'],workspace=fixture.project['id'],immutable_prior_revision_unchanged=True,browser_exit=result.returncode))+'\n')
        return result.returncode
    finally:
        server.shutdown();server.server_close();thread.join();fixture.doCleanups()


if __name__=='__main__':raise SystemExit(main())
