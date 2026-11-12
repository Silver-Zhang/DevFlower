"""Explicit, reproducible architecture exercises against the actual V2 service and installed CLIs."""
import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from test_v2_canvas_structure import edge, graph, node
from test_v2_development_runtime import development_document
from test_v2_git_platform import git, initialize_repo
from v2_rag_fixture import build_and_rank, score_answers, score_retrieval

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'}
CHECKER = """import json, subprocess, sys
from pathlib import Path
script = Path(__file__).with_name('candidate.py')
print(json.dumps({'cases': [int(subprocess.check_output(
    [sys.executable, '-I', '-B', str(script), str(value)], timeout=5)) for value in (2, 5)]}))
"""
WRONG = 'import sys\nprint(int(sys.argv[1]) + 1)\n'
CORRECT = 'import sys\nprint(int(sys.argv[1]) * 2)\n'
CORRECT_SOURCE = {'field': 'result.text', 'op': 'equals', 'value': CORRECT}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def command(source, output, workspace):
    args = [sys.executable, '-B', str(source / 'tools/devflower_v2.py'), '--no-browser',
            '--state-dir', str(output / 'state'), '--allow-root', str(workspace)]
    for name, flag, value in (
        ('history.json', '--native-history-config', {'version': 1, 'discover_defaults': True}),
        ('writes.json', '--native-session-writes', {'version': 1, 'claude': True}),
    ):
        path = output / name
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(value, stream)
        args.extend([flag, str(path)])
    return args


class Exercise:
    def __init__(self, origin, token, workspace, output):
        self.origin, self.token = origin, token
        self.workspace, self.output = workspace, output
        self.scope = self.api('workspaces/open', {'path': str(workspace)})['id']

    def api(self, route, data=None):
        request = Request(self.origin + '/api/v2/' + route,
                          data=None if data is None else json.dumps(data).encode(),
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=65) as reply:
                return json.load(reply)
        except HTTPError as error:
            body = json.load(error)
            raise AssertionError((route.split('?')[0], error.code, body.get('code'), body.get('error'))) from None

    def catalog(self, provider):
        response = self.api('cli-providers/discover', {'provider': provider})
        return next(row for row in response['providers'] if row['provider'] == provider)

    def agent(self, title, provider, mode, permission, prompt, template='custom'):
        config = dict(name=title, role=title, template=template, provider=provider,
                      model=None if provider == 'claude' else 'gpt-6.1-sol',
                      model_source='native_default' if provider == 'claude' else 'catalog',
                      effort='low', mode=mode, permission=permission, cwd=str(self.workspace), session={'kind': 'new'},
                      skills=[], subagents='disabled', timeout=170, max_budget_usd='0.35' if provider == 'claude' else None,
                      responsibilities='', background='', goal='', initial_prompt='', round_prompt=prompt,
                      output_prompt='Keep the actual observations and the requested output format.')
        record = self.api('agents/save', dict(workspace=self.scope, id=None, expected=None,
                                             config=config, session_revision=None))
        save(self.output / ('agent-' + record['id'] + '.json'),
             dict(id=record['id'], revision=record['revision'], config=config))
        return record

    def launch(self, name, document, task, files=()):
        save(self.output / (name + '-graph.json'), document)
        draft = self.api('drafts/save', dict(workspace=self.scope, id=None, expected=None, document=document))
        inputs = [self.api('task-file?' + urlencode({'workspace': self.scope, 'path': file})) for file in files]
        payload = dict(workspace=self.scope, draft_id=draft['id'], revision=draft['revision'],
                       intent_key=uuid4().hex, task=task)
        if inputs:
            payload['task_files'] = [{key: item[key] for key in ('path', 'sha256')} for item in inputs]
        return self.api('launch', payload)['run']['id'], payload

    def wait(self, run, name, approve=None):
        since = time.monotonic()
        seen = set()
        while time.monotonic() - since < 620:
            value = self.api('run?' + urlencode({'workspace': self.scope, 'id': run}))
            if value['state'] == 'WAITING_CONFIRMATION':
                key = value['run']['waiting_approval']
                if key not in seen and approve:
                    row = next(node for node in value['nodes'] if node['id'] == key)
                    choice = approve(row, value)
                    save(self.output / (name + '-approval-' + str(len(seen) + 1) + '.json'),
                         {'activation': key, 'round': row['iteration_path'], 'inputs': row['inputs'], 'decision': choice})
                    self.api('approval', {'workspace': self.scope, 'run_id': run, 'activation': key, 'decision': choice})
                    seen.add(key)
            if value['state'] in TERMINAL and not value['worker_alive']:
                save(self.output / (name + '-run.json'), value)
                return value
            time.sleep(.85)
        save(self.output / (name + '-timeout.json'), value)
        raise AssertionError('Run did not settle inside the fixture observation limit.')

    def run(self, name, document, task, files=(), approve=None):
        started = time.monotonic()
        ident, payload = self.launch(name, document, task, files)
        value = self.wait(ident, name, approve)
        assert self.api('launch', payload)['run']['id'] == ident
        usage = []
        for row in value['nodes']:
            receipt = row.get('receipt') or {}
            if row['kind'] == 'agent' and row.get('receipt_verified'):
                usage.append(dict(node=row['node'], iteration=row['iteration_path'], provider=row['provider'],
                                  session_id=receipt.get('session_id'), reported=receipt.get('usage')))
        save(self.output / (name + '-metrics.json'), dict(
            state=value['state'], actual_calls=value['run']['actual_calls'],
            observed_seconds=round(time.monotonic()-started, 3), reported_usage=usage))
        print(name, value['state'], 'actual calls', value['run']['actual_calls'], flush=True)
        return value


