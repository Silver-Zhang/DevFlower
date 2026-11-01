"""Local, versioned reusable designs; no session, receipt or run authority."""
import copy,secrets,time
from pathlib import Path
from orchestration.common import canonical,digest,Blocked
from .graph import Invalid
from .server import Conflict
from .workbench_graph import validate_graph


class WorkflowLibrary:
    def __init__(self,runtime):self.rt=runtime

    def listing(self,workspace):
        self.rt.session_center.workspace(workspace)
        with self.rt.project_files(workspace).library() as store:
            return dict(templates=[r for r in store.list('workflow-template') if r['workspace']==workspace])

    def save(self,data):
        if set(data)-{'workspace','graph','name','description','id','expected'} or not {'workspace','graph','name'}<=set(data):raise Invalid('模板字段无效')
        self.rt.session_center.workspace(data['workspace'])
        if not isinstance(data['name'],str):raise Invalid('模板名称无效')
        name=data['name'].strip();description=data.get('description','')
        if not 1<=len(name)<=100 or not isinstance(description,str) or len(description)>1000:raise Invalid('请填写模板名称（1–100字）和简短说明')
        from .canvas_assistant import redact_visible
        # Same explicit credential redaction as visible assistant context.
        graph=validate_graph(redact_visible(self.rt,data['graph']))
        graph=copy.deepcopy(graph)
        def clean(g):
            for node in g['nodes']:
                keep={'id','kind','name','position','provider','profile','model','effort','mode','permission','subagents','timeout','responsibility','background','initial_prompt','round_prompt','handoff_prompt','task_context','tools','input_contract','output_contract','strategy','test','missing_policy','routes','default_route','classifier','max_rounds','until','body','external_profile'}
                for key in set(node)-keep:node.pop(key)
                if node['kind']=='agent':node['session']='new'
                if node['kind']=='loop':clean(node['body'])
            for edge in g['edges']:
                for key in set(edge)-{'id','source','target','transfer','when','label','max_chars','field','route'}:edge.pop(key)
        clean(graph);graph['name']=name;graph=validate_graph(graph)
        key=data.get('id') or secrets.token_hex(16)
        if not isinstance(key,str) or len(key)>80:raise Invalid('模板身份无效')
        with self.rt.project_files(data['workspace']).library() as store, store.tx() as db:
            import json
            old=db.execute("SELECT data FROM records WHERE kind='workflow-template' AND id=?",(key,)).fetchone();old=json.loads(old[0]) if old else None
            if (old or {}).get('workspace',data['workspace'])!=data['workspace']:raise Blocked('模板不属于当前工作区')
            if (old or {}).get('revision')!=data.get('expected'):raise Conflict('模板已变化，请刷新后保存')
            row=dict(id=key,workspace=data['workspace'],name=name,description=redact_visible(self.rt,description),graph=graph,
                favorite=True,collection='active',created_at=(old or {}).get('created_at',time.time()),updated_at=time.time(),revision=secrets.token_hex(16),graph_sha256=digest(canonical(graph)))
            db.execute('INSERT INTO records VALUES(?,?,?)',('workflow-template-version',key+':'+row['revision'],canonical(row).decode()))
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('workflow-template',key,canonical(row).decode()))
        self.rt.flow.put('workflow-template', key, row)
        self.rt.flow.put('workflow-template-version', key+':'+row['revision'], row)
        return row

    def manage(self,data):
        if set(data)!={'workspace','id','expected','operation'} or data['operation'] not in ('favorite','unfavorite','trash','restore'):raise Invalid('模板操作无效')
        self.rt.session_center.workspace(data['workspace'])
        import json
        with self.rt.project_files(data['workspace']).library() as store, store.tx() as db:
            raw=db.execute("SELECT data FROM records WHERE kind='workflow-template' AND id=?",(data['id'],)).fetchone()
            if not raw:raise Invalid('模板不存在')
            row=json.loads(raw[0])
            if row['workspace']!=data['workspace']:raise Blocked('模板不属于当前工作区')
            if row['revision']!=data['expected']:raise Conflict('模板已变化，请刷新后重试')
            op=data['operation'];row.update(revision=secrets.token_hex(16),updated_at=time.time())
            if op in ('favorite','unfavorite'):row['favorite']=op=='favorite'
            else:row['collection']='trash' if op=='trash' else 'active'
            db.execute("UPDATE records SET data=? WHERE kind='workflow-template' AND id=?",(canonical(row).decode(),row['id']))
        self.rt.flow.put('workflow-template', row['id'], row)
        return row
