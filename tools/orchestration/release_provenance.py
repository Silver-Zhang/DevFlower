"""Read-only v2 release evidence verification; never creates an approval or Gate.

The externally pinned operator/owner files are the trust anchor. All referenced
bytes must remain outside candidate and Agent-writable mounts. See WORKBENCH.md.
"""
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .common import Blocked, canonical, digest
from .contracts import require_keys
from .gate import gate as check_gate
from .trust import overlap
from studio.session_sources import opened


def object_id(kind, raw):
    return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def git_tree(root, entries):
    """Git SHA-1 tree of ALL frozen bytes/modes, including untracked files; no .git."""
    tree = {}
    for entry in entries:
        path = Path(entry['path'])
        if path.is_absolute() or '..' in path.parts or entry['mode'] not in ('100644', '100755'):
            raise Blocked('Invalid frozen Git tree entry')
        with opened(Path(root)/path) as fd:
            raw = bytearray()
            while block := os.read(fd, 1024*1024): raw.extend(block)
            mode = '100755' if os.fstat(fd).st_mode & 0o111 else '100644'
        if digest(raw) != entry['sha256'] or len(raw) != entry['size'] or mode != entry['mode']:
            raise Blocked('Frozen Git bytes changed')
        branch = tree
        for part in path.parts[:-1]: branch = branch.setdefault(os.fsencode(part), {})
        branch[os.fsencode(path.name)] = (entry['mode'], object_id('blob', raw))

    def encode(branch):
        raw = b''
        for name, value in sorted(branch.items(), key=lambda item: item[0]+(b'/' if isinstance(item[1], dict) else b'')):
            mode, oid = ('40000', encode(value)) if isinstance(value, dict) else value
            raw += mode.encode()+b' '+name+b'\0'+bytes.fromhex(oid)
        return object_id('tree', raw)
    return encode(tree)


def versioned(value, kind, fields):
    require_keys(value, ['version', 'kind', *fields])
    if type(value['version']) is not int or value['version'] != 1 or value['kind'] != kind:
        raise Blocked('Unsupported Stable evidence version/kind')


def successful(process):
    if (not isinstance(process, dict) or type(process.get('exit')) is not int or process['exit'] != 0 or
            any(type(process.get(k)) is not bool or process[k] for k in ('timeout', 'cancelled', 'overflow'))):
        raise Blocked('Unsuccessful Stable evidence process')


def native_result(value):
    fields = {'report', 'raw_report_sha256', 'process', 'session_id', 'requested_model',
              'requested_effort', 'resolved_model', 'resolved_effort'}
    if not isinstance(value, dict) or not fields <= value.keys():
        raise Blocked('Missing native reviewer/implementation provenance')
    successful(value['process'])
    try:
        if str(uuid.UUID(value['session_id'])) != value['session_id']:
            raise ValueError('noncanonical session identity')
    except (ValueError, TypeError, AttributeError): raise Blocked('Invalid native session identity') from None
    for key in ('requested_model', 'requested_effort', 'resolved_model', 'resolved_effort'):
        if not isinstance(value[key], str) or not value[key].strip() or key.startswith('requested_') and value[key] == 'unknown':
            raise Blocked('Missing native model/effort provenance')
    if value['raw_report_sha256'] != digest(canonical(value['report'])):
        raise Blocked('Native result does not bind the raw report')


