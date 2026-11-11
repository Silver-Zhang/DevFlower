"""Explicit V2 terminal lifecycle over the common native writer and attach controls."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import secrets
import selectors
import sqlite3
import stat
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import termios
from threading import Event, Thread, current_thread
import time
from types import SimpleNamespace

from orchestration.common import Blocked, canonical, clean_env, digest
from orchestration.privacy import credentials
from orchestration.process import stop
from orchestration.sandbox import runtime_paths
from orchestration.store import process_identity
from studio.graph import Invalid
from studio.native_codex_exec import FEATURES, provider_overrides, untrusted_project_args
from studio.native_codex_image import NativeImage, binary_digest
from studio.native_codex_rpc import CodexRPC, metadata_guard
from studio.native_terminals import NativeTerminals, dimensions
from studio.server import Conflict
from studio.session_sources import opened
from studio.terminal_text import TerminalText

from .codex_pty_gateway import CodexProtocol, PROFILE, UnixGateway
from .native_session_links import workflow_use
from .run_inspector import RUN
from .project_files import mount_id, protected_roots
from .session_identity import SessionLocator, SessionRefused, StorageRootIdentity
from .session_lease import CODEX_COORDINATION_VERSION, production_session_gate
from .session_operations import operation_id

KEY = re.compile(r'[0-9a-f]{64}')
BROWSER = re.compile(r'browser:[0-9a-f]{32}')
RUNTIME = ('/usr/bin/bwrap', '/usr/bin/setsid')
FRONTEND_EXE = '/run/devflower-cli/codex'
FRONTEND_HOME = '/tmp/df-ui'
PUBLIC = {'id', 'workspace', 'handle', 'provider', 'session_id', 'state', 'reason', 'output', 'next',
          'reset', 'process_alive', 'input_owner', 'input_generation', 'input_sequence', 'input_status'}
SHELL_DENIED = '此终端已拒绝直接 Shell（!）命令。'
BACKEND_SUPERVISOR = '''
import ctypes, os, signal, sys
parent, fd = map(int, sys.argv[1:3])
if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent:
    os._exit(125)
os.execve(fd, sys.argv[3:], os.environ)
'''


def guarded_workspace(store, binding, directory):
    root = Path(binding['cwd'])
    if (Path('/tmp').is_relative_to(root)
            or any(root.is_relative_to(path) or path.is_relative_to(root) for path in protected_roots(store))
            or any(root.is_relative_to(path) or path.is_relative_to(root) for path in map(Path, runtime_paths()))
            or StorageRootIdentity.from_fd(directory).fields() != binding['cwd_identity']):
        raise SessionRefused('LOCATOR_REFUSED')
    if os.path.lexists(root / '.devflower'):
        raise Blocked('此工作空间含保留记录目录，原生终端无法开放其完整读写。')
    expected = mount_id(directory)
    remaining = 24000

    def walk(parent, depth):
        nonlocal remaining
        if depth > 64:
            raise Blocked('终端工作空间层级超过核验范围。')
        scan = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            with os.scandir(scan) as entries:
                for entry in entries:
                    remaining -= 1
                    if remaining < 0:
                        raise Blocked('终端工作空间超过挂载安全核验范围。')
                    fd = os.open(entry.name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                    try:
                        meta = os.fstat(fd)
                        if mount_id(fd) != expected:
                            raise Blocked('终端工作空间包含独立挂载。')
                        if stat.S_ISDIR(meta.st_mode):
                            walk(fd, depth + 1)
                        elif not (stat.S_ISLNK(meta.st_mode) or stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1):
                            raise Blocked('终端工作空间包含硬链接或特殊文件。')
                    finally:
                        os.close(fd)
        finally:
            os.close(scan)

    walk(directory, 0)


def frontend_args(binding, directory, executable_reader, gateway_dir, socket_name):
    args = [RUNTIME[1], '--ctty', RUNTIME[0], '--unshare-user', '--unshare-pid',
            '--unshare-net', '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--die-with-parent',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    for path in runtime_paths():
        if not path.startswith(('/proc/', '/dev/')) and Path(path).exists():
            args += ['--ro-bind', path, path]
    cwd = binding['cwd']
    return args + ['--dir', FRONTEND_HOME, '--dir', FRONTEND_HOME + '/.codex',
                   '--dir', '/run/devflower-cli', '--perms', '0500', '--file', str(executable_reader), FRONTEND_EXE,
                   '--bind-fd', str(directory), cwd, '--ro-bind', str(gateway_dir), '/run/df-pty',
                   '--remount-ro', '/', '--chdir', cwd, '--', FRONTEND_EXE, 'resume', binding['session_id'],
                   '--remote', 'unix:///run/df-pty/' + socket_name, '--no-alt-screen',
                   '-c', 'check_for_update_on_startup=false',
                   '-c', 'projects.' + json.dumps(cwd) + '.trust_level="untrusted"']


def backend_args(binding, provider_args, executable, profile, *, read_only=False, code_host=False):
    cwd = binding['cwd']
    fields = {':root': 'deny', ':minimal': 'read', cwd: 'read' if read_only else 'write', executable: 'read'}
    filesystem = '{' + ','.join(json.dumps(key) + '=' + json.dumps(value) for key, value in fields.items()) + '}'
    flags = dict(FEATURES, worktrees=False, multi_agent=False, code_mode_host=code_host and not read_only, shell_tool=not read_only,
                 default_mode_request_user_input=False)
    if read_only:
        flags['unified_exec'] = False
    trust_args = (untrusted_project_args(cwd) if binding.get('v2_development_untrusted') is True
                  else ['-c', 'projects.' + json.dumps(cwd) + '.trust_level="untrusted"'])
    args = [executable, *provider_args,
            '-c', f'permissions.{profile}.filesystem={filesystem}',
            '-c', f'permissions.{profile}.network.enabled=false', '-c', f'default_permissions="{profile}"',
            *trust_args, '-c', 'approval_policy="never"', '-c', 'web_search="disabled"', '-c', 'notify=[]',
            '-c', 'check_for_update_on_startup=false', '-c', 'skills.include_instructions=false',
            '-c', 'skills.bundled.enabled=false', '-c', 'project_doc_max_bytes=0',
            '-c', 'shell_environment_policy={inherit="none",include_only=["PATH","LANG"],'
                  'set={PATH="/usr/bin:/bin",LANG="C.UTF-8"}}']
    for key, value in flags.items():
        args += ['-c', 'features.' + key + '=' + json.dumps(value)]
    return args + ['app-server', '--listen', 'stdio://']


def safe_backend_config(store, binding, image, *, disable_extensions=False):
    if os.path.lexists(Path(binding['config_root']) / '.env'):
        raise Blocked('终端尚不支持原生 Codex .env 配置。')
    if Path('/etc/codex').exists():
        raise Blocked('此主机的系统 Codex 配置尚未核验终端权限。')
    with metadata_guard(store.state, timeout=20) as remaining:
        options = {'untrusted_project': binding['cwd']} if binding.get('v2_development_untrusted') is True else {}
        with CodexRPC(binding['executable'], binding['home'], binding['config_root'], str(store.state),
                      timeout=remaining, executable_image=image, **options) as rpc:
            config = rpc.request('config/read', {'cwd': str(store.state), 'includeLayers': False})['config']
            workspace_config = (rpc.request('config/read', {'cwd': binding['cwd'], 'includeLayers': False})['config']
                                if disable_extensions else None)
    policy = (config.get('shell_environment_policy') or {}) if isinstance(config, dict) else None
    if (not isinstance(config, dict) or config.get('agents')
            or not isinstance(policy, dict) or policy.get('filters')
            or config.get('sqlite_home') not in (None, binding['config_root'])
            or not disable_extensions and any(config.get(key) for key in ('mcp_servers', 'hooks', 'plugins'))):
        raise Blocked('当前 Codex 配置含未核验的扩展或原生存储覆盖，原生启动已停止。')
    args = provider_overrides(config)
    if disable_extensions:
        if not isinstance(workspace_config, dict):
            raise Blocked('无法核验当前工作区的 Codex 扩展配置。')
        for name in sorted(configured_mcp(config).keys() | configured_mcp(workspace_config).keys()):
            args += ['-c', 'mcp_servers.' + name + '.enabled=false']
    return args


def configured_mcp(config):
    servers = config.get('mcp_servers')
    if servers is None:
        return {}
    if (not isinstance(servers, dict) or len(servers) > 128
            or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name) or not isinstance(value, dict)
                   for name, value in servers.items())):
        raise Blocked('Codex MCP 配置无法逐项禁用；未启动会话。')
    return servers


def pin_backend_image(root, image, stack):
    """Private named copy from sealed bytes for Codex's native arg0 helper."""
    folder = root / 'bin'
    folder.mkdir(mode=0o700)
    return pin_image_file(folder / 'codex', image.fd, image.sha256, stack)


