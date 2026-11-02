#!/bin/bash
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
exec python3 -B "$REPO/tools/devflower_launch.py" "$@"
