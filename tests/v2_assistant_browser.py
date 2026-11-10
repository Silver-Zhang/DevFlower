"""Real V2 HTTP and Chromium assistant UI with an explicitly synthetic transport."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_v2_assistant import FixtureOperations
from v2_browser import redact_trace
from v2_execution_browser import SyntheticProvider
from v2.runtime import V2Runtime
from v2.server import create_server
from v2.store import DraftStore


def main():
    parser = argparse.ArgumentParser()
    for field in ('artifacts','chromium','playwright-module'):
        parser.add_argument('--'+field,required=True)
    args = parser.parse_args()
    out = Path(args.artifacts).resolve()
    out.mkdir(parents=True,mode=0o700,exist_ok=False)
    spaces = out/'workspaces'
    project = spaces/'A'
    project.mkdir(parents=True)
    (project/'note.txt').write_text('SELECTED_ASSISTANT_ATTACHMENT')
    (project/'unselected.txt').write_text('UNSELECTED_ASSISTANT_SENTINEL')
    store = DraftStore(out/'state',[spaces])
    workspace = store.open_workspace(str(project))['id']
    document = dict(version=1,name='Assistant browser draft',nodes=[
        dict(id='AgentA',name='Original Agent',position={'x':200,'y':120},
             provider='codex',model='fixture-model',effort='low',round_prompt='fixture')],edges=[])
    draft = store.save(workspace,None,None,document)
    operations = FixtureOperations()
    with patch('v2.runtime.CodexProvider',SyntheticProvider):
        runtime = V2Runtime(store,executable=str(out/'no-native-cli'))
    node = lambda id,kind,x,config: dict(id=id,name=id,kind=kind,config_version=1,config=config,position={'x':x,'y':80})
    failing = dict(version=1,name='Assistant diagnosis fixture',nodes=[
        node('Start','start',60,{}),node('Route','router',300,{'rules':[
            {'id':'match','label':'Never matched','test':{'field':'result.never','op':'exists','value':True}}]}),
        node('Finish','output',560,{})],edges=[
        dict(id='first',source='Start',target='Route',source_port='out',target_port='in'),
        dict(id='second',source='Route',target='Finish',source_port='match',target_port='in')])
    failure = store.save(workspace,None,None,failing)
    runtime.launch(workspace,failure['id'],failure['revision'],'assistant-browser-failure')
    server, token = create_server(store,runtime=runtime,session_operations=operations)
    Thread(target=server.serve_forever,daemon=True).start()
    env = dict(os.environ,V2_URL=f'http://127.0.0.1:{server.server_port}/#{token}',
               V2_WORKSPACES=str(spaces),CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module)
    try:
        result = subprocess.run(['node',str(Path(__file__).with_suffix('.cjs')),str(out)],env=env,timeout=140)
        preserved = store.draft(workspace,draft['id'])['graph']==document
        print(json.dumps(dict(browser_exit=result.returncode,synthetic_model_calls=len(operations.calls),
                              real_model_calls=0,draft_preserved=preserved,run_count=len(store.db.list('run')))))
        return int(bool(result.returncode or not preserved or len(operations.calls)!=2 or len(store.db.list('run'))!=1))
    finally:
        server.shutdown()
        server.server_close()
        for trace in out.glob('*.zip'):
            redact_trace(trace)


if __name__=='__main__':
    raise SystemExit(main())
