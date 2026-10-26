"""Embedded conversational canvas editor. This is not an execution Provider.

Visible conversations live in the workbench database. Each turn has one durable
identity; refresh/reconnect reads it and never replays an inference. The backend
is replaceable, while proposal validation and permission boundaries stay local.
"""
from pathlib import Path
import copy, json, os, secrets, sqlite3, threading, time
from orchestration.common import Blocked,canonical,digest
from orchestration.store import alive,process_identity
from .graph import Invalid
from .server import Conflict
from .agent_sessions import safe_text
from .configuration_assistant import ConfigurationAssistant,plan_graph,schema
from .devflower_agent import AGENT_ID,DevFlowerAgent


def expand_plan(plan,choices):
    """Compact assistant output -> unchanged strict public plan validator.

    Only known local defaults are filled; unknown keys, profiles and mismatched
    model/effort pairs still fail the final validator. No execution permission.
    """
    value=copy.deepcopy(plan)
    if not isinstance(value,dict) or not isinstance(value.get('nodes'),list):raise Invalid('画布计划格式无效')
    fields={'id','kind','name','profile','model','effort','mode','initial_prompt','round_prompt','handoff_prompt'}
    for n in value['nodes']:
        if not isinstance(n,dict) or set(n)-fields:raise Invalid('节点包含未允许的配置字段')
        p=choices.get(n.get('profile'),{}) if n.get('kind')=='agent' else {}
        models=p.get('models',[])
        if n.get('kind')=='agent' and (not p or not models):raise Invalid('助手选择了尚未配置的 Agent')
        n.setdefault('profile','');n.setdefault('model',p.get('default_model',models[0]['id'] if len(models)==1 else ''))
        n.setdefault('effort',p.get('default_effort',models[0]['efforts'][0] if len(models)==1 and len(models[0]['efforts'])==1 else ''))
        n.setdefault('mode','Ask')
        for key in ('initial_prompt','round_prompt','handoff_prompt'):n.setdefault(key,'')
    # Entry/result are local canvas scaffolding, not extra Agent invocations.
    # LLMs may describe just the meaningful processing nodes. Add missing
    # scaffolding deterministically, then apply the unchanged graph validator.
    if not isinstance(value.get('edges'),list) or any(not isinstance(e,dict) or set(e)!={'source','target'} for e in value['edges']):raise Invalid('画布连线格式无效')
    ids={n.get('id') for n in value['nodes'] if isinstance(n.get('id'),str)}
    def add(kind):
        node_id='assistant_'+kind
        while node_id in ids:node_id+='x'
        ids.add(node_id);n=dict(id=node_id,kind=kind,name='开始' if kind=='start' else '输出结果',profile='',model='',effort='',mode='Ask',initial_prompt='',round_prompt='',handoff_prompt='')
        value['nodes'].append(n);return node_id
    if value['nodes'] and not any(n.get('kind')=='start' for n in value['nodes']):
        targets={e['target'] for e in value['edges']};roots=[n.get('id') for n in value['nodes'] if n.get('id') not in targets];entry=add('start')
        value['edges'][:0]=[dict(source=entry,target=n) for n in roots]
    if value['nodes'] and not any(n.get('kind')=='result' for n in value['nodes']):
        sources={e['source'] for e in value['edges']};leaves=[n.get('id') for n in value['nodes'] if n.get('id') not in sources];result=add('result')
        value['edges'].extend(dict(source=n,target=result) for n in leaves)
    return value


def decode_reply(text,choices):
    if not isinstance(text,str) or len(text.encode())>150000:raise Invalid('助手回复为空或超出大小限制')
    value=text.strip()
    if value.startswith('```json\n') and value.endswith('```'):value=value[8:-3].strip()
    try:result=json.loads(value)
    except (ValueError,RecursionError):raise Invalid('模型没有返回有效配置；未修改画布，可继续描述需求') from None
    if not isinstance(result,dict) or set(result)!={'reply','plan'} or not isinstance(result['reply'],str) or not result['reply'].strip() or len(result['reply'])>12000:
        raise Invalid('助手回复不符合画布接口；未修改草稿')
    if result['plan'] is not None:result['plan']=expand_plan(result['plan'],choices)
    graph=plan_graph(result['plan'],choices) if result['plan'] is not None else None
    return result,graph


