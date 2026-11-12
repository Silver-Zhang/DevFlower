"""Codex native title readback and explicit, leased metadata mutation."""
from contextlib import contextmanager
from pathlib import Path
import re

from orchestration.common import Blocked, canonical, digest
from orchestration.privacy import credentials, redact
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.native_codex_image import NativeImage
from studio.native_codex_rpc import CodexRPC, metadata_guard
from studio.server import Conflict

from .native_session_links import claim_key, verified_workflow
from .native_session_views import safe_alias, verify_bytes
from .native_writer_state import require_native_writer_idle
from .session_identity import SessionRefused, StorageRootIdentity
from .session_lease import CODEX_COORDINATION_VERSION, codex_presentation_guard, production_session_gate
from .session_operations import operation_id

KIND = 'native-session-title'
SHA = re.compile(r'[0-9a-f]{64}')


class NativeSessionTitles:
    def __init__(self, operations):
        self.operations, self.store = operations, operations.store

    def managed(self, binding):
        sid = binding['session_id']
        key = claim_key(binding['config_root'],sid)
        try:
            claim = self.store.db.get('standalone-native-claim',key)
        except Invalid:
            proof = verified_workflow(self.store,binding['config_root'],sid)
            if (proof['workspace'],proof['cwd'],proof['home']) != (binding['project'],binding['cwd'],binding['home']):
                raise SessionRefused('LOCATOR_REFUSED')
            return
        saved = self.store.db.get('native-session-operation',claim['operation'])
        if (saved.get('authorization', 'explicit-session-send') != self.operations.authority or saved.get('state')!='COMPLETED'
                or saved.get('observed_session')!=sid
                or saved.get('receipt',{}).get('session_id')!=sid or saved.get('workspace')!=binding['project']
                or any(saved['binding'].get(key)!=binding[key] for key in ('provider','home','config_root','cwd','root_identity'))):
            raise SessionRefused('LOCATOR_REFUSED')

    def locate(self, workspace, handle, revision):
        source, approved = self.operations.profiles['codex']
        locator, record = self.operations.resolve(workspace,handle,revision)
        cwd = self.store.workspace(workspace)['path']
        if (locator.provider!='codex' or record.source.profile!=source.profile
                or locator.original_cwd!=cwd or len(record.cwd_evidence)!=1):
            raise SessionRefused('LOCATOR_REFUSED')
        binding = dict(approved,project=workspace,cwd=cwd,session_id=locator.native_uuid,
                       cwd_identity=StorageRootIdentity.capture(cwd).fields())
        self.operations.require_scope(binding)
        self.managed(binding)
        return binding,locator,record

    @contextmanager
    def client(self, binding):
        native = self.operations.codex
        if native.version!=CODEX_COORDINATION_VERSION:
            raise SessionRefused('PROFILE_REFUSED')
        with metadata_guard(self.store.state,timeout=20) as remaining:
            with NativeImage(native.image['path'],expected_sha256=native.image['sha256']) as image:
                with CodexRPC(image.path,binding['home'],binding['config_root'],binding['cwd'],
                              timeout=remaining,executable_image=image,untrusted_project=binding['cwd']) as rpc:
                    yield rpc

    def checked_thread(self, rpc, binding, record):
        thread = rpc.request('thread/read',{'threadId':binding['session_id'],'includeTurns':False})['thread']
        expected_path = str(record.source.profile.root/record.ref.relative)
        if (thread.get('id'),thread.get('cwd'),thread.get('path')) != (binding['session_id'],binding['cwd'],expected_path):
            raise SessionRefused('LOCATOR_REFUSED')
        title = thread.get('name')
        if title is not None and (not isinstance(title,str) or len(title)>4096):
            raise SessionRefused('LOCATOR_REFUSED')
        return title

    def projection(self, binding, handle, title):
        text = safe_text(redact(title or '',credentials(Path(binding['home']))))
        return dict(workspace=binding['project'],handle=handle,session_id=binding['session_id'],
                    native_title=text[:160] or None,title_revision=digest(canonical(title)))

    def recheck(self, workspace, handle, binding, locator, record):
        self.operations.require_scope(binding)
        path,identity,proof = self.operations.reader.workspace(workspace)
        with self.operations.reader.verified_record(workspace,handle,path,identity,record) as (view,current):
            if current!=locator:
                raise SessionRefused('LOCATOR_REFUSED')
            verify_bytes(view,record.ref)
        self.operations.reader.require_proof(proof)

    def read(self, workspace, handle, revision):
        binding, _, record = self.locate(workspace,handle,revision)
        with self.client(binding) as rpc:
            return self.projection(binding,handle,self.checked_thread(rpc,binding,record))

    def operation(self, workspace, intent, *, verify=False):
        self.store.workspace(workspace)
        key = operation_id(workspace,intent)
        try:
            row = self.store.db.get(KIND,key)
        except Invalid:
            return {'workspace':workspace,'intent_key':intent,'state':'UNREGISTERED'}
        value = dict(workspace=workspace,intent_key=intent,handle=row['handle'])
        if verify and row['state']!='COMPLETED':
            row = self.verify_pending(row)
        if row['state']=='COMPLETED':
            return dict(value,state='COMPLETED',**row['result'])
        return dict(value,state='RECOVERY_REQUIRED',reason='原生标题修改结果待核验，请读取当前原生标题。')

    def verify_pending(self, row):
        workspace,handle = row['workspace'],row['handle']
        listing = self.operations.reader.listing(workspace)
        binding,locator,record = self.locate(workspace,handle,listing['revision'])
        if binding!=row['binding'] or str(record.ref.relative)!=row['relative']:
            raise SessionRefused('LOCATOR_REFUSED')
        sid = binding['session_id']
        with production_session_gate(binding,locator), codex_presentation_guard(binding['config_root'],[sid]) as fence:
            require_native_writer_idle(self.store,binding,sid,ignore_title=row['id'])
            self.recheck(workspace,handle,binding,locator,record)
            current = self.store.db.get(KIND,row['id'])
            if current['state']=='COMPLETED':
                return current
            with self.client(binding) as rpc:
                title = self.checked_thread(rpc,binding,record)
                if digest(canonical(title))!=row['requested_title_sha256']:
                    return current
                fence()
                value = self.projection(binding,handle,title)
                result = {key:value[key] for key in ('session_id','native_title','title_revision')}
                return self.store.db.change(KIND,row['id'],dict(state='COMPLETED',effect_uncertain=False,result=result))

    def rename(self, data):
        if set(data)!={'workspace','handle','revision','intent_key','expected_title_revision','title'}:
            raise Invalid('修改标题需要明确的会话和当前原生标题版本。')
        title = safe_alias(data['title'])
        expected = data['expected_title_revision']
        if not isinstance(expected,str) or not SHA.fullmatch(expected):
            raise Invalid('请先读取当前原生标题。')
        workspace,handle,intent = data['workspace'],data['handle'],data['intent_key']
        self.store.workspace(workspace)
        key = operation_id(workspace,intent)
        request = digest(canonical(data))
        try:
            prior = self.store.db.get(KIND,key)
        except Invalid:
            prior = None
        if prior:
            if prior['request_sha256']!=request:
                raise Conflict('同一标题修改意图对应不同内容。')
            return self.operation(workspace,intent)
        binding,locator,record = self.locate(workspace,handle,data['revision'])
        if safe_text(redact(title,credentials(binding['home'])))!=title:
            raise Invalid('原生标题包含受保护内容。')
        sid = binding['session_id']
        with production_session_gate(binding,locator), codex_presentation_guard(binding['config_root'],[sid]) as fence:
            require_native_writer_idle(self.store,binding,sid)
            self.recheck(workspace,handle,binding,locator,record)
            with self.client(binding) as rpc:
                before = self.checked_thread(rpc,binding,record)
                if digest(canonical(before))!=expected:
                    raise Conflict('原生标题已变化，请重新读取。')
                row = dict(id=key,workspace=workspace,handle=handle,target=sid,binding=binding,state='RUNNING',
                           request_sha256=request,requested_title_sha256=digest(canonical(title)),
                           relative=str(record.ref.relative),effect_uncertain=True)
                with self.store.db.tx() as db:
                    if db.execute('SELECT 1 FROM records WHERE kind=? AND id=?',(KIND,key)).fetchone():
                        raise Conflict('此标题操作已登记，请核验原意图。')
                    db.execute('INSERT INTO records VALUES(?,?,?)',(KIND,key,canonical(row).decode()))
                try:
                    fence()
                    rpc.request('thread/name/set',{'threadId':sid,'name':title})
                    actual = self.checked_thread(rpc,binding,record)
                    if actual!=title:
                        raise SessionRefused('RECOVERY_REQUIRED')
                    fence()
                    value = self.projection(binding,handle,actual)
                    result = {key:value[key] for key in ('session_id','native_title','title_revision')}
                    self.store.db.change(KIND,key,dict(state='COMPLETED',effect_uncertain=False,result=result))
                except (OSError,ValueError,Blocked,KeyError,TypeError):
                    self.store.db.change(KIND,key,{'state':'RECOVERY_REQUIRED'})
        return self.operation(workspace,intent)
