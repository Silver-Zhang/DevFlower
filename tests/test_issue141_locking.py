"""File locks are POSIX-only (#141).

No Windows lock branch is shipped: Windows is refused before any lock is
taken, so a tested-but-unreachable ``msvcrt`` path would be misleading.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'tools'), str(Path(__file__).resolve().parent)]

from orchestration import locking


class PosixLockTests(unittest.TestCase):
    def test_exclusive_lock_is_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lock'
            first = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            second = os.open(path, os.O_RDWR)
            try:
                locking.lock(first, exclusive=True, blocking=False)
                with self.assertRaises(BlockingIOError):
                    locking.lock(second, exclusive=True, blocking=False)
                locking.unlock(first)
                locking.lock(second, exclusive=True, blocking=False)
                locking.unlock(second)
            finally:
                os.close(first)
                os.close(second)

    def test_shared_lock_allows_two_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lock'
            first = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            second = os.open(path, os.O_RDWR)
            try:
                locking.lock(first, exclusive=False, blocking=False)
                locking.lock(second, exclusive=False, blocking=False)
                locking.unlock(first)
                locking.unlock(second)
            finally:
                os.close(first)
                os.close(second)


if __name__ == '__main__':
    unittest.main()
