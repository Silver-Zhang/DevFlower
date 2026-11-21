"""Real V2 HTTP, worker, and Chromium inspection of a deterministic run."""
from pathlib import Path

from workbench_editor_browser import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).with_suffix('.cjs')))
