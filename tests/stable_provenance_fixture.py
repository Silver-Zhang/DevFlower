"""SYNTHETIC external evidence factory, NEVER actual owner/reviewer acceptance.

The product has no corresponding writer. Real evidence is emitted only by the
fixed external supervisor after actual review/Gate/owner authorization.
"""
import copy
from pathlib import Path
from orchestration.common import canonical, digest, manifest, save
from orchestration.release_provenance import git_tree, object_id


class SyntheticProvenance:
    def __init__(self, fixture, root, changes):
        self.f = fixture
        self.paths = {key:fixture.base/('SYNTHETIC-'+key+'.json') for key in
            ('contract', 'report', 'native', 'implementation', 'review', 'gate', 'owner', 'commit', 'supervisor', 'user_grant',
             'review_invocation', 'implementation_invocation')}
        self.paths['contract'].write_bytes(b'SYNTHETIC acceptance contract, not actual release acceptance\n')
        self.paths['supervisor'].write_bytes(b'SYNTHETIC fixed external supervisor fixture, not a real Gate\n')
        self.paths['user_grant'].write_bytes(b'SYNTHETIC user grant fixture, never actual owner authorization\n')
        entries = manifest(root); tree = git_tree(root, entries)
        commit = (f'tree {tree}\nauthor Synthetic <fixture@example.invalid> 0 +0000\n'
                  'committer Synthetic <fixture@example.invalid> 0 +0000\n\nSYNTHETIC, not a release commit\n').encode()
        self.paths['commit'].write_bytes(commit)
        self.identity = dict(run_id='SYNTHETIC-run', round=1,
            candidate_sha256=digest(canonical(entries)), acceptance_sha256=self.ref('contract')['sha256'])
        proc = dict(exit=0, timeout=False, cancelled=False, overflow=False)
        self.docs = {}
        self.docs['report'] = dict(schema_version=1, **self.identity, verdict='ACCEPT',
            contracts=[dict(id='fixture-contract', status='PASS', evidence_refs=['fixture-check'])],
            findings=[], limitations=['SYNTHETIC fixture only; no real review/owner acceptance'])
        for role, sid, report in [('native', '11111111-2222-4333-8444-555555555555', self.docs['report']),
                ('implementation', 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', {'status':'IMPLEMENTED'})]:
            self.docs[role] = dict(report=copy.deepcopy(report), raw_report_sha256=digest(canonical(report)),
                session_id=sid, requested_model='SYNTHETIC-model', requested_effort='high',
                resolved_model='unknown', resolved_effort='unknown', process=copy.deepcopy(proc))
        executable = fixture.operator['profiles']['fixture']['executable']
        executable_ref = dict(path=executable, sha256=digest(Path(executable).read_bytes()))
        bindings = {}
        for role, provider, result, receipt in [('reviewer','claude','native','review_invocation'),
                ('implementer','codex','implementation','implementation_invocation')]:
            native = self.docs[result]
            bindings[role] = dict(provider=provider, role=role, executable=copy.deepcopy(executable_ref),
                **{key:native[key] for key in ('requested_model','requested_effort','session_id')})
            self.docs[receipt] = dict(version=1, kind='release-invocation', identity=copy.deepcopy(self.identity),
                supervisor=self.ref('supervisor'), result=self.ref(result, pending=True),
                binding=copy.deepcopy(bindings[role]), process=copy.deepcopy(native['process']))
        self.docs['review'] = dict(version=1, kind='release-review', identity=copy.deepcopy(self.identity), provider='claude',
            report=self.ref('report', pending=True), native_result=self.ref('native', pending=True),
            invocation=self.ref('review_invocation', pending=True))
        self.docs['gate'] = dict(version=1, kind='release-gate', identity=copy.deepcopy(self.identity), verdict='ACCEPT',
            process=copy.deepcopy(proc), review=self.ref('review', pending=True),
            report_sha256=digest(canonical(self.docs['report'])+b'\n'),
            supervisor=self.ref('supervisor'), implementation=self.ref('implementation', pending=True),
            implementation_invocation=self.ref('implementation_invocation', pending=True),
            verification=[dict(id='fixture-check', passed=True, **self.identity, counts=dict(tests=1, failures=0, errors=0, skipped=0),
                process=copy.deepcopy(proc))])
        self.data = dict(version=2, root=str(root), git_tree=tree, git_commit=object_id('commit', commit),
            candidate_sha256=self.identity['candidate_sha256'], files=entries, provenance={})
        self.docs['owner'] = dict(version=1, kind='release-owner', identity=copy.deepcopy(self.identity),
            root=str(root), git_tree=tree, git_commit=self.data['git_commit'], scope=['install', 'start'],
            required_contracts=['fixture-contract'], verification_ids=['fixture-check'],
            reviewer_session_id=self.docs['native']['session_id'], implementation_session_id=self.docs['implementation']['session_id'],
            native_bindings=copy.deepcopy(bindings), invocations=dict(reviewer=self.ref('review_invocation', pending=True),
                implementer=self.ref('implementation_invocation', pending=True)),
            **{key:self.ref(key, pending=True) for key in ('contract', 'review', 'gate', 'commit', 'supervisor', 'user_grant')})
        self.data.update(changes)
        self.seal()

    def ref(self, role, pending=False):
        path = self.paths[role]
        return dict(path=str(path), sha256=digest(path.read_bytes()) if path.exists() else '0'*64)

    def sync_report(self):
        self.docs['native']['report'] = copy.deepcopy(self.docs['report'])
        self.docs['native']['raw_report_sha256'] = digest(canonical(self.docs['report']))
        self.docs['gate']['report_sha256'] = digest(canonical(self.docs['report'])+b'\n')

    def seal(self):
        # Re-pin a synthetic graph after an adversarial edit, preserving wrong
        # identities/decisions. This exercises semantic checks, not just SHA.
        def refs(value):
            if isinstance(value, dict):
                if set(value) == {'path', 'sha256'} and value['path'] in map(str, self.paths.values()):
                    p = Path(value['path'])
                    if p.exists(): value['sha256'] = digest(p.read_bytes())
                else:
                    for child in value.values(): refs(child)
            elif isinstance(value, list):
                for child in value: refs(child)
        for role in ('report', 'native', 'implementation', 'review_invocation', 'implementation_invocation', 'review', 'gate', 'owner'):
            refs(self.docs[role])
            save(self.paths[role], self.docs[role])
        self.data['provenance'] = {key:self.ref(key) for key in ('contract', 'review', 'gate', 'owner', 'commit')}
        save(self.f.record, self.data)
        self.f.operator['stable'] = dict(record=str(self.f.record), sha256=digest(self.f.record.read_bytes()))
