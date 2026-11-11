#!/usr/bin/env python3
"""Install and manage the private DevFlower V2 Linux desktop service."""
import argparse
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
import webbrowser

sys.dont_write_bytecode = True
from devflower_start import directory, private, read
from orchestration.common import Blocked, canonical, digest
from orchestration.locking import lock
from orchestration.store import alive, process_identity
from studio.graph import Invalid
from studio.session_sources import clean_path, opened
from v2.native_agent_profiles import PROFILE_ID, approved_paths
from v2.native_history_config import unique_pairs
from v2.project_files import protected_roots
from v2.session_identity import StorageRootIdentity
from v2.session_lease import mount_table, physical_path

SCRIPT = Path(__file__).resolve()
SOURCE = SCRIPT.parents[1]
PYTHON = str(Path(sys.executable).resolve())
MARKER = b'# DevFlower V2 desktop installer'
OPTIONS = ('native_history', 'managed_codex', 'managed_claude', 'codex_terminal')
REGISTRATIONS = {
    'native_agent_profile': '批准原生 CLI 配置层',
    'native_agent_home': '批准独立原生 HOME',
    'git_delivery_target': '批准本机裸 Git 交付目标',
}
HOST = '127.0.0.1'


class Layout:
    def __init__(self):
        self.home = Path(clean_path(str(Path.home())))
        def xdg(name, default):
            value = os.environ.get('XDG_' + name + '_HOME') or str(self.home / default)
            return Path(clean_path(value))
        self.config = xdg('CONFIG', '.config') / 'devflower-v2'
        self.state = xdg('STATE', '.local/state') / 'devflower-v2'
        self.desktop = xdg('DATA', '.local/share') / 'applications/DevFlower-V2.desktop'
        self.command = self.home / '.local/bin/devflower-v2'
        if self.config.is_relative_to(self.state) or self.state.is_relative_to(self.config):
            raise Blocked('V2 配置与状态目录必须分开。')
        self.workbench = self.state / 'workbench'
        self.settings = self.config / 'desktop.json'
        self.receipt = self.state / 'desktop-service.json'
        reserved = [path for path in protected_roots(self) if path != self.state]
        if any(path.is_relative_to(other) or other.is_relative_to(path)
               for path in (self.config, self.state, self.command.parent, self.desktop.parent) for other in reserved):
            raise Blocked('V2 安装目录必须与产品源码和原生账户分开。')

    def service_args(self):
        return ['_serve', str(self.config), str(self.state)]


