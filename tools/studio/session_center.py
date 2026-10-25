"""Workspace/profile/session workflows. Studio metadata is not Controller authority.

Only exact indexed native identities cross the HTTP boundary. External discovery
grants read access; only dedicated, previously managed homes can be resumed.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from orchestration.adapters import CLI
from orchestration.common import Blocked, canonical, clean_env, digest, identity, tree_hash
from orchestration.contracts import validate
from orchestration.privacy import credentials, redact
from orchestration.process import group_alive, stop
from orchestration.store import alive, lease, process_identity
from .agent_sessions import capabilities, conversation, nested_profiles, safe_text, session_identity
from .graph import Invalid
from .server import Conflict
from .session_sources import clean_path, opened, Refused, scan, UUID
from .workspace_sessions import WorkspaceSessions, display

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}
REPLY_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['reply'],
                'properties': {'reply': {'type': 'string'}}}


def credential_bytes(path):
    """Optional display redaction inputs cannot follow project-controlled links."""
    with opened(path) as fd:
        before = os.fstat(fd)
        if before.st_size > 4 * 1024 * 1024:
            raise Refused('oversized_credential')
        raw = os.read(fd, before.st_size + 1)
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise Refused('changed_credential')
        return raw


class SessionCenter:
    def __init__(self, runtime):
        self.runtime, self.db = runtime, runtime.db

    def workspace(self, key):
        row = self.db.get('workspace', key)
        # Opening and reading do not call the execution authorization validator.
        self.runtime.readable_directory(row['path'])
        return row

    def register(self, workspace, provider, config_root):
        self.runtime.check_operator()
        project = self.workspace(workspace)
        if provider not in ('codex', 'claude'):
            raise Invalid('Storage discovery is unsupported for this Provider')
        try:
            root = clean_path(config_root)
            allowed = [project['path'], *self.runtime.operator.get('session_read_roots', [])]
            if not any(Path(root).is_relative_to(p) for p in allowed):
                raise Blocked('Storage root must be inside this workspace or operator-approved read roots')
            with opened(root, directory=True):
                pass
        except (OSError, Refused):
            raise Invalid('Choose an accessible native configuration directory without symlinks') from None
        key = session_identity(provider, '', root, '', workspace)
        row = dict(id=key, workspace_id=workspace, provider=provider, config_root=root,
                   home=None, origin='registered')
        self.db.put('session-registration', key, row)
        return dict(id=key, provider=provider, storage_root=display(root), execution_authorized=False)

    def _profiles(self, project):
        nested, coverage = nested_profiles(project['path'])
        profiles = [p for p in self.runtime.operator.get('session_profiles', [])
                    if not p.get('workspace') or p['workspace'] == project['path']]
        profiles += [{k: v for k, v in row.items() if k not in ('id', 'workspace_id')}
                     for row in self.db.listing('session-registration', filters={'workspace_id': project['id']}, limit=None)]
        profiles += nested
        # Dedicated conversation homes remain discoverable even after a failed
        # first turn. They never require cloning another HOME or its records.
        for row in self.db.listing('chat-profile', filters={'workspace': project['id']}, limit=None):
            if row['workspace'] == project['id']:
                b = row['binding']
                profiles.append(dict(provider=b['provider'], home=b['home'],
                    config_root=str(Path(b['home']) / ('.' + b['provider']))))
        owned = self.runtime.owned_sessions(project['id'], validate=False)
        managed = [dict(provider=row['provider'], home=row['home'], config_root=row['config_root'],
                        workspace=project['path'], original_project_root=row['execution_root']) for row in owned]
        return managed + profiles, coverage, owned

    def discover(self, workspace, include_hidden=False):
        self.runtime.check_operator()
        project = self.workspace(workspace)
        profiles, nested, owned = self._profiles(project)
        # A busy installation must report incomplete coverage instead of making
        # every session unavailable when it exceeds the bounded scanner budget.
        unique = list({canonical(p): p for p in profiles}.values())
        profile_coverage = dict(status='partial' if len(unique) > 32 else 'complete',
                                registered=len(unique), scanned=min(len(unique), 32), limit=32)
        profiles = unique[:32]
        directory = self.runtime.storage / 'discovery'
        directory.mkdir(exist_ok=True, mode=0o700)
        discovery = WorkspaceSessions(directory, [project['path']], persist=False,
            profile_config=dict(profiles=profiles, defaults=True))
        selected = next(iter(discovery.roots))
        rows, coverage, seen = [], [], set()
        for p in discovery.profiles:
            if p['workspace'] and p['workspace'] != project['path']:
                continue
            native, report = scan(str(Path(p['config_root']) / ('sessions' if p['provider'] == 'codex' else 'projects')),
                                  p['provider'], discovery.limits, p['session_ids'], include_paths=True)
            coverage.append(dict(profile_id=p['id'], provider=p['provider'], storage_root=display(p['config_root']), **report))
            for record in native:
                public = discovery._row(record, p, selected)
                if not public:
                    continue
                key = session_identity(p['provider'], record['session_id'], p['id'], record['cwd'], project['id'])
                # Duplicated UUIDs in the same profile/cwd cannot select a writer.
                if key in seen:
                    self.db.update('session-handle', key, dict(ambiguous=True))
                    for old in rows:
                        if old['handle'] == key:
                            old['resume'] = False
                            old['capabilities']['read'] = False
                            old['note'] = 'Ambiguous duplicate native identity; metadata only'
                    continue
                seen.add(key)
                hidden = False
                try:
                    hidden = self.db.get('session-visibility', key)['hidden']
                except Invalid:
                    pass
                read_root = p['original'] if p['workspace'] else project['path']
                readable = all(Path(cwd).is_relative_to(read_root) for cwd in record.get('cwds', [record['cwd']]))
                internal = dict(id=key, workspace=workspace, profile=p, native=record, ambiguous=False, authorized_read=readable)
                self.db.put('session-handle', key, internal)
                binding, note = self._binding(internal, project, owned) if readable else (None,
                    'Transcript contains cwd outside the approved workspace mapping; metadata only')
                rows.append(dict(handle=key, identity=key, provider=p['provider'], session_id=record['session_id'],
                    profile_id=p['id'], storage_root=display(p['config_root']), cwd=public['cwd'],
                    association=public['association'], cwd_verified=public['cwd_verified'], hidden=hidden,
                    workspace_id=workspace, updated=record['updated'], status='unconfirmed',
                    capabilities=dict(capabilities(p['provider']), read=readable), resume=binding is not None, note=note))
        return dict(workspace_id=workspace, owned=owned,
            discovery=dict(workspace_id=workspace, sessions=[x for x in rows if include_hidden or not x['hidden']],
                coverage=coverage, nested_coverage=nested, profile_coverage=profile_coverage,
                unsupported=['copilot'], scanned_at=time.time()),
            capabilities={p: capabilities(p) for p in ('codex', 'claude', 'copilot')},
            operations=[self.operation(x['id'], workspace) for x in self.db.listing('session-operation', filters={'workspace': workspace}) if not x.get('diagnostic')])

    def _binding(self, handle, project, owned=None):
        p, native = handle['profile'], handle['native']
        if handle.get('ambiguous') or native['cwd_changed'] or not p['home']:
            return None, 'Native environment or unique cwd identity is unverified; read only'
        # Imported records, including default HOME, are never adopted as writers.
        for row in self.db.listing('chat-profile', filters={'workspace': project['id']}, limit=None):
            b = row['binding']
            if row['workspace'] == project['id'] and b['home'] == p['home'] and b['provider'] == p['provider']:
                if p['config_root'] != str(Path(b['home']) / ('.' + b['provider'])):
                    return None, 'Storage root differs from the managed native environment; read only'
                if b.get('session_id') != native['session_id']:
                    # A native fork shares HOME with its parent. Another managed
                    # UUID is not evidence against this exact transcript identity.
                    continue
                if native['cwd'] != project['path']:
                    return None, 'Original cwd differs from the approved workspace'
                if self._busy(b['home']):
                    return None, 'Managed session home is occupied or requires recovery'
                return b, 'Explicit native UUID in a dedicated managed HOME'
        for row in owned or self.runtime.owned_sessions(project['id'], validate=False):
            if (row['home'] == p['home'] and row['provider'] == p['provider'] and
                    row['config_root'] == p['config_root'] and row['id'] == native['session_id']):
                if not row['resume'] or self._busy(p['home']):
                    return None, 'Controller session is occupied or requires recovery'
                return row['binding'], 'Controller-managed independent role; original cwd preserved'
        return None, 'UUID has no managed native request/receipt binding; external history is read only'

    def _busy(self, home):
        for row in self.db.listing('session-operation', filters={'binding.home': home}, limit=None):
            if row['binding']['home'] == home and row['state'] not in TERMINAL:
                return True
        # Share the exact Controller HOME lease, without interpreting mtimes.
        try:
            with lease(Path(home).parent / (digest(str(Path(home).resolve()).encode()) + '.session.lock')):
                pass
        except Blocked:
            return True
        return False

    def resolve(self, workspace, handle, *, readable=True):
        project = self.workspace(workspace)
        if not isinstance(handle, str) or not __import__('re').fullmatch(r'[0-9a-f]{64}', handle):
            raise Invalid('Expected an opaque authorized session handle')
        old = self.db.get('session-handle', handle)
        if old['workspace'] != workspace:
            raise Blocked('Session belongs to another workspace')
        listing = self.discover(workspace, True)
        if not any(r['handle'] == handle for r in listing['discovery']['sessions']):
            raise Conflict('Session is no longer in the authorized discovery snapshot')
        current = self.db.get('session-handle', handle)
        # A previously indexed locator alone is never proof it still belongs.
        if current.get('ambiguous'):
            raise Conflict('Session disappeared, changed identity or is ambiguous; refresh discovery')
        if readable and not current.get('authorized_read'):
            raise Blocked('Transcript cwd is outside the approved workspace mapping')
        return project, current

    def history(self, workspace, handle, query='', limit=100, *, start_offset=0, end_offset=None, include_metadata=False):
        self.runtime.check_operator()
        _, row = self.resolve(workspace, handle)
        p = row['profile']
        extra = [str(Path(p['config_root']) / name) for name in ('auth.json', '.credentials.json', 'settings.json')]
        # bwrap overlays disappear from the dedicated HOME after each turn.
        # Redact against their approved original sources as well as native files.
        extra += [src for profile in self.runtime.operator['profiles'].values()
                  for src, _ in profile.get('readonly_files', [])]
        secrets_found = credentials(p['home'] or p['config_root'], extra, read_file=credential_bytes)
        messages = conversation(p['provider'], row['native']['path'], limit, query,
                                expected_sha256=row['native']['sha256'], secrets=secrets_found,start_offset=start_offset,end_offset=end_offset,include_metadata=include_metadata)
        return dict(workspace_id=workspace, handle=handle, messages=messages)

    def visibility(self, workspace, handle, hidden):
        self.runtime.check_operator()
        if type(hidden) is not bool:
            raise Invalid('hidden must be a boolean')
        self.resolve(workspace, handle, readable=False)
        self.db.put('session-visibility', handle, dict(id=handle, workspace=workspace, hidden=hidden))
        return dict(handle=handle, hidden=hidden, native_modified=False)

    def prepare(self, request):
        self.runtime.check_operator()
        if not {'workspace','handle','profile','prompt','permission'}<=set(request) or set(request)-{'workspace','handle','profile','prompt','permission','model','effort','mode','auto_verify'} or (set(request)-{'workspace','handle','profile','prompt','permission'} and not hasattr(self.runtime,'node_binding')):
            raise Invalid('Expected workspace, session handle or new, approved profile, prompt and permission')
        project = self.runtime.project(request['workspace'])
        source = self.runtime.validate_source(project['path'])
        if request['permission'] not in ('read', 'workspace-write'):
            raise Invalid('Unsupported session permission')
        if not isinstance(request['prompt'], str) or not request['prompt'].strip() or len(request['prompt']) > 60000:
            raise Invalid('A bounded nonempty user message is required')
        selected=None
        automatic = request.get('auto_verify', False)
        if type(automatic) is not bool: raise Invalid('自动连接检查标记必须为布尔值')
        if hasattr(self.runtime,'node_binding'):
            profile=self.runtime.operator['profiles'].get(request['profile'])
            if not profile:raise Invalid('请选择已登记的执行配置')
            selected=self.runtime.node_binding(project['id'],dict(profile=request['profile'],provider=profile['provider'],session=request['handle'],model=request.get('model',profile['model']),effort=request.get('effort',profile['effort']),mode=request.get('mode','Agent' if request['permission']=='workspace-write' else 'Ask'),permission=request['permission']),admission=not automatic)
        handle, native_sha, cwd = request['handle'], None, project['path']
        if handle == 'new':
            p = self.runtime.operator['profiles'].get(request['profile'])
            if not p or not capabilities(p['provider'])['create']:
                raise Invalid('Choose an operator-approved Codex or Claude execution profile')
            home = str(self.runtime.native / secrets.token_hex(16))
            binding = dict(p, home=home, session_id=None, project=project['id'], role='reviewer',
                           runtime_profile=request['profile'], isolation='bwrap', external_sandbox=p['provider'] == 'codex')
        else:
            _, row = self.resolve(project['id'], handle)
            binding, reason = self._binding(row, project)
            if binding is None:
                raise Blocked(reason)
            binding = dict(binding, session_id=row['native']['session_id'])
            p = self.runtime.operator['profiles'].get(request['profile'])
            if (not p or binding.get('runtime_profile', request['profile']) != request['profile'] or
                    any(binding.get(k) != p.get(k) for k in (('provider','executable','read_paths','readonly_files') if selected else ('provider','executable','model','effort','read_paths','readonly_files')))):
                raise Blocked('Resume requires the exact approved Provider/model/effort/configuration profile')
            native_sha, cwd = row['native']['sha256'], row['native']['cwd']
        if selected:
            binding,cwd,native_sha=selected['binding'],selected['cwd'],selected['native_sha256']
        # No writable access to a frozen Controller candidate or baseline.
        if request['permission'] == 'workspace-write' and cwd != project['path']:
            raise Blocked('Frozen Controller sources can only be continued with read permission')
        binding = dict(binding, role='implementer' if request['permission'] == 'workspace-write' else 'reviewer',
                       tools=['native'] if binding['provider'] == 'codex' else
                       ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit'] if request['permission'] == 'workspace-write' else ['Read', 'Glob', 'Grep'])
        prompt=request['prompt']
        if 'mode' in request:
            mode=request['mode'];prompt=prompt+'\n\n工作方式：'+mode+'。'+('只读制定计划，不执行修改。' if mode=='Plan' else '只读回答，不执行修改。' if mode=='Ask' else '仅在已授权工作区和工具范围内执行。')
        value = dict(workspace=project['id'], source=source, source_sha256=tree_hash(source['root']),
                     binding=binding, cwd=cwd, prompt=prompt, user_prompt=request['prompt'], permission=request['permission'],
                     handle=handle, native_sha256=native_sha, profile=request['profile'],
                     mode=request.get('mode','Agent' if request['permission']=='workspace-write' else 'Ask'),
                     operator_sha256=self.runtime.operator_sha256,
                     human_questions=['确认此消息、精确会话、模型、原 HOME/cwd 和本次工具权限。'])
        checks = []
        if automatic and selected:
            from .model_admission import ModelAdmission
            check = ModelAdmission(self.runtime).pending_check(selected,include_valid=True)
            if check: checks.append(check)
        if checks:
            value['human_questions'][0]+=' 发送前将自动检查当前连接，至多一次只读原生诊断调用，不发送任务或源码；失败不发送消息。'
        return self.db.prepare(dict(value, purpose='native-session',admission_checks=checks))

    def launch(self, permit, expected, answer):
        with self.runtime.lock:
            self.runtime.check_operator()
            # consume is single-use and commits before any native side effects.
            value, receipt = self.db.consume(permit, expected, [answer])
            if value.get('purpose') != 'native-session':
                raise Blocked('Permit is not a native-session authorization')
            self._recheck(value)
            op = dict(value, id=secrets.token_hex(16), state='QUEUED', created_at=time.time(),
                      cancel_requested=False, authorization=receipt)
            self.db.put('session-operation', op['id'], op)
            self.db.put('chat-profile', digest(op['binding']['home'].encode()),
                        dict(workspace=op['workspace'], binding=op['binding']))
            try:
                child = self._spawn(op['id'])
                self.db.update('session-operation', op['id'], dict(worker=process_identity(child.pid)))
                self.runtime.children['chat:' + op['id']] = child
            except OSError:
                self.db.update('session-operation', op['id'], dict(state='FAILED', reason='Native supervisor could not start'))
            return self.operation(op['id'], op['workspace'])

    def _spawn(self, key):
        self.runtime.check_operator()
        return subprocess.Popen([sys.executable, '-B', str(Path(__file__).with_name('session_worker.py')),
            '--operator', str(self.runtime.operator_file), '--sha256', self.runtime.operator_sha256,
            '--operation', key, *(['--candidate-sha256', self.runtime.candidate_sha256] if getattr(self.runtime, 'session_only', False) else []),
            *(['--policy', str(self.runtime.policy_file), '--policy-sha256', self.runtime.policy_sha]
              if hasattr(self.runtime, 'policy_file') else [])], cwd=Path(__file__).resolve().parents[2],
            env=clean_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True)

    def _recheck(self, value):
        if value['operator_sha256'] != self.runtime.operator_sha256:
            raise Blocked('Approved operator profile changed')
        project = self.runtime.project(value['workspace'])
        if self.runtime.validate_source(project['path']) != value['source'] or tree_hash(project['path']) != value['source_sha256']:
            raise Conflict('Workspace changed after the session preview')
        if value['handle'] != 'new':
            _, row = self.resolve(value['workspace'], value['handle'])
            if row['native']['sha256'] != value['native_sha256']:
                raise Conflict('Native session changed after preview')
        with opened(value['cwd'], directory=True):
            pass
        if value['binding'].get('admission_policy'):
            from .model_admission import ModelAdmission
            ModelAdmission(self.runtime).require(value['binding'], value['cwd'])

    def operation(self, key, workspace):
        self.runtime.check_operator()
        row = self.db.get('session-operation', key)
        if row['workspace'] != workspace:
            raise Blocked('Session operation belongs to another workspace')
        b = row['binding']
        process = row.get('process') or next((x['data'].get('process') for x in self.db.listing('session-step',
                        filters={'operation': key, 'kind': 'PROCESS'})), None)
        state = row['state']
        extra=[src for profile in self.runtime.operator['profiles'].values() for src,_ in profile.get('readonly_files',[])]
        from orchestration.privacy import redact
        secret=credentials(b['home'],extra,read_file=credential_bytes)
        reply=safe_text(redact(row['reply'],secret)) if isinstance(row.get('reply'),str) else None
        receipt=row.get('receipt')
        if isinstance(receipt,dict):
            from .usage import public_usage
            receipt=dict(receipt,usage=public_usage(receipt.get('usage')))
        if state not in TERMINAL and not alive(row.get('worker')) and time.time() - row['created_at'] > 3:
            state = 'RECOVERY_REQUIRED'
        return dict(id=key, workspace_id=workspace, state=state, provider=b['provider'],
                    session_id=row.get('native_session') or row.get('receipt', {}).get('session_id') or b['session_id'],
                    requested_session=row.get('requested_session'),
                    model=b['model'], effort=b['effort'], cwd=display(row['cwd']), permission=row['permission'],
                    process=process, alive=alive(process), worker_alive=alive(row.get('worker')),
                    reason=row.get('reason'), error_category=row.get('error_category'),
                    process_result=row.get('process_result'), receipt=receipt, reply=reply,
                    user_message=safe_text(redact(row.get('user_prompt',row.get('prompt','')),secret)))

    def cancel(self, workspace, key):
        self.runtime.check_operator()
        current = self.operation(key, workspace)
        if current['state'] in TERMINAL:
            return current
        self.db.update('session-operation', key, dict(cancel_requested=True))
        if not current['worker_alive']:
            stop(current['process'])
            self.db.update('session-operation', key, dict(state='CANCELLED', reason='Explicit cancellation after supervisor loss'))
        return self.operation(key, workspace)

    def execute(self, key):
        """Detached worker entry; no Controller state or Stable approval writes."""
        value = self.db.update('session-operation', key, dict(state='STARTING'), states={'QUEUED'})
        journal = SessionJournal(self.db, key)
        try:
            self.runtime.check_operator()
            self._recheck(value)
            b = value['binding']
            with ExitStack() as stack:
                stack.enter_context(lease(Path(value['source']['common_dir']) / 'devflower-writer.lock',
                                          shared=value['permission'] == 'read'))
                stack.enter_context(lease(Path(b['home']).parent / (digest(str(Path(b['home']).resolve()).encode()) + '.session.lock')))
                if journal.get(key)['data']['cancel_requested']:
                    raise Blocked('Cancelled before native spawn')
                # Re-read exact transcript under the shared HOME lease.
                self._recheck(value)
                artifacts = self.runtime.storage / 'session-artifacts' / key
                artifacts.mkdir(parents=True, mode=0o700)
                schema = artifacts / 'reply.schema.json'
                schema.write_bytes(canonical(REPLY_SCHEMA))
                limits = dict(step_timeout_seconds=b.get('timeout_seconds', 1800), max_log_bytes=8_000_000)
                verification=None
                if b.get('admission_policy'):
                    from .model_admission import ModelAdmission
                    verification=ModelAdmission(self.runtime).require(b,value['cwd'])
                reply = CLI(b['provider'], b, limits).start(value['prompt'] +
                    '\nReturn a JSON object with your visible answer in the string field reply.',
                    value['cwd'], artifacts, schema, journal, key)
                validate(reply['report'], REPLY_SCHEMA)
                if verification:
                    ModelAdmission(self.runtime).renew(b,value['cwd'],reply,verification)
                # Pin receipt to the actual UUID, requested environment and process.
                summary = {k: reply.get(k) for k in ('session_id', 'requested_model', 'requested_effort',
                    'resolved_model', 'resolved_effort', 'permission_mode', 'process', 'usage')}
                summary.update(cwd=value['cwd'], home=b['home'], provider=b['provider'], profile=value['profile'])
                self.db.update('session-operation', key, dict(state='CANCELLED' if journal.get(key)['data']['cancel_requested'] else 'COMPLETED',
                    receipt=summary, reply=safe_text(reply['report'].get('reply', ''))))
        except (Blocked, Conflict, Invalid, OSError, ValueError) as exc:
            if value['binding'].get('admission_policy'):
                from .model_admission import ModelAdmission
                ModelAdmission(self.runtime).invalidate(value['binding'], value['cwd'])
            cancelled = journal.get(key)['data']['cancel_requested']
            self.db.update('session-operation', key, dict(state='CANCELLED' if cancelled else 'FAILED',
                reason=safe_text(str(exc))))


class SessionJournal:
    """Adapter process journal backed only by private Studio storage."""
    def __init__(self, db, operation, *, cancelled=None):
        self.db, self.operation,self.cancelled = db, operation,cancelled

    def get(self, run):
        data=self.db.get('session-operation', self.operation)
        if self.cancelled is not None:data=dict(data,cancel_requested=data.get('cancel_requested',False) or self.cancelled())
        return dict(data=data)

    def intent(self, run, kind, data):
        key = secrets.token_hex(16)
        self.db.put('session-step', key, dict(id=key, operation=self.operation, kind=kind, data=data, status='INTENT'))
        if kind == 'AGENT_REQUEST' and data.get('requested_session'):
            self.db.update('session-operation', self.operation, dict(requested_session=data['requested_session']))
        return key

    def bind_session(self, sid):
        if not isinstance(sid, str) or not UUID.fullmatch(sid):
            raise Blocked('Native journal returned an invalid exact UUID')
        row = self.db.get('session-operation', self.operation)
        if row.get('diagnostic'):
            return
        binding = dict(row['binding'], session_id=sid)
        self.db.put('chat-profile', digest(binding['home'].encode()),
                    dict(workspace=row['workspace'], binding=binding))

    def finish(self, key, status, **updates):
        row = self.db.get('session-step', key)
        self.db.update('session-step', key, dict(status=status, data=dict(row['data'], **updates)))
        if row['kind'] == 'PROCESS' and status == 'RUNNING':
            self.db.update('session-operation', self.operation, dict(state='RUNNING', process=updates.get('process'),native_started_at=time.time()))
            operation = self.db.get('session-operation', self.operation)
            if operation['binding']['provider'] == 'claude' and operation.get('requested_session'):
                # Claude preassigns an exact UUID. It becomes resumable only if
                # that same UUID is also found in its native transcript.
                self.bind_session(operation['requested_session'])
        elif row['kind'] == 'PROCESS' and status == 'EXITED':
            self.db.update('session-operation', self.operation, dict(process_result=updates,native_finished_at=time.time()))
        elif row['kind'] == 'AGENT_REQUEST' and updates.get('actual_session'):
            self.db.update('session-operation', self.operation, dict(native_session=updates['actual_session']))
            self.bind_session(updates['actual_session'])
        if updates.get('error_category'):
            self.db.update('session-operation', self.operation, dict(error_category=updates['error_category']))
