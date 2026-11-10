"""Pin and confine Codex's optional native Code Mode companion."""
import os
from pathlib import Path
import stat

from orchestration.common import Blocked
from orchestration.sandbox import abi
from studio.native_codex_image import binary_digest, seal_binary, static_elf
from studio.session_sources import opened

from .codex_terminals import pin_image_file

NAME = 'codex-code-mode-host'
PYTHON = '/usr/bin/python3'

WRAPPER = '''
import ctypes
import hashlib
import os
import signal
import sys
sys.path.insert(0, {tools!r})
from orchestration.sandbox import restrict

def main():
    parent = os.getppid()
    args = sys.argv[1:]
    if (parent <= 1 or args not in ([], ['--listen', 'grpc+stdio://'])
            or ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent):
        return
    image = os.stat(f'/proc/{{parent}}/exe')
    if (image.st_dev, image.st_ino) != {parent_image!r}:
        return
    binary = {binary!r}
    fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    os.closerange(3, fd)
    os.closerange(fd + 1, os.sysconf('SC_OPEN_MAX'))
    with os.fdopen(os.dup(fd), 'rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != {sha!r}:
            return
    os.chdir(os.path.dirname(binary))
    restrict((binary, '/dev/urandom', '/dev/random', '/proc/cpuinfo', '/proc/meminfo'),
             ('/dev/null',), deny_network=True)
    os.execve(fd, [binary, *args], {{'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}})

try:
    main()
except (OSError, RuntimeError):
    pass
os._exit(125)
'''


def companion_path(executable):
    return Path(executable).with_name(NAME)


def companion_identity(executable):
    """Record an existing exact sibling; missing packages keep native direct tools."""
    try:
        with opened(companion_path(executable)) as fd:
            if not os.fstat(fd).st_mode & 0o111:
                raise Blocked('Codex Code Mode 原生组件不可执行。')
            static_elf(fd)
            return binary_digest(fd)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise Blocked('Codex Code Mode 原生组件无法核验。') from None


def pin_code_mode_host(executable, original, expected, stack):
    if expected is None:
        return None
    if abi() < 6 or not os.access(PYTHON, os.X_OK):
        raise Blocked('Codex Code Mode 隔离不可用，未启动原生组件。')
    parent = os.stat(executable, follow_symlinks=False)
    folder = Path(executable).parent
    try:
        with opened(companion_path(original)) as source:
            static_elf(source)
            sealed, sha = seal_binary(source, 'devflower-codex-code-mode', expected_sha256=expected)
        try:
            binary, _, verify_binary = pin_image_file(folder / 'df-code-mode-native', sealed, sha, stack)
        finally:
            os.close(sealed)
        wrapper = folder / NAME
        script = (f'#!{PYTHON} -I\n' + WRAPPER.format(tools=str(Path(__file__).resolve().parents[1]),
                  parent_image=(parent.st_dev, parent.st_ino), binary=str(binary), sha=sha)).encode()
        fd = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(script)
            os.fchmod(stream.fileno(), 0o500)
            created = os.fstat(stream.fileno())
    except (OSError, ValueError):
        raise Blocked('Codex Code Mode 原生组件在启动前已变化。') from None

    def verify():
        try:
            verify_binary()
            with opened(wrapper) as fd:
                current = os.fstat(fd)
                if (not os.path.samestat(current, created) or current.st_uid != os.geteuid()
                        or current.st_nlink != 1 or stat.S_IMODE(current.st_mode) != 0o500
                        or os.read(fd, len(script) + 1) != script):
                    raise Blocked('Codex Code Mode 隔离启动器已变化。')
        except (OSError, ValueError):
            raise Blocked('Codex Code Mode 隔离启动器已变化。') from None

    verify()
    return verify