class CanvasAssistant:
    def __init__(self,rt):
        self.rt=rt;self.lock=threading.RLock();self.active={};self.closed=False;self.catalog_cache=None
        self.root=rt.storage/'canvas-assistant';self.root.mkdir(mode=0o700,exist_ok=True)
        self.default_backend=lambda cwd,**kw:DevFlowerAgent(self.rt,cwd,**kw);self.backend=self.default_backend

    def catalog(self,refresh=False):
        self.rt.check_operator()
        with self.lock:
            if self.closed:raise Blocked('助手服务正在关闭')
        tag=None
        if self.backend is self.default_backend:
            from .devflower_agent import AgentConnection
            tag=AgentConnection(self.rt).read()['fingerprint']
        with self.lock:
            if not refresh and self.catalog_cache and self.catalog_cache[0]==tag and time.time()-self.catalog_cache[1]<60:
                return dict(self.catalog_cache[2],cached=True)
        # A catalog read sends no workspace, prompt or source and does no inference.
        key='catalog-'+secrets.token_hex(16);cancel=threading.Event()
        with self.lock:
            if self.closed:raise Blocked('助手服务正在关闭')
            self.active[key]=(cancel,threading.current_thread())
        try:
            with self.backend(self.root,timeout=30,cancelled=cancel.is_set) as client:result=client.catalog()
            with self.lock:self.catalog_cache=(tag,time.time(),result)
            return dict(result,cached=False)
        finally:
            with self.lock:self.active.pop(key,None)

    def listing(self,workspace,view='active',query=''):
        self.rt.session_center.workspace(workspace)
        if view not in ('active','archived','trash') or not isinstance(query,str) or len(query)>200:raise Invalid('助手会话筛选无效')
        with self.rt.flow.tx(read=True) as db:
            rows=db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND json_extract(data,'$.workspace')=? AND json_extract(data,'$.agent_id')=? AND COALESCE(json_extract(data,'$.collection'),'active')=? AND instr(lower(COALESCE(json_extract(data,'$.title'),json_extract(data,'$.messages[0].text'),'')),lower(?))>0 ORDER BY COALESCE(json_extract(data,'$.updated_at'),0) DESC,rowid DESC LIMIT 100",(workspace,AGENT_ID,view,query)).fetchall()
            result=[]
            for raw, in rows:
                v=json.loads(raw);turn=None
                if v.get('active_turn'):
                    row=db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(v['active_turn'],)).fetchone()
                    if row:turn=json.loads(row[0])
                state=turn['state'] if turn else 'EMPTY'
                if turn and state in ('QUEUED','RUNNING'):
                    with self.lock:running=turn['id'] in self.active
                    if not running and (not alive(turn.get('owner')) or turn['owner']['pid']==os.getpid()):state='RECOVERY_REQUIRED'
                result.append(dict(id=v['id'],title=v.get('title') or next((m['text'][:60] for m in v['messages'] if m['role']=='user'),'新对话'),collection=v.get('collection','active'),metadata_revision=v.get('metadata_revision',0),updated_at=v.get('updated_at'),message_count=len(v['messages']),state=state))
        return dict(conversations=result,view=view,limit=100)

    def manage(self,data):
        if set(data)-{'workspace','id','operation','title','expected'} or not {'workspace','id','operation','expected'}<=set(data):raise Invalid('助手会话操作格式无效')
        self.rt.session_center.workspace(data['workspace']);op=data['operation']
        if op not in ('rename','archive','trash','restore'):raise Invalid('不支持此助手会话操作')
        if type(data['expected']) is not int:raise Invalid('助手会话版本无效')
        with self.lock,self.rt.flow.tx() as db:
            row=db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND id=?",(data['id'],)).fetchone()
            if not row:raise Invalid('助手会话不存在')
            v=json.loads(row[0])
            if v['workspace']!=data['workspace'] or v.get('agent_id')!=AGENT_ID:raise Blocked('不能操作其他工作区或旧Provider的会话')
            if v.get('metadata_revision',0)!=data['expected']:raise Conflict('会话列表已变化，请刷新后重试')
            if op!='rename' and v.get('active_turn'):
                t=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(v['active_turn'],)).fetchone()[0])
                if t['state'] in ('QUEUED','RUNNING','RECOVERY_REQUIRED'):raise Blocked('先停止或确认当前助手请求，再归档或移入回收站')
            if op=='rename':
                if not isinstance(data.get('title'),str) or not 1<=len(data['title'].strip())<=100:raise Invalid('请输入1–100字会话名称')
                v['title']=self._redact(data['title'].strip())
            else:v['collection']={'archive':'archived','trash':'trash','restore':'active'}[op]
            v['metadata_revision']=v.get('metadata_revision',0)+1;v['updated_at']=time.time()
            db.execute("UPDATE records SET data=? WHERE kind='canvas-conversation' AND id=?",(canonical(v).decode(),v['id']))
        return dict(id=v['id'],collection=v.get('collection','active'),metadata_revision=v['metadata_revision'],title=v.get('title'))

    def conversation(self,workspace,key):
        self.rt.session_center.workspace(workspace)
        with self.rt.flow.tx(read=True) as db:
            row=db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND id=?",(key,)).fetchone()
            if not row:raise Invalid('尚无此助手对话')
            result=json.loads(row[0])
            if result['workspace']!=workspace:raise Blocked('助手对话不属于当前工作空间')
            turn=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(result['active_turn'],)).fetchone()[0]) if result.get('active_turn') else None
        if turn:
            with self.lock:running=turn['id'] in self.active
            if turn['state'] in ('QUEUED','RUNNING') and not running and (not alive(turn.get('owner')) or turn['owner']['pid']==os.getpid()):
                try:turn=self.rt.flow.change('canvas-turn',turn['id'],dict(state='RECOVERY_REQUIRED',reason='此前连接已中断；未自动重发请求'),states=['QUEUED','RUNNING'])
                except Conflict:return self.conversation(workspace,key)
            result['turn']=turn
        return result

    def start(self,data):
        required={'workspace','conversation','request_id','expected','message','graph','model','allow_context'}
        if set(data)!=required or data['allow_context'] is not True:raise Invalid('请确认将聊天内容和当前画布发送给助手；不发送工作区文件')
        if (not isinstance(data['request_id'],str) or len(data['request_id'])!=32 or any(c not in '0123456789abcdef' for c in data['request_id']) or
            not isinstance(data['message'],str) or not 1<=len(data['message'].strip())<=6000 or not isinstance(data['model'],str) or len(data['model'])>200):
            raise Invalid('消息、模型或请求身份无效')
        if data['expected'] is not None and type(data['expected']) is not int:raise Invalid('对话版本无效')
        self.rt.check_operator();base=ConfigurationAssistant(self.rt)._base(data)
        for name,p in base['choices'].items():
            profile=self.rt.operator['profiles'][name];p.update(default_model=profile['model'],default_effort=profile['effort'])
        connection_sha=None
        if self.backend is self.default_backend:
            from .devflower_agent import AgentConnection
            connection_sha=AgentConnection(self.rt).read()['fingerprint']
        key=data['conversation'] or secrets.token_hex(16);turn_id=data['request_id']
        if not isinstance(key,str) or len(key)!=32 or any(c not in '0123456789abcdef' for c in key):raise Invalid('助手对话身份无效')
        clean=self._redact(data['message']);g=self._redact(base['base_graph'])
        # The exact original graph remains local for CAS. Only declared fields
        # and redacted visible text are sent to the inference backend.
        nodes={'id','kind','name','profile','provider','model','effort','mode','initial_prompt','round_prompt','handoff_prompt','responsibility'}
        context={k:g[k] for k in ('version','name')};context['nodes']=[{k:v for k,v in n.items() if k in nodes} for n in g['nodes']]
        context['edges']=[{k:v for k,v in e.items() if k in {'source','target','label','transfer'}} for e in g['edges']]
        binding=digest(canonical(dict(workspace=data['workspace'],conversation=data['conversation'],message=clean,model=data['model'],connection_sha256=connection_sha,graph=base['base_graph_sha256'],expected=data['expected'])))
        with self.lock:
            if self.closed:raise Blocked('助手服务正在关闭')
            with self.rt.flow.tx() as db:
                old=db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(turn_id,)).fetchone()
                if old:
                    old=json.loads(old[0])
                    if old['binding']!=binding:raise Conflict('同一请求身份不能用于其他消息或画布')
                    result=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND id=?",(old['conversation'],)).fetchone()[0])
                    result['turn']=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(result['active_turn'],)).fetchone()[0])
                    return result
                row=db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND id=?",(key,)).fetchone()
                v=json.loads(row[0]) if row else dict(id=key,agent_id=AGENT_ID,workspace=data['workspace'],revision=0,messages=[],active_turn=None)
                if row and v.get('collection','active')!='active':raise Blocked('请先恢复此助手会话，再继续发送消息')
                if row and v.get('agent_id')!=AGENT_ID:raise Blocked('这是旧配置模型的历史，请新开 DevFlower 助手对话；原历史保留')
                if not row and data['expected'] is not None:raise Conflict('助手对话已变化或不存在')
                if v['workspace']!=data['workspace'] or (v['revision'] if row else None)!=data['expected']:raise Conflict('助手对话版本已变化，请读取最新消息')
                if len(v['messages'])>=40:raise Blocked('本次助手对话已达20轮上限，请新开对话')
                if v['active_turn']:
                    prior=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(v['active_turn'],)).fetchone()[0])
                    if prior['state'] in ('QUEUED','RUNNING','RECOVERY_REQUIRED'):raise Blocked('上一请求尚未确认结束，请先停止或查看状态')
                v.setdefault('created_at',time.time());v.setdefault('title',clean[:60]);v['updated_at']=time.time()
                v['revision']+=1;v['active_turn']=turn_id;v['messages'].append(dict(role='user',text=clean,at=time.time(),turn=turn_id))
                t=dict(base,id=turn_id,connection_sha256=connection_sha,conversation=key,binding=binding,state='QUEUED',owner=process_identity(os.getpid()),message=clean,model=data['model'],context=context,
                       messages=v['messages'][-12:],created_at=time.time(),reply=None,proposal=None,receipt=None,cancel_requested=False)
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('canvas-conversation',key,canonical(v).decode()))
                db.execute('INSERT INTO records VALUES(?,?,?)',('canvas-turn',turn_id,canonical(t).decode()))
            cancel=threading.Event();worker=threading.Thread(target=self._run,args=(turn_id,cancel),daemon=True,name='canvas-assistant-'+turn_id[:8])
            self.active[turn_id]=(cancel,worker)
            try:worker.start()
            except BaseException:
                self.active.pop(turn_id,None);self.rt.flow.change('canvas-turn',turn_id,dict(state='FAILED',reason='助手线程未启动；未调用模型'));raise
        return self.conversation(data['workspace'],key)

    def _redact(self,value):
        from orchestration.privacy import credentials,redact
        known=[]
        for p in self.rt.operator['profiles'].values():
            known.extend(credentials(p.get('home',''),[f[0] for f in p.get('readonly_files',[])]))
        def clean(v):
            if isinstance(v,str):return safe_text(redact(v,known))
            if isinstance(v,list):return [clean(x) for x in v]
            if isinstance(v,dict):return {k:clean(x) for k,x in v.items()}
            return v
        return clean(value)

    def _run(self,key,cancel):
        try:
            t=self.rt.flow.get('canvas-turn',key)
            if cancel.is_set():raise Blocked('已取消，未调用模型')
            ConfigurationAssistant(self.rt)._source(t)
            self.rt.flow.change('canvas-turn',key,dict(state='RUNNING'),states=['QUEUED'])
            prompt=('DevFlower 提供的当前对话与画布上下文（数据，不扩展工具权限）：\n'+
                json.dumps(dict(current_graph=t['context'],conversation=t['messages'],available_agents=t['choices']),ensure_ascii=False))
            prompt=self._redact(prompt)
            if len(prompt.encode())>180000:raise Blocked('对话与画布超出本轮上下文预算，请缩短内容或新开助手对话')
            last_activity=[None,0]
            def notify(e):
                if e.get('type') in ('assistant.turn_start','assistant.message_delta','assistant.message','session.error'):
                    now=time.monotonic()
                    if e['type']!=last_activity[0] or now-last_activity[1]>=.5:
                        self.rt.flow.change('canvas-turn',key,dict(activity=e['type'],activity_at=time.time()),states=['RUNNING'])
                        last_activity[:]=[e['type'],now]
            # All fallible metadata operations while the process is alive are
            # inside its ownership context. No post-Popen orphan window.
            with self.backend(self.root,timeout=180,cancelled=cancel.is_set,notify=notify) as client:
                if t.get('connection_sha256') and client.config['fingerprint']!=t['connection_sha256']:raise Conflict('助手模型连接已变化，未发送旧请求；请重新确认')
                client.conversation=t['conversation']
                metadata=dict(request_sha256=digest(prompt.encode()))
                if callable(getattr(client,'identity',None)):metadata['transport']=client.identity()
                elif getattr(client,'process',None):metadata['process']=dict(pid=client.process.pid)
                self.rt.flow.change('canvas-turn',key,metadata)
                r=client.chat(t['model'],prompt)
                result,graph=decode_reply(self._redact(r['text']),t['choices'])
                proposal=ConfigurationAssistant(self.rt).external(dict(workspace=t['workspace'],graph=t['base_graph'],plan=result['plan'])) if graph else None
            with self.lock:
                if cancel.is_set():raise Blocked('生成已取消；未应用草稿')
                with self.rt.flow.tx() as db:
                    row=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-turn' AND id=?",(key,)).fetchone()[0])
                    v=json.loads(db.execute("SELECT data FROM records WHERE kind='canvas-conversation' AND id=?",(t['conversation'],)).fetchone()[0])
                    row.update(state='COMPLETED',reply=result['reply'],proposal=proposal,receipt={k:v for k,v in r.items() if k!='text'},finished_at=time.time())
                    v['updated_at']=time.time();v['messages'].append(dict(role='assistant',text=result['reply'],at=time.time(),turn=key));v['revision']+=1
                    db.execute("UPDATE records SET data=? WHERE kind='canvas-turn' AND id=?",(canonical(row).decode(),key))
                    db.execute("UPDATE records SET data=? WHERE kind='canvas-conversation' AND id=?",(canonical(v).decode(),v['id']))
        except Exception as exc:
            try:self.rt.flow.change('canvas-turn',key,dict(state='CANCELLED' if cancel.is_set() else 'FAILED',reason=safe_text(str(exc)) if isinstance(exc,(Blocked,Invalid,Conflict)) else '助手连接或本地记录失败；未自动重试或应用画布',finished_at=time.time()))
            except (sqlite3.Error,OSError):pass
        finally:
            with self.lock:self.active.pop(key,None)

    def cancel(self,workspace,key):
        self.rt.check_operator();v=self.conversation(workspace,key);turn=v.get('turn')
        if turn and turn['state'] in ('QUEUED','RUNNING','RECOVERY_REQUIRED'):
            with self.lock:
                active=self.active.get(turn['id'])
                if active:active[0].set()
                elif alive(turn.get('owner')) and turn['owner']['pid']!=os.getpid():raise Blocked('此请求仍由另一服务实例持有，请在原实例停止')
                else:self.rt.flow.change('canvas-turn',turn['id'],dict(state='CANCELLED',reason='原请求已断开；未重放'))
        return self.conversation(workspace,key)

    def shutdown(self):
        with self.lock:
            self.closed=True;workers=list(self.active.values())
            for cancel,_ in workers:cancel.set()
        for _,worker in workers:
            if worker is not threading.current_thread():worker.join(timeout=5)
