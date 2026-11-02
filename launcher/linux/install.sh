#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DEST"
sed "s|@REPO@|$REPO|g" "$HERE/DevFlower.desktop" > "$DEST/DevFlower.desktop"
chmod 0755 "$DEST/DevFlower.desktop"
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DEST/DevFlower.desktop" || true
fi
echo "已安装：$DEST/DevFlower.desktop"
echo "从应用菜单打开 DevFlower，或运行：python3 -B \"$REPO/tools/devflower_launch.py\""
