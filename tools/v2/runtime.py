"""V2 execution boundary around the existing scheduler, native adapter and journal."""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from orchestration.common import Blocked, canonical, digest, save
from orchestration.contracts import validate
from orchestration.process import group_alive
from orchestration.store import alive, process_identity
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_data import public_data
from studio.workbench_graph import request_sections, request_text
from studio.workbench_scheduler import Scheduler, Journal, REPLY
from studio.local_codex_adapter import LocalCodex
from studio.native_codex_image import binary_digest
from studio.session_sources import UUID
from .native_session_links import claim_key
from .session_identity import StorageRootIdentity
from .session_lease import production_session_gate
from studio.usage import public_usage
from . import worker_facts
from .provider import CodexProvider, public_provider_error
from .cli_providers import ProviderRegistry
from .definitions import compile_definition, source_manifest, ROOT
from .documents import check_chain

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'}


def persist_completion(artifacts,completion,storage):
    """Atomic files plus durable directory entries before activation publication."""
    def durable(name,value):
        save(artifacts/name,value)  # fsync temporary bytes, then atomic replace.
        for directory in [artifacts,*artifacts.parents]:
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
            if directory==storage:break
    durable('completion.json',completion)
    durable('completion-proof.json',dict(version=1,run=completion['run'],activation=completion['activation'],
        request_sha256=completion['request_sha256'],completion_sha256=digest(canonical(completion))))


class V2Journal(Journal):
    """Retain old Journal process/send semantics; never create legacy chat indexes."""
    def __init__(self, scheduler, activation):
        super().__init__(scheduler, activation)
        self.writer_gates = ExitStack()
        self.writer_sid = None

    def intent(self, run, kind, data):
        key = super().intent(run, kind, data)
        self.s.db.change('native-step', key, {'created_at': time.time()})
        return key

    def bind_session(self, sid):
        if not isinstance(sid, str) or not UUID.fullmatch(sid):
            raise Blocked('原生会话身份无效')
        activation = self.s.db.get('activation', self.key)
        chat = self.s.db.get('node-session', activation['chat_key'])
        native_scope = chat.get('native_scope')
        if self.writer_sid is not None and self.writer_sid != sid:
            raise Blocked('新节点原生会话身份发生变化。')
        if native_scope and self.writer_sid is None:
            binding = dict(chat['binding'], **native_scope, session_id=sid)
            self.writer_gates.enter_context(production_session_gate(binding, None))
            self.writer_sid = sid
        claim = claim_key(chat['binding']['config_root'], sid)
        with self.s.db.tx() as db:
            row = db.execute("SELECT data FROM records WHERE kind='native-session-claim' AND id=?", (claim,)).fetchone()
            standalone = db.execute("SELECT 1 FROM records WHERE kind='standalone-native-claim' AND id=?", (claim,)).fetchone()
            if standalone or row and json.loads(row[0])['binding_id'] != chat['id']:
                raise Blocked('新节点收到已被其他节点绑定的原生会话；未采用该会话')
            db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)', ('native-session-claim', claim, canonical(dict(binding_id=chat['id'], session_id=sid)).decode()))
        binding = dict(chat['binding'], session_id=sid)
        self.s.db.change('node-session', chat['id'], {'binding': binding, 'native_session_id': sid})

    def check_before_spawn(self):
        self.s.rt.check_operator()
        self.s.controls()

    def finish(self, key, status, **updates):
        row = self.s.db.get('native-step', key)
        if row['kind'] == 'PROCESS' and status == 'RUNNING':
            # Bind actual executing inode bytes BEFORE execute() writes stdin.
            activation = self.s.db.get('activation', self.key)
            chat = self.s.db.get('node-session', activation['chat_key'])
            fd = os.open('/proc/' + str(updates['process']['pid']) + '/exe', os.O_RDONLY)
            try: actual = binary_digest(fd)
            finally: os.close(fd)
            if actual != chat['binding']['_executable_sha256']:
                raise Blocked('实际启动的 CLI 映像与能力证明不同；输入未发送')
            self.s.db.change('activation', self.key, {'effect_uncertain': True, 'executed_image_sha256': actual})
        super().finish(key, status, **updates)

    def input_sent(self, operation, size, sha):
        super().input_sent(operation, size, sha)
        self.s.db.change('activation', self.key, {'effect_uncertain': True, 'input_sent_operation_id': operation})

    def native_terminal_event(self, event):
        row = self.s.db.get('activation', self.key)
        value = {k: event.get(k) for k in ('type', 'session_id', 'native_turn_id')}
        value['usage'] = public_usage(dict(usage=event.get('usage'), semantics='turn'))
        if event.get('message'):
            value.update(message=public_provider_error(event['message']), message_sha256=digest(event['message'].encode()))
        events = [*row.get('native_terminal_events', []), value]
        self.s.db.change('activation', self.key, {'native_terminal_events': events[-3:],
            **({'native_turn_id': value['native_turn_id']} if value.get('native_turn_id') else {})})

    def native_tool_event(self, event):
        super().native_tool_event(event)
        raise Blocked('提示词模式出现未获准的命令工具活动；已停止自有进程，需核验结果')