class ReleaseProvenance:
    def __init__(self, release):
        self.release = release
        self.pins, self.inodes = {}, {}
        self.code_paths = set()

    @property
    def paths(self):
        return tuple(p for p in self.pins if p not in self.code_paths)

    def read(self, ref, as_json=True, *, code=False):
        require_keys(ref, ('path', 'sha256'))
        path, sha = ref['path'], ref['sha256']
        if (not isinstance(path, str) or not Path(path).is_absolute() or
                str(Path(path)) != path or '..' in Path(path).parts or
                not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)):
            raise Blocked('Invalid pinned Stable evidence reference')
        if overlap(self.release.root, path):
            raise Blocked('Stable evidence must be external to the candidate')
        if path in self.pins and (path in self.code_paths) != code:
            raise Blocked('Authority evidence cannot double as executable code')
        try:
            with opened(path) as fd:
                before = os.fstat(fd)
                if code:
                    if not before.st_mode & 0o111: raise Blocked('Native invocation requires an executable file')
                    content = hashlib.sha256()
                    while block := os.read(fd, 1024*1024): content.update(block)
                    actual_sha, raw = content.hexdigest(), None
                else:
                    if before.st_size > 2_000_000: raise Blocked('Stable evidence exceeds budget')
                    raw = os.read(fd, 2_000_001); actual_sha = digest(raw)
                after = os.fstat(fd)
            stamp = lambda st: (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode, st.st_nlink)
            current = stamp(after)
            if (stamp(before) != current or actual_sha != sha or
                    path in self.inodes and self.inodes[path] != current or
                    path in self.pins and self.pins[path] != sha):
                raise Blocked('Stable evidence changed or was replaced')
            self.inodes[path], self.pins[path] = current, sha
            if code: self.code_paths.add(path)
            return json.loads(raw) if as_json else raw
        except (OSError, ValueError, RecursionError):
            raise Blocked('Invalid or inaccessible Stable evidence') from None

    def invocation(self, ref, result_ref, native, owner, role, provider):
        """Consume supervisor observation separately from an unchanged old result."""
        observed = self.read(ref)
        versioned(observed, 'release-invocation', ('identity', 'supervisor', 'result', 'binding', 'process'))
        binding = observed['binding']
        require_keys(binding, ('provider', 'role', 'executable', 'requested_model', 'requested_effort', 'session_id'))
        if (observed['identity'] != owner['identity'] or observed['supervisor'] != owner['supervisor'] or
                observed['result'] != result_ref or binding != owner['native_bindings'][role] or
                binding['provider'] != provider or binding['role'] != role or
                observed['process'] != native['process']):
            raise Blocked('Native invocation differs from observed/owner-authorized role and result')
        successful(observed['process'])
        for key in ('requested_model', 'requested_effort', 'session_id'):
            if binding[key] != native[key]: raise Blocked('Native request differs from approved invocation')
        self.read(binding['executable'], as_json=False, code=True)
        # Old Stable results omit provider. New attestations never add it to the
        # original; any identity claims already present must agree, however.
        expected = dict(owner['identity'], **binding,
                        model=binding['requested_model'], effort=binding['requested_effort'],
                        requested_provider=provider, requested_role=role,
                        executable_sha256=binding['executable']['sha256'])
        for claims in (native, native.get('binding', {}), native.get('identity', {})):
            if not isinstance(claims, dict): raise Blocked('Invalid native identity claims')
            for key in claims.keys() & expected.keys():
                value = claims[key]
                target = expected[key]
                if key == 'executable' and isinstance(value, str): target = target['path']
                if value != target: raise Blocked('Original native result contradicts invocation identity')

    def verify(self, entries):
        release = self.release; data = release.data; refs = data['provenance']
        require_keys(refs, ('contract', 'review', 'gate', 'owner', 'commit'))
        self.read(dict(path=str(release.record), sha256=release.sha256), as_json=False)
        contract = self.read(refs['contract'], as_json=False)
        try:
            if not contract.decode('utf-8').strip(): raise Blocked('Empty acceptance contract')
        except UnicodeDecodeError: raise Blocked('Acceptance contract must be UTF-8') from None
        owner = self.read(refs['owner']); review = self.read(refs['review']); gate = self.read(refs['gate'])
        versioned(owner, 'release-owner', ('identity', 'root', 'git_commit', 'git_tree', 'scope',
            'required_contracts', 'verification_ids', 'reviewer_session_id', 'implementation_session_id',
            'contract', 'review', 'gate', 'commit', 'supervisor', 'user_grant', 'native_bindings', 'invocations'))
        versioned(review, 'release-review', ('identity', 'provider', 'report', 'native_result', 'invocation'))
        versioned(gate, 'release-gate', ('identity', 'verdict', 'process', 'review', 'report_sha256',
            'supervisor', 'implementation', 'verification', 'implementation_invocation'))
        require_keys(owner['native_bindings'], ('reviewer', 'implementer'))
        require_keys(owner['invocations'], ('reviewer', 'implementer'))
        if (review['invocation'] != owner['invocations']['reviewer'] or
                gate['implementation_invocation'] != owner['invocations']['implementer'] or
                review['provider'] not in ('codex', 'claude')):
            raise Blocked('Release requires owner-bound Codex implementation and supported independent review')
        identity = owner['identity']
        require_keys(identity, ('run_id', 'round', 'candidate_sha256', 'acceptance_sha256'))
        if (not isinstance(identity['run_id'], str) or not identity['run_id'].strip() or
                type(identity['round']) is not int or identity['round'] < 1 or
                identity['candidate_sha256'] != data['candidate_sha256'] or
                identity['acceptance_sha256'] != refs['contract']['sha256'] or
                review['identity'] != identity or gate['identity'] != identity):
            raise Blocked('Cross-candidate/contract/run/round Stable evidence')
        for key in ('contract', 'review', 'gate', 'commit'):
            if owner[key] != refs[key]: raise Blocked('Owner authorization does not bind release evidence')
        if (owner['root'] != str(release.root) or owner['git_commit'] != data['git_commit'] or
                owner['git_tree'] != data['git_tree'] or owner['scope'] != ['install', 'start'] or
                gate['review'] != refs['review'] or gate['supervisor'] != owner['supervisor'] or
                gate['verdict'] != 'ACCEPT' or gate['report_sha256'] != review['report']['sha256']):
            raise Blocked('Owner/Gate does not authorize this exact Stable install/start')
        for key in ('required_contracts', 'verification_ids'):
            value = owner[key]
            if (not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value) or
                    len(set(value)) != len(value)):
                raise Blocked('Owner must pin nonempty unique contract/verification obligations')
        try:
            if not self.read(owner['user_grant'], as_json=False).decode('utf-8').strip():
                raise Blocked('Missing original external user grant')
        except UnicodeDecodeError: raise Blocked('User grant must be original UTF-8 evidence') from None
        if not self.read(gate['supervisor'], as_json=False).strip():
            raise Blocked('Missing fixed external supervisor')
        successful(gate['process'])
        report = self.read(review['report']); native = self.read(review['native_result'])
        implementation = self.read(gate['implementation'])
        native_result(native); native_result(implementation)
        self.invocation(review['invocation'], review['native_result'], native, owner, 'reviewer', review['provider'])
        self.invocation(gate['implementation_invocation'], gate['implementation'], implementation, owner, 'implementer', 'codex')
        if not isinstance(gate['verification'], list): raise Blocked('Invalid Gate verification evidence')
        for verification in gate['verification']:
            if not isinstance(verification, dict) or type(verification.get('passed')) is not bool:
                raise Blocked('Invalid Gate verification evidence')
            successful(verification.get('process'))
        if (native['report'] != report or owner['reviewer_session_id'] != native['session_id'] or
                owner['implementation_session_id'] != implementation['session_id']):
            raise Blocked('Native receipt/report/session mismatch')
        # Recheck existing mechanical invariants; this consumes an EXTERNAL
        # Gate receipt and does not issue acceptance or replace the old Gate.
        check_gate(identity['run_id'], identity['round'], identity['candidate_sha256'],
            identity['acceptance_sha256'], owner['required_contracts'], gate['verification'],
            implementation, native, expected_verification=owner['verification_ids'])
        commit = self.read(refs['commit'], as_json=False)
        if (not all(isinstance(data[k], str) and re.fullmatch('[0-9a-f]{40}', data[k]) for k in ('git_commit', 'git_tree')) or
                object_id('commit', commit) != data['git_commit'] or git_tree(release.root, entries) != data['git_tree']):
            raise Blocked('Stable Git commit/tree object proof differs from frozen bytes')
        headers = commit.split(b'\n\n', 1)[0].split(b'\n')
        if (b'\n\n' not in commit or b'\0' in commit or not headers or headers[0] != b'tree '+data['git_tree'].encode() or
                sum(line.startswith(b'tree ') for line in headers) != 1 or
                sum(line.startswith(b'author ') for line in headers) != 1 or
                sum(line.startswith(b'committer ') for line in headers) != 1):
            raise Blocked('Malformed Git commit object proof')
        for line in headers:
            if line.startswith((b'author ', b'committer ')) and not re.fullmatch(
                    rb'(author|committer) [^<>\n]+ <[^<>\n]+> -?[0-9]+ [+-][0-9]{4}', line):
                raise Blocked('Malformed Git author/committer proof')
            if line.startswith(b'parent ') and not re.fullmatch(rb'parent [0-9a-f]{40}', line):
                raise Blocked('Malformed Git parent object identity')
