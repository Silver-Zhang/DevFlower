"""Workspace-scoped Git platform observations and pinned local review tasks."""
import json
import os
import re
import time
from urllib.parse import parse_qs, urlsplit

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from .git_platform_adapters import SHA, checked_ref
from .git_workspace import bind_workspace
from .run_inspector import IDENT, RUN, RunInspector, take

TASK = 'git-review-task'
INTENT = 'git-review-intent'
NUMBER = re.compile(r'[1-9][0-9]{0,17}')
LOG_BYTES = 180000
LIST = 50
JS_MAX = 2**53 - 1
FIELDS = {
    'repository': ('platform','repository','name','default_branch','url','private'),
    'branch': ('name','sha','protected','url'),
    'issue': ('id','title','state','url','updated_at','body'),
    'change': ('id','title','state','url','updated_at','body','head_sha','source_branch','target_branch','author','merged','draft'),
    'check': ('id','name','state','conclusion','sha','url'),
    'job': ('id','name','state','conclusion','url'),
    'log': ('job_id','check_id','sha','text','truncated'),
}
GET = {
    'repository': set(), 'branches': set(), 'issues': set(), 'changes': set(), 'reviews': set(),
    'issue': {'number'}, 'change': {'number'},
    'checks': {'target_type','target','sha'},
    'jobs': {'target_type','target','sha','check'},
    'log': {'target_type','target','sha','check','job'},
}
DELIVERY = {'state':'GATE_REQUIRED','publish_available':False,
            'reason':'远端发布需要已核验的独立 Gate、固定候选和明确的人工授权。'}


def parameters(route, query, headers):
    try:
        if (route not in GET or len(query)>1500 or headers.get_all('Transfer-Encoding', [])
                or headers.get_all('Content-Length', []) not in ([], ['0'])):
            raise ValueError()
        values = parse_qs(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=7)
        required = GET[route] | ({'workspace'} if route=='repository' else {'workspace','binding'})
        if set(values)!=required or any(len(row)!=1 for row in values.values()):
            raise ValueError()
        result = {key:row[0] for key,row in values.items()}
        if not IDENT.fullmatch(result['workspace']) or 'binding' in result and not IDENT.fullmatch(result['binding']):
            raise ValueError()
        for key in ('number','check','job'):
            if key in result:
                result[key] = number(result[key])
        if 'sha' in result and not SHA.fullmatch(result['sha']):
            raise ValueError()
        if 'target' in result:
            if result['target_type']=='change':
                result['target'] = number(result['target'])
            elif result['target_type']=='branch':
                checked_ref(result['target'])
            else:
                raise ValueError()
        return result
    except ValueError:
        raise Invalid('Git 平台读取需要明确的工作区、对象和已观察提交。') from None


def number(value):
    if isinstance(value,str) and NUMBER.fullmatch(value):
        return int(value)
    if type(value) is int and 0<value<10**18:
        return value
    raise ValueError('invalid identity')


def get_row(tx, kind, key):
    raw = tx.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,key)).fetchone()
    return json.loads(raw[0]) if raw else None


