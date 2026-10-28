"""Host-only real abstract socket and stdio bridge, synthetic child result."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from orchestration.common import canonical
from orchestration.store import process_identity
from studio.managed_children import OwnedEndpoint

class ManagedSocketTests(unittest.TestCase):
 def test_stdio_bridge_to_authenticated_host_endpoint(self):
  with tempfile.TemporaryDirectory() as folder:
   calls=[]
   class Owner:
    rt=SimpleNamespace(storage=Path(folder));parent=process_identity(os.getpid())
    deadline=__import__('time').monotonic()+30
    def call(self,slot,task):calls.append((slot,task));return dict(id='fixture-child',slot=slot,state='COMPLETED',result=dict(reply='synthetic'))
    def close(self):return dict(status='closed')
   endpoint=OwnedEndpoint(Owner())
   try:
    program='from studio.managed_child_bridge import main;main()'
    requests=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),dict(jsonrpc='2.0',id=2,method='tools/list'),dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='delegated_readonly',arguments=dict(slot=1,task='inspect')))]
    proc=subprocess.run([sys.executable,'-B','-c',program,str(endpoint.path)],input=b''.join(canonical(x)+b'\n' for x in requests),capture_output=True,timeout=10)
    self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'));rows=[json.loads(x) for x in proc.stdout.splitlines()]
    self.assertEqual(len(rows),3);self.assertFalse(rows[-1]['result']['isError']);self.assertEqual(calls,[(1,'inspect')])
   finally:endpoint.close();endpoint.thread.join(3)
   self.assertFalse(endpoint.thread.is_alive());self.assertEqual(endpoint.path.read_bytes(),b'')
