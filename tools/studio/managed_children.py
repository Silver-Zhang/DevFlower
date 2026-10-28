"""Host-only managed readonly child runner; no browser-facing API.

The authenticated endpoint and parent lifecycle integration are not yet wired;
the capability remains disabled in the catalog until those checks exist.
"""
import copy
import json
import os
from pathlib import Path
import secrets
import select
import socket
import stat
import struct
import threading
import time
import uuid

from orchestration.common import Blocked,canonical,digest,workspace_hash
from orchestration.process import group_alive,stop
from orchestration.store import alive,process_identity
from .session_sources import opened
from .local_codex_adapter import LocalCodex
from .native_local import session_lease
from .workbench_scheduler import Journal,source_lease

CHILD_TOKEN=object()

class ChildJournal(Journal):
    def __init__(self,scheduler,activation,owner):
        super().__init__(scheduler,activation);self.owner=owner;self.process_started=False;self._managed_child_token=CHILD_TOKEN
    def get(self,run):
        row=super().get(run)
        if self.owner.closed.is_set() or not alive(self.owner.parent) or time.monotonic()>=self.owner.deadline:
            return {'data':dict(row['data'],cancel_requested=True)}
        return row
    def check_before_spawn(self):
        if self.get(self.s.run)['data'].get('cancel_requested'):raise Blocked('父任务已结束；未发送子任务')
        self.owner.check_source()
    def finish(self,key,status,**updates):
        row=self.s.db.get('native-step',key)
        if row.get('kind')=='PROCESS' and status=='RUNNING':
            record=updates.get('process');actual=process_identity(record.get('pid')) if isinstance(record,dict) and type(record.get('pid')) is int else None
            fields=('host','boot_id','pid','start','pgid','sid')
            if not actual or any(actual.get(k)!=record.get(k) for k in fields) or not alive(record):raise Blocked('子进程身份未确认')
            with self.owner.lock:self.owner.proofs[self.key]={k:record[k] for k in fields}
        result=super().finish(key,status,**updates)
        if row.get('kind')=='PROCESS' and status=='RUNNING':
            child=self.s.db.get('activation',self.key)
            self.owner.journal.native_tool_event(dict(event='item.started',id='managed:'+self.key,tool_kind='managed_child',command='只读子任务 '+str(child['slot']),status='running'))
        elif row.get('kind')=='AGENT_REQUEST' and status=='AGENT_FAILED' and self.process_started:
            child=self.s.db.get('activation',self.key)
            self.owner.journal.native_tool_event(dict(event='item.updated',id='managed:'+self.key,tool_kind='managed_child',command='只读子任务 '+str(child['slot']),status='unknown'))
        return result


