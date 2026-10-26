"""Fixed native extension arguments and immutable, scoped Skill mounts."""
import json
import os
from pathlib import Path
import shutil
import stat
from .common import Blocked,canonical,digest


def verify_skill(value):
    root=Path(value['path']);actual={}
    if root.is_symlink() or not root.is_dir():raise Blocked('Skill snapshot unavailable')
    for parent,dirs,names in os.walk(root,followlinks=False):
        for name in [*dirs,*names]:
            p=Path(parent)/name;s=p.lstat()
            if p.is_symlink() or not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)):raise Blocked('Skill snapshot links refused')
        for name in names:
            p=Path(parent)/name;s=p.lstat()
            if s.st_nlink!=1 or s.st_size>524288:raise Blocked('Skill snapshot unsafe')
            actual[p.relative_to(root).as_posix()]=digest(p.read_bytes())
    if actual!=value['manifest'] or digest(canonical(actual))!=value['sha256']:raise Blocked('Skill snapshot changed')


def diagnostic_skills(binding,artifacts):
    """Keep native Skill paths/options, exclude business text from a nonce call.

    Admission still pins the ORIGINAL binding/hash. This verifies CLI/model
    configuration, not the semantics of business Skill content or its scripts.
    """
    result=[]
    for value in binding.get('skills',[]):
        verify_skill(value)
        root=Path(artifacts)/('diagnostic-skill-'+value['id']);root.mkdir(mode=0o700)
        raw=('---\nname: model-check-'+value['id'][:16]+'\ndescription: Public connection-check placeholder; no business content.\n---\nDIAGNOSTIC_ONLY. This is not a business Skill. Answer the connection nonce without invoking Skills.\n').encode()
        (root/'SKILL.md').write_bytes(raw);(root/'SKILL.md').chmod(0o600)
        manifest={'SKILL.md':digest(raw)}
        result.append(dict(id=value['id'],path=str(root),manifest=manifest,sha256=digest(canonical(manifest))))
    return result


def native_args(binding):
    enabled=binding.get('subagents','disabled')=='readonly'
    if enabled and (binding.get('role')=='implementer' or binding.get('execution_mode','Ask')=='Agent' or binding.get('isolation')!='bwrap'):raise Blocked('Native subagents require enforced read-only source')
    if binding['provider']=='codex':
        values=['features.multi_agent='+str(enabled).lower(),'agents.enabled='+str(enabled).lower()]
        if enabled:values += ['agents.max_threads=2','agents.max_depth=1','agents.default_subagent_model='+json.dumps(binding['model']),'agents.default_subagent_reasoning_effort='+json.dumps(binding['effort'])]
        if 'skills' in binding:
            values += ['skills.config='+('['+','.join('{path='+json.dumps(str(Path(binding['home'])/'.agents/skills'/v['id']))+',enabled=true}' for v in binding['skills'])+']')]
        return [a for v in values for a in ('-c',v)],{}
    if binding['provider']=='claude':
        if enabled:
            worker=dict(description='只读分析子任务；不得修改文件或扩大权限',prompt='在当前已授权只读范围内分析，返回简短结果。禁止继续创建子 Agent。',tools=[t for t in binding.get('tools',['Read','Glob','Grep']) if t in ('Read','Glob','Grep')],model='inherit')
            return ['--agents',json.dumps({'devflower-reader':worker})],dict(CLAUDE_CODE_SUBAGENT_MODEL=binding['model'],CLAUDE_CODE_SUBAGENT_MODEL_FORCE='1')
        return [],{}
    return [],{}


def skill_mounts(binding,cwd,artifacts):
    if 'skills' not in binding:return [],[]
    if binding.get('isolation')!='bwrap':raise Blocked('Skills require enforced readonly mounts')
    # A new invocation owns its pack. Reconnect does not modify it.
    pack=Path(artifacts)/('skill-pack-'+os.urandom(12).hex());pack.mkdir(mode=0o700)
    for value in binding['skills']:
        verify_skill(value);shutil.copytree(value['path'],pack/value['id'],symlinks=False)
        verify_skill(dict(value,path=str(pack/value['id'])))
    targets=['.agents/skills','.codex/skills'] if binding['provider']=='codex' else ['.claude/skills']
    mounts=[(str(pack),rel) for rel in targets]
    # Mask unselected project Skills so auto-discovery cannot bypass selection.
    masks=[str(Path(cwd)/rel) for rel in ('.agents/skills','.claude/skills','.codex/skills') if (Path(cwd)/rel).exists()]
    return mounts,masks
