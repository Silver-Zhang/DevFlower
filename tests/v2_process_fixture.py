"""Test-only real local processes with a synthetic Provider; never Codex/account.

Modes hold after observable stdin delivery, or crash after a coherent completion
artifact. Uses production Scheduler, process.execute, V2Journal and HTTP control.
"""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import Blocked,canonical,digest,save
from orchestration.store import lease,process_identity
from orchestration.process import execute
from studio.workbench_graph import request_sections,request_text
from studio.workbench_scheduler import Scheduler
from v2.store import DraftStore
from v2.server import create_server
from v2.runtime import V2Runtime,V2Journal,persist_completion
from v2.recovery import observe


class Provider:
    def __init__(self,store,executable=None):self.store=store
    def cached(self):return dict(provider='codex',source='SYNTHETIC local process fixture',at=time.time(),stale=False,error=None,authenticated=False,inference_verified=False,models=[dict(id='fixture',name='Synthetic process',efforts=['low'])])
    discover=cached
    def binding(self,workspace,node):
        exe=str(Path(sys.executable).resolve());home=self.store.state.parent/'fixture-home';home.mkdir(exist_ok=True,mode=0o700)
        return dict(provider='codex',executable=exe,home=str(home),config_root=str(home),model='fixture',effort='low',_executable_sha256=digest(Path(exe).read_bytes()))
    def require_local(self,binding):pass  # Only this synthetic fixture; never native authority proof.


class Runtime(V2Runtime):
    def __init__(self,store,mode='hold',*,worker_run=None):
        self.store,self.flow,self.storage=store,store.db,store.state;self.worker_run=worker_run
        self.mode=mode;self.provider=Provider(store);self.provider_error=None
    def spawn(self,run):
        read,write=os.pipe()
        argv=[sys.executable,'-B',str(Path(__file__).resolve()),'worker','--state',str(self.storage),'--spaces',str(self.store.roots[0]),'--run',run,'--mode',self.mode,'--gate',str(read)]
        child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,pass_fds=(read,))
        os.close(read)
        try:
            identity=process_identity(child.pid)
            self.flow.change('run',run,dict(worker=identity,worker_published_at=time.time(),launch_state='STARTED'))
            os.write(write,b'GO\n')
        finally:os.close(write)
    def fixture_turn(self,node,key,inputs,prefix,iteration):
        run=self.worker_run;definition=self.flow.get('definition',self.flow.get('run',run)['definition_id'])
        binding=definition['bindings'][node['id']];sid=str(uuid.uuid4());chat=digest(canonical([run,node['id']]))
        self.flow.put('node-session',chat,dict(id=chat,run=run,node=node['id'],workspace=self.flow.get('run',run)['workspace'],binding=binding))
        request=request_text(node,inputs,first=True,task_context='');sha=digest(request.encode());request_id=digest(canonical([run,key,'fixture-request']))
        for item in inputs:item['handoff_id']=digest(canonical([run,item['edge'],key]))
        self.flow.change('activation',key,dict(chat_key=chat,session_binding_id=chat,request_id=request_id,inputs=inputs,request_sections=request_sections(node,inputs,first=True,task_context=''),synthetic_fixture=True))
        journal=V2Journal(Scheduler(self,run),key);journal.visible_request(request)
        root=self.storage.parent;barrier=root/'barriers'/(run+'-'+node['id']+'.json');barrier.parent.mkdir(exist_ok=True)
        release=root/'releases'/(run+'-'+node['id']);release.parent.mkdir(exist_ok=True)
        if self.mode=='before-send':
            save(barrier,dict(stage='before-send',run=run,node=node['id'],worker=process_identity(os.getpid())))
            while not release.exists():
                if journal.get(run)['data'].get('cancel_requested'):raise Blocked('synthetic cancellation before send')
                time.sleep(.02)
        proc,stdout,stderr=execute([binding['executable'],'-B',str(Path(__file__).resolve()),'child','--barrier',str(barrier),'--release',str(release)],
            self.store.workspace(self.flow.get('run',run)['workspace'])['path'],binding['home'],timeout=40,cap=100000,
            store=journal,run=run,input_text=request,isolate=False,backend='native-local',context=dict(provider='codex',synthetic_fixture=True))
        if proc['cancelled'] or proc['exit']!=0:raise Blocked('synthetic process stopped without Provider terminal')
        self.flow.change('activation',key,dict(observed_session=sid));journal.bind_session(sid)
        journal.native_terminal_event(dict(type='turn.completed',session_id=sid,native_turn_id=None))
        receipt=dict(session_id=sid,native_turn_id='unknown',requested_model='fixture',requested_effort='low',resolved_model='unknown',resolved_effort='unknown',process=proc)
        output=dict(answer='synthetic controlled completion',summary='',result={},evidence=[],issues=[],next='',artifacts=[])
        completion=dict(run=run,activation=key,request_id=request_id,request_sha256=sha,receipt=receipt,output=output)
        path=self.storage/'flow-artifacts'/run/key;path.mkdir(parents=True,exist_ok=True);persist_completion(path,completion,self.storage)
        if self.mode in ('completion-before-record','corrupt-completion'):
            if self.mode=='corrupt-completion':(path/'completion.json').write_text('{synthetic-corrupt')
            save(barrier,dict(stage='completion-before-record',run=run,node=node['id']))
            os._exit(42)
        self.flow.change('activation',key,dict(effect_uncertain=False,receipt_verified=True,receipt=receipt,completion_sha256=digest(canonical(completion))))
        return output


def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['server','worker','child']);p.add_argument('--state');p.add_argument('--spaces');p.add_argument('--run');p.add_argument('--mode',default='hold');p.add_argument('--gate',type=int);p.add_argument('--port',type=int,default=0);p.add_argument('--barrier');p.add_argument('--release');a=p.parse_args()
    if a.kind=='child':
        data=sys.stdin.buffer.read();save(a.barrier,dict(stage='input-read',bytes=len(data),sha256=digest(data),process=process_identity(os.getpid())))
        deadline=time.monotonic()+35
        while not Path(a.release).exists():
            if time.monotonic()>deadline:return 3
            time.sleep(.02)
        print('synthetic result',flush=True);return 0
    store=DraftStore(a.state,[a.spaces]);rt=Runtime(store,a.mode,worker_run=a.run if a.kind=='worker' else None)
    if a.kind=='worker':
        try:
            if not select.select([a.gate],[],[],10)[0] or os.read(a.gate,16)!=b'GO\n':return 1
        finally:os.close(a.gate)
        with lease(store.state/(a.run+'.worker.lock')):
            row=store.db.get('run',a.run)
            if observe(row['worker'])!='alive' or any(row['worker'].get(k)!=process_identity(os.getpid()).get(k) for k in ('host','boot_id','pid','start','pgid','sid')):return 1
            Scheduler(rt,a.run,executor=rt.fixture_turn).run_graph()
        return 0
    with lease(store.state/'v2-service.lock'):
        server,token=create_server(store,a.port,runtime=rt)
        print('http://127.0.0.1:'+str(server.server_port)+'/#'+token,flush=True);server.serve_forever()
if __name__=='__main__':raise SystemExit(main())
