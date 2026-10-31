#!/usr/bin/env python3
"""Fixed stdio bridge entrypoint; descriptor is supplied by the host."""
import sys
sys.dont_write_bytecode=True
from studio.managed_child_bridge import main
main()
