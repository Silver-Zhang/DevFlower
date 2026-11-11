"""Scoped Claude Code using native tools, Skills and one native history inode."""
from contextlib import ExitStack, contextmanager
import ctypes
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time
import uuid
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from orchestration import sandbox
from orchestration.common import Blocked, canonical, clean_env, digest
from orchestration.privacy import credentials
from orchestration.store import process_identity
from studio.agent_extensions import skill_files, skill_header
from studio.native_codex_image import SEALS, binary_digest, sealed_executable
from studio.session_sources import opened

from .claude_session_profile import startup_environment
from .claude_scope_exec import HOME, ROOT, TEMP
from .codex_terminals import guarded_workspace
from .native_history_config import unique_pairs
from .project_files import mount_id
from .session_identity import StorageRootIdentity

BWRAP = '/usr/bin/bwrap'
CLAUDE = ROOT + '/claude'
SHELL = ROOT + '/bash'
SETTINGS = ROOT + '/settings.json'
PYTHON = '/usr/bin/python3'
EXEC = ROOT + '/scope.py'
RUNTIME = ('/usr/bin', '/usr/lib', '/usr/lib64', '/etc/resolv.conf', '/etc/hosts',
           '/etc/nsswitch.conf', '/etc/ssl/certs/ca-certificates.crt', '/etc/ld.so.cache')


