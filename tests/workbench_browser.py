"""Ordinary Chromium and real HTTP races; synthetic snapshots, no model acceptance."""
import argparse,json,os,subprocess,sys,threading,time,unittest
from unittest.mock import patch
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_workbench_runtime import fixture,structured_session
from studio.workbench_server import create_server
from studio.workbench_examples import examples


def main():
    p=argparse.ArgumentParser();p.add_argument('--chromium',required=True);p.add_argument('--artifacts',required=True);p.add_argument('--node',default='node');p.add_argument('--playwright-module',default='playwright');a=p.parse_args()
    root=Path(__file__).resolve().parents[1];out=Path(a.artifacts).resolve()
    if out.is_relative_to(root) or root.is_relative_to(out):p.error('Artifacts must stay outside source')
    out.mkdir(parents=True,mode=0o700);owner=unittest.TestCase();base,rt=fixture(owner, admission=True);workspace=base.project['id']
    for label in ('A','B'):
        graph=examples(rt.operator['profiles'])[0];graph['name']='运行 '+label
        graph['budget']={'calls':10 if label=='A' else 12,'seconds':600 if label=='A' else 900}
        graph['nodes'][1]['name']='分析 '+label
        rt.flow.put('run',label,dict(id=label,workspace=workspace,state='COMPLETED',graph=graph,task='任务 '+label,created_at=time.time(),actual_calls=0))
        rt.flow.put('activation',label,dict(id=label,run=label,node='analyze',name='分析 '+label,kind='agent',state='COMPLETED',iteration=1,output={'answer':'结果 '+label}))
    # A lost worker with no completion receipt is genuinely unrecoverable.
    preview=rt.prepare_flow(dict(workspace=workspace,graph=examples(rt.operator['profiles'])[0],task='Synthetic missing completion recovery'))
    with patch.object(rt,'spawn'):
        lost=rt.launch_flow(preview['id'],preview['sha256'],'Synthetic recovery test, never a native model')['run']['id']
    rt.flow.change('run',lost,dict(created_at=time.time()-10,state='RUNNING'))
    rt.flow.put('activation','lost-recovery',dict(id='lost-recovery',run=lost,node='analyze',kind='agent',state='RUNNING',name='Unconfirmed fixture'))
    session_rows={}
    for label in ('dialog-A','dialog-B'):
        _,row=structured_session(base,rt,label)
        rt.flow.put('session-meta',row['handle'],dict(id=row['handle'],workspace=workspace,collection='active',alias='Synthetic '+label))
        session_rows[label]=dict(handle=row['handle'],session_id=row['session_id'])
    rt.flow.event('dialog-A-run','fixture-history',node='loop/analyze',reason='')  # Synthetic UI-only history event, not a real control action.
    # Native Codex final text already supplies this answer: no second result body.
    _,row=structured_session(base,rt,'dialog-native','codex',native_answers=True)
    rt.flow.put('session-meta',row['handle'],dict(id=row['handle'],workspace=workspace,collection='active',alias='Synthetic dialog-native'))
    session_rows['dialog-native']=dict(handle=row['handle'],session_id=row['session_id'])
    # Tool payload alone, with a failed or unconfirmed activation, is not a result.
    _,row=structured_session(base,rt,'dialog-failed')
    for turn,state in ((1,'FAILED'),(2,'CANCELLED')):rt.flow.change('activation',f'dialog-failed-turn-{turn}',dict(state=state))
    rt.flow.put('session-meta',row['handle'],dict(id=row['handle'],workspace=workspace,collection='active',alias='Synthetic dialog-failed'))
    session_rows['dialog-failed']=dict(handle=row['handle'],session_id=row['session_id'])
    # Match actual non-loop scheduler activations: internal iteration=0, path=[].
    _,row=structured_session(base,rt,'dialog-plain')
    chat=rt.flow.get('node-session','dialog-plain-chat')
    rt.flow.put('node-session',chat['id'],dict(chat,node='analyze'))
    rt.flow.change('activation','dialog-plain-turn-1',dict(node='analyze',iteration=0,iteration_path=[]))
    rt.flow.change('activation','dialog-plain-turn-2',dict(state='CANCELLED'))
    rt.flow.put('session-meta',row['handle'],dict(id=row['handle'],workspace=workspace,collection='active',alias='Synthetic dialog-plain'))
    session_rows['dialog-plain']=dict(handle=row['handle'],session_id=row['session_id'])
    # Explicit synthetic metadata boundary. /usr/bin/true is not a native model.
    from studio.workbench_sessions import WorkbenchSessions
    original_rpc=WorkbenchSessions.rpc
    def metadata(manager,binding,cwd,method,params):
        if method=='cli/probe':return dict(available=True,machine_output=True,version='synthetic metadata fixture',resume=True)
        if method=='model/list':return dict(data=[dict(model=binding['model'],supportedReasoningEfforts=[dict(reasoningEffort=binding['effort'])]),dict(model='synthetic-configurable-model',supportedReasoningEfforts=[dict(reasoningEffort='high'),dict(reasoningEffort='low')])])
        if method=='account/read':return dict(account={'type':'synthetic fixture'})
        if method=='claude/auth-status':return dict(logged_in=True,auth_method='synthetic fixture',service_verified=False)
        return original_rpc(manager,binding,cwd,method,params)
    metadata_patch=patch.object(WorkbenchSessions,'rpc',metadata);metadata_patch.start();owner.addCleanup(metadata_patch.stop)
    # Built-in assistant browser scenarios are synthetic; never use a real
    # signed-in Copilot account as an unlabelled catalog/model fixture.
    from test_canvas_assistant import SyntheticBackend
    from studio.canvas_assistant import CanvasAssistant
    from studio.devflower_agent import AGENT_ID
    rt.flow.put('canvas-conversation','c'*32,dict(id='c'*32,agent_id=AGENT_ID,workspace=workspace,revision=1,title='Synthetic 请求状态待确认',updated_at=0,active_turn='d'*32,messages=[dict(role='user',text='此前请求，没有伪造回答',turn='d'*32)]))
    rt.flow.put('canvas-turn','d'*32,dict(id='d'*32,conversation='c'*32,workspace=workspace,state='RUNNING',owner=None,created_at=0,reason=None,proposal=None,receipt=None))
    def synthetic_assistant(runtime):
        assistant=CanvasAssistant(runtime);assistant.backend=SyntheticBackend;return assistant
    assistant_patch=patch('studio.canvas_assistant.CanvasAssistant',synthetic_assistant);assistant_patch.start();owner.addCleanup(assistant_patch.stop)
    server,token=create_server(rt)
    # Drain real in-flight history requests before deleting the fixture DBs.
    # Browser disconnect does not itself end the server's request thread.
    server.daemon_threads=False
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    access=out/'access.json';access.write_text(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),token=token,workspace=workspace,path=str(base.source),sessions=session_rows,lost_run=lost)))
    access.chmod(0o600)
    try:return subprocess.run([a.node,str(Path(__file__).with_suffix('.cjs')),str(out)],timeout=900,env=dict(os.environ,CHROMIUM=a.chromium,PLAYWRIGHT_MODULE=a.playwright_module,WORKBENCH_TEST_PYTHON=sys.executable)).returncode
    finally:server.shutdown();server.server_close();thread.join();owner.doCleanups()


if __name__=='__main__':sys.exit(main())
