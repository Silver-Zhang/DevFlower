"""Reap a fresh, locally owned supervisor when its publication fails.

Never use the PID fallback for recovered processes: it is safe only before the
new Popen has been polled/reaped or handed to a reader. Published identities use
the normal boot/start/session checked group stop instead.
"""
import os
import signal
from orchestration.process import stop


def reap_unpublished(child, record=None):
    if record is not None:
        try: stop(record)
        except ProcessLookupError: pass
    elif child.returncode is None:
        # This unreaped child pins its PID, including if it has already exited.
        # Every caller creates a new session; do not touch the host's own group.
        if child.pid == os.getpgrp():
            raise RuntimeError('Supervisor child must own a separate process group')
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    child.wait(timeout=3)


def failure_record(publish):
    """Cleanup must already have happened; unavailable storage cannot undo it."""
    try: publish()
    except Exception: pass
