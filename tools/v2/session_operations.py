"""Explicit standalone native effects, bound to V2 workspace and provider evidence."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
from threading import BoundedSemaphore, Thread
import time
import uuid

from orchestration.common import Blocked, canonical, digest
from orchestration.process import group_alive
from orchestration.store import process_identity
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.local_claude_adapter import LocalClaude, claude_secrets
from studio.local_codex_adapter import LocalCodex
from studio.model_registration import profile_identity
from studio.native_codex_image import binary_digest
from studio.native_codex_rpc import CodexRPC, metadata, metadata_guard
from studio.native_local import native_busy
from studio.server import Conflict
from studio.session_sources import UUID
from studio.usage import public_usage

from .execution_facts import failure_fact
from .claude_session_profile import discover_cli, environment_identity, legacy_endpoint_valid, startup_environment
from .native_session_links import claim_key, claude_workflow_history, require_creation_profile, verified_workflow
from .native_writer_state import require_native_writer_idle
from .native_history_config import CONFIG_BYTES, private_bytes, unique_pairs
from .native_session_views import verify_bytes
from .native_sessions import HANDLE, REVISION, prove_file
from .project_files import FileView, mount_id
from .session_identity import SessionLocator, SessionRefused, StorageRootIdentity, session_path, verified_directory
from .session_lease import current_owner, production_session_gate

KIND = 'native-session-operation'
STEP = 'native-session-step'
INTENT = re.compile(r'[A-Za-z0-9_-]{16,96}')
MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}')
PROVIDERS = ('codex', 'claude')
REASON_UNKNOWN = '原生结果待核验。已保留本次请求记录，未自动重发。'


def load_write_profiles(path):
    try:
        path = session_path(path)
        with verified_directory(path.parent) as directory:
            raw = private_bytes(FileView(directory, mount_id(directory)), directory, path.name, CONFIG_BYTES)
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        if (not isinstance(value, dict) or set(value) - {'version', 'codex_terminal', *PROVIDERS}
                or type(value.get('version')) is not int or value['version'] != 1
                or 'codex' in value and value['codex'] is not True
                or 'codex_terminal' in value and (value['codex_terminal'] is not True or value.get('codex') is not True)):
            raise ValueError()
        if 'claude' in value:
            claude = {} if value['claude'] is True else value['claude']
            if (not isinstance(claude, dict) or set(claude) - {'model', 'effort', 'anthropic_base_url'}
                    or 'model' in claude and (not isinstance(claude['model'], str) or not MODEL.fullmatch(claude['model']))
                    or 'effort' in claude and claude['effort'] not in ('low', 'medium', 'high', 'max', 'xhigh')
                    or 'anthropic_base_url' in claude and not legacy_endpoint_valid(claude['anthropic_base_url'])):
                raise ValueError()
            value['claude'] = claude
        return {key: value[key] for key in (*PROVIDERS, 'codex_terminal') if key in value}
    except (OSError, ValueError, TypeError, Blocked):
        raise Invalid('原生写入配置无效。请核对启动配置与私有权限。') from None


def operation_id(workspace, intent):
    if not isinstance(intent, str) or not INTENT.fullmatch(intent):
        raise Invalid('会话操作意图无效。')
    return digest(canonical(['native-session-operation-v1', workspace, intent]))


class OperationJournal:
    """Provider transport callbacks backed by the reserved V2 effect row."""
    process_started = False

    def __init__(self, operations, row, check):
        self.operations, self.row, self.check_before_spawn = operations, row, check
        self.db = operations.store.db
        self.writer_gates = ExitStack()
        self.writer_sid = None

    def get(self, key):
        return {'data': self.db.get(KIND, key)}

    def intent(self, run, kind, data):
        key = uuid.uuid4().hex
        self.db.put(STEP, key, dict(id=key, operation=run, kind=kind, status='INTENT', data=data))
        return key

    def finish(self, key, status, **updates):
        step = self.db.get(STEP, key)
        row = self.row
        if step['kind'] == 'PROCESS' and status == 'RUNNING':
            process = updates.get('process')
            if (not process or process['pid'] != process['pgid'] or process['pid'] != process['sid']):
                raise Blocked('原生进程身份未确认。')
            self._require_process_scope(process)
            fd = os.open(f"/proc/{process['pid']}/exe", os.O_RDONLY)
            try:
                image = binary_digest(fd)
            finally:
                os.close(fd)
            self._require_process_scope(process)
            if image != row['binding']['_executable_sha256']:
                raise Blocked('原生进程映像已变化，未发送输入。')
            self.db.change(KIND, row['id'], dict(state='RUNNING', process=process, process_step=key,
                                                effect_uncertain=True), states={'RESERVED'})
        sid = updates.get('actual_session')
        if step['kind'] == 'AGENT_REQUEST' and sid:
            if not row['target'] and self.writer_sid is None:
                self.writer_gates.enter_context(production_session_gate(row['binding'], None, target=sid))
                self.writer_sid = sid
                require_native_writer_idle(self.operations.store, row['binding'], sid, ignore_operation=row['id'])
            if self.writer_sid is not None and self.writer_sid != sid:
                raise SessionRefused('LOCATOR_REFUSED')
            self.operations.bind_observed(row, sid)
        self.db.change(STEP, key, dict(status=status, data=dict(step['data'], **updates)))

    def _require_process_scope(self, process):
        root = Path('/proc') / str(process['pid'])
        if os.readlink(root / 'cwd') != self.row['binding']['cwd']:
            raise SessionRefused('LOCATOR_REFUSED')
        fd = os.open(root / 'cwd', os.O_RDONLY | os.O_DIRECTORY)
        try:
            if StorageRootIdentity.from_fd(fd).fields() != self.row['binding']['cwd_identity']:
                raise SessionRefused('LOCATOR_REFUSED')
        finally:
            os.close(fd)
        current = process_identity(process['pid'])
        if not current or any(current[key] != process[key] for key in ('host', 'boot_id', 'pid', 'start', 'pgid', 'sid')):
            raise SessionRefused('OWNER_UNKNOWN')

    def input_sent(self, step, size, sha):
        if sha != self.row['prompt_sha256'] or size != self.row['prompt_size']:
            raise Blocked('输入回执与本次授权不一致。')
        self.db.change(KIND, self.row['id'], {'input_step': step, 'input_sha256': sha})

    def native_terminal_event(self, event):
        row = self.db.get(KIND,self.row['id'])
        self.db.change(KIND,self.row['id'],{'terminal_count':row.get('terminal_count',0)+1,'terminal_event':{
            key:event.get(key) for key in ('type','session_id','native_turn_id')}})

    def native_limit_event(self, value):
        self.db.change(KIND,self.row['id'],{'native_limit':value})

    def native_tool_event(self, _):
        raise Blocked('对话出现未获准的工具活动，已停止本机进程。')


class SessionOperations:
    authority = 'explicit-session-send'

    def __init__(self, store, reader, codex, profiles):
        self.store, self.reader, self.codex = store, reader, codex
        self.profiles = {}
        self.claude_env = {}
        self.codex_terminal = profiles.get('codex_terminal') is True
        self.initialize_journal(store)
        for provider in PROVIDERS:
            if provider not in profiles:
                continue
            options = profiles[provider]
            source = next((source for source in reader.static if source.profile.id == 'default-' + provider), None)
            if source is None:
                raise Invalid('原生写入需要同时明确批准对应的默认原生历史根。')
            home = reader.home
            config = home / ('.' + provider)
            binding = dict(provider=provider, home=str(home), config_root=str(config),
                           runtime_profile='default-' + provider, execution_backend='native-local',
                           root_identity=StorageRootIdentity.capture(config).fields(),
                           local_lock_root=str(config / '.devflower-writer-locks'))
            if provider == 'codex':
                if not codex or (codex.home, codex.config_root) != (str(home), str(config)):
                    raise Invalid('Codex 写入与当前原生账户根不一致。')
            else:
                if os.environ.get('CLAUDE_CONFIG_DIR'):
                    raise Invalid('Claude 原生写入仅支持默认 HOME 配置。')
                self.claude_env = startup_environment(os.environ, options.get('anthropic_base_url'))
                binding.update(discover_cli(), model=options.get('model'), effort=options.get('effort'),
                               cli_environment_sha256=environment_identity(self.claude_env),
                               role='reviewer', chat_only=True, readonly_files=[])
                self.remember_claude_secrets(binding)
            self.profiles[provider] = (source, binding)
        self.reader.managed_records = self.managed_records

    def initialize_journal(self, store):
        self.slots = BoundedSemaphore(4)
        self.owner = current_owner()
        from .store import private_directory
        self.artifacts = private_directory(store.state / 'session-artifacts')

    def remember_claude_secrets(self, binding):
        literals = claude_secrets(binding, self.claude_env)
        with self.reader.guard:
            self.reader.literals = tuple(sorted(set(self.reader.literals) | literals, key=len, reverse=True))

    def status(self):
        return list(self.profiles)

    def _public(self, row):
        state = row['state']
        if state in ('RESERVED', 'RUNNING') and row.get('owner') != self.owner:
            state = 'RECOVERY_REQUIRED' if row.get('effect_uncertain') else 'NOT_SENT'
        result = {key: row[key] for key in ('workspace', 'provider', 'intent_key') }
        result.update(state=state, reason=REASON_UNKNOWN if state == 'RECOVERY_REQUIRED' else row.get('reason'))
        if state == 'COMPLETED':
            result.update(handle=row['result_handle'], receipt=row['receipt'], reply=row['reply'])
        if row.get('execution_failure'):
            result['execution_failure'] = row['execution_failure']
        return result

    def _get(self, key):
        try:
            return self.store.db.get(KIND, key)
        except Invalid:
            return None

    def operation(self, workspace, intent_key):
        self.store.workspace(workspace)
        row = self._get(operation_id(workspace, intent_key))
        if row is None or row.get('authorization', SessionOperations.authority) != self.authority:
            return {'workspace': workspace, 'intent_key': intent_key, 'state': 'UNREGISTERED'}
        return self._public(row)

    def resolve(self, workspace, handle, revision):
        path, identity, proof = self.reader.workspace(workspace)
        with self.reader.guard:
            snapshot = self.reader._snapshot(workspace, revision)
            record = snapshot.reads.get(handle)
        if record is None or identity != snapshot.workspace_identity:
            raise SessionRefused('LOCATOR_REFUSED')
        with self.reader.verified_record(workspace, handle, path, identity, record) as (view, locator):
            verify_bytes(view, record.ref)
        self.reader.require_proof(proof)
        with self.reader.guard:
            self.reader._snapshot(workspace, revision)
        return locator, record

    def claude_history_state(self, binding, locator, record, db):
        """Reconcile an exact current record against V2's completed creation and last receipt."""
        scope = ('provider', 'project', 'cwd', 'cwd_identity', 'home', 'config_root', 'root_identity',
                 'runtime_profile', 'cli_version', '_executable_sha256', 'native_profile')
        latest = None
        created = False
        rows = db.execute("""
            SELECT data FROM records WHERE kind=? AND json_extract(data,'$.workspace')=?
            AND json_extract(data,'$.provider')='claude'
            AND coalesce(json_extract(data,'$.observed_session'),json_extract(data,'$.target'))=?
            ORDER BY rowid DESC
        """, (KIND, locator.workspace_id, locator.native_uuid))
        for (raw,) in rows:
            row = json.loads(raw)
            prior = row.get('binding', {})
            if any(prior.get(key) != binding.get(key) for key in scope):
                continue
            if row['state'] == 'RECOVERY_REQUIRED' or row['state'] == 'RUNNING' and row['owner'] != self.owner:
                return 'RECOVERY_REQUIRED'
            if (row['state'] == 'COMPLETED' and row.get('observed_session') == locator.native_uuid
                    and row.get('receipt', {}).get('session_id') == locator.native_uuid):
                if latest is None:
                    latest = row
                created |= row.get('handle') is None and prior.get('new_session_id') == locator.native_uuid
        workflows, uncertain = claude_workflow_history(self.store, db, binding, locator)
        if uncertain:
            return 'RECOVERY_REQUIRED'
        for row in workflows:
            if any(row['binding'].get(key) != binding.get(key) for key in scope):
                continue
            created |= row['is_creation']
            if latest is None or row['created'] > latest.get('created', 0):
                latest = row
        if not created:
            return 'EXTERNAL_SESSION'
        if (latest.get('relative') != str(record.ref.relative) or latest.get('native_after_sha256') != record.ref.sha
                or any(latest['binding'].get(key) != binding.get(key) for key in (
                    'cli_environment_sha256', 'direct_environment_sha256'))):
            return 'MANAGED_HISTORY_CHANGED'
        return 'managed'

    def require_claude_history(self, binding, locator, record):
        with self.store.db.tx(read=True) as db:
            state = self.claude_history_state(binding, locator, record, db)
        if state != 'managed':
            raise SessionRefused(state)

    def add_continue_eligibility(self, listing):
        workspace, revision = listing['workspace'], listing['revision']
        with self.reader.guard:
            snapshot = self.reader._snapshot(workspace, revision)
        claude = self.profiles.get('claude')
        claude_binding = None
        if claude:
            try:
                cwd = self.store.workspace(workspace)['path']
                claude_binding = dict(claude[1], project=workspace, cwd=cwd,
                    cwd_identity=snapshot.workspace_identity.fields(), direct_environment_sha256=profile_identity(claude[1]))
            except (OSError, ValueError, Blocked):
                pass
        with self.store.db.tx(read=True) as db:
            for row in listing['sessions']:
                profile = self.profiles.get(row['provider'])
                record = snapshot.reads.get(row['handle'])
                eligible = bool(profile and record and record.source.profile == profile[0].profile
                    and len(record.cwd_evidence) == 1 and record.cwd_evidence[0][0] == Path('.'))
                if row['provider'] == 'codex':
                    row['can_request_continue'] = eligible
                elif claude:
                    state = 'unverified'
                    if record and record.cwd_evidence and record.source.profile == claude[0].profile and claude_binding is not None:
                        try:
                            locator = record.index.resolve(workspace, row['handle'])
                            scope = dict(claude_binding, cwd=locator.original_cwd,
                                         cwd_identity=record.cwd_evidence[-1][1].fields())
                            state = self.claude_history_state(scope, locator, record, db)
                        except (OSError, ValueError, Blocked):
                            pass
                    row.update(claude_session_state=state, can_request_continue=eligible and state == 'managed')

    def prepare_binding(self, workspace, provider, handle, revision, model=None, effort=None):
        if provider not in self.profiles:
            raise Invalid('此 Provider 尚未启用独立会话写入。')
        source, approved = self.profiles[provider]
        cwd = self.store.workspace(workspace)['path']
        locator, record = self.resolve(workspace, handle, revision) if handle else (None, None)
        if locator is not None:
            if (locator.provider != provider or locator.profile_id != source.profile.id
                    or locator.root_identity != source.profile.identity or locator.original_cwd != cwd
                    or record.source.profile.root != source.profile.root or len(record.cwd_evidence) != 1):
                raise SessionRefused('LOCATOR_REFUSED')
        if locator is not None:
            require_creation_profile(self.store,approved['config_root'],locator.native_uuid,provider)
        if provider == 'codex':
            binding = dict(self.codex.binding(workspace, dict(provider=provider, model=model, effort=effort)), **approved)
        else:
            binding = dict(approved, direct_environment_sha256=profile_identity(approved))
            self.remember_claude_secrets(binding)
        cwd_identity = StorageRootIdentity.capture(cwd)
        if digest(canonical([cwd, cwd_identity.device, cwd_identity.inode])) != workspace:
            raise SessionRefused('LOCATOR_REFUSED')
        binding.update(cwd=cwd, cwd_identity=cwd_identity.fields(), project=workspace,
                       session_id=locator.native_uuid if locator else None, conversation_format='text')
        if provider == 'claude' and locator is not None:
            self.require_claude_history(binding, locator, record)
        return binding, locator, record

    def send(self, data):
        base = {'workspace', 'provider', 'handle', 'revision', 'intent_key', 'prompt'}
        provider, handle, revision = (data.get(key) for key in ('provider', 'handle', 'revision'))
        prompt = data.get('prompt')
        if (provider not in PROVIDERS or set(data) != (base | {'model', 'effort'} if provider == 'codex' else base)
                or not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 60000 or '\x00' in prompt
                or handle is None and revision is not None
                or handle is not None and (not isinstance(handle, str) or not HANDLE.fullmatch(handle)
                    or not isinstance(revision, str) or not REVISION.fullmatch(revision))):
            raise Invalid('需要明确的 Provider、会话和本次用户消息。')
        workspace = data['workspace']
        self.store.workspace(workspace)
        key = operation_id(workspace, data['intent_key'])
        request_sha = digest(canonical(data))
        prior = self._get(key)
        if prior is not None:
            if prior.get('authorization', SessionOperations.authority) != self.authority or prior['request_sha256'] != request_sha:
                raise Conflict('同一发送意图对应不同请求。')
            return self._public(prior)
        binding, locator, record = self.prepare_binding(workspace, provider, handle, revision, data.get('model'), data.get('effort'))
        return self.reserve(workspace, data['intent_key'], prompt, binding, handle, revision, locator, record,
                            request_sha)

    def reserve(self, workspace, intent, prompt, binding, handle, revision, locator, record, request_sha):
        key = operation_id(workspace, intent)
        provider = binding['provider']
        if not self.slots.acquire(blocking=False):
            raise Conflict('已有多个原生操作正在执行。请查询原操作后再提交。')
        started = False
        try:
            if provider == 'claude' and locator is None:
                binding['new_session_id'] = str(uuid.uuid4())
            row = dict(id=key, workspace=workspace, provider=provider, intent_key=intent, handle=handle,
                       revision=revision, request_sha256=request_sha, prompt_sha256=digest(prompt.encode()),
                       prompt_size=len(prompt.encode()), authorization=self.authority, owner=self.owner,
                       binding=binding, native_sha256=record.ref.sha if record else None,
                       target=binding['session_id'] or binding.get('new_session_id'), state='RESERVED', created=time.time())
            with self.store.db.tx() as db:
                stored = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone()
                if stored:
                    prior = json.loads(stored[0])
                    if prior.get('authorization', SessionOperations.authority) != self.authority or prior['request_sha256'] != request_sha:
                        raise Conflict('同一发送意图对应不同请求。')
                    return self._public(prior)
                db.execute('INSERT INTO records VALUES(?,?,?)', (KIND, key, canonical(row).decode()))
            try:
                Thread(target=self._execute, args=(row, prompt, locator, record), daemon=True,
                       name='native-session-' + key[:8]).start()
            except RuntimeError:
                failed = self.store.db.change(KIND, key, dict(state='NOT_SENT', reason='本机无法启动会话执行线程。'))
                return self._public(failed)
            started = True
            return self._public(row)
        finally:
            if not started:
                self.slots.release()

    def require_idle(self, binding, *, ignore=None, owner_kind=KIND):
        sid = binding.get('session_id') or binding.get('new_session_id')
        if not sid:
            return
        require_native_writer_idle(self.store, binding, sid,
            ignore_operation=ignore if owner_kind == KIND else None,
            ignore_pty=ignore if owner_kind == 'native-session-pty' else None)
        if binding['provider'] == 'codex':
            try:
                self.store.db.get('native-session-claim', claim_key(binding['config_root'], sid))
            except Invalid:
                pass
            else:
                verified_workflow(self.store, binding['config_root'], sid)
            if native_busy(binding, sid):
                raise SessionRefused('EXTERNAL_BUSY')

    def require_scope(self, binding):
        if (self.store.workspace(binding['project'])['path'] != binding['cwd']
                or StorageRootIdentity.capture(binding['cwd']).fields() != binding['cwd_identity']):
            raise SessionRefused('LOCATOR_REFUSED')
        if StorageRootIdentity.capture(binding['config_root']).fields() != binding['root_identity']:
            raise SessionRefused('ROOT_CHANGED')

    def require_binding(self, binding):
        self.require_scope(binding)
        if binding['provider'] == 'codex':
            self.codex.require_account(binding)
        elif (profile_identity(binding) != binding['direct_environment_sha256']
              or environment_identity(self.claude_env) != binding['cli_environment_sha256']):
            raise SessionRefused('ROOT_CHANGED')

    def check_resume(self, row, locator, record):
        binding = row['binding']
        self.require_binding(binding)
        if locator is None:
            return
        path, identity, proof = self.reader.workspace(row['workspace'])
        with self.reader.verified_record(row['workspace'], row['handle'], path, identity, record) as (view, current):
            if current != locator or record.ref.sha != row['native_sha256']:
                raise SessionRefused('LOCATOR_REFUSED')
            verify_bytes(view, record.ref)
        self.reader.require_proof(proof)
        if binding['provider'] == 'claude':
            self.require_claude_history(binding, locator, record)
            return
        with metadata_guard(self.store.state, timeout=20) as remaining:
            with CodexRPC(binding['executable'], binding['home'], binding['config_root'], binding['cwd'],
                          timeout=remaining) as rpc:
                thread = rpc.request('thread/read', {'threadId': locator.native_uuid, 'includeTurns': False})['thread']
                config = rpc.request('config/read', {'cwd': binding['cwd'], 'includeLayers': False})['config']
        if (thread.get('id') != locator.native_uuid or thread.get('cwd') != binding['cwd']
                or thread.get('path') != str(record.source.profile.root / record.ref.relative)
                or thread.get('modelProvider') != (config.get('model_provider') or 'openai')):
            raise SessionRefused('LOCATOR_REFUSED')

    def bind_observed(self, row, sid):
        if not isinstance(sid, str) or not UUID.fullmatch(sid) or row['target'] and sid != row['target']:
            raise SessionRefused('LOCATOR_REFUSED')
        if row['provider'] == 'codex' and not row['target']:
            key = claim_key(row['binding']['config_root'], sid)
            with self.store.db.tx() as db:
                claim = db.execute("SELECT data FROM records WHERE kind IN ('native-session-claim','standalone-native-claim') AND id=?",
                                   (key,)).fetchall()
                if any(json.loads(item[0]).get('operation') != row['id'] for item in claim):
                    raise SessionRefused('LOCATOR_REFUSED')
                db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)',
                           ('standalone-native-claim', key, canonical({'operation': row['id']}).decode()))
        self.store.db.change(KIND, row['id'], {'observed_session': sid})

    def history_source(self, binding):
        return self.profiles[binding['provider']][0]

    def prove_after(self, binding, sid, source):
        if binding['provider'] == 'codex':
            thread = metadata(binding, binding['cwd'], 'thread/read', dict(threadId=sid, includeTurns=False))['thread']
            if thread['id'] != sid or thread.get('cwd') != binding['cwd']:
                raise SessionRefused('LOCATOR_REFUSED')
            relative = Path(thread['path']).relative_to(source.profile.root)
        else:
            directory = re.sub('[^a-zA-Z0-9]', '-', binding['cwd'])
            relative = Path(directory) / (sid + '.jsonl')
        return prove_file(source, binding['project'], binding['cwd'], sid, relative)

    def transport(self, binding, limits, *, managed_resume):
        return LocalCodex(binding, limits) if binding['provider'] == 'codex' else LocalClaude(
            binding, limits, credential_env=self.claude_env, managed_resume=managed_resume)

    def _execute(self, row, prompt, locator, record):
        binding = row['binding']
        provider = row['provider']
        def check():
            self.check_resume(row, locator, record)
            self.require_idle(binding, ignore=row['id'])
        journal = OperationJournal(self, row, check)
        try:
            with journal.writer_gates as held:
                if row['target'] is not None:
                    held.enter_context(production_session_gate(binding, locator, target=row['target']))
                check()
                artifacts = self.artifacts / row['id']
                artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
                limits = dict(step_timeout_seconds=300, max_log_bytes=2_000_000)
                adapter = self.transport(binding, limits, managed_resume=locator is not None)
                try:
                    result = adapter.start(prompt, binding['cwd'], artifacts, None, journal, row['id'])
                finally:
                    if provider == 'claude':
                        self.remember_claude_secrets(binding)
                final = self.store.db.get(KIND, row['id'])
                process = result.get('process')
                step = self.store.db.get(STEP, final['process_step'])
                sid = result['session_id']
                if (sid != final.get('observed_session') or not process or process['exit'] != 0
                        or any(process.get(key) for key in ('timeout', 'cancelled', 'overflow'))
                        or process['operation_id'] != final['process_step'] or process['process'] != final['process']
                        or step['status'] != 'EXITED' or step['data'].get('operation_id') != process['operation_id']
                        or final.get('input_step') != process['operation_id'] or final.get('input_sha256') != row['prompt_sha256']
                        or group_alive(process['process'])
                        or any(result.get(key) != binding[key] for key in ('home', 'cwd', 'provider'))
                        or result.get('requested_model') != binding['model'] or result.get('requested_effort') != binding['effort']
                        or not isinstance(result['report'].get('reply'), str)
                        or provider == 'codex' and (final.get('terminal_count',1)!=1 or final.get('terminal_event') != {
                            'type':'turn.completed','session_id':sid,'native_turn_id':result.get('native_turn_id')})):
                    raise Blocked('原生终结回执无法关联到本次输入。')
                source = self.history_source(binding)
                actual, ref, _ = self.prove_after(binding, sid, source)
                self.require_scope(binding)
                completion = dict(
                    state='COMPLETED', effect_uncertain=False, result_handle=source.registry.handle(actual),
                    relative=str(ref.relative), native_after_sha256=ref.sha, reply=safe_text(result['report']['reply']),
                    receipt=dict(provider=provider, session_id=sid, native_turn_id=result.get('native_turn_id'),
                                 requested_model=binding['model'],requested_effort=binding['effort'],
                                 resolved_model=result.get('resolved_model', 'unknown'),
                                 resolved_effort=result.get('resolved_effort', 'unknown'),
                                 raw_report_sha256=result.get('raw_report_sha256'), transport=result.get('transport'),
                                 usage=public_usage(result.get('usage'))))
            # The durable RUNNING state fences cooperating writers until the lease has released.
            self.store.db.change(KIND, row['id'], completion, states={'RUNNING'})
        except Exception as exc:
            current = self.store.db.get(KIND, row['id'])
            uncertain = bool(current.get('effect_uncertain') or journal.process_started)
            step_key = current.get('process_step')
            try:
                step = self.store.db.get(STEP,step_key) if step_key else {}
            except Invalid:
                step = {}
            process = step.get('data',{}) if step.get('status')=='EXITED' else {}
            sent = bool(current.get('input_sha256')==row['prompt_sha256'] and current.get('input_step')==step_key)
            terminal = current.get('terminal_event') or {}
            failed = (provider=='codex' and current.get('terminal_count')==1 and sent
                and terminal.get('type')=='turn.failed' and current.get('observed_session')
                and terminal.get('session_id')==current['observed_session']
                and type(process.get('exit')) is int and process['exit']!=0
                and process.get('operation_id')==step_key and process.get('process')==current.get('process')
                and not any(process.get(key) for key in ('timeout','cancelled','overflow'))
                and not group_alive(current['process']))
            fact = failure_fact(provider,exc,process=process,uncertain=uncertain,sent=sent,
                                terminal_failed=failed,native_limit=current.get('native_limit'))
            state = 'FAILED' if failed else 'RECOVERY_REQUIRED' if uncertain else 'NOT_SENT'
            self.store.db.change(KIND,row['id'],dict(state=state,reason=fact['reason'],execution_failure=fact,
                                                    effect_uncertain=uncertain and not failed))
        finally:
            self.slots.release()

    def managed_records(self, workspace):
        seen = set()
        with self.store.db.tx(read=True) as db:
            rows = db.execute("SELECT data FROM records WHERE kind=? AND json_extract(data,'$.workspace')=? "
                              "AND json_extract(data,'$.state')='COMPLETED' ORDER BY rowid DESC LIMIT 120",
                              (KIND, workspace)).fetchall()
        for (raw,) in rows:
            row = json.loads(raw)
            handle, provider = row['result_handle'], row['provider']
            if row.get('authorization', SessionOperations.authority) != self.authority or handle in seen or provider not in self.profiles:
                continue
            seen.add(handle)
            source, current = self.profiles[provider]
            binding = row['binding']
            if any(binding.get(key) != current.get(key) for key in ('provider', 'home', 'config_root', 'root_identity')):
                continue
            yield source, binding['cwd'], row['observed_session'], row['relative'], handle
