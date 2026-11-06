"""Real service/worker/child/Chromium faults; explicitly synthetic Provider only."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'tools'),str(Path(__file__).parent)]
from v2.store import DraftStore
from v2.recovery import observe,owned_group
from orchestration.process import stop
from v2_browser import redact_trace


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',required=True);parser.add_argument('--chromium',required=True);parser.add_argument('--playwright-module',required=True);args=parser.parse_args()
    out=Path(args.artifacts).resolve();root=Path(__file__).resolve().parents[1]
    if out.is_relative_to(root) or root.is_relative_to(out):parser.error('Evidence must remain outside source')
    out.mkdir(parents=True,mode=0o700,exist_ok=False);spaces=out/'workspaces';spaces.mkdir();(spaces/'A').mkdir()
    log=(out/'server-stderr.txt').open('w');services=[]
    def launch(mode='hold',port=0):
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('v2_process_fixture.py')),'server','--state',str(out/'state'),'--spaces',str(spaces),'--mode',mode,'--port',str(port)],stdout=subprocess.PIPE,stderr=log,text=True);services.append(proc)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout,selectors.EVENT_READ)
            if not selector.select(10):raise RuntimeError('Synthetic service did not start')
            url=proc.stdout.readline().strip()
        if not url.startswith('http://127.0.0.1:') or '/#' not in url:raise RuntimeError('Synthetic service missing private local entry')
        return proc,url
    server,url=launch();unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(240)'],start_new_session=True)
    env=dict(os.environ,V2_URL=url,V2_WORKSPACES=str(spaces),CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module,UNRELATED_PID=str(unrelated.pid))
    browser=subprocess.Popen(['node',str(Path(__file__).with_suffix('.cjs')),str(out)],env=env,stdout=subprocess.PIPE,stdin=subprocess.PIPE,text=True)
    output=(out/'browser-output.txt').open('w');deadline=time.monotonic()+240
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(browser.stdout,selectors.EVENT_READ)
            while browser.poll() is None:
                if time.monotonic()>deadline:raise RuntimeError('Bounded recovery browser timeout')
                if not selector.select(.2):continue
                line=browser.stdout.readline()
                if line.startswith('{"control":'):
                    command=json.loads(line)
                    if command['control']=='restart-service':
                        port=urlsplit(url).port if command.get('samePort') else 0;server.terminate();server.wait(timeout=5);server,url=launch(command.get('mode','hold'),port);reply=dict(url=url)
                    elif command['control']=='kill-worker':
                        store=DraftStore(out/'state',[spaces]);worker=store.db.get('run',command['run'])['worker']
                        if observe(worker)!='alive':raise RuntimeError('Owned fixture worker identity unavailable')
                        os.kill(worker['pid'],signal.SIGKILL);reply=dict(killed=True)
                    else:raise RuntimeError('Unknown fixture control')
                    browser.stdin.write(json.dumps(reply)+'\n');browser.stdin.flush()
                else:output.write(line);output.flush();print(line,end='',flush=True)
        result=browser.wait(timeout=5)
        (out/'command-result.json').write_text(json.dumps(dict(exit=result,category='synthetic Provider; actual local processes and Chromium; real_model_calls=0'),indent=2)+'\n')
        return result
    finally:
        if browser.poll() is None:browser.kill();browser.wait(timeout=5)
        browser.stdin.close();browser.stdout.close();output.close()
        for proc in services:
            if proc.poll() is None:proc.terminate()
            proc.wait(timeout=5);proc.stdout.close()
        if unrelated.poll() is None:unrelated.terminate()
        unrelated.wait(timeout=5);log.close()
        if (out/'state/v2.sqlite').exists():
            store=DraftStore(out/'state',[spaces]);steps=store.db.list('native-step')
            for run in store.db.list('run'):
                store.db.change('run',run['id'],dict(cancel_requested=True))
                worker=run.get('worker')
                if observe(worker)=='alive':os.kill(worker['pid'],signal.SIGKILL)
            for node in store.db.list('activation'):
                try:
                    record=owned_group(node,steps)
                    if record and observe(record,group=True)=='alive':stop(record)
                except Exception:pass  # Retain uncertain fixture evidence; never signal an unowned PID.
        for trace in out.glob('*.zip'):redact_trace(trace)
if __name__=='__main__':raise SystemExit(main())
