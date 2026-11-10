"""Opt-in real Codex developer/tester/reviewer workflow in a disposable Git root."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_v2_development_runtime import development_document
from test_v2_git_platform import git, initialize_repo
from v2.git_workspace import bind_workspace
from v2.store import DraftStore

CHECKER = """import json
from pathlib import Path
import subprocess
import sys
app = Path(__file__).with_name('candidate.py')
results = []
for sample in (2, 5):
    proc = subprocess.run([sys.executable, '-I', '-B', str(app), str(sample)],
                          check=True, capture_output=True, text=True, timeout=4)
    results.append(int(proc.stdout.strip()))
print(json.dumps({'cases': results}))
"""
ROLES = [
    ('Developer','developer','Agent','workspace-write',
     'Use an authorized workspace tool to implement candidate.py, a standalone Python script that reads one '
     'integer argv and prints one integer. In round 1 only, implement the deliberately faulty legacy behavior '
     'n+1. This exercises a real red test. If the actual upstream Gate result is REJECT, repair the existing '
     'script to print 2*n instead. Do not modify check.py, .git, or other files. Verify candidate.py was written, '
     'then return the requested normal response schema. Report the change in your visible reply.'),
    ('Tester','custom','Ask','workspace-read',
     'Use read-only tools to inspect candidate.py and run python3 -I -B check.py once. It prints the two actual '
     'case values. Do not change any file. Report the actual observations in your visible reply. The independent '
     'deterministic verifier will separately assess both cases against the frozen expected result.'),
    ('Reviewer','reviewer','Ask','workspace-read',
     'Independently read candidate.py and check.py with authorized read-only tools. Review the actual deterministic '
     'verification facts in the upstream input. Return exactly the fields verdict, candidate_sha256, feedback in '
     'your structured result_json (and preferably in reply). Copy the exact upstream 64-character candidate_sha256. '
     'If passed is false or candidate.py does not double n, verdict must be REJECT with the concrete repair. '
     'If passed is true and source correctly doubles n, verdict must be ACCEPT and describe the two cases. '
     'Do not change any file. DevFlower separately verifies test evidence and makes the delivery decision.'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts',required=True)
    parser.add_argument('--allow-native-model-calls',action='store_true',required=True)
    options = parser.parse_args()
    out = Path(options.artifacts).resolve()
    repo = Path(__file__).resolve().parents[1]
    if out.is_relative_to(repo) or repo.is_relative_to(out):
        parser.error('Use a fresh disposable artifact root outside product source.')
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    spaces = out/'spaces'
    project = spaces/'loop-fixture'
    initialize_repo(project,'https://github.com/devflower-disposable/gate-fixture.git')
    checker = project/'check.py'
    checker.write_text(CHECKER)
    git(project,'add','check.py')
    git(project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Freeze operator test')
    initial_head = git(project,'rev-parse','HEAD')
    args = [sys.executable,'-B',str(repo/'tools/devflower_v2.py'),'--state-dir',str(out/'state'),
            '--allow-root',str(spaces),'--no-browser']
    with (out/'server-stderr.txt').open('w') as stderr:
        service = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=stderr,text=True)
        try:
            with selectors.DefaultSelector() as waiting:
                waiting.register(service.stdout,selectors.EVENT_READ)
                if not waiting.select(30):
                    raise RuntimeError('V2 launcher timeout')
            url, token = service.stdout.readline().strip().split('/#',1)

            def api(route, data=None, expected=200):
                request = Request(url+'/api/v2/'+route,data=None if data is None else json.dumps(data).encode(),
                                  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                try:
                    with urlopen(request,timeout=55) as response:
                        result,status = json.load(response),response.status
                except HTTPError as error:
                    result,status = json.load(error),error.code
                assert status==expected,(route.split('?')[0],status,result.get('error'))
                return result

            workspace = api('workspaces/open',{'path':str(project)})['id']
            catalog = api('cli-providers/discover',{'provider':'codex'})
            if 'providers' in catalog:
                catalog = next(row for row in catalog['providers'] if row['provider']=='codex')
            assert any(row['id']=='gpt-6.1-sol' and 'low' in row['efforts'] for row in catalog['models'])
            agents = []
            for title,template,mode,permission,prompt in ROLES:
                config = dict(name=title,role=title,template=template,provider='codex',model='gpt-6.1-sol',
                    model_source='catalog',effort='low',mode=mode,permission=permission,cwd=str(project),
                    session={'kind':'new'},skills=[],subagents='disabled',timeout=150,max_budget_usd=None,
                    responsibilities='Perform the explicitly assigned fixture role.',background='Disposable Git fixture.',
                    goal='Demonstrate red, repair, and green for a doubling program.',initial_prompt='',
                    round_prompt=prompt,output_prompt='Preserve real observations and exact upstream evidence.')
                agents.append(api('agents/save',dict(workspace=workspace,id=None,expected=None,config=config,session_revision=None)))
            document = development_document(agents)
            draft = api('drafts/save',dict(workspace=workspace,id=None,expected=None,document=document))
            payload = dict(workspace=workspace,draft_id=draft['id'],revision=draft['revision'],
                           intent_key=uuid4().hex,task='Complete the bounded disposable doubling workflow.')
            run = api('launch',payload)['run']['id']
            lookup = 'run?'+urlencode({'workspace':workspace,'id':run})
            stop = time.monotonic()+420
            prior = None
            while time.monotonic()<stop:
                value = api(lookup)
                states = [(row['node'],row['iteration_path'],row['state']) for row in value['nodes']
                          if row['kind']=='agent' or row['node'].endswith(('/Verify','/Gate'))]
                current = value['state'],states
                if current!=prior:
                    print(json.dumps(dict(state=value['state'],stages=states),ensure_ascii=False),flush=True)
                    prior=current
                if value['run']['persisted_state'] in ('FAILED','CANCELLED','COMPLETED','RECOVERY_REQUIRED') and not value['worker_alive']:
                    break
                time.sleep(.65)
            else:
                raise AssertionError('development workflow did not settle')
            (out/'run-evidence.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            assert value['state']=='COMPLETED',(value['state'],value['run'].get('reason'),
                [(row['node'],row.get('reason')) for row in value['nodes'] if row.get('reason')])
            indexed={(row['node'],tuple(row['iteration_path'])):row for row in value['nodes']}
            stages=[indexed[(f'RepairLoop/{role}',(i,))] for i in (1,2) for role in ('Developer','Tester','Reviewer')]
            assert all(row['receipt_verified'] and row['actual_request_sha256']==row['input_sent_sha256'] for row in stages)
            assert len({row['receipt']['session_id'] for row in stages})==6 and value['run']['actual_calls']==6
            verifies=[indexed[('RepairLoop/Verify',(i,))]['output']['result'] for i in (1,2)]
            gates=[indexed[('RepairLoop/Gate',(i,))]['output']['result'] for i in (1,2)]
            assert [item['passed'] for item in verifies]==[False,True] and all(item['counts']['tests']==1 for item in verifies)
            assert [item['gate_verdict'] for item in gates]==['REJECT','ACCEPT']
            assert [item['candidate_sha256'] for item in verifies]==[item['candidate_sha256'] for item in gates]
            assert gates[0]['candidate_sha256']!=gates[1]['candidate_sha256']
            assert 'REJECT' in indexed[('RepairLoop/Developer',(2,))]['actual_request']
            assert value['run']['output']['result']==gates[1]
            assert api('launch',payload)['run']['id']==run
            assert checker.read_text()==CHECKER and git(project,'rev-parse','HEAD')==initial_head
            gate_scope = bind_workspace(DraftStore(out/'state',[spaces]),workspace)
            reviews = 'git-platform/reviews?'+urlencode({'workspace':workspace,'binding':gate_scope.identity})
            evidence = api(reviews)['delivery']
            assert evidence['state']=='LOCAL_GATE_ACCEPTED' and evidence['publish_available'] is False,evidence
            candidate = project/'candidate.py'
            original = candidate.read_bytes()
            candidate.write_bytes(original+b'\n# Disposable stale candidate probe\n')
            try:
                assert api(reviews)['delivery']['state']=='CANDIDATE_CHANGED'
            finally:
                candidate.write_bytes(original)
            print('PASS six real Codex CLI sessions, red test and REJECT, repaired test and ACCEPT, exact candidate readback; zero remote writes',flush=True)
        finally:
            service.terminate()
            try:
                service.wait(timeout=8)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
