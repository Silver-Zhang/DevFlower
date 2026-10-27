"""Workspace/profile-scoped user model choices, never execution certificates."""
from pathlib import Path
import hashlib
import os
from functools import lru_cache
import time
from orchestration.common import canonical, digest, Blocked
from .graph import Invalid
from .server import Conflict
from .session_sources import opened


@lru_cache(maxsize=32)
def _executable_hash(path, device, inode, size, mtime, ctime):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(2*1024*1024),b''):value.update(part)
    return value.hexdigest()


def profile_identity(profile):
    files = []
    for source, target in profile.get('readonly_files', []):
        with opened(source) as fd:
            raw = os.read(fd, 200001)
        if len(raw) > 200000: raise Blocked('原生配置超过读取预算')
        files.append([source, target, digest(raw)])
    exe = Path(profile['executable']).resolve() if profile.get('executable') else None
    exe_hash=None
    if exe and exe.is_file():
        before=exe.stat();stamp=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
        exe_hash=_executable_hash(str(exe),*stamp)
        after=exe.stat()
        if stamp!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Blocked('原生工具正在更新，配置待确认')
    auth_identity=None
    if profile.get('provider')=='copilot':
        from .copilot_adapter import credential
        # Account/credential rotation invalidates an old execution receipt.
        auth_identity=digest(credential(profile).encode())
    return digest(canonical(dict(auth_identity=auth_identity,provider=profile.get('provider'), executable=str(exe) if exe else None,
        executable_sha256=exe_hash,
        files=files)))


class ModelRegistration:
    def __init__(self, rt): self.rt = rt

    def key(self, workspace, profile, model): return digest(canonical([workspace,profile,model]))

    def choices(self, workspace, profile):
        if not workspace: return {}
        self.rt.session_center.workspace(workspace)
        p=self.rt.operator['profiles'][profile];fp=profile_identity(p)
        return {r['model']:r for r in self.rt.flow.list('model-registration')
                if r['workspace']==workspace and r['profile']==profile and r['state']=='CONFIGURED'
                and r['operator']==self.rt.operator_sha256 and r['policy']==self.rt.policy_sha and r['environment']==fp}

    def prepare(self, data):
        if set(data)-{'workspace','profile','model','effort'} or not {'workspace','profile','model'}<=set(data):
            raise Invalid('请选择工作区、Agent与模型')
        self.rt.check_operator();self.rt.session_center.workspace(data['workspace'])
        p=self.rt.operator['profiles'].get(data['profile'])
        if not p: raise Invalid('Agent配置不存在')
        cache=self.rt.flow.get('model-discovery',data['profile'])
        if cache.get('error') or not 0<=time.time()-cache.get('at',0)<300 or cache.get('profile_sha256')!=digest(canonical(p)):
            # ModelCatalog uses a JSON profile key for backward compatibility.
            import json
            if cache.get('error') or not 0<=time.time()-cache.get('at',0)<300 or cache.get('profile_sha256')!=digest(json.dumps(p,sort_keys=True).encode()):
                raise Blocked('模型目录已过期或配置变化，请刷新后再配置')
        if cache.get('environment_sha256')!=profile_identity(p):raise Blocked('模型目录的认证或服务已变化，请刷新')
        row=next((r for r in cache.get('models',[]) if r['id']==data['model']),None)
        if not row: raise Invalid('此模型不在当前真实目录中，请刷新后选择')
        from .model_catalog import selectable_efforts
        allowed,effort_source=selectable_efforts(self.rt,data['profile'],p,row)
        effort=data.get('effort') or allowed[0]
        if not isinstance(effort,str) or effort not in allowed: raise Invalid('请选择目录报告的强度，或沿用此Agent已配置的强度后验证')
        key=self.key(data['workspace'],data['profile'],data['model'])
        try: prior=self.rt.flow.get('model-registration',key)
        except Invalid: prior=None
        return self.rt.db.prepare(dict(purpose='model-registration',workspace=data['workspace'],profile=data['profile'],
            model=data['model'],effort=effort,key=key,environment=profile_identity(p),operator=self.rt.operator_sha256,
            policy=self.rt.policy_sha,catalog_sha256=digest(canonical(cache)),prior_sha256=digest(canonical(prior)),
            human_questions=['配置此模型供当前工作区选择；不调用模型、不授予执行权限。首次使用仍需真实连接检查。'],
            capability_source=effort_source))

    def select(self,data):
        # A model pick is a local selection, not a second approval ceremony.
        # The same fresh account/profile/effort contract still applies; this
        # record never grants execution or changes workspace permissions.
        permit=self.prepare(data)
        return self.confirm(permit['id'],permit['sha256'],'操作者在模型选择器直接选择此模型和强度；只保存选择，不授予执行权限。')

    def confirm(self, id, sha256, answer):
        self.rt.check_operator();value,_=self.rt.db.consume(id,sha256,[answer])
        if value.get('purpose')!='model-registration':raise Blocked('不是模型配置确认')
        p=self.rt.operator['profiles'].get(value['profile'])
        if not p or value['operator']!=self.rt.operator_sha256 or value['policy']!=self.rt.policy_sha or profile_identity(p)!=value['environment']:
            raise Conflict('Agent配置或认证已变化，请重新配置')
        cache=self.rt.flow.get('model-discovery',value['profile'])
        if digest(canonical(cache))!=value['catalog_sha256'] or not 0<=time.time()-cache.get('at',0)<300:
            raise Conflict('模型目录已变化或过期，请刷新后重新选择')
        with self.rt.flow.tx() as db:
            import json
            raw=db.execute("SELECT data FROM records WHERE kind='model-registration' AND id=?",(value['key'],)).fetchone()
            prior=json.loads(raw[0]) if raw else None
            if digest(canonical(prior))!=value['prior_sha256']:raise Conflict('模型配置已被其他操作更新')
            record={k:value[k] for k in ('workspace','profile','model','effort','environment','operator','policy','capability_source')}
            record.update(id=value['key'],state='CONFIGURED',configured_at=time.time())
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('model-registration',value['key'],canonical(record).decode()))
        return dict(configured=True,model=value['model'],effort=value['effort'],execution_allowed=False,capability_source=value['capability_source'])

    def require(self,binding):
        if not binding.get('model_registration_key'):return
        row=self.rt.flow.get('model-registration',binding['model_registration_key'])
        if (row['state']!='CONFIGURED' or digest(canonical(row))!=binding.get('model_registration_sha256') or
            row['environment']!=profile_identity(binding) or row['operator']!=self.rt.operator_sha256 or row['policy']!=self.rt.policy_sha):
            raise Blocked('模型配置或认证已变化，旧连接验证不能继续使用')
