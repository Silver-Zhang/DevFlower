"""SYNTHETIC external invocation observations only; no native models or approval."""
import copy
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_workbench_stable import StableFixture, serve
import test_workbench_stable as boundary
from orchestration.common import Blocked, digest, manifest, save
from orchestration.release_provenance import ReleaseProvenance
from stable_provenance_fixture import SyntheticProvenance


class InvocationTests(unittest.TestCase):
    def fixture(self):
        f = StableFixture(self); f.approval(); return f

    def refused(self, f):
        with self.assertRaises((Blocked, ValueError, OSError)): f.launch()
        self.assertFalse((Path(f.operator['storage'])/'usable.sqlite').exists())

    def codex_reviewer(self, e):
        e.docs['review']['provider'] = 'codex'
        e.docs['review_invocation']['binding']['provider'] = 'codex'
        e.docs['owner']['native_bindings']['reviewer']['provider'] = 'codex'

    def test_owner_bound_codex_reviewer_is_accepted(self):
        f = StableFixture(self); e = SyntheticProvenance(f, f.workspace, {})
        self.codex_reviewer(e); e.seal()
        release = SimpleNamespace(root=f.workspace, data=e.data, record=f.record,
                                  sha256=digest(f.record.read_bytes()))
        ReleaseProvenance(release).verify(manifest(f.workspace))

    def test_codex_graph_rejects_inconsistent_or_unsafe_evidence(self):
        cases = ('review-only', 'owner-only', 'observation-only', 'unknown-provider',
                 'reviewer-role', 'implementer-role', 'implementer-provider', 'same-session',
                 'model', 'effort', 'source', 'contract', 'run', 'round', 'process',
                 'result', 'raw-provider', 'raw-role', 'raw-identity', 'raw-report',
                 'failed-contract', 'blocked-contract', 'open-finding', 'blocked-finding',
                 'blocked-verdict', 'failed-verification')
        for case in cases:
            with self.subTest(case=case):
                self.doCleanups()
                f = StableFixture(self); e = SyntheticProvenance(f, f.workspace, {})
                self.codex_reviewer(e)
                review = e.docs['review']; observed = e.docs['review_invocation']
                owner = e.docs['owner']; native = e.docs['native']
                if case == 'review-only': review['provider'] = 'claude'
                elif case == 'owner-only': owner['native_bindings']['reviewer']['provider'] = 'claude'
                elif case == 'observation-only': observed['binding']['provider'] = 'claude'
                elif case == 'unknown-provider':
                    review['provider'] = observed['binding']['provider'] = owner['native_bindings']['reviewer']['provider'] = 'other'
                elif case in ('reviewer-role', 'implementer-role', 'implementer-provider'):
                    role = 'reviewer' if case == 'reviewer-role' else 'implementer'
                    receipt = observed if role == 'reviewer' else e.docs['implementation_invocation']
                    key, value = ('provider', 'claude') if case == 'implementer-provider' else ('role', 'implementer' if role == 'reviewer' else 'reviewer')
                    receipt['binding'][key] = owner['native_bindings'][role][key] = value
                elif case == 'same-session':
                    sid = e.docs['implementation']['session_id']
                    native['session_id'] = owner['reviewer_session_id'] = sid
                    observed['binding']['session_id'] = owner['native_bindings']['reviewer']['session_id'] = sid
                elif case in ('model', 'effort'):
                    key = 'requested_' + case
                    observed['binding'][key] = owner['native_bindings']['reviewer'][key] = 'changed'
                elif case in ('source', 'contract', 'run', 'round'):
                    key, value = {'source': ('candidate_sha256', '0'*64), 'contract': ('acceptance_sha256', '0'*64),
                                  'run': ('run_id', 'other'), 'round': ('round', 2)}[case]
                    observed['identity'][key] = value
                elif case == 'process': observed['process']['exit'] = 1
                elif case == 'result': observed['result'] = e.ref('implementation')
                elif case == 'raw-provider': native['provider'] = 'claude'
                elif case == 'raw-role': native['binding'] = {'role': 'implementer'}
                elif case == 'raw-identity': native['identity'] = dict(e.identity, run_id='other')
                elif case == 'raw-report': native['raw_report_sha256'] = '0'*64
                elif case in ('failed-contract', 'blocked-contract'):
                    e.docs['report']['contracts'][0]['status'] = 'FAIL' if case == 'failed-contract' else 'BLOCKED'
                    e.sync_report()
                elif case in ('open-finding', 'blocked-finding'):
                    e.docs['report']['findings'] = [dict(id='required', severity='P1',
                        status='OPEN' if case == 'open-finding' else 'BLOCKED', title='required',
                        contract_ids=['fixture-contract'], evidence_refs=['fixture-check'])]
                    e.sync_report()
                elif case == 'blocked-verdict': e.docs['report']['verdict'] = 'BLOCKED'; e.sync_report()
                elif case == 'failed-verification': e.docs['gate']['verification'][0]['passed'] = False
                e.seal()
                release = SimpleNamespace(root=f.workspace, data=e.data, record=f.record,
                                          sha256=digest(f.record.read_bytes()))
                with self.assertRaises(Blocked): ReleaseProvenance(release).verify(manifest(f.workspace))

    def test_exact_reviewer_counterexamples_are_refused_after_honest_repin(self):
        f = self.fixture(); e = f.evidence
        self.assertEqual(e.docs['review']['provider'],'claude')
        e.docs['native']['provider'] = 'codex'; e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['native']['requested_model'] = 'gpt-6.1-sol'
        e.docs['implementation'].update(provider='claude',requested_model='claude-opus-5-5[1m]')
        e.seal(); self.refused(f)

    def test_original_old_adapter_results_need_no_new_provider_or_role_fields(self):
        f = self.fixture(); e = f.evidence
        original = {name:e.paths[name].read_bytes() for name in ('native','implementation')}
        for role in original:
            self.assertNotIn('provider', e.docs[role]); self.assertNotIn('role', e.docs[role])
        rt = f.launch(); self.assertEqual(rt.release_identity()['channel'],'Stable')
        for role, raw in original.items(): self.assertEqual(e.paths[role].read_bytes(),raw)

    def test_approved_proxy_aliases_are_opaque_even_across_familiar_model_spellings(self):
        for alias in ('gpt-6.1-sol','private-proxy/any-name'):
            with self.subTest(alias=alias):
                f = self.fixture(); e = f.evidence
                e.docs['native']['requested_model'] = alias
                e.docs['review_invocation']['binding']['requested_model'] = alias
                e.docs['owner']['native_bindings']['reviewer']['requested_model'] = alias
                e.docs['implementation']['requested_model'] = 'claude-named-proxy-alias'
                e.docs['implementation_invocation']['binding']['requested_model'] = 'claude-named-proxy-alias'
                e.docs['owner']['native_bindings']['implementer']['requested_model'] = 'claude-named-proxy-alias'
                e.seal(); self.assertEqual(f.launch().release_identity()['channel'],'Stable')

    def test_missing_observed_or_owner_binding_is_refused(self):
        for doc, key in (('owner','invocations'), ('owner','native_bindings'), ('review','invocation'),
                ('gate','implementation_invocation'), ('review_invocation','binding')):
            with self.subTest(doc=doc,key=key):
                f = self.fixture(); e = f.evidence; del e.docs[doc][key]; e.seal(); self.refused(f)

    def test_every_observed_request_field_must_equal_owner_authorization(self):
        for receipt in ('review_invocation','implementation_invocation'):
            for field, value in (('provider','other-provider'), ('role','other-role'), ('requested_model','other-model'),
                    ('requested_effort','other-effort'), ('session_id','bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee'),
                    ('executable',dict(path='/usr/bin/false',sha256=digest(Path('/usr/bin/false').read_bytes())))):
                with self.subTest(receipt=receipt,field=field):
                    f = self.fixture(); e = f.evidence; e.docs[receipt]['binding'][field] = value
                    e.seal(); self.refused(f)

    def test_owner_model_or_effort_cannot_override_actual_native_request(self):
        for field in ('requested_model','requested_effort'):
            for role, receipt in (('reviewer','review_invocation'),('implementer','implementation_invocation')):
                with self.subTest(field=field,role=role):
                    f = self.fixture(); e = f.evidence
                    e.docs['owner']['native_bindings'][role][field] = 'changed'
                    e.docs[receipt]['binding'][field] = 'changed'
                    e.seal(); self.refused(f)

    def test_original_result_claims_cannot_contradict_observation(self):
        for native in ('native','implementation'):
            for key, value in (('provider','codex' if native=='native' else 'claude'), ('role','other-role'),
                    ('model','other-model'), ('effort','other-effort'), ('executable','/usr/bin/false'),
                    ('executable_sha256','f'*64)):
                with self.subTest(native=native,key=key):
                    f = self.fixture(); e = f.evidence; e.docs[native][key] = value
                    e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence; e.docs['native']['binding'] = dict(provider='codex')
        e.seal(); self.refused(f)

    def test_repinning_both_owner_and_observation_cannot_swap_required_roles(self):
        for role, receipt, provider, wrong_role in (('reviewer','review_invocation','codex','implementer'),
                ('implementer','implementation_invocation','claude','reviewer')):
            for key, value in (('provider',provider),('role',wrong_role)):
                with self.subTest(role=role,key=key):
                    f = self.fixture(); e = f.evidence
                    e.docs[receipt]['binding'][key] = e.docs['owner']['native_bindings'][role][key] = value
                    e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['review']['provider'] = 'codex'; e.seal(); self.refused(f)

    def test_source_result_process_and_supervisor_must_match_exactly(self):
        for receipt in ('review_invocation','implementation_invocation'):
            for field, value in (('candidate_sha256','0'*64),('acceptance_sha256','0'*64),('run_id','other'),('round',2)):
                with self.subTest(receipt=receipt,field=field):
                    f = self.fixture(); e = f.evidence; e.docs[receipt]['identity'][field] = value
                    e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['review_invocation']['result'] = e.ref('implementation'); e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['implementation_invocation']['process']['exit'] = 1; e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['review_invocation']['supervisor'] = e.ref('user_grant'); e.seal(); self.refused(f)
        f = self.fixture(); e = f.evidence
        e.docs['native']['identity'] = dict(e.identity, candidate_sha256='f'*64)
        e.seal(); self.refused(f)

    def test_same_observation_cannot_be_reused_for_both_roles(self):
        f = self.fixture(); e = f.evidence
        e.docs['gate']['implementation_invocation'] = e.ref('review_invocation')
        e.docs['owner']['invocations']['implementer'] = e.ref('review_invocation')
        e.seal(); self.refused(f)

    def test_actual_session_remains_bound_even_if_owner_and_attestation_agree(self):
        f = self.fixture(); e = f.evidence; sid = 'bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        e.docs['owner']['native_bindings']['reviewer']['session_id'] = sid
        e.docs['review_invocation']['binding']['session_id'] = sid
        e.seal(); self.refused(f)

    def test_distinct_sessions_required_even_when_entire_binding_graph_agrees(self):
        f = self.fixture(); e = f.evidence; sid = e.docs['implementation']['session_id']
        e.docs['native']['session_id'] = e.docs['owner']['reviewer_session_id'] = sid
        e.docs['owner']['native_bindings']['reviewer']['session_id'] = sid
        e.docs['review_invocation']['binding']['session_id'] = sid
        e.seal(); self.refused(f)

    def test_new_observations_reject_links_overlap_and_same_byte_replacement(self):
        for receipt in ('review_invocation','implementation_invocation'):
            for kind in ('symlink','hardlink','ancestor-link','overlap','replacement'):
                with self.subTest(receipt=receipt,kind=kind):
                    f = self.fixture(); e = f.evidence; p = e.paths[receipt]
                    if kind == 'replacement':
                        rt = f.launch(); replacement = p.with_suffix('.replacement')
                        replacement.write_bytes(p.read_bytes()); replacement.replace(p)
                        with self.assertRaises(Blocked): rt.check_operator()
                        continue
                    if kind in ('symlink','hardlink'):
                        original = p.with_suffix('.original'); p.rename(original)
                        if kind == 'symlink': p.symlink_to(original)
                        else: os.link(original,p)
                    else:
                        if kind == 'ancestor-link':
                            parent = f.base/'SYNTHETIC-alias'; parent.symlink_to(f.base,target_is_directory=True)
                            target = parent/p.name
                        else:
                            target = f.workspace/p.name; p.rename(target)
                        # Update every synthetic reference and honestly re-pin it.
                        def move(value):
                            if isinstance(value,dict):
                                if value.get('path') == str(p): value['path'] = str(target)
                                for child in value.values(): move(child)
                            elif isinstance(value,list):
                                for child in value: move(child)
                        for doc in e.docs.values(): move(doc)
                        e.paths[receipt] = target
                        e.seal()
                    self.refused(f)

    def test_observation_paths_are_private_protected_and_rechecked(self):
        f = self.fixture(); rt = f.launch(); e = f.evidence; url, token = serve(self,rt)
        for receipt in ('review_invocation','implementation_invocation'):
            self.assertIn(e.paths[receipt],rt.protected)
        code = e.docs['owner']['native_bindings']['reviewer']['executable']['path']
        self.assertNotIn(Path(code),rt.protected,'Shared readonly code is not private authority')
        status_code, status = boundary.StableEntryTests().request(url,token,'/api/status')
        self.assertEqual(status_code,200)
        for receipt in ('review_invocation','implementation_invocation'):
            self.assertNotIn(str(e.paths[receipt]),json.dumps(status))
        p = e.paths['review_invocation']; p.write_bytes(p.read_bytes()+b' ')
        with patch('studio.workbench_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(Blocked): rt.spawn('fixture')
            with self.assertRaises(Blocked): rt.session_center._spawn('fixture')
            spawn.assert_not_called()
        self.assertEqual(boundary.StableEntryTests().request(url,token,'/api/status')[0],400)

    def test_executable_reference_is_readonly_code_not_unchecked_authority(self):
        for kind in ('bad-sha','non-executable','writable-overlap'):
            with self.subTest(kind=kind):
                f = self.fixture(); e = f.evidence
                path = f.workspace/'SYNTHETIC-executable'; path.write_bytes(b'SYNTHETIC executable fixture\n')
                path.chmod(0o600 if kind=='non-executable' else 0o700)
                ref = dict(path=str(path),sha256='f'*64 if kind=='bad-sha' else digest(path.read_bytes()))
                e.docs['owner']['native_bindings']['reviewer']['executable'] = copy.deepcopy(ref)
                e.docs['review_invocation']['binding']['executable'] = ref
                e.seal(); self.refused(f)
        for kind in ('mutation','replacement'):
            with self.subTest(poststart=kind):
                f = self.fixture(); e = f.evidence; path = f.base/'SYNTHETIC-readonly-cli'
                path.write_bytes(b'SYNTHETIC executable fixture\n'); path.chmod(0o700)
                ref = dict(path=str(path),sha256=digest(path.read_bytes()))
                e.docs['owner']['native_bindings']['reviewer']['executable'] = copy.deepcopy(ref)
                e.docs['review_invocation']['binding']['executable'] = ref
                e.seal(); rt = f.launch()
                if kind == 'mutation': path.write_bytes(b'changed SYNTHETIC code\n')
                else:
                    replacement = path.with_suffix('.replacement'); replacement.write_bytes(path.read_bytes())
                    replacement.chmod(0o700); replacement.replace(path)
                with self.assertRaises(Blocked): rt.check_operator()


if __name__ == '__main__': unittest.main()
