#!/usr/bin/env python3
"""Launch the isolated Agent workbench, optionally using an external Stable approval."""
import argparse
import sys
sys.dont_write_bytecode=True
from orchestration.common import Blocked
from orchestration.store import lease
from studio.workbench_runtime import WorkbenchRuntime
from studio.workbench_server import create_server


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('operator','operator-sha256','candidate-sha256','policy','policy-sha256'):p.add_argument('--'+name,required=True)
    p.add_argument('--port',type=int,default=0);a=p.parse_args()
    try:
        rt=WorkbenchRuntime(a.operator,a.operator_sha256,a.candidate_sha256,a.policy,a.policy_sha256)
        with lease(rt.storage/'workbench-service.lock'):
            server,token=create_server(rt,a.port)
            print(f'http://127.0.0.1:{server.server_port}/#'+token,flush=True)
            print(rt.release_identity()['channel']+' Agent workbench · legacy services unchanged',flush=True)
            server.serve_forever()
    except (Blocked,OSError,ValueError) as exc:print('BLOCKED: '+str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
