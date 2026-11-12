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
from v2.native_sessions import NativeSessions
from v2.session_operations import SessionOperations, load_write_profiles


def main(argv=None, *, ready=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True, help='new private V2 state directory, separate from allowed workspaces')
    parser.add_argument('--allow-root', action='append', required=True, help='explicit folder browsing boundary; repeatable')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true', help='print private local URL')
    parser.add_argument('--codex-cli', help='explicit installed Codex executable; default: discover installed CLI')
    parser.add_argument('--native-history-config', help='private opt-in JSON for read-only native history')
    parser.add_argument('--native-agent-profile',action='append',default=[],
                        help='approved native model/effort layer: codex:NAME or claude:NAME=/private/settings.json')
    parser.add_argument('--native-agent-home',action='append',default=[],
                        help='approved independent native HOME: codex:NAME=/private/home or claude:NAME=/private/home')
    parser.add_argument('--native-session-writes', help='private opt-in JSON for standalone native session writes')
    parser.add_argument('--copilot-catalog-config', help='private opt-in GitHub Copilot catalog profile')
    parser.add_argument('--git-delivery-target', action='append', default=[],
                        help='operator-approved local bare Git destination NAME=/absolute/repository.git')
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'linux':
            raise Invalid('V2 当前支持 Linux 本机环境')
        store = DraftStore(args.state_dir, args.allow_root)
        with lease(store.state / 'v2-service.lock'):
            native = NativeSessions.from_path(store, args.native_history_config) if args.native_history_config else None
            runtime = V2Runtime(store,args.codex_cli,native_profiles=args.native_agent_profile,native_homes=args.native_agent_home)
            if args.native_session_writes and native is None:
                raise Invalid('原生会话写入同时需要 --native-history-config。')
            operations = (SessionOperations(store, native, runtime.provider, load_write_profiles(args.native_session_writes))
                          if args.native_session_writes else None)
            catalog = None
            if args.copilot_catalog_config:
                from v2.copilot_catalog import CopilotCatalog
                catalog = CopilotCatalog.from_path(args.copilot_catalog_config, store, native_sessions=native)
            server, token = create_server(store, args.port, runtime=runtime, native_sessions=native, copilot_catalog=catalog,
                                          session_operations=operations, git_delivery_targets=args.git_delivery_target)
            with server:
                if ready is None:
                    url = 'http://127.0.0.1:' + str(server.server_port) + '/#' + token
                    if args.no_browser or not webbrowser.open(url):
                        print(url, flush=True)
                else:
                    ready(server.server_port, token, store)
                print('DevFlower V2 · Development · 提示词工作流；保存不启动 Agent', file=sys.stderr, flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (Blocked, Invalid, OSError, ValueError) as exc:
        print('无法启动 V2：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
