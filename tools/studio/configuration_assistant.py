"""AI-generated configuration is a bounded draft, never permission or execution."""
from pathlib import Path
import copy,json,secrets,sqlite3,subprocess,sys,time
from orchestration.common import canonical,digest,tree_hash,clean_env,Blocked
from orchestration.adapters import CLI
from orchestration.contracts import validate
from orchestration.store import lease,process_identity
from .graph import Invalid
from .server import Conflict
from .session_center import SessionJournal
from .model_admission import ModelAdmission
from .agent_sessions import safe_text
from .workbench_graph import validate_graph


def schema(profiles):
    # Keep the schema within the adapter's closed JSON-schema subset.
    def string(limit,**kwargs):return dict(type='string',pattern=r'^[\s\S]{0,'+str(limit)+r'}$',**kwargs)
    text=string(6000)
    node=dict(type='object',additionalProperties=False,required=['id','kind','name','profile','model','effort','mode','initial_prompt','round_prompt','handoff_prompt'],properties=dict(
        id=dict(type='string',pattern='^[A-Za-z][A-Za-z0-9_-]{0,63}$'),kind=dict(type='string',enum=['start','agent','merge','result']),name=string(100,minLength=1),
        profile=dict(type='string',enum=['',*profiles]),model=string(200),effort=string(40),mode=dict(type='string',enum=['Ask','Plan','Agent']),
        initial_prompt=text,round_prompt=text,handoff_prompt=text))
    edge=dict(type='object',additionalProperties=False,required=['source','target'],properties=dict(source=string(64),target=string(64)))
    return dict(type='object',additionalProperties=False,required=['version','name','summary','nodes','edges'],properties=dict(version=dict(type='integer',const=1),name=string(160,minLength=1),summary=text,
        nodes=dict(type='array',minItems=3,items=node),edges=dict(type='array',minItems=2,items=edge)))


def plan_graph(plan, choices):
    validate(plan,schema(choices))
    if len(canonical(plan))>150000 or len(plan['nodes'])>12 or len(plan['edges'])>24:raise Invalid('配置提案超过大小预算')
    nodes=[];ids=set();depth={};incoming={n['id']:[] for n in plan['nodes']}
    for edge in plan['edges']:
        if edge['source'] not in incoming or edge['target'] not in incoming:raise Invalid('提案连线指向不存在的节点')
        incoming[edge['target']].append(edge['source'])
    for _ in plan['nodes']:
        for n in plan['nodes']:
            if n['id'] not in depth and all(p in depth for p in incoming[n['id']]):depth[n['id']]=max([depth[p]+1 for p in incoming[n['id']]] or [0])
    if len(depth)!=len(incoming):raise Invalid('AI提案包含无界循环，请用画布的有界循环节点配置')
    slots={}
    for n in plan['nodes']:
        layer=depth[n['id']];row=slots.get(layer,0);slots[layer]=row+1
        v=dict(id=n['id'],kind=n['kind'],name=n['name'],position=dict(x=80+layer*280,y=120+row*200))
        if n['kind']=='agent':
            allowed=choices.get(n['profile'])
            if not allowed or (n['model'],n['effort']) not in [(m['id'],e) for m in allowed['models'] for e in m['efforts']]:raise Invalid('AI提案选择了尚未配置的模型或强度')
            if allowed['provider']=='copilot' and n['mode'] not in ('Ask','Plan'):raise Invalid('Copilot 提案仅允许 Ask/Plan 上下文分析')
            v.update(profile=n['profile'],provider=allowed['provider'],model=n['model'],effort=n['effort'],mode=n['mode'],permission='workspace-write' if n['mode']=='Agent' else 'read',
                session='new',subagents='disabled',timeout=300,responsibility=n['name'],initial_prompt=n['initial_prompt'],round_prompt=n['round_prompt'],handoff_prompt=n['handoff_prompt'])
        nodes.append(v)
    if not any(n['kind']=='agent' for n in nodes) or sum(n['kind']=='result' for n in nodes)!=1:raise Invalid('提案需要Agent与唯一结果节点')
    graph=dict(version=3,name=plan['name'],nodes=nodes,edges=[dict(id='ai-edge'+str(i),**e,transfer='result') for i,e in enumerate(plan['edges'])],budget=dict(calls=24,seconds=3600))
    return validate_graph(graph)


