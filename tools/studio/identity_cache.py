"""Reuse hashes only while kernel inode identity is unchanged, never by TTL.

Every read reopens without following symlinks and checks ctime as well as mtime.
Tree enumeration is repeated, so additions, removals and links remain visible.
"""
import hashlib,os,stat,threading
from collections import OrderedDict
from pathlib import Path
from orchestration.common import Blocked,canonical,digest
from .session_sources import opened

class IdentityCache:
    def __init__(self):self.values=OrderedDict();self.lock=threading.RLock()
    @staticmethod
    def stamp(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink)
    def file(self,path):
        with opened(path) as fd:
            before=os.fstat(fd);key=(str(path),self.stamp(before))
            with self.lock:value=self.values.get(key)
            if value is None:
                h=hashlib.sha256()
                while data:=os.read(fd,1024*1024):h.update(data)
                value=h.hexdigest()
            if self.stamp(before)!=self.stamp(os.fstat(fd)):raise Blocked('文件在身份校验期间发生变化')
            with self.lock:
                self.values[key]=value;self.values.move_to_end(key)
                while len(self.values)>4096:self.values.popitem(last=False)
            return value
    def tree(self,root):
        root=Path(root);rows=[]
        with opened(root,directory=True):pass
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d!='.git')
            links=[d for d in dirs if (Path(folder)/d).is_symlink()];dirs[:]=[d for d in dirs if d not in links]
            for name in sorted(files+links):
                if name=='.git':continue
                p=Path(folder)/name;s=p.lstat();rel=p.relative_to(root).as_posix()
                if stat.S_ISLNK(s.st_mode):
                    data=os.fsencode(os.readlink(p));sha=digest(data);mode='120000';size=len(data)
                elif stat.S_ISREG(s.st_mode):
                    sha=self.file(p);mode='100755' if s.st_mode&0o111 else '100644';size=s.st_size
                    if self.stamp(s)!=self.stamp(p.lstat()):raise Blocked('目录在身份校验期间发生变化')
                    if rel=='.gitmodules' or rel.endswith('.gitattributes') and b'filter=' in p.read_bytes():raise Blocked('submodule/LFS/clean filters not supported')
                else:raise Blocked('unsupported source node: '+rel)
                rows.append(dict(path=rel,mode=mode,size=size,sha256=sha))
        return digest(canonical(sorted(rows,key=lambda x:x['path'])))