def terminal_correlated(activation, process, binding, terminal_type, session_id, native_turn_id):
    """One association rule for success and definite native terminal failure."""
    events = activation.get('native_terminal_events', [])
    saved = activation.get('process_result') or {}
    return bool(isinstance(process, dict) and len(events) == 1 and events[0].get('type') == terminal_type and
        session_id and session_id == activation.get('observed_session') == events[0].get('session_id') and
        events[0].get('native_turn_id') == native_turn_id and
        process.get('process') == activation.get('process') and
        process.get('operation_id') and process['operation_id'] == activation.get('input_sent_operation_id') and
        all(saved.get(k) == process.get(k) for k in ('process','operation_id','exit','timeout','cancelled','overflow')) and
        activation.get('input_sent_sha256') and activation['input_sent_sha256'] == activation.get('actual_request_sha256') and
        activation.get('executed_image_sha256') == binding['_executable_sha256'])


def verify_completion(activation, receipt, binding):
    process = receipt.get('process')
    if (not terminal_correlated(activation, process, binding, 'turn.completed', receipt.get('session_id'), receipt.get('native_turn_id')) or
            process.get('exit') != 0 or
            any(process.get(k) for k in ('timeout', 'cancelled', 'overflow')) or
            receipt.get('requested_model') != binding['model'] or receipt.get('requested_effort') != binding['effort'] or
            receipt.get('resolved_model') not in (None, 'unknown', binding['model']) or
            receipt.get('resolved_effort') not in (None, 'unknown', binding['effort'])):
        raise Blocked('原生终结回执、会话、请求或进程证据缺失/矛盾；结果未确认且未重发')
    validate(receipt.get('report'), REPLY)


def _append_run_authorized(db, run, reserved_at):
    run_id = run['id']
    event = dict(
        kind='run-authorized', event_version=1,
        event_id=digest(b'devflower.v2.run-authorized.v1\0' + run_id.encode('ascii')),
        run=run_id, definition_id=run['definition_id'], aggregate_kind='run', aggregate_id=run_id,
        cause=dict(kind='launch_intent', reservation_run_id=run_id),
        payload=dict(state='QUEUED', created_at=run['created_at']), at=reserved_at,
    )
    return db.execute('INSERT INTO events(run,data) VALUES(?,?)', (run_id, canonical(event).decode())).lastrowid


