#!/usr/bin/env python3
"""One-click launcher for the private local Development workbench.

It reuses devflower_start.py for every start, security and lifecycle decision:
the launcher only resolves the state directory, checks whether the recorded
service is already answering, and otherwise forwards the start arguments.
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def parser():
    p = argparse.ArgumentParser(description='启动 DevFlower Development 本机工作台')
    p.add_argument('--port', type=int, default=0, help='监听端口；0 表示自动选择')
    p.add_argument('--state-dir', help='私有状态目录；默认与 devflower_start.py 一致')
    p.add_argument('--no-browser', action='store_true', help='打印私有 URL，不打开浏览器')
    p.add_argument('--print-url', action='store_true', help='打印私有 URL（不打开浏览器）')
    return p


def start_module():
    """Import the existing starter lazily so the failure is a readable message."""
    try:
        import devflower_start
    except ImportError as exc:
        raise RuntimeError('无法加载 devflower_start.py：'+str(exc)+
                           '；当前平台需要 POSIX 文件锁支持') from None
    return devflower_start


def state_path(args, start):
    return start.location(argparse.Namespace(state_dir=args.state_dir), Path.home())


def running_url(state, start):
    """Return the recorded private URL only when that service answers as Development."""
    try:
        receipt = json.loads(start.read(state/'runtime'/'service.json', confidential=True))
        if receipt.get('kind') != start.KIND or receipt.get('host') != '127.0.0.1':
            return None
        port = receipt.get('port')
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        process = receipt.get('process')
        if not isinstance(process, dict) or not start.alive(process):
            return None
        token = start.read(state/'runtime'/'workbench-capability', confidential=True).decode('utf-8').strip()
        if not token or len(token) > 500:
            return None
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', '/api/status', headers={'Authorization': 'Bearer '+token})
            response = connection.getresponse()
            value = json.loads(response.read(2000001))
        finally:
            connection.close()
        if response.status != 200 or value.get('release', {}).get('channel') != 'Development':
            return None
        return 'http://127.0.0.1:'+str(port)+'/#'+token
    except Exception:
        # A missing, stale or unreadable record means "not running"; the starter
        # then produces its own controlled reason instead of guessing here.
        return None


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        start = start_module()
        state = state_path(args, start)
        url = running_url(state, start)
        if url:
            if args.print_url or args.no_browser:
                print(url, flush=True)
            else:
                if not webbrowser.open(url, new=2):
                    print('无法自动打开浏览器；请手动打开以下私有地址：', flush=True)
                    print(url, flush=True)
            return 0
        forwarded = ['--host', '127.0.0.1', '--port', str(args.port)]
        if args.state_dir:
            forwarded += ['--state-dir', args.state_dir]
        if args.no_browser or args.print_url:
            forwarded.append('--no-browser')
        return start.main(forwarded)
    except Exception as exc:
        from orchestration.common import Blocked
        message = str(exc) if isinstance(exc, Blocked) else '启动入口无法继续：'+str(exc)
        print('启动失败：'+message, file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
