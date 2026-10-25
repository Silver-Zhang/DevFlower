"""Names and reversible lifecycle metadata; native records are never rewritten."""
from __future__ import annotations
from collections import Counter
import json
import os
from pathlib import Path
import shlex
import sys
import time
from orchestration.common import Blocked, canonical, digest
from orchestration.process import execute
from orchestration.store import lease
from .agent_sessions import _visible, safe_text
from .graph import Invalid
from .session_sources import opened


class WorkbenchSessions:
    def __init__(self, runtime): self.rt=runtime;self.db=runtime.flow

    def operation_session(self, workspace, operation):
        op=self.rt.session_center.operation(operation,workspace)
        matches=[]
        for session in self.listing(workspace)['sessions']:
            internal=self.rt.db.get('session-handle',session['handle'])
            if self.matching_operation(workspace,internal,op):matches.append(session)
        if len(matches)>1:raise Blocked('原生会话身份不唯一，未自动打开')
        return dict(session=matches[0] if matches else None)

    def node_conversation(self, workspace, run, activation):
        """Read a scoped native conversation; never start/resume an Agent."""
        from orchestration.process import group_alive
        from orchestration.privacy import credentials, redact
        flow = self.db.get('run', run); item = self.db.get('activation', activation)
        if flow['workspace'] != workspace or item['run'] != run or item.get('kind') != 'agent':
            raise Blocked('对话不属于当前工作区、运行或Agent节点')
        if not item.get('chat_key'):
            extra=[src for p in self.rt.operator['profiles'].values() for src,_ in p.get('readonly_files',[])]
            secret=credentials(self.rt.native,extra)
            return dict(workspace=workspace,run=run,activation=activation,node=item['node'],
                iteration_path=item.get('iteration_path',[]),state='UNKNOWN' if self.rt.snapshot(run)['state']=='RECOVERY_REQUIRED' else item['state'],
                task=safe_text(redact(flow['task'],secret)),process_alive=False,connection_check=False,
                request_sections=[],messages=[],activity=[],summaries=[],session=None,history_status='waiting')
        chat = self.db.get('node-session', item['chat_key']); binding = chat['binding']
        secret = credentials(binding['home'], [x[0] for x in binding.get('readonly_files', [])])
        snapshot = self.rt.snapshot(run)
        state = item['state']
        if snapshot['state'] == 'RECOVERY_REQUIRED' or state == 'RUNNING' and not group_alive(item.get('process')):
            state = 'UNKNOWN'
        value = dict(workspace=workspace, run=run, activation=activation, node=item['node'],
            iteration_path=item.get('iteration_path', []), state=state,
            process_alive=group_alive(item.get('process')), task=safe_text(redact(flow['task'],secret)),
            connection_check=bool(item.get('connection_check')),
            request_sections=redact(item.get('request_sections', []), secret),
            messages=[],activity=[],summaries=[],session=None,history_status='waiting')
        try:
            offset=item.get('native_history_offset');end=item.get('native_history_end')
            if type(offset) is not int or offset<0 or state not in ('STARTING','RUNNING') and type(end) is not int:
                raise Invalid('此历史运行没有记录可核验的本轮原生范围')
            session = self.activation_session(workspace,run,activation)
            _,native=self.rt.session_center.resolve(workspace,session['handle'])
            if item.get('native_history_path') and native['native']['path']!=item['native_history_path']:
                raise Blocked('原生历史路径与本轮绑定不一致')
            history = self.history(workspace,session['handle'],start_offset=offset,end_offset=end,include_turns=False)
            current=self.db.get('activation',activation)
            if any(current.get(k)!=item.get(k) for k in ('state','native_history_offset','native_history_end')):
                raise Invalid('本轮记录正在提交，下一次读取核验')
            value.update(session=session,history_status='available',
                **{k:history.get(k,[]) for k in ('messages','activity','summaries')})
        except (Invalid,Blocked,ValueError,OSError):
            # Growing/unflushed native records may not yet have a stable readable
            # snapshot. Absence is not evidence of model thinking or success.
            value['history_status'] = 'waiting' if state in ('STARTING','RUNNING') else 'unavailable'
        if state != 'UNKNOWN':
            output=item.get('output',{}); receipt=output.get('receipt',{}); proc=receipt.get('process',{})
            if (state=='COMPLETED' and receipt.get('session_id')==binding.get('session_id') and
                ('receipt' not in item or item['receipt']==receipt) and
                receipt.get('requested_model')==binding['model'] and receipt.get('requested_effort')==binding['effort'] and
                proc.get('exit')==0 and all(proc.get(k) is False for k in ('timeout','cancelled','overflow')) and
                isinstance(output.get('answer'),str)):
                value['saved_answer']=safe_text(redact(output['answer'],secret))
        return value

    def activation_session(self, workspace, run, activation):
        flow = self.db.get('run', run); item = self.db.get('activation', activation)
        if flow['workspace'] != workspace or item['run'] != run:
            raise Blocked('节点会话不属于当前工作区和运行')
        chat = self.db.get('node-session', item['chat_key'])
        for row in self.listing(workspace)['sessions']:
            internal = self.rt.db.get('session-handle', row['handle'])
            if chat['id'] in self.matching_chats(workspace, internal):
                return row
        raise Blocked('原生会话尚未完成唯一身份核验，请刷新后重试')

    def matching_chats(self,workspace,row):
        """A UUID alone is not a session: keep its provider, HOME and cwd exact."""
        p,native=row['profile'],row['native']
        if row.get('ambiguous') or not row.get('authorized_read',True) or not p.get('home') or native.get('cwd_changed'):return {}
        if p['config_root']!=str(Path(p['home'])/('.'+p['provider'])):return {}
        return {c['id']:c for c in self.db.list('node-session') if
            c.get('workspace')==workspace and c.get('cwd')==native['cwd'] and
            c['binding'].get('provider')==p['provider'] and c['binding'].get('home')==p['home'] and
            c['binding'].get('session_id')==native['session_id'] and
            c['binding'].get('project',workspace)==workspace}

    def matching_operation(self,workspace,row,operation):
        if not row or not row['profile'].get('home') or row.get('ambiguous') or not row.get('authorized_read',True):return False
        try:stored=self.rt.db.get('session-operation',operation['id'])
        except Invalid:return False
        p,native=row['profile'],row['native'];binding=stored['binding']
        return (stored['workspace']==workspace and stored['cwd']==native['cwd'] and
            binding.get('provider')==p['provider'] and binding.get('home')==p['home'] and
            p['config_root']==str(Path(p['home'])/('.'+p['provider'])) and
            (operation.get('session_id') or operation.get('requested_session'))==native['session_id'])

    def related_activations(self,workspace,row):
        p=row['profile']
        chats=self.matching_chats(workspace,row)
        runs={r['id']:r for r in self.db.list('run') if r.get('workspace')==workspace}
        related=[]
        for a in self.db.list('activation'):
            c=chats.get(a.get('chat_key'));run=runs.get(a.get('run'))
            if (c and run and c.get('run')==a.get('run') and c.get('node')==a.get('node') and
                    a.get('provider')==p['provider']):related.append((a,c))
        return sorted(related,key=lambda pair:(pair[0].get('started_at',0),
            pair[0].get('iteration_path') or [pair[0].get('iteration',0)],pair[0]['id']))

    def execution_results(self,workspace,row,related,messages,query,secrets):
        """Display confirmed output.answer, never promote a native tool payload.

        Controller-owned completion state/receipts remain authoritative. Reading
        this projection does not reconcile, recover, or change any run state.
        """
        from orchestration.privacy import redact
        native_answers=Counter(m['text'].strip() for m in messages if m['role']=='assistant')
        results=[];run_states={}
        for a,chat in related:
            output=a.get('output')
            if a.get('kind')!='agent' or a.get('state')!='COMPLETED' or not isinstance(output,dict):continue
            receipt=output.get('receipt');binding=chat['binding']
            if not isinstance(receipt,dict) or not isinstance(output.get('answer'),str):continue
            if (receipt.get('session_id')!=row['native']['session_id'] or
                    receipt.get('requested_model')!=binding.get('model') or
                    receipt.get('requested_effort')!=binding.get('effort') or
                    'receipt' in a and a['receipt']!=receipt):continue
            process=receipt.get('process')
            if not isinstance(process,dict) or process.get('exit')!=0 or any(
                    process.get(k) is not False for k in ('timeout','cancelled','overflow')):continue
            text=safe_text(redact(output['answer'],secrets)).strip()
            if not text or query and query.casefold() not in text.casefold():continue
            # Codex usually records its JSON final as ordinary native text.
            # Count matches rather than collapsing identical answers across rounds.
            if native_answers[text]:
                native_answers[text]-=1
                continue
            if a['run'] not in run_states:run_states[a['run']]=self.rt.snapshot(a['run'])['state']
            result={k:a.get(k) for k in ('id','run','node','name','iteration','iteration_path','started_at','finished_at')}
            if isinstance(result.get('name'),str):result['name']=safe_text(redact(result['name'],secrets))
            result.update(source='structured_execution_result',text=text,provider=binding['provider'],
                session_id=receipt['session_id'],state='COMPLETED',run_state=run_states[a['run']])
            results.append(result)
        return results

    def title(self, row):
        try:
            native=self.rt.db.get('session-handle',row['handle'])
            if native['workspace']!=row['workspace_id'] or not native.get('authorized_read') or native.get('ambiguous'):raise Invalid('历史不属于本工作区或身份不唯一')
            first=None;title=None
            with opened(native['native']['path']) as fd:
                with os.fdopen(os.dup(fd),'rb') as stream:
                    for i in range(1200):
                        line=stream.readline(1048577)
                        if not line or len(line)>1048576:break
                        obj=json.loads(line)
                        if obj.get('type')=='custom-title' and isinstance(obj.get('customTitle'),str): title=obj['customTitle']
                        visible=_visible(obj,row['provider'])
                        if not first and visible and visible['role']=='user':
                            text=visible['text']
                            if text.startswith(('# AGENTS.md instructions for','<environment_context>','<permissions instructions>')):continue
                            if text.startswith('Role:') and 'Writable test artifacts:' in text:
                                text='\n'.join(text.splitlines()[2:]).strip()
                            first=text
            return safe_text(title or first or '新对话')[:90].replace('\n',' ')
        except (Invalid,ValueError,OSError):return '对话记录暂不可读'

    def listing(self, workspace, view='active', query=''):
        # Failed first turns still own native records. Index their dedicated HOME
        # without inventing a successful UUID receipt or adopting external HOME.
        for chat in self.db.list('node-session'):
            if chat['workspace']!=workspace:continue
            key=digest(chat['binding']['home'].encode())
            try:self.rt.db.get('chat-profile',key)
            except Invalid:self.rt.db.put('chat-profile',key,dict(workspace=workspace,binding=chat['binding']))
        listing=self.rt.session_center.discover(workspace,True);rows=listing['discovery']['sessions']
        native_keys={r['handle'] for r in rows}
        for meta in self.db.list('session-meta'):
            if meta['workspace']==workspace and meta.get('native_archived') and meta['id'] not in native_keys:
                rows.append(meta['snapshot'])
        result=[]
        operations=listing['operations']
        for row in rows:
            handle=row['handle']
            try:meta=self.db.get('session-meta',handle)
            except Invalid:meta=dict(id=handle,workspace=workspace,collection='active')
            if meta['workspace']!=workspace:raise Blocked('会话元数据不属于当前工作区')
            if meta.get('collection','active')!=view:continue
            internal=None
            try:internal=self.rt.db.get('session-handle',handle)
            except Invalid:pass
            related=list(self.matching_chats(workspace,internal).values()) if internal else []
            actual=[a for a,_ in self.related_activations(workspace,internal)] if internal else []
            # Old caches may have been derived from another HOME's same UUID.
            # Invalidate derived data once; explicit user aliases stay separate.
            title_binding=digest(canonical(['exact-native-v2',workspace,row['profile_id'],
                internal['profile'].get('home') if internal else None,row['provider'],row['cwd'],row['session_id']]))
            title=meta.get('alias')
            if not title:
                try:
                    cached=self.db.get('session-title',handle)
                    title=cached.get('title') if cached.get('binding')==title_binding else None
                except Invalid:
                    title=None
                if not title or title.startswith(('## 本次运行初始化','# AGENTS.md instructions','Role:')):
                    title=next((c.get('name') for c in related if c.get('name')),None) or next((a.get('name') for a in actual if a.get('name')),None) or self.title(row)
                    self.db.put('session-title',handle,{'id':handle,'title':title,'binding':title_binding})
            title=title or '新对话'
            running=next((o for o in operations if o['state'] not in ('COMPLETED','FAILED','CANCELLED') and self.matching_operation(workspace,internal,o)),None)
            busy=False
            if internal and internal['profile'].get('home'):
                try:
                    busy=self.busy({'home':internal['profile']['home']},row['session_id'])
                except (Invalid,Blocked):busy=True
            latest=max(actual,key=lambda a:a.get('started_at',0)) if actual else None
            named=dict(row,title=title,collection=meta.get('collection','active'),alias_only=bool(meta.get('alias')),
                display_state=running['state'] if running else 'BUSY' if busy else '运行未确认',busy=busy,
                node=latest['node'] if latest else related[0]['node'] if related else None,
                choices={'resume':bool(row.get('resume')) and not busy and not meta.get('native_archived'),
                         'context_new':row['capabilities'].get('read',False),'fork':row['provider']=='codex' and bool(row.get('resume')) and not busy,
                         'view':True},native_archived=meta.get('native_archived',False))
            if latest and not busy and not running:
                from orchestration.process import group_alive
                named['display_state']='运行未确认' if latest['state'] in ('RUNNING','STARTING') and not group_alive(latest.get('process')) else latest['state']
            if latest:
                workflow = self.db.get('run', latest['run'])
                named.update(workflow=workflow.get('graph', {}).get('name', '未命名工作流'),
                             workflow_id=latest['run'], node_name=latest.get('name', latest['node']))
            # Show the session's actual bound request configuration, never the
            # default profile selected for a different/new conversation.
            bound = related[0]['binding'] if related else next((c['binding'] for c in
                self.rt.db.listing('chat-profile', filters={'workspace': workspace}, limit=None)
                if internal and c['binding'].get('home') == internal['profile'].get('home') and
                c['binding'].get('provider') == row['provider'] and
                c['binding'].get('session_id') == row['session_id']), None)
            if bound:
                exact = next((name for name, profile in self.rt.operator['profiles'].items() if
                    all(bound.get(k) == profile.get(k) for k in
                        ('provider', 'executable', 'read_paths', 'readonly_files')) and
                    (name==bound.get('runtime_profile') or (bound.get('model'),bound.get('effort'))==(profile['model'],profile['effort']))), None)
                named['execution'] = dict(profile=exact, provider=bound['provider'],
                                          model=bound['model'], effort=bound['effort'])
            if not query or query.casefold() in (title+' '+row['provider']).casefold():result.append(named)
        return dict(sessions=result,coverage=listing['discovery']['coverage'],operations=operations)

    def busy(self,binding,sid):
        if self.rt.session_center._busy(binding['home']):return True
        # External native terminals need not honour our lock; check their actual HOME/explicit session.
        for p in Path('/proc').glob('[0-9]*'):
            comm=''
            try:
                comm=(p/'comm').read_text().strip()
                if not any(x in comm for x in ('codex','claude')):continue
                argv=(p/'cmdline').read_bytes().split(b'\0')
                env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
                if sid.encode() in argv or env.get(b'HOME')==binding['home'].encode():return True
            except (OSError,ValueError):
                if 'comm' in locals() and any(x in comm for x in ('codex','claude')):return True
                continue
        return False

    def history(self,workspace,handle,query='',*,start_offset=0,end_offset=None,include_turns=True):
        try:meta=self.db.get('session-meta',handle)
        except Invalid:meta={}
        if meta.get('native_archived'):
            if meta['workspace']!=workspace:raise Blocked('归档会话不属于本工作区')
            from .session_sources import LIMITS,read_session,regular_at
            from .agent_sessions import conversation
            from .session_center import credential_bytes
            from orchestration.privacy import credentials
            binding=meta['binding'];sid=meta['snapshot']['session_id']
            archive=Path(binding['home'])/'.codex/archived_sessions';records=[]
            # Native Codex archives are flat, unlike sessions/YYYY/MM/DD.
            # Keep the ordinary scanner's layout restriction intact.
            with opened(archive,directory=True) as directory:
                names=os.listdir(directory)
                if len(names)>2000:raise Blocked('原生归档超过读取预算')
                for name in names:
                    if sid not in name or not name.endswith('.jsonl'):continue
                    with regular_at(name,dir_fd=directory) as fd:
                        item=read_session(fd,'codex',name,LIMITS);item['path']=str(archive/name);records.append(item)
            records=[r for r in records if r['session_id']==sid and r['cwd']==meta['cwd'] and not r['cwd_changed']]
            if len(records)!=1:raise Blocked('原生归档记录不能唯一核验；请先恢复后查看')
            row=dict(profile=dict(binding,config_root=str(Path(binding['home'])/'.codex')),native=records[0])
            secret=credentials(binding['home'],[x[0] for x in binding.get('readonly_files',[])],read_file=credential_bytes)
            result=dict(workspace_id=workspace,handle=handle,messages=conversation('codex',records[0]['path'],200,query,expected_sha256=records[0]['sha256'],secrets=secret,start_offset=start_offset,end_offset=end_offset,include_metadata=True))
        else:
            result=self.rt.session_center.history(workspace,handle,query,200,start_offset=start_offset,end_offset=end_offset,include_metadata=True)
            _,row=self.rt.session_center.resolve(workspace,handle)
        messages=[]
        for m in result['messages']:
            text=m['text']
            if m['role']=='user' and text.startswith(('# AGENTS.md instructions for','<environment_context>','<permissions instructions>')):continue
            if m['role']=='user' and text.startswith('Role:') and 'Writable test artifacts:' in text:
                text='\n'.join(text.splitlines()[2:]).strip()
            if m['role']=='user' and '## 每轮任务' in text and '## 上游输入' in text:
                # The complete, unchanged request remains in the expandable
                # request group; the chat bubble shows the human round task.
                text=text.split('## 每轮任务',1)[1].split('## 上游输入',1)[0].strip()
            if m['role']=='assistant':
                try:
                    value=json.loads(text)
                    if isinstance(value,dict) and isinstance(value.get('reply'),str):text=value['reply']
                except ValueError:pass
            messages.append(dict(m,text=text))
        related=self.related_activations(workspace,row)
        groups=[{k:a.get(k) for k in ('id','run','node','name','iteration','iteration_path','state','initialization_included','request_sections','actual_request','actual_request_sha256','input_sent_sha256','receipt')}
                for a,_ in related]
        from orchestration.privacy import credentials,redact
        from .session_center import credential_bytes
        extra=[src for p in self.rt.operator['profiles'].values() for src,_ in p.get('readonly_files',[])]
        secrets=credentials(row['profile']['home'] or row['profile']['config_root'],extra,read_file=credential_bytes)
        # JSON reply decoding can introduce escaped credential bytes. Search and
        # deduplication must use the final, redacted display text, not its envelope.
        messages=[dict(m,text=safe_text(redact(m['text'],secrets))) for m in messages]
        if query:messages=[m for m in messages if query.casefold() in m['text'].casefold()]
        groups=redact(groups,secrets)
        for group in groups:
            if isinstance(group.get('actual_request'),str):group['actual_request']=safe_text(group['actual_request'])
        execution_results=self.execution_results(workspace,row,related,messages,query,secrets)
        activity=[];summaries=[];activity_omitted=False
        with opened(row['native']['path']) as fd:
            with os.fdopen(os.dup(fd),'rb') as stream:
                stream.seek(start_offset);total=start_offset
                while True:
                    if end_offset is not None and total>=end_offset:break
                    raw=stream.readline(1048577);total+=len(raw)
                    if not raw:break
                    if len(raw)>1048576 or total>16*1024*1024 or end_offset is not None and total>end_offset:raise Invalid('工具活动超过本轮读取预算')
                    obj=json.loads(raw);items=[]
                    if row['profile']['provider']=='claude' and obj.get('type') in ('user','assistant') and not obj.get('isMeta') and not obj.get('isSidechain'):
                        content=obj.get('message',{}).get('content',[])
                        if isinstance(content,list):
                            for c in content:
                                if isinstance(c,dict) and c.get('type') in ('tool_use','tool_result'):
                                    items.append({'kind':c['type'],'name':c.get('name','工具结果'),'content':c.get('input',c.get('content','')),'call_id':c.get('id') if c['type']=='tool_use' else c.get('tool_use_id'),'at':obj.get('timestamp')})
                    elif row['profile']['provider']=='codex' and obj.get('type')=='response_item':
                        v=obj.get('payload',{})
                        # Only an explicit public summary; never content, thinking or encrypted payloads.
                        if v.get('type')=='reasoning' and isinstance(v.get('summary'),list):
                            for part in v['summary']:
                                if isinstance(part,dict) and part.get('type')=='summary_text' and isinstance(part.get('text'),str):
                                    summaries.append({'source':'provider-public-summary','text':safe_text(redact(part['text'][:8000],secrets))})
                            summaries=summaries[-20:]
                        if v.get('type') in ('function_call','function_call_output','custom_tool_call','custom_tool_call_output'):
                            # Codex 0.163 emits custom_tool_call for its exec tool.
                            # Whitelist visible tool fields only; protocol metadata
                            # and reasoning payloads must never enter chat activity.
                            items.append({'kind':v['type'],'name':v.get('name','工具结果'),
                                          'content':v.get('arguments',v.get('input',v.get('output',''))),'call_id':v.get('call_id'),'at':obj.get('timestamp')})
                    for v in items:
                        from .tool_activity import visible_content
                        value=redact(v,secrets)
                        # JSON/text-block decoding may reveal escaped secrets.
                        text=safe_text(redact(visible_content(value['content'],protocol=v['kind'] in ('tool_result','function_call_output','custom_tool_call_output')),secrets))
                        activity.append(dict(value,content=safe_text(text[:4000]),truncated=len(text)>4000))
                    if len(activity)>100:activity_omitted=True
                    activity=activity[-100:]
        from .tool_activity import group_tools
        activity=group_tools(activity)
        if activity_omitted and activity:activity[0]['history_truncated']=True
        # No thinking/reasoning payloads or full protocol envelopes cross this boundary.
        from .conversation_projection import conversation_turns
        turns=conversation_turns(self,workspace,row,related,messages,secrets,original_messages=result['messages']) if include_turns else []
        messages=[{k:m[k] for k in ('role','text')} for m in messages]
        return dict(result,messages=messages,execution_results=execution_results,groups=groups,activity=activity,summaries=summaries,conversation_turns=turns)

    def change(self,workspace,handle,action,*,name='',answer=''):
        self.rt.check_operator()
        native_modified=False
        try:meta=self.db.get('session-meta',handle)
        except Invalid:meta=dict(id=handle,workspace=workspace,collection='active')
        if meta['workspace']!=workspace:raise Blocked('会话不属于当前工作区')
        if meta.get('native_archived') and action=='restore':
            binding=meta['binding']
            if self.busy(binding,meta['snapshot']['session_id']):raise Blocked('会话占用状态未确认，无法恢复')
            self.rpc(binding,meta['cwd'],'thread/unarchive',{'threadId':meta['snapshot']['session_id']})
            native_modified=True
            meta.update(native_archived=False,collection='active')
        else:
            project,row=self.rt.session_center.resolve(workspace,handle)
            binding,reason=self.rt.session_center._binding(row,project)
            if binding and self.busy(binding,row['native']['session_id']):raise Blocked('会话正在被使用；请先停止原回合')
            if action=='rename':
                if not isinstance(name,str) or not 1<=len(name.strip())<=160:raise Invalid('会话名称需要 1–160 个字符')
                meta['alias']=safe_text(name.strip())
            elif action in ('native_rename','fork'):
                if not binding or row['profile']['provider']!='codex' or not answer.strip():
                    raise Blocked('只有明确确认且可核验原环境的 Codex 会话可执行此原生操作')
                sid=row['native']['session_id']
                if action=='native_rename':
                    if not isinstance(name,str) or not 1<=len(name.strip())<=160:raise Invalid('请填写有效会话名称')
                    self.rpc(binding,row['native']['cwd'],'thread/name/set',{'threadId':sid,'name':name.strip()})
                    native_modified=True
                    meta['alias']=safe_text(name.strip());meta['native_named']=True
                else:
                    result=self.rpc(binding,row['native']['cwd'],'thread/fork',{'threadId':sid,'excludeTurns':True,
                        'cwd':row['native']['cwd'],'approvalPolicy':'never','sandbox':'read-only'})
                    child=result.get('thread',{}).get('id')
                    from .session_sources import UUID
                    if not isinstance(child,str) or not UUID.fullmatch(child) or child==sid:raise Blocked('没有可验证的新原生会话 ID，未伪称分叉成功')
                    fork=dict(binding,session_id=child)
                    self.rt.db.put('chat-profile',digest(canonical([fork['home'],child])),dict(workspace=workspace,binding=fork))
                    return dict(native_fork=True,parent_session=sid,new_session=child,cwd=row['native']['cwd'],original_preserved=True)
            elif action in ('archive','trash'):
                if not answer.strip():raise Invalid('请确认影响范围；此操作不会永久删除原生记录')
                meta['collection']='archived' if action=='archive' else 'trash'
            elif action=='native_archive':
                if not answer.strip() or not binding or row['profile']['provider']!='codex':
                    raise Blocked('此会话没有可验证的原生归档权限；可使用 DevFlower 归档')
                # Only dedicated managed homes, never another application's/default HOME.
                snapshot=next(x for x in self.rt.session_center.discover(workspace,True)['discovery']['sessions'] if x['handle']==handle)
                self.rpc(binding,row['native']['cwd'],'thread/archive',{'threadId':row['native']['session_id']})
                native_modified=True
                meta.update(collection='archived',native_archived=True,binding=binding,cwd=row['native']['cwd'],snapshot=snapshot)
            elif action=='restore':meta['collection']='active'
            else:raise Invalid('永久删除未启用；原生会话和源码不会被删除')
        meta['updated_at']=time.time();self.db.put('session-meta',handle,meta)
        return dict(handle=handle,collection=meta['collection'],alias=meta.get('alias'),native_archived=meta.get('native_archived',False),
                    native_modified=native_modified,permanently_deleted=False)

    def rpc(self,binding,cwd,method,params):
        helper=Path(__file__).with_name('workbench_rpc.py')
        home=Path(binding['home']);artifacts=self.rt.storage/'metadata-artifacts';artifacts.mkdir(exist_ok=True)
        mutation=method in ('thread/archive','thread/unarchive','thread/name/set','thread/fork')
        operation=None
        if mutation:
            import secrets
            operation=secrets.token_hex(16)
            with self.db.tx() as db:
                rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE kind='native-mutation'")]
                if any(r['home']==str(home) and r['state']!='COMPLETED' for r in rows):
                    raise Blocked('此前原生操作回执未确认；禁止盲目重复改名、分叉或归档')
                value=dict(id=operation,home=str(home),cwd=cwd,method=method,params=params,state='PENDING',at=time.time())
                db.execute('INSERT INTO records VALUES(?,?,?)',('native-mutation',operation,canonical(value).decode()))
        try:
            with lease(home.parent/(digest(str(home.resolve()).encode())+'.session.lock')):
                result,out,_=execute([sys.executable,'-B',str(helper)],cwd,home,
                    read=[str(helper),cwd,*binding['read_paths']],write=[],timeout=30,cap=2_000_000,
                    backend='bwrap',readonly_files=binding.get('readonly_files',[]),
                    input_text=json.dumps({'executable':binding['executable'],'method':method,'params':params}))
            if result['exit'] or result['timeout']:raise Blocked('原生元数据接口未完成，未伪称操作成功')
            response=json.loads(out)
            if 'error' in response:raise Blocked('原生工具拒绝操作：'+safe_text(response['error'].get('message','unknown')))
        except (Blocked,OSError,ValueError):
            if operation:self.db.change('native-mutation',operation,{'state':'RECOVERY_REQUIRED'})
            raise
        if operation:self.db.change('native-mutation',operation,{'state':'COMPLETED','result':response['result']})
        return response['result']

    def terminal(self,workspace,handle):
        project,row=self.rt.session_center.resolve(workspace,handle)
        binding,reason=self.rt.session_center._binding(row,project)
        if not binding or self.busy(binding,row['native']['session_id']):raise Blocked(reason or '原生会话正被占用')
        command=[sys.executable,str(Path(__file__).parents[1]/'devflower_native_terminal.py')]
        for key,value in dict(operator=str(self.rt.operator_file),operator_sha256=self.rt.operator_sha256,candidate_sha256=self.rt.candidate_sha256,policy=str(self.rt.policy_file),policy_sha256=self.rt.policy_sha,workspace=workspace,handle=handle).items():command+=['--'+key.replace('_','-'),value]
        return dict(command=shlex.join(command),cwd=row['native']['cwd'],mode='只读原生终端；启动前重新核验原环境和占用，退出后释放同一 HOME 锁',attached=False)

    _catalog_locks={}

    def capabilities(self,refresh=False,selected=None):
        if not refresh:return self._capabilities(False,selected)
        if selected is None:
            rows=[self.capabilities(True,name)[0] for name in self.rt.operator['profiles']]
            return rows+[dict(id='copilot',provider='copilot',available=False,reason='认证、原生会话与隔离能力尚未验证')]
        if selected not in self.rt.operator['profiles']:raise Invalid('请选择已登记的 Provider 配置')
        import threading
        lock=self._catalog_locks.setdefault((str(self.rt.storage),selected),threading.Lock())
        if not lock.acquire(blocking=False):
            return [dict(self._capabilities(False,selected)[0],refresh_pending=True)]
        try:return self._capabilities(True,selected)
        finally:lock.release()

    def _capabilities(self,refresh=False,selected=None):
        from .model_catalog import ModelCatalog
        result=[]
        for name,profile in self.rt.operator['profiles'].items():
            if selected is not None and selected!=name:continue
            if refresh:
                try:cli=self.rpc(dict(profile,home=str(self.rt.rpc_home/name)),str(self.rt.storage),'cli/probe',{'provider':profile['provider']})
                except (Blocked,OSError,ValueError):cli={'available':False,'machine_output':False,'version':'unknown','reason':'原生接口探测未完成，请检查已登记工具路径后刷新'}
                cli.update(id=name,at=time.time(),executable_sha256=digest(Path(profile['executable']).resolve().read_bytes()))
                self.db.put('cli-probe',name,cli)
            else:
                try:cli=self.db.get('cli-probe',name)
                except Invalid:cli={'available':Path(profile['executable']).is_file(),'status':'registered; native capability refresh not yet performed'}
            catalog=ModelCatalog(self).get(name,profile,refresh)
            result.append(dict(id=name,provider=profile['provider'],available=cli['available'],cli=cli,**catalog,
                inference_verified=False,verification_scope='exact selection; use model-admission/status',modes=['Ask','Plan','Agent'],skills=[],subagents=False,
                model=profile['model'],effort=profile['effort'],resolved_effort='unknown'))
        if selected is not None and not result:raise Invalid('请选择已登记的 Provider 配置')
        if selected is None:
            result.append(dict(id='copilot',provider='copilot',available=False,installed=bool(__import__('shutil').which('copilot')),
                reason='认证、模型发现与隔离执行尚未验证，不能作为可执行节点'))
        return result
