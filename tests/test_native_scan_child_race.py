"""Controlled race inside the real isolated scan subprocess; synthetic files only."""
import os,subprocess,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from orchestration.common import Blocked
from studio.workspace_access import native_workspace_view,native_workspace_hash

class IsolatedScanRaceTests(unittest.TestCase):
 def test_child_scan_detects_link_and_root_replacement(self):
  for change in ('unchanged','symlink','root','hardlink'):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
    parent=Path(folder);root=parent/'workspace';root.mkdir();storage=root/'private';storage.mkdir();(storage/'state').write_text('SYNTHETIC')
    rt=SimpleNamespace(storage=storage,native=storage/'native',operator_file=storage/'operator',policy_file=storage/'policy',protected=[storage],operator={'profiles':{},'native_local':{}},policy={})
    view=native_workspace_view(rt,str(root));ready=parent/'child-ready';release=parent/'parent-release';real_run=subprocess.run;out=[]
    def instrument(argv,**kwargs):
     args=list(argv);program=args[args.index('-c')+1];needle=' check_protected_hardlinks(json.loads(sys.argv[2]))'
     if needle not in program:raise AssertionError('unexpected isolated scan program')
     hook=(' import os,time\n from pathlib import Path\n original_walk=os.fwalk\n count=0\n def barrier_walk(top,*a,**kw):\n  global count\n  if str(top)=='+repr(str(storage))+':\n   count+=1\n   if count==2:\n    Path('+repr(str(ready))+').touch()\n    until=time.monotonic()+8\n    while not Path('+repr(str(release))+').exists():\n     if time.monotonic()>until:raise TimeoutError("synthetic barrier timed out")\n     time.sleep(.01)\n  yield from original_walk(top,*a,**kw)\n os.fwalk=barrier_walk\n'+needle)
     args[args.index('-c')+1]=program.replace(needle,hook);return real_run(args,**kwargs)
    def scan():
     try:out.append(native_workspace_hash(rt,view))
     except BaseException as e:out.append(e)
    with patch('subprocess.run',side_effect=instrument):
     worker=threading.Thread(target=scan);worker.start()
     try:
      until=time.monotonic()+10
      while not ready.exists() and worker.is_alive() and time.monotonic()<until:time.sleep(.01)
      self.assertTrue(ready.exists(),'isolated subprocess never reached second-scan barrier')
      if change=='unchanged':pass
      elif change=='symlink':(storage/'unexpected').symlink_to('/synthetic/target')
      elif change=='hardlink':os.link(storage/'state',root/'synthetic-alias')
      else:storage.rename(root/'moved');storage.mkdir()
     finally:release.touch();worker.join(15)
    self.assertFalse(worker.is_alive());self.assertEqual(len(out),1)
    if change=='unchanged':self.assertIsInstance(out[0],str);self.assertEqual(len(out[0]),64)
    else:self.assertIsInstance(out[0],Blocked,str(out));self.assertIn('受保护目录在检查期间变化',str(out[0]));print('SYNTHETIC_SCAN_REFUSAL',change,str(out[0]))
