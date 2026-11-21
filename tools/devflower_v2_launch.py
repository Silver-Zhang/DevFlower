#!/usr/bin/env python3
"""Compatibility entry for an existing desktop shortcut."""
import sys
sys.dont_write_bytecode = True

from devflower_desktop import main


if __name__ == '__main__':
    raise SystemExit(main())
