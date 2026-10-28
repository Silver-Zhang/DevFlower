"""Owned synthetic diagnostic children, without a CLI or provider."""
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from orchestration.common import Blocked
from studio.native_codex_exec_probe import _run_command


class ProbeLifetimeTests(unittest.TestCase):
    def test_timeout_and_cancel_collect_child(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                with self.assertRaises(Blocked):
                    _run_command([sys.executable, '-c', 'import time; time.sleep(30)'], cwd='.', env=os.environ.copy(),
                                 deadline=time.monotonic()+.15, cancelled=lambda:cancelled)

    def test_parent_death_terminates_owned_direct_child_only(self):
        code = ("import os,sys,time,subprocess\n"
                "from studio.native_codex_exec_probe import _spawn\n"
                "p=_spawn([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "print(p.pid,flush=True)\n"
                "p.wait()\n")
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        parent=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=os.environ.copy())
        child=None
        try:
            child=int(parent.stdout.readline())
            os.kill(parent.pid,signal.SIGKILL);parent.wait(timeout=3)
            end=time.monotonic()+3
            while time.monotonic()<end:
                try:
                    state=Path('/proc',str(child),'stat').read_text().split(') ',1)[1][0]
                except FileNotFoundError:break
                if state=='Z':break
                time.sleep(.02)
            else:self.fail('owned child remained alive after parent death')
            self.assertIsNone(unrelated.poll())
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            unrelated.kill();unrelated.wait()
            parent.stdout.close();parent.stderr.close()

    def test_parent_normal_exit_terminates_owned_direct_child(self):
        code = ("import subprocess,sys\n"
                "from studio.native_codex_exec_probe import _spawn\n"
                "p=_spawn([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "print(p.pid,flush=True)\n")
        parent=subprocess.run([sys.executable,'-c',code],capture_output=True,env=os.environ.copy(),timeout=3,check=True)
        child=int(parent.stdout);end=time.monotonic()+3
        while time.monotonic()<end:
            try:state=Path('/proc',str(child),'stat').read_text().split(') ',1)[1][0]
            except FileNotFoundError:break
            if state=='Z':break
            time.sleep(.02)
        else:self.fail('owned child remained alive after parent exit')

    def test_normal_command(self):
        result=_run_command([sys.executable,'-c','print("ok")'],cwd='.',env=os.environ.copy(),
                            deadline=time.monotonic()+3,cancelled=lambda:False)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,b'ok\n')

    def test_nonzero_and_repeated_collection_do_not_leak_descriptors(self):
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(4):
            result=_run_command([sys.executable,'-c','raise SystemExit(7)'],cwd='.',env=os.environ.copy(),
                                deadline=time.monotonic()+3,cancelled=lambda:False)
            self.assertEqual(result.returncode,7)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)
