"""Workbench orchestration with optional externally approved Stable identity."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from orchestration.common import Blocked, canonical, clean_env, digest, identity, tree_hash
from orchestration.process import execute, group_alive, stop
from orchestration.store import alive, process_identity, lease
from orchestration.trust import TrustedRelease, overlap
from .agent_sessions import safe_text
from .graph import Invalid
from .server import Conflict
from .session_runtime import SessionRuntime
from .session_sources import opened, clean_path
from .workbench_graph import validate_graph
from .workbench_store import WorkbenchStore
from .workbench_external import validate_profile,validate_receipt

ROOT = Path(__file__).resolve().parents[2]
END = {'COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'}


class WorkbenchRuntime(SessionRuntime):
    operator_metadata_fields = frozenset({'stable'})

    def __init__(self, operator, operator_sha, candidate_sha, policy, policy_sha):
        self.policy_file, self.policy_sha = Path(clean_path(str(policy))), policy_sha
        with opened(self.policy_file) as fd: raw = os.read(fd, 200001)
        if len(raw) > 200000 or digest(raw) != policy_sha: raise Blocked('工作台策略身份不一致')
        self.policy = json.loads(raw)
        if self.policy.get('version') != 1 or set(self.policy) - {'version', 'models', 'external_steps', 'label'}:
            raise Blocked('工作台策略字段不受支持')
        for profile in self.policy.get('external_steps',{}).values():
            validate_profile(profile)
        super().__init__(operator, operator_sha, candidate_sha)
        for profile in self.policy.get('external_steps',{}).values():
            self.protected.extend(Path(p) for p in [*profile.get('dependencies',{}),
                *profile.get('dependency_trees',{}), *profile.get('write_paths',[])])
        self.flow = WorkbenchStore(self.storage / 'workbench.sqlite', scoped_drafts=True)
        self.rpc_home = self.native / 'metadata'

    def operator_protected_paths(self):
        protected = [self.policy_file]
        self.trust = None
        if 'stable' in self.operator:
            binding = self.operator['stable']
            if (not isinstance(binding, dict) or set(binding) != {'record', 'sha256'} or
                    not isinstance(binding['record'], str) or not isinstance(binding['sha256'], str)):
                raise Blocked('Invalid workbench Stable binding')
            # opened refuses symlink ancestors/hardlinks, including after startup.
            with opened(clean_path(binding['record'])):
                pass
            self.trust = TrustedRelease.bound(binding)
            if self.trust.data['version'] != 2:
                raise Blocked('v3 Stable requires version 2 verifiable release provenance')
            if self.trust.data['candidate_sha256'] != self.candidate_sha256:
                raise Blocked('Workbench candidate differs from the approved Stable release')
            for path in [*self.operator['allowed_roots'], self.storage, self.native, *protected]:
                self.trust.permits(path, self.operator_file)
            protected.extend(self.trust.protected_paths)
            # Authority bytes are never native authentication or source mounts.
            mounts = [src for profile in self.operator['profiles'].values() for src, _ in profile['readonly_files']]
            mounts += [p for profile in self.operator['profiles'].values() for p in profile['read_paths']]
            for profile in self.policy.get('external_steps', {}).values():
                mounts.extend([*profile.get('read_paths', []), *profile.get('dependencies', {}), *profile.get('dependency_trees', {})])
            if any(overlap(path, evidence) for path in mounts for evidence in
                    [self.operator_file, self.policy_file, *self.trust.protected_paths]):
                raise Blocked('Stable provenance cannot be repurposed as native credentials or mounts')
            # External adapters have explicit write mounts as well as workspaces.
            for profile in self.policy.get('external_steps',{}).values():
                for path in profile.get('write_paths',[]):
                    self.trust.permits(path, self.operator_file)
                    if any(overlap(path, p) for p in [ROOT, self.operator_file, self.policy_file,
                            self.trust.record, self.storage, self.native,
                            *[src for p in self.operator['profiles'].values() for src, _ in p['readonly_files']]]):
                        raise Blocked('External writer overlaps protected Stable inputs')
        return protected

    def check_operator(self):
        super().check_operator()
        if self.trust:
            with opened(clean_path(str(self.trust.record))):
                pass
            self.trust.verify()
        with opened(self.policy_file) as fd:
            if digest(os.read(fd, 200001)) != self.policy_sha: raise Blocked('工作台策略已变化')
        for profile in self.policy.get('external_steps',{}).values():
            validate_profile(profile)

    def release_identity(self):
        """Public identity only; never expose private approval paths or references."""
        self.check_operator()
        value = dict(channel='Stable' if self.trust else 'Development',
                     candidate_sha256=self.candidate_sha256)
        if self.trust:
            value.update(git_commit=self.trust.data['git_commit'], git_tree=self.trust.data['git_tree'])
        return value

    def folder(self, path=None):
        """Directory names are navigation, not execution authority or file access."""
        roots = self.operator['allowed_roots'];path = clean_path(path) if path else roots[0]
        def private(p):
            return (any(Path(p).is_relative_to(x) for x in [*self.protected,self.storage,self.native,'/proc','/sys','/dev','/etc','/usr','/bin','/sbin','/lib','/lib64','/boot','/root']) or
                    any(part.startswith('.') or part in ('node_modules','__pycache__') for part in Path(p).parts))
        if private(path):raise Blocked('此位置包含系统或私有数据，请选择普通工作目录')
        rows = []
        try:
            with opened(path, directory=True) as fd:
                for name in sorted(os.listdir(fd))[:1000]:
                    if name.startswith('.') or name in ('node_modules', '__pycache__'): continue
                    target = str(Path(path) / name)
                    try:
                        with opened(target, directory=True): pass
                        if not private(target): rows.append({'name': name, 'path': target})
                    except (OSError, ValueError): continue
        except (OSError, ValueError): raise Invalid('没有权限读取这个文件夹，请返回父目录') from None
        parent = str(Path(path).parent)
        locations=[dict(name='主文件夹',path=str(Path.home())),dict(name='此电脑',path='/')]
        locations += [dict(name=Path(p).name,path=p) for p in ('/home','/mnt','/media','/tmp') if Path(p).is_dir()]
        return dict(path=path, name=Path(path).name, parent=parent if path!='/' else None,locations=locations,
                    execution_allowed=any(Path(path).is_relative_to(r) for r in roots),
                    roots=roots, folders=rows, machine=socket.gethostname(), environment='Linux Runtime', release=self.release_identity(),
                    recent=self.db.listing('workspace', limit=8))

    def source_lock(self, cwd):
        try: return Path(identity(cwd)['common_dir']) / 'devflower-writer.lock'
        except Blocked: return self.storage / ('source-' + digest(cwd.encode()) + '.lock')

    def node_binding(self, workspace, node, *, admission=True):
        project = self.session_center.workspace(workspace)
        if not any(Path(project['path']).is_relative_to(x) for x in self.operator['allowed_roots']): raise Blocked('工作区未获执行授权')
        profile = self.operator['profiles'].get(node.get('profile'))
        if not profile or node.get('provider', profile['provider']) != profile['provider']: raise Invalid('请选择已探测的执行工具与配置')
        try:auth=self.flow.get('auth-probe',node['profile'])
        except Invalid:auth=None
        if auth and time.time()-auth.get('at',0)<300 and auth.get('logged_in') is False:
            raise Blocked('原生工具报告尚未认证；请通过本机原生登录后刷新模型')
        try:cli=self.flow.get('cli-probe',node['profile'])
        except Invalid:cli=None
        if cli and (not cli['available'] or not cli.get('machine_output')):
            raise Blocked('该原生CLI尚未通过当前版本接口探测，不能只修改界面字段执行')
        model, effort = node.get('model', profile['model']), node.get('effort', profile['effort'])
        from .model_registration import ModelRegistration
        registry=ModelRegistration(self)
        registration=registry.choices(workspace,node['profile']).get(model)
        known = self.policy.get('models', {}).get(profile['provider'], [])
        if registration:known=[*known,dict(id=model,efforts=[registration['effort']])]
        if not any(v['id'] == model and effort in v.get('efforts', []) for v in known) and (model, effort) != (profile['model'], profile['effort']):
            raise Invalid('模型/强度尚未发现或显式登记，请刷新能力或选择已批准配置')
        handle = node.get('session', 'new');native_sha = None;cwd = project['path']
        if node.get('context_from') and handle!='new':raise Invalid('上下文新建与原生恢复是不同操作，请只选一种')
        if handle != 'new':
            _, row = self.session_center.resolve(workspace, handle)
            binding, reason = self.session_center._binding(row, project)
            if not binding: raise Blocked(reason + '；可选择“按选定上下文新建”')
            from .workbench_sessions import WorkbenchSessions
            if WorkbenchSessions(self).busy(binding,row['native']['session_id']): raise Blocked('原生会话正在被占用或占用状态无法确认')
            if (binding['provider'], binding['model'], binding['effort']) != (profile['provider'], model, effort):
                raise Blocked('恢复必须保持原工具、模型和强度；如需改变，请上下文新建')
            binding = dict(binding, session_id=row['native']['session_id']);native_sha=row['native']['sha256'];cwd=row['native']['cwd']
        else:
            binding = dict(profile, home=str(self.native / secrets.token_hex(16)), session_id=None,
                           isolation='bwrap', external_sandbox=profile['provider'] == 'codex', project=workspace,
                           runtime_profile=node['profile'])
        if node.get('mode','Ask') not in ('Ask','Plan','Agent'):raise Invalid('工作方式请选择 Ask、Plan 或 Agent')
        write = node.get('permission', 'workspace-write' if node.get('mode') == 'Agent' else 'read') == 'workspace-write'
        if write and node.get('mode') != 'Agent': raise Invalid('Ask / Plan 必须保持只读权限')
        if write and cwd != project['path']: raise Blocked('原冻结目录不能授予写权限')
        if profile['provider']=='copilot' and (write or node.get('mode','Ask')=='Agent' or node.get('tools')):
            raise Blocked('Copilot 当前仅支持 Ask/Plan 上下文分析，尚未开放写入或原生工具')
        allowed = [] if profile['provider']=='copilot' else ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit'] if write else ['Read', 'Glob', 'Grep']
        tools = node.get('tools', allowed)
        if not isinstance(tools, list) or any(t not in allowed for t in tools): raise Invalid('所选工具超出工作方式允许的权限')
        if profile['provider']=='codex' and 'tools' in node:
            raise Invalid('Codex当前由原生工具集和强制工作区隔离控制，不能承诺逐工具过滤')
        binding.update(model=model, effort=effort, role='implementer' if write else 'reviewer',
            responsibility=node.get('responsibility') or node.get('role', '通用 Agent'), tools=['native'] if profile['provider'] == 'codex' else tools,
            timeout_seconds=node.get('timeout', 300), execution_mode=node.get('mode','Ask'),
            admission_policy=self.policy_sha, admission_policy_file=str(self.policy_file))
        if registration:
            binding.update(model_registration_key=registration['id'],model_registration_sha256=digest(canonical(registration)))
        if admission:
            from .model_admission import ModelAdmission
            ModelAdmission(self).require(binding, cwd)
        contexts=[]
        if node.get('context_from'):
            limit=node.get('context_limit',10)
            if type(limit) is not int or not 1<=limit<=30:raise Invalid('请选择1–30条可见历史消息')
            messages=self.session_center.history(workspace,node['context_from'],limit=limit)['messages']
            text='\n\n'.join(m['role']+': '+m['text'] for m in messages)
            contexts.append(dict(type='selected-visible-history',source=node['context_from'],message_count=len(messages),
                text=text[:24000],truncated=len(text)>24000,notice='这是用户选择的上下文副本，不是原生恢复；原记录保留'))
        for ref in node.get('context_refs',[]):
            if not isinstance(ref,str) or Path(ref).is_absolute() or '..' in Path(ref).parts:raise Invalid('上下文文件必须位于本工作区')
            with opened(Path(cwd)/ref) as fd:
                raw=os.read(fd,16001)
                if len(raw)>16000:raise Invalid('上下文文件超过16000字节预算：'+ref)
                contexts.append(dict(path=ref,sha256=digest(raw),text=safe_text(raw.decode())))
        return dict(binding=binding, cwd=cwd, handle=handle, native_sha256=native_sha, workspace=workspace,contexts=contexts)

    def recheck_session(self, chat):
        if chat.get('native_path') and chat.get('expected_native_sha256'):
            with opened(chat['native_path']) as fd:
                import hashlib
                current=hashlib.sha256()
                while data:=os.read(fd,1024*1024):current.update(data)
            if current.hexdigest()!=chat['expected_native_sha256']:raise Conflict('原生会话在上个回合后被其他写入者修改；禁止盲目恢复')
        if chat.get('native_sha256') and chat['handle'] != 'new':
            _, row = self.session_center.resolve(chat['workspace'], chat['handle'])
            if row['native']['sha256'] != chat['native_sha256']: raise Conflict('会话在确认后发生变化，未启动新回合')

    def native_snapshot(self,workspace,binding):
        listing=self.session_center.discover(workspace,True)
        for row in listing['discovery']['sessions']:
            if row['session_id']==binding['session_id']:
                internal=self.db.get('session-handle',row['handle'])
                if internal['profile']['home']==binding['home']:
                    return dict(native_path=internal['native']['path'],expected_native_sha256=internal['native']['sha256'])
        raise Blocked('真实原生回执已有 UUID，但记录无法唯一核验；禁止下一轮自动重发')

    def attachments(self, cwd, refs):
        if not isinstance(refs, list) or len(refs) > 50: raise Invalid('产物引用数量超限')
        result = []
        for ref in refs:
            if not isinstance(ref, str): raise Invalid('产物引用必须为路径文本')
            path = Path(ref) if ref.startswith('/') else Path(cwd) / ref
            accessible = path.is_relative_to(cwd) and '..' not in path.parts
            try:
                if accessible:
                    with opened(path) as fd:
                        meta = os.fstat(fd); accessible = meta.st_size < 4_000_000
                        sha = digest(os.read(fd, 4_000_001)) if accessible else None
                else: sha = None
            except (OSError, ValueError): accessible=False;sha=None
            result.append(dict(reference=safe_text(ref), accessible=accessible, sha256=sha,
                               note=None if accessible else '接收方未获该路径访问权限或文件不可读'))
        return result

    def recipient_inputs(self,cwd,inputs):
        """Recheck attachment access at the receiver, never inherit a sender's claim."""
        import copy
        result=copy.deepcopy(inputs)
        def inspect(value):
            if isinstance(value,dict):
                if 'reference' in value and 'accessible' in value:
                    checked=self.attachments(cwd,[value['reference']])[0]
                    if not value.get('accessible') or checked['sha256']!=value.get('sha256'):
                        checked.update(accessible=False,note='附件未获接收方访问权，或发送后内容已变化')
                    value.update(checked)
                for child in value.values():inspect(child)
            elif isinstance(value,list):
                for child in value:inspect(child)
        inspect(result)
        return result

    def prepare_flow(self, request):
        self.check_operator();workspace=request['workspace'];project=self.session_center.workspace(workspace)
        automatic = request.get('auto_verify', False)
        if type(automatic) is not bool: raise Invalid('自动连接检查标记必须为布尔值')
        graph=validate_graph(request['graph']);task=request.get('task', '')
        if not isinstance(task,str) or not 1 <= len(task.strip()) <= 50000: raise Invalid('请填写本次任务')
        bindings={}
        def inspect(g,prefix=''):
            for n in g['nodes']:
                if n['kind']=='agent' or n['kind']=='router' and n.get('classifier'):
                    from .routing import classifier_node
                    try:bindings[prefix+n['id']]=self.node_binding(workspace,classifier_node(n) if n['kind']=='router' else n,admission=not automatic)
                    except (Invalid,Blocked) as exc:
                        exc.node=prefix+n['id'];raise
                if n['kind']=='loop': inspect(n['body'],prefix+n['id']+'/')
                if n['kind'] in ('verify','delivery') and n.get('profile') not in self.policy.get('external_steps',{}):
                    raise Invalid('请为验证/交付节点选择固定外部执行配置：'+n['name'])
        inspect(graph)
        from .model_admission import ModelAdmission
        checks = {}
        if automatic:
            for selected in bindings.values():
                check = ModelAdmission(self).pending_check(selected,include_valid=True)
                if check: checks[check['key']] = check
        draft_id = request.get('draft_id')
        if draft_id is not None:
            current = self.flow.get('draft', draft_id)
            if current.get('workspace') != workspace:
                raise Conflict('工作流不属于此工作区；未分配的旧工作流请先新建工作区副本')
            if current['graph'] != graph: raise Conflict('运行图和已保存草稿不一致，请先保存')
        return self.db.prepare(dict(purpose='workbench-v3',workspace=workspace,graph=graph,task=task,bindings=bindings,
            draft_id=draft_id,admission_checks=list(checks.values()),automatic_admission=automatic,
            graph_sha256=digest(canonical(graph)),candidate_sha256=self.candidate_sha256,operator_sha256=self.operator_sha256,
            policy_sha256=self.policy_sha,source_sha256=tree_hash(project['path']),
            human_questions=['确认本次目标、外部模型使用、各节点权限与调用预算。'+
                (f' 将检查 {len(checks)} 个连接；已有有效结果会复用，未验证或过期配置至多进行一次只读原生诊断，不发送源码。长运行中已通过的连接如过期，会在节点执行前检查，额外调用计入本次运行预算；失败/撤销不自动重试。' if checks else '')]))

    def launch_flow(self, key, expected, answer):
        self.check_operator()
        try:
            prior=self.flow.get('permit',key)
            if prior.get('sha256')!=expected:raise Conflict('重试请求与原授权身份不一致')
            return self.snapshot(prior['run'])
        except Invalid: pass
        value, receipt=self.db.consume(key,expected,[answer])
        if value.get('purpose')!='workbench-v3' or value['candidate_sha256']!=self.candidate_sha256 or value['policy_sha256']!=self.policy_sha:
            raise Blocked('授权不属于此工作台候选')
        project=self.session_center.workspace(value['workspace'])
        if tree_hash(project['path'])!=value['source_sha256']: raise Conflict('工作区在预览后发生变化，请重新确认')
        from .model_admission import ModelAdmission
        for selected in value['bindings'].values():
            ModelAdmission(self).require(selected['binding'], selected['cwd'])
        run=secrets.token_hex(16);row=dict(value,id=run,run=run,state='QUEUED',calls=0,paused=False,cancel_requested=False,
                                        created_at=time.time(),authorization=receipt)
        with self.flow.tx() as db:
            if value.get('draft_id'):
                current=db.execute("SELECT data FROM records WHERE kind='draft' AND id=?",(value['draft_id'],)).fetchone()
                if (not current or json.loads(current[0])['revision']!=value['graph_sha256'] or
                        json.loads(current[0]).get('workspace')!=value['workspace']):
                    raise Conflict('工作流草稿已删除或更新，请重新选择后运行')
            db.execute('INSERT INTO records VALUES(?,?,?)',('run',run,canonical(row).decode()))
            db.execute('INSERT INTO records VALUES(?,?,?)',('permit',key,canonical({'run':run,'sha256':expected}).decode()))
        self.flow.event(run,'run-authorized',graph_sha256=value['graph_sha256'])
        self.spawn(run);return self.snapshot(run)

    def spawn(self, run):
        self.check_operator()
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('workbench_worker.py')),
            '--operator',str(self.operator_file),'--operator-sha256',self.operator_sha256,'--candidate-sha256',self.candidate_sha256,
            '--policy',str(self.policy_file),'--policy-sha256',self.policy_sha,'--run',run],
            cwd=ROOT,env=clean_env(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
            start_new_session=True,close_fds=True)
        self.flow.change('run',run,{'worker':process_identity(child.pid)})
        self.children[run]=child

    def snapshot(self, run, after=0):
        row=self.flow.get('run',run);nodes=[n for n in self.flow.list('activation') if n['run']==run]
        state=row['state']
        if state not in END and time.time()-row['created_at']>5 and not alive(row.get('worker')):
            state='RECOVERY_REQUIRED'
        return dict(run={k:v for k,v in row.items() if k not in ('authorization',)},state=state,nodes=nodes,
                    events=self.flow.events(run,after),worker_alive=alive(row.get('worker')))

    def control(self, run, action):
        row=self.flow.get('run',run)
        if row['state'] in ('COMPLETED','FAILED','CANCELLED'):
            raise Conflict('已结束的运行只供查看，不能改写或重复启动')
        if action=='pause': self.flow.change('run',run,{'paused':True})
        elif action=='stop':
            self.flow.change('run',run,{'cancel_requested':True})
            if not alive(row.get('worker')):
                for n in self.flow.list('activation'):
                    if n['run']==run and group_alive(n.get('process')): stop(n['process'])
                self.flow.change('run',run,{'state':'CANCELLED'})
        elif action=='resume':
            if row['candidate_sha256']!=self.candidate_sha256 or row['policy_sha256']!=self.policy_sha or row['operator_sha256']!=self.operator_sha256:
                raise Conflict('旧版本运行只能查看；新候选不能复用旧授权恢复')
            if row['state'] in END:
                raise Blocked('本运行已持久化为状态未确认；不能自动重发。请查看原生会话和完成回执，另行授权新运行')
            if not alive(row.get('worker')):
                for n in self.flow.list('activation'):
                    if n['run']!=run or n['state'] not in ('STARTING','RUNNING'): continue
                    if group_alive(n.get('process')): raise Blocked('原进程仍在运行，不能启动第二个写入者')
                    file=self.storage/'flow-artifacts'/run/n['id']/'completion.json'
                    if not file.is_file(): raise Blocked('缺少可靠原生完成回执，状态未确认；禁止重复发送初始化或执行')
                    completion=json.loads(file.read_text())
                    if completion['run']!=run or completion['activation']!=n['id']: raise Blocked('完成回执身份不一致')
                    chat=self.flow.get('node-session',completion['chat_key'])
                    if tree_hash(chat['cwd'])!=completion['source_after']:
                        raise Blocked('源码与最后的可靠完成回执不同，禁止自动重发')
                    self.flow.change('activation',n['id'],{'state':'COMPLETED','output':completion['output']})
                    binding=dict(chat['binding'],session_id=completion['session_id'])
                    self.db.put('chat-profile',digest(binding['home'].encode()),dict(workspace=chat['workspace'],binding=binding))
                    self.flow.change('node-session',chat['id'],{'binding':binding,'initialized':True,'native_sha256':None,
                                     **self.native_snapshot(chat['workspace'],binding)})
                self.spawn(run)
            self.flow.change('run',run,{'paused':False})
        else: raise Invalid('不支持的运行操作')
        self.flow.event(run,'control-'+action);return self.snapshot(run)

    def external_step(self, run, key, node, inputs):
        """Fixed external adapter, never a model-supplied command or a Stable approval."""
        from .workbench_scheduler import source_lease
        row=self.flow.get('run',run);project=self.session_center.workspace(row['workspace'])
        with source_lease(self.source_lock(project['path']),False):
            return self._external_step_locked(run,key,node,inputs)

    def _external_step_locked(self,run,key,node,inputs):
        profile=self.policy.get('external_steps',{}).get(node.get('profile'))
        if not profile or profile.get('kind')!=node['kind']: raise Blocked('缺少固定外部验证/交付适配器')
        validate_profile(profile)
        command=profile['argv'];executable=Path(command[0])
        if digest(executable.read_bytes())!=profile['executable_sha256']: raise Blocked('固定外部执行器已变化')
        row=self.flow.get('run',run);project=self.session_center.workspace(row['workspace']);source=tree_hash(project['path'])
        payload=dict(run=run,activation=key,source=project['path'],candidate_sha256=source,inputs=inputs,
                     graph_sha256=row['graph_sha256'],authorization_sha256=digest(canonical(row['authorization'])),
                     acceptance_sha256=digest(row['task'].encode('utf-8')))
        # The trusted external program owns tests/delivery policy. It receives no browser argv or credentials.
        from .workbench_scheduler import Scheduler,Journal
        journal=Journal(Scheduler(self,run),key)
        artifacts=self.storage/'external-artifacts'/run/key;artifacts.mkdir(parents=True,exist_ok=True,mode=0o700)
        home=self.native/('external-'+key)
        result,out,_=execute(command,project['path'],home,read=[project['path'],*profile.get('read_paths',[]),str(executable)],
            write=[str(artifacts),*profile.get('write_paths',[])],timeout=profile.get('timeout',600),cap=200000,
            store=journal,run=run,input_text=canonical(dict(payload,artifacts=str(artifacts))).decode(),
            backend=profile.get('isolation','landlock'),context={'step_kind':node['kind'],'model_call':False})
        if result['exit'] or result['timeout'] or result['cancelled'] or result['overflow']:raise Blocked('固定外部步骤失败或已停止，未伪称通过')
        receipt=json.loads(out)
        from orchestration.common import save
        save(artifacts/'receipt.json',receipt)
        self.flow.change('activation',key,{'external_receipt':receipt,'external_receipt_sha256':digest(canonical(receipt))})
        if tree_hash(project['path'])!=source:raise Blocked('外部执行期间源码变化')
        validate_receipt(receipt,payload,node['kind'])
        return dict(answer=receipt.get('summary','固定外部步骤完成'),result=receipt,evidence=[receipt])
