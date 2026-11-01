"""Immutable, static native CLI image. Launchers and dynamic ELF fail closed.

An operator-selected executable is trusted code, not authenticated by ELF shape.
Snapshotting binds compatibility checks and later exec to identical bytes; it
cannot establish the provenance or safety of arbitrary operator-selected code.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import stat
import struct

from orchestration.common import Blocked
from .session_sources import opened

EXECUTABLE_ALIAS='/run/devflower-cli/codex'
LIMIT=512*1024*1024
SEALS=fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL


def binary_digest(fd):
    before=os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=LIMIT:
        raise Blocked('原生CLI文件类型或大小不受支持')
    value=hashlib.sha256();offset=0
    while offset<before.st_size:
        chunk=os.pread(fd,min(1048576,before.st_size-offset),offset)
        if not chunk:raise Blocked('原生CLI在核验时变化')
        value.update(chunk);offset+=len(chunk)
    after=os.fstat(fd)
    stamp=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if stamp(before)!=stamp(after):raise Blocked('原生CLI在核验时变化')
    return value.hexdigest()


def static_elf(fd):
    """Support Linux little-endian ELF64 x86-64 without an ELF interpreter.

    Validate the complete program-header table and reject dynamic DT_NEEDED
    dependencies too (a static PIE can contain PT_DYNAMIC without DT_NEEDED).
    No script, version command or helper is executed to identify this format.
    """
    if platform.system()!='Linux' or platform.machine()!='x86_64':
        raise Blocked('此平台尚未验证不可变原生CLI执行')
    size=os.fstat(fd).st_size;raw=os.pread(fd,64,0)
    if len(raw)!=64 or raw[:7]!=b'\x7fELF\x02\x01\x01':
        raise Blocked('原生CLI必须直接指定静态原生二进制；JS、shell及其他启动器不受支持')
    fields=struct.unpack('<16sHHIQQQIHHHHHH',raw)
    _,kind,machine,version,entry,phoff,_,_,ehsize,phsize,phnum,*_=fields
    if kind not in (2,3) or machine!=62 or version!=1 or ehsize!=64 or phsize!=56 or not 0<phnum<=256 or phoff<64 or phoff+phnum*56>size:
        raise Blocked('原生CLI的ELF结构不受支持')
    executable=False
    for i in range(phnum):
        typ,flags,offset,vaddr,_,length,memsize,_=struct.unpack('<IIQQQQQQ',os.pread(fd,56,phoff+i*56))
        if offset+length>size or typ==1 and length>memsize or typ==3:
            raise Blocked('动态加载器或无效ELF区段不受支持')
        if typ==1 and flags&1 and vaddr<=entry<vaddr+memsize:executable=True
        if typ==2:
            if length>1048576 or length%16:raise Blocked('原生CLI动态区段不受支持')
            terminated=False
            for j in range(0,length,16):
                tag,_=struct.unpack('<qQ',os.pread(fd,16,offset+j))
                if tag==1:raise Blocked('原生CLI外部动态依赖尚未固定；未启动程序')
                if tag==0:terminated=True;break
            if not terminated:raise Blocked('原生CLI动态区段不完整')
    if not executable:raise Blocked('原生CLI入口不受支持')


class NativeImage:
    def __init__(self, executable, *, expected_sha256=None):
        self.fd=None;self.closed=True
        try:
            self.path=str(Path(executable).resolve(strict=True))
            with opened(self.path) as source:
                before=os.fstat(source)
                if not before.st_mode&0o111:raise Blocked('原生CLI不可执行')
                static_elf(source)
                digest=binary_digest(source)
                if expected_sha256 is not None and digest!=expected_sha256:
                    raise Blocked('原生CLI在兼容性预检后变化；未启动程序')
                fd=os.memfd_create('devflower-codex',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
                self.fd=fd;offset=0
                while offset<before.st_size:
                    chunk=os.pread(source,min(1048576,before.st_size-offset),offset)
                    if not chunk:raise Blocked('原生CLI在复制时变化')
                    view=memoryview(chunk)
                    while view:
                        written=os.write(fd,view)
                        if written<=0:raise Blocked('无法固定原生CLI')
                        view=view[written:]
                    offset+=len(chunk)
                os.fchmod(fd,0o500)
                fcntl.fcntl(fd,fcntl.F_ADD_SEALS,SEALS)
                after=os.fstat(source)
                stamp=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                if stamp(before)!=stamp(after) or binary_digest(fd)!=digest:
                    raise Blocked('原生CLI在复制时变化；未启动程序')
                static_elf(fd)
                self.sha256=digest;self.closed=False;self.validate()
        except BaseException as exc:
            self.close()
            if isinstance(exc,(Blocked,KeyboardInterrupt,SystemExit)):raise
            raise Blocked('无法建立不可变原生CLI；未启动程序') from None

    def validate(self):
        if self.closed or self.fd is None or fcntl.fcntl(self.fd,fcntl.F_GET_SEALS)!=SEALS:
            raise Blocked('不可变原生CLI已经失效；未启动程序')

    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
        self.closed=True

    def __enter__(self):self.validate();return self
    def __exit__(self,*args):self.close()
