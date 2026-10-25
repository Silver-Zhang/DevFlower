"""Fixed subprocess entry: inherit stdio, watch the owning server, no shell."""
from pathlib import Path
import os, signal, subprocess, sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devflower_native_terminal import bind_parent_lifetime

if __name__=='__main__':
    bind_parent_lifetime(int(sys.argv[1]),sys.argv[2])
    def terminate(*_):raise SystemExit(143)
    signal.signal(signal.SIGTERM,terminate)
    args=[sys.argv[3],'--headless','--stdio','--no-auto-update','--log-level','none',
          '--no-custom-instructions','--disable-builtin-mcps']
    child=subprocess.Popen(args)
    try:sys.exit(child.wait())
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
