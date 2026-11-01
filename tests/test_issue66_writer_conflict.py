"""Synthetic stderr conflict; only the owned contender may be stopped."""
import subprocess,sys,tempfile,unittest
from orchestration.common import Blocked

class WriterConflictTests(unittest.TestCase):
 def test_writer_conflict_stops_only_failed_contender_within_budget(self):
  import time
  from orchestration.process import execute
  from studio.local_codex_adapter import writer_conflict_observer
  holder=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=True)
  self.addCleanup(lambda:holder.poll() is None and (holder.terminate(),holder.wait(timeout=3)))
  with tempfile.TemporaryDirectory() as d:
   started=time.monotonic()
   with self.assertRaisesRegex(Blocked,'另一写入者'):
    execute([sys.executable,'-c','import sys,time;sys.stderr.write("thread-store conflict: already has an active writer\\n");sys.stderr.flush();time.sleep(20)'],d,d,timeout=10,isolate=False,on_stderr=writer_conflict_observer())
   self.assertLess(time.monotonic()-started,3);self.assertIsNone(holder.poll(),'Existing writer must remain alive')
