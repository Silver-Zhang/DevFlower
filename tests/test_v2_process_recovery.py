"""Actual local service/worker/native process faults, synthetic Provider only."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request,urlopen

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).resolve().parent)]
from orchestration.common import canonical,digest
from orchestration.store import process_identity
from v2.store import DraftStore
from v2.recovery import observe


class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);self.spaces=self.root/'spaces';self.spaces.mkdir();(self.spaces/'A').mkdir()
        self.services=[];self.workers=[]
        self.addCleanup(self.cleanup_owned)
    def cleanup_owned(self):
        for service in self.services:
            if service.poll() is None:service.terminate()
            service.wait(timeout=5)
            service.stdout.close();service.stderr.close()
        for identity in self.workers:
            if observe(identity)=='alive':
                os.kill(identity['pid'],signal.SIGKILL)
                self.until(lambda:observe(identity)!='alive')
    def start(self,mode='hold',port=0):
        argv=[sys.executable,'-B',str(Path(__file__).with_name('v2_process_fixture.py')),'server','--state',str(self.root/'state'),'--spaces',str(self.spaces),'--mode',mode,'--port',str(port)]
        proc=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);self.services.append(proc)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout,selectors.EVENT_READ);self.assertTrue(selector.select(10));url=proc.stdout.readline().strip()
        self.assertTrue(url.startswith('http://127.0.0.1:'));base,token=url.split('/#');self.base,self.token=base,token;return proc
    def request(self,path,data=None):
        req=Request(self.base+'/api/v2/'+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with urlopen(req,timeout=10) as response:return json.load(response)
    def until(self,fn):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if fn():return
            time.sleep(.02)
        self.fail('Observable barrier was not reached')
    def launch(self):
        workspace=self.request('workspaces/open',dict(path=str(self.spaces/'A')))['id']
        doc=dict(version=1,name='synthetic process recovery',nodes=[dict(id='AgentA',name='A',position=dict(x=1,y=2),provider='codex',model='fixture',effort='low',round_prompt='synthetic only')],edges=[])
        draft=self.request('drafts/save',dict(workspace=workspace,id=None,expected=None,document=doc))
        payload=dict(workspace=workspace,draft_id=draft['id'],revision=draft['revision'],intent_key='synthetic-restart-key',task='')
        snap=self.request('launch',payload);self.workers.append(snap['run']['worker']);return payload,snap
    def barrier(self,run):return self.root/'barriers'/(run+'-AgentA.json')
    def release(self,run):
        root=self.root/'releases';root.mkdir(exist_ok=True);(root/(run+'-AgentA')).touch()
    def snapshot(self,payload,run):return self.request('run?workspace='+payload['workspace']+'&id='+run)
    def counts(self):
        store=DraftStore(self.root/'state',[self.spaces])
        return dict(runs=len(store.db.list('run')),processes=len([p for p in store.db.list('native-step') if p['kind']=='PROCESS']),sends=len([a for a in store.db.list('activation') if a.get('input_sent_sha256')]))

    def test_service_process_restart_keeps_same_live_worker_then_completes(self):
        server=self.start();payload,first=self.launch();run=first['run']['id'];self.until(lambda:self.barrier(run).exists())
        before=self.counts();owner=first['run']['worker'];self.assertEqual(observe(owner),'alive')
        server.terminate();server.wait(timeout=5);self.assertEqual(observe(owner),'alive')
        self.start();snap=self.snapshot(payload,run);self.assertTrue(snap['worker_alive']);self.assertEqual(snap['run']['worker'],owner)
        self.assertEqual(self.request('launch',payload)['run']['id'],run);self.assertEqual(self.counts(),before)
        self.release(run);self.until(lambda:self.snapshot(payload,run)['state']=='COMPLETED')
        snap=self.snapshot(payload,run);self.assertTrue(next(n for n in snap['nodes'] if n['kind']=='agent')['receipt_verified']);self.assertEqual(self.counts(),before)

    def test_sent_worker_death_restart_is_unknown_and_never_replays(self):
        server=self.start();payload,first=self.launch();run=first['run']['id'];self.until(lambda:self.barrier(run).exists())
        self.until(lambda:self.counts()['sends']==1);before=self.counts();owner=first['run']['worker']
        os.kill(owner['pid'],signal.SIGKILL);self.until(lambda:observe(owner)!='alive');server.terminate();server.wait(timeout=5);self.start()
        snap=self.snapshot(payload,run);self.assertEqual(snap['state'],'RECOVERY_REQUIRED');self.assertEqual(snap['provider_outcome'],'unknown')
        self.assertEqual(self.request('launch',payload)['run']['id'],run);self.assertEqual(self.counts(),before)

    def test_worker_completion_crash_window_and_corrupt_control(self):
        for mode,expected in [('completion-before-record','COMPLETED'),('corrupt-completion','RECOVERY_REQUIRED')]:
            with self.subTest(mode=mode):
                # Separate state per case while retaining real process identities.
                self.start(mode);payload,first=self.launch();run=first['run']['id'];self.until(lambda:self.barrier(run).exists());self.release(run)
                self.until(lambda:json.loads(self.barrier(run).read_text())['stage']=='completion-before-record');self.until(lambda:observe(first['run']['worker'])!='alive')
                self.assertEqual(self.snapshot(payload,run)['state'],expected)
                self.services[-1].terminate();self.services[-1].wait(timeout=5)
                self.start(mode);self.assertEqual(self.snapshot(payload,run)['state'],expected)
                self.assertEqual(self.counts()['sends'],1)
                self.services[-1].terminate();self.services[-1].wait(timeout=5)
                # Next mode uses its own untouched directory.
                self.root=self.root/'next';self.root.mkdir();self.spaces=self.root/'spaces';self.spaces.mkdir();(self.spaces/'A').mkdir()

    def test_live_cooperative_cancel_and_no_send_positive_control(self):
        for mode,expected,outcome in [('before-send','CANCELLED','not_sent'),('hold','RECOVERY_REQUIRED','unknown')]:
            with self.subTest(mode=mode):
                self.start(mode);payload,first=self.launch();run=first['run']['id'];self.until(lambda:self.barrier(run).exists())
                unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
                try:
                    self.request('cancel',dict(workspace=payload['workspace'],run_id=run));self.until(lambda:observe(first['run']['worker'])!='alive')
                    snap=self.snapshot(payload,run);self.assertEqual(snap['state'],expected);self.assertEqual(snap['provider_outcome'],outcome)
                    self.assertIsNone(unrelated.poll());self.assertEqual(self.counts()['sends'],0 if mode=='before-send' else 1)
                finally:unrelated.kill();unrelated.wait(timeout=3)
                self.services[-1].terminate();self.services[-1].wait(timeout=5)
                self.root=self.root/'next';self.root.mkdir();self.spaces=self.root/'spaces';self.spaces.mkdir();(self.spaces/'A').mkdir()

if __name__=='__main__':unittest.main()
