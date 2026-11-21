#!/usr/bin/env python3
"""Compatibility entry for older local workbench commands."""
import sys
sys.dont_write_bytecode = True

from devflower_workbench_service import main


if __name__ == '__main__':
    raise SystemExit(main())
