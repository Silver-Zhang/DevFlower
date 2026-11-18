"""Structured contracts and branch execution with synthetic native CLI output."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest

from devflower_export import ExportError, compile_workflow
from workflow_output_contract import (OutputContractError, candidate_record, output_json,
                                      validate_structured_answer)
from workflow_script_runtime import WorkflowError, _run_directory, evaluate_condition


def graph():
    def agent(name, provider, kind, **options):
        return dict(id=name, name=name, kind='agent', execution='native-cli', provider=provider,
                    cwd='.', role=name, round_prompt='Task', output_prompt='Deliver',
                    native_output={'version': 1, 'kind': kind}, **options)
    return dict(version=1, name='P1 delivery', nodes=[
        agent('Developer', 'codex', 'result', artifact_paths=['a.py', 'b.py'],
              output_contract=[dict(field='result.data.done', type='boolean', required=True)]),
        agent('Reviewer', 'claude', 'review'),
    ], edges=[dict(id='handoff', source='Developer', target='Reviewer')])


def condition_graph():
    document = graph()
    document['nodes'] += [
        dict(id='Start', kind='start'),
        dict(id='Condition', kind='condition', config={'expression': {
            'field': 'result.verdict', 'op': 'equals', 'value': 'accept'}}),
        dict(id='Success', kind='agent', provider='codex', cwd='success'),
        dict(id='Correction', kind='agent', provider='claude', cwd='correction'),
        dict(id='SuccessOutput', kind='output'), dict(id='CorrectionOutput', kind='output'),
    ]
    document['edges'] += [
        dict(id='entry', source='Start', target='Developer'),
        dict(id='decision', source='Reviewer', target='Condition'),
        dict(id='yes', source='Condition', target='Success', source_port='true'),
        dict(id='no', source='Condition', target='Correction', source_port='false'),
        dict(id='accepted', source='Success', target='SuccessOutput'),
        dict(id='rejected', source='Correction', target='CorrectionOutput'),
    ]
    return document


def router_graph():
    classifier = dict(id='Classifier', name='Classifier', kind='agent', execution='native-cli',
                      provider='codex', cwd='.', role='Classify', round_prompt='Classify', output_prompt='Deliver',
                      native_output=dict(version=1, kind='result'), output_contract=[
                          dict(field='result.data.category', type='string', required=True),
                          dict(field='result.data.ready', type='boolean'),
                          dict(field='result.data.rank', type='number'),
                      ])
    tests = [('research', 'category', 'research'), ('development', 'ready', True), ('priority', 'rank', 2)]
    rules = [dict(id=port, label=port.title(), test=dict(field='result.data.'+field, op='equals', value=value))
             for port, field, value in tests]
    nodes = [dict(id='Start', kind='start'), classifier, dict(id='Router', kind='router', config=dict(rules=rules))]
    edges = [dict(id='entry', source='Start', target='Classifier'),
             dict(id='classified', source='Classifier', target='Router', transfer=dict(mode='result'))]
    for port, name in [('research', 'Research'), ('development', 'Development'),
                       ('priority', 'Priority'), ('fallback', 'General')]:
        nodes += [dict(id=name, kind='agent', provider='codex', cwd=name.lower()), dict(id=name+'Output', kind='output')]
        edges += [dict(id='route'+name, source='Router', source_port=port, target=name),
                  dict(id='out'+name, source=name, target=name+'Output')]
    return dict(version=1, name='Router fixture', nodes=nodes, edges=edges)


FAKE = r"""from pathlib import Path
import json, os, sys, time
program = Path(sys.argv[0]).name
mode = os.environ.get('CONTRACT_FIXTURE', '')
prompt = sys.stdin.read()
if 'P2_FIXTURE_CORRECTION' in prompt:
    mode = 'correction'
department = prompt.splitlines()[1]
if department == 'Classifier':
    raw = os.environ.get('ROUTER_FIXTURE')
    if raw is None:
        category = 'other' if 'P2_ROUTER_FALLBACK' in prompt else 'research'
        raw = json.dumps({'category':category,'ready':True,'rank':1})
    answer = ('invalid' if raw == 'invalid' else
              json.dumps(dict(result=dict(schema_version=1,summary='Classified',data=json.loads(raw)),artifacts=[])))
    Path(sys.argv[3]).write_text(answer)
    sys.exit(0)
