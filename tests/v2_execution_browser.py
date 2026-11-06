"""Chromium faults against real HTTP/SQLite, synthetic execution only; no native CLI."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from orchestration.common import canonical,digest,save
from orchestration.store import process_identity
from studio.workbench_graph import request_sections,request_text
from studio.workbench_scheduler import Scheduler
from v2.runtime import V2Runtime
from v2.store import DraftStore
from v2.server import create_server
from v2_browser import redact_trace


class SyntheticProvider:
    def __init__(self,store,executable=None):self.store=store
    def cached(self):return dict(provider='codex',source='SYNTHETIC fixture; no inference',at=time.time(),stale=False,error=None,authenticated=False,inference_verified=False,models=[dict(id='fixture-model',name='Fixture model',efforts=['low','high'])])
    def discover(self):return self.cached()
    def binding(self,workspace,node):return dict(provider='codex',executable='/synthetic/never-started',model=node['model'],effort=node['effort'],_executable_sha256='a'*64)


class SyntheticRuntime(V2Runtime):
    def spawn(self,run):
        self.flow.change('run',run,dict(worker=process_identity(os.getpid()),worker_published_at=time.time()))
        def execute():Scheduler(self,run,executor=self.synthetic_turn).run_graph()
        threading.Thread(target=execute,daemon=True).start()
    def synthetic_turn(self,node,key,inputs,prefix,iteration):
        time.sleep(.15)
        parts=request_sections(node,inputs,first=True,task_context='')
        request=request_text(node,inputs,first=True,task_context='')
        for item in inputs:item['handoff_id']=digest(canonical([key,item['edge']]))
        run=self.flow.get('activation',key)['run'];operation='fixture-'+uuid.uuid4().hex;sid=str(uuid.uuid4());process=dict(process_identity(os.getpid()),pid=2147483647,pgid=2147483647,sid=2147483647)
        proc=dict(process=process,operation_id=operation,exit=0,timeout=False,cancelled=False,overflow=False)
        receipt=dict(session_id=sid,native_turn_id='unknown',requested_model='fixture-model',requested_effort='low',resolved_model='unknown',resolved_effort='unknown',process=proc)
        answer='synthetic visible answer' if node['name']!='Long source' else 'LONG_PUBLIC_'+'a'*17000
        output=dict(answer=answer,summary='synthetic only',result={},evidence=[],issues=[],next='',artifacts=[])
        sha=digest(request.encode());request_id=digest(canonical([key,'synthetic-request']))
        completion=dict(run=run,activation=key,request_id=request_id,request_sha256=sha,receipt=receipt,output=output)
        artifact=self.storage/'flow-artifacts'/run/key;artifact.mkdir(parents=True,exist_ok=True)
        save(artifact/'completion.json',completion)
        self.flow.put('native-step',operation,dict(id=operation,run=run,activation=key,kind='PROCESS',status='EXITED',data=proc))
        self.flow.change('activation',key,dict(inputs=inputs,actual_request=request,actual_request_sha256=sha,request_sections=parts,
            request_id=request_id,input_sent_sha256=sha,input_sent_operation_id=operation,process=process,process_result=proc,
            observed_session=sid,native_turn_id=None,native_terminal_events=[dict(type='turn.completed',session_id=sid,native_turn_id=None)],
            executed_image_sha256='a'*64,receipt=receipt,receipt_verified=True,effect_uncertain=False,completion_sha256=digest(canonical(completion)),synthetic_fixture=True))
        return output


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',required=True);parser.add_argument('--chromium',required=True);parser.add_argument('--playwright-module',required=True);args=parser.parse_args()
    out=Path(args.artifacts).resolve();out.mkdir(parents=True,mode=0o700,exist_ok=False)
    spaces=out/'workspaces';spaces.mkdir();(spaces/'A').mkdir();(spaces/'B').mkdir()
    store=DraftStore(out/'state',[spaces])
    with patch('v2.runtime.CodexProvider',SyntheticProvider):runtime=SyntheticRuntime(store)
    server,token=create_server(store,runtime=runtime);threading.Thread(target=server.serve_forever,daemon=True).start()
    env=dict(os.environ,V2_URL='http://127.0.0.1:'+str(server.server_port)+'/#'+token,V2_WORKSPACES=str(spaces),CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module)
    try:
        result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(out)],env=env,timeout=180)
        print(json.dumps(dict(browser_exit=result.returncode,category='synthetic execution; real HTTP/SQLite/Chromium',artifacts=str(out))))
        return result.returncode
    finally:
        server.shutdown();server.server_close()
        for trace in out.glob('*.zip'):redact_trace(trace)
if __name__=='__main__':raise SystemExit(main())
