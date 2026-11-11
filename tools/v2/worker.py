"""Detached, one-owner V2 scheduler worker with a durable entry handshake."""
import argparse
import os
from pathlib import Path
import select
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.common import Blocked
from orchestration.store import lease
from orchestration.store import process_identity
from studio.workbench_scheduler import Scheduler
from v2 import worker_facts
from v2.store import DraftStore
from v2.runtime import V2Runtime


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--state-dir', required=True); p.add_argument('--allow-root', action='append', required=True)
    p.add_argument('--run', required=True); p.add_argument('--codex-cli')
    p.add_argument('--native-agent-home', action='append', default=[])
    p.add_argument('--start-fd', type=int, required=True)
    a = p.parse_args()
    try:
        if not select.select([a.start_fd], [], [], 10)[0] or os.read(a.start_fd, 16) != b'GO\n': return 1
    finally: os.close(a.start_fd)
    store = DraftStore(a.state_dir, a.allow_root)
    with lease(store.state / (a.run + '.worker.lock')):
        row = store.db.get('run', a.run)
        if row['state'] != 'QUEUED': return 1
        own = process_identity(os.getpid())
        if not own or not row.get('worker') or any(row['worker'].get(k) != own.get(k) for k in ('host','boot_id','pid','start','pgid','sid')):
            return 1
        try:
            if worker_facts.versioned(row):
                worker_facts.enter_worker(store.db, a.run, own)
        except Blocked:
            return 1
        rt = V2Runtime(store, a.codex_cli, worker_run=a.run, native_homes=a.native_agent_home)
        if row.get('designer_runtime') == 1:
            from v2.designer_scheduler import DesignerScheduler
            scheduler = DesignerScheduler
        else:
            scheduler = Scheduler
        scheduler(rt, a.run, executor=rt.native_turn).run_graph()
    return 0


if __name__ == '__main__': raise SystemExit(main())
