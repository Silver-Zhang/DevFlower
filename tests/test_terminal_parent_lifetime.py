"""Actual process/thread lifetime counterexamples; no native model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'

class TerminalParentLifetimeTests(unittest.TestCase):
    def test_request_thread_exit_keeps_cli_but_server_exit_stops_owned_group(self):
        child_code=(f'import sys,os,time;sys.path.insert(0,{str(TOOLS)!r});'
                    'from devflower_native_terminal import bind_parent_lifetime;'
                    'bind_parent_lifetime(os.getppid());print("ready",flush=True);time.sleep(20)')
        parent_code=f'''import subprocess,sys,threading,time,json
children=[]
def request():
 p=subprocess.Popen([sys.executable,'-B','-c',{child_code!r}],stdout=subprocess.PIPE,text=True,start_new_session=True)
 children.append(p)
 assert p.stdout.readline().strip()=='ready'
t=threading.Thread(target=request);t.start();t.join();time.sleep(.4)
print(json.dumps(dict(pid=children[0].pid,survived=children[0].poll() is None)),flush=True)
sys.stdin.read(1)
'''
        parent=subprocess.Popen([sys.executable,'-B','-c',parent_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        pid=None
        try:
            result=json.loads(parent.stdout.readline());pid=result['pid']
            self.assertTrue(result['survived'],'A completed HTTP thread must not terminate the native CLI')
            parent.stdin.close();self.assertEqual(parent.wait(timeout=4),0)
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                try:state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
                except FileNotFoundError:break
                if state=='Z':break
                time.sleep(.05)
            else:self.fail('Owned terminal survived disappearance of pinned server')
        finally:
            if parent.poll() is None:parent.terminate();parent.wait(timeout=4)
            if pid:
                try:os.killpg(pid,15)
                except ProcessLookupError:pass
            for stream in [parent.stdin,parent.stdout,parent.stderr]:
                if stream and not stream.closed:stream.close()

    def test_reused_or_wrong_parent_identity_is_rejected_before_cli(self):
        code=(f'import sys,os;sys.path.insert(0,{str(TOOLS)!r});'
              'from devflower_native_terminal import bind_parent_lifetime;'
              'bind_parent_lifetime(os.getppid(),"not-the-server-start")')
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,start_new_session=True,timeout=5)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('监督器身份未确认',result.stderr)
