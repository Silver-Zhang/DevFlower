"""Bounded static inspection of a draft. Never invokes a provider or grants scope."""
from pathlib import Path,PurePosixPath
import os,re
from orchestration.common import Blocked
from .graph import Invalid
from .workbench_graph import validate_graph


def inspect_workflow(rt, graph, task, *, workspace_path=None, workspace_id=None):
    issues=[]
    def add(severity, message, *, node=None, edge=None, field=None):
        if len(issues)<256:
            issues.append(dict(severity=severity,message=message,node=node,edge=edge,field=field))
    if not isinstance(task,str) or not task.strip():
        add('error','请填写本次任务，说明希望 Agent 完成什么。',field='task')
    elif len(task)>50000:add('error','本次任务超过长度上限，请缩短。',field='task')
    if not isinstance(graph,dict):
        add('error','工作流格式无效，请重新打开草稿。',field='graph')
        return dict(issues=issues,execution_verified=False)
    budget=graph.get('budget',{})
    if not isinstance(budget,dict):add('error','运行预算格式无效。',field='budget')
    else:
        for key,default,lo,hi,label in [('calls',32,1,100,'调用次数'),('seconds',3600,10,14400,'运行时间')]:
            value=budget.get(key,default)
            if type(value) is not int or not lo<=value<=hi:add('error',f'{label}需要在 {lo}–{hi} 范围内。',field='budget')
    def walk(g,prefix='',depth=0):
        if not isinstance(g,dict) or not isinstance(g.get('nodes'),list) or not isinstance(g.get('edges'),list):
            add('error','流程节点或连线格式无效。',node=prefix.rstrip('/') or None,field='graph');return
        if depth>2 or len(g['nodes'])>64 or len(g['edges'])>128:
            add('error','节点、连线或嵌套层级超出上限。',node=prefix.rstrip('/') or None,field='graph');return
        nodes=[n for n in g['nodes'] if isinstance(n,dict) and isinstance(n.get('id'),str)]
        ids={n.get('id') for n in nodes if isinstance(n.get('id'),str)}
        incoming={n:0 for n in ids};outgoing={n:0 for n in ids};forward={n:[] for n in ids};reverse={n:[] for n in ids}
        by_id={n['id']:n for n in nodes}
        for e in g['edges']:
            if not isinstance(e,dict):add('error','连线格式无效，请删除后重连。',node=prefix.rstrip('/') or None,field='edge');continue
            source,target=e.get('source'),e.get('target');eid=e.get('id')
            if not isinstance(source,str) or not isinstance(target,str) or source not in ids or target not in ids or source==target:
                add('error','连线端点不存在或相同，请删除后重新连接。',node=prefix.rstrip('/') or None,edge=eid,field='edge');continue
            incoming[target]+=1;outgoing[source]+=1
            forward[source].append(target);reverse[target].append(source)
            source_node=by_id[source];transfer=e.get('transfer','result')
            def edge_error(message):add('error',message,node=prefix.rstrip('/') or None,edge=eid,field='edge')
            if transfer not in ('result','answer','summary','field','artifacts','issues','evidence'):
                edge_error('交接类型无效，请在连线设置中选择内容。')
            elif transfer=='field':
                from .workbench_data import public_field
                from .workbench_graph import field
                try:field({},public_field(e.get('field','')))
                except (Invalid,TypeError,ValueError):edge_error('交接字段无效或属于私有字段，请选择公开结果字段。')
            if source_node.get('kind') in ('start','merge'):
                selected=e.get('field','').split('.')[0] if transfer=='field' and isinstance(e.get('field',''),str) else transfer
                allowed={'answer','result','sources'} if source_node['kind']=='merge' else {'answer','result'}
                if not isinstance(selected,str) or selected not in allowed:edge_error('上游节点没有所选交接字段，请选择已有的结果或回答。')
            if source_node.get('kind')=='router':
                routes=source_node.get('routes',[]);labels=[r.get('label') for r in routes if isinstance(r,dict)] if isinstance(routes,list) else []
                labels.append(source_node.get('default_route'))
                if e.get('route') not in labels:edge_error('路由连线尚未选择已声明的分支。')
            elif 'route' in e:edge_error('只有路由节点的连线可以选择分支。')
            cap=e.get('max_chars',16000)
            if type(cap) is not int or not 128<=cap<=60000:edge_error('交接上下文预算需要在 128–60000 范围内。')
            # A single scalar handoff is wrapped by the scheduler as
            # {answer: str(value), result: value}. Multiple inputs take a
            # different merge shape, so do not infer their individual types.
            if transfer in ('answer','summary') and sum(isinstance(x,dict) and x.get('target')==target for x in g['edges'])==1:
                contracts=by_id[target].get('input_contract',[])
                if isinstance(contracts,list):
                    for row in contracts:
                        if not isinstance(row,dict) or not isinstance(row.get('field'),str):continue
                        field_name=row['field'];expected=row.get('type','any')
                        if field_name in ('answer','result'):
                            if expected not in ('any','string'):
                                edge_error(f'交接内容「{field_name}」是文本，但下游合同要求 {expected}；请调整连线或输入类型。')
                        elif row.get('required') is True:
                            edge_error(f'文本交接不提供必需字段「{field_name}」；请调整传递内容或下游输入合同。')
            if transfer=='field' and sum(isinstance(x,dict) and x.get('target')==target for x in g['edges'])==1:
                output=source_node.get('output_contract',[]);target_contract=by_id[target].get('input_contract',[])
                if isinstance(output,list) and isinstance(target_contract,list):
                    selected=next((r for r in output if isinstance(r,dict) and r.get('field')==e.get('field')),None)
                    actual=selected.get('type') if selected else None
                    if actual in ('string','number','boolean','array','null'):
                        for row in target_contract:
                            if not isinstance(row,dict) or not isinstance(row.get('field'),str):continue
                            field_name=row['field'];expected=row.get('type','any')
                            if field_name in ('answer','result'):
                                kind='string' if field_name=='answer' else actual
                                if expected not in ('any',kind):edge_error(f'交接字段「{field_name}」的类型为 {kind}，下游要求 {expected}。')
                            elif row.get('required') is True:edge_error(f'所选字段交接不提供必需字段「{field_name}」；请调整连线或输入合同。')
            if transfer=='result' and sum(isinstance(x,dict) and x.get('target')==target for x in g['edges'])==1:
                provided=source_node.get('output_contract',[]);required=by_id[target].get('input_contract',[])
                if isinstance(provided,list) and isinstance(required,list):
                    declared={r.get('field'):r.get('type','any') for r in provided if isinstance(r,dict) and isinstance(r.get('field'),str)}
                    for row in required:
                        if not isinstance(row,dict) or not isinstance(row.get('field'),str):continue
                        field_name=row['field'];expected=row.get('type','any');actual=declared.get(field_name)
                        if actual and actual!='any' and expected!='any' and actual!=expected:
                            edge_error(f'交接字段「{field_name}」的类型为 {actual}，下游要求 {expected}；请调整字段合同。')
        def reachable(seeds,links):
            seen=set();pending=list(seeds)
            while pending:
                item=pending.pop()
                if item not in seen:seen.add(item);pending.extend(links[item])
            return seen
        starts=[n['id'] for n in nodes if n.get('kind')=='start'];results=[n['id'] for n in nodes if n.get('kind')=='result']
        from_start=reachable(starts,forward);to_result=reachable(results,reverse)
        for n in nodes:
            ident=n.get('id');path=prefix+str(ident or '?');kind=n.get('kind');name=n.get('name') or '未命名节点'
            if starts and ident not in from_start:add('error',f'「{name}」无法从开始节点到达，请连接到流程。',node=path,field='edge')
            if results and ident not in to_result:add('error',f'「{name}」无法到达结果节点，请连接到结果。',node=path,field='edge')
            if kind!='start' and incoming.get(ident,0)==0:add('error',f'「{name}」没有输入连线，请连接上游节点。',node=path,field='edge')
            if kind!='result' and outgoing.get(ident,0)==0:add('error',f'「{name}」没有输出连线，请连接下一节点。',node=path,field='edge')
            if incoming.get(ident,0)>1 and kind not in ('merge','result'):add('error',f'「{name}」收到多路输入，请先添加汇合节点。',node=path,field='edge')
            from .data_contracts import normalize
            for contract in ('input_contract','output_contract'):
                if contract in n:
                    try:normalize(n[contract],[])
                    except (Invalid,ValueError,TypeError,KeyError):add('error',f'「{name}」的字段合同无效，请检查字段名称和类型。',node=path,field=contract)
            if kind=='merge' and n.get('strategy','all')=='all' and incoming.get(ident,0)>1:
                contracts=n.get('input_contract',[])
                if isinstance(contracts,list):
                    possible={'answer':'string','result':'object','result.inputs':'array'}
                    for row in contracts:
                        if not isinstance(row,dict) or not isinstance(row.get('field'),str):continue
                        field_name=row['field'];expected=row.get('type','any');actual=possible.get(field_name)
                        if actual and expected not in ('any',actual):add('error',f'「{name}」的汇合输入「{field_name}」类型为 {actual}，合同要求 {expected}。',node=path,field='input_contract')
                        elif not actual and row.get('required') is True:add('error',f'「{name}」的多路汇合输入不提供必需字段「{field_name}」。',node=path,field='input_contract')
            if kind=='router':
                routes=n.get('routes',[])
                if isinstance(routes,list):
                    connected={e.get('route') for e in g['edges'] if isinstance(e,dict) and e.get('source')==ident}
                    labels=[r.get('label') for r in routes if isinstance(r,dict) and isinstance(r.get('label'),str) and r.get('label')]
                    if isinstance(n.get('default_route'),str) and n['default_route']:labels.append(n['default_route'])
                    for label in labels:
                        if label not in connected:add('error',f'「{name}」的分支「{label}」尚未连接目标节点。',node=path,field='routes')
                if not n.get('default_route'):
                    add('warning','「'+name+'」没有默认分支；规则未命中时流程会停止。',node=path,field='default_route')
                classifier=n.get('classifier')
                if classifier is not None:
                    if not isinstance(classifier,dict):
                        add('error','「'+name+'」的分类器配置无效。',node=path,field='classifier')
                    else:
                        for key,label in [('profile','Provider'),('provider','Provider 类型'),('model','模型'),('effort','推理强度')]:
                            if not isinstance(classifier.get(key),str) or not classifier[key].strip():
                                add('error',f'「{name}」的分类器缺少{label}。',node=path,field='classifier.'+key)
                        profile=rt.operator['profiles'].get(classifier.get('profile')) if isinstance(classifier.get('profile'),str) else None
                        if not profile:
                            add('error',f'「{name}」的分类器 Provider 配置不可用。',node=path,field='classifier.profile')
                        elif classifier.get('provider')!=profile.get('provider'):
                            add('error',f'「{name}」的分类器 Provider 与配置不一致。',node=path,field='classifier.provider')
                        if profile:
                            try:cli=rt.flow.get('cli-probe',classifier['profile'])
                            except Invalid:cli=None
                            if cli and not cli.get('available'):add('error',f'「{name}」的分类器 CLI 不可用，请检查安装。',node=path,field='classifier.profile')
                            try:auth=rt.flow.get('auth-probe',classifier['profile'])
                            except Invalid:auth=None
                            if auth and auth.get('logged_in') is False:add('warning',f'「{name}」的分类器上次检查显示未登录。',node=path,field='classifier.profile')
                            try:catalog=rt.flow.get('model-discovery',classifier['profile'])
                            except Invalid:catalog=None
                            if catalog and not catalog.get('error'):
                                selected=next((row for row in catalog.get('models',[]) if row.get('id')==classifier.get('model')),None)
                                if selected and selected.get('efforts') and classifier.get('effort') not in selected['efforts']:
                                    add('error',f'「{name}」的分类器推理强度不在模型目录中。',node=path,field='classifier.effort')
                                elif not selected and classifier.get('model')!=profile.get('model'):
                                    add('warning',f'「{name}」的分类器模型不在上次目录中，请刷新。',node=path,field='classifier.model')
                            else:add('warning',f'「{name}」的分类器尚无可用模型目录，请刷新核对。',node=path,field='classifier.model')
                        timeout=classifier.get('timeout',60)
                        if type(timeout) is not int or not 10<=timeout<=120:
                            add('error',f'「{name}」的分类超时应为 10–120 秒。',node=path,field='classifier.timeout')
                        if not n.get('default_route'):
                            add('error',f'「{name}」启用了分类器，请设置默认分支。',node=path,field='default_route')
            if kind in ('verify','delivery'):
                fixed=rt.policy.get('external_steps',{})
                if not isinstance(fixed,dict) or n.get('profile') not in fixed:
                    add('error',f'「{name}」需要选择已固定配置的外部执行器。',node=path,field='profile')
                if kind=='delivery' and rt.operator.get('native_local'):
                    add('error',f'「{name}」的交付需要使用受控外部流程，当前本机模式不自动交付。',node=path,field='profile')
            if kind=='agent':
                for key,label in [('profile','Provider'),('model','模型'),('effort','推理强度'),('round_prompt','每轮提示词')]:
                    if not isinstance(n.get(key),str) or not n[key].strip():add('error',f'「{name}」缺少{label}，请在节点设置中填写。',node=path,field=key)
                profile_id=n.get('profile');profile=rt.operator['profiles'].get(profile_id) if isinstance(profile_id,str) else None
                if not profile:add('error',f'「{name}」的 Provider 配置不可用，请重新选择。',node=path,field='profile')
                else:
                    if n.get('provider',profile['provider'])!=profile['provider']:add('error',f'「{name}」的 Provider 与所选配置不一致。',node=path,field='profile')
                    try: cli=rt.flow.get('cli-probe',n['profile'])
                    except Invalid:cli=None
                    if cli and not cli.get('available'):add('error',f'「{name}」的 CLI 不可用，请检查安装后刷新。',node=path,field='profile')
                    try: auth=rt.flow.get('auth-probe',n['profile'])
                    except Invalid:auth=None
                    if auth and auth.get('logged_in') is False:add('warning',f'「{name}」上次检查显示未登录，请检查原生登录状态。',node=path,field='profile')
                    try: catalog=rt.flow.get('model-discovery',n['profile'])
                    except Invalid:catalog=None
                    if catalog and not catalog.get('error'):
                        found=next((x for x in catalog.get('models',[]) if x.get('id')==n.get('model')),None)
                        if found:
                            efforts=found.get('efforts') or []
                            if efforts and n.get('effort') not in efforts:add('error',f'「{name}」的推理强度不在模型目录中，请重新选择。',node=path,field='effort')
                        elif n.get('model')!=profile.get('model'):add('warning',f'「{name}」的模型不在上次目录中，请刷新目录核对。',node=path,field='model')
                    else:add('warning',f'「{name}」尚无可用模型目录，请刷新后核对模型。',node=path,field='model')
                if workspace_id and workspace_path and profile and profile.get('provider')=='claude' and rt.operator.get('native_local',{}).get('claude') and n.get('session','new')!='new':
                    try:
                        from .native_local import bind,own_busy
                        from .managed_claude import require_managed
                        from .session_sources import opened,LIMITS
                        import hashlib
                        if not isinstance(n['session'],str) or not re.fullmatch(r'[0-9a-f]{64}',n['session']):raise Blocked('会话身份无效')
                        row=rt.db.get('session-handle',n['session']);native=row['native']
                        if row.get('workspace')!=workspace_id or row.get('ambiguous') or not row.get('authorized_read') or native.get('cwd_changed') or native.get('cwd')!=workspace_path:raise Blocked('会话身份或工作区不匹配')
                        binding=bind(rt,profile,workspace_id);binding.update(runtime_profile=profile_id,session_id=native['session_id'],model=n.get('model'),effort=n.get('effort'))
                        if any(row.get('profile',{}).get(k)!=binding.get(k) for k in ('provider','home','config_root')):raise Blocked('原生环境不匹配')
                        file=Path(native['path']);root=Path(binding['config_root'])/'projects'
                        if not file.is_absolute() or not file.is_relative_to(root):raise Blocked('原生历史位置不匹配')
                        with opened(file) as fd:
                            before=os.fstat(fd)
                            if before.st_size>LIMITS['file_bytes']:raise Blocked('原生历史超过检查预算')
                            hasher=hashlib.sha256()
                            while chunk:=os.read(fd,65536):hasher.update(chunk)
                            after=os.fstat(fd)
                            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise Blocked('原生历史正在变化')
                        if hasher.hexdigest()!=native['sha256']:raise Blocked('原生历史已变化')
                        require_managed(rt,binding,workspace_path,native['sha256'])
                        if own_busy(rt,binding,native['session_id']):raise Blocked('会话正在由另一端使用')
                    except (Blocked,Invalid,OSError,ValueError,KeyError,TypeError):
                        add('error',f'「{name}」的 Claude 会话无法安全续接；请刷新会话或选择新会话。',node=path,field='session')
                mode=n.get('mode','Ask');permission=n.get('permission','read' if mode!='Agent' else 'workspace-write')
                timeout=n.get('timeout',300)
                if type(timeout) is not int or not 10<=timeout<=3600:add('error',f'「{name}」的超时应为 10–3600 秒。',node=path,field='timeout')
                if mode not in ('Ask','Plan','Agent'):add('error',f'「{name}」的工作方式无效。',node=path,field='mode')
                if permission not in ('read','workspace-write') or permission=='workspace-write' and mode!='Agent':add('error',f'「{name}」的权限与工作方式不匹配。',node=path,field='permission')
                provider=profile.get('provider') if profile else n.get('provider')
                if provider=='copilot' and (mode=='Agent' or permission=='workspace-write'):
                    add('error',f'「{name}」的 Copilot 配置只支持只读 Ask/Plan。',node=path,field='mode')
                if provider=='claude' and permission=='workspace-write' and rt.operator.get('native_local',{}).get('claude'):
                    add('error',f'「{name}」的本机 Claude 写入边界尚未开放，请使用只读方式。',node=path,field='permission')
                if not isinstance(n.get('context_refs',[]),list) or len(n.get('context_refs',[]))>16:add('error',f'「{name}」的上下文文件列表无效或超过 16 项。',node=path,field='context_refs')
                for ref in n.get('context_refs',[]) if isinstance(n.get('context_refs',[]),list) else []:
                    if not isinstance(ref,str) or not ref or PurePosixPath(ref).is_absolute() or '..' in PurePosixPath(ref).parts or '.devflower' in PurePosixPath(ref).parts:
                        add('error',f'「{name}」的上下文路径不受支持，请选择工作区内文件。',node=path,field='context_refs')
                    elif workspace_path and n.get('session','new')=='new':
                        from .session_sources import opened
                        try:
                            with opened(Path(workspace_path)/ref) as fd:size=os.fstat(fd).st_size
                            if size>16000:add('error',f'「{name}」的上下文文件「{ref}」超过 16000 字节，请缩小内容。',node=path,field='context_refs')
                        except (OSError,ValueError):
                            add('error',f'「{name}」无法读取上下文文件「{ref}」；请检查文件与链接。',node=path,field='context_refs')
                subagents=n.get('subagents','disabled')
                if subagents not in ('disabled','readonly') or subagents=='readonly' and (mode=='Agent' or permission=='workspace-write'):
                    add('error',f'「{name}」的子 Agent 设置不符合只读权限，请关闭或改用只读工作方式。',node=path,field='subagents')
                if subagents=='readonly' and rt.operator.get('native_local',{}).get(profile.get('provider') if profile else n.get('provider')):
                    add('error',f'「{name}」的本机原生模式尚不支持受管子 Agent，请关闭该选项。',node=path,field='subagents')
                skills=n.get('skills',[])
                if not isinstance(skills,list) or len(skills)>6 or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{64}',v) for v in skills):add('error',f'「{name}」的 Skill 选择身份无效。',node=path,field='skills')
                elif skills:
                    if workspace_id:
                        if rt.operator.get('native_local',{}).get(profile.get('provider') if profile else n.get('provider')):
                            add('error',f'「{name}」的本机原生模式尚不支持附加 Skill，请移除选择。',node=path,field='skills')
                        for skill in skills:
                            try:source=rt.flow.get('skill-source',skill)
                            except Invalid:source=None
                            if not source or source.get('workspace')!=workspace_id or source.get('profile')!=n.get('profile'):
                                add('error',f'「{name}」的 Skill 不属于当前工作区或 Provider，请刷新后重新选择。',node=path,field='skills')
                    add('warning',f'「{name}」使用了 Skill；启动前仍会核对其内容和权限。',node=path,field='skills')
            if kind in ('condition','loop','router'):
                tests=([n.get('test',{})] if kind=='condition' else [n.get('until',{})] if kind=='loop' else [r.get('test',{}) for r in n.get('routes',[]) if isinstance(r,dict)] if isinstance(n.get('routes',[]),list) else [])
                for test in tests:
                    if not isinstance(test,dict):continue
                    op=test.get('op','equals');value=test.get('value',True)
                    if op in ('greater','less') and type(value) not in (int,float) or op=='contains' and not isinstance(value,str):
                        add('error',f'「{name}」的比较值类型与条件操作不匹配，请选择数字或文本。',node=path,field='test' if kind=='condition' else 'until' if kind=='loop' else 'routes')
                    if kind in ('condition','router') and 'input_contract' in n and isinstance(n['input_contract'],list):
                        declared=next((r for r in n['input_contract'] if isinstance(r,dict) and r.get('field')==test.get('field')),None)
                        field_name=test.get('field')
                        if isinstance(field_name,str) and not declared:
                            add('error',f'「{name}」读取的字段「{field_name}」未在输入合同中声明。',node=path,field='input_contract')
                        elif declared:
                            actual=declared.get('type','any');needed='number' if op in ('greater','less') else 'string' if op=='contains' else None
                            if needed and actual not in ('any',needed):add('error',f'「{name}」条件需要 {needed} 类型，字段「{field_name}」声明为 {actual}。',node=path,field='test' if kind=='condition' else 'routes')
            if kind=='loop':
                if type(n.get('max_rounds',5)) is not int or not 1<=n.get('max_rounds',5)<=5:add('error',f'「{name}」的循环次数应为 1–5。',node=path,field='max_rounds')
                walk(n.get('body'),path+'/',depth+1)
    walk(graph)
    def maximum_calls(g,depth=0):
        if depth>2 or not isinstance(g,dict) or not isinstance(g.get('nodes'),list):return 0
        total=0
        for n in g['nodes'][:64]:
            if not isinstance(n,dict):continue
            if n.get('kind')=='agent':total+=1
            elif n.get('kind')=='router' and isinstance(n.get('classifier'),dict):
                retries=n['classifier'].get('retries',0);total+=1+(retries if type(retries) is int and 0<=retries<=1 else 0)
            elif n.get('kind')=='loop':
                rounds=n.get('max_rounds',5);total+=(rounds if type(rounds) is int and 1<=rounds<=5 else 1)*maximum_calls(n.get('body'),depth+1)
        return total
    estimate=maximum_calls(graph)
    if isinstance(budget,dict) and type(budget.get('calls',32)) is int and estimate>budget.get('calls',32):
        add('warning',f'所有分支及循环的上限合计可能需要 {estimate} 次模型调用，超过当前预算；未选分支或提前结束可能少于此数。',field='budget')
    try:validate_graph(graph)
    except (Invalid,ValueError,TypeError,KeyError,AttributeError,RecursionError) as exc:add('error','流程结构需要修正：'+str(exc),field='graph')
    add('warning','静态检查不会调用模型或证明连接成功；实际结果以任务回执为准。',field='model')
    return dict(issues=issues,execution_verified=False)