class OwnedChildren:
    def __init__(self,journal,binding,cwd,parent,deadline):
        if not isinstance(journal,Journal) or not alive(parent) or not isinstance(cwd,str) or type(deadline) not in (float,int) or deadline<=time.monotonic():
            raise Blocked('缺少有效父运行、父进程或时间预算')
        self.journal=journal;self.scheduler=journal.s;self.rt=self.scheduler.rt;self.db=self.scheduler.db
        activation=self.db.get('activation',journal.key);chat=self.db.get('node-session',activation['chat_key'])
        original=chat['binding']
        if (chat.get('cwd')!=cwd or set(binding)-set(original)-{'native_interaction','native_protected','_metadata_guard'} or any(binding.get(k)!=v for k,v in original.items()) or
            binding.get('provider')!='codex' or binding.get('execution_backend')!='native-local' or
            binding.get('native_interaction')!='ordinary' or binding.get('role')!='reviewer' or
            binding.get('execution_mode') not in ('Ask','Plan') or binding.get('subagents')!='readonly' or
            binding.get('_metadata_guard')!=str(self.rt.storage)):
            raise Blocked('父节点没有匹配的只读原生绑定')
        from .native_codex_exec import protected_paths
        if binding.get('native_protected')!=protected_paths(self.rt,binding):raise Blocked('受保护路径与宿主绑定不一致')
        run=self.db.get('run',self.scheduler.run);created=run.get('created_at');seconds=run.get('graph',{}).get('budget',{}).get('seconds',3600)
        if type(created) not in (int,float) or type(seconds) not in (int,float) or not 0<=time.time()-created<seconds:
            raise Blocked('父运行时间预算不可用')
        self.binding=copy.deepcopy(binding);self.cwd=cwd;self.parent=dict(parent)
        self.deadline=min(deadline,time.monotonic()+seconds-(time.time()-created))
        self.closed=threading.Event();self.lock=threading.Lock();self.active={};self.proofs={};self.unresolved=False

    def check_source(self):
        run=self.db.get('run',self.scheduler.run)
        if run.get('cancel_requested') or run.get('state')!='RUNNING' or self.closed.is_set() or not alive(self.parent) or time.monotonic()>=self.deadline:
            raise Blocked('父任务已停止或时间预算已用完')
        activation=self.db.get('activation',self.journal.key);source=activation.get('source_at_request') or {};view=run.get('source_view')
        actual=self.rt.source_view_hash(self.cwd,view) if view else workspace_hash(self.cwd)
        if actual!=source.get('sha256'):raise Blocked('子任务启动前源码已变化；未发送子任务')
        return actual

    def call(self,slot,task):
        from .managed_child_bridge import arguments
        arguments(dict(slot=slot,task=task))
        with self.lock:
            if self.closed.is_set() or time.monotonic()>=self.deadline or not alive(self.parent):raise Blocked('父任务已结束；未启动子任务')
            row,created=self.journal.reserve_managed_child(slot,task,self.parent,with_created=True);key=row['id']
            if not created:return row
            # Claim once, atomically, even if another host request races.
            with self.db.tx() as db:
                from .managed_child_reservation import transition
                item=db.execute("SELECT data FROM records WHERE kind='managed-child' AND id=?",(key,)).fetchone()
                current=json.loads(item[0])
                if current['state']!='RESERVED':return current
                transition(db,current,'STARTING')
            done=threading.Event();self.active[key]=done
        # A loop reuses the parent node name across rounds; include the full
        # activation identity so each round has its own native child session.
        child_node=row['node']+'/managed-child-'+self.journal.key+'-'+str(slot)
        child_id=digest(canonical([self.scheduler.run,child_node,row['iteration_path']]));chat_id=digest(canonical([self.scheduler.run,child_node]))
        child=copy.deepcopy(self.binding)
        child.update(session_id=None,subagents='disabled',skills=[],skill_ids=[],role='reviewer',conversation_format='text')
        for name in ('expected_native_turn_id','require_atomic_resume','admission_probe'):child.pop(name,None)
        process_started=False
        try:
            if self.closed.is_set() or not alive(self.parent) or time.monotonic()>=self.deadline:raise Blocked('父任务已结束；未启动子任务')
            run=self.db.get('run',self.scheduler.run)
            parent=self.db.get('activation',self.journal.key);name=str(parent.get('name') or row['node'])+' · 子任务'+str(slot)
            activation=dict(id=child_id,run=self.scheduler.run,node=child_node,name=name,provider='codex',kind='agent',state='STARTING',started_at=time.time(),
                            iteration_path=row['iteration_path'],chat_key=chat_id,parent_activation=self.journal.key,parent_session=row['parent_session'],slot=slot,source_at_request=row['source_at_request'],request_sections=[dict(title='每轮任务',text=task)])
            chat=dict(id=chat_id,run=self.scheduler.run,workspace=run['workspace'],node=activation['node'],name=name,cwd=self.cwd,binding=child,initialized=False,parent_activation=self.journal.key,slot=slot)
            with self.db.tx() as db:
                db.execute("INSERT INTO records VALUES('activation',?,?)",(child_id,canonical(activation).decode()))
                db.execute("INSERT INTO records VALUES('node-session',?,?)",(chat_id,canonical(chat).decode()))
                current['child_activation']=child_id;current['child_chat']=chat_id
                db.execute("UPDATE records SET data=? WHERE kind='managed-child' AND id=?",(canonical(current).decode(),key))
            artifact=Path(self.rt.storage)/'managed-child-artifacts'/key;artifact.mkdir(mode=0o700,parents=True,exist_ok=False)
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise Blocked('父任务时间预算已用完')
            journal=ChildJournal(self.scheduler,child_id,self)
            limits=dict(step_timeout_seconds=remaining,max_log_bytes=1_000_000)
            with source_lease(self.rt.source_lock(self.cwd),False),session_lease(child):
                view=run.get('source_view')
                source_sha=self.rt.source_view_hash(self.cwd,view) if view else workspace_hash(self.cwd)
                if source_sha!=row['source_at_request']['sha256']:
                    raise Blocked('子任务启动前源码已变化；未发送子任务')
                result=LocalCodex(child,limits).start(task,self.cwd,artifact,store=journal,run=self.scheduler.run)
                try:self.check_source()
                except Blocked:raise Blocked('子任务调用已经发生，源码核验失败；未批准且未重发') from None
            process_started=bool(getattr(journal,'process_started',False))
            session=result.get('session_id')
            try:
                if str(uuid.UUID(session))!=session or session==row['parent_session']:raise ValueError()
            except (TypeError,ValueError,AttributeError):raise Blocked('子会话身份未确认') from None
            actual=self.db.get('activation',child_id)
            if not process_started or not actual.get('process') or actual.get('observed_session')!=session:
                raise Blocked('子任务进程或会话记录未确认')
            process=result.get('process');report=result.get('report')
            if (not isinstance(process,dict) or process.get('exit')!=0 or any(process.get(k) is not False for k in ('timeout','cancelled','overflow')) or
                result.get('requested_model')!=child['model'] or result.get('requested_effort')!=child['effort'] or not isinstance(report,dict) or not isinstance(report.get('reply'),str)):
                raise Blocked('子任务完成回执未确认')
            receipt={k:result.get(k) for k in ('session_id','requested_model','requested_effort','resolved_model','resolved_effort','process','permission_mode','raw_report_sha256','usage','native_turn_id')}
            with self.db.tx() as db:
                for raw, in db.execute("SELECT data FROM records WHERE kind='managed-child'"):
                    prior=json.loads(raw)
                    if prior.get('id')!=key and prior.get('activation')==self.journal.key and prior.get('session_id')==session:
                        raise Blocked('子任务会话身份与其他槽位重复')
                actual.update(state='COMPLETED',output=dict(answer=report['reply'],receipt=receipt),receipt=receipt,native_session=session,finished_at=time.time())
                current.update(result=result.get('report'),session_id=session,usage=result.get('usage') or 'unknown')
                db.execute("UPDATE records SET data=? WHERE kind='activation' AND id=?",(canonical(actual).decode(),child_id))
                transition(db,current,'COMPLETED')
            self.journal.native_tool_event(dict(event='item.completed',id='managed:'+child_id,tool_kind='managed_child',command='只读子任务 '+str(slot),output='子任务已完成 · 会话 '+session,status='completed'))
            return current
        except Exception:
            # A publication failure after Popen is never interpreted as a safe
            # retry. A missing process record is likewise conservatively unknown.
            started=process_started or bool(locals().get('journal') and getattr(journal,'process_started',False))
            try:
                with self.db.tx() as db:
                    raw=db.execute("SELECT data FROM records WHERE kind='managed-child' AND id=?",(key,)).fetchone()
                    value=json.loads(raw[0]);value['reason']='子任务未完成；不会自动重发'
                    transition(db,value,'RECOVERY_REQUIRED' if started else 'FAILED')
            except Exception:pass
            raise
        finally:
            done.set()
            with self.lock:self.active.pop(key,None)

    def close(self,timeout=3):
        self.closed.set()
        with self.lock:
            active=list(self.active.items())
            if active:self.unresolved=True
        # An active child at parent closure cannot count as a completed parent,
        # even if cancellation later succeeds.
        unresolved=self.unresolved
        for key,_ in active:
            try:
                row=self.db.get('managed-child',key);activation=self.db.get('activation',row['child_activation']);process=activation.get('process')
                with self.lock:proof=self.proofs.get(row['child_activation'])
                fields=('host','boot_id','pid','start','pgid','sid')
                if not proof or not isinstance(process,dict) or any(process.get(k)!=proof.get(k) for k in fields):unresolved=True;continue
                if alive(proof) and group_alive(proof):stop(proof)
            except Exception:unresolved=True
        end=time.monotonic()+timeout
        for _,event in active:event.wait(max(0,end-time.monotonic()))
        for key,_ in active:
            try:
                row=self.db.get('managed-child',key)
                with self.lock:proof=self.proofs.get(row.get('child_activation'))
                if proof and group_alive(proof):unresolved=True
            except Exception:unresolved=True
        if unresolved or not all(event.is_set() for _,event in active):
            with self.lock:self.unresolved=True
        return dict(status='UNRESOLVED' if self.unresolved else 'closed')


