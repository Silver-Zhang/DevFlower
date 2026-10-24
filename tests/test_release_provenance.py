"""Adversarial v3 provenance tests; ALL authority/result bytes are SYNTHETIC."""
import os
import pty
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_workbench_stable as boundary
from test_workbench_stable import StableFixture, ROOT, serve
from orchestration.common import Blocked, canonical, digest, manifest, save
from orchestration.release_provenance import git_tree, object_id
from orchestration.trust import TrustedRelease


class ProvenanceTests(unittest.TestCase):
    def fixture(self):
        f = StableFixture(self); f.approval(); return f

    def refused(self, f):
        with self.assertRaises((Blocked, ValueError, OSError)): f.launch()
        self.assertFalse((Path(f.operator['storage'])/'usable.sqlite').exists())

    def test_legacy_record_stays_usable_but_cannot_promote_v3(self):
        f = StableFixture(self); entries = manifest(ROOT)
        save(f.record, dict(version=1, root=str(ROOT), files=entries, candidate_sha256=digest(canonical(entries)),
            git_commit='not-a-git-commit', git_tree='not-a-git-tree', review_verdict='ACCEPT',
            review_receipt_sha256='b'*64, approval_ref='invented'))
        f.operator['stable'] = dict(record=str(f.record), sha256=digest(f.record.read_bytes()))
        self.assertEqual(TrustedRelease.bound(f.operator['stable']).data['version'], 1)
        self.refused(f)

    def test_invented_receipt_is_not_immutable_native_evidence(self):
        f = self.fixture(); e = f.evidence
        e.paths['native'].write_text('b'*64)
        # Re-pin every enclosing reference to the invented bytes.
        review = e.docs['review']; review['native_result'] = e.ref('native'); save(e.paths['review'], review)
        gate = e.docs['gate']; gate['review'] = e.ref('review'); save(e.paths['gate'], gate)
        owner = e.docs['owner']; owner.update(review=e.ref('review'), gate=e.ref('gate')); save(e.paths['owner'], owner)
        e.data['provenance'].update(review=e.ref('review'), gate=e.ref('gate'), owner=e.ref('owner'))
        save(f.record, e.data); f.operator['stable']['sha256'] = digest(f.record.read_bytes())
        self.refused(f)

    def test_limited_blocked_or_failed_contract_report_cannot_be_accepted(self):
        for verdict in ('ACCEPT_WITH_LIMITATIONS', 'BLOCKED', 'NEEDS_CORRECTION'):
            with self.subTest(verdict=verdict):
                f = self.fixture(); e = f.evidence; e.docs['report']['verdict'] = verdict
                e.sync_report(); e.seal(); self.refused(f)
        for status in ('FAIL', 'BLOCKED', 'NOT_APPLICABLE'):
            with self.subTest(status=status):
                f = self.fixture(); e = f.evidence; e.docs['report']['contracts'][0]['status'] = status
                e.sync_report(); e.seal(); self.refused(f)

    def test_cross_candidate_contract_run_round_and_native_report_are_rejected(self):
        for field, value in [('candidate_sha256','0'*64), ('acceptance_sha256','0'*64), ('run_id','other'), ('round',2)]:
            with self.subTest(field=field):
                f = self.fixture(); e = f.evidence; e.docs['report'][field] = value
                e.sync_report(); e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['native']['report']['verdict'] = 'BLOCKED'
        e.docs['native']['raw_report_sha256'] = digest(canonical(e.docs['native']['report']))
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['owner']['identity']['acceptance_sha256'] = 'f'*64
        e.seal(); self.refused(f)

    def test_required_findings_and_obligations_cannot_be_omitted(self):
        for status in ('OPEN', 'BLOCKED', 'NOT_REPRODUCED'):
            with self.subTest(status=status):
                f = self.fixture(); e = f.evidence
                e.docs['report']['findings'] = [dict(id='fixture-P1', severity='P1', status=status,
                    title='SYNTHETIC required finding', contract_ids=['fixture-contract'], evidence_refs=['fixture-check'])]
                e.sync_report(); e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['owner']['required_contracts'].append('missing-contract')
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['owner']['required_contracts'] = []
        e.seal(); self.refused(f)

    def test_native_session_model_effort_and_actual_process_must_match(self):
        changes = [('session_id','not-a-native-uuid'), ('requested_model','unknown'), ('requested_effort',''),
            ('resolved_effort',''), ('raw_report_sha256','f'*64)]
        for key, value in changes:
            with self.subTest(field=key):
                f = self.fixture(); e = f.evidence; e.docs['native'][key] = value; e.seal(); self.refused(f)
        for key, value in [('exit',1), ('exit',False), ('timeout',True), ('cancelled',True), ('overflow',True)]:
            with self.subTest(process=key, value=value):
                f = self.fixture(); e = f.evidence; e.docs['native']['process'][key] = value
                e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['owner']['reviewer_session_id'] = e.docs['implementation']['session_id']
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['owner']['reviewer_session_id'] = e.docs['native']['session_id'] = e.docs['implementation']['session_id'].upper()
        e.seal(); self.refused(f)

    def test_fake_gate_missing_verification_or_wrong_raw_report_cannot_authorize(self):
        for patch_value in ({'verdict':'BLOCKED'}, {'verification':[]}, {'report_sha256':'f'*64},
                {'process':dict(exit=2,timeout=False,cancelled=False,overflow=False)}):
            with self.subTest(change=patch_value):
                f = self.fixture(); e = f.evidence; e.docs['gate'].update(patch_value)
                e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['gate']['verification'][0]['counts']['failures'] = 1
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['gate']['identity']['round'] = 2
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['gate']['verification'][0]['passed'] = 'false'
        e.seal(); self.refused(f)

    def test_missing_changed_grant_or_wrong_owner_scope_refuses_before_storage(self):
        f = self.fixture(); f.evidence.paths['owner'].unlink(); self.refused(f)
        f = self.fixture(); f.evidence.paths['user_grant'].write_text('changed grant'); self.refused(f)
        for field, value in [('scope',['start']), ('git_commit','0'*40), ('root','/tmp/other-install')]:
            with self.subTest(field=field):
                f = self.fixture(); e = f.evidence; e.docs['owner'][field] = value; e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.paths['user_grant'].write_bytes(b''); e.seal(); self.refused(f)

    def test_shape_valid_false_git_identity_and_wrong_commit_tree_are_refused(self):
        for field in ('git_commit', 'git_tree'):
            with self.subTest(field=field):
                f = self.fixture(); e = f.evidence
                e.data[field] = e.docs['owner'][field] = '9'*40; e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        raw = e.paths['commit'].read_bytes().replace(e.data['git_tree'].encode(), b'9'*40)
        e.paths['commit'].write_bytes(raw)
        e.data['git_commit'] = e.docs['owner']['git_commit'] = object_id('commit', raw)
        e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        raw = e.paths['commit'].read_bytes().replace(b'Synthetic <fixture@example.invalid> 0 +0000', b'invalid-author')
        e.paths['commit'].write_bytes(raw)
        e.data['git_commit'] = e.docs['owner']['git_commit'] = object_id('commit', raw)
        e.seal(); self.refused(f)

    def test_all_evidence_paths_reject_symlinks_hardlinks_and_ancestor_links(self):
        for role in ('contract', 'report', 'native', 'implementation', 'review', 'gate', 'owner', 'commit', 'supervisor', 'user_grant'):
            for kind in ('symlink', 'hardlink'):
                with self.subTest(role=role, kind=kind):
                    f = self.fixture(); p = f.evidence.paths[role]; backup = p.with_suffix('.backup')
                    p.rename(backup)
                    if kind == 'symlink': p.symlink_to(backup)
                    else: os.link(backup, p)
                    self.refused(f)
        f = self.fixture(); e = f.evidence; alias = f.base/'alias'; alias.symlink_to(f.base, target_is_directory=True)
        e.data['provenance']['contract']['path'] = str(alias/e.paths['contract'].name)
        save(f.record, e.data); f.operator['stable']['sha256'] = digest(f.record.read_bytes()); self.refused(f)

    def test_evidence_never_overlaps_writable_roots_or_credentials(self):
        for location in ('workspace', 'storage', 'native', 'external'):
            with self.subTest(location=location):
                f = self.fixture(); e = f.evidence
                parent = f.workspace if location == 'workspace' else f.base/location
                parent.mkdir(exist_ok=True); target = parent/'SYNTHETIC-grant'
                e.paths['user_grant'].rename(target); e.paths['user_grant'] = target
                e.docs['owner']['user_grant'] = e.ref('user_grant'); e.seal()
                if location == 'external':
                    exe = f.operator['profiles']['fixture']['executable']; sha = f.operator['dependencies'][exe]
                    save(f.policy_file, dict(version=1, external_steps={'fixture':dict(kind='verify', argv=[exe],
                        executable_sha256=sha, dependencies={exe:sha}, write_paths=[str(parent)])}))
                self.refused(f)
        for role in ('record', 'owner', 'native', 'user_grant', 'policy'):
            with self.subTest(overlay=role):
                f = self.fixture(); path = f.record if role == 'record' else f.policy_file if role == 'policy' else f.evidence.paths[role]
                f.operator['profiles']['fixture']['readonly_files'] = [[str(path),'.codex/auth.json']]
                f.operator['dependencies'][str(path)] = digest(path.read_bytes()); self.refused(f)
        f = self.fixture(); path = f.evidence.paths['owner']
        f.operator['profiles']['fixture']['read_paths'].append(str(path))
        f.operator['dependencies'][str(path)] = digest(path.read_bytes()); self.refused(f)

    def test_poststart_mutation_or_same_bytes_swap_blocks_execution_and_queries(self):
        request = boundary.StableEntryTests().request
        for role in ('owner', 'contract', 'native', 'gate', 'commit', 'user_grant', 'supervisor'):
            with self.subTest(role=role):
                f = self.fixture(); rt = f.launch(); url, token = serve(self, rt)
                p = f.evidence.paths[role]; p.write_bytes(p.read_bytes()+b' ')
                self.assertEqual(request(url,token,'/api/status')[0],400)
                with patch('studio.workbench_runtime.subprocess.Popen') as spawn:
                    with self.assertRaises(Blocked): rt.spawn('fixture')
                    with self.assertRaises(Blocked): rt.session_center._spawn('fixture')
                    spawn.assert_not_called()
        f = self.fixture(); rt = f.launch(); p = f.evidence.paths['owner']; raw = p.read_bytes()
        replacement = p.with_suffix('.replacement'); replacement.write_bytes(raw); replacement.replace(p)
        with self.assertRaises(Blocked): rt.check_operator()

    def test_git_tree_without_repository_matches_independent_git_object_hashes(self):
        f = StableFixture(self); root = f.workspace
        (root/'a').mkdir(); (root/'a'/'run').write_bytes(b'#!/bin/sh\n'); (root/'a'/'run').chmod(0o755)
        (root/'a.c').write_bytes(b'ordinary bytes\x00\xff')
        def oid(kind, raw):
            return subprocess.run(['git','hash-object','-t',kind,'--stdin'], input=raw, capture_output=True,
                check=True, cwd=root).stdout.decode().strip()
        blob_run = oid('blob', (root/'a'/'run').read_bytes()); blob_c = oid('blob', (root/'a.c').read_bytes())
        subtree = oid('tree', b'100755 run\0'+bytes.fromhex(blob_run))
        expected = oid('tree', b'100644 a.c\0'+bytes.fromhex(blob_c)+b'40000 a\0'+bytes.fromhex(subtree))
        self.assertFalse((root/'.git').exists()); self.assertEqual(git_tree(root,manifest(root)), expected)

    def test_detached_pty_entry_reconstructs_exact_binding_and_rejects_proof_drift(self):
        f = self.fixture(); rt = f.launch()
        harness = '''import json,runpy,sys
sys.path.insert(0,sys.argv[1]);script=sys.argv[2];sys.argv=[script,*sys.argv[3:]]
from studio.session_center import SessionCenter
def boundary(self,*args):
    print(json.dumps(dict(binding=self.runtime.trust.binding(),candidate=self.runtime.candidate_sha256,
        policy=self.runtime.policy_sha,proofs=sorted(map(str,self.runtime.trust.protected_paths)))))
    raise SystemExit(0)
SessionCenter.resolve=boundary
runpy.run_path(script,run_name='__main__')
'''
        argv = [sys.executable,'-B','-c',harness,str(ROOT/'tools'),str(ROOT/'tools/devflower_native_terminal.py'),
            '--operator',str(rt.operator_file),'--operator-sha256',rt.operator_sha256,
            '--candidate-sha256',rt.candidate_sha256,'--policy',str(rt.policy_file),'--policy-sha256',rt.policy_sha,
            '--workspace','SYNTHETIC','--handle','SYNTHETIC']
        def terminal():
            master, slave = pty.openpty()
            try:
                child = subprocess.run(argv,stdin=slave,stdout=slave,stderr=subprocess.PIPE,text=True)
            finally: os.close(slave)
            output = bytearray()
            try:
                while True:
                    try: block = os.read(master,65536)
                    except OSError: break  # PTY EOF is EIO on Linux.
                    if not block: break
                    output.extend(block)
            finally: os.close(master)
            child.stdout = output.decode(); return child
        child = terminal()
        self.assertEqual(child.returncode,0,child.stderr)
        import json
        result = json.loads(child.stdout)
        self.assertEqual(result['binding'],f.operator['stable']); self.assertEqual(result['candidate'],f.candidate)
        self.assertEqual(result['policy'],rt.policy_sha)
        self.assertIn(str(f.evidence.paths['owner']),result['proofs'])
        p = f.evidence.paths['user_grant']; p.write_bytes(p.read_bytes()+b' ')
        child = terminal()
        self.assertNotEqual(child.returncode,0); self.assertEqual(child.stdout,'')


if __name__ == '__main__': unittest.main()
