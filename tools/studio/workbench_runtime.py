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
from contextvars import ContextVar
from orchestration.common import Blocked, canonical, clean_env, digest, identity, tree_hash, workspace_hash
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
from .native_local import profile_key

ROOT = Path(__file__).resolve().parents[2]
END = {'COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'}
READONLY_VIEW = ContextVar('workbench_readonly_view',default=False)


class WorkbenchRuntime(SessionRuntime):
    operator_metadata_fields = frozenset({'stable','native_local'})

    def __init__(self, operator, operator_sha, candidate_sha, policy, policy_sha, *, assistant_settings=None):
        from .assistant_settings import settings_root
        self.assistant_settings = Path(clean_path(str(assistant_settings or settings_root())))
        self.policy_file, self.policy_sha = Path(clean_path(str(policy))), policy_sha
        with opened(self.policy_file) as fd: raw = os.read(fd, 200001)
        if len(raw) > 200000 or digest(raw) != policy_sha: raise Blocked('工作台策略身份不一致')
        self.policy = json.loads(raw)
        if self.policy.get('version') != 1 or set(self.policy) - {'version', 'models', 'external_steps', 'label'}:
            raise Blocked('工作台策略字段不受支持')
        for profile in self.policy.get('external_steps',{}).values():
            validate_profile(profile)
        from .preference_baseline import original
        with opened(clean_path(str(operator))) as fd: operator_raw=os.read(fd,131073)
        if digest(operator_raw)!=operator_sha:raise Blocked('Development operator binding changed')
        self.preference_baseline=original(json.loads(operator_raw))
        super().__init__(operator, operator_sha, candidate_sha)
        from .native_local import configured
        local=self.operator.get('native_local',{})
        if not isinstance(local,dict) or set(local)-{'codex','claude'}:raise Blocked('不支持的本机原生 Provider 配置')
        for provider in local:configured(self,provider)
        for profile in self.policy.get('external_steps',{}).values():
            self.protected.extend(Path(p) for p in [*profile.get('dependencies',{}),
                *profile.get('dependency_trees',{}), *profile.get('write_paths',[])])
        self.flow = WorkbenchStore(self.storage / 'workbench.sqlite', scoped_drafts=True)
        self.rpc_home = self.native / 'metadata'

    def operator_protected_paths(self):
        protected = [self.policy_file, self.assistant_settings]
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

    def dependency_accepted(self, path, sha):
        actual=self.identity_cache.file(path)
        if actual==sha:return True
        # A Codex auth overlay is a mutable credential input, not a permission
        # policy. Still refuse drift if the same path has any executable,
        # readable-code or external-adapter role. No operator hash is changed.
        profiles=list(self.operator['profiles'].values())
        mutable=any(p['provider']=='codex' and any(src==path and dest=='.codex/auth.json' for src,dest in p['readonly_files']) for p in profiles)
        fixed=any(path==p['executable'] or path in p['read_paths'] or any(src==path and not (p['provider']=='codex' and dest=='.codex/auth.json') for src,dest in p['readonly_files']) for p in profiles)
        fixed=fixed or any(path in v.get('dependencies',{}) or path in v.get('dependency_trees',{}) or path in v.get('read_paths',[]) or path in v.get('write_paths',[]) or path==v.get('argv',[None])[0] for v in self.policy.get('external_steps',{}).values())
        local=self.operator.get('native_local',{}).get('codex',{})
        config=str(Path(local.get('config_root','/'))/'config.toml') if isinstance(local,dict) else None
        hard_fixed=(path in (str(self.operator_file),str(self.policy_file)) or any(path==p['executable'] for p in profiles) or any(path in v.get('dependencies',{}) or path in v.get('dependency_trees',{}) or path in v.get('read_paths',[]) or path in v.get('write_paths',[]) or path==v.get('argv',[None])[0] for v in self.policy.get('external_steps',{}).values()))
        from .preference_baseline import preference_only
        if path==config and not hard_fixed and preference_only(self.preference_baseline,path):return True
        if READONLY_VIEW.get() and path==config and not hard_fixed:
            return True
        return mutable and not fixed

    def readonly_configuration_warning(self):
        local=self.operator.get('native_local',{}).get('codex',{})
        if not isinstance(local,dict) or not local.get('config_root'):return None
        path=str(Path(local['config_root'])/'config.toml');expected=self.operator['dependencies'].get(path)
        from .preference_baseline import preference_only
        if expected and self.identity_cache.file(path)!=expected and not preference_only(self.preference_baseline,path):
            return '本机 Codex 配置已变化：新任务启动已阻止；已打开的原生终端仍可继续操作或关闭。请结束现有操作后重启工作台入口以加载当前配置；历史记录保留。'
        return None

    def release_identity(self):
        """Public identity only; never expose private approval paths or references."""
        self.check_operator()
        value = dict(channel='Stable' if self.trust else 'Development',
                     candidate_sha256=self.candidate_sha256)
        if self.trust:
            value.update(git_commit=self.trust.data['git_commit'], git_tree=self.trust.data['git_tree'])
        return value

    def readable_directory(self, path):
        from .workspace_access import browse_directory
        return browse_directory(path)

    def validate_source(self, path, *, ordinary=False):
        from .workspace_access import view_policy, selected_scope, native_workspace_view
        from orchestration.common import workspace_metadata, has_git_metadata
        path = self.readable_directory(path)
        policy=view_policy(self,path)
        partial=(ordinary and not self.trust and policy['contains'] and not policy['inside'] and not policy['system'] and bool(self.operator.get('native_local',{}).get('codex')))
        if policy['protected_container'] and not partial:
            raise Blocked('文件夹已打开，但此位置包含系统或工作台保护数据；运行 Agent 请选择具体的项目代码目录。')
        if not (any(Path(path).is_relative_to(p) for p in self.operator['allowed_roots']) or selected_scope(self,path)):
            raise Blocked('请先通过“打开工作区”选择此项目，再确认本次任务的运行权限。')
        if self.trust: self.trust.permits(path, self.operator_file)
        if partial:return native_workspace_view(self,path)
        try:
            workspace_metadata(path,check_git=True)
            # General conversations/workflows also work in ordinary folders.
            # Git delivery still requires a separate real repository identity.
            if not has_git_metadata(path): return dict(root=path,head=None,branch=None)
            return identity(path)
        except (OSError,ValueError):
            raise Blocked('文件夹已打开；项目内记录保留区需要检查，暂不允许 Agent 执行。') from None

    def hosted_project_records(self, row, reason):
        base = self.storage/'project-records'
        base.mkdir(mode=0o700,exist_ok=True)
        with opened(base,directory=True): pass
        target = base/row['id'];target.mkdir(mode=0o700,exist_ok=True)
        with opened(target,directory=True): pass
        row.update(records_directory=str(target),record_location='host',record_notice=reason)
        self.db.put('workspace',row['id'],row)

    def project_files(self, workspace):
        from .project_files import ProjectFiles
        return ProjectFiles(self, workspace)

    def open_workspace(self, path):
        from .workspace_access import view_policy, selected_scope
        path=self.readable_directory(path)
        self.check_operator()
        policy=view_policy(self,path)
        if not policy['protected_container'] or (path!='/home' and policy['contains'] and not policy['inside'] and not policy['system'] and not self.trust and self.operator.get('native_local',{}).get('codex')):
            selected_scope(self,path,grant=True)
        try: old=self.db.get('workspace',digest(path.encode())[:32])
        except Invalid: old={}
        row = super().open_workspace(path)
        policy = view_policy(self,row['path'])
        if policy['contains'] and not policy['inside'] and not policy['system'] and not self.trust and self.operator.get('native_local',{}).get('codex'):
            try:
                self.validate_source(path,ordinary=True)
                row.update(execution_allowed=True,execution_reason='仅普通本机 Codex；运行前检查受保护目录。',execution_scope='native-codex-only')
            except Blocked:pass
        row['protected_container'] = policy['protected_container']
        # Reuse an existing hosted view cache; never silently abandon its data.
        if policy['hosted_records'] or old.get('record_location')=='host':
            self.hosted_project_records(row,policy['reason'] or old.get('record_notice'))
        else:
            row.update(record_location='project',records_directory=row['path'],record_notice=None)
            self.db.put('workspace',row['id'],row)
        try: row['project_storage'] = self.project_files(row['id']).initialize()
        except (OSError,ValueError,Blocked,Invalid) as exc:
            if row['record_location']=='host': raise
            self.hosted_project_records(row,'文件夹已打开；原有数据未覆盖。此位置不能使用项目内记录，已改用独立本机记录区。')
            row['project_storage'] = self.project_files(row['id']).initialize()
        row['project_storage'].update(location=row['record_location'],notice=row['record_notice'])
        self.db.put('workspace', row['id'], row)
        # Non-destructive migration of visible hosted conversation records.
        for value in self.flow.list('canvas-conversation'):
            if value.get('workspace') == row['id']: self.project_files(row['id']).assistant(value)
        return row

    def project_drafts(self):
        rows = {r['id']: r for r in self.flow.list('draft')}
        errors = []
        for w in self.db.listing('workspace', limit=None):
            if not w.get('project_storage'): continue
            # Suppress stale host caches even if the project is offline.
            rows = {k: r for k, r in rows.items() if r.get('workspace') != w['id']}
            try:
                with self.project_files(w['id']).library() as store:
                    for r in store.list('draft'): rows[w['id'] + ':' + r['id']] = r
            except (OSError, ValueError, Invalid, Blocked):
                errors.append(dict(workspace=w['id'], error='项目记录不可读，请重新打开工作区；本机旧缓存未冒充最新工作流'))
        return list(rows.values()), errors

    def saved_runs(self):
        from .workbench_failures import recovery_notice
        current = [dict(r, state='RECOVERY_REQUIRED') if recovery_notice(self,r['id']) else r for r in self.flow.list('run')]; known = {(r['workspace'],r['id']) for r in current}
        result = [dict({k: v for k, v in r.items() if k in ('id','workspace','state','created_at','task','draft_id')},workflow_name=r.get('graph',{}).get('name','工作流')) for r in current]
        for w in self.db.listing('workspace', limit=None):
            if not w.get('project_storage'): continue
            try:
                for v in self.project_files(w['id']).archives('run'):
                    if (w['id'],v['key']) in known: continue
                    r = v['data']
                    result.append(dict(id=v['key'], workspace=w['id'], state='UNKNOWN',
                        created_at=r.get('created_at') or 0, task=r.get('task',''),draft_id=r.get('draft_id'),workflow_name=r.get('name','工作流'), saved_history=True))
            except (OSError, ValueError, Invalid, Blocked): pass  # project_drafts exposes the read error.
        return result

    def folder(self, path=None, show_hidden=False):
        """Directory names are navigation, not execution authority or file access."""
        roots = self.operator['allowed_roots'];path = self.readable_directory(path if path else roots[0])
        def private(p):
            return not show_hidden and (Path(p).name.startswith('.') or Path(p).name in ('node_modules','__pycache__'))
        rows = []
        try:
            with opened(path, directory=True) as fd:
                for name in sorted(os.listdir(fd))[:1000]:
                    if not show_hidden and (name.startswith('.') or name in ('node_modules', '__pycache__')): continue
                    target = str(Path(path) / name)
                    try:
                        with opened(target, directory=True): pass
                        if not private(target): rows.append({'name': name, 'path': target})
                    except (OSError, ValueError): continue
        except (OSError, ValueError): raise Invalid('没有权限读取这个文件夹，请返回父目录') from None
        parent = str(Path(path).parent)
        locations=[dict(name='主文件夹',path=str(Path.home())),dict(name='此电脑',path='/')]
        locations += [dict(name=Path(p).name,path=p) for p in ('/home','/mnt','/media','/tmp') if Path(p).is_dir()]
        try:
            self.readable_directory(path); openable = True; reason = None
        except (Blocked, Invalid) as exc: openable = False; reason = str(exc)
        from .workspace_access import view_policy
        policy = view_policy(self,path)
        contains_private = policy['protected_container']
        return dict(path=path, name=Path(path).name, parent=parent if path!='/' else None,locations=locations,show_hidden=show_hidden,
                    openable=openable, open_reason=reason, protected_container=contains_private,
                    record_location='host' if policy['hosted_records'] else 'project',record_notice=policy['reason'],
                    execution_allowed=not contains_private, selection_grants_scope=not contains_private and not bool(self.trust),
                    roots=roots, folders=rows, machine=socket.gethostname(), environment='Linux Runtime', release=self.release_identity(),
                    recent=self.db.listing('workspace', limit=8))

    def source_lock(self, cwd):
        try: return Path(identity(cwd)['common_dir']) / 'devflower-writer.lock'
        except Blocked: return self.storage / ('source-' + digest(cwd.encode()) + '.lock')

    def source_view_hash(self,path,view=None):
        if view:
            from .workspace_access import native_workspace_hash
            if view.get('root')!=path or self.validate_source(path,ordinary=True)!=view:
                raise Blocked('工作区保护范围已变化，请重新准备。')
            return native_workspace_hash(self,view)
        return workspace_hash(path)

    def node_binding(self, workspace, node, *, admission=True, direct=False, direct_environment_version=3, managed_workflow=False):
        if 'shared_conversation' in node and type(node['shared_conversation']) is not bool:
            raise Invalid('共享原生对话选项必须是布尔值')
        project = self.session_center.workspace(workspace)
        profile = self.operator['profiles'].get(node.get('profile'))
        if not profile or node.get('provider', profile['provider']) != profile['provider']: raise Invalid('请选择已探测的执行工具与配置')
        self.validate_source(project['path'],ordinary=bool(direct and profile['provider']=='codex' and self.operator.get('native_local',{}).get('codex')))
        try:auth=self.flow.get('auth-probe',node['profile'])
        except Invalid:auth=None
        if not direct and auth and time.time()-auth.get('at',0)<300 and auth.get('logged_in') is False:
            raise Blocked('原生工具报告尚未认证；请通过本机原生登录后刷新模型')
        try:cli=self.flow.get('cli-probe',node['profile'])
        except Invalid:cli=None
        if not direct and cli and (not cli['available'] or not cli.get('machine_output')):
            raise Blocked('该原生CLI尚未通过当前版本接口探测，不能只修改界面字段执行')
        model, effort = node.get('model', profile['model']), node.get('effort', profile['effort'])
        from .model_registration import ModelRegistration
        registry=ModelRegistration(self)
        registration=registry.choices(workspace,node['profile']).get(model)
        known = self.policy.get('models', {}).get(profile['provider'], [])
        if registration:known=[*known,dict(id=model,efforts=[registration['effort']])]
        direct_efforts=None
        if direct:
            from .model_catalog import selectable_efforts
            discovered=None
            try: catalog=self.flow.get('model-discovery',node['profile'])
            except Invalid: catalog=None
            if catalog and not catalog.get('error'):
                from .model_registration import profile_identity
                if catalog.get('environment_sha256')==profile_identity(profile):
                    discovered=next((row for row in catalog.get('models',[]) if row.get('id')==model),None)
            registered_ids={entry['id'] for entry in known}|{profile['model']}
            if discovered or model in registered_ids:
                efforts,_=selectable_efforts(self,node['profile'],profile,discovered or {})
                direct_efforts=efforts
                known=[*known,dict(id=model,efforts=efforts)]
        if (direct_efforts is not None and effort not in direct_efforts or direct_efforts is None and
            not any(v['id'] == model and effort in v.get('efforts', []) for v in known) and (model, effort) != (profile['model'], profile['effort'])):
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
            if binding.get('shared_conversation') and node.get('shared_conversation') is False:
                raise Blocked('此原生会话已使用共享纯对话；请保持共享选项和 Ask/Plan，或新建另一条会话')
        else:
            binding = dict(profile, home=str(self.native / secrets.token_hex(16)), session_id=None,
                           isolation='bwrap', external_sandbox=profile['provider'] == 'codex', project=workspace,
                           runtime_profile=node['profile'])
            from .native_local import bind
            native=bind(self,profile,workspace)
            if native:binding=dict(native,runtime_profile=node['profile'])
        if node.get('mode','Ask') not in ('Ask','Plan','Agent'):raise Invalid('工作方式请选择 Ask、Plan 或 Agent')
        write = node.get('permission', 'workspace-write' if node.get('mode') == 'Agent' else 'read') == 'workspace-write'
        if write and node.get('mode') != 'Agent': raise Invalid('Ask / Plan 必须保持只读权限')
        if write and cwd != project['path']: raise Blocked('原冻结目录不能授予写权限')
        if binding.get('execution_backend')=='native-local' and profile['provider']=='claude' and write:
            raise Blocked('本机 Claude 写入边界尚未验证；请选择 Ask/Plan 或保留的旧隔离配置')
        if profile['provider']=='copilot' and (write or node.get('mode','Ask')=='Agent' or node.get('tools')):
            raise Blocked('Copilot 当前仅支持 Ask/Plan 上下文分析，尚未开放写入或原生工具')
        allowed = [] if profile['provider']=='copilot' else ['Read', 'Glob', 'Grep', 'Bash', 'Write', 'Edit'] if write else ['Read', 'Glob', 'Grep']
        tools = node.get('tools', allowed)
        if not isinstance(tools, list) or any(t not in allowed for t in tools): raise Invalid('所选工具超出工作方式允许的权限')
        if profile['provider']=='codex' and 'tools' in node:
            raise Invalid('Codex工具由工作方式的固定权限边界控制，不能从工作流自定义工具清单')
        binding.update(model=model, effort=effort, role='implementer' if write else 'reviewer',
            responsibility=node.get('responsibility') or node.get('role', '通用 Agent'), tools=['native'] if profile['provider'] == 'codex' else tools,
            timeout_seconds=node.get('timeout', profile.get('timeout_seconds', 1800)), execution_mode=node.get('mode','Ask'),
            admission_policy=self.policy_sha, admission_policy_file=str(self.policy_file))
        if not direct and binding.get('execution_backend')=='native-local' and binding['provider']=='codex' and binding.get('session_id'):
            from .native_codex_rpc import metadata
            turns=metadata(dict(binding,_metadata_guard=str(self.storage)),cwd,'thread/read',dict(threadId=binding['session_id'],includeTurns=True))['thread'].get('turns',[])
            if turns:binding['expected_native_turn_id']=turns[-1]['id']
        if registration and not direct:
            binding.update(model_registration_key=registration['id'],model_registration_sha256=digest(canonical(registration)))
        from .agent_extensions import AgentExtensions
        if node.get('subagents','disabled')!='disabled':
            AgentExtensions(self).bind(workspace,node,binding,managed_workflow=bool(direct and managed_workflow))
        else:AgentExtensions(self).bind(workspace,node,binding)
        if node.get('shared_conversation',binding.get('shared_conversation',False)):
            if (not direct or binding.get('execution_backend')!='native-local' or profile['provider']!='codex'
                    or write or node.get('mode','Ask') not in ('Ask','Plan')
                    or binding.get('skills') or binding.get('subagents','disabled')!='disabled'):
                raise Blocked('共享原生对话目前仅支持本机 Codex Ask/Plan，不启用工具、Skill 或子 Agent')
            binding.update(shared_conversation=True,no_tools=True,tools=[])
            if binding.get('session_id'):
                from .native_codex_rpc import metadata
                prior=metadata(dict(binding,_metadata_guard=str(self.storage)),cwd,'thread/read',dict(threadId=binding['session_id'],includeTurns=True))['thread'].get('turns',[])
                binding['expected_native_turn_id']=prior[-1]['id'] if prior else None
        from .codex_narrow_read import uses_narrow_read,TOOLS
        if uses_narrow_read(binding):binding['tools']=[tool['name'] for tool in TOOLS]
        if binding.get('execution_backend')=='native-local' and binding.get('skills') and not direct:
            raise Blocked('本机 Codex Skill 包仅支持普通直接操作；旧受控任务不自动迁移')
        if binding.get('execution_backend')=='native-local' and (binding.get('subagents') not in (None,'disabled') and not (direct and managed_workflow and binding.get('provider')=='codex' and not write) or binding.get('skills') and binding.get('provider')!='codex'):
            raise Blocked('本机模式的定制 Skill 挂载/子Agent边界尚未验证；不会假装旧隔离挂载仍然有效')
        if direct:
            from .model_registration import direct_environment_identity
            # This is a fixed runtime choice, never a field accepted from the
            # graph, browser, or Agent. Legacy terminal permits can request v1.
            binding.pop('direct_environment_version',None);binding.pop('direct_environment_scope',None)
            if type(direct_environment_version) is not int or direct_environment_version not in (1,2,3):
                raise Blocked('不支持的原生环境指纹版本')
            if binding.get('execution_backend')=='native-local' and binding.get('provider')=='codex' and direct_environment_version in (2,3):
                binding.update(direct_environment_version=direct_environment_version,direct_environment_scope='native-codex-ordinary')
            binding['direct_environment_sha256']=direct_environment_identity(binding)
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
            if Path(ref).parts and Path(ref).parts[0]=='.devflower':raise Blocked('项目对话记录不是 Agent 源码或隐式上下文，请只选择授权的项目文件')
            with opened(Path(cwd)/ref) as fd:
                raw=os.read(fd,16001)
                if len(raw)>16000:raise Invalid('上下文文件超过16000字节预算：'+ref)
                contexts.append(dict(path=ref,sha256=digest(raw),text=safe_text(raw.decode())))
        return dict(binding=binding, cwd=cwd, handle=handle, native_sha256=native_sha, workspace=workspace,contexts=contexts)

    def recheck_session(self, chat, *, native_latest=False):
        if native_latest:
            binding=chat['binding']
            if binding.get('execution_backend')!='native-local' or binding.get('provider')!='codex' or not binding.get('session_id'):
                raise Blocked('原生最新历史仅适用于明确的本机 Codex 会话')
            current=self.native_snapshot(chat['workspace'],binding)
            if current.get('native_cwd')!=chat['cwd'] or chat.get('native_path') and current.get('native_path')!=chat['native_path']:
                raise Conflict('原生会话的位置或身份已变化；未启动新回合')
            return
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
                if (internal['profile']['home']==binding['home'] and internal['profile']['provider']==binding['provider'] and
                        (not binding.get('config_root') or internal['profile']['config_root']==binding['config_root'])):
                    if internal.get('ambiguous') or not internal.get('authorized_read') or internal['native'].get('cwd_changed'):
                        raise Blocked('原生会话身份不唯一或工作目录未确认；未启动新回合')
                    value=dict(native_path=internal['native']['path'],native_cwd=internal['native']['cwd'],expected_native_sha256=internal['native']['sha256'])
                    if binding.get('execution_backend')=='native-local' and binding['provider']=='codex':
                        from .native_codex_rpc import metadata
                        turns=metadata(dict(binding,_metadata_guard=str(self.storage)),internal['native']['cwd'],'thread/read',dict(threadId=binding['session_id'],includeTurns=True))['thread'].get('turns',[])
                        if turns:value['expected_native_turn_id']=turns[-1]['id']
                    return value
        raise Blocked('真实原生回执已有 UUID，但记录无法唯一核验；禁止下一轮自动重发')

    def attachments(self, cwd, refs):
        if not isinstance(refs, list) or len(refs) > 50: raise Invalid('产物引用数量超限')
        result = []
        from .workspace_access import view_policy,native_workspace_view
        policy=view_policy(self,cwd)
        view=native_workspace_view(self,cwd) if policy['contains'] and not policy['inside'] and not policy['system'] else None
        for ref in refs:
            if not isinstance(ref, str): raise Invalid('产物引用必须为路径文本')
            path = Path(ref) if ref.startswith('/') else Path(cwd) / ref
            accessible = path.is_relative_to(cwd) and '..' not in path.parts and '.devflower' not in path.relative_to(cwd).parts
            if accessible and view:
                rel=path.relative_to(cwd).as_posix()
                accessible=not any(rel==x or rel.startswith(x+'/') for x in view['excluded'])
            try:
                if accessible:
                    with opened(path) as fd:
                        meta = os.fstat(fd); accessible = meta.st_size < 4_000_000 and (not view or meta.st_nlink==1)
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
        direct = request.get('interaction') == 'click'
        source=self.validate_source(project['path'],ordinary=direct)
        view=source if source.get('kind')=='native-workspace-view-v1' else None
        automatic = request.get('auto_verify', False) if not direct else False
        if type(automatic) is not bool: raise Invalid('自动连接检查标记必须为布尔值')
        graph=validate_graph(request['graph']);task=request.get('task', '')
        if not isinstance(task,str) or not 1 <= len(task.strip()) <= 50000: raise Invalid('请填写本次任务')
        bindings={}
        from .workbench_scheduler import has_delivery
        managed_workflow=direct and not has_delivery(graph)
        def inspect(g,prefix=''):
            for n in g['nodes']:
                if n['kind']=='agent' or n['kind']=='router' and n.get('classifier'):
                    from .routing import classifier_node
                    try:
                        selected=classifier_node(n) if n['kind']=='router' else n
                        extra={'managed_workflow':managed_workflow} if selected.get('subagents','disabled')!='disabled' else {}
                        bindings[prefix+n['id']]=self.node_binding(workspace,selected,admission=not (automatic or direct),direct=direct,**extra)
                    except (Invalid,Blocked) as exc:
                        exc.node=prefix+n['id'];raise
                if n['kind']=='loop': inspect(n['body'],prefix+n['id']+'/')
                if view and n['kind'] in ('verify','delivery'):
                    raise Blocked('父工作区的部分视图不能用于外部验证或交付。')
                if n['kind']=='delivery' and self.operator.get('native_local'):
                    raise Blocked('本机原生模式的自动交付保护尚未独立验收；请由固定外部可信流程完成交付')
                if n['kind'] in ('verify','delivery') and n.get('profile') not in self.policy.get('external_steps',{}):
                    raise Invalid('请为验证/交付节点选择固定外部执行配置：'+n['name'])
        inspect(graph)
        if view and (not bindings or any(x['binding'].get('execution_backend')!='native-local' or x['binding'].get('provider')!='codex' for x in bindings.values())):
            raise Blocked('父工作区仅支持本机 Codex 普通工作流。')
        from .model_admission import ModelAdmission
        checks = {}
        if automatic:
            for selected in bindings.values():
                check = ModelAdmission(self).pending_check(selected,include_valid=True)
                if check: checks[check['key']] = check
        draft_id = request.get('draft_id')
        if draft_id is not None:
            with self.project_files(workspace).library() as designs:
                try: current = designs.get('draft', draft_id)
                except Invalid: current = self.flow.get('draft', draft_id)  # legacy offline clients
            if current.get('workspace') != workspace:
                raise Conflict('工作流不属于此工作区；未分配的旧工作流请先新建工作区副本')
            if current['graph'] != graph: raise Conflict('运行图和已保存草稿不一致，请先保存')
        return self.db.prepare(dict(purpose='workbench-v3',workspace=workspace,graph=graph,task=task,task_sha256=digest(task.encode('utf-8')),bindings=bindings,
            draft_id=draft_id,admission_checks=list(checks.values()),automatic_admission=automatic,
            interaction='click' if direct else 'legacy',click_action='start' if direct else None,
            graph_sha256=digest(canonical(graph)),candidate_sha256=self.candidate_sha256,operator_sha256=self.operator_sha256,
            policy_sha256=self.policy_sha,source_view=view,source_sha256=self.source_view_hash(project['path'],view),
            human_questions=['确认本次目标、外部模型使用、各节点权限与调用预算。'+
                (f' 将检查 {len(checks)} 个连接；已有有效结果会复用，未验证或过期配置至多进行一次只读原生诊断，不发送源码。长运行中已通过的连接如过期，会在节点执行前检查，额外调用计入本次运行预算；失败/撤销不自动重试。' if checks else '')]))

    def launch_flow(self, key, expected, answer):
        with self.lock:
            return self._launch_flow(key, expected, answer)

    def _launch_flow(self, key, expected, answer):
        self.check_operator()
        try:
            prior=self.flow.get('permit',key)
            if prior.get('sha256')!=expected:raise Conflict('重试请求与原授权身份不一致')
            return self.snapshot(prior['run'])
        except Invalid: pass
        value, receipt=(self.db.consume_click(key,expected,'start') if answer == {'action':'start'} else self.db.consume(key,expected,[answer]))
        if value.get('purpose')!='workbench-v3' or value['candidate_sha256']!=self.candidate_sha256 or value['policy_sha256']!=self.policy_sha or value['operator_sha256']!=self.operator_sha256:
            raise Blocked('授权不属于此工作台候选')
        project=self.session_center.workspace(value['workspace'])
        if self.source_view_hash(project['path'],value.get('source_view'))!=value['source_sha256']: raise Conflict('工作区在预览后发生变化，请重新确认')
        from .model_admission import ModelAdmission
        for selected in value['bindings'].values():
            if value.get('interaction') == 'click':
                from .model_registration import ModelRegistration
                ModelRegistration(self).require_direct(selected['binding'])
            else:
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
        child=None;identity=None
        try:
            from .preference_baseline import worker_baseline
            env=clean_env()
            with worker_baseline(self,env) as fds:
                child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('workbench_worker.py')),
                    '--operator',str(self.operator_file),'--operator-sha256',self.operator_sha256,'--candidate-sha256',self.candidate_sha256,
                    '--policy',str(self.policy_file),'--policy-sha256',self.policy_sha,'--run',run],
                    cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,
                    start_new_session=True,close_fds=True,pass_fds=fds)
            self.children[run]=child
            identity=process_identity(child.pid)
            if identity is None:raise Blocked('工作流进程身份未确认')
            self.flow.change('run',run,{'worker':identity})
        except BaseException:
            from .spawn_ownership import reap_unpublished,failure_record
            if child is not None:
                reap_unpublished(child,identity);self.children.pop(run,None)
            failure_record(lambda:self.flow.change('run',run,dict(
                state='RECOVERY_REQUIRED' if child is not None else 'FAILED',
                reason='工作流启动未确认；自有进程已回收，未自动重试')))
            raise

    def snapshot(self, run, after=0):
        row=self.flow.get('run',run);nodes=[n for n in self.flow.list('activation') if n['run']==run]
        from .workbench_failures import recovery_notice
        recovery=recovery_notice(self,run)
        if recovery:
            row=dict(row,state='RECOVERY_REQUIRED',reason=recovery['reason'])
            nodes=[dict(n,state='RECOVERY_REQUIRED',reason=recovery['reason']) if n['state'] not in END and n['state']!='SKIPPED' else n for n in nodes]
        state=row['state']
        if state not in END and time.time()-row['created_at']>5 and not alive(row.get('worker')):
            state='RECOVERY_REQUIRED'
        return dict(run={k:v for k,v in row.items() if k not in ('authorization',)},state=state,nodes=nodes,
                    events=self.flow.events(run,after),worker_alive=alive(row.get('worker')),recovery_pending=bool(recovery))

    def control(self, run, action):
        from .workbench_failures import recovery_notice,reconcile_interrupted
        if action=='reconcile':
            reconcile_interrupted(self,run)
            return self.snapshot(run)
        if recovery_notice(self,run):
            raise Blocked('此运行有未核对的异常记录；先核对并记录未知结局，禁止自动重发或恢复')
        row=self.flow.get('run',run)
        if row['state'] in ('COMPLETED','FAILED','CANCELLED'):
            raise Conflict('已结束的运行只供查看，不能改写或重复启动')
        if action=='pause':
            self.flow.change('run',run,{'paused':True},states={row['state']},event={'kind':'control-pause'})
        elif action=='stop':
            self.flow.change('run',run,{'cancel_requested':True})
            if not alive(row.get('worker')):
                for n in self.flow.list('activation'):
                    if n['run']==run and group_alive(n.get('process')): stop(n['process'])
                self.flow.change('run',run,{'state':'CANCELLED'})
        elif action=='resume':
            if row['candidate_sha256']!=self.candidate_sha256 or row['policy_sha256']!=self.policy_sha or row['operator_sha256']!=self.operator_sha256:
                raise Conflict('旧版本运行只能查看；新候选不能复用旧授权恢复')
            if row.get('source_view'):
                self.source_view_hash(row['source_view']['root'],row['source_view'])
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
                    if self.source_view_hash(chat['cwd'],row.get('source_view'))!=completion['source_after']:
                        raise Blocked('源码与最后的可靠完成回执不同，禁止自动重发')
                    self.flow.change('activation',n['id'],{'state':'COMPLETED','output':completion['output']})
                    binding=dict(chat['binding'],session_id=completion['session_id'])
                    self.db.put('chat-profile',profile_key(binding),dict(workspace=chat['workspace'],binding=binding))
                    self.flow.change('node-session',chat['id'],{'binding':binding,'initialized':True,'native_sha256':None,
                                     **self.native_snapshot(chat['workspace'],binding)})
                self.spawn(run)
            self.flow.change('run',run,{'paused':False})
        else: raise Invalid('不支持的运行操作')
        if action!='pause':self.flow.event(run,'control-'+action)
        return self.snapshot(run)

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
        row=self.flow.get('run',run);project=self.session_center.workspace(row['workspace']);source=workspace_hash(project['path'])
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
        if workspace_hash(project['path'])!=source:raise Blocked('外部执行期间源码变化')
        validate_receipt(receipt,payload,node['kind'])
        return dict(answer=receipt.get('summary','固定外部步骤完成'),result=receipt,evidence=[receipt])
