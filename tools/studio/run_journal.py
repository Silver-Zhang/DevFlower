"""Read-only whole-run conversation projection from persisted Runtime evidence."""
from pathlib import Path
from urllib.parse import unquote,urlsplit
import math,re
from orchestration.common import Blocked
from orchestration.privacy import credentials,redact
from .agent_sessions import safe_text
from .graph import Invalid
from .session_sources import opened
from .workbench_data import public_data


def file_reference(rt,workspace,reference):
    root=Path(rt.session_center.workspace(workspace)['path'])
    if not isinstance(reference,str) or len(reference)>4096:raise Invalid('文件引用无效')
    value=unquote(reference)
    if urlsplit(value).scheme or '?' in value or '\x00' in value:raise Invalid('只支持工作区内的普通文件引用')
    line=1
    m=re.search(r'(?:#L|:)([1-9][0-9]{0,6})(?::[1-9][0-9]{0,6})?$',value)
    if m:line=int(m[1]);value=value[:m.start()]
    p=Path(value)
    if '..' in p.parts:raise Blocked('不能打开工作区以外的引用')
    p=p if p.is_absolute() else root/p
    if not p.is_relative_to(root) or any(part.startswith('.') or re.search(r'(?i)(secret|token|password|api.?key|receipt)',part) for part in p.relative_to(root).parts):raise Blocked('此文件引用位于工作区外或包含私有信息')
    if any(p.is_relative_to(x) for x in [*rt.protected,rt.storage,rt.native]):raise Blocked('不能打开受保护的运行数据')
    try:
        with opened(str(p)):pass
    except (OSError,ValueError):raise Invalid('文件不存在、不可访问或不是安全的常规文件') from None
    return dict(path=str(p),line=line,editor='vscode',contents_read=False)


def run_conversation(manager,workspace,run):
    rt=manager.rt;rt.session_center.workspace(workspace)
    snapshot=rt.snapshot(run);flow=snapshot['run']
    if flow['workspace']!=workspace:raise Blocked('运行不属于当前工作区')
    extra=[s for p in rt.operator['profiles'].values() for s,_ in p.get('readonly_files',[])]
    secrets=credentials(rt.native,extra)
    def text(value):return safe_text(redact(value,secrets)) if isinstance(value,str) else ''
    def stamp(value):return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None
    entries=[];notices=[]
    def add(key,kind,at,name,**data):entries.append(dict(id=key,kind=kind,at=stamp(at),name=text(name),**data))
    add('task','user',flow.get('created_at'),'你',text=text(flow.get('task')))
    names={a['node']:a.get('name',a['node']) for a in snapshot['nodes']}
    for a in snapshot['nodes']:
        if a.get('kind')!='agent':continue
        value=manager.node_conversation(workspace,run,a['id'])
        common=dict(node=a['node'],activation=a['id'],round=a.get('iteration_path',[]),provider=a.get('provider'),state=value['state'])
        add(a['id']+':input','input',a.get('started_at'),a.get('name',a['node']),sections=value['request_sections'],**common)
        answer=value.get('saved_answer')
        if answer:add(a['id']+':answer','assistant',a.get('finished_at'),a.get('name',a['node']),text=answer,session=value.get('session'),**common)
        elif value['state'] in ('STARTING','RUNNING','UNKNOWN'):
            for i,m in enumerate(value['messages']):
                if m['role']=='assistant':add(a['id']+':visible:'+str(i),'visible',None,a.get('name',a['node']),text=text(m['text']),**common)
        elif value['state']=='COMPLETED':notices.append(text(a.get('name',a['node']))+'：没有可核验的完成回答，未用协议内容补造')
        for i,item in enumerate(value['activity']):
            add(a['id']+':tool:'+str(i),'tool',item.get('started_at'),a.get('name',a['node']),tool=item,**common)
        for i,summary in enumerate(value['summaries']):
            add(a['id']+':summary:'+str(i),'summary',None,a.get('name',a['node']),text=text(summary.get('text')),**common)
        if value['history_status']=='unavailable':notices.append(text(a.get('name',a['node']))+'：部分原生工具/对话历史不可读；已保存回答单独保留')
    cursor=0;more=True
    for _ in range(5):
        batch=rt.flow.events(run,cursor)
        for event in batch:
            cursor=event['seq'];kind=event.get('kind','unknown');common=dict(node=event.get('node'),round=event.get('iteration_path',[]),event_kind=kind)
            if kind in ('handoff-sent','handoff-received'):
                add('event:'+str(cursor),'handoff',event.get('at'),names.get(event.get('source'),event.get('source','工作流')),target=text(names.get(event.get('node'),event.get('node',''))),content=public_data(event.get('content'),secrets),**common)
            elif kind not in ('handoff-ready',):add('event:'+str(cursor),'event',event.get('at'),names.get(event.get('node'),'工作流'),text=text(event.get('reason','')),**common)
        more=len(batch)==1000
        if not more:break
    if more:notices.append('仅展示前5000项事件，完整持久记录保留在本机')
    # Equal timestamps have a stable identity order. Unknown clocks never receive invented dates.
    entries.sort(key=lambda e:(e['at'] is None,e['at'] or 0,e['id']))
    return dict(workspace=workspace,run=run,name=text(flow['graph']['name']),task=text(flow.get('task')),state=snapshot['state'],
        draft_id=flow.get('draft_id'),candidate_sha256=flow.get('candidate_sha256','unknown'),viewer_candidate_sha256=rt.candidate_sha256,entries=entries,notices=notices,more=more)
