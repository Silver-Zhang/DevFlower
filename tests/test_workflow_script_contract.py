"""P1 compiler and isolated process acceptance with synthetic native CLI output."""
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


FAKE = r"""from pathlib import Path
import json, os, sys, time
program = Path(sys.argv[0]).name
mode = os.environ.get('CONTRACT_FIXTURE', '')
prompt = sys.stdin.read()
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
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        for name in ('codex', 'claude'):
            exe = bin_dir / name
            exe.write_text('#!' + sys.executable + '\n' + FAKE)
            exe.chmod(0o700)
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