@contextmanager
def service(source, output, workspace):
    with (output / 'server.log').open('w') as log:
        process = subprocess.Popen(command(source, output, workspace), stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                if not ready.select(40):
                    raise RuntimeError('V2 startup did not return a private local URL.')
            origin, token = process.stdout.readline().strip().split('/#', 1)
            yield Exercise(origin, token, workspace, output)
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def agent_node(name, record):
    return node(name, 'agent', agent_id=record['id'], agent_revision=record['revision'])


def flow(name, nodes, links=None):
    document = graph(nodes, links if links is not None else [edge('E'+str(i), a['id'], b['id'])
                    for i, (a, b) in enumerate(zip(nodes, nodes[1:]))])
    for i, item in enumerate(nodes):
        item['position'] = {'x': 80 + (i % 4)*280, 'y': 120 + (i//4)*220}
    document['name'] = name
    return document


def observed(value, name, iteration=()):
    activation = next(row for row in value['nodes'] if row['node'] == name and tuple(row['iteration_path']) == iteration)
    if activation['kind'] == 'agent' and activation['state'] == 'COMPLETED':
        assert activation['receipt_verified'] and activation['actual_request_sha256'] == activation['input_sent_sha256']
    return activation


def envelope(text):
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.split('\n', 1)[1][:-3].strip()
    return json.loads(text)


def rag(driver):
    retrieved = build_and_rank(driver.workspace)
    rank_metrics = score_retrieval(retrieved)
    save(driver.output / 'retrieval-metrics.json', rank_metrics)
    save(driver.workspace / 'retrieved.json', retrieved)
    analyst = driver.agent('Source Answerer', 'claude', 'Ask', 'none',
        'Answer each ID in the attached retrieved.json only from its supplied excerpts. Return one JSON '
        'object keyed by the exact ID strings 01 to 12. Each value is an object with answer and sources. '
        'answer is a short text and sources is an array of source IDs. Use null and [] if evidence is missing. '
        'Use digits for numbers, separate two facts with a comma, preserve specified order and units. '
        'Use the effective rule where a draft is marked obsolete. Output only the JSON object.')
    document = flow('Retrieval fixture and explicit V2 attachment', [
        node('Start', 'start'), agent_node('Answer', analyst), node('End', 'output', format='json')])
    run = driver.run('rag', document, 'Answer the twelve fixture questions.', files=['retrieved.json'])
    assert run['state'] == 'COMPLETED' and run['run']['actual_calls'] == 1
    answer = observed(run, 'Answer')
    assert 'retrieved.json' in answer['actual_request']
    answers = envelope(answer['output']['answer'])
    report = score_answers(retrieved, answers)
    save(driver.output / 'answer-metrics.json', report)
    print('External lexical retrieval', rank_metrics, flush=True)
    print('Actual model metrics', {key: value for key, value in report.items() if key != 'rows'}, flush=True)
    assert rank_metrics['recall_at_k'] == 1
    assert report['answer_accuracy'] >= .9 and report['citation_precision'] >= .9
    assert report['unsupported_answer_rate'] == 0


def write_checker(workspace):
    (workspace / 'check.py').write_text(CHECKER, encoding='utf-8')


def freeze_checker(workspace, *extra):
    initialize_repo(workspace, 'https://github.com/devflower-disposable/gate-fixture.git')
    write_checker(workspace)
    git(workspace, 'add', 'check.py', *extra)
    git(workspace, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Freeze operator test')


def react(driver):
    candidate = driver.workspace / 'candidate.py'
    candidate.write_text(WRONG, encoding='utf-8')
    freeze_checker(driver.workspace, 'candidate.py')
    outside = driver.output / 'private-marker.txt'
    outside.write_text('DF_OUTSIDE_NEVER_READ_2902\n')
    repairer = driver.agent('Investigate and repair', 'claude', 'Agent', 'workspace-write',
        'Use native Read on candidate.py. Use Bash to run exactly python3 -I -B check.py and observe red results. '
        f'Try native Read on the disposable absolute path {outside} and record whether access is denied. '
        'Do not print private bytes. '
        f'Use Edit or Write so candidate.py has exactly these bytes, represented as a JSON string: {json.dumps(CORRECT)}. '
        'Then use Bash to rerun exactly '
        'python3 -I -B check.py. Do not edit the tester. Report the first and last observed cases. Use actual tools.',
        'developer')
    document = flow('ReAct native tools and independent verification', [
        node('Start', 'start'), agent_node('Repair', repairer),
        node('Readback', 'tool', tool='read-file', permission='workspace-read', path='candidate.py'),
        node('Verify', 'validation', rules=json.dumps(CORRECT_SOURCE)), node('End', 'output', format='json'),
        node('InspectionFailed', 'output', format='json')],
        [edge('Input','Start','Repair'), edge('Changed','Repair','Readback'),
         edge('Read','Readback','Verify'), edge('Verified','Verify','End','pass'),
         edge('Rejected','Verify','InspectionFailed','fail')])
    result = driver.run('react', document, 'Observe, repair, and rerun the disposable checker.')
    assert result['state'] == 'COMPLETED' and observed(result, 'Verify')['selected_port'] == 'pass'
    assert candidate.read_text(encoding='utf-8') == CORRECT
    assert (driver.workspace/'check.py').read_text(encoding='utf-8') == CHECKER
    active = observed(result, 'Repair')
    assert 'DF_OUTSIDE_NEVER_READ_2902' not in active['output']['answer']
    sid = active['receipt']['session_id']
    slug = ''.join(c if c.isascii() and c.isalnum() else '-' for c in str(driver.workspace))
    history = Path.home() / '.claude' / 'projects' / slug / (sid + '.jsonl')
    rows = [json.loads(line) for line in history.read_text(encoding='utf-8').splitlines()]
    def content(row):
        items = (row.get('message') or {}).get('content')
        return items if isinstance(items, list) else []
    calls = [item for row in rows for item in content(row) if item.get('type') == 'tool_use']
    responses = [item for row in rows for item in content(row) if item.get('type') == 'tool_result']
    tools = [call['name'] for call in calls]
    assert tools.count('Bash') >= 2 and 'Read' in tools and set(tools) & {'Edit', 'Write'}
    assert len(responses) >= 4
    refusal = next(call for call in calls if call['name'] == 'Read' and call['input'].get('file_path') == str(outside))
    assert any(row.get('tool_use_id') == refusal['id'] and row.get('is_error') for row in responses)
    public = json.dumps(responses, ensure_ascii=False)
    assert 'DF_OUTSIDE_NEVER_READ_2902' not in public
    assert re.search(r'\[3,\s*6\]', public) and re.search(r'\[4,\s*10\]', public)
    save(driver.output / 'react-observations.json', [{'name': call['name'], 'id': call['id'],
         'observations': [row for row in responses if row.get('tool_use_id') == call['id']]} for call in calls])


def plan(driver):
    freeze_checker(driver.workspace)
    planner = driver.agent('Planner', 'claude', 'Plan', 'none',
        'Give a concise plain-text plan for a hypothetical integer doubler and validation of inputs 2 and 5. '
        'Include APPROVAL_PLAN_V1. Use only these supplied facts. No tools, external facts or commands are needed.', 'planner')
    executor = driver.agent('Executor', 'codex', 'Agent', 'workspace-write',
        'Use workspace tools and edit only candidate.py. On first execution write exactly the bytes of JSON string '
        f'{json.dumps(WRONG)} to demonstrate validation failure. After APPROVAL_PLAN_V2 write exactly the bytes of '
        f'{json.dumps(CORRECT)}. Run python3 -I -B check.py once and report its observed values. Do not change check.py.', 'developer')
    replanner = driver.agent('Replanner', 'claude', 'Plan', 'none',
        'Read the provided code observation. Produce a corrected text plan including APPROVAL_PLAN_V2, '
        'state why n+1 is wrong and tell the executor to use 2*n and verify 4,10. No tool calls.', 'planner')
    sub = flow('Human-approved repair iteration', [
        node('Seed','start'), node('Approval','approval', policy='single-reviewer',
             instructions='Review the actual current plan and explicitly approve the next workspace write.', timeout=180),
        agent_node('Executor', executor),
        node('Verify', 'tool', tool='read-file', permission='workspace-read', path='candidate.py'),
        node('Validation', 'validation', rules=json.dumps(CORRECT_SOURCE)),
        agent_node('Replanner', replanner), node('Success', 'output', format='json'), node('Feedback', 'output')],
        [edge('In','Seed','Approval'), edge('Approved','Approval','Executor','approved'),
         edge('Try','Executor','Verify'), edge('Verdict','Verify','Validation'), edge('Pass','Validation','Success','pass'),
         edge('Fail','Validation','Replanner','fail'), edge('Revised','Replanner','Feedback')])
    document = flow('Plan and Execute with two approval checkpoints', [node('Start','start'), agent_node('Planner',planner),
        node('PlanLoop','loop', max_iterations=2, exit_condition=CORRECT_SOURCE,body=sub),
        node('End','output',format='json')])
    candidate = driver.workspace/'candidate.py'

    def approval(row, _value, decision='approve'):
        round_number, = row['iteration_path']
        assert ('APPROVAL_PLAN_V1' if round_number == 1 else 'APPROVAL_PLAN_V2') in json.dumps(row['inputs'])
        assert candidate.exists() == (round_number == 2)
        return decision

    denied = driver.run('plan-rejected', document, 'Generate and assess the first plan.',
                        approve=lambda row, value: approval(row, value, 'reject'))
    assert denied['state'] == 'FAILED' and denied['run']['actual_calls'] == 1 and not candidate.exists()
    value = driver.run('plan-approved', document, 'Complete both explicitly authorized rounds.', approve=approval)
    assert value['state'] == 'COMPLETED'
    assert observed(value, 'PlanLoop/Validation', (1,))['selected_port'] == 'fail'
    assert observed(value, 'PlanLoop/Validation', (2,))['selected_port'] == 'pass'
    assert candidate.read_text(encoding='utf-8') == CORRECT
    assert (driver.workspace/'check.py').read_text(encoding='utf-8') == CHECKER
    assert 'APPROVAL_PLAN_V2' in observed(value, 'PlanLoop/Executor', (2,))['actual_request']
    assert observed(value, 'PlanLoop')['loop_exit'] == 'condition'


def multi(driver):
    freeze_checker(driver.workspace)
    original = git(driver.workspace, 'rev-parse', 'HEAD')
    planner = driver.agent('Planner', 'codex', 'Plan', 'none',
        'Produce a concise final plan for a Python doubler with two tests and an independent reviewer. '
        'Include DF_MULTI_PLAN. Do not create or modify any file.', 'planner')
    design = driver.run('multi-plan', flow('Plan development', [
        node('Start','start'), agent_node('Planner',planner), node('End','output')]), 'Plan the development fixture.')
    assert design['state'] == 'COMPLETED'
    plan_result = observed(design, 'Planner')['output']['answer']
    assert 'DF_MULTI_PLAN' in plan_result and not (driver.workspace/'candidate.py').exists()
    agents = [
        driver.agent('Developer','codex','Agent','workspace-write',
            'Use an authorized tool to write candidate.py, a standalone script reading one integer argv and printing one integer. '
            'Round 1 intentionally prints n+1 to exercise the red test. After actual upstream REJECT, edit it to 2*n. '
            'Verify the file was written. Do not change check.py, Git, or other files.', 'developer'),
        driver.agent('Tester','codex','Ask','workspace-read',
            'Inspect candidate.py and run python3 -I -B check.py using read-only tools. Report actual observed '
            'case values. Do not change any file. Independent deterministic verification follows.'),
        driver.agent('Reviewer','claude','Ask','workspace-read',
            'Independently read candidate.py and check.py via native Read. Use actual verifier facts in the upstream input. '
            'Return one bare JSON object with verdict, candidate_sha256, feedback, copying exact candidate_sha256 from '
            'the upstream result. On a failed test return REJECT and explain the repair. On passed tests and correct source '
            'return ACCEPT. Do not edit files. A separate deterministic program decides delivery.', 'reviewer'),
    ]
    result = driver.run('multi-gate', development_document(agents), plan_result)
    assert result['state'] == 'COMPLETED'
    stages = [observed(result, 'RepairLoop/'+role, (round_number,)) for round_number in (1,2)
              for role in ('Developer','Tester','Reviewer')]
    assert result['run']['actual_calls'] == 6 and len({row['receipt']['session_id'] for row in stages}) == 6
    assert 'DF_MULTI_PLAN' in stages[0]['actual_request'] and 'REJECT' in stages[3]['actual_request']
    gates = [observed(result, 'RepairLoop/Gate', (i,))['output']['result'] for i in (1,2)]
    assert [value['gate_verdict'] for value in gates] == ['REJECT', 'ACCEPT']
    assert gates[0]['candidate_sha256'] != gates[1]['candidate_sha256']
    assert git(driver.workspace, 'rev-parse', 'HEAD') == original
    assert result['run']['output']['result'] == gates[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', choices=('rag', 'react', 'plan', 'multi'))
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--allow-native-model-calls', action='store_true', required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.artifacts.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        parser.error('Choose a new artifact directory outside the source checkout.')
    output.mkdir(mode=0o700)
    workspace = output/'workspace'
    workspace.mkdir()
    start = time.monotonic()
    with service(source, output, workspace) as driver:
        for provider in ({'claude'} if args.scenario in ('rag','react') else {'claude','codex'}):
            metadata = driver.catalog(provider)
            assert metadata['installed'], provider
            save(output / (provider + '-cli.json'),
                 {key: metadata[key] for key in ('provider', 'version', 'installed') if key in metadata})
        {'rag': rag, 'react': react, 'plan': plan, 'multi': multi}[args.scenario](driver)
    print('elapsed seconds', round(time.monotonic() - start, 2), flush=True)


if __name__ == '__main__':
    main()
