"""Chromium and real V2 Git workspace/HTTP with synthetic platform observations."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_v2_git_platform import FixturePlatform, git, initialize_repo
from v2_browser import redact_trace
from v2.server import create_server
from v2.store import DraftStore


def main():
    parser = argparse.ArgumentParser()
    for key in ('artifacts','chromium','playwright-module'):
        parser.add_argument('--'+key,required=True)
    args = parser.parse_args()
    out = Path(args.artifacts).resolve()
    out.mkdir(parents=True,mode=0o700,exist_ok=False)
    roots = out/'workspaces'
    origins = {'A':'https://github.com/example/one.git','B':'git@gitlab.com:group/sub/two.git'}
    for key,origin in origins.items():
        initialize_repo(roots/key,origin)
    heads = {key:git(roots/key,'rev-parse','HEAD') for key in origins}
    store = DraftStore(out/'state',[roots])
    with patch('v2.git_platform_adapters.adapter_for',FixturePlatform):
        server,token = create_server(store)
        Thread(target=server.serve_forever,daemon=True).start()
        env = dict(os.environ,V2_URL=f'http://127.0.0.1:{server.server_port}/#{token}',
                   V2_WORKSPACES=str(roots),CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module)
        try:
            result = subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(out)],env=env,timeout=120)
            tasks = store.db.list('git-review-task')
            preserved = all(git(roots/key,'rev-parse','HEAD')==head and
                            git(roots/key,'config','remote.origin.url')==origins[key] for key,head in heads.items())
            print(json.dumps(dict(browser_exit=result.returncode,local_review_tasks=len(tasks),
                                  repository_preserved=preserved,synthetic_platform=True,remote_writes=0)))
            return int(bool(result.returncode or len(tasks)!=1 or not preserved))
        finally:
            server.shutdown()
            server.server_close()
            for trace in out.glob('*.zip'):
                redact_trace(trace)


if __name__=='__main__':
    raise SystemExit(main())
