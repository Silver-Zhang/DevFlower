#!/bin/bash
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
APP="$HOME/Applications/DevFlower.app"
chmod +x "$HERE/DevFlower.command"
mkdir -p "$HOME/Applications"
if ! command -v osacompile >/dev/null 2>&1; then
  echo "未找到 osacompile；可直接双击 launcher/macos/DevFlower.command 启动。" >&2
  exit 1
fi
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"python3 -B '$REPO/tools/devflower_launch.py'\""
echo "已安装：$APP"
echo "卸载：rm -rf \"$APP\""