class ManagedContext:
    """One parent invocation: endpoint exists before its real process exists."""
    def __init__(self,journal,binding,cwd,deadline):
        if type(journal) is not Journal or deadline<=time.monotonic():raise Blocked('缺少工作流宿主上下文')
        self.journal=journal;self.rt=journal.s.rt;self.binding=copy.deepcopy(binding);self.cwd=str(cwd);self.deadline=deadline
        self.parent=None;self.owner=None;self.closed=threading.Event();self.lock=threading.Lock();self.close_lock=threading.Lock();self.outcome=None
        activation=journal.s.db.get('activation',journal.key);chat=journal.s.db.get('node-session',activation['chat_key'])
        self.baseline=copy.deepcopy(chat['binding'])
        self.chat_key=activation['chat_key'];self.node=activation['node'];self.workspace=chat.get('workspace')
        run=journal.s.db.get('run',journal.s.run)
        if (run.get('state')!='RUNNING' or run.get('interaction')!='click' or run.get('cancel_requested') or
            activation.get('run')!=journal.s.run or activation.get('kind')!='agent' or activation.get('state') not in ('STARTING','RUNNING') or
            chat.get('run')!=journal.s.run or chat.get('workspace')!=run.get('workspace') or chat.get('node')!=activation.get('node') or
            chat.get('cwd')!=self.cwd or set(binding)-set(self.baseline)-{'native_interaction','native_protected','_metadata_guard'} or
            any(binding.get(k)!=v for k,v in self.baseline.items()) or binding.get('_metadata_guard')!=str(self.rt.storage)):
            raise Blocked('父工作流或冻结绑定不匹配；未启动原生请求')
        from .native_codex_exec import protected_paths
        if binding.get('native_protected')!=protected_paths(self.rt,binding):raise Blocked('父任务保护范围不匹配')
        from .native_codex_rpc import metadata_guard
        with metadata_guard(str(self.rt.storage),deadline-time.monotonic(),lambda:self.closed.is_set()):
            self.endpoint=OwnedEndpoint(self)
        self.path=self.endpoint.path
    def bind_process(self,record):
        with self.lock:
            activation=self.journal.s.db.get('activation',self.journal.key)
            if self.parent is not None or self.closed.is_set() or not isinstance(record,dict) or activation.get('process')!=record or not alive(record):
                raise Blocked('父进程身份发布不一致')
            self.parent=copy.deepcopy(record)
    def call(self,slot,task):
        wait_until=min(self.deadline,time.monotonic()+3)
        while True:
            with self.lock:
                if self.closed.is_set() or not self.parent or not alive(self.parent):raise Blocked('父进程尚未就绪或已结束')
                if time.monotonic()>=wait_until:raise Blocked('父原生会话尚未完成发布；未启动子任务')
                with self.journal.s.db.tx(read=True) as db:
                    def read(kind,key):
                        row=db.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,key)).fetchone()
                        if not row:raise Blocked('父运行记录已变化')
                        return json.loads(row[0])
                    run=read('run',self.journal.s.run);activation=read('activation',self.journal.key);chat=read('node-session',self.chat_key)
                if (run.get('state')!='RUNNING' or run.get('cancel_requested') or run.get('workspace')!=self.workspace or activation.get('process')!=self.parent or activation.get('run')!=self.journal.s.run or activation.get('state')!='RUNNING' or activation.get('node')!=self.node or activation.get('chat_key')!=self.chat_key or chat.get('workspace')!=self.workspace or chat.get('run')!=self.journal.s.run or chat.get('node')!=self.node or chat.get('cwd')!=self.cwd):raise Blocked('父运行已停止或身份已变化')
                binding=chat.get('binding');sid=activation.get('observed_session');base_sid=self.baseline.get('session_id')
                if not isinstance(binding,dict) or {k:v for k,v in binding.items() if k!='session_id'}!={k:v for k,v in self.baseline.items() if k!='session_id'}:raise Blocked('父会话冻结绑定已变化')
                current=binding.get('session_id')
                if base_sid and (current!=base_sid or sid is not None and sid!=base_sid):raise Blocked('父恢复会话身份不匹配')
                if sid is not None:
                    try:
                        if not isinstance(sid,str) or str(uuid.UUID(sid))!=sid:raise ValueError()
                    except ValueError:raise Blocked('父原生会话身份无效') from None
                if current not in (base_sid,sid) or sid is None and current!=base_sid:raise Blocked('父会话绑定已变化')
                if sid and current==sid:
                    if time.monotonic()>=wait_until:raise Blocked('父原生会话尚未完成发布；未启动子任务')
                    if self.owner is None:
                        effective=copy.deepcopy(self.binding);effective['session_id']=sid
                        self.owner=OwnedChildren(self.journal,effective,self.cwd,self.parent,self.deadline)
                    owner=self.owner
                    break
            left=wait_until-time.monotonic()
            if left<=0:raise Blocked('父原生会话尚未完成发布；未启动子任务')
            self.closed.wait(min(.05,left))
        return owner.call(slot,task)
    def stop_children(self):
        self.closed.set()
        with self.lock:owner=self.owner
        return owner.close() if owner else dict(status='closed')
    def close(self):
        with self.close_lock:
            if self.outcome is None:self.outcome=self.endpoint.close()
            return self.outcome