@contextmanager
def launcher_lock(layout, create):
    with directory(layout.config, create=create) as parent:
        fd = os.open('launcher.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            private(os.fstat(fd))
            lock(fd, blocking=True)
            yield
        finally:
            os.close(fd)


def ensure_asset_directory(path):
    try:
        with opened(path, directory=True) as fd:
            meta = os.fstat(fd)
    except FileNotFoundError:
        ensure_asset_directory(path.parent)
        with opened(path.parent, directory=True) as parent:
            try:
                os.mkdir(path.name, 0o755, dir_fd=parent)
            except FileExistsError:
                pass
        ensure_asset_directory(path)
        return
    if meta.st_uid == os.geteuid():
        if not meta.st_mode & 0o022:
            return
        # An existing private ancestor protects a group-writable bin or
        # applications directory without changing its permissions.
        for ancestor in path.parents:
            with opened(ancestor, directory=True) as fd:
                boundary = os.fstat(fd)
                if boundary.st_uid == os.geteuid() and not boundary.st_mode & 0o077:
                    return
    raise Blocked('桌面安装目录必须归当前用户所有，且其他用户不可写。')


def write_file(path, data, *, mode=0o600, asset=False):
    if asset:
        ensure_asset_directory(path.parent)
    with (opened(path.parent, directory=True) if asset else directory(path.parent, create=True)) as parent:
        if asset:
            try:
                with opened(path) as current:
                    meta = os.fstat(current)
                    if (meta.st_uid != os.geteuid() or meta.st_nlink != 1 or meta.st_mode & 0o022
                            or MARKER not in os.read(current, 512).splitlines()[:2]):
                        raise Blocked('现有入口不是 DevFlower V2 安装器创建的文件，未覆盖。')
            except FileNotFoundError:
                pass
        else:
            try:
                with opened(path) as current:
                    private(os.fstat(current))
            except FileNotFoundError:
                pass
        name = '.install-' + secrets.token_hex(12)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass


def read_json(path):
    try:
        return json.loads(read(path, confidential=True, limit=65536), object_pairs_hook=unique_pairs)
    except FileNotFoundError:
        return None


def validate_settings(value, layout):
    if (not isinstance(value, dict) or set(value) != {'version', 'roots', 'port', 'codex_cli', *OPTIONS, *REGISTRATIONS}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['port']) is not int or not 1024 <= value['port'] <= 65535
            or any(type(value[option]) is not bool for option in OPTIONS)
            or not isinstance(value['roots'], list) or not 1 <= len(value['roots']) <= 16
            or value['codex_cli'] is not None and clean_path(value['codex_cli']) != value['codex_cli']):
        raise Blocked('V2 桌面设置无效，请重新配置。')
    if any(not isinstance(row, dict) or set(row) != {'path', 'identity'}
           or clean_path(row['path']) != row['path'] or not isinstance(row['identity'], list)
           or len(row['identity']) != 2 or any(type(part) is not int for part in row['identity'])
           for row in value['roots']):
        raise Blocked('V2 工作区登记无效，请重新配置。')
    for key in REGISTRATIONS:
        entries = value[key]
        if (not isinstance(entries, list) or len(entries) > 16 or any(not isinstance(entry, str)
                or not 0 < len(entry) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in entry)
                for entry in entries)):
            raise Blocked('V2 原生配置或交付登记无效，请重新配置。')
    approved_paths(value['native_agent_profile'], layout.home)
    for key, limit in (('native_agent_home', 12), ('git_delivery_target', 8)):
        if len(value[key]) > limit:
            raise Blocked(f'此类 V2 登记最多 {limit} 项。')
        for entry in value[key]:
            name, separator, path = entry.partition('=')
            if key == 'native_agent_home':
                provider, _, name = name.partition(':')
                if provider not in ('codex', 'claude') or name == 'default':
                    raise Blocked('请按 provider:NAME=/absolute/home 登记独立原生 HOME。')
            if not separator or not PROFILE_ID.fullmatch(name):
                raise Blocked('原生 HOME 或本机 Git 交付名称无效。')
            path = Path(clean_path(path))
            if any(path.is_relative_to(private_path) or private_path.is_relative_to(path)
                   for private_path in (layout.config, layout.state, layout.desktop.parent, layout.command.parent)):
                raise Blocked('独立原生 HOME 和 Git 交付目标须与 V2 安装及状态目录分开。')
    if (value['managed_codex'] or value['managed_claude']) and not value['native_history']:
        raise Blocked('启用受管会话还需要明确启用原生历史。')
    if value['codex_terminal'] and not value['managed_codex']:
        raise Blocked('启用 Codex 原生终端还需要明确启用受管 Codex 会话。')
    return value


def roots(layout, values):
    if not 1 <= len(values) <= 16:
        raise Blocked('请用 --allow-root 明确登记 1 至 16 个工作区根目录。')
    protected = [*protected_roots(layout), layout.config, layout.desktop.parent, layout.command.parent]
    table = mount_table()
    with opened(layout.home, directory=True) as fd:
        home_identity = StorageRootIdentity.from_fd(fd)
    home = physical_path(layout.home, home_identity, table)
    anchored = []
    for entry in protected:
        try:
            entry = entry.resolve(strict=True)
            with opened(entry, directory=True) as fd:
                identity = StorageRootIdentity.from_fd(fd)
            anchored.append((identity.device, physical_path(entry, identity, table)))
        except (FileNotFoundError, NotADirectoryError):
            continue

    result = []
    for value in values:
        path = Path(clean_path(value))
        if (layout.home.is_relative_to(path) or any(part.startswith('.') for part in path.parts[1:])
                or any(path.is_relative_to(entry) or entry.is_relative_to(path) for entry in protected)):
            raise Blocked('请选择具体的项目根目录，并与 HOME、V2 状态、产品源码和原生账户分开。')
        with opened(path, directory=True) as fd:
            identity = StorageRootIdentity.from_fd(fd)
        physical = physical_path(path, identity, table)
        if ((identity.device == home_identity.device and home.is_relative_to(physical))
                or any(identity.device == dev and (physical.is_relative_to(other) or other.is_relative_to(physical))
                       for dev, other in anchored)):
            raise Blocked('工作区根目录不能使用其他授权位置的挂载别名。')
        record = {'path': str(path), 'identity': [identity.device, identity.inode]}
        if record not in result:
            result.append(record)
    return result


