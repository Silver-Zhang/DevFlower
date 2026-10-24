"""Explicit Development session host. No Controller, gate, delivery or Stable identity.

An operator pins the candidate bytes and a private configuration for isolated
native API testing. This is never interpreted as a release approval.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from orchestration.common import Blocked, digest, identity, tree_hash
from orchestration.controller import overlaps
from .graph import Invalid
from .session_center import SessionCenter
from .session_sources import clean_path, opened, Refused
from .usable_store import Storage

ROOT = Path(__file__).resolve().parents[2]


class SessionRuntime:
    session_only = True
    operator_metadata_fields = frozenset()

    def operator_protected_paths(self):
        """Subclass metadata must be validated before any runtime storage is made."""
        return []

    def __init__(self, operator_file, expected_sha256, candidate_sha256):
        self.operator_file = Path(clean_path(str(operator_file)))
        self.operator_sha256, self.candidate_sha256 = expected_sha256, candidate_sha256
        with opened(self.operator_file) as fd:
            if os.fstat(fd).st_size > 131072:
                raise Blocked('Development session configuration exceeds budget')
            raw = os.read(fd, 131073)
        if digest(raw) != expected_sha256 or tree_hash(ROOT) != candidate_sha256:
            raise Blocked('Operator configuration or Development candidate identity differs')
        self.operator = json.loads(raw)
        required = {'version', 'kind', 'storage', 'native', 'allowed_roots', 'profiles', 'dependencies'}
        if set(self.operator) - required - {'dependency_trees', 'session_profiles', 'session_read_roots'} - self.operator_metadata_fields or not required <= set(self.operator):
            raise Blocked('Unexpected Development session configuration fields')
        if self.operator['version'] != 1 or self.operator['kind'] != 'development-sessions':
            raise Blocked('This entry point accepts only Development session configurations')
        if not isinstance(self.operator['profiles'], dict) or not 1 <= len(self.operator['profiles']) <= 16:
            raise Blocked('Register 1–16 explicit native execution profiles')
        if not isinstance(self.operator['allowed_roots'], list) or not 1 <= len(self.operator['allowed_roots']) <= 8:
            raise Blocked('Register 1–8 explicit local execution roots')
        for path in self.operator['allowed_roots']:
            clean_path(path)
        self.storage = Path(clean_path(self.operator['storage']))
        self.native = Path(clean_path(self.operator['native']))
        self.authority = self.operator_file  # Protected input, never a writable authority directory.
        self.browser_directory = self.storage
        self.lock, self.children = threading.RLock(), {}
        protected = [ROOT, self.operator_file, *self.operator_protected_paths()]
        for p in self.operator['profiles'].values():
            fields = {'provider', 'executable', 'model', 'effort', 'read_paths', 'readonly_files'}
            if not fields <= set(p) or set(p) - fields - {'buffered_tool_transport', 'timeout_seconds'}:
                raise Blocked('Unexpected native execution profile fields')
            if p['provider'] not in ('codex', 'claude') or not all(isinstance(p[k], str) and p[k] for k in ('model', 'effort')):
                raise Blocked('Only explicit Codex/Claude model and effort profiles are supported')
            if len(p['readonly_files']) > 2:
                raise Blocked('Only explicit native authentication/settings overlays are supported')
            destinations = set()
            for src, dest in p['readonly_files']:
                if dest not in (('.codex/auth.json',) if p['provider'] == 'codex' else ('.claude/settings.json', '.claude/.credentials.json')) or dest in destinations:
                    raise Blocked('Credential destination differs from Provider')
                destinations.add(dest);protected.append(Path(clean_path(src)))
                if src not in self.operator['dependencies']:
                    raise Blocked('Native authentication/settings must be operator-pinned')
            if p['executable'] not in self.operator['dependencies']:
                raise Blocked('Native executable must be operator-pinned')
            if type(p.get('buffered_tool_transport', False)) is not bool:
                raise Blocked('Buffered transport requires an explicit boolean')
            if 'timeout_seconds' in p and (type(p['timeout_seconds']) is not int or not 30 <= p['timeout_seconds'] <= 3600):
                raise Blocked('Native step budget must be 30–3600 seconds')
        self.protected = protected
        if overlaps(self.storage, self.native) or any(overlaps(p, x) for p in (self.storage, self.native) for x in protected):
            raise Blocked('Development storage/native homes overlap protected inputs')
        for p in self.operator['profiles'].values():
            for path in p['read_paths']:
                clean_path(path)
                if path in ('/', '/home', '/tmp', '/var/tmp', str(Path.home())) or any(overlaps(path, x) for x in [*protected, self.storage, self.native]):
                    raise Blocked('Native dependency exposes protected configuration, credentials or runtime data')
                if Path(path).is_dir():
                    if path not in self.operator.get('dependency_trees', {}):
                        raise Blocked('Native dependency directories require a pinned tree hash')
                elif path not in self.operator['dependencies']:
                    raise Blocked('Native dependency files require a pinned hash')
        for p in (self.storage, self.native):
            marker = p / '.devflower-session-center'
            if p.exists():
                with opened(marker):
                    pass
            else:
                with opened(p.parent, directory=True):
                    pass
                p.mkdir(mode=0o700)
                marker.write_text('Development session storage only; no Stable approval\n');marker.chmod(0o600)
        self.db = Storage(self.storage)
        self.session_center = SessionCenter(self)
        self.check_operator()

    def check_operator(self):
        with opened(self.operator_file) as fd:
            if digest(os.read(fd, 131073)) != self.operator_sha256:
                raise Blocked('Development operator binding changed')
        if tree_hash(ROOT) != self.candidate_sha256:
            raise Blocked('Development candidate changed; restart with the independently pinned candidate hash')
        for path, sha in self.operator['dependencies'].items():
            with opened(clean_path(path)) as fd:
                import hashlib
                content = hashlib.sha256()
                while block := os.read(fd, 1024 * 1024):
                    content.update(block)
            if content.hexdigest() != sha:
                raise Blocked('Approved native executable or configuration changed')
        for path, sha in self.operator.get('dependency_trees', {}).items():
            if tree_hash(clean_path(path)) != sha:
                raise Blocked('Approved native dependency tree changed')

    def readable_directory(self, path):
        try:
            path = clean_path(path)
            if path in ('/', str(Path.home())) or any(Path(path).is_relative_to(p) for p in
                    ('/proc', '/sys', '/dev', '/etc', '/usr', '/bin', '/lib', '/lib64')):
                raise Blocked('Choose a local project directory, not a system or HOME scan root')
            with opened(path, directory=True):
                pass
        except (OSError, Refused):
            raise Invalid('Choose an accessible absolute local folder; remote/WSL URIs and symlinks are unsupported') from None
        if any(overlaps(path, x) for x in [*self.protected, self.storage, self.native]):
            raise Blocked('Workspace overlaps protected Development session data')
        return path

    def validate_source(self, path):
        path = self.readable_directory(path)
        if not any(Path(path).is_relative_to(clean_path(p)) for p in self.operator['allowed_roots']):
            raise Blocked('Folder is outside the operator-approved execution roots')
        return identity(path)

    def project(self, key):
        row = self.db.get('workspace', key)
        self.validate_source(row['path'])
        return row

    def open_workspace(self, path):
        self.check_operator();path = self.readable_directory(path)
        try:
            source = self.validate_source(path)
            row = dict(path=source['root'], head=source['head'], branch=source['branch'],
                       execution_allowed=True, execution_reason=None)
        except Blocked as exc:
            row = dict(path=path, head=None, branch=None, execution_allowed=False, execution_reason=str(exc))
        row.update(id=digest(row['path'].encode())[:32], environment='wsl' if os.environ.get('WSL_DISTRO_NAME') else 'local',
                   repository=None, unsupported_environments=['wsl-uri', 'remote-uri'])
        self.db.put('workspace', row['id'], row)
        return row

    def profiles(self):
        return [dict(id=k, provider=p['provider'], model=p['model'], effort=p['effort'], subagents=False,
                     tools=['native'] if p['provider'] == 'codex' else ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit'])
                for k, p in self.operator['profiles'].items()]

    def owned_sessions(self, workspace, validate=False):
        return []  # No access to Controller authority or its session ownership records.
