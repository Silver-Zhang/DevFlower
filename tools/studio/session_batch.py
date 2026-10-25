"""Fixed-membership, recoverable workflow cleanup. Native history stays untouched."""
from contextlib import ExitStack
from pathlib import Path
import json,time
from orchestration.common import canonical,digest,Blocked
from orchestration.store import lease
from .graph import Invalid
from .server import Conflict


class SessionBatch:
    def __init__(self,manager):self.manager=manager;self.rt=manager.rt

    def members(self,workspace,workflow):
        self.rt.session_center.workspace(workspace)
        rows={s['handle']:s for view in ('active','archived') for s in self.manager.listing(workspace,view)['sessions']
              if s.get('workflow_group_id',s.get('workflow_id'))==workflow}
        if not rows:raise Invalid('此工作流没有可清理的会话')
        if len(rows)>200:raise Invalid('一次最多清理200个会话')
        result=[]
        for handle,s in sorted(rows.items()):
            _,row=self.rt.session_center.resolve(workspace,handle)
            b,reason=self.rt.session_center._binding(row,self.rt.project(workspace))
            if not b or self.manager.busy(b,row['native']['session_id']):raise Blocked(reason or '有会话正在运行或占用未确认，请先结束后再清理')
            related=self.manager.related_activations(workspace,row)
            groups={self.rt.flow.get('run',a['run']).get('draft_id') or 'run:'+a['run'] for a,_ in related}
            if len(groups)>1:raise Blocked('会话同时关联其他工作流，请单独处理以免误移除')
            try:meta=self.rt.flow.get('session-meta',handle)
            except Invalid:meta=None
            if meta and meta.get('native_archived'):raise Blocked('包含原生归档会话，请先恢复后单独处理')
            result.append(dict(handle=handle,title=s['title'],home=b['home'],session_id=row['native']['session_id'],
                native_sha256=row['native']['sha256'],meta_sha256=digest(canonical(meta))))
        return result

    def prepare(self,data):
        if set(data)!={'workspace','workflow'}:raise Invalid('批量清理需要当前工作区和工作流')
        self.rt.check_operator();rows=self.members(data['workspace'],data['workflow'])
        return self.rt.db.prepare(dict(purpose='workflow-session-trash',**data,members=rows,
            human_questions=[f'将此工作流列表中的{len(rows)}个会话移入可恢复回收站；不删除原生历史、不停止进程、不删除运行记录。']))

    def confirm(self,id,sha256,answer):
        self.rt.check_operator()
        with self.rt.lock,ExitStack() as held:
            v,_=self.rt.db.consume(id,sha256,[answer])
            if v.get('purpose')!='workflow-session-trash':raise Blocked('不是工作流会话清理确认')
            now=self.members(v['workspace'],v['workflow'])
            if digest(canonical(now))!=digest(canonical(v['members'])):raise Conflict('会话成员、历史或分类已变化；没有批量修改，请重新核对')
            homes=sorted({r['home'] for r in now})
            for home in homes:held.enter_context(lease(Path(home).parent/(digest(str(Path(home).resolve()).encode())+'.session.lock')))
            # A native turn may have completed between discovery and taking
            # its lease. Re-read exact native identities under all held leases.
            for r in now:
                _,current=self.rt.session_center.resolve(v['workspace'],r['handle'])
                if (current['native']['sha256'],current['native']['session_id'],current['profile']['home'])!=(r['native_sha256'],r['session_id'],r['home']):
                    raise Conflict('原生历史在核对后变化；没有批量修改，请重新确认')
                if self.manager.native_process_busy(r['home'],r['session_id']):
                    raise Blocked('会话在核对后被外部终端占用；整批未修改')
            if any(o.get('binding',{}).get('home') in homes and o['state'] not in ('COMPLETED','FAILED','CANCELLED')
                   for o in self.rt.db.listing('session-operation',limit=None)):raise Blocked('有新会话操作待执行；没有批量修改')
            with self.rt.flow.tx() as db:
                for r in now:
                    raw=db.execute("SELECT data FROM records WHERE kind='session-meta' AND id=?",(r['handle'],)).fetchone();meta=json.loads(raw[0]) if raw else None
                    if digest(canonical(meta))!=r['meta_sha256']:raise Conflict('会话分类已被其他操作修改；整批未生效')
                    meta=dict(meta or dict(id=r['handle'],workspace=v['workspace']),collection='trash',updated_at=time.time())
                    db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('session-meta',r['handle'],canonical(meta).decode()))
        return dict(trashed=len(now),handles=[r['handle'] for r in now],native_modified=False,permanently_deleted=False)