def load_settings(layout):
    value = read_json(layout.settings)
    if value is None:
        raise Blocked('V2 尚未安装，请先运行 launcher/linux/install-v2.sh --allow-root /绝对路径。')
    return validate_settings(value, layout)


def check_roots(layout, settings):
    if roots(layout, [entry['path'] for entry in settings['roots']]) != settings['roots']:
        raise Blocked('已登记工作区根目录的身份已改变，请重新运行 configure --allow-root。')


def configure(layout, args, old):
    if old is None and args.allow_root is None:
        raise Blocked('首次安装需要 --allow-root /绝对路径。')
    if old is not None:
        validate_settings(old, layout)
    else:
        with socket.socket() as probe:
            probe.bind((HOST, 0))
            port = probe.getsockname()[1]
        old = dict(version=1, port=port, codex_cli=None, **dict.fromkeys(OPTIONS, False),
                   **{key: [] for key in REGISTRATIONS})
    config = dict(old)
    if args.allow_root is not None:
        config['roots'] = roots(layout, args.allow_root)
    if args.port is not None:
        config['port'] = args.port
    if args.codex_cli is not None:
        config['codex_cli'] = clean_path(args.codex_cli)
    for option in (*OPTIONS, *REGISTRATIONS):
        chosen = getattr(args, option)
        if chosen is not None:
            config[option] = chosen
    validate_settings(config, layout)
    check_roots(layout, config)
    if config != old:
        raw = canonical(config) + b'\n'
        if len(raw) > 65536:
            raise Blocked('V2 桌面配置超过读取上限。')
        write_file(layout.settings, raw)


def desktop_arg(path):
    value = str(path).replace('\\', '\\\\\\\\').replace('%', '%%')
    for char in ('"', '`', '$'):
        value = value.replace(char, '\\\\' + char)
    return '"' + value + '"'


def install_entries(layout):
    launcher = '#!/bin/sh\n' + MARKER.decode() + '\nexec ' + shlex.join([PYTHON, '-B', str(SCRIPT)]) + ' "$@"\n'
    icon = str(SOURCE / 'launcher/assets/devflower.svg').replace('\\', '\\\\')
    desktop = (MARKER.decode() + '\n[Desktop Entry]\nType=Application\nVersion=1.0\nName=DevFlower V2\n'
               'Comment=Open the private local DevFlower V2 workbench\n'
               f'Exec=/bin/sh {desktop_arg(layout.command)} start\nIcon={icon}\n'
               'Terminal=false\nCategories=Development;\nStartupNotify=false\n')
    write_file(layout.command, launcher.encode(), mode=0o700, asset=True)
    write_file(layout.desktop, desktop.encode(), mode=0o644, asset=True)


