"""Workspace identities and minimal session index. Operator configuration only."""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sqlite3
import threading
import time
from .graph import Invalid
from .session_sources import clean_path, opened, Refused, scan, LIMITS, UUID

PROVIDERS = {'codex': 'sessions', 'claude': 'projects', 'copilot': 'session-state'}


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()[:32]


def inside(child, parent):
    return Path(child).is_relative_to(parent)


def display(path):
    # Never echo credential-looking components from paths, including operator input.
    return '/'.join('[redacted]' if re.search(r'(?i)(secret|token|password|api.?key)', p) else p for p in str(path).split('/'))


def configuration(path):
    if not path:
        return {}
    try:
        with opened(path) as fd:
            if os.fstat(fd).st_size > 131072:
                raise Invalid('Session registration is too large')
            with os.fdopen(os.dup(fd), 'rb') as f:
                obj = json.load(f)
        if not isinstance(obj, dict) or set(obj)-{'profiles', 'limits', 'defaults'}:
            raise ValueError()
        return obj
    except (OSError, ValueError, RecursionError):
        raise Invalid('Invalid session registration; use an explicit regular JSON file') from None


class WorkspaceSessions:
    def __init__(self, storage, project_roots=(), registration=None, environ=None, persist=True, profile_config=None):
        env = os.environ if environ is None else environ
        self.lock = threading.Lock()
        self.machine = identity(socket.gethostname())
        self.roots = {}
        self.profiles = []
        self.storage = Path(storage)
        self.path = self.storage/'sessions.sqlite'
        cfg = configuration(registration) if profile_config is None else profile_config
        self.limits = dict(LIMITS)
        if not isinstance(cfg.get('limits', {}), dict):
            raise Invalid('Scan limits must be an object')
        for k, v in cfg.get('limits', {}).items():
            if k not in LIMITS or type(v) is not int or not 1 <= v <= LIMITS[k]:
                raise Invalid('Scan limits must be positive and cannot exceed M0 ceilings')
            self.limits[k] = v
        for value in project_roots:
            try:
                root = clean_path(str(value))
                with opened(root, directory=True):
                    pass
            except (OSError, Refused):
                raise Invalid('Project root must be an accessible absolute directory without symlinks') from None
            self.roots[identity(self.machine, root)] = root
        profiles = cfg.get('profiles', [])
        if not isinstance(profiles, list) or len(profiles) > 32:
            raise Invalid('Register at most 32 storage profiles')
        if type(cfg.get('defaults', True)) is not bool:
            raise Invalid('defaults must be a boolean')
        if self.roots and cfg.get('defaults', True):
            home = env.get('HOME')
            if home:
                for provider, name in [('codex', 'CODEX_HOME'), ('claude', 'CLAUDE_CONFIG_DIR')]:
                    profiles = [*profiles, dict(provider=provider, home=home,
                        config_root=env.get(name) or str(Path(home)/('.'+provider)), origin='default')]
            for root in self.roots.values():
                # Only these immediate, explicit source roots; never crawl a project or HOME.
                for provider in ('codex', 'claude','copilot'):
                    profiles = [*profiles, dict(provider=provider, config_root=str(Path(root)/('.'+provider)),
                                               workspace=root, original_project_root=root, origin='nested',
                                               home=None)]
        for value in profiles:
            if not isinstance(value, dict) or set(value)-{'provider', 'home', 'config_root', 'workspace', 'original_project_root', 'origin', 'session_ids', 'settings_file'}:
                raise Invalid('Unknown profile fields')
            provider = value.get('provider')
            if provider not in PROVIDERS:
                raise Invalid('Unknown native session Provider')
            try:
                config_root = clean_path(value['config_root'])
                home = clean_path(value['home']) if value.get('home') else None
                target = clean_path(value['workspace']) if value.get('workspace') else None
                original = clean_path(value['original_project_root']) if value.get('original_project_root') else None
            except (KeyError, Refused):
                raise Invalid('Invalid profile paths') from None
            ids = value.get('session_ids')
            if ids is not None and (not isinstance(ids, list) or not ids or len(ids)>200 or any(not isinstance(x,str) or not UUID.fullmatch(x) for x in ids)):
                raise Invalid('session_ids must be a bounded explicit list of native UUIDs')
            settings = value.get('settings_file')
            if settings:
                try:
                    settings = clean_path(settings)
                    with opened(settings):
                        pass
                except (OSError, Refused):
                    raise Invalid('Settings binding must be an accessible regular file') from None
                if provider != 'claude':
                    raise Invalid('Only Claude supports an explicit settings-file binding')
            if target and target not in self.roots.values() or bool(target) != bool(original):
                raise Invalid('An origin mapping requires a registered workspace and original project root')
            if inside(self.storage, config_root) or inside(config_root, self.storage):
                raise Invalid('The Studio index and native storage roots must be disjoint')
            profile = dict(id=identity(provider, config_root, home, target, original, ids, settings), provider=provider,
                           session_ids=ids, settings_file=settings,
                           config_root=config_root, home=home, workspace=target, original=original,
                           origin=value.get('origin') if value.get('origin') in ('default', 'nested') else 'registered')
            if any(x['id'] == profile['id'] for x in self.profiles):
                continue
            self.profiles.append(profile)
        if len(self.profiles) > 64 or len(self.roots) > 8:
            raise Invalid('M0 supports at most 8 workspaces and 64 profiles')
        # Standalone capability declaration; not a pretend native parser.
        self.unsupported = []
        if persist:
            self.initialize_storage()

    def initialize_storage(self):
        for p in [self.path, *(Path(str(self.path)+s) for s in ('-wal', '-shm', '-journal'))]:
            if p.is_symlink() or p.exists() and (not p.is_file() or p.stat().st_nlink != 1):
                raise Invalid('Session index must be a private regular database')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS session_profiles(id TEXT PRIMARY KEY, metadata TEXT);
                CREATE TABLE IF NOT EXISTS session_index(workspace TEXT, identity TEXT, metadata TEXT,
                    PRIMARY KEY(workspace,identity));
                CREATE TABLE IF NOT EXISTS workspace_registry(id TEXT PRIMARY KEY, path TEXT);''')
            db.execute('DELETE FROM session_profiles')
            db.execute('DELETE FROM workspace_registry')
            db.execute('DELETE FROM session_index')
            db.executemany('INSERT INTO workspace_registry VALUES(?,?)', self.roots.items())
            db.executemany('INSERT INTO session_profiles VALUES(?,?)',
                           [(p['id'], json.dumps(p)) for p in self.profiles])
        self.path.chmod(0o600)

    def listing(self):
        return dict(workspaces=[dict(id=i, path=display(p), machine=self.machine) for i, p in self.roots.items()],
                    unsupported=self.unsupported, selection='operator_registered_only')

    def _row(self, native, profile, selected):
        if profile['session_ids'] is not None and native['session_id'] not in profile['session_ids']:
            return None
        cwd, root = native['cwd'], self.roots[selected]
        original = profile['original']
        association = 'cwd'
        if profile['workspace']:
            if profile['workspace'] != root:
                return None
            association = 'registered_origin' if inside(cwd, original) else 'mapping_mismatch'
        else:
            matches = [(len(v), k) for k, v in self.roots.items() if inside(cwd, v)]
            if not matches or max(matches)[1] != selected:
                return None
        verified = False
        if association != 'mapping_mismatch' and not native['cwd_changed']:
            try:
                with opened(cwd, directory=True):
                    verified = True
            except (OSError, Refused):
                pass
        if native['cwd_changed']:
            association = 'cwd_changed'
        disclosed = inside(cwd, root) or bool(original and inside(cwd, original))
        visible_cwd = display(cwd) if disclosed else '[outside current workspace; private path withheld]'
        row = dict(identity=identity(profile['id'], native['session_id'], native['file_key']),
                   provider=profile['provider'], profile_id=profile['id'], session_id=native['session_id'],
                   storage_root=display(profile['config_root']), cwd=visible_cwd, cwd_verified=verified,
                   workspace_id=selected, workspace=display(root), association=association,
                   created=native['created'], updated=native['updated'], cli_version=native['version'],
                   status='unconfirmed', capabilities=dict(discover=True, metadata=True, resume='unverified',
                   view_only=True), resume_command=None,
                   resume_note='Native CLI resume has not been confirmed. Occupancy/auth/version are unknown.')
        # A command is a copy-only suggestion, never a claim of native confirmation.
        # An unknown HOME or changed/mismatched cwd cannot reconstruct the original environment.
        if verified and association in ('cwd', 'registered_origin') and profile['home'] and disclosed and profile['provider']!='copilot':
            provider = profile['provider']
            assignments = ['env', 'HOME='+profile['home'],
                           ('CODEX_HOME=' if provider == 'codex' else 'CLAUDE_CONFIG_DIR=')+profile['config_root']]
            argv = assignments+(['codex', '--cd', cwd, 'resume', native['session_id']] if provider == 'codex'
                                else ['claude', '--resume', native['session_id']])
            if profile['settings_file']:
                argv += ['--settings', profile['settings_file']]
            command = 'cd -- '+shlex.quote(cwd)+' && '+shlex.join(argv)
            # Do not disclose credential-looking paths even through a copy command.
            if display(command) == command:
                row['resume_command'] = command
        return row

    def snapshot(self, workspace_id):
        if workspace_id not in self.roots:
            raise Invalid('Unknown registered workspace')
        with self.lock:
            result = dict(workspace_id=workspace_id, workspace=display(self.roots[workspace_id]),
                          machine=self.machine, scanned_at=time.time(), sessions=[], coverage=[],
                          unsupported=self.unsupported)
            for p in self.profiles:
                if p['workspace'] and p['workspace'] != self.roots[workspace_id]:
                    result['coverage'].append(dict(profile_id=p['id'], provider=p['provider'],
                        storage_root=display(p['config_root']), status='not_scanned', reason='different_workspace'))
                    continue
                native, coverage = scan(str(Path(p['config_root'])/(PROVIDERS[p['provider']] or '')),
                                        p['provider'], self.limits, p['session_ids'])
                result['coverage'].append(dict(profile_id=p['id'], provider=p['provider'],
                    storage_root=display(p['config_root']), origin=p['origin'], native_id_filter=p['session_ids'], **coverage))
                for row in native:
                    public = self._row(row, p, workspace_id)
                    if public:
                        result['sessions'].append(public)
            result['sessions'].sort(key=lambda x: (x['updated'] or '', x['identity']), reverse=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('DELETE FROM session_index WHERE workspace=?', (workspace_id,))
                db.executemany('INSERT INTO session_index VALUES(?,?,?)',
                    [(workspace_id, x['identity'], json.dumps(x, ensure_ascii=False)) for x in result['sessions']])
            return result
