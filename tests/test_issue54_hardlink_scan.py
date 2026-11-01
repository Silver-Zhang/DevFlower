"""Synthetic isolated descriptor-limit and hard-link regression."""
import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from orchestration.common import Blocked

class HardlinkScanTests(unittest.TestCase):
 def test_isolated_hardlink_scan_handles_low_parent_fd_limit(self):
  from studio.native_codex_exec import check_protected_hardlinks_isolated
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'authority';root.mkdir()
   for n in range(1200):(root/str(n)).mkdir()
   secret=root/'s';secret.write_text('synthetic')
   program='import resource,sys;sys.path.insert(0,sys.argv[1]);from studio.native_codex_exec import check_protected_hardlinks_isolated;resource.setrlimit(resource.RLIMIT_NOFILE,(1024,resource.getrlimit(resource.RLIMIT_NOFILE)[1]));check_protected_hardlinks_isolated([sys.argv[2]]);assert resource.getrlimit(resource.RLIMIT_NOFILE)[0]==1024'
   r=subprocess.run([sys.executable,'-B','-I','-c',program,str(Path(__file__).resolve().parents[1]/'tools'),str(root)],capture_output=True,timeout=35)
   self.assertEqual(r.returncode,0,r.stderr.decode())
   os.link(secret,Path(d)/'alias')
   with self.assertRaisesRegex(Blocked,'硬链接'):check_protected_hardlinks_isolated([str(root)])

