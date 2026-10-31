"""Real HTTP/Chromium layout and reading checks; explicitly synthetic model data."""
import argparse,json,os,subprocess,sys,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_workbench_runtime import fixture
from studio.workbench_server import create_server
from studio.workbench_sessions import WorkbenchSessions
from studio.canvas_assistant import CanvasAssistant
from test_canvas_assistant import SyntheticBackend

def main():
    p=argparse.ArgumentParser();p.add_argument('--artifacts',required=True);p.add_argument('--chromium',required=True);p.add_argument('--node',default='node');p.add_argument('--playwright-module',default='playwright');a=p.parse_args()
    out=Path(a.artifacts).resolve();root=Path(__file__).resolve().parents[1]
    if out.is_relative_to(root) or root.is_relative_to(out):p.error('Artifacts must stay outside source')
    out.mkdir(parents=True,mode=0o700);owner=unittest.TestCase();base,rt=fixture(owner,admission=True)
    original=WorkbenchSessions.rpc
    def metadata(manager,binding,cwd,method,params):
        if method=='cli/probe':return dict(available=True,machine_output=True,version='synthetic design fixture',resume=True)
        if method=='model/list':return dict(data=[dict(model=binding['model'],supportedReasoningEfforts=[dict(reasoningEffort=binding['effort'])])])
        if method=='account/read':return dict(account={'type':'synthetic fixture'})
        if method=='claude/auth-status':return dict(logged_in=True,auth_method='synthetic fixture',service_verified=False)
        return original(manager,binding,cwd,method,params)
    def synthetic(runtime):
        assistant=CanvasAssistant(runtime);assistant.backend=SyntheticBackend;return assistant
    with patch.object(WorkbenchSessions,'rpc',metadata),patch('studio.canvas_assistant.CanvasAssistant',synthetic):
        server,token=create_server(rt);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        access=out/'access.json';access.write_text(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),token=token,workspace=base.project['id'])));access.chmod(0o600)
        try:return subprocess.run([a.node,str(Path(__file__).with_suffix('.cjs')),str(out)],env=dict(os.environ,CHROMIUM=a.chromium,PLAYWRIGHT_MODULE=a.playwright_module),timeout=240).returncode
        finally:server.shutdown();server.server_close();thread.join();owner.doCleanups()

if __name__=='__main__':sys.exit(main())
