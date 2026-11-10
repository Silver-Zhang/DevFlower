"""Mechanical evidence checks and a separate real Bubblewrap verifier test."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch
import os
import socket
import subprocess
import tempfile

from orchestration.common import Blocked, canonical, digest, tree_hash
from v2 import development_evidence as evidence
from v2.git_workspace import bind_workspace
from v2.store import DraftStore


class DevelopmentEvidenceTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('-c', 'user.email=fixture@example.invalid', '-c', 'user.name=Fixture',
                 'commit', '--allow-empty', '-qm', 'fixture')
        self.state = self.base / 'state'
        store = DraftStore(self.state, [self.root])
        workspace = store.open_workspace(str(self.root))['id']
        source = bind_workspace(store, workspace, require_origin=False)
        self.run = dict(id='run', workspace=workspace, graph={'development': {'source': dict(
            binding=source.identity, branch=source.branch, head=source.head)}})
        self.verify = dict(id='verify', round=1, iteration_path=[1], node='loop/verify')
        self.rt = SimpleNamespace(storage=self.state, check_operator=lambda: None,
            store=store,
            flow=SimpleNamespace(get=lambda *_: self.run, list=lambda _: []))

    def git(self, *args):
        subprocess.run(['/usr/bin/git', '-C', str(self.root), *args], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def checker(self, content='print("true")\n'):
        (self.root / 'check.py').write_text(content)
        return dict(path='check.py', expected_json=True,
                    entry_sha256=evidence.verification_entry_sha(self.root, 'check.py'))

    def test_manifest_is_exact_including_untracked_and_rejects_unpinned_links(self):
        self.checker()
        initial = evidence.current_candidate(self.root)
        self.assertEqual(initial['candidate_sha256'], tree_hash(self.root))
        (self.root / 'untracked').write_text('changed')
        self.assertNotEqual(initial, evidence.current_candidate(self.root))
        target = self.base / 'private'
        target.write_text('test marker')
        link = self.root / 'escape'
        link.symlink_to(target)
        with self.assertRaises(Blocked):
            evidence.current_candidate(self.root)
        link.unlink()
        os.link(target, link)
        with self.assertRaises(Blocked):
            evidence.current_candidate(self.root)

    def test_operator_checker_mutation_never_starts_process(self):
        frozen = self.checker()
        (self.root / 'check.py').write_text('print("false")\n')
        with patch.object(evidence, 'verify') as spawn:
            with self.assertRaisesRegex(Blocked, '冻结的文件不同'):
                evidence.verify_candidate(self.rt, 'run', self.verify, frozen)
        spawn.assert_not_called()

    def test_git_head_or_branch_change_refuses_verifier_before_spawn(self):
        frozen = self.checker()
        self.git('branch', '-m', 'changed')
        with patch.object(evidence, 'verify') as spawn:
            with self.assertRaisesRegex(Blocked, '启动时的候选不同'):
                evidence.verify_candidate(self.rt, 'run', self.verify, frozen)
        spawn.assert_not_called()

    def test_gate_needs_exact_unchanged_candidate_checks_and_three_distinct_sessions(self):
        node = self.checker()
        result = self.process_result()
        result["acceptance_sha256"] = digest(canonical(evidence.verification_spec(node)))
        with patch.object(evidence, 'verify', return_value=[result]):
            proof = evidence.verify_candidate(self.rt, 'run', self.verify, node)
        definition = dict(graph={'nodes': []})
        definition['id'] = self.run['definition_id'] = digest(canonical(definition))
        self.rt.flow.get = lambda kind, _id: self.run if kind == 'run' else definition
        row = dict(id='gate', node='loop/gate', round=1, iteration_path=[1])
        roles = dict(verification_node='verify', developer_node='developer', tester_node='tester', reviewer_node='reviewer')
        session = lambda role: dict(receipt=dict(session_id=role, process=result['process']), output=dict(
            answer='', result=dict(verdict='ACCEPT', candidate_sha256=proof['result']['candidate_sha256'], feedback='reviewed')))
        all_roles = {role: session(role) for role in ('developer', 'tester', 'reviewer')}
        fixed = dict(self.verify, output=proof)
        with (patch.object(evidence, 'role_activation', side_effect=lambda _rt, _run, _row, role:
                           fixed if role == 'verify' else {'id': role}),
              patch.object(evidence, 'frozen_node', return_value=node),
              patch.object(evidence, 'read_completion', side_effect=lambda _rt, _run, role, _def, _steps: all_roles[role['id']])):
            run = lambda: evidence.delivery_gate(self.rt, 'run', row, roles)['result']['gate_verdict']
            self.assertEqual(run(), 'ACCEPT')
            all_roles['reviewer']['output']['result']['verdict'] = 'ACCEPT_WITH_LIMITATIONS'
            self.assertEqual(run(), 'ACCEPT_WITH_LIMITATIONS')
            all_roles['reviewer']['output']['result']['candidate_sha256'] = '0' * 64
            self.assertEqual(run(), 'REJECT')
            all_roles['reviewer']['receipt']['session_id'] = 'developer'
            with self.assertRaisesRegex(Blocked, '不同的真实会话'):
                run()
            all_roles['reviewer'] = session('reviewer')
            failed = self.process_result(passed=False)
            failed["acceptance_sha256"] = result["acceptance_sha256"]
            with patch.object(evidence, 'verify', return_value=[failed]):
                fixed['output'] = evidence.verify_candidate(self.rt, 'run', self.verify, node)
            self.assertEqual(run(), 'REJECT')
            (self.root / 'new.txt').write_text('candidate changed after review')
            with self.assertRaisesRegex(Blocked, '当前候选'):
                run()

    @staticmethod
    def process_result(*, passed=True):
        return dict(id=evidence.CONTRACT, run_id='run', round=1,
                    process=dict(exit=0, timeout=False, overflow=False, cancelled=False),
                    counts=dict(tests=1, failures=int(not passed), errors=0, skipped=0),
                    passed=passed, result_sha256='a'*64, acceptance_sha256='mocked')

    @skipUnless(os.environ.get('DEVFLOWER_REQUIRE_BWRAP_VERIFIER') == '1', 'requires real Linux Bubblewrap namespaces')
    def test_real_readonly_verifier_denies_private_file_write_and_host_loopback(self):
        marker = self.base / 'outside'
        marker.write_text('fixture only')
        with socket.socket() as host:
            host.bind(('127.0.0.1', 0))
            host.listen()
            node = self.checker(f"""
from pathlib import Path
import json
import os
import socket
result = {{}}
for name, action in [
    ('workspace', lambda: Path('check.py').read_text()),
    ('private', lambda: Path({str(marker)!r}).read_text()),
    ('write', lambda: Path('unauthorized').write_text('attempt')),
    ('network', lambda: socket.create_connection(('127.0.0.1', {host.getsockname()[1]}), 1)),
    ('auth', lambda: os.environ['DEVFLOWER_FIXTURE_SECRET']),
]:
    try:
        action()
        result[name] = True
    except (OSError, KeyError):
        result[name] = False
print(json.dumps(result))
""")
            node['expected_json'] = dict(workspace=True, private=False, write=False, network=False, auth=False)
            with patch.dict(os.environ, DEVFLOWER_FIXTURE_SECRET='synthetic-marker'):
                verdict = evidence.verify_candidate(self.rt, 'run', self.verify, node)['result']
        self.assertTrue(verdict['passed'], verdict)
        self.assertEqual(verdict['counts'], dict(tests=1, failures=0, skipped=0, errors=0))
        self.assertFalse((self.root / 'unauthorized').exists())