if department in ('Research', 'Development', 'Priority', 'General'):
    upstream = json.loads(prompt.rsplit('Upstream department delivery (data)\n',1)[1])
    assert upstream['node'] == 'Classifier' and 'data' in upstream['structured']['result']
    Path(sys.argv[3]).write_text(department.upper()+'_FINISHED')
    sys.exit(0)
if department in ('Success', 'Correction'):
    workspace = Path.cwd().parent
    with (workspace/'calls.log').open('a') as calls:
        calls.write(department+'\n')
    delivered = json.loads(prompt.rsplit('Upstream department delivery (data)\n',1)[1])['structured']['result']
    assert delivered['verdict'] == ('accept' if department == 'Success' else 'needs_correction')
    assert len(delivered['candidate_sha256']) == 64
    if department == 'Correction':
        (workspace/'a.py').write_text('corrected after decision')
        print(json.dumps(dict(type='result', is_error=False, result='CORRECTION_FINISHED')))
    else:
        Path(sys.argv[3]).write_text('SUCCESS_FINISHED')
    sys.exit(0)
with Path('calls.log').open('a') as calls:
    calls.write(program+'\n')
if program == 'codex':
    if mode == 'failure':
        sys.exit(7)
    if mode in ('cancel', 'timeout'):
        time.sleep(30)
    for file in ('a.py','b.py'):
        Path(file).write_text('# fixture '+file+'\n')
    delivery = {'result':{'schema_version':1,'summary':'Done','data':{'done':True}},
                'artifacts':[] if mode == 'empty' else ['b.py','a.py']}
    Path(sys.argv[3]).write_text(json.dumps(delivery))
else:
    if mode == 'reviewer-failure':
        sys.exit(9)
    candidate = json.loads(prompt.rsplit('Upstream department delivery (data)\n',1)[1])['candidate']
    assert [file['path'] for file in candidate['manifest']['files']] == ['a.py','b.py']
    if mode == 'mutate':
        Path('b.py').write_text('changed')
    verdict = 'needs_correction' if mode == 'correction' else 'accept'
    issues = [dict(severity='major',file='a.py',description='Missing test',suggestion='Add test')] if mode == 'correction' else []
    review = {'result': {'schema_version': 1, 'summary':'Review', 'verdict':verdict, 'issues':issues,
                         'candidate_sha256':'0'*64 if mode == 'mismatch' else candidate['candidate_sha256']},
              'artifacts': []}
    if mode == 'unknown':
        print('{}')
    else:
        print(json.dumps(dict(type='result', is_error=False, result='invalid JSON' if mode == 'bad_json' else json.dumps(review))))