def _descendant(pid,parent):
    """Check the peer's live process chain; never trust a pid in its message."""
    first=process_identity(pid)
    if not first or not alive(parent):return False
    current=pid
    try:
        for _ in range(32):
            if current==parent['pid']:
                return alive(parent) and alive(first)
            row=process_identity(current)
            if not row or row['state']=='Z':return False
            fields=Path('/proc/'+str(current)+'/stat').read_text().rsplit(')',1)[1].split()
            up=int(fields[1])
            if up<=1 or up==current or not alive(row):return False
            current=up
    except (OSError,ValueError,IndexError):return False
    return False

def public_result(row):
    """Expose only a bounded child result and opaque record identity."""
    if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row.get('state') not in ('RESERVED','STARTING','COMPLETED','FAILED','RECOVERY_REQUIRED'):
        raise Blocked('子任务状态不可确认')
    result=dict(child_id=row['id'],state=row['state'],slot=row.get('slot'))
    if row.get('session_id'):result['session_id']=row['session_id']
    if row['state']=='COMPLETED':
        report=row.get('result');text=report.get('reply') if isinstance(report,dict) else None
        if not isinstance(text,str):raise Blocked('子任务可见结果不可确认')
        raw=text.encode('utf-8');result['text']=raw[:12000].decode('utf-8','ignore');result['complete']=len(raw)<=12000
        if not result['complete']:result['notice']='结果较长；此处仅展示节选，完整结果保留在本机记录。'
    return result


