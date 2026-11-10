"""Deterministic approval and checkpoint recovery through real V2 HTTP/Chromium."""
from pathlib import Path

from v2_browser import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).with_suffix('.cjs')))