class ConfigurationJournal(SessionJournal):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.process_started=False

    def intent(self,run,kind,data):
        # Cancellation and the actual adapter's request/process intents use
        # the same durable ordering, including across supervisor processes.
        with self.db.lock,self.db.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            raw=db.execute("SELECT data FROM objects WHERE kind='session-operation' AND id=?",(self.operation,)).fetchone()
            current=json.loads(raw[0])
            if current.get('cancel_requested'):raise Blocked('配置生成已取消；未发起原生请求')
            key=secrets.token_hex(16)
            db.execute('INSERT INTO objects VALUES(?,?,?)',('session-step',key,canonical(dict(id=key,operation=self.operation,kind=kind,data=data,status='INTENT')).decode()))
            if kind=='AGENT_REQUEST' and data.get('requested_session'):
                current['requested_session']=data['requested_session']
                db.execute("UPDATE objects SET data=? WHERE kind='session-operation' AND id=?",(canonical(current).decode(),self.operation))
        return key

    def finish(self,key,status,**updates):
        if status=='RUNNING' and self.db.get('session-step',key)['kind']=='PROCESS':self.process_started=True
        return super().finish(key,status,**updates)

    def visible_request(self,prompt):
        self.db.update('session-operation',self.operation,dict(actual_request=safe_text(prompt),actual_request_sha256=digest(prompt.encode())))