class GitPlatform:
    def __init__(self, store, runtime=None):
        self.store = store
        self.runtime = runtime
        self.inspector = RunInspector(store)
        self.secrets = {value for key in ('GH_TOKEN','GITHUB_TOKEN','GITLAB_TOKEN','DEVFLOWER_GITLAB_TOKEN','GL_TOKEN')
                        if (value:=os.environ.get(key)) and len(value)>=8}

    def bind(self, workspace):
        from .git_platform_adapters import adapter_for
        binding = bind_workspace(self.store, workspace)
        adapter = adapter_for(binding.origin)
        if adapter.platform not in ('github','gitlab'):
            raise Blocked('此工作区的 origin 不属于已接入平台。')
        return binding, adapter

    def still_bound(self, binding):
        current = bind_workspace(self.store, binding.workspace)
        if (current.identity, current.origin)!=(binding.identity,binding.origin):
            raise Conflict('工作空间 Git 配置已变化，请刷新平台绑定。')
        return current

    def public_text(self, text):
        text = self.inspector.text(text)
        for secret in self.secrets:
            text = text.replace(secret,'[已隐藏]')
        return text

    def record(self, adapter, kind, data):
        if not isinstance(data,dict):
            raise Blocked('平台未返回预期记录。')
        value = take(data,FIELDS[kind])
        for key, item in list(value.items()):
            if key=='url':
                try:
                    url=urlsplit(item) if isinstance(item,str) else None
                    host='github.com' if adapter.platform=='github' else 'gitlab.com'
                    root='/' + adapter.repository
                    if (not url or url.scheme!='https' or url.netloc!=host or url.query or url.fragment
                            or not (url.path==root or url.path.startswith(root+'/'))):
                        value.pop(key)
                except ValueError:
                    value.pop(key)
            elif isinstance(item,str):
                cap = LOG_BYTES if key=='text' else 12000 if key=='body' else 300
                raw = self.public_text(item).encode()
                value[key] = raw[:cap].decode(errors='ignore')
                if key=='text' and len(raw)>cap:
                    value['truncated'] = True
            elif key in ('id','job_id','check_id') and type(item) is int and item>JS_MAX:
                value[key] = str(item)
            elif item is not None and type(item) not in (int,bool):
                value.pop(key)
        return value

    def list_records(self, adapter, kind, values):
        if not isinstance(values,list):
            raise Blocked('平台未返回预期列表。')
        return [self.record(adapter,kind,row) for row in values[:LIST]]

    def repository(self, adapter):
        repo = self.record(adapter,'repository',adapter.repo())
        if repo.get('platform')!=adapter.platform or repo.get('repository')!=adapter.repository:
            raise Conflict('远端仓库身份与工作空间 origin 不同。')
        return repo

    def branches(self, adapter, current):
        rows = self.list_records(adapter,'branch',adapter.branches())
        preferred = [self.repository(adapter).get('default_branch'),current]
        names = {row.get('name') for row in rows}
        pinned = []
        for ref in dict.fromkeys(preferred):
            if ref is None or ref in names:
                continue
            observed = adapter.branch_info(ref)
            if observed is not None:
                row = self.record(adapter,'branch',observed)
                if row.get('name')!=ref:
                    raise Conflict('平台返回的固定分支身份不同。')
                pinned.append(row)
        return [*pinned,*rows][:LIST]

    def change(self, adapter, ident):
        row = self.record(adapter,'change',adapter.change(ident))
        try:
            valid = number(row.get('id'))==ident
            if row.get('head_sha') is not None:
                valid = valid and bool(SHA.fullmatch(row['head_sha']))
        except ValueError:
            valid = False
        if not valid:
            raise Conflict('平台变更记录与请求身份不同。')
        return row

    def target(self, adapter, target_type, target, sha):
        if target_type=='change':
            item = self.change(adapter,target)
            actual = item.get('head_sha')
        else:
            observed = adapter.branch_info(target)
            if observed is None:
                raise Conflict('此分支已不可用。请刷新远端分支。')
            item = self.record(adapter,'branch',observed)
            actual = item.get('sha') if item.get('name')==target else None
        if actual!=sha:
            raise Conflict('远端提交已变化。请刷新变更或分支后核对。')
        return item

    def checks(self, adapter, sha):
        rows = self.list_records(adapter,'check',adapter.checks(sha))
        if any(row.get('sha')!=sha for row in rows):
            raise Conflict('平台检查不属于所选提交。')
        return rows

    def selected(self, rows, ident, label):
        matches = [row for row in rows if str(row.get('id'))==str(ident)]
        if len(matches)!=1:
            raise Conflict(label+'不属于所选提交或未被平台确认。')
        return matches[0]

    def reviews(self, binding, adapter):
        with self.store.db.tx(read=True) as tx:
            rows = tx.execute("""SELECT data FROM records WHERE kind=?
                AND json_extract(data,'$.workspace')=? AND json_extract(data,'$.platform')=?
                AND json_extract(data,'$.repository')=? ORDER BY rowid DESC LIMIT ?""",
                (TASK,binding.workspace,adapter.platform,adapter.repository,LIST)).fetchall()
        return [self.public_review(json.loads(raw)) for (raw,) in rows]

    def delivery(self, scope, *, run_id=None):
        if self.runtime is None:
            return DELIVERY
        with self.store.db.tx(read=True) as tx:
            raw = tx.execute("""SELECT data FROM records WHERE kind='run'
                AND json_extract(data,'$.workspace')=? AND json_type(data,'$.graph.development')='object'
                AND (? IS NULL OR id=?) ORDER BY rowid DESC LIMIT 1""",(scope.workspace,run_id,run_id)).fetchone()
            if raw is None:
                return DELIVERY
            run = json.loads(raw[0])
            definition = get_row(tx,'definition',run['definition_id'])
            development = run['graph']['development']
            from .designer_scheduler import activation_key
            loop = get_row(tx,'activation',activation_key(run['id'],development['loop'],[]))
            round_number = loop.get('loop_round') if loop else None
            gate = get_row(tx,'activation',activation_key(run['id'],development['loop']+'/'+development['gate'],
                                                        [round_number])) if round_number else None
        result = (gate or {}).get('output',{}).get('result',{})
        facts = self.inspector.clean(take(result,('gate_verdict','candidate_sha256','git_head','verification_sha256','feedback')))
        value = dict(DELIVERY, run=run['id'], gate=facts, gate_activation=gate['id'] if gate else None)
        if run['state']!='COMPLETED' or facts.get('gate_verdict')!='ACCEPT':
            return dict(value,state='GATE_NOT_ACCEPTED',reason='此研发运行未记录已完成的 ACCEPT。请核对测试、审核和循环结果。')
        expected = {'binding':scope.identity,'branch':scope.branch,'head':scope.head}
        if (development.get('source')!=expected or not scope.branch or facts.get('git_head')!=scope.head
                or not definition or definition.get('graph')!=run['graph']
                or run.get('output',{}).get('result')!=result):
            return dict(value,state='CANDIDATE_CHANGED',reason='当前分支、候选或冻结运行身份与 Gate 不一致。')
        try:
            from .development_evidence import delivery_gate
            from .recovery import frozen_node
            verified_run = self.runtime.snapshot(scope.workspace,run['id'])
            if verified_run['state']!='COMPLETED' or verified_run['run'].get('output',{}).get('result')!=result:
                raise Blocked('研发运行尚未完成核验。')
            checked = delivery_gate(self.runtime,run['id'],gate,frozen_node(definition,gate['node']))
            if checked['result']!=result:
                raise Blocked('交付 Gate 证据已变化。')
        except (Blocked,Invalid,OSError,ValueError,KeyError,TypeError):
            return dict(value,state='CANDIDATE_CHANGED',reason='当前候选或独立 Gate 证据无法重新核验。')
        return dict(value,state='LOCAL_GATE_ACCEPTED',
                    reason='当前本机候选通过独立 Gate。远端发布仍需要固定交付目标及明确的人工授权。')

    def read(self, route, workspace, binding=None, **params):
        scope, adapter = self.bind(workspace)
        if route!='repository' and binding!=scope.identity:
            raise Conflict('工作空间 Git 绑定已变化。请重新读取仓库。')
        result = dict(workspace=workspace,platform=adapter.platform,repository=adapter.repository,binding=scope.identity)
        if route=='repository':
            result.update(remote=self.repository(adapter),local=dict(branch=self.public_text(scope.branch) if scope.branch else None,head=scope.head),delivery=self.delivery(scope))
        elif route=='branches':
            result.update(items=self.branches(adapter,scope.branch),limit=LIST)
        elif route in ('issues','changes'):
            kind = {'issues':'issue','changes':'change'}[route]
            result.update(items=self.list_records(adapter,kind,getattr(adapter,route)()),limit=LIST)
        elif route=='issue':
            result['item'] = self.record(adapter,'issue',adapter.issue(params['number']))
            if str(result['item'].get('id'))!=str(params['number']):
                raise Conflict('平台 Issue 与请求身份不同。')
        elif route=='change':
            result.update(item=self.change(adapter,params['number']),delivery=DELIVERY)
        elif route=='reviews':
            result.update(items=self.reviews(scope,adapter),delivery=self.delivery(scope))
        else:
            sha = params['sha']
            self.target(adapter,params['target_type'],params['target'],sha)
            checks = self.checks(adapter,sha)
            result['sha'] = sha
            if route=='checks':
                result.update(items=checks,limit=LIST)
            else:
                check = params['check']
                self.selected(checks,check,'检查')
                jobs = self.list_records(adapter,'job',adapter.jobs(check,sha))
                if route=='jobs':
                    result.update(items=jobs,check=check,limit=LIST)
                else:
                    job = params['job']
                    self.selected(jobs,job,'作业')
                    log = self.record(adapter,'log',adapter.log(job,check,sha))
                    if (str(log.get('job_id')),str(log.get('check_id')),log.get('sha'))!=(str(job),str(check),sha):
                        raise Conflict('平台日志的提交、检查或作业身份不匹配。')
                    result['log'] = log
        self.still_bound(scope)
        return result

    def public_review(self, row):
        value = self.inspector.clean(take(row,('id','workspace','platform','repository','change','head_sha',
                                              'source_branch','target_branch','title','url','state','created_at')))
        if value['change']>JS_MAX:
            value['change'] = str(value['change'])
        return value

    def review(self, data):
        required = {'workspace','binding','change','head_sha','intent','authorization'}
        if (not isinstance(data,dict) or set(data)!=required
                or not isinstance(data['intent'],str) or not RUN.fullmatch(data['intent'])
                or not isinstance(data['binding'],str) or not IDENT.fullmatch(data['binding'])
                or not isinstance(data['head_sha'],str) or not SHA.fullmatch(data['head_sha'])
                or data['authorization']!='create-local-review'):
            raise Invalid('创建本机审核任务需要明确确认当前变更和提交。')
        try:
            ident = number(data['change'])
        except ValueError:
            raise Invalid('变更身份无效。') from None
        workspace, sha = data['workspace'],data['head_sha']
        binding, adapter = self.bind(workspace)
        if data['binding']!=binding.identity:
            raise Conflict('此审核确认对应先前的 Git 绑定，请刷新仓库后重新选择。')
        request = digest(canonical([workspace,adapter.platform,adapter.repository,ident,sha]))
        approved = digest(canonical([request,binding.identity]))
        intent = digest(canonical([workspace,data['intent']]))
        with self.store.db.tx(read=True) as tx:
            previous = get_row(tx,INTENT,intent)
            if previous:
                return self.replay_review(tx,previous,request,approved)
        item = self.change(adapter,ident)
        if item.get('head_sha')!=sha:
            raise Conflict('当前变更的提交已更新。请重新选择要审核的版本。')
        self.still_bound(binding)
        row = dict(id=request,workspace=workspace,platform=adapter.platform,repository=adapter.repository,
                   change=ident,head_sha=sha,title=item.get('title'),url=item.get('url'),
                   source_branch=item.get('source_branch'),target_branch=item.get('target_branch'),
                   binding_identity=binding.identity,state='AWAITING_INDEPENDENT_REVIEW',created_at=time.time())
        with self.store.db.tx() as tx:
            previous = get_row(tx,INTENT,intent)
            if previous:
                return self.replay_review(tx,previous,request,approved)
            old = get_row(tx,TASK,request)
            if old is None:
                tx.execute('INSERT INTO records VALUES(?,?,?)',(TASK,request,canonical(row).decode()))
            tx.execute('INSERT INTO records VALUES(?,?,?)',(INTENT,intent,canonical(dict(task=request,approved=approved)).decode()))
        return self.public_review(old or row)

    def replay_review(self, tx, previous, request, approved):
        if previous.get('task')!=request or previous.get('approved')!=approved:
            raise Conflict('同一审核意图对应不同仓库、变更或提交。')
        row = get_row(tx,TASK,request)
        if row is None:
            raise Conflict('审核任务的持久记录未核验。')
        return self.public_review(row)
