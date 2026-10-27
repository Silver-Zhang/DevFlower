#!/usr/bin/env python3
"""Display the same supervised native CLI in a local terminal; Ctrl-] detaches.

Observes by default. --takeover explicitly transfers input; it never starts a
second Codex/Claude process or copies/resumes its history. Use the exact command
from DevFlower's native terminal, with --takeover to type in this terminal.
"""
import argparse
import codecs
import os
from pathlib import Path
import select
import sys
import termios
import tty

sys.dont_write_bytecode = True
from orchestration.common import Blocked
from studio.native_attach import AttachClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('descriptor', type=Path)
    parser.add_argument('--takeover', action='store_true')
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Blocked('请在本机交互终端连接；不接受管道中的自动输入')
    client = AttachClient(args.descriptor)
    original = termios.tcgetattr(sys.stdin.fileno())
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    after = 0
    try:
        row = client.request('observe')
        generation, sequence = row.get('input_generation', 0), row['input_sequence']
        owner = 'local-terminal' if args.takeover else None
        if args.takeover:
            row = client.request('claim', generation=generation)
            generation, sequence = row['input_generation'], row['input_sequence']
        print(f"{client.hello['provider']} · 原生会话 {client.hello['session_id']} · "
              f"{'本机接管' if owner else '只读观察'}；Ctrl-] 断开显示，保持原生进程。", flush=True)
        tty.setraw(sys.stdin.fileno())
        while True:
            row = client.request('observe', after=after)
            if row['reset']: os.write(sys.stdout.fileno(), b'\x1b[0m\x1b[2J\x1b[H')
            output = row['output'].encode()
            if output: os.write(sys.stdout.fileno(), output)
            after = row['next']
            if not row['process_alive'] or row['state'] != 'RUNNING': break
            writable = owner and row.get('input_owner') == owner and row.get('input_generation') == generation
            if owner and not writable:
                os.write(sys.stdout.fileno(), b'\r\n[Input transferred; observing only. Ctrl-] detaches.]\r\n')
                owner = None
            if select.select([sys.stdin], [], [], .15)[0]:
                raw = os.read(sys.stdin.fileno(), 1024)
                if not raw or b'\x1d' in raw: break
                if writable:
                    # A failed/ambiguous request ends this client; never replay.
                    text = decoder.decode(raw)
                    if text:
                        sequence += 1
                        client.request('input', generation=generation, sequence=sequence, text=text)
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
        client.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, Blocked) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