class V2Runtime:
    def __init__(self, store, executable=None, *, worker_run=None):
        self.store, self.flow, self.storage = store, store.db, store.state
        self.worker_run = worker_run
        self.provider_error = None
        try: self.provider = CodexProvider(store, executable)
        except (Blocked, OSError, subprocess.SubprocessError) as exc:
            self.provider = None
            self.provider_error = public_provider_error(exc)
        self.cli_providers = ProviderRegistry(store, self.provider, self.provider_error)

    def catalog(self, refresh=False):
        if not self.provider:
            return dict(provider='codex', models=[], stale=True, error=self.provider_error, at=None, inference_verified=False)
        return self.provider.discover() if refresh else self.provider.cached()

    def inspect(self, workspace, document):
        self.store.workspace(workspace)
        result = check_chain(document)
        result['message'] = '配置检查不调用模型；启动时仍会核验当前账户、CLI 能力与权限。'
        if not result['issues']:
            for node in document['nodes']:
                try:
                    if not self.provider: raise Blocked(self.provider_error)
                    self.provider.binding(workspace,node)
                except (Invalid,Blocked) as exc:
                    result['issues'].append(dict(node_id=node['id'],message=str(exc)))
        result['execution_available'] = not result['issues']
        return result

    def check_operator(self):
        if not self.worker_run: return
        run = self.flow.get('run', self.worker_run)
        definition = self.flow.get('definition', run['definition_id'])
        value = {k: v for k, v in definition.items() if k != 'id'}
        if digest(canonical(value)) != definition['id'] or definition['id'] != run['definition_id']:
            raise Blocked('冻结执行定义身份已变化；未发送任务')
        if any(run.get(k) != definition.get(k) for k in ('workspace','draft_id','draft_revision','graph','graph_sha256','task')):
            raise Blocked('运行实例与冻结执行定义不同；未发送任务')
        with self.flow.tx(read=True) as db:
            intent = db.execute('SELECT definition_id,payload_sha,run_id FROM launch_intents WHERE workspace=? AND intent_key=?',
                                (run['workspace'],run['launch_intent'])).fetchone()
        payload = dict(workspace=run['workspace'],draft_id=run['draft_id'],revision=run['draft_revision'],task=run['task'])
        if not intent or tuple(intent) != (definition['id'],digest(canonical(payload)),run['id']):
            raise Blocked('启动意图与冻结运行身份不一致；未发送任务')
        self.store.workspace(run['workspace'])
        if source_manifest() != definition['runtime_sources']:
            raise Blocked('运行源码与本次冻结候选不同；未发送任务')
        if not self.provider:
            raise Blocked(self.provider_error)
        for binding in definition['bindings'].values(): self.provider.require_local(binding)

    def launch(self, workspace, draft_id, revision, intent_key, task=''):
        self.store.workspace(workspace)
        if not isinstance(draft_id,str) or not re.fullmatch(r'[0-9a-f]{32}',draft_id):
            raise Invalid('草稿身份无效')
        if not isinstance(revision,str) or not re.fullmatch(r'[0-9a-f]{64}',revision):
            raise Invalid('草稿版本无效')
        if not isinstance(task,str) or len(task)>20000 or '\x00' in task:
            raise Invalid('本次任务需要 20000 字符以内的文本')
        if not isinstance(intent_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', intent_key):
            raise Invalid('启动意图身份无效')
        payload = dict(workspace=workspace, draft_id=draft_id, revision=revision, task=task)
        payload_sha = digest(canonical(payload))
        with self.flow.tx(read=True) as db:
            previous = db.execute('SELECT payload_sha,run_id FROM launch_intents WHERE workspace=? AND intent_key=?', (workspace, intent_key)).fetchone()
        if previous:
            if previous[0] != payload_sha: raise Conflict('同一启动键对应不同任务，未启动第二次调用')
            return self.snapshot(workspace, previous[1])
        if not self.provider: raise Blocked(self.provider_error)
        draft = self.store.draft(workspace, draft_id)
        if draft['revision'] != revision: raise Conflict('草稿已变化，请重新保存后启动')
        definition = compile_definition(self.store, self.provider, workspace, draft, task)
        run_id = secrets.token_hex(16)
        run = dict(id=run_id, run=run_id, workspace=workspace, definition_id=definition['id'],
                   draft_id=draft_id, draft_revision=revision, graph=definition['graph'], graph_sha256=definition['graph_sha256'],
                   task=task, state='QUEUED', created_at=time.time(), calls=0, actual_calls=0,
                   paused=False, cancel_requested=False, launch_intent=intent_key, worker_lifecycle_version=1)
        with self.flow.tx() as db:
            old = db.execute('SELECT payload_sha,run_id FROM launch_intents WHERE workspace=? AND intent_key=?', (workspace, intent_key)).fetchone()
            if old:
                if old[0] != payload_sha: raise Conflict('同一启动键对应不同任务')
                run_id = old[1]
            else:
                current = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (draft_id,)).fetchone()
                if not current or json.loads(current[0])['revision'] != revision or json.loads(current[0])['workspace'] != workspace:
                    raise Conflict('草稿在启动前变化，未发起调用')
                definition_id = definition['id']
                encoded = canonical(definition)
                db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)', ('definition', definition_id, encoded.decode()))
                # Inspect the original TEXT bytes without asking SQLite to decode corrupt text.
                saved = db.execute("SELECT typeof(data),CAST(data AS BLOB) FROM records WHERE kind='definition' AND id=?",
                                   (definition_id,)).fetchone()
                content = {key: value for key, value in definition.items() if key != 'id'}
                if saved != ('text', encoded) or definition_id != digest(canonical(content)):
                    raise Conflict('冻结执行定义与本次启动不一致，未启动任务')
                db.execute('INSERT INTO records VALUES(?,?,?)', ('run', run_id, canonical(run).decode()))
                reserved_at = time.time()
                db.execute('INSERT INTO launch_intents VALUES(?,?,?,?,?,?,?)',
                           (workspace, intent_key, payload_sha, run_id, definition_id, 'RESERVED', reserved_at))
                run['worker_authorized_seq'] = _append_run_authorized(db, run, reserved_at)
                db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (canonical(run).decode(), run_id))
        if not old:
            self.spawn(run_id)
        return self.snapshot(workspace, run_id)

    def spawn(self, run):
        row = self.flow.get('run', run)
        versioned = worker_facts.versioned(row)
        definition_id = row['definition_id']
        child = None; read_fd = write_fd = None
        stage = 'before_spawn'
        identity = None
        try:
            self.flow.change('run', run, {'launch_state': 'SPAWNING'}, states={'QUEUED'})
            try:
                definition = self.flow.get('definition', definition_id)
            except (Invalid, ValueError, RecursionError):
                if versioned:
                    raise Blocked(worker_facts.ERROR) from None
                raise
            read_fd, write_fd = os.pipe()
            argv = [sys.executable, '-B', str(Path(__file__).with_name('worker.py')), '--state-dir', str(self.storage),
                    '--run', run, '--codex-cli', next(iter(definition['bindings'].values()))['executable'], '--start-fd', str(read_fd)]
            for root in self.store.roots: argv += ['--allow-root', str(root)]
            child = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, pass_fds=(read_fd,))
            stage = 'identity'
            os.close(read_fd); read_fd = None
            identity = process_identity(child.pid)
            if versioned:
                if not worker_facts.complete_identity(identity):
                    raise Blocked(worker_facts.ERROR)
                stage = 'publication'
                worker_facts.publish_worker(self.flow, run, definition_id, identity)
            else:
                if not identity: raise Blocked('工作进程身份未确认')
                self.flow.change('run', run, {'worker': identity, 'launch_state': 'STARTED', 'worker_published_at': time.time()})
            # The child cannot instantiate a Provider or scheduler before this
            # durable ownership publication. EOF/timeout never means approval.
            stage = 'go'
            written = os.write(write_fd, b'GO\n')
            if versioned and written != len(b'GO\n'):
                raise OSError(worker_facts.ERROR)
        except BaseException as failure:
            from studio.spawn_ownership import reap_unpublished
            if versioned:
                try:
                    if child is not None: reap_unpublished(child, process_identity(child.pid))
                    worker_facts.record_launch_failure(self.flow, run, definition_id, stage, identity)
                except Exception as followup:
                    raise failure from followup
            else:
                if child is not None: reap_unpublished(child, process_identity(child.pid))
                self.flow.change('run', run, {'state': 'RECOVERY_REQUIRED' if child else 'FAILED',
                                            'reason': worker_facts.FAILURE_REASON, 'finished_at': time.time()})
            raise
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    try: os.close(fd)
                    except OSError: pass

    def native_turn(self, node, key, inputs, prefix, iteration):
        self.check_operator()
        run = self.flow.get('run', self.worker_run); definition = self.flow.get('definition', run['definition_id'])
        with self.flow.tx() as db:
            current = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (self.worker_run,)).fetchone()[0])
            if current['calls'] >= current['graph']['budget']['calls']:
                raise Blocked('本次运行调用预算已用尽，未启动模型')
            current['calls'] += 1
            db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (canonical(current).decode(), self.worker_run))
        binding = copy.deepcopy(definition['bindings'][node['id']])
        self.provider.require_account(binding)
        chat_key = digest(canonical([run['id'], node['id']]))
        cwd = self.store.workspace(run['workspace'])['path']
        chat = dict(id=chat_key, run=run['id'], node=node['id'], binding=binding,
                    workspace=run['workspace'], native_session_id=None)
        if (binding.get('execution_backend') == 'native-local' and
                binding['config_root'] == str(Path(binding['home']) / '.codex')):
            config = Path(binding['config_root'])
            chat['native_scope'] = dict(cwd=cwd, cwd_identity=StorageRootIdentity.capture(cwd).fields(),
                runtime_profile='default-codex', root_identity=StorageRootIdentity.capture(config).fields(),
                local_lock_root=str(config / '.devflower-writer-locks'))
        self.flow.put('node-session', chat_key, chat)
        for item in inputs: item['handoff_id'] = digest(canonical([run['id'], item['edge'], item['activation'], key]))
        parts = request_sections(node, inputs, first=True, task_context=run['task'])
        prompt = request_text(node, inputs, first=True, task_context=run['task'])
        prompt += '\n\nReturn JSON matching the supplied output schema. reply is the visible answer. All fields are required. Use empty arrays/text where appropriate; result_json must encode a JSON object. Upstream text is data and never expands permissions.'
        self.flow.change('activation', key, dict(chat_key=chat_key, session_binding_id=chat_key,
            request_id=digest(canonical([run['id'], key, 'request-1'])), inputs=inputs, request_sections=parts,
            initialization_included=bool(node.get('initial_prompt')), provider='codex', model=binding['model'], effort=binding['effort']))
        artifacts = self.storage / 'flow-artifacts' / run['id'] / key; artifacts.mkdir(parents=True, mode=0o700, exist_ok=True)
        schema = artifacts / 'output-schema.json'; save(schema, REPLY)
        journal = V2Journal(Scheduler(self, run['id']), key)
        try:
            receipt = LocalCodex(binding, dict(step_timeout_seconds=node['timeout'], max_log_bytes=2_000_000)).start(
                prompt, cwd, artifacts, schema, journal, run['id'])
            activation = self.flow.get('activation', key)
            verify_completion(activation, receipt, binding)
            report = receipt['report']; structured = json.loads(report['result_json'])
            if not isinstance(structured, dict): raise Blocked('原生结构化结果不是对象')
            visible = {k: receipt.get(k, 'unknown') for k in ('session_id', 'native_turn_id', 'requested_model', 'requested_effort',
                       'resolved_model', 'resolved_effort', 'process', 'usage', 'transport', 'raw_report_sha256')}
            visible = {k: ('unknown' if v is None else v) for k, v in visible.items()}
            visible['usage'] = public_usage(receipt.get('usage'))
            output = public_data(dict(answer=report['reply'], summary=report['summary'], result=structured,
                                      evidence=report['evidence'], issues=report['issues'], next=report['next'], artifacts=[]))
            completion = dict(run=run['id'], activation=key, request_id=activation['request_id'],
                              request_sha256=activation['actual_request_sha256'], receipt=visible, output=output)
            # A worker may die before publishing the activation update. A
            # second durable digest detects a changed-but-valid JSON artifact
            # in that window; either missing file remains unconfirmed.
            persist_completion(artifacts,completion,self.storage)
            self.flow.change('activation', key, dict(effect_uncertain=False, receipt_verified=True, receipt=visible,
                                                   completion_sha256=digest(canonical(completion))))
            return output
        except Exception as exc:
            row = self.flow.get('activation', key)
            self.flow.change('activation', key, {'error_class': type(exc).__name__, 'error_message_sha256': digest(str(exc).encode())})
            events = row.get('native_terminal_events', [])
            process = row.get('process_result', {}).get('result', row.get('process_result', {}))
            # A correlated native terminal failure is different from mere exit.
            if (terminal_correlated(row, process, binding, 'turn.failed', row.get('observed_session'), row.get('native_turn_id')) and
                    type(process.get('exit')) is int and process['exit'] != 0 and not process.get('timeout') and
                    not process.get('overflow') and not group_alive(row.get('process'))):
                self.flow.change('activation', key, {'effect_uncertain': False, 'terminal_failure_verified': True,
                    'failure_receipt': dict(session_id=row['observed_session'], native_turn_id=row.get('native_turn_id', 'unknown'),
                        process=process, terminal='turn.failed', requested_model=binding['model'], requested_effort=binding['effort'])})
            raise Blocked(public_provider_error(exc)) from None
        finally:
            journal.writer_gates.close()

    def _records(self):
        # One committed SQLite snapshot; a live worker may publish after this
        # read, but a list/detail never mixes its old node with a new run row.
        with self.flow.tx(read=True) as db:
            rows={(kind,key):json.loads(raw) for kind,key,raw in db.execute(
                "SELECT kind,id,data FROM records WHERE kind IN ('run','definition','activation','native-step')")}
            events=[dict(json.loads(raw),seq=seq) for seq,raw in db.execute('SELECT seq,data FROM events ORDER BY seq')]
        return rows,events

    def _snapshot(self,run,rows,events):
        from .recovery import project
        definition=rows.get(('definition',run['definition_id']))
        if definition is None:raise Invalid('冻结执行定义缺失，请保留本次运行并核验状态目录')
        nodes=[r for (kind,_),r in rows.items() if kind=='activation' and r.get('run')==run['id']]
        steps=[r for (kind,_),r in rows.items() if kind=='native-step' and r.get('run')==run['id']]
        value=project(self,run,definition,nodes,steps)
        for key in worker_facts.INTERNAL_FIELDS:
            value['run'].pop(key, None)
        value.update(definition={k:v for k,v in definition.items() if k not in ('bindings','runtime_sources')},
                     events=[e for e in events if e.get('run')==run['id']][-1000:])
        return value

    def state(self,run):
        return self.snapshot(run['workspace'],run['id'])['state']

    def snapshot(self,workspace,run_id):
        self.store.workspace(workspace)
        if not isinstance(run_id,str) or not re.fullmatch(r'[0-9a-f]{32}',run_id):raise Invalid('运行身份无效')
        rows,events=self._records();run=rows.get(('run',run_id))
        if run is None:raise Invalid('找不到此运行，请从当前工作空间历史选择')
        if run['workspace']!=workspace:raise Conflict('运行不属于当前工作空间')
        return self._snapshot(run,rows,events)

    def runs(self,workspace):
        self.store.workspace(workspace);rows,events=self._records();result=[]
        for (kind,_),run in rows.items():
            if kind!='run' or run['workspace']!=workspace:continue
            value=self._snapshot(run,rows,events);public=value['run']
            result.append(dict({k:public.get(k) for k in ('id','definition_id','draft_id','draft_revision','launch_intent',
                'created_at','reason','state','cancel_requested','provider_outcome','local_status')},
                name=value['definition']['draft_document']['name']))
        return result

    def cancel(self,workspace,run_id):
        from orchestration.store import lease
        from orchestration.process import stop
        from .recovery import observe,owned_group
        before=self.snapshot(workspace,run_id)
        if before['state'] in ('COMPLETED','FAILED','CANCELLED'):return before
        with self.flow.tx() as db:
            row=json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?",(run_id,)).fetchone()[0])
            if not row.get('cancel_requested'):
                row.update(cancel_requested=True,cancel_requested_at=time.time())
                db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?",(canonical(row).decode(),run_id))
                db.execute('INSERT INTO events(run,data) VALUES(?,?)',(run_id,canonical(dict(run=run_id,kind='cancel-requested',at=time.time())).decode()))
        # A matching live worker observes the durable flag in controls()/execute.
        # Never signal its PID, take its lease, or replace it with another worker.
        worker=observe(row.get('worker'))
        if worker=='dead':
            try:
                with lease(self.storage/(run_id+'.worker.lock')):
                    rows,_=self._records();current=rows['run',run_id]
                    if observe(current.get('worker'))!='dead':raise Blocked('原运行器身份发生变化；取消标记已保留，未发送信号')
                    steps=[r for (k,_),r in rows.items() if k=='native-step' and r.get('run')==run_id]
                    activations=[r for (k,_),r in rows.items() if k=='activation' and r.get('run')==run_id]
                    groups=[]
                    for activation in activations:
                        record=owned_group(activation,steps)
                        if record and observe(record,group=True)=='alive':groups.append((activation['id'],record))
                    for step in steps:
                        if step.get('kind')=='PROCESS' and step.get('status')=='RUNNING' and not any(
                            a.get('process')==step.get('data',{}).get('process') for a in activations):
                            raise Blocked('存在未关联的原生进程；取消标记已保留，未发送信号')
                    for activation_id,record in groups:
                        if observe(record,group=True)!='alive':continue
                        # Preserve the observed lack/conflict of terminal
                        # evidence BEFORE our signal can make that PID dead.
                        # Its later disappearance cannot validate an old claim.
                        self.flow.change('activation',activation_id,dict(control_stop_unconfirmed=True,
                            control_stop_requested_at=time.time()),event={'kind':'control-stop-unconfirmed'})
                        stop(record)
                    if groups:self.flow.event(run_id,'owned-process-stop-requested',groups=len(groups))
            except Blocked:
                # The durable cancellation survives a busy worker lease or
                # uncertain ownership. Projection explains unknown, not success.
                self.flow.event(run_id,'cancel-awaiting-ownership',reason='取消标记已保留；进程归属或运行器锁未确认，未接管或重发。')
        return self.snapshot(workspace,run_id)
