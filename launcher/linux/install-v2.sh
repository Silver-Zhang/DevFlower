#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1-}" = upgrade ]; then
  command=$1
  shift
else
  command=install
fi
exec python3 -B "$HERE/../../tools/devflower_v2_launch.py" "$command" "$@"
