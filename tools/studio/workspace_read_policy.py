"""Conservative workspace ignore/sensitive rules, never an authority grant.

Workspace .gitignore/.ignore are deny filters. Negations cannot grant reads;
an explicitly authorized, non-sensitive single file may bypass ignore only.
No git command, global config, hooks, symlink or external policy is loaded.
"""
from contextlib import contextmanager
import fnmatch
import hashlib
import os
from pathlib import Path
import re

from orchestration.common import Blocked
from .graph import Invalid
from .session_sources import regular_at,Refused,opened

SENSITIVE=re.compile(r'(secret|token|password|api.?key|receipt|credential)|\.(pem|key|p12|pfx)$|(^|[._-])id_(rsa|dsa|ecdsa|ed25519)($|[._-])|(^|[._-])auth($|[._-])',re.IGNORECASE)


def safe_parts(value):
    if not isinstance(value,str) or len(value)>4096 or '\x00' in value:raise Invalid('文件路径无效')
    p=Path(value)
    if p.is_absolute() or '..' in p.parts or any(part.startswith('.') or SENSITIVE.search(part) or any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in part) for part in p.parts):
        raise Blocked('只允许查看工作区内的非敏感普通文件')
    return p


def stamp(s):
    return s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns


class IgnoreRules:
    def __init__(self,opener,*,snapshot=False,forbidden=lambda p:False):
        self.opener=opener;self.forbidden=forbidden
        self.directories={};self.files={};self.seen=set();self.total=0;self.rules_count=0
        self.frozen=False
        if snapshot:self.snapshot()
        self.frozen=snapshot

    def read(self,relative):
        try:return self._read(relative)
        except (OSError,UnicodeError,Refused):
            raise Blocked('无法固定工作区忽略规则；本次读取已停止') from None

    def _read(self,relative):
        with self.opener(relative) as directory:
            current=stamp(os.fstat(directory))
            values=[]
            for name in ('.gitignore','.ignore'):
                try:
                    with regular_at(name,dir_fd=directory) as fd:
                        before=os.fstat(fd)
                        if before.st_size>16000:raise Blocked('忽略规则超过16000字节预算；读取已停止')
                        raw=os.pread(fd,16001,0);after=os.fstat(fd)
                        if stamp(before)!=stamp(after) or after.st_nlink!=1 or len(raw)>16000:raise Blocked('忽略规则变化或超限；读取已停止')
                        text=raw.decode('utf-8')
                except FileNotFoundError:
                    values.append((name,None,[]));continue
                patterns=[]
                for line in text.splitlines():
                    if not line or line.startswith('#') or line.startswith('!'):continue
                    if len(line)>1024 or '\\' in line or any(ord(c)<32 for c in line):raise Blocked('忽略规则含未支持的转义或字符；读取已停止')
                    pattern=line.rstrip(' ').rstrip('/')
                    if not pattern:continue
                    anchored=pattern.startswith('/');pattern=pattern.lstrip('/')
                    # fnmatch '*' may cover extra descendants (safe overdeny).
                    # Preserve Git's zero-directory meaning for every '**/'.
                    regex=fnmatch.translate(pattern.replace('**/','\x01')).replace('\x01','(?:.*/)?')
                    patterns.append((anchored or '/' in pattern,re.compile(regex)))
                    if len(patterns)>256:raise Blocked('忽略规则超过256条预算；读取已停止')
                values.append((name,(stamp(after),hashlib.sha256(raw).hexdigest()),patterns))
            if stamp(os.fstat(directory))!=current:raise Blocked('目录在忽略规则读取期间变化')
            return current,values

    def remember(self,relative):
        key=Path(relative).as_posix();current,values=self.read(relative)
        if key in self.directories:
            prior=self.files[key]
            if self.directories[key]!=current or [(n,s) for n,s,_ in values]!=[(n,s) for n,s,_ in prior]:
                raise Blocked('忽略规则或授权目录已变化；本次读取停止')
        elif getattr(self,'frozen',False):
            raise Blocked('目录不在本轮固定的读取规则中')
        else:
            self.directories[key]=current;self.files[key]=values
            self.total+=sum(s[0][3] for _,s,_ in values if s is not None)
            self.rules_count+=sum(len(p) for _,_,p in values)
            if self.total>128000 or self.rules_count>2048:raise Blocked('工作区忽略规则总量超过预算')
        self.seen.add(key)
        return values

    def denied(self,relative):
        relative=Path(relative)
        safe_parts(relative.as_posix())
        # Evaluate each ancestor's deny rules against target and its parents.
        parts=relative.parts
        for depth in range(len(parts)+1):
            if depth==len(parts) and parts:break
            directory=Path(*parts[:depth]) if depth else Path('.')
            if self.frozen and directory.as_posix() not in self.directories:return True
            values=self.remember(directory)
            remaining=parts[depth:]
            if not remaining:continue
            prefixes=['/'.join(remaining[:i]) for i in range(1,len(remaining)+1)]
            for _,_,patterns in values:
                for anchored,pattern in patterns:
                    targets=prefixes if anchored else remaining
                    if any(pattern.fullmatch(item) for item in targets):return True
        return False

    def validate(self):
        for key in tuple(self.seen):self.remember(Path(key))

    def snapshot(self):
        pending=[Path('.')];entries=0
        while pending:
            relative=pending.pop()
            if len(self.directories)>=2048 or len(relative.parts)>32:raise Blocked('读取规则扫描超过目录或深度预算')
            self.remember(relative)
            with self.opener(relative) as directory,os.scandir(directory) as iterator:
                for entry in iterator:
                    entries+=1
                    if entries>20000:raise Blocked('读取规则扫描超过20000项预算')
                    child=relative/entry.name
                    try:safe_parts(child.as_posix())
                    except (Blocked,Invalid):continue
                    if self.forbidden(child) or not entry.is_dir(follow_symlinks=False):continue
                    if not self.denied(child):pending.append(child)
        self.seen={'.'}


def ignored(root,relative):
    """One UI request's current rules; native turns use a full fixed snapshot."""
    @contextmanager
    def opener(value):
        with opened(Path(root)/value,directory=True) as fd:yield fd
    policy=IgnoreRules(opener)
    denied=policy.denied(relative);policy.validate()
    return denied