def inspect_service(layout):
    row = read_json(layout.receipt)
    if row is None:
        return None
    identity = row['process']
    pid = identity['pid']
    if (row['version'] != 1 or type(pid) is not int or pid <= 1
            or type(row['port']) is not int or not 1024 <= row['port'] <= 65535
            or not re.fullmatch(r'[A-Za-z0-9_-]{43}', row['token'])):
        raise Blocked('V2 服务登记无效，无法安全操作。')
    if not alive(identity):
        return None
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as stream:
            argv = stream.read(16385)
        image = os.stat(f'/proc/{pid}/exe')
        uid = os.stat(f'/proc/{pid}').st_uid
    except FileNotFoundError:
        if not alive(identity):
            return None
        raise
    current = process_identity(pid)
    if current is None or not alive(identity):
        return None
    args = row['argv']
    if (uid != os.geteuid() or current['sid'] != pid or current['pgid'] != pid
            or len(args) != 6 or args[1] != '-B' or Path(args[2]).name != SCRIPT.name or args[3:] != layout.service_args()
            or argv != b'\0'.join(os.fsencode(arg) for arg in args) + b'\0'
            or row['image'] != [image.st_dev, image.st_ino]):
        raise Blocked('无法核验 V2 服务进程身份，未操作此进程。')
    return row


def serving(row):
    connection = http.client.HTTPConnection(HOST, row['port'], timeout=1)
    try:
        connection.request('GET', '/api/v2/status', headers={'Authorization': 'Bearer ' + row['token']})
        response = connection.getresponse()
        if response.status != 200:
            return False
        value = json.loads(response.read(2097153))
        return value.get('channel') == 'Development' and value.get('environment', {}).get('id') == row['environment']
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def service_url(row):
    return f'http://{HOST}:{row["port"]}/#' + row['token']


def require_no_other_service(layout):
    try:
        with opened(layout.workbench / 'v2-service.lock') as fd:
            lock(fd)
    except FileNotFoundError:
        pass
    except BlockingIOError:
        raise Blocked('此状态目录已有其他 V2 进程，桌面入口未接管。') from None


