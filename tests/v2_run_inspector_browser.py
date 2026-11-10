"""Real V2 HTTP, worker, and Chromium inspection of a deterministic run."""
from pathlib import Path

from v2_browser import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).with_suffix('.cjs')))
