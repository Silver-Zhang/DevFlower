"""Ordinary Chromium/HTTP project persistence; synthetic history, no model claims."""
import argparse,json,os,subprocess,sys,threading,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_workbench_runtime import fixture,structured_session
from studio.workbench_server import create_server
from studio.workbench_sessions import WorkbenchSessions
from studio.workbench_examples import examples
from studio.workflow_library import WorkflowLibrary


def main():
    p=argparse.ArgumentParser()
    for name in ('chromium','artifacts','node','playwright-module'):p.add_argument('--'+name,required=True)
    a=p.parse_args();out=Path(a.artifacts).resolve();root=Path(__file__).resolve().parents[1]
    if out.is_relative_to(root) or root.is_relative_to(out):p.error('Artifacts must be outside source')
    out.mkdir(mode=0o700,parents=True);owner=unittest.TestCase();base,rt=fixture(owner);w=base.project['id'];files=rt.project_files(w)
    graph=examples(rt.operator['profiles'])[0];graph['name']='已保存的项目工作流';files.save_draft('local-browser',graph,None)
    WorkflowLibrary(rt).save(dict(workspace=w,graph=graph,name='项目收藏模板'))
    path,row=structured_session(base,rt,'portable-browser')
    manager=WorkbenchSessions(rt);named=next(s for s in manager.listing(w)['sessions'] if s['handle']==row['handle'])
    files.session(named,manager.history(w,row['handle']));files.run('portable-browser-run');path.unlink()
    files.assistant(dict(id='a'*32,title='已保存的助手问答',revision=2,messages=[dict(role='user',text='如何保存项目'),dict(role='assistant',text='保存在项目自己的隐藏目录内。')]))
    with rt.flow.tx() as db:db.execute("DELETE FROM records WHERE kind IN ('draft','workflow-template','run','activation','node-session')")
    other=base.source.parent/'another-project';other.mkdir()
    server,token=create_server(rt);server.daemon_threads=False
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    access=out/'access.json';access.write_text(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),token=token,path=str(base.source),other=str(other))))
    access.chmod(0o600)
    try:
        result=subprocess.run([a.node,str(Path(__file__).with_suffix('.cjs')),str(out)],timeout=120,
            env=dict(os.environ,CHROMIUM=a.chromium,PLAYWRIGHT_MODULE=a.playwright_module))
        if result.returncode:return result.returncode
        with files.library() as local:
            assert local.get('draft','local-browser')['graph']['name']=='浏览器保存的新版本'
        assert not rt.flow.list('permit') and not rt.flow.list('activation')
        return 0
    finally:server.shutdown();server.server_close();thread.join();owner.doCleanups()


if __name__=='__main__':sys.exit(main())