def start(layout, settings):
    check_roots(layout, settings)
    settings_sha = digest(canonical(settings))
    row = inspect_service(layout)
    if row is not None:
        if row['settings'] != settings_sha or not serving(row):
            raise Blocked('V2 进程已存在但设置或响应无法核对。请先运行 status 或 stop。')
        return service_url(row)
    require_no_other_service(layout)
    with directory(layout.state, create=True) as parent:
        fd = os.open('desktop.log', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as log:
            private(os.fstat(log.fileno()))
            log.truncate(0)
            child = subprocess.Popen([PYTHON, '-B', str(SCRIPT), *layout.service_args()], start_new_session=True,
                                     cwd='/', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and child.poll() is None:
        row = inspect_service(layout)
        if row and row['process']['pid'] == child.pid:
            if row['settings'] != settings_sha:
                break
            if serving(row):
                return service_url(row)
        time.sleep(0.07)
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    raise Blocked(f'V2 未能启动。请查看 {layout.state / "desktop.log"}，并核对端口是否已占用。')


def stop(layout):
    row = inspect_service(layout)
    if row is None:
        require_no_other_service(layout)
        return
    try:
        fd = os.pidfd_open(row['process']['pid'])
    except ProcessLookupError:
        return
    try:
        if inspect_service(layout) != row:
            return
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            return
        if not select.select([fd], [], [], 15)[0]:
            raise Blocked('V2 尚在关闭。已发出正常停止请求，请稍后重新查看状态。')
    finally:
        os.close(fd)


def serve(layout):
    import devflower_v2

    settings = load_settings(layout)
    check_roots(layout, settings)
    args = ['--state-dir', str(layout.workbench), '--port', str(settings['port'])]
    for root in settings['roots']:
        args += ['--allow-root', root['path']]
    if settings['codex_cli']:
        args += ['--codex-cli', settings['codex_cli']]
    for key in REGISTRATIONS:
        for entry in settings[key]:
            args += ['--' + key.replace('_', '-'), entry]
    if settings['native_history']:
        path = layout.state / 'default-native-history.json'
        write_file(path, canonical({'version': 1, 'discover_defaults': True}) + b'\n')
        args += ['--native-history-config', str(path)]
    writes = {key: True for key, flag in (('codex','managed_codex'), ('claude','managed_claude'),
                                          ('codex_terminal','codex_terminal')) if settings[flag]}
    if writes:
        path = layout.state / 'default-native-writes.json'
        write_file(path, canonical({'version': 1, **writes}) + b'\n')
        args += ['--native-session-writes', str(path)]

    def ready(port, token, store):
        check_roots(layout, settings)
        if port != settings['port'] or list(map(str, store.roots)) != [row['path'] for row in settings['roots']]:
            raise Blocked('V2 服务地址或工作区与已保存设置不一致。')
        image = os.stat('/proc/self/exe')
        row = dict(version=1, port=port, token=token, environment=store.environment['id'],
                   process=process_identity(os.getpid()), image=[image.st_dev, image.st_ino],
                   argv=[PYTHON, '-B', str(SCRIPT), *layout.service_args()], settings=digest(canonical(settings)))
        write_file(layout.receipt, canonical(row) + b'\n')

    def terminate(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, terminate)
    return devflower_v2.main(args, ready=ready)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('install', 'upgrade', 'configure'):
        command = commands.add_parser(name)
        command.add_argument('--allow-root', action='append', help='登记绝对工作区根目录，可重复，替换现有列表')
        command.add_argument('--port', type=int, help='固定本机端口 1024–65535，默认保留现有端口')
        command.add_argument('--codex-cli', help='已安装 Codex CLI 的绝对路径')
        for option, description in zip(OPTIONS, ('读取默认原生历史', '允许默认 Codex 受管会话',
                                                   '允许默认 Claude 受管会话', '允许受管 Codex 原生终端')):
            command.add_argument('--' + option.replace('_', '-'), action=argparse.BooleanOptionalAction, help=description)
        for key, description in REGISTRATIONS.items():
            flag = key.replace('_', '-')
            group = command.add_mutually_exclusive_group()
            group.add_argument('--' + flag, action='append', help=description + '，可重复，替换此类现有登记')
            group.add_argument('--clear-' + flag + 's', dest=key, action='store_const', const=[])
    commands.add_parser('start').add_argument('--no-browser', action='store_true', help='仅打印私有访问地址')
    commands.add_parser('status')
    commands.add_parser('stop')
    try:
        if (sys.version_info < (3, 11) or sys.platform != 'linux'
                or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal')):
            raise Blocked('V2 桌面入口需要支持 pidfd 的 Linux 和 Python 3.11+。')
        try:
            os.close(os.pidfd_open(os.getpid()))
        except OSError:
            raise Blocked('当前 Linux 环境无法使用 pidfd 安全管理桌面服务。') from None
        layout = Layout()
        if argv == layout.service_args():
            return serve(layout)
        args = parser.parse_args(argv or ['start'])
        with launcher_lock(layout, args.command == 'install'):
            if args.command in ('install', 'upgrade', 'configure'):
                if inspect_service(layout):
                    raise Blocked('请先用 devflower-v2 stop 停止服务，再更改安装或配置。')
                require_no_other_service(layout)
                old = read_json(layout.settings) if args.command == 'install' else load_settings(layout)
                configure(layout, args, old)
                if args.command != 'configure':
                    install_entries(layout)
                    print(f'已安装 DevFlower V2。桌面入口：{layout.desktop}\n命令：{layout.command}')
                else:
                    print('V2 桌面配置已保存。')
            elif args.command == 'stop':
                stop(layout)
                print('DevFlower V2 已停止。')
            elif args.command == 'status':
                row = inspect_service(layout)
                if row is None:
                    require_no_other_service(layout)
                    print('DevFlower V2 已停止。')
                elif serving(row):
                    print(f'DevFlower V2 正在运行，http://{HOST}:{row["port"]}/，PID {row["process"]["pid"]}。')
                else:
                    raise Blocked('V2 进程仍存在，服务响应未核实。可用 stop 正常停止。')
            else:
                url = start(layout, load_settings(layout))
        if args.command == 'start' and (args.no_browser or not webbrowser.open(url, new=2)):
            print(url, flush=True)
        return 0
    except (Blocked, OSError, ValueError, TypeError, KeyError) as exc:
        print('DevFlower V2：' + (str(exc) if isinstance(exc, (Blocked, Invalid))
                               else '无法核对本机目录、配置或进程。'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