def pin_image_file(target, source, sha256, stack):
    folder = target.parent
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
    try:
        offset = 0
        length = os.fstat(source).st_size
        while offset < length:
            wrote = os.sendfile(fd, source, offset, min(1048576, length - offset))
            if wrote <= 0:
                raise Blocked('无法固定 Codex 终端帮助程序。')
            offset += wrote
        os.fchmod(fd, 0o500)
    finally:
        os.close(fd)
    fd = stack.enter_context(opened(target))
    original = os.fstat(fd)
    expected_root = StorageRootIdentity.capture(folder)
    def verify():
        with opened(target) as current:
            meta = os.fstat(current)
            if ((meta.st_dev, meta.st_ino, meta.st_nlink, meta.st_uid, stat.S_IMODE(meta.st_mode)) !=
                    (original.st_dev, original.st_ino, 1, os.geteuid(), 0o500)
                    or StorageRootIdentity.capture(folder) != expected_root
                    or stat.S_IMODE(folder.stat().st_mode) != 0o700 or binary_digest(current) != sha256):
                raise Blocked('Codex 终端帮助程序映像已变化。')
    verify()
    return str(target), fd, verify


def wait_owned(process, executable_fd=None):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and process.poll() is None:
        identity = process_identity(process.pid)
        if identity and identity['pid'] == identity['pgid'] == identity['sid']:
            if executable_fd is None or os.path.samestat(os.stat(f'/proc/{process.pid}/exe'), os.fstat(executable_fd)):
                return identity
        time.sleep(.01)
    raise Blocked('原生终端进程归属未确认。')