"""


class ContractTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='devflower-contract-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()

    def install_cli_fixture(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        for name in ('codex', 'claude'):
            exe = bin_dir / name
            exe.write_text('#!' + sys.executable + '\n' + FAKE)
            exe.chmod(0o700)
        return bin_dir

    def test_result_contract_strict_json_and_types(self):
        valid = '{"result":{"schema_version":1,"summary":"done","data":{"done":true}},"artifacts":[]}'
        spec = dict(kind='result', version=1, allowed_paths=[],
                    fields=[dict(field='result.data.done', type='boolean', required=True)])
        self.assertTrue(validate_structured_answer(valid, spec)['result']['data']['done'])
        bad = (valid.replace('"summary":"done",', ''), valid.replace('"summary"', '"extra"'),
               valid.replace('"summary":"done"', '"summary":"done","summary":"again"'),
               valid.replace('"done":true', '"done":1'), valid.replace('"done":true', '"done":NaN'),
               valid.replace('true', 'Infinity'), valid.replace('true', '1e999'),
               valid.replace('true', 'null'), valid.replace('true', '{}'), valid.replace('1', 'true', 1),
               valid.replace('"done":true', '"other":true'), valid.replace('"artifacts":[]', '"artifacts":["extra.py"]'),
               valid + ' ' * 80000)
        for raw in bad:
            with self.subTest(raw=raw[:100]), self.assertRaises(OutputContractError):
                validate_structured_answer(raw, spec)

    def test_review_contract_requires_consistent_verdict_issues_and_digest(self):
        candidate = dict(candidate_sha256='a'*64, manifest={'files': [{'path':'a.py'}]})
        spec = dict(kind='review', version=1, fields=[])
        valid = dict(schema_version=1, verdict='accept', issues=[], summary='Checked', candidate_sha256='a'*64)

        def answer(result):
            return json.dumps(dict(result=result, artifacts=[]))

        self.assertEqual(validate_structured_answer(answer(valid), spec, candidate)['result'], valid)
        issue = dict(severity='major', file='a.py', description='Missing check', suggestion='Add a check')
        for changes in (dict(schema_version=2), dict(verdict='pass'), dict(verdict='needs_correction'),
                        dict(candidate_sha256='1'*64), dict(summary=' '), dict(issues=[issue]), dict(extra=1),
                        *(dict(verdict='needs_correction', issues=[dict(issue, **patch)]) for patch in
                          (dict(severity='urgent'),dict(file='other.py'),dict(description=7),dict(suggestion=''))),
                        dict(verdict='needs_correction', issues=[{'severity':'major', 'file':'a.py'}])):
            with self.subTest(changes=changes), self.assertRaises(OutputContractError):
                validate_structured_answer(answer(dict(valid, **changes)), spec, candidate)
        with self.assertRaises(OutputContractError):
            validate_structured_answer('', spec, candidate)

    def test_candidate_sorted_canonical_and_bounded(self):
        a, b = [self.workspace / name for name in ('a.py', 'b.py')]
        a.write_bytes(b'a')
        b.write_bytes(b'b')
        sealed = candidate_record(self.workspace, ['b.py', 'a.py'])
        self.assertEqual(sealed, candidate_record(self.workspace, ['a.py', 'b.py']))
        self.assertEqual(sealed['candidate_sha256'], sha256(output_json(sealed['manifest']).encode()).hexdigest())
        self.assertEqual(sealed['manifest']['files'][0], dict(path='a.py', bytes=1, sha256=sha256(b'a').hexdigest()))
        (self.workspace/'link').symlink_to(a)
        (self.workspace/'link-dir').symlink_to(self.workspace, target_is_directory=True)
        (self.workspace/'empty-dir').mkdir()
        os.mkfifo(self.workspace/'fifo')
        os.link(a, self.workspace/'hard')
        with self.assertRaises(OutputContractError):
            candidate_record(self.workspace, ['hard'])
        (self.workspace/'hard').unlink()
        with b.open('wb') as stream:
            stream.truncate(1_000_001)
        for paths in ([], ['missing'], ['link'], ['link-dir/a.py'], ['fifo'], ['empty-dir'], ['b.py'],
                      ['../a.py'], ['a//b'], ['/a.py'], ['a.py','a.py'], ['.git/config'], ['api-key.txt']):
            with self.subTest(paths=paths), self.assertRaises(OutputContractError):
                candidate_record(self.workspace, paths)

    def test_compiler_rejects_missing_candidate_handoff_and_unknown_version(self):
        for mode in ('none', 'missing', 'version'):
            doc = graph()
            if mode == 'none':
                doc['edges'][0]['transfer'] = {'mode':'none'}
            elif mode == 'missing':
                doc['nodes'][0]['artifact_paths'] = []
            else:
                doc['nodes'][0]['native_output']['version'] = 2
            with self.subTest(mode=mode), self.assertRaises(ExportError):
                compile_workflow(doc, workspace=self.workspace, native_config=True)

    def test_real_subprocess_handoff_outcomes_and_candidate_drift(self):
        bin_dir = self.install_cli_fixture()
        doc = graph()
        doc['nodes'][0]['timeout'] = 3
        filename, source = compile_workflow(doc, workspace=self.workspace, native_config=True)
        self.assertEqual((filename, source), compile_workflow(doc, workspace=self.workspace, native_config=True))
        self.assertNotIn('from workflow_output_contract import', source)
        script = self.root / filename
        script.write_text(source)
        for mode in ('accept', 'correction', 'mutate', 'bad_json', 'mismatch', 'unknown', 'empty', 'failure', 'timeout', 'cancel'):
            with self.subTest(mode=mode):
                calls = self.workspace / 'calls.log'
                calls.unlink(missing_ok=True)
                run = self.root / mode
                command = [sys.executable, '-I', str(script), '--run-dir', str(run)]
                env = dict(os.environ, PATH=str(bin_dir), CONTRACT_FIXTURE=mode)
                with subprocess.Popen([*command, '--workspace', str(self.workspace)], env=env, cwd=self.root,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                    if mode == 'cancel':
                        deadline = time.monotonic() + 2
                        while not calls.exists() and time.monotonic() < deadline:
                            time.sleep(.01)
                        subprocess.run([*command, '--cancel'], capture_output=True, check=True, timeout=5)
                    _, err = child.communicate(timeout=10)
                successful = mode in ('accept', 'correction')
                self.assertEqual(child.returncode == 0, successful, err.decode())
                status = json.loads(subprocess.check_output([*command, '--status', '--details'], cwd=self.root))
                self.assertEqual(status['status'], 'COMPLETED' if successful else
                                 'CANCELLED' if mode == 'cancel' else 'STOPPED')
                expected = ['codex'] if mode in ('empty', 'failure', 'timeout', 'cancel') else ['codex','claude']
                self.assertEqual(calls.read_text().splitlines(), expected)
                reviews = [event for event in status['events'] if 'verdict' in event]
                self.assertEqual(len(reviews), int(successful))
                if successful:
                    self.assertEqual(reviews[0]['verdict'], 'accept' if mode == 'accept' else 'needs_correction')
                    self.assertTrue(status['candidate_current'])
                    candidate = json.loads((run / 'Developer' / 'candidate.json').read_text())
                    self.assertEqual(reviews[0]['candidate_sha256'], candidate['candidate_sha256'])
                    structured = run / 'Reviewer' / 'structured.json'
                    original = structured.read_bytes()
                    structured.write_bytes(original + b' ')
                    damaged = json.loads(subprocess.check_output([*command, '--status'], cwd=self.root))
                    self.assertEqual(damaged['status'], 'RECOVERY_REQUIRED')
                    self.assertIn('Structured evidence is missing or changed', damaged['reason'])
                    structured.write_bytes(original)
                    (self.workspace / 'a.py').write_text('edited after completion')
                    changed = json.loads(subprocess.check_output([*command, '--status'], cwd=self.root))
                    self.assertEqual(changed['status'], 'RECOVERY_REQUIRED')
                    self.assertFalse(changed['candidate_current'])
                    self.assertTrue((run / 'Reviewer' / 'structured.json').is_file())

    def test_condition_executes_only_selected_branch_and_verifies_journal(self):
        bin_dir = self.install_cli_fixture()
        doc = condition_graph()
        # Non-topological input order exercises topology and canonical true/false emission.
        doc['nodes'].reverse()
        doc['edges'].reverse()
        filename, source = compile_workflow(doc, workspace=self.workspace, native_config=True)
        self.assertEqual((filename, source), compile_workflow(doc, workspace=self.workspace, native_config=True))
        self.assertIn("if evaluate_condition(context, WORKFLOW['condition']", source)
        script = self.root / filename
        script.write_text(source)
        for mode, selected, port in (('accept', 'Success', 'true'), ('correction', 'Correction', 'false'),
                                     ('reviewer-failure', None, None), ('mutate', None, None), ('mismatch', None, None)):
            with self.subTest(mode=mode):
                project = self.root / mode
                project.mkdir()
                if selected:
                    # The other branch has a missing working directory, which cannot block this run.
                    (project / selected.lower()).mkdir()
                run = project / 'run'
                command = [sys.executable, '-I', str(script), '--run-dir', str(run)]
                env = dict(os.environ, PATH=str(bin_dir), CONTRACT_FIXTURE=mode)
                child = subprocess.run([*command, '--workspace', str(project)], env=env,
                                       cwd=self.root, capture_output=True, timeout=10)
                self.assertEqual(child.returncode, 0 if selected else 1, child.stderr.decode())
                status = json.loads(subprocess.check_output([*command, '--status', '--details'], cwd=self.root))
                self.assertEqual(status['status'], 'COMPLETED' if selected else 'STOPPED')
                calls = ['codex', 'claude'] + ([selected] if selected else [])
                self.assertEqual((project/'calls.log').read_text().splitlines(), calls)
                events = status['events']
                started = [event['node'] for event in events if event['kind'] == 'PROCESS_STARTED']
                self.assertEqual(started, ['Developer', 'Reviewer'] + ([selected] if selected else []))
                self.assertEqual(sum(event['kind'] == 'CONDITION_EVALUATED' for event in events), int(bool(selected)))
                if not selected:
                    self.assertNotIn('condition', status)
                    self.assertFalse((run/'result.txt').exists())
                    continue
                decision = status['condition']
                self.assertEqual(decision['selected_port'], port)
                self.assertEqual(decision['branches'][port], [selected, selected+'Output'])
                self.assertEqual(status['completed_nodes'], started)
                self.assertEqual(status['candidate_current'], mode == 'accept')
                self.assertEqual((run/'result.txt').read_text(), selected.upper()+'_FINISHED')
                journal = run / 'events.jsonl'
                wrong = 'false' if port == 'true' else 'true'
                events[-1:-1] = [dict(kind='CALL_INTENT', node=decision['branches'][wrong][0])]
                journal.write_text(''.join(json.dumps(event)+'\n' for event in events))
                damaged = json.loads(subprocess.check_output([*command, '--status'], cwd=self.root))
                self.assertEqual(damaged['status'], 'RECOVERY_REQUIRED')
                self.assertIn('unselected branch', damaged['reason'])

    def test_condition_rechecks_candidate_after_the_review_already_passed(self):
        artifact = self.workspace / 'a.py'
        artifact.write_text('reviewed contents')
        candidate = candidate_record(self.workspace, ['a.py'])
        envelope = dict(result=dict(schema_version=1, verdict='accept', issues=[], summary='Checked',
                                    candidate_sha256=candidate['candidate_sha256']), artifacts=[])
        structured = validate_structured_answer(output_json(envelope), dict(kind='review', version=1, fields=[]), candidate)
        artifact.write_text('changed after review and before decision')
        with _run_directory(self.root) as fd, self.assertRaisesRegex(WorkflowError, 'before Condition evaluation'):
            evaluate_condition(dict(directory_fd=fd, directory=self.root, workspace=self.workspace),
                               dict(node='Condition', reviewer='Reviewer'),
                               dict(node='Reviewer', structured=structured), candidate, None)
        event = json.loads((self.root/'events.jsonl').read_text())
        self.assertEqual(event['kind'], 'CONDITION_REJECTED')

    def test_condition_compiler_rejects_invalid_topology_and_expression(self):
        for mode in ('multiple', 'cycle', 'merge', 'port', 'missing-output', 'unreachable',
                     'no-start', 'no-review', 'expression', 'no-handoff', 'router'):
            with self.subTest(mode=mode), self.assertRaises(ExportError):
                doc = condition_graph()
                nodes = {node['id']: node for node in doc['nodes']}
                edges = {edge['id']: edge for edge in doc['edges']}
                if mode == 'multiple':
                    nodes['Success']['kind'] = 'condition'
                    nodes['Success'].pop('provider')
                    nodes['Success'].pop('cwd')
                    edges['accepted']['source_port'] = 'true'
                elif mode == 'cycle':
                    edges['accepted']['target'] = 'Condition'
                elif mode == 'merge':
                    edges['rejected']['target'] = 'SuccessOutput'
                elif mode == 'port':
                    edges['no']['source_port'] = 'true'
                elif mode == 'missing-output':
                    nodes['CorrectionOutput']['kind'] = 'agent'
                elif mode == 'unreachable':
                    doc['nodes'].append(dict(id='Orphan', kind='agent'))
                elif mode == 'no-start':
                    doc['nodes'].remove(nodes['Start'])
                    doc['edges'].remove(edges['entry'])
                elif mode == 'no-review':
                    nodes['Reviewer'].pop('native_output')
                elif mode == 'expression':
                    nodes['Condition']['config']['expression']['op'] = 'contains'
                elif mode == 'no-handoff':
                    edges['decision']['transfer'] = {'mode': 'none'}
                elif mode == 'router':
                    doc['nodes'].append(dict(id='ExtraRouter', kind='router'))
                compile_workflow(doc, workspace=self.workspace, native_config=True)

    def test_condition_branch_can_go_directly_to_output(self):
        doc = condition_graph()
        doc['nodes'] = [node for node in doc['nodes'] if node['id'] not in ('Success', 'Correction')]
        doc['edges'] = [dict(edge, target=edge['target']+'Output') if edge['source']=='Condition' else edge
                        for edge in doc['edges'] if edge['source'] not in ('Success', 'Correction')]
        filename, source = compile_workflow(doc, workspace=self.workspace, native_config=True)
        script = self.root / filename
        script.write_text(source)
        env = dict(os.environ, PATH=str(self.install_cli_fixture()))
        run = self.root / 'direct'
        command = [sys.executable, '-I', str(script), '--run-dir', str(run)]
        output = subprocess.check_output([*command, '--workspace', str(self.workspace)], env=env)
        status = json.loads(subprocess.check_output([*command, '--status']))
        self.assertEqual(status['condition']['branches']['true'], ['SuccessOutput'])
        self.assertEqual(status['completed_nodes'], ['Developer', 'Reviewer'])
        self.assertEqual(json.loads(output)['result']['verdict'], 'accept')

    def test_router_routes_first_match_second_match_number_and_fallback(self):
        bin_dir = self.install_cli_fixture()
        original = router_graph()
        for mode, data, port, selected in (
            ('first', dict(category='research', ready=True, rank=2), 'research', 'Research'),
            ('second', dict(category='development', ready=True, rank=1), 'development', 'Development'),
            ('number', dict(category='other', ready=False, rank=2.0), 'priority', 'Priority'),
            ('fallback', dict(category='other', ready=False, rank=1), 'fallback', 'General'),
            ('reordered', dict(category='research', ready=True, rank=2), 'development', 'Development'),
            ('missing-later-field', dict(category='research', rank=1), None, None),
            ('boolean-is-not-number', dict(category='research', ready=True, rank=True), None, None),
            ('number-is-not-boolean', dict(category='research', ready=1, rank=1), None, None),
            ('invalid-output', 'invalid', None, None),
        ):
            with self.subTest(mode=mode):
                doc = router_graph() if mode == 'reordered' else original
                if mode == 'reordered':
                    rules = doc['nodes'][2]['config']['rules']
                    rules[0], rules[1] = rules[1], rules[0]
                project = self.root/mode
                project.mkdir()
                if selected:
                    # Unselected branch directories do not exist.
                    (project/selected.lower()).mkdir()
                name, source = compile_workflow(doc, workspace=project, native_config=True)
                self.assertEqual((name, source), compile_workflow(doc, workspace=project, native_config=True))
                self.assertIn("port = evaluate_router(context, WORKFLOW['router']", source)
                script = project/name
                script.write_text(source)
                run = project/'run'
                command = [sys.executable, '-I', str(script), '--run-dir', str(run)]
                env = dict(os.environ, PATH=str(bin_dir), ROUTER_FIXTURE=data if data == 'invalid' else json.dumps(data))
                child = subprocess.run([*command, '--workspace', str(project)], env=env,
                                       cwd=self.root, capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 0 if selected else 1, child.stderr)
                status = json.loads(subprocess.check_output([*command, '--status', '--details'], cwd=self.root))
                self.assertEqual(status['status'], 'COMPLETED' if selected else 'STOPPED')
                events = status['events']
                self.assertEqual([e['node'] for e in events if e['kind'] == 'PROCESS_STARTED'],
                                 ['Classifier'] + ([selected] if selected else []))
                decisions = [event for event in events if event['kind'] == 'ROUTER_EVALUATED']
                if not selected:
                    self.assertNotIn('router', status)
                    self.assertEqual(decisions, [])
                    kind = 'ROUTER_REJECTED' if mode == 'missing-later-field' else 'OUTPUT_CONTRACT_FAILED'
                    self.assertIn(kind, [event['kind'] for event in events])
                    continue
                self.assertEqual(len(decisions), 1)
                decision = status['router']
                self.assertEqual(decision['source_node'], 'Classifier')
                self.assertEqual(decision['matched_rule'], None if port == 'fallback' else port)
                self.assertEqual(decision['selected_port'], port)
                self.assertEqual(decision['branches'][port], [selected, selected+'Output'])
                self.assertEqual(json.loads(decision['input_json']), {'result.data.'+key:value for key, value in data.items()})
                self.assertEqual(decision['input_sha256'], sha256((run/'Classifier'/'structured.json').read_bytes()).hexdigest())
                self.assertEqual(child.stdout.strip(), selected.upper()+'_FINISHED')
                if mode == 'first':
                    decisions[0]['matched_rule'] = 'development'
                    (run/'events.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
                    damaged = json.loads(subprocess.check_output([*command, '--status'], cwd=self.root))
                    self.assertEqual(damaged['status'], 'RECOVERY_REQUIRED')
                    self.assertNotIn('router', damaged)
                    self.assertIn('verified structured input', damaged['reason'])

    def test_router_rejects_unsupported_rules_graphs_and_ports_at_compile_time(self):
        for mode, message in (
            ('operator', 'only equals'), ('undeclared', 'declared as'), ('value-type', 'type does not match'),
            ('object', 'string, finite number or boolean'), ('nonfinite', 'finite number'), ('absent-contract', 'structured result Agent'),
            ('optional-type-any', 'declared as'), ('reserved', 'reserved Router'), ('duplicate', 'Duplicate'),
            ('fallback', 'Missing: fallback'), ('unknown-port', 'Unsupported port'), ('merge', 'branch merges'),
            ('cycle', 'reachable from the root'), ('nested', 'multiple control nodes'), ('mixed', 'multiple control nodes'),
        ):
            with self.subTest(mode=mode), self.assertRaisesRegex(ExportError, message):
                doc = router_graph()
                classifier = doc['nodes'][1]
                rules = doc['nodes'][2]['config']['rules']
                by_id = {edge['id']: edge for edge in doc['edges']}
                if mode == 'operator':
                    rules[0]['test']['op'] = 'contains'
                elif mode == 'undeclared':
                    rules[0]['test']['field'] = 'result.data.unknown'
                elif mode == 'value-type':
                    rules[2]['test']['value'] = True
                elif mode == 'object':
                    rules[0]['test']['value'] = {}
                elif mode == 'nonfinite':
                    rules[2]['test']['value'] = float('inf')
                elif mode == 'absent-contract':
                    classifier.pop('native_output')
                    classifier.pop('output_contract')
                elif mode == 'optional-type-any':
                    classifier['output_contract'][0]['type'] = 'any'
                elif mode in ('reserved', 'duplicate'):
                    rules[1]['id'] = 'fallback' if mode == 'reserved' else rules[0]['id']
                elif mode == 'fallback':
                    doc['edges'].remove(by_id['routeGeneral'])
                elif mode == 'unknown-port':
                    by_id['routeGeneral']['source_port'] = 'unknown'
                elif mode == 'merge':
                    by_id['outGeneral']['target'] = 'ResearchOutput'
                    doc['nodes'].pop()
                elif mode == 'cycle':
                    doc['nodes'] += [dict(id=node, kind='agent') for node in ('CycleA', 'CycleB')]
                    doc['edges'] += [dict(id='cycle1', source='CycleA', target='CycleB'),
                                     dict(id='cycle2', source='CycleB', target='CycleA')]
                else:
                    doc['nodes'].append(dict(id='Extra', kind='router' if mode=='nested' else 'condition'))
                compile_workflow(doc, workspace=self.workspace, native_config=True)