class OwnedEndpoint:
    """Private descriptor and authenticated abstract socket for one parent."""
    def __init__(self,owner):
        from .managed_child_bridge import ADDRESS
        self.owner=owner;self.closed=threading.Event();self.clients=set();self.lock=threading.Lock()
        self.listener=None;self.rootfd=self.filefd=None;self.root_identity=None;self.cleanup_info=None;self.secret=secrets.token_urlsafe(32)
        try:
            from .native_capability_cache import _directory
            storage=Path(owner.rt.storage)
            with opened(storage,directory=True) as root:
                _directory(root,scope=True)
                st=os.fstat(root)
                if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Blocked('子任务存储目录不安全')
                try:os.mkdir('managed-child-endpoints',0o700,dir_fd=root)
                except FileExistsError:pass
                base=os.open('managed-child-endpoints',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root)
                try:
                    _directory(base,scope=True)
                    st=os.fstat(base)
                    if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Blocked('子任务私有目录不安全')
                    name=secrets.token_hex(16);os.mkdir(name,0o700,dir_fd=base)
                    self.root=storage/'managed-child-endpoints'/name;self.path=self.root/'endpoint.json'
                    self.rootfd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=base)
                    _directory(self.rootfd,scope=True)
                    st=os.fstat(self.rootfd);self.root_identity=(st.st_dev,st.st_ino)
                    if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Blocked('子任务端点目录不安全')
                finally:os.close(base)
            address='df-child-'+secrets.token_hex(16)
            if not ADDRESS.fullmatch(address):raise Blocked('子任务地址无效')
            self.listener=socket.socket(socket.AF_UNIX);self.listener.bind('\0'+address);self.listener.listen(4);self.listener.settimeout(.2)
            from .managed_child_bridge import remaining
            remaining(owner.deadline)
            data=canonical(dict(version=2,socket=address,token=self.secret,server_pid=os.getpid(),deadline=owner.deadline))
            self.filefd=os.open('endpoint.json',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.rootfd)
            if os.write(self.filefd,data)!=len(data):raise OSError('short descriptor write')
            with opened(self.root,directory=True) as fd:
                st=os.fstat(fd)
                if (st.st_dev,st.st_ino)!=self.root_identity:raise Blocked('子任务目录已变化')
            self.thread=threading.Thread(target=self._accept,daemon=True);self.thread.start()
        except BaseException:
            self.close();raise

    def _accept(self):
        while not self.closed.is_set():
            try:client,_=self.listener.accept()
            except socket.timeout:continue
            except OSError:return
            with self.lock:
                if len(self.clients)>=4:client.close();continue
                self.clients.add(client)
            threading.Thread(target=self._serve,args=(client,),daemon=True).start()

    def _serve(self,client):
        from .managed_child_bridge import MAX_LINE,parse,arguments,remaining,read_line
        try:
            pid,uid,_=struct.unpack('3i',client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=os.getuid() or not _descendant(pid,self.owner.parent):return
            client.settimeout(remaining(self.owner.deadline))
            with client.makefile('rwb') as stream:
                line=read_line(stream,client,self.owner.deadline)
                if not line or len(line)>MAX_LINE or not line.endswith(b'\n'):return
                request=parse(line)
                if (set(request)!={'version','token','arguments'} or type(request['version']) is not int or request['version']!=1 or
                    not isinstance(request['token'],str) or not secrets.compare_digest(request['token'],self.secret) or
                    not _descendant(pid,self.owner.parent)):return
                done=threading.Event()
                def watch_disconnect():
                    while not done.is_set():
                        try:
                            ready,_,_=select.select([client],[],[],.1)
                            if ready:
                                # Either EOF or unexpected extra input ends this
                                # one-request connection and cancels its work.
                                client.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT)
                                if not done.is_set():
                                    if callable(getattr(self.owner,'stop_children',None)):self.owner.stop_children()
                                    else:self.owner.close()
                                return
                        except (OSError,ValueError):
                            if not done.is_set():
                                if callable(getattr(self.owner,'stop_children',None)):self.owner.stop_children()
                                else:self.owner.close()
                            return
                watcher=threading.Thread(target=watch_disconnect,daemon=True);watcher.start()
                try:reply=dict(ok=True,result=public_result(self.owner.call(**arguments(request['arguments']))))
                except Exception:reply=dict(ok=False,result='子任务未完成或状态未确认；未自动重发')
                finally:done.set();watcher.join(.3)
                raw=canonical(reply)
                if len(raw)>MAX_LINE:raw=canonical(dict(ok=False,result='子任务结果超限；未自动重发'))
                client.settimeout(remaining(self.owner.deadline));stream.write(raw+b'\n');stream.flush()
        except (OSError,ValueError,TypeError,Blocked):pass
        finally:
            with self.lock:self.clients.discard(client)
            client.close()

    def close(self):
        self.closed.set()
        if self.listener is not None:self.listener.close()
        with self.lock:
            for client in list(self.clients):
                try:client.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        result=self.owner.stop_children() if callable(getattr(self.owner,'stop_children',None)) else self.owner.close()
        if self.filefd is not None:
            try:os.ftruncate(self.filefd,0);self.cleanup_info=dict(status='retained_private_metadata',path=str(self.path))
            except OSError as exc:self.cleanup_info=dict(status='cleanup_failed',path=str(self.path),reason=str(exc))
            finally:os.close(self.filefd);self.filefd=None
        if self.rootfd is not None:
            try:
                current=self.root.lstat()
                if (current.st_dev,current.st_ino)!=self.root_identity or not stat.S_ISDIR(current.st_mode):self.cleanup_info=dict(status='identity_changed',path=str(self.root),capability_cleanup=self.cleanup_info)
            except OSError as exc:self.cleanup_info=dict(status='identity_changed',path=str(self.root),reason=str(exc),capability_cleanup=self.cleanup_info)
            finally:os.close(self.rootfd);self.rootfd=None
        return dict(children=result,cleanup=self.cleanup_info)
