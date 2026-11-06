#!/usr/bin/env python3
"""Start the isolated Development V2 editor and explicitly launched Agent chains."""
import argparse
from pathlib import Path
import sys
import webbrowser
sys.dont_write_bytecode = True
from orchestration.store import lease
from orchestration.common import Blocked
from studio.graph import Invalid
from v2.store import DraftStore
from v2.server import create_server
from v2.runtime import V2Runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True, help='new private V2 state directory, separate from allowed workspaces')
    parser.add_argument('--allow-root', action='append', required=True, help='explicit folder browsing boundary; repeatable')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true', help='print private local URL')
    parser.add_argument('--codex-cli', help='explicit installed Codex executable; default: discover installed CLI')
    args = parser.parse_args()
    try:
        if sys.platform != 'linux':
            raise Invalid('V2 当前支持 Linux 本机环境')
        store = DraftStore(args.state_dir, args.allow_root)
        with lease(store.state / 'v2-service.lock'):
            runtime = V2Runtime(store,args.codex_cli)
            server, token = create_server(store, args.port,runtime=runtime)
            url = 'http://127.0.0.1:' + str(server.server_port) + '/#' + token
            if args.no_browser or not webbrowser.open(url):
                print(url, flush=True)
            print('DevFlower V2 · Development · 提示词工作流；保存不启动 Agent', file=sys.stderr, flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (Blocked, Invalid, OSError, ValueError) as exc:
        print('无法启动 V2：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
