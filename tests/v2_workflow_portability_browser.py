"""Real V2 launcher, Chromium and scoped Bubblewrap Tool process."""
from pathlib import Path

from v2_browser import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).with_suffix('.cjs')))