class ConfigurationAssistant:
    def __init__(self,rt):self.rt=rt

    def choices(self,workspace):
        from .workbench_sessions import WorkbenchSessions
        return {p['id']:dict(provider=p['provider'],models=[dict(id=m['id'],efforts=m['efforts']) for m in p.get('models',[]) if m.get('operator_registered')])
                for p in WorkbenchSessions(self.rt).capabilities(False,workspace=workspace) if p.get('available')}

    def _base(self,data):
        self.rt.check_operator();project=self.rt.session_center.workspace(data['workspace']);source=self.rt.validate_source(project['path'])
        g=data['graph']
        if (not isinstance(g,dict) or len(canonical(g))>150000 or g.get('version')!=3 or
            set(g)-{'version','name','nodes','edges','budget','viewport'} or not isinstance(g.get('nodes'),list) or not isinstance(g.get('edges'),list)):
            raise Invalid('当前画布快照无效或过大')
        # A blank graph is a legitimate editor state, not an executable graph.
        if g['nodes']:validate_graph(g)
        elif g['edges']:raise Invalid('空画布不能有未绑定连线')
        return dict(workspace=data['workspace'],source=source,source_sha256=tree_hash(project['path']),base_graph=copy.deepcopy(g),base_graph_sha256=digest(canonical(g)),choices=self.choices(data['workspace']))

    def prepare(self,data):
        if set(data)!={'workspace','graph','goal','profile','model','effort'}:raise Invalid('请提供任务说明、当前画布与配置助手Agent')
        if not isinstance(data['goal'],str) or not 1<=len(data['goal'].strip())<=6000:raise Invalid('请简要描述想完成的工作流')
        base=self._base(data);selection=dict(workspace=data['workspace'],profile=data['profile'],model=data['model'],effort=data['effort'],mode='Ask',permission='read',timeout=120,context='configuration')
        ModelAdmission.validate_request(selection);chosen=ModelAdmission(self.rt).select(selection)
        return self.rt.db.prepare(dict(base,purpose='configuration-generation',goal=safe_text(data['goal']),selection=selection,chosen=chosen,
            admission_checks=[ModelAdmission(self.rt).pending_check(chosen,include_valid=True)],
            human_questions=['发送任务说明与当前节点/提示词给所选外部模型生成配置提案；不读取工作区文件，不启动工作流。生成1次调用，必要连接检查另计；上限120秒，不自动重试。']))

    def _source(self,value):
        project=self.rt.session_center.workspace(value['workspace'])
        if self.rt.validate_source(project['path'])!=value['source'] or tree_hash(project['path'])!=value['source_sha256']:raise Conflict('工作区源码已变化，请重新生成或导入配置')

    def start(self,id,sha256,answer):
        self.rt.check_operator();v,receipt=self.rt.db.consume(id,sha256,[answer])
        if v.get('purpose')!='configuration-generation':raise Blocked('不是AI配置确认')
        self._source(v);selected=v['chosen'];ModelAdmission(self.rt).require(selected['binding'],selected['cwd']);key=secrets.token_hex(16)
        value=dict(v,id=key,state='QUEUED',created_at=time.time(),permission='read',diagnostic=True,configuration=True,
            binding=selected['binding'],cwd=selected['cwd'],profile=v['selection']['profile'],handle='new',cancel_requested=False,authorization=receipt)
        self.rt.db.put('session-operation',key,value)
        argv=[sys.executable,'-B',str(Path(__file__).with_name('configuration_worker.py'))]
        for name,val in dict(operator=self.rt.operator_file,operator_sha256=self.rt.operator_sha256,candidate_sha256=self.rt.candidate_sha256,policy=self.rt.policy_file,policy_sha256=self.rt.policy_sha,job=key).items():argv+=['--'+name.replace('_','-'),str(val)]
        try:
            child=subprocess.Popen(argv,cwd=Path(__file__).parents[2],env=clean_env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self.rt.db.update('session-operation',key,dict(worker=process_identity(child.pid)))
        except OSError:self.rt.db.update('session-operation',key,dict(state='FAILED',reason='配置助手进程未能启动'))
        return self.status(v['workspace'],key)

    def execute(self,key):
        v=self.rt.db.update('session-operation',key,dict(state='STARTING'),states={'QUEUED'})
        if not v.get('configuration'):raise Blocked('不是配置助手任务')
        attempted=False;admission_record=None;journal=None
        try:
            if v.get('cancel_requested'):raise Blocked('配置生成已在排队期间取消；未调用模型')
            self.rt.check_operator();self._source(v);b=v['binding'];verification=ModelAdmission(self.rt).require(b,v['cwd'])
            admission_record=self.rt.db.get('model-admission',verification['key'])
            if admission_record.get('state')!='VERIFIED' or admission_record.get('id')!=verification.get('verification_id'):
                raise Blocked('配置助手连接状态已变化；未调用模型')
            out=self.rt.storage/'configuration-artifacts'/key;out.mkdir(parents=True,mode=0o700);sf=out/'schema.json';sf.write_bytes(canonical(schema(v['choices'])))
            from orchestration.privacy import credentials,redact
            context={k:copy.deepcopy(value) for k,value in v['base_graph'].items() if k in {'version','name','nodes','edges','budget'}};fields={'id','kind','name','profile','provider','model','effort','mode','initial_prompt','round_prompt','handoff_prompt','responsibility'}
            context['nodes']=[{k:value for k,value in n.items() if k in fields} for n in context.get('nodes',[])]
            context['edges']=[{k:value for k,value in e.items() if k in {'source','target','label','transfer'}} for e in context.get('edges',[])]
            known=credentials(b['home'],[src for src,_ in b.get('readonly_files',[])])
            prompt='仅规划工作流配置，不调用工具、不读取文件、不执行任务。只使用给定Agent/模型/强度；优先最少必要调用。支持start/agent/merge/result，可组成顺序或只读并行汇合。其他控制节点请说明由用户在画布添加。非Agent节点profile/model/effort填空，提示词填空，mode填Ask。\n'+json.dumps(dict(goal=v['goal'],current_graph=context,available_agents=v['choices']),ensure_ascii=False)
            prompt=safe_text(redact(prompt,known))
            self.rt.db.update('session-operation',key,dict(actual_request=prompt,actual_request_sha256=digest(prompt.encode())))
            with lease(Path(b['home']).parent/(digest(str(Path(b['home']).resolve()).encode())+'.session.lock')):
                ModelAdmission(self.rt).require(b,v['cwd'])
                if self.rt.db.get('model-admission',verification['key'])!=admission_record:raise Blocked('配置助手连接在启动前变化；未调用模型')
                if not self._claim_dispatch(key):
                    raise Blocked('配置生成已在启动前取消；未调用模型')
                attempted=True
                journal=ConfigurationJournal(self.rt.db,key)
                r=CLI(b['provider'],b,dict(step_timeout_seconds=120,max_log_bytes=2_000_000)).start(prompt,v['cwd'],out,sf,journal,key)
            if self._finish_cancelled(key):return
            def sanitize(value):
                if isinstance(value,str):return safe_text(redact(value,known))
                if isinstance(value,list):return [sanitize(x) for x in value]
                if isinstance(value,dict):return {k:sanitize(x) for k,x in value.items()}
                return value
            r['report']=sanitize(r['report'])
            graph=plan_graph(r['report'],v['choices']);self._source(v)
            if not ModelAdmission(self.rt).renew(b,v['cwd'],r,verification,unless_cancelled=key):
                if self._finish_cancelled(key):return
                raise Blocked('配置助手连接已被撤销或回执不匹配；未发布提案')
            # Cancellation and publication are ordered in the same transaction.
            with self.rt.db.lock,self.rt.db.db() as db,db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute("SELECT data FROM objects WHERE kind='session-operation' AND id=?",(key,)).fetchone();current=json.loads(row[0])
                if current.get('cancel_requested'):current.update(state='CANCELLED')
                else:current.update(state='COMPLETED',plan=r['report'],proposed_graph=graph,receipt={k:r.get(k) for k in ('session_id','provider','requested_model','requested_effort','resolved_model','resolved_effort','process','usage','home','cwd','raw_report_sha256')},summary=safe_text(r['report']['summary']))
                self._finish_dispatch(db,current)
                db.execute("UPDATE objects SET data=? WHERE kind='session-operation' AND id=?",(canonical(current).decode(),key))
        except (Blocked,Invalid,ValueError,OSError,Conflict,sqlite3.Error) as e:
            if attempted:
                # A cancellation before a process exists vetoes revocation in
                # the same certificate transaction, not via a stale snapshot.
                ModelAdmission(self.rt).invalidate(b,v['cwd'],'configuration-call-failed',expected_record=admission_record,
                    unless_cancelled=key if not journal or not journal.process_started else None)
            with self.rt.db.lock,self.rt.db.db() as db,db:
                db.execute('BEGIN IMMEDIATE')
                raw=db.execute("SELECT data FROM objects WHERE kind='session-operation' AND id=?",(key,)).fetchone();current=json.loads(raw[0])
                current.update(state='CANCELLED' if current.get('cancel_requested') else 'FAILED',reason=safe_text(str(e)))
                self._finish_dispatch(db,current)
                db.execute("UPDATE objects SET data=? WHERE kind='session-operation' AND id=?",(canonical(current).decode(),key))

    @staticmethod
    def _finish_dispatch(db,current):
        if not current.get('dispatch_step'):return
        row=db.execute("SELECT data FROM objects WHERE kind='session-step' AND id=?",(current['dispatch_step'],)).fetchone();step=json.loads(row[0])
        step['status']=current['state']
        db.execute("UPDATE objects SET data=? WHERE kind='session-step' AND id=?",(canonical(step).decode(),step['id']))

    def _claim_dispatch(self,key):
        """Linearization point: cancellation wins, or one durable dispatch intent wins.

        Later cancellation still reaches the adapter journal and process supervisor;
        it cannot turn an already claimed operation into a second queued attempt.
        """
        with self.rt.db.lock,self.rt.db.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT data FROM objects WHERE kind='session-operation' AND id=?",(key,)).fetchone();current=json.loads(row[0])
            if current.get('cancel_requested'):return False
            if current.get('dispatch_step') or current['state']!='STARTING':raise Blocked('配置请求已发起或状态已变化；未重复调用')
            step=secrets.token_hex(16);current.update(dispatch_step=step,dispatch_claimed_at=time.time())
            db.execute('INSERT INTO objects VALUES(?,?,?)',('session-step',step,canonical(dict(id=step,operation=key,kind='CONFIGURATION_DISPATCH',status='INTENT')).decode()))
            db.execute("UPDATE objects SET data=? WHERE kind='session-operation' AND id=?",(canonical(current).decode(),key))
        return True

    def _finish_cancelled(self,key):
        with self.rt.db.lock,self.rt.db.db() as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT data FROM objects WHERE kind='session-operation' AND id=?",(key,)).fetchone();current=json.loads(row[0])
            if not current.get('cancel_requested'):return False
            current['state']='CANCELLED'
            self._finish_dispatch(db,current)
            db.execute("UPDATE objects SET data=? WHERE kind='session-operation' AND id=?",(canonical(current).decode(),key))
        return True

    def status(self,workspace,key):
        v=self.rt.db.get('session-operation',key)
        if v['workspace']!=workspace or not v.get('configuration'):raise Blocked('配置提案不属于当前工作区')
        if v['state'] not in ('COMPLETED','FAILED','CANCELLED'):
            live=self.rt.session_center.operation(key,workspace)
            # operation() can observe a publication after our first read. Reload
            # one complete row: never pair its new status with our old payload.
            v=self.rt.db.get('session-operation',key);state=v['state']
            if state not in ('COMPLETED','FAILED','CANCELLED') and live['state']=='RECOVERY_REQUIRED':state='RECOVERY_REQUIRED'
        else:state=v['state']
        return dict(id=key,workspace=workspace,state=state,source_kind=v.get('source_kind','native-ai'),summary=v.get('summary'),graph=v.get('proposed_graph') if state=='COMPLETED' else None,
            base_graph_sha256=v['base_graph_sha256'],reason=v.get('reason'),receipt=v.get('receipt'),actual_request=v.get('actual_request'),actual_request_sha256=v.get('actual_request_sha256'))

    def cancel(self,workspace,key):
        v=self.rt.db.get('session-operation',key)
        if v['workspace']!=workspace or not v.get('configuration') or v.get('source_kind')=='external-unverified':raise Blocked('不是可取消的当前配置任务')
        self.rt.session_center.cancel(workspace,key)
        return self.status(workspace,key)

    def external(self,data):
        if set(data)!={'workspace','graph','plan'}:raise Invalid('外部提案只接受工作区、当前画布和版本化配置计划，不接受命令/凭据/授权')
        base=self._base(data);graph=plan_graph(data['plan'],base['choices']);key=secrets.token_hex(16)
        v=dict(base,id=key,configuration=True,source_kind='external-unverified',diagnostic=True,state='COMPLETED',proposed_graph=graph,plan=data['plan'],summary=safe_text(data['plan']['summary']),created_at=time.time())
        self.rt.db.put('session-operation',key,v);return self.status(data['workspace'],key)

    def apply(self,data):
        if set(data)!={'workspace','id','graph','answer'} or not isinstance(data['answer'],str) or not data['answer'].strip():raise Invalid('请确认将提案应用到当前画布；这不会开始运行')
        self.rt.check_operator();v=self.rt.db.get('session-operation',data['id'])
        if v['workspace']!=data['workspace'] or not v.get('configuration') or v['state']!='COMPLETED':raise Blocked('没有可应用的当前工作区提案')
        self._source(v)
        if digest(canonical(data['graph']))!=v['base_graph_sha256']:raise Conflict('画布在生成后已被修改；未覆盖，请基于当前版本重新生成')
        # Revalidate current profile/model choices after any registration changes.
        graph=plan_graph(v['plan'],self.choices(v['workspace']))
        return dict(graph=graph,applied_to_draft_only=True,execution_started=False,permissions_granted=False)
