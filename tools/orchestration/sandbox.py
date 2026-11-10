"""Linux Landlock ABI >= 6. Fail closed; never claim chmod is isolation.

Filesystem rules deny reads outside runtime/source allowlists and writes outside
explicit scratch/work directories. Signal and abstract socket scopes restrict
cross-domain access. Controller disables dumpability against same-UID ptrace.
Network egress is NOT confined; only separately approved source may be sent.
"""
import ctypes
import os
from pathlib import Path
import platform

LIBC = ctypes.CDLL(None, use_errno=True)
READ = (1 << 0) | (1 << 2) | (1 << 3)
ALL = (1 << 16) - 1  # ABI5 filesystem rights, including truncate and ioctl.


class Ruleset(ctypes.Structure):
    _fields_ = [('fs', ctypes.c_uint64), ('net', ctypes.c_uint64), ('scoped', ctypes.c_uint64)]


class Beneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]


class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]


def confine_processes(*, deny_network=False):
    """Keep descendants in their supervised session; block cross-process memory APIs.

    Apply after Popen creates the session. ABI syscall numbers are architecture
    specific; unsupported architectures are rejected by enforce().
    """
    numbers = [109, 112, 101, 310, 311, 312, 438] if platform.machine() == 'x86_64' else [154, 157, 117, 270, 271, 272, 438]
    if deny_network:
        # No addressable sockets or io_uring, which can submit socket ops.
        numbers.extend([41, 425] if platform.machine() == 'x86_64' else [198, 425])
    arch = 0xc000003e if platform.machine() == 'x86_64' else 0xc00000b7
    filters = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, arch),
               Filter(0x06, 0, 0, 0x80000000), Filter(0x20, 0, 0, 0),
               Filter(0x45, 0, 1, 0x40000000), Filter(0x06, 0, 0, 0x50000 | 1)]
    for nr in numbers:
        filters.extend([Filter(0x15, 0, 1, nr), Filter(0x06, 0, 0, 0x50000 | 1)])  # EPERM
    if deny_network:
        # Tokio's signal runtime uses an anonymous AF_UNIX socketpair. The
        # pair has no address and cannot connect to an external endpoint.
        pair = 53 if platform.machine() == 'x86_64' else 199
        filters.extend([Filter(0x15, 0, 4, pair), Filter(0x20, 0, 0, 16),
                        Filter(0x15, 0, 1, 1), Filter(0x06, 0, 0, 0x7fff0000),
                        Filter(0x06, 0, 0, 0x50000 | 1)])
    filters.append(Filter(0x06, 0, 0, 0x7fff0000))
    array = (Filter * len(filters))(*filters)
    program = Program(len(filters), array)
    if LIBC.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError(ctypes.get_errno(), 'seccomp confinement')


def abi():
    if platform.machine() not in ('x86_64', 'aarch64'):
        return -1
    return LIBC.syscall(444, 0, 0, 1)


def protect_controller():
    from .common import Blocked
    if LIBC.prctl(4, 0, 0, 0, 0):  # PR_SET_DUMPABLE
        raise Blocked('cannot protect controller from ptrace')


def enforce(read=(), write=()):
    from .common import Blocked
    if abi() < 6:
        raise Blocked('Landlock ABI >= 6 required')
    restrict(read, write)


def restrict(read=(), write=(), *, deny_network=False):
    """Apply the same Landlock policy in an already isolated native child."""
    if abi() < 6:
        raise OSError('Landlock ABI >= 6 required')
    attr = Ruleset(ALL, 0, 3)
    fd = LIBC.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'landlock create')
    try:
        for paths, access in ((read, READ), (write, ALL)):
            for value in paths:
                p = Path(value).resolve()
                if not p.exists():
                    continue
                handle = os.open(p, os.O_PATH | os.O_CLOEXEC)
                try:
                    mask = access if p.is_dir() else access & ((1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15))
                    rule = Beneath(mask, handle)
                    if LIBC.syscall(445, fd, 1, ctypes.byref(rule), 0):
                        raise OSError(ctypes.get_errno(), 'landlock add')
                finally:
                    os.close(handle)
        if LIBC.prctl(38, 1, 0, 0, 0) or LIBC.syscall(446, fd, 0):
            raise OSError(ctypes.get_errno(), 'landlock restrict')
        confine_processes(deny_network=deny_network)
    finally:
        os.close(fd)


def runtime_paths():
    return ['/usr', '/bin', '/lib', '/lib64', '/etc/ssl', '/etc/ld.so.cache',
            '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/passwd',
            '/etc/group', '/dev/null', '/dev/urandom', '/dev/random',
            '/proc/self', '/proc/meminfo', '/proc/cpuinfo', '/sys/devices/system/cpu']