def scoped_environment(binding, inherited):
    config = Path(binding['config_root']) / 'settings.json'
    try:
        with opened(config) as fd:
            raw = os.read(fd, 200001)
        if len(raw) > 200000:
            raise ValueError
        values = json.loads(raw, object_pairs_hook=unique_pairs)
        native = values.get('env') or {}
        if not isinstance(native, dict):
            raise ValueError
        for field, key in (('model', 'ANTHROPIC_MODEL'), ('effortLevel', 'CLAUDE_CODE_EFFORT_LEVEL')):
            if isinstance(values.get(field), str):
                native.setdefault(key, values[field])
        values = {**native, **inherited}
        if any(not isinstance(value, str) or '\0' in value for value in startup_environment(values).values()):
            raise ValueError
        env = startup_environment(values)
    except FileNotFoundError:
        env = inherited
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        raise Blocked('无法核验 Claude 工作区模式的原生认证配置。') from None
    if not any(env.get(key) for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN')):
        raise Blocked('Claude 工作区工具需要通过原生环境或 settings.json 配置 API 认证。')
    if env.get('NODE_EXTRA_CA_CERTS'):
        raise Blocked('Claude 工作区隔离尚未验证自定义证书文件。')
    return env


def scope_supported():
    if sandbox.abi() < 6 or not os.access(BWRAP, os.X_OK) or not os.access(PYTHON, os.X_OK):
        return False
    try:
        probe = subprocess.run([BWRAP, '--unshare-user', '--disable-userns', '--unshare-pid', '--as-pid-1',
                                '--ro-bind', '/', '/', '--', '/usr/bin/true'], env=clean_env(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=3)
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def snapshot(stack, label, data):
    fd = os.memfd_create(label, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    stack.callback(os.close, fd)
    with os.fdopen(os.dup(fd), 'wb') as stream:
        stream.write(data)
    os.lseek(fd, 0, os.SEEK_SET)
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)
    return fd


def project_skills(binding, cwd, scratch):
    selected = binding.get('v2_agent_skills', [])
    if not isinstance(selected, list) or len(selected) > 24:
        raise Blocked('Claude Skill 选择无效。')
    roots = {Path(cwd) / '.claude/skills': 'df-workspace',
             Path(binding['config_root']) / 'skills': 'df-user'}
    known = [item.encode() for item in credentials(binding['home']) if item]
    names, plugins = set(), {}
    for choice in selected:
        path = Path(choice['path'])
        plugin = roots.get(path.parent.parent)
        if (plugin is None or path.name != 'SKILL.md'
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', path.parent.name)):
            raise Blocked('Claude Skill 不在已授权的工作区或用户目录。')
        files = skill_files(path.parent)
        metadata = skill_header(files['SKILL.md'].decode())
        fingerprint = digest(canonical({name: digest(raw) for name, raw in files.items()}))
        if (metadata['name'] != choice['name'] or fingerprint != choice['sha256']
                or any(secret in raw for secret in known for raw in files.values())):
            raise Blocked('Claude Skill 已变化或含受保护内容。')
        if plugin not in plugins:
            folder = scratch / plugin
            manifest = folder / '.claude-plugin'
            manifest.mkdir(parents=True, mode=0o700)
            (manifest / 'plugin.json').write_text(json.dumps({'name': plugin, 'version': '1.0.0'}))
            plugins[plugin] = folder
        name = plugin + ':' + path.parent.name
        if name in names:
            raise Blocked('Claude Skill 选择重复。')
        names.add(name)
        for filename, raw in files.items():
            dest = plugins[plugin] / 'skills' / path.parent.name / filename
            dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            dest.write_bytes(raw)
    return names, plugins


def make_directory(parent, name):
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)


def native_history(stack, binding, cwd, sid, resume):
    slug = re.sub('[^a-zA-Z0-9]', '-', cwd)
    root = Path(binding['config_root'])
    runtime_projects = HOME + '/runtime/projects/'
    destination = runtime_projects + slug
    filename = sid + '.jsonl'
    config = stack.enter_context(opened(root, directory=True))
    if StorageRootIdentity.from_fd(config).fields() != binding['root_identity']:
        raise Blocked('Claude 原生配置目录身份已变化。')
    projects = make_directory(config, 'projects')
    stack.callback(os.close, projects)
    project = make_directory(projects, slug)
    stack.callback(os.close, project)
    canonical = root / 'projects' / slug

    def verify_project():
        with opened(canonical, directory=True) as current:
            if StorageRootIdentity.from_fd(current) != StorageRootIdentity.from_fd(project):
                raise Blocked('Claude 原生历史目录身份已变化。')

    if resume:
        expected = str(Path(slug) / filename)
        if binding.get('resume_proof', {}).get('relative') != expected:
            raise Blocked('Claude 续聊历史与工作区不匹配。')
        fd = stack.enter_context(opened(canonical / filename))

        def verify_resume():
            verify_project()
            current = os.stat(filename, dir_fd=project, follow_symlinks=False)
            pinned = os.fstat(fd)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or not os.path.samestat(current, pinned):
                raise Blocked('Claude 续聊原始记录的文件身份已变化。')

        verify_resume()
        return fd, destination + '/' + filename, verify_resume, verify_resume

    def verify_new():
        verify_project()
        try:
            os.stat(filename, dir_fd=project, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise Blocked('Claude 新建会话的原生 UUID 已占用。')

    verify_new()

    # Claude rejects --session-id if the file already exists. Give it an
    # empty private directory and publish the original file, without replacing
    # a native transcript or merging bytes. Failed publication preserves it.
    staged = make_directory(config, '.devflower-scoped-history')
    stack.callback(os.close, staged)
    if mount_id(staged) != mount_id(project):
        raise Blocked('Claude 原生历史需要同一文件系统以原子发布。')
    os.mkdir(sid, mode=0o700, dir_fd=staged)
    stage = make_directory(staged, sid)
    stack.callback(os.close, stage)

    def clean_empty():
        try:
            os.rmdir(sid, dir_fd=staged)
        except OSError:
            pass

    stack.callback(clean_empty)

    def publish():
        try:
            meta = os.stat(filename, dir_fd=stage, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size <= 0:
            raise Blocked('Claude 原生历史文件无效。')
        verify_new()
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stage)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.renameat2(stage, filename.encode(), project, filename.encode(), 1):
            raise Blocked('Claude 原生历史未能原子发布，原始文件已保留。')
        os.fsync(project)
        os.fsync(stage)

    return stage, destination, verify_new, publish


def attest_child(info, record, binding):
    end = time.monotonic() + 8
    try:
        if not select.select([info], [], [], max(0, end - time.monotonic()))[0]:
            raise ValueError
        pid = json.loads(os.read(info, 4096))['child-pid']
        if type(pid) is not int or pid <= 1 or pid == record['pid']:
            raise ValueError
        path = Path('/proc') / str(pid)
        while time.monotonic() < end:
            child = process_identity(pid)
            if not child or child['pgid'] != record['pgid'] or child['sid'] != record['sid']:
                raise ValueError
            if os.readlink(path / 'exe') == CLAUDE:
                with (path / 'exe').open('rb') as image:
                    if binary_digest(image.fileno()) != binding['_executable_sha256']:
                        raise ValueError
                fd = os.open(path / 'cwd', os.O_RDONLY | os.O_DIRECTORY)
                try:
                    if StorageRootIdentity.from_fd(fd).fields()[1:] != binding['cwd_identity'][1:]:
                        raise ValueError
                finally:
                    os.close(fd)
                return child
            time.sleep(.01)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    raise Blocked('未能核验隔离中的 Claude 进程，未发送输入。')


@dataclass(frozen=True)
class Scope:
    prefix: list
    fds: tuple
    environment: dict
    skills: frozenset
    attest: object
    publish_history: object


@contextmanager
def prepare_scope(binding, cwd, sid, resume, inherited):
    try:
        if str(uuid.UUID(sid)) != sid:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise Blocked('Claude 隔离需要明确的原生会话 UUID。') from None
    if (not scope_supported() or binding.get('v2_claude_scope') is not True
            or binding.get('provider') != 'claude' or binding.get('cwd') != cwd
            or not binding.get('_metadata_guard') or Path(cwd).is_relative_to('/run')):
        raise Blocked('Claude 工作区工具需要已核验的 Linux Bubblewrap 和 Landlock。')
    read_only = binding.get('v2_claude_read_only') is True
    if read_only and binding.get('v2_agent_skills'):
        raise Blocked('Claude 只读工作区模式不加载 Skills。')
    auth = scoped_environment(binding, inherited)
    with ExitStack() as stack:
        directory = stack.enter_context(opened(cwd, directory=True))
        guarded_workspace(SimpleNamespace(state=Path(binding['_metadata_guard'])), binding, directory)
        image, _ = stack.enter_context(sealed_executable(binding['executable'], 'devflower-claude',
                                                          expected_sha256=binding['_executable_sha256']))
        os.lseek(image, 0, os.SEEK_SET)
        scratch = Path(stack.enter_context(TemporaryDirectory(prefix='df-claude-scope-', dir='/tmp')))
        names, plugins = project_skills(binding, cwd, scratch)
        history, destination, verify_history, publish = native_history(stack, binding, cwd, sid, resume)
        info, writer = os.pipe2(os.O_CLOEXEC)
        stack.callback(os.close, info)
        stack.callback(os.close, writer)

        tools = ['Read','Glob','Grep'] if read_only else ['Read','Glob','Grep','Write','Edit','Bash']
        settings = dict(disableAllHooks=True, permissions={'allow': [*tools,
                              *[f'Skill({name})' for name in sorted(names)]]})
        wrapper = f'#!/bin/sh\nexec {PYTHON} -I {EXEC} bash "$@"\n'.encode()
        assets = {SHELL: ('0700', wrapper), SETTINGS: ('0400', canonical(settings)),
                  EXEC: ('0400', Path(__file__).with_name('claude_scope_exec.py').read_bytes()),
                  ROOT + '/sandbox.py': ('0400', Path(sandbox.__file__).read_bytes())}
        args = [BWRAP, '--unshare-user', '--disable-userns', '--unshare-pid', '--as-pid-1',
                '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--die-with-parent',
                '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', HOME,
                '--dir', TEMP, '--dir', ROOT,
                '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
                '--symlink', 'usr/lib64', '/lib64']
        for runtime in RUNTIME:
            if Path(runtime).exists():
                args += ['--ro-bind', runtime, runtime]
        fds = [image, directory, history, writer]
        for target, (perms, data) in assets.items():
            fd = snapshot(stack, Path(target).name, data)
            fds.append(fd)
            args += ['--perms', perms, '--file', str(fd), target]
        plugins_args = []
        for plugin, folder in plugins.items():
            target = ROOT + '/' + plugin
            args += ['--ro-bind', str(folder), target]
            plugins_args += ['--plugin-dir', target]
        args += ['--perms', '0500', '--file', str(image), CLAUDE,
                 '--ro-bind-fd' if read_only else '--bind-fd', str(directory), cwd, '--bind-fd', str(history), destination,
                 '--info-fd', str(writer), '--remount-ro', '/', '--chdir', cwd,
                 '--', PYTHON, '-I', EXEC, 'claude-read' if read_only else 'claude', destination, CLAUDE,
                 '--restricted', '--permission-prompts', 'none', '--strict-mcp-config',
                 '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '', '--no-chrome',
                 '--settings', SETTINGS, *plugins_args]
        env = dict(auth, HOME=HOME, CLAUDE_CONFIG_DIR=HOME + '/runtime', PWD=cwd, SHELL=SHELL,
                   CLAUDE_CODE_SHELL=SHELL, CLAUDE_CODE_MAX_RETRIES='0', DISABLE_AUTOUPDATER='1',
                   DISABLE_UPDATES='1', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        # execute() generates a host-only TMPDIR. Set the native private one
        # in bwrap without putting any configured authentication in argv.
        args[1:1] = ['--setenv', 'TMPDIR', TEMP]
        def attest(record):
            child = attest_child(info, record, binding)
            verify_history()
            return child

        yield Scope(args, tuple(fds), env, frozenset(names), attest, publish)
