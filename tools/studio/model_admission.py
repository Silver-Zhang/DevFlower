"""Explicit, bounded native execution checks. Discovery and permission are separate.

Certificates prove a recent native request completed for an exact configuration;
they do not prove a gateway's physical backend or grant any additional permission.
"""
from pathlib import Path
import json
import secrets
import time

from orchestration.adapters import CLI
from orchestration.common import Blocked, canonical, clean_env, digest, save
from orchestration.store import lease
from .graph import Invalid
from .agent_sessions import safe_text

TTL = 300


class ModelAdmission:
    def __init__(self, runtime):
        self.rt, self.db = runtime, runtime.db

    def scope(self, binding, cwd):
        # operator_sha pins executable, credential/settings bytes and dependencies.
        # Never retain credential values in a public scope or certificate.
        return dict(version=1, candidate=self.rt.candidate_sha256,
            operator=self.rt.operator_sha256, policy=binding.get('admission_policy'),
            environment_sha256=digest(canonical(clean_env())),
            profile=binding.get('runtime_profile'), cwd=str(cwd),
            config={k: binding.get(k) for k in ('provider', 'model', 'effort',
                'execution_mode', 'role', 'tools', 'timeout_seconds', 'executable',
                'read_paths', 'readonly_files', 'isolation', 'external_sandbox', 'buffered_tool_transport')})

    def key(self, binding, cwd):
        return digest(canonical(self.scope(binding, cwd)))

    def status(self, binding, cwd):
        key = self.key(binding, cwd)
        try: value = self.db.get('model-admission', key)
        except Invalid: return dict(execution_allowed=False, status='unverified', key=key)
        now = time.time()
        valid = (value.get('state') == 'VERIFIED' and
                 0 <= now-value.get('verified_at', 0) < TTL and
                 value.get('scope') == self.scope(binding, cwd))
        return dict(execution_allowed=valid, status='verified' if valid else
                    'expired' if value.get('state') == 'VERIFIED' else
                    'failed' if value.get('state') == 'FAILED' else 'unknown',
                    key=key, verification_id=value.get('id'), verified_at=value.get('verified_at'),
                    expires_at=value.get('verified_at', 0)+TTL,
                    physical_backend='unknown', resolved_effort='unknown')

    def require(self, binding, cwd):
        self.rt.check_operator()
        if binding.get('admission_policy_file'):
            from .session_sources import opened
            import os
            with opened(binding['admission_policy_file']) as fd:
                if digest(os.read(fd, 200001)) != binding['admission_policy']:
                    raise Blocked('工作台执行策略已变化，请重新验证')
        from .workbench_store import WorkbenchStore
        flow = getattr(self.rt, 'flow', None) or WorkbenchStore(self.rt.storage/'workbench.sqlite')
        try: auth = flow.get('auth-probe', binding.get('runtime_profile'))
        except Invalid: auth = None
        if auth and 0 <= time.time()-auth.get('at',0) < TTL and auth.get('logged_in') is False:
            raise Blocked('当前原生 Profile 已报告认证失效，请重新登录并验证')
        status=self.status(binding, cwd)
        if not status['execution_allowed']:
            raise Blocked('此模型/强度/工作方式和 Profile 尚无有效原生执行验证；请点击“验证执行”并确认。登记和目录发现不等于可执行，配置变化或验证过期需重验')
        return status

    def _publish(self, event, expected_id=None, expected_state='VERIFIED', expected_record=None):
        # Certificate and check history commit together. A late successful call
        # cannot overwrite a revocation or a newer independently verified check.
        with self.db.lock, self.db.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            if expected_id is not None or expected_record is not None:
                row=db.execute("SELECT data FROM objects WHERE kind='model-admission' AND id=?",(event['key'],)).fetchone()
                previous=json.loads(row[0]) if row else {}
                if expected_record is not None and previous != expected_record:return False
                if expected_id is not None and (previous.get('id')!=expected_id or previous.get('state')!=expected_state):return False
            for kind,key in [('model-admission-event',event['id']),('model-admission',event['key'])]:
                db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?)',(kind,key,canonical(event).decode()))
        return True

    def renew(self, binding, cwd, receipt, verification):
        process=receipt.get('process',{})
        if (not verification.get('verification_id') or process.get('exit')!=0 or
            any(process.get(k,True) for k in ('timeout','overflow','cancelled')) or
            receipt.get('requested_model')!=binding['model'] or receipt.get('requested_effort')!=binding['effort'] or
            not receipt.get('session_id')):return False
        self.rt.check_operator()
        event=dict(id=secrets.token_hex(16),key=self.key(binding,cwd),state='VERIFIED',
            at=time.time(),verified_at=time.time(),scope=self.scope(binding,cwd),home=binding['home'],cwd=str(cwd),
            renewed_from=verification['verification_id'],evidence_type='successful-native-turn',
            receipt={k:receipt.get(k) for k in ('provider','session_id','requested_model','requested_effort',
                'resolved_model','resolved_effort','process','permission_mode','raw_report_sha256')})
        return self._publish(event,expected_id=verification['verification_id'])

    def invalidate(self, binding, cwd, reason='native-call-failed'):
        key = self.key(binding, cwd)
        # Append failure history; leave the original successful check untouched.
        event = dict(id=secrets.token_hex(16), key=key, state='FAILED', at=time.time(),
                     scope=self.scope(binding, cwd), reason=reason)
        self._publish(event)

    def prepare(self, request):
        self.rt.check_operator()
        self.validate_request(request)
        selected = self.rt.node_binding(request['workspace'], dict(request, session='new'), admission=False)
        return self.db.prepare(dict(purpose='model-admission', **selected,
            candidate_sha256=self.rt.candidate_sha256, operator_sha256=self.rt.operator_sha256,
            policy_sha256=self.rt.policy_sha, scope=self.scope(selected['binding'], selected['cwd']),
            human_questions=['确认使用此外部模型完成一次固定只读验证；不发送任务或源码，不授予交付权限。验证会产生单次原生调用（可能计费）和独立诊断会话。']))

    @staticmethod
    def validate_request(request):
        allowed = {'workspace', 'profile', 'model', 'effort', 'mode', 'permission', 'tools', 'timeout'}
        if not isinstance(request, dict) or set(request)-allowed or not {'workspace','profile','model','effort','mode'} <= set(request):
            raise Invalid('执行验证需要工作区、Profile、模型、强度及工作方式')
        if any(not isinstance(request[k],str) or not request[k].strip() or request[k]=='unknown' for k in ('workspace','profile','model','effort','mode')):
            raise Invalid('请选择明确的模型与推理强度；unknown 不能作为执行验证配置')
        if request.get('permission','read') not in ('read','workspace-write'):
            raise Invalid('执行验证权限只能为只读或工作区写入')
        if 'timeout' in request and (type(request['timeout']) is not int or not 10 <= request['timeout'] <= 3600):
            raise Invalid('执行超时必须在10–3600秒内')

    def selection_status(self, request):
        self.validate_request(request)
        selected = self.rt.node_binding(request['workspace'], dict(request, session='new'), admission=False)
        self.rt.check_operator()
        return self.status(selected['binding'], selected['cwd'])

    def pending_check(self, selected, *, include_valid=False):
        """Expose an exact public selection for one confirmed composite action.

        Preparing never invokes a model and never grants admission. Existing
        launch/worker gates still require the real successful native check.
        """
        binding = selected['binding']
        status=self.status(binding, selected['cwd'])
        if status['execution_allowed'] and not include_valid:
            return None
        request = dict(workspace=selected['workspace'], profile=binding['runtime_profile'],
            model=binding['model'], effort=binding['effort'],
            mode=binding['execution_mode'], timeout=binding['timeout_seconds'],
            permission='workspace-write' if binding['role']=='implementer' else 'read')
        if binding['provider'] != 'codex':
            request['tools'] = binding['tools']
        return dict(key=self.key(binding, selected['cwd']), selection=request,
                    verification_required=not status['execution_allowed'],
                    agent='Codex' if binding['provider']=='codex' else 'Claude Code')

    def ensure_for_node(self, selected, run, activation):
        """Refresh only an expired successful check in an already authorized run.

        Failed/revoked/missing evidence never gets silently re-armed. An uncertain
        diagnostic is never replayed; extra checks consume the same run budget.
        """
        binding,cwd=selected['binding'],selected['cwd']
        status=self.status(binding,cwd)
        if status['execution_allowed']:return self.require(binding,cwd)
        row=self.rt.flow.get('run',run)
        if not row.get('automatic_admission') or status['status']!='expired':return self.require(binding,cwd)
        with lease(self.rt.storage/('model-admission-'+self.key(binding,cwd)+'.lock')):
            status=self.status(binding,cwd)
            if status['execution_allowed']:return self.require(binding,cwd)
            if status['status']!='expired':return self.require(binding,cwd)
            if not isinstance(status.get('verification_id'),str):return self.require(binding,cwd)
            key=digest(canonical([run,activation,status['key']]))
            with self.rt.flow.tx() as db:
                row=json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?",(run,)).fetchone()[0])
                if row.get('cancel_requested'):raise Blocked('本次运行已请求停止，未发起连接检查')
                if db.execute("SELECT 1 FROM records WHERE kind='connection-check' AND id=?",(key,)).fetchone():
                    raise Blocked('本节点已有连接检查记录，结果需核验；不会重复调用')
                if row.get('calls',0)+row.get('connection_checks',0)>=row['graph']['budget'].get('calls',32):
                    raise Blocked('剩余预算不足以重新检查连接；未启动 Agent')
                row['connection_checks']=row.get('connection_checks',0)+1
                db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?",(canonical(row).decode(),run))
                db.execute('INSERT INTO records VALUES(?,?,?)',('connection-check',key,canonical(dict(id=key,run=run,activation=activation,state='STARTING')).decode()))
            self.rt.flow.change('activation',activation,{'connection_check':True},event={'kind':'node-connection-check'})
            try:
                value=dict(binding=binding,cwd=cwd,workspace=selected['workspace'],scope=self.scope(binding,cwd))
                def cancelled():
                    current=self.rt.flow.get('run',run)
                    return current.get('cancel_requested',False) or time.time()-current.get('started_at',current.get('created_at',time.time()))>current['graph']['budget'].get('seconds',3600)
                result=self._verify(value,row['authorization'],expected_start_id=status['verification_id'],
                    cancelled=cancelled)
                self.rt.flow.change('connection-check',key,{'state':'COMPLETED','verification_id':result['verification_id']})
                return self.require(binding,cwd)
            except BaseException:
                self.rt.flow.change('connection-check',key,{'state':'FAILED'})
                raise
            finally:self.rt.flow.change('activation',activation,{'connection_check':False})

    def verify(self, permit, expected, answer, *, reuse_verified=False):
        self.rt.check_operator()
        if type(reuse_verified) is not bool: raise Invalid('复用连接检查标记必须为布尔值')
        value, authorization = self.db.consume(permit, expected, [answer])
        if (value.get('purpose') != 'model-admission' or
            value['candidate_sha256'] != self.rt.candidate_sha256 or
            value['operator_sha256'] != self.rt.operator_sha256 or
            value['policy_sha256'] != self.rt.policy_sha or
            value['scope'] != self.scope(value['binding'], value['cwd'])):
            raise Blocked('执行验证授权或配置身份不一致')
        binding, cwd = value['binding'], value['cwd']
        with lease(self.rt.storage/('model-admission-'+self.key(binding, cwd)+'.lock')):
            try: previous=self.db.get('model-admission', self.key(binding,cwd))
            except Invalid: previous={}
            # Concurrent, explicitly authorized actions may share one successful
            # check; never make an extra model call after waiting for that lease.
            if reuse_verified:
                if self.status(binding, cwd)['execution_allowed']:
                    return self.require(binding, cwd)
                if previous and previous.get('state') != 'VERIFIED':
                    raise Blocked('连接检查已失败、撤销或结果未确认；自动检查不会重试。请核对当前配置后明确选择重试连接')
            # Both first-time and expired automatic checks compare the exact
            # prior record, including absence. A revoke before STARTING wins.
            # Manual verification always probes, even with a valid certificate.
            return self._verify(value, authorization, expected_start=previous)

    def _verify(self, value, authorization, *, expected_start_id=None, expected_start=None, cancelled=None):
        binding, cwd = value['binding'], value['cwd']
        key = self.key(binding, cwd)
        check_id = secrets.token_hex(16)
        artifacts = self.rt.storage/'model-checks'/check_id
        artifacts.mkdir(parents=True, mode=0o700)
        # Never test in the user's source/session. Same native mode/tool flags,
        # with a stronger outer read-only sandbox and an empty diagnostic cwd.
        probe_cwd = artifacts/'workspace'; probe_cwd.mkdir(mode=0o700)
        probe = dict(binding, home=str(self.rt.storage/'model-check-homes'/check_id),
                     session_id=None, admission_probe=True)
        schema = artifacts/'schema.json'
        save(schema, dict(type='object', additionalProperties=False, required=['reply'],
                          properties={'reply':dict(type='string')}))
        nonce = secrets.token_hex(16)
        event = dict(id=check_id, key=key, state='STARTING', at=time.time(), scope=value['scope'],
            authorization_sha256=digest(canonical(authorization)), home=probe['home'], cwd=str(probe_cwd))
        if expected_start is not None:
            if not self._publish(event,expected_record=expected_start):
                raise Blocked('连接检查前准入已被撤销或更新；未发起原生调用，请重新核对当前配置')
        elif expected_start_id is not None:
            if not self._publish(event,expected_id=expected_start_id):
                # A revoke/update raced with the refresh. Only an independently
                # valid current result may proceed; never overwrite the revoke.
                return self.require(binding,cwd)
        else:self._publish(event)
        try:
            from .workbench_sessions import WorkbenchSessions
            from .model_catalog import ModelCatalog
            catalog = ModelCatalog(WorkbenchSessions(self.rt)).get(binding['runtime_profile'],
                self.rt.operator['profiles'][binding['runtime_profile']], True)
            if catalog['authentication'].get('logged_in') is False:
                raise Blocked('当前原生 Profile 尚未认证')
            # Native Codex metadata exposes actual supported efforts. Contradictory
            # native metadata is a rejection, never fixed by adding policy values.
            if binding['provider'] == 'codex' and catalog['discovery_complete']:
                found = next((m for m in catalog['models'] if m['id'] == binding['model'] and m['discovered']), None)
                if found and binding['effort'] not in found.get('native_efforts', []):
                    raise Blocked('Codex model/list 不支持此模型的所选推理强度')
            from .session_center import SessionJournal
            # Same durable process journal as a real call, but no business session.
            operation = dict(id=check_id, state='STARTING', binding=probe,
                workspace=value['workspace'], cancel_requested=False)
            self.db.put('session-operation', check_id, dict(operation, diagnostic=True,
                created_at=time.time(), cwd=str(probe_cwd), permission='read'))
            receipt = CLI(binding['provider'], probe, dict(step_timeout_seconds=120, max_log_bytes=2_000_000)).start(
                'Do not use tools or read files. Return the exact nonce in reply: '+nonce,
                probe_cwd, artifacts, schema, SessionJournal(self.db, check_id,cancelled=cancelled), check_id)
            process = receipt.get('process', {})
            if (process.get('exit') != 0 or any(process.get(k) for k in ('timeout','overflow','cancelled')) or
                receipt.get('requested_model') != binding['model'] or
                receipt.get('requested_effort') != binding['effort'] or
                receipt.get('report') != {'reply':nonce} or not receipt.get('session_id')):
                raise Blocked('原生执行验证未返回身份一致的有效回执')
            self.rt.check_operator()
            event.update(state='VERIFIED', verified_at=time.time(), receipt={k:receipt.get(k) for k in
                ('provider','session_id','requested_model','requested_effort','resolved_model',
                 'resolved_effort','process','permission_mode','raw_report_sha256','transport')})
            from .usage import public_usage
            event['receipt']['usage']=public_usage(receipt.get('usage'))
            self.db.put('session-operation', check_id, dict(operation, diagnostic=True, state='COMPLETED',
                created_at=event['at'], cwd=str(probe_cwd), permission='read', receipt=event['receipt']))
            if not self._publish(event, expected_id=check_id, expected_state='STARTING'):
                raise Blocked('执行验证期间准入已被撤销或更新；迟到回执不能覆盖，请重新确认验证')
            return self.status(binding, cwd)
        except (Blocked, Invalid, OSError, ValueError) as exc:
            self.invalidate(binding, cwd)
            event.update(state='FAILED', reason=safe_text(str(exc)))
            self.db.put('model-admission-event', check_id, event)
            try: self.db.update('session-operation', check_id, dict(state='FAILED', reason=event['reason']))
            except Invalid: pass
            raise Blocked('原生执行验证失败：'+event['reason']) from None