def reap(process, identity):
    if process is None:
        return
    if identity:
        stop(identity)
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=4)


class CodexTerminals(NativeTerminals):
    record_kind = 'native-session-pty'

    def __init__(self, operations):
        self.operations = operations
        self.store = operations.store
        self.pending = {}
        super().__init__(SimpleNamespace(storage=self.store.state, flow=self.store.db), None)

    @classmethod
    def available(cls, operations):
        return bool(operations and getattr(operations, 'codex_terminal', False)
                    and operations.codex.version == CODEX_COORDINATION_VERSION
                    and all(Path(path).is_file() and os.access(path, os.X_OK) for path in RUNTIME))

    @staticmethod
    def _owner_label(owner):
        return 'workbench' if owner.startswith('browser:') else 'local-terminal'

    def _snapshot_row(self, row, entry, after=0):
        if entry is None:
            state = ('STARTING' if row['id'] in self.pending else
                     row['state'] if row['state'] in ('COMPLETED', 'FAILED', 'CANCELLED') else 'RECOVERY_REQUIRED')
            value = dict(row, state=state, output='', next=after, reset=False, process_alive=False)
        else:
            value = super()._snapshot_row(row, entry, after)
        return {key: value[key] for key in PUBLIC if key in value}

    def listing(self, workspace):
        self.store.workspace(workspace)
        rows = [row for row in self.store.db.list(self.record_kind) if row['workspace'] == workspace][:30]
        return {'terminals': [{key: value for key, value in self.snapshot(workspace, row['id']).items()
                               if key != 'output'} for row in rows]}

    def run_target(self, workspace, run_id, activation_id):
        self.store.workspace(workspace)
        if not RUN.fullmatch(run_id) or not KEY.fullmatch(activation_id):
            raise Invalid('请选择一次运行的明确激活。')
        try:
            activation = self.store.db.get('activation',activation_id)
            chat = self.store.db.get('node-session',activation['session_binding_id'])
            if activation['run']!=run_id or chat['workspace']!=workspace:
                raise ValueError()
            binding = chat['binding']
            sid = chat['native_session_id']
            if binding['provider']!='codex':
                return {'available':False,'reason':'此 Provider 尚未开放原生终端。'}
            with self.store.db.tx(read=True) as db:
                run,_,_,actual,_,frozen,cwd = workflow_use(self.store,db,chat,binding['config_root'],sid,'codex')
            if (run['id'],run['workspace'],actual['id'])!=(run_id,workspace,activation_id):
                raise ValueError()
        except (Invalid,OSError,ValueError,Blocked,KeyError,TypeError,StopIteration):
            raise SessionRefused('LOCATOR_REFUSED') from None
        with self.store.db.tx(read=True) as db:
            exact = db.execute("""
                SELECT id FROM records WHERE kind=? AND json_extract(data,'$.workspace')=?
                    AND json_extract(data,'$.session_id')=?
                    AND json_extract(data,'$.binding.cwd')=?
                    AND json_extract(data,'$.binding.config_root')=?
                    AND json_extract(data,'$.state') NOT IN ('COMPLETED','FAILED','CANCELLED')
                LIMIT 2
            """,(self.record_kind,workspace,sid,cwd,frozen['config_root'])).fetchall()
        if len(exact)>1:
            raise SessionRefused('RECOVERY_REQUIRED')
        if exact:
            terminal = self.snapshot(workspace,exact[0][0])
            return {'available':True,'id':terminal['id'],'handle':terminal['handle'],'run':run_id,
                    'activation':activation_id,'start_available':False}
        if frozen.get('native_profile'):
            return {'available':False,'reason':'此激活使用命名 CLI 配置，当前终端仅使用默认配置。'}
        if cwd!=self.store.workspace(workspace)['path']:
            return {'available':False,'reason':'此激活的原生工作目录不是当前工作区根，无法启动工作区终端。'}
        listing = self.operations.reader.listing(workspace)
        approved = self.operations.profiles['codex'][0]
        expected = SessionLocator('codex',approved.profile.id,approved.profile.identity,cwd,workspace,sid)
        handle = approved.registry.handle(expected)
        locator,record = self.operations.resolve(workspace,handle,listing['revision'])
        if locator!=expected or record.source.profile!=approved.profile:
            raise SessionRefused('LOCATOR_REFUSED')
        return {'available':True,'handle':handle,'revision':listing['revision'],'run':run_id,
                'activation':activation_id,'start_available':True}

    def start(self, data):
        if set(data) != {'workspace', 'handle', 'revision', 'intent_key', 'model', 'effort', 'cols', 'rows'}:
            raise Invalid('原生终端启动需要明确的会话、模型与工作区读写授权。')
        workspace = data['workspace']
        self.store.workspace(workspace)
        key = operation_id(workspace, data['intent_key'])
        fingerprint = digest(canonical(data))
        cols, rows = dimensions(data['cols'], data['rows'])
        with self.lock:
            try:
                prior = self.store.db.get(self.record_kind, key)
            except Invalid:
                prior = None
            if prior:
                if prior['request_sha256'] != fingerprint:
                    raise Conflict('原生终端意图与已登记授权不同。')
                return self.snapshot(workspace, key)
            if len(self.pending) >= 4:
                raise Conflict('本机终端数量已达上限。')
            binding, locator, record = self.operations.prepare_binding(workspace, 'codex', data['handle'],
                data['revision'], data['model'], data['effort'])
            if locator is None:
                raise Invalid('只能为已有的已核验原生会话打开终端。')
            row = dict(id=key, workspace=workspace, handle=data['handle'], provider='codex',
                       session_id=locator.native_uuid, target=locator.native_uuid, binding=binding,
                       native_sha256=record.ref.sha, request_sha256=fingerprint, owner=self.operations.owner,
                       authorization='explicit-workspace-write-pty', state='RESERVED', input_owner='unclaimed',
                       input_generation=0, input_sequence=0, pending_effect=False)
            self.store.db.put(self.record_kind, key, row)
            worker = Thread(target=self.run_terminal, args=(row, locator, record, cols, rows),
                            daemon=True, name='v2-native-terminal')
            self.pending[key] = worker
            try:
                worker.start()
            except RuntimeError:
                self.pending.pop(key, None)
                self.store.db.change(self.record_kind, key, {'state': 'FAILED'})
            return self.snapshot(workspace, key)

    def run_terminal(self, row, locator, record, cols, rows):
        key, binding = row['id'], row['binding']
        frontend = backend = frontend_id = backend_id = entry = gateway_thread = None
        failure = None
        def check_cancel():
            if self.store.db.get(self.record_kind, key).get('cancel_requested'):
                raise Blocked('原生终端启动已取消。')

        with ExitStack() as held:
            try:
                held.enter_context(production_session_gate(binding, locator))
                self.operations.check_resume(row, locator, record)
                self.operations.require_idle(binding, ignore=key, owner_kind=self.record_kind)
                check_cancel()
                image = held.enter_context(NativeImage(binding['executable'], expected_sha256=binding['_executable_sha256']))
                approved_config = safe_backend_config(self.store, binding, image)
                workspace = held.enter_context(opened(binding['cwd'], directory=True))
                guarded_workspace(self.store, binding, workspace)
                root = Path(held.enter_context(TemporaryDirectory(prefix='df-v2-pty-', dir='/tmp')))
                sockets = root / 'socket'
                sockets.mkdir(mode=0o700)
                gateway = UnixGateway(sockets / (secrets.token_hex(16) + '.sock'))
                held.callback(gateway.close)
                scratch = root / 'tmp'
                scratch.mkdir(mode=0o700)
                executable, backend_fd, verify_backend = pin_backend_image(root, image, held)
                profile = PROFILE + '_' + secrets.token_hex(16)
                backend_env = dict(clean_env(binding['home']), CODEX_HOME=binding['config_root'], TMPDIR=str(scratch))
                argv = [sys.executable, '-I', '-B', '-c', BACKEND_SUPERVISOR, str(os.getpid()), str(backend_fd),
                        *backend_args(binding, approved_config, executable, profile)]
                master, slave = pty.openpty()
                held.callback(os.close, master)
                reader = os.open('/proc/self/fd/' + str(image.fd), os.O_RDONLY | os.O_CLOEXEC)
                held.callback(os.close, reader)
                try:
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
                    with self.lock:
                        check_cancel()
                        frontend = subprocess.Popen(frontend_args(binding, workspace, reader, sockets, gateway.path.name),
                            cwd=binding['cwd'], env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TERM': 'xterm-256color',
                            'HOME': FRONTEND_HOME, 'CODEX_HOME': FRONTEND_HOME + '/.codex'}, stdin=slave, stdout=slave,
                            stderr=slave, close_fds=True, pass_fds=(workspace, reader))
                finally:
                    os.close(slave)
                frontend_id = wait_owned(frontend)
                os.set_blocking(master, False)
                secrets_found = set(self.operations.reader.literals) | credentials(binding['home'])
                entry = dict(proc=frontend, process=frontend_id, master=master, first=0, next=0, text='',
                    filter=TerminalText(secrets_found), inputs={}, owner=None, generation=0,
                    state='STARTING', thread=current_thread(), cancelled=False, pending_effect=False)
                with self.lock:
                    check_cancel()
                    self.store.db.change(self.record_kind, key, {'state': 'STARTING', 'process': frontend_id})
                    self.entries[key] = entry
                gateway.open(frontend_id, lambda: entry['cancelled'] or frontend.poll() is not None)
                with self.lock:
                    check_cancel()
                    backend = subprocess.Popen(argv, cwd=binding['cwd'], env=backend_env, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                        close_fds=True, pass_fds=(backend_fd,))
                backend_id = wait_owned(backend, backend_fd)
                self.store.db.change(self.record_kind, key, {'backend_process': backend_id})
                closed = Event()
                errors = []
                def check_effect_scope():
                    check_cancel()
                    self.operations.require_scope(binding)
                    self.operations.codex.require_local(binding)
                    verify_backend()
                    guarded_workspace(self.store, binding, workspace)

                def on_ready():
                    with self.lock:
                        check_cancel()
                        self.store.db.change(self.record_kind, key, {'state': 'RUNNING'})
                        entry['state'] = 'RUNNING'

                def on_effect(pending):
                    with self.lock:
                        if pending:
                            check_effect_scope()
                        self.store.db.change(self.record_kind, key, {'pending_effect': pending})
                        entry['pending_effect'] = pending

                def shell_denied():
                    with self.lock:
                        self.store.db.change(self.record_kind, key, {'reason': SHELL_DENIED})
                        entry['shell_denied'] = True

                def bridge():
                    try:
                        protocol = CodexProtocol(binding, locator.native_uuid, secrets_found, on_effect,
                                                 profile=profile, on_resume=check_effect_scope, on_ready=on_ready,
                                                 on_shell_denied=shell_denied)
                        gateway.serve(backend, protocol, closed.is_set)
                    except (OSError, EOFError, ValueError, TypeError, KeyError, Blocked, sqlite3.Error) as error:
                        errors.append(type(error).__name__)
                    finally:
                        closed.set()

                gateway_thread = Thread(target=bridge, daemon=True, name='v2-codex-unix-gateway')
                gateway_thread.start()
                with selectors.DefaultSelector() as reader_selector:
                    reader_selector.register(master, selectors.EVENT_READ)
                    total, started = 0, time.monotonic()
                    while not closed.is_set() and not entry['cancelled'] and frontend.poll() is None and backend.poll() is None:
                        if time.monotonic() - started >= (20 if entry['state'] == 'STARTING' else 1800) or total >= 16 * 1024 * 1024:
                            raise Blocked('原生终端达到时间或输出上限。')
                        if reader_selector.select(.15):
                            raw = os.read(master, 65536)
                            total += len(raw)
                            with self.lock:
                                self._append(entry, raw)
                    if errors and not entry['cancelled']:
                        failure = '原生终端协议或隔离未能维持，未自动重发。'
                    if entry.get('shell_denied') and not entry['cancelled']:
                        failure = SHELL_DENIED
            except (OSError, ValueError, TypeError, KeyError, Blocked, Conflict, sqlite3.Error, subprocess.SubprocessError) as error:
                failure = str(error) if isinstance(error, SessionRefused) else '原生终端未能安全启动或维持。'
            finally:
                if gateway_thread is not None:
                    closed.set()
                    gateway.close()
                for process, identity in ((frontend, frontend_id), (backend, backend_id)):
                    try:
                        reap(process, identity)
                    except (OSError, subprocess.SubprocessError):
                        failure = '原生终端进程退出未确认。'
                        if entry:
                            entry['pending_effect'] = True
                if gateway_thread is not None:
                    gateway_thread.join(timeout=4)
                    if gateway_thread.is_alive() and entry:
                        entry['pending_effect'] = True
                if backend is not None:
                    for stream in (backend.stdin, backend.stdout):
                        stream.close()
                with self.lock:
                    try:
                        current = self.store.db.get(self.record_kind, key)
                        unknown = current.get('pending_effect') or current.get('input_status') in ('SENDING', 'UNKNOWN')
                        if entry:
                            unknown |= entry['pending_effect'] or entry['state'] == 'RECOVERY_REQUIRED'
                        state = ('RECOVERY_REQUIRED' if unknown else 'CANCELLED' if current.get('cancel_requested')
                                 else 'FAILED' if failure or entry is None or entry['state'] == 'STARTING' else 'COMPLETED')
                        if state == 'FAILED' and not failure:
                            failure = '原生终端退出前未完成会话与权限核验。'
                        if entry:
                            entry['state'] = state
                            self._append(entry, b'', True)
                            if entry.get('attach'):
                                entry['attach'].close()
                        self.store.db.change(self.record_kind, key, {'state': state,
                            'reason': '原生终端有未确认的操作。' if unknown else failure})
                    finally:
                        self.pending.pop(key, None)
                        for previous in list(self.entries):
                            if len(self.entries) <= 16:
                                break
                            if previous not in self.pending:
                                self.entries.pop(previous)

    def close(self, workspace, key):
        with self.lock:
            self.snapshot(workspace, key)
            worker = self.pending.get(key)
            if not worker:
                return {'stopped': False}
            self.store.db.change(self.record_kind, key, {'cancel_requested': True})
            entry = self.entries.get(key)
            if entry:
                entry['cancelled'] = True
        worker.join(timeout=5)
        final = self.snapshot(workspace, key)
        return {'stopped': not worker.is_alive(), 'state': final['state']}

    def shutdown(self):
        with self.lock:
            pending = [(self.store.db.get(self.record_kind, key)['workspace'], key) for key in self.pending]
        for workspace, key in pending:
            self.close(workspace, key)

    def action(self, route, data):
        if route == 'start':
            return self.start(data)
        base = {'workspace', 'id'}
        fields = {'claim': {'owner', 'generation'}, 'release': {'owner', 'generation'},
                  'input': {'owner', 'generation', 'sequence', 'text'},
                  'resize': {'owner', 'generation', 'cols', 'rows'}, 'share': set(), 'stop': set()}
        if route not in fields or set(data) != base | fields[route] or not isinstance(data['id'], str) or not KEY.fullmatch(data['id']):
            raise Invalid('终端操作需要当前工作区和明确身份。')
        if 'owner' in data and (not isinstance(data['owner'], str) or not BROWSER.fullmatch(data['owner'])):
            raise Invalid('浏览器输入身份无效。')
        method = self.close if route == 'stop' else getattr(self, route)
        return method(data['workspace'], data['id'], **{key: data[key] for key in fields[route]})
