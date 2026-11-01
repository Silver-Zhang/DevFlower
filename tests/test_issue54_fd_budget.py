"""The protected-tree scan must not die with EMFILE on a normal install."""
import resource
import tempfile
import unittest
from pathlib import Path
from studio.native_codex_exec import raise_fd_soft_limit, check_protected_hardlinks
from orchestration.common import Blocked


class ProtectedScanDescriptorBudgetTests(unittest.TestCase):
 def test_soft_limit_is_raised_or_reported(self):
  soft_before, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
  now = raise_fd_soft_limit()
  soft_after, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
  self.assertGreaterEqual(soft_after, soft_before)
  if now is not None:
   self.assertGreaterEqual(now, soft_before)
   if hard == resource.RLIM_INFINITY or hard >= 65536:
    self.assertGreaterEqual(soft_after, 65536)

 def test_a_deep_tree_inside_a_small_soft_limit_still_completes(self):
  # 300 directories with the old 1024 soft limit is where EMFILE appeared.
  with tempfile.TemporaryDirectory() as tmp:
   root = Path(tmp) / 'assets'
   current = root
   for index in range(300):
    current = current / ('d%03d' % index)
    current.mkdir(parents=True)
    (current / 'file.txt').write_text('x')
   soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
   try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(soft, 256), hard))
    check_protected_hardlinks([str(root)])
   finally:
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))

if __name__ == '__main__':
 unittest.main()
