"""Discover native capabilities and freeze explicitly selected Skill directories."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from orchestration.common import Blocked, canonical, clean_env, digest
from .graph import Invalid
from .session_sources import opened


@lru_cache(maxsize=32)
def _probe(executable,provider,mtime,size):
    if provider not in ('codex','claude'):return dict(skills=False,subagents=False,reason='此工具尚未验证原生扩展接口')
    try:
        with tempfile.TemporaryDirectory(prefix='df-cap-') as home:
            args=['features','list'] if provider=='codex' else ['--help']
            r=subprocess.run([executable,*args],env=clean_env(home),capture_output=True,timeout=12)
        text=r.stdout.decode(errors='replace')
        supported=r.returncode==0 and (bool(re.search(r'^multi_agent\s+stable\s+',text,re.M)) if provider=='codex' else '--agents ' in text)
        return dict(skills=supported,subagents=supported,source='当前原生 CLI '+('features list' if provider=='codex' else '--help'),reason=None if supported else '原生接口未确认，未启用扩展')
    except (OSError,subprocess.TimeoutExpired):return dict(skills=False,subagents=False,reason='原生能力探测失败')


def capabilities(profile):
    try:
        p=Path(profile['executable']);s=p.stat()
        return dict(_probe(str(p),profile['provider'],s.st_mtime_ns,s.st_size))
    except OSError:return dict(skills=False,subagents=False,reason='原生工具不可用')


def skill_header(text):
    """A deliberately small native YAML subset, with no aliases or policy keys.

    Reject quoted/merged/unknown keys rather than regex-filtering only their
    unquoted spelling. Native engines must not interpret a richer policy than
    the controller has inspected. Ordinary descriptive block strings work.
    """
    lines=text.splitlines()
    if not lines or lines[0]!='---':raise Blocked('Skill 需要带 name 和 description 的标准头部')
    try:end=lines.index('---',1)
    except ValueError:raise Blocked('Skill 头部未完整关闭') from None
    allowed={'name','description','license','compatibility','metadata'};values={};current=None;block=False
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):continue
        if line[0].isspace():
            if current=='metadata':
                meta=re.fullmatch(r'([a-zA-Z0-9_-]+):\s+(.+)',line.strip())
                if not meta or meta[1]=='<<' or meta[2].lstrip().startswith(('{','[','&','*','!','|','>')):
                    raise Blocked('Skill metadata 仅支持简单描述字段，不接受别名、标签或嵌套配置')
                continue
            if block:
                values[current]+=' '+line.strip()
                continue
            raise Blocked('Skill 头部包含不支持的多行配置')
        match=re.fullmatch(r'([a-z][a-z0-9_-]*):(?:\s+(.*))?',line)
        if not match or match[1] not in allowed or match[1] in values:
            raise Blocked('Skill 头部只支持描述字段；不接受权限、模型、Hooks、分叉、合并或带引号的配置键')
        current=match[1];value=(match[2] or '').strip();block=value in ('|','>','|-','>-','|+','>+')
        if current=='metadata':
            if value:
                try:meta=json.loads(value)
                except ValueError:raise Blocked('Skill metadata 需要普通缩进描述或 JSON 字符串映射') from None
                if not isinstance(meta,dict) or any(not isinstance(v,str) for v in meta.values()):raise Blocked('Skill metadata 需要字符串映射')
            values[current]='';block=False;continue
        if block:values[current]='';continue
        if value.startswith('"'):
            try:value=json.loads(value)
            except ValueError:raise Blocked('Skill 描述字段需要普通文本') from None
            if not isinstance(value,str):raise Blocked('Skill 描述字段需要普通文本')
        elif value.startswith("'") and value.endswith("'"):value=value[1:-1].replace("''","'")
        elif value.startswith(('{','[','&','*','!')):raise Blocked('Skill 描述字段不接受 YAML 对象、别名或类型标签')
        values[current]=value
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',values.get('name','')) or len(values['name'])>64:
        raise Blocked('Skill name 需为不超过64字的小写英文、数字及连字符')
    if not values.get('description','').strip() or len(values['description'])>4096:
        raise Blocked('Skill 需要简短、非空的 description')
    return values


def skill_files(root):
    """No links, special files, hidden credential bundles or executable policy."""
    files={};total=0;directories=0
    with opened(root,directory=True):pass
    for parent,dirs,names in os.walk(root,followlinks=False):
        directories+=1
        if directories>128 or len(Path(parent).relative_to(root).parts)>8:raise Blocked('Skill 目录超过深度或数量预算')
        for name in [*dirs,*names]:
            p=Path(parent)/name;info=p.lstat()
            if p.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_nlink!=1 and stat.S_ISREG(info.st_mode):raise Blocked('Skill 不能包含链接或特殊文件')
            if name.startswith('.') or name.lower() in ('credentials.json','auth.json','settings.json','config.toml','mcp.json','hooks.json') or name.lower().endswith(('.pem','.key')):raise Blocked('Skill 含不支持的私有配置或凭据文件')
        for name in names:
            p=Path(parent)/name
            with opened(p) as fd:raw=os.read(fd,524289)
            total+=len(raw)
            if len(raw)>524288 or total>2_000_000 or len(files)>=64:raise Blocked('Skill 超过文件或大小预算')
            files[p.relative_to(root).as_posix()]=raw
    if 'SKILL.md' not in files:raise Invalid('Skill 缺少 SKILL.md')
    text=files['SKILL.md'].decode('utf-8')
    skill_header(text)
    front=text.split('---',2)[1] if text.startswith('---\n') and len(text.split('---',2))==3 else ''
    if re.search(r'(?mi)^\s*(?:allowed-tools|hooks|context|agent|model|mcpServers|permissions)\s*:',front):raise Blocked('此 Skill 会改变模型、工具或子 Agent 策略，当前不允许挂载')
    return files


class AgentExtensions:
    def __init__(self,rt):self.rt=rt
    def catalog(self,workspace,profile):
        project=self.rt.session_center.workspace(workspace);p=self.rt.operator['profiles'].get(profile)
        if not p:raise Invalid('请选择已配置的 Agent')
        cap=capabilities(p);rows=[]
        if self.rt.operator.get('native_local',{}).get(p['provider']):
            return dict(capabilities=dict(skills=False,subagents=False,
                reason='本机原生模式沿用原客户端扩展；额外挂载与子 Agent 策略尚未验证'),
                skills=[],subagent_modes=['disabled'],
                notice='不会改写本机 Skills 或子 Agent 配置。旧隔离模式的已验证挂载仍保留。')
        if cap['skills'] and not project.get('protected_container'):
            roots=[Path(project['path'])/x for x in ('.agents/skills','.claude/skills','skills')]
            roots += [Path.home()/x for x in ('.agents/skills','.codex/skills','.claude/skills')]
            seen=set()
            for root in roots:
                if not root.is_dir() or root.is_symlink():continue
                try:
                    with opened(root,directory=True):pass
                    candidates=sorted(root.iterdir())[:100]
                    # Codex system Skills use one namespace level.
                    for folder in candidates:
                        if folder.name=='.system' and not folder.is_symlink():candidates += sorted(folder.iterdir())[:50]
                        if folder.is_symlink() or not folder.is_dir() or not (folder/'SKILL.md').is_file() or str(folder) in seen:continue
                        seen.add(str(folder));key=digest(canonical([workspace,profile,str(folder)]))
                        try:
                            files=skill_files(folder);manifest={k:digest(v) for k,v in files.items()};sha=digest(canonical(manifest));key=digest(canonical([workspace,profile,str(folder),sha]))
                            text=files['SKILL.md'].decode();header=skill_header(text)
                            row=dict(id=key,name=header['name'],available=True,sha256=sha,files=len(files),origin='项目' if folder.is_relative_to(project['path']) else '本机')
                            self.rt.flow.put('skill-source',key,dict(workspace=workspace,profile=profile,path=str(folder),sha256=sha))
                        except (OSError,ValueError,Blocked,Invalid):row=dict(id=key,name=folder.name,available=False,reason='包含不支持的配置、链接或超过预算')
                        rows.append(row)
                except (OSError,ValueError):continue
        return dict(capabilities=cap,skills=rows,subagent_modes=['disabled','readonly'] if cap['subagents'] else ['disabled'],notice='子 Agent 仅在 Ask/Plan 强制只读下开放；写入任务保持一个源码写入者。')
    def bind(self,workspace,node,binding):
        ids=node.get('skills',[]);policy=node.get('subagents','disabled')
        if not isinstance(ids,list) or len(ids)>6 or any(not isinstance(k,str) for k in ids) or len(set(ids))!=len(ids):raise Invalid('每个节点最多选择六个不同 Skill')
        if policy not in ('disabled','readonly'):raise Invalid('子 Agent 策略无效')
        if not ids and policy=='disabled':
            binding.update(skills=[],skill_ids=[],subagents='disabled');return
        cap=capabilities(binding)
        if ids and not cap['skills'] or policy!='disabled' and not cap['subagents']:raise Blocked(cap.get('reason') or '原生扩展接口不可用')
        if policy!='disabled' and (node.get('mode','Ask')=='Agent' or binding['role']=='implementer'):raise Blocked('子 Agent 当前只开放只读 Ask/Plan，写入任务必须保持一个源码写入者')
        selected=[]
        base=self.rt.storage/'skill-snapshots';base.mkdir(mode=0o700,exist_ok=True)
        with opened(base,directory=True):pass
        for key in ids:
            source=self.rt.flow.get('skill-source',key)
            if source['workspace']!=workspace or source['profile']!=binding['runtime_profile']:raise Blocked('Skill 不属于当前工作区和 Agent 配置')
            files=skill_files(Path(source['path']));manifest={k:digest(v) for k,v in files.items()};sha=digest(canonical(manifest))
            if sha!=source['sha256']:raise Blocked('Skill 已变化，请刷新后重新选择')
            target=base/sha
            if not target.exists():
                temporary=Path(tempfile.mkdtemp(prefix='.pack-',dir=base))
                try:
                    for rel,raw in files.items():
                        f=temporary/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(raw);f.chmod(0o600)
                    try:os.rename(temporary,target)
                    except OSError:
                        if not target.is_dir():raise
                finally:
                    if temporary.exists():
                        import shutil;shutil.rmtree(temporary)
            from orchestration.native_features import verify_skill
            verify_skill(dict(path=str(target),manifest=manifest,sha256=sha))
            selected.append(dict(id=key,path=str(target),manifest=manifest,sha256=sha))
        binding.update(skills=selected,skill_ids=ids,subagents=policy)
