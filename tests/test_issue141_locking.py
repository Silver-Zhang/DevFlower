"""Portable lock abstraction: POSIX flock and Windows msvcrt (#141)."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


class WindowsLockTests(unittest.TestCase):
    def test_windows_branch_uses_msvcrt_byte_lock(self):
        calls = []
        fake = types.ModuleType('msvcrt')
        fake.LK_LOCK, fake.LK_NBLCK, fake.LK_UNLCK = 1, 2, 3
        fake.locking = lambda fd, mode, count: calls.append((fd, mode, count))
        with patch.object(locking.os, 'name', 'nt'), \
             patch.object(locking.os, 'lseek', lambda *args: 0), \
             patch.dict(sys.modules, {'msvcrt': fake}):
            locking.lock(7, exclusive=True, blocking=False)
            locking.unlock(7)
        self.assertEqual(calls, [(7, 2, 1), (7, 3, 1)])


if __name__ == '__main__':
    unittest.main()
