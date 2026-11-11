"""Trusted one-operation child for creating an approved workspace output."""
import errno
import os
from pathlib import Path
import signal
import sys

UNAVAILABLE = 125


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from orchestration.sandbox import LIBC, restrict

    owner, root, parent, temporary = map(int, sys.argv[1:5])
    name = sys.argv[5]
    if (owner <= 1 or os.getppid() != owner
            or LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != owner
            or LIBC.prctl(4, 0, 0, 0, 0)
            or name in ('', '.', '..') or '/' in name or not name.endswith('.json')):
        return UNAVAILABLE
    try:
        # The kernel checks the current ancestry of the destination on linkat,
        # including when another writer moved the inherited parent directory.
        restrict(read=('/proc/self/fd',), write_fds=(root,), deny_network=True)
    except OSError:
        return UNAVAILABLE
    try:
        os.link(f'/proc/self/fd/{temporary}', name, dst_dir_fd=parent, follow_symlinks=True)
    except FileExistsError:
        return errno.EEXIST
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM, errno.EXDEV):
            return errno.EACCES
        raise
    return 0


if __name__ == '__main__':
    sys.exit(main())
