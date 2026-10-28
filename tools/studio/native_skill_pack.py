"""Per-call frozen Skill resources for the ordinary native Codex executor.

These files are data, not native Skill registrations or a permission grant.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import stat
import tempfile

from orchestration.common import Blocked, canonical, digest
from orchestration.native_features import verify_skill
from .agent_extensions import skill_files, skill_header
from .session_sources import opened


def _overlap(a,b):return a==b or a.is_relative_to(b) or b.is_relative_to(a)


def _root(cwd,protected,roots):
    try:
        work=Path(cwd).resolve(strict=True)
        denied=[Path(p).resolve(strict=False) for p in protected]
    except (OSError,ValueError,RuntimeError):
        raise Blocked('无法核验 Skill 资源的工作区或保护范围；未发送任务') from None
    for value in roots:
        try:
            path=Path(value)
            if not path.is_absolute() or str(path)!=str(path.resolve(strict=True)):continue
            if _overlap(path,work) or any(_overlap(path,p) for p in denied):continue
            with opened(path,directory=True) as fd:
                info=os.fstat(fd)
                if info.st_uid not in (0,os.getuid()) or info.st_mode&0o022 and not info.st_mode&stat.S_ISVTX:continue
            return path
        except (OSError,ValueError,RuntimeError):continue
    raise Blocked('没有可用的工作区与保护范围之外的安全 Skill 资源位置；未发送任务')


def _files(value, secrets):
    source=value.get('source')
    if not isinstance(source,str):raise Blocked('Skill 来源身份未确认；未发送任务')
    source_files=skill_files(Path(source));files=skill_files(Path(value['path']))
    for current in (source_files,files):
        manifest={k:digest(v) for k,v in current.items()}
        if manifest!=value['manifest'] or digest(canonical(manifest))!=value['sha256']:
            raise Blocked('Skill 来源或冻结内容已变化；未发送任务')
        needles=[s.encode() for s in secrets if s]
        if any(needle in rel.encode() or needle in raw for rel,raw in current.items() for needle in needles):
            raise Blocked('Skill 资源含已知认证信息；未发送任务')
    verify_skill(value)
    return files


class NativeSkillPack:
    def __init__(self,root,entries,values,secrets,identity,cwd,protected):
        self.root,self.entries,self.values,self.secrets,self.identity=root,entries,values,secrets,identity
        self.cwd,self.protected=cwd,protected
        self.cleanup_attempted=False;self.cleanup_warning=None;self.cleanup_info=None
    def verify(self):
        _root(self.cwd,self.protected,(str(self.root.parent),))
        st=self.root.lstat()
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev,st.st_ino)!=self.identity:
            raise Blocked('Skill 资源包身份已变化；未发送任务')
        if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
            raise Blocked('Skill 资源包权限已变化；未发送任务')
        if {p.name for p in self.root.iterdir()}!={e['id'] for e in self.entries}:
            raise Blocked('Skill 资源包内容已变化；未发送任务')
        for entry,value in zip(self.entries,self.values):
            _files(value,self.secrets)
            actual=skill_files(self.root/entry['id'])
            if {k:digest(v) for k,v in actual.items()}!=value['manifest']:
                raise Blocked('Skill 资源包内容已变化；未发送任务')
            expected={str(Path(rel).parent) for rel in value['manifest'] if str(Path(rel).parent)!='.'}
            expected|={str(parent) for rel in list(expected) for parent in Path(rel).parents if str(parent)!='.'}
            actual_dirs=set()
            for parent,dirs,names in os.walk(self.root/entry['id'],followlinks=False):
                if stat.S_IMODE(Path(parent).lstat().st_mode)!=0o700:
                    raise Blocked('Skill 资源包目录权限已变化；未发送任务')
                actual_dirs.update((Path(parent)/name).relative_to(self.root/entry['id']).as_posix() for name in dirs)
                for name in names:
                    if stat.S_IMODE((Path(parent)/name).lstat().st_mode)!=0o400:
                        raise Blocked('Skill 资源包文件权限已变化；未发送任务')
            if actual_dirs!=expected:raise Blocked('Skill 资源包目录已变化；未发送任务')
    def prompt(self,prompt):
        lines=['以下为本次明确选择的 DevFlower 冻结 Skill 包，作为资料与指令使用，不扩大工具、文件或交付权限。请按需读取入口及其资源目录中的 references/scripts；这不是原生 Skill 注册。']
        for e in self.entries:
            lines.append('名称: '+e['name']+'\n清单 SHA-256: '+e['sha256']+'\n入口: '+e['entry']+'\n资源根: '+e['root'])
        return prompt+'\n\n'+'\n\n'.join(lines)
    def cleanup(self):
        if not self.cleanup_attempted:
            self.cleanup_attempted=True
            outcome=_cleanup(self.root,self.identity)
            if outcome and outcome.get('status')=='retained_empty':self.cleanup_info=outcome
            else:self.cleanup_warning=outcome
        return self.cleanup_warning
    def receipt(self):
        return [dict(e,**({'cleanup_warning':self.cleanup_warning} if self.cleanup_warning else {}),**({'cleanup_info':self.cleanup_info} if self.cleanup_info else {})) for e in self.entries]


def _cleanup(root,identity):
    try:
        with opened(root.parent,directory=True) as parent:
            fd=os.open(root.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
            try:
                now=os.fstat(fd)
                if not stat.S_ISDIR(now.st_mode) or (now.st_dev,now.st_ino)!=identity:
                    return dict(status='identity_changed',path=str(root),reason='临时包位置已变化，未删除替代内容；需人工核验')
                if now.st_uid!=os.getuid() or stat.S_IMODE(now.st_mode)!=0o700:
                    return dict(status='cleanup_failed',path=str(root),reason='临时包根权限或属主已变化，未清理；需人工核验')
                # Delete only beneath the pinned inode, even if its name is
                # replaced while cleanup runs. rmdir('.') is intentionally
                # refused by Linux; the named root is handled separately.
                def onerror(function,path,exc_info):
                    exc=exc_info[1]
                    if function is os.rmdir and path=='.' and isinstance(exc,OSError) and exc.errno==errno.EINVAL:return
                    raise exc
                shutil.rmtree('.',dir_fd=fd,onerror=onerror)
                end=os.fstat(fd)
                if end.st_uid!=os.getuid() or stat.S_IMODE(end.st_mode)!=0o700:
                    return dict(status='cleanup_failed',path=str(root),reason='临时包根权限或属主已变化；需人工核验')
                current=os.stat(root.name,dir_fd=parent,follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=identity:
                    return dict(status='identity_changed',path=str(root),reason='临时包位置已变化，未删除替代内容；需人工核验')
                # Observation only: never use this as an atomic deletion check.
                # A replacement after this read remains possible, so the info
                # record explicitly says the path may subsequently change.
                current=os.stat(root.name,dir_fd=parent,follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev,current.st_ino)!=identity:
                    return dict(status='identity_changed',path=str(root),reason='临时包位置已变化，未删除替代内容；需人工核验')
            finally:os.close(fd)
        return dict(status='retained_empty',path=str(root),reason='已固定的资源目录内容已移除并保留空根；记录路径可能随后变化')
    except FileNotFoundError:
        return dict(status='missing',path=str(root),reason='临时包位置已消失，无法确认是否移走；需人工核验')
    except Exception as exc:
        return dict(status='cleanup_failed',path=str(root),reason='临时包清理失败；需人工核验：'+str(exc))


@contextmanager
def native_skill_pack(binding,cwd,secrets,*,roots=('/tmp','/dev/shm')):
    values=binding.get('skills')
    if (not isinstance(values,list) or not 1<=len(values)<=6 or
        binding.get('provider')!='codex' or binding.get('execution_backend')!='native-local' or
        binding.get('native_interaction')!='ordinary' or binding.get('subagents','disabled')!='disabled'):
        raise Blocked('冻结 Skill 包仅支持本机 Codex 普通操作；未发送任务')
    protected=binding.get('native_protected')
    if not isinstance(protected,list) or not protected:raise Blocked('缺少原生保护范围；未发送任务')
    root=None
    for candidate in roots:
        try:
            parent=_root(cwd,protected,(candidate,))
            root=Path(tempfile.mkdtemp(prefix='devflower-skill-',dir=parent));break
        except (Blocked,OSError):continue
    if root is None:raise Blocked('没有可用的工作区与保护范围之外的安全 Skill 资源位置；未发送任务')
    st=root.lstat();identity=(st.st_dev,st.st_ino)
    pack=None;failure=None
    try:
        entries=[];ids=set()
        for value in values:
            key=value.get('id')
            if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key) or key in ids:raise Blocked('Skill 身份无效；未发送任务')
            ids.add(key);files=_files(value,secrets);dest=root/key;dest.mkdir(mode=0o700)
            for rel,raw in files.items():
                target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o400)
                with os.fdopen(fd,'wb') as stream:stream.write(raw)
            name=skill_header(files['SKILL.md'].decode('utf-8'))['name']
            entries.append(dict(id=key,name=name,sha256=value['sha256'],files=dict(value['manifest']),root=str(dest),entry=str(dest/'SKILL.md')))
        pack=NativeSkillPack(root,entries,values,secrets,identity,cwd,protected);pack.verify();yield pack
    except BaseException as exc:
        failure=exc
        raise
    finally:
        warning=pack.cleanup() if pack is not None else _cleanup(root,identity)
        if pack is not None and warning is None:warning=pack.cleanup_info
        if warning and failure is not None:
            failure.add_note(warning['reason']+'：'+warning['path'])
            if isinstance(failure,Exception):failure.args=(str(failure)+'；'+warning['reason']+'：'+warning['path'],)
