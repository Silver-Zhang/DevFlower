#!/usr/bin/env python3
"""Start a private local Development workbench; never issue release approval."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import webbrowser

sys.dont_write_bytecode = True
from orchestration.common import Blocked, canonical, clean_env, digest, manifest
from orchestration.store import alive, process_identity
from studio.session_sources import clean_path, opened

ROOT = Path(__file__).resolve().parents[1]
BWRAP = Path('/usr/bin/bwrap')  # The existing isolated runner uses this exact path.
KIND = 'devflower-public-start/1'
GUIDANCE = '保留旧目录和会话；停止原服务并核对配置，或用 --state-dir 指定新的私有目录。'


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', help='private state directory; default: XDG_STATE_HOME/devflower/workbench')
    p.add_argument('--host', choices=['127.0.0.1'], default='127.0.0.1')
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--check', action='store_true', help='read-only preflight; no service, inference or files created')
    p.add_argument('--no-browser', action='store_true', help='print the private local URL instead of opening a browser')
    p.add_argument('--provider', action='append', choices=['codex', 'claude'], help='limit discovery to these providers')
    for provider in ('codex', 'claude'):
        p.add_argument('--'+provider+'-cli', help='installed CLI executable')
        p.add_argument('--'+provider+'-model', help='explicit requested model; no availability claim')
        p.add_argument('--'+provider+'-effort', help='explicit requested effort; no inferred default')
    return p


def private(meta, directory=False):
    expected = 0o700 if directory else 0o600
    if (meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != expected or
            not (stat.S_ISDIR(meta.st_mode) if directory else stat.S_ISREG(meta.st_mode)) or
            not directory and meta.st_nlink != 1):
        raise Blocked('运行状态必须归当前用户所有，目录 0700、单链接常规文件 0600；不自动修正已有文件。')


@contextmanager
def directory(path, create=False):
    """Hold a directory reached via openat; never follow even an ancestor link."""
    path = Path(clean_path(str(path)))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError: pass
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        private(os.fstat(fd), True)
        yield fd
    finally:
        os.close(fd)


def read(path, *, confidential=False, limit=200000):
    with opened(path) as fd:
        before = os.fstat(fd)
        if confidential: private(before)
        if before.st_size > limit: raise Blocked('配置文件超过读取预算。')
        raw = os.read(fd, limit+1)
        after = os.fstat(fd)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if stamp(before) != stamp(after) or len(raw) != after.st_size:
            raise Blocked('配置在读取期间变化；未启动。')
        return raw


def file_hash(path):
    with opened(path) as fd:
        before = os.fstat(fd)
        value = hashlib.sha256()
        while block := os.read(fd, 1024*1024): value.update(block)
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Blocked('依赖在核验期间变化；未启动。')
        return value.hexdigest()


def write_new(path, value):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)+b'\n')
            stream.flush()
            os.fsync(stream.fileno())


def probe(argv, home):
    """Only metadata/help and a /bin/true isolation probe; discard native stderr."""
    env = clean_env(str(home))
    env['PATH'] = os.environ.get('PATH', env['PATH'])
    env['CODEX_HOME'] = str(home/'.codex')
    try:
        result = subprocess.run([str(v) for v in argv], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd='/', env=env, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked('CLI/隔离探测未完成；检查工具安装与权限，不会自动重试。') from None
    if result.returncode or len(result.stdout) > 200000:
        raise Blocked('CLI/隔离探测失败；检查安装、版本及 Linux 用户命名空间权限。')
    return result.stdout.decode('utf-8', 'replace')


def executable(value):
    try: path = Path(value).resolve(strict=True)
    except FileNotFoundError:
        raise Blocked('CLI/依赖路径不存在；请检查安装或 --codex-cli/--claude-cli 参数。') from None
    if not path.is_file() or not os.access(path, os.X_OK): raise Blocked('CLI 不是可执行常规文件。')
    file_hash(path)
    return path


def system_preflight(home):
    if sys.version_info < (3, 11) or not sys.platform.startswith('linux'):
        raise Blocked('需要 Python 3.11+ 和 Linux。')
    git = shutil.which('git')
    if not git: raise Blocked('缺少 Git；请先安装 Git。')
    git = executable(git)
    probe([git, '--version'], home)
    if not BWRAP.is_file(): raise Blocked('缺少 /usr/bin/bwrap；请安装 bubblewrap。')
    bwrap = executable(BWRAP)
    probe([bwrap, '--die-with-parent', '--unshare-user', '--unshare-pid', '--unshare-ipc',
           '--unshare-uts', '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
           '--ro-bind', '/bin', '/bin', '--ro-bind', '/lib', '/lib',
           *(['--ro-bind', '/lib64', '/lib64'] if Path('/lib64').exists() else []),
           '--proc', '/proc', '--dev', '/dev', '--', '/bin/true'], home)
    from orchestration.sandbox import abi
    if abi() < 6: raise Blocked('现有隔离执行器需要 Landlock ABI >= 6；不能以 chmod 替代。')
    for name in ('workbench.html', 'workbench.css', 'workbench.js'):
        if not (ROOT/'tools/studio/static'/name).is_file():
            raise Blocked('随库前端不完整；请取得完整发行副本，无需 Node 启动。')
    return {str(p): file_hash(p) for p in (git, bwrap, executable(sys.executable))}


def preference(value, option):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise Blocked('缺少或无效模型/强度；请从自己的原生配置确认并明确传入 '+option+'，不推断模型存在。')
    return value


def discover(args, home):
    profiles, local, models, dependencies = {}, {}, {}, {}
    notices = []
    selected = args.provider or [p for p in ('codex', 'claude') if getattr(args, p+'_cli') or shutil.which(p)]
    if not selected: raise Blocked('未找到 Codex/Claude CLI；请先安装并自行登录，或传入 --codex-cli/--claude-cli。')
    for provider in dict.fromkeys(selected):
        command = getattr(args, provider+'_cli') or shutil.which(provider)
        if not command: raise Blocked('缺少 '+provider+' CLI；请安装或传入 --'+provider+'-cli。')
        exe = executable(command)
        root = home/('.'+provider)
        try:
            with opened(root, directory=True) as fd:
                if os.fstat(fd).st_uid != os.getuid(): raise Blocked('原生配置根不属于当前用户。')
        except FileNotFoundError:
            raise Blocked(provider+' 原生配置根不存在；请先在自己的 CLI 完成登录和配置，不会代建原生 HOME。') from None
        override = os.environ.get('CODEX_HOME' if provider == 'codex' else 'CLAUDE_CONFIG_DIR')
        if override and Path(override) != root:
            raise Blocked('自定义原生配置根不符合现有 native_local schema；请使用管理员入口，不迁移原配置。')
        config = root/('config.toml' if provider == 'codex' else 'settings.json')
        try:
            raw = read(config)
            data = tomllib.loads(raw.decode()) if provider == 'codex' else json.loads(raw)
            if not isinstance(data, dict): raise ValueError()
        except FileNotFoundError:
            raw, data = None, {}
        except (ValueError, UnicodeError):
            raise Blocked('原生配置格式无效；请用原生工具核对，不覆盖配置。') from None
        if provider == 'codex' and data.get('profile'):
            raise Blocked('当前启用了 Codex profile；公共入口不改写其路由，请使用管理员入口核对配置。')
        env = data.get('env', {}) if provider == 'claude' else {}
        if not isinstance(env, dict): raise Blocked('Claude env 配置格式无效。')
        # Native adapters intentionally use a clean environment. Never silently
        # replace an environment-only model, gateway or account with defaults.
        inherited = ('OPENAI_API_KEY', 'OPENAI_BASE_URL') if provider == 'codex' else (
            'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
            'ANTHROPIC_MODEL', 'CLAUDE_CODE_EFFORT_LEVEL')
        if any(os.environ.get(key) and os.environ[key] != env.get(key) for key in inherited):
            raise Blocked('原生配置依赖当前环境变量；公共入口不会复制认证或更换服务，请用管理员入口核对配置。')
        model = data.get('model') or env.get('ANTHROPIC_MODEL')
        effort = data.get('model_reasoning_effort' if provider == 'codex' else 'effortLevel')
        if provider == 'claude': effort = effort or env.get('CLAUDE_CODE_EFFORT_LEVEL')
        model_arg, effort_arg = getattr(args, provider+'_model'), getattr(args, provider+'_effort')
        if provider == 'claude' and (
                model_arg is None and data.get('model') and env.get('ANTHROPIC_MODEL') and data['model'] != env['ANTHROPIC_MODEL'] or
                effort_arg is None and data.get('effortLevel') and env.get('CLAUDE_CODE_EFFORT_LEVEL') and data['effortLevel'] != env['CLAUDE_CODE_EFFORT_LEVEL']):
            raise Blocked('Claude 用户配置有冲突的模型/强度来源；请明确传入 --claude-model/--claude-effort，不猜测优先级。')
        model = preference(model_arg if model_arg is not None else model, '--'+provider+'-model')
        effort = preference(effort_arg if effort_arg is not None else effort, '--'+provider+'-effort')
        probe([exe, '--version'], home)
        help_text = probe([exe, 'exec', '--help'] if provider == 'codex' else [exe, '--help'], home)
        required = ('--json',) if provider == 'codex' else ('--output-format', '--model', '--effort', '--setting-sources')
        if any(flag not in help_text for flag in required):
            raise Blocked(provider+' CLI 缺少现有适配器所需参数；请核对安装版 --help，未启动模型。')
        profiles['default-'+provider] = dict(provider=provider, executable=str(exe), model=model,
            effort=effort, read_paths=[], readonly_files=(
                [[str(config), '.claude/settings.json']] if provider == 'claude' and raw is not None else []))
        local[provider] = dict(home=str(home), config_root=str(root))
        models[provider] = [dict(id=model, efforts=[effort], inference_verified=False,
            source='explicit launcher argument' if model_arg is not None else 'native user config: '+config.name,
            effort_source='explicit launcher argument' if effort_arg is not None else 'native user config: '+config.name)]
        dependencies[str(exe)] = file_hash(exe)
        if raw is not None: dependencies[str(config)] = digest(raw)
        notices.append(provider+': 已核验 CLI 元数据；模型为明确偏好，认证与实际推理均未验证。')
    return profiles, local, models, dependencies, notices


def location(args, home):
    base = os.environ.get('XDG_STATE_HOME') or str(home/'.local/state')
    state = Path(clean_path(args.state_dir or str(Path(base)/'devflower/workbench')))
    for protected in (ROOT, home/'.codex', home/'.claude'):
        if state.is_relative_to(protected) or protected.is_relative_to(state):
            raise Blocked('状态目录不能与源码或原生配置范围重叠。')
    # Check existing ancestors without creating anything, even for --check.
    ancestor = state
    while not ancestor.exists() and not ancestor.is_symlink(): ancestor = ancestor.parent
    with opened(ancestor, directory=True): pass
    return state


def inspect_state(state):
    with directory(state) as fd:
        entries = set(os.listdir(fd))
        if entries != {'config', 'projects', 'runtime', 'native-cache'}:
            raise Blocked('状态目录含未知或未完成初始化内容。'+GUIDANCE)
    for name in ('config', 'projects', 'runtime', 'native-cache'):
        with directory(state/name): pass
    if set(os.listdir(state/'config')) - {'operator.json', 'policy.json', 'assistant-settings'}:
        raise Blocked('配置目录含未知文件。'+GUIDANCE)
    runtime_names = {'.devflower-session-center', 'installation.json', 'service.json', 'start-intent.json',
        'workbench-capability', 'workbench-service.lock', 'canvas-assistant', 'discovery',
        'configuration-context', 'configuration-artifacts', 'session-artifacts', 'model-checks',
        'model-check-homes', 'skill-snapshots', 'managed-child-endpoints', 'native-attach',
        'metadata-artifacts', 'project-records', 'flow-artifacts', 'external-artifacts'}
    runtime_names |= {db+suffix for db in ('usable.sqlite', 'workbench.sqlite', 'sessions.sqlite')
                      for suffix in ('', '-wal', '-shm', '-journal')}
    for name in os.listdir(state/'runtime'):
        if name not in runtime_names and not re.fullmatch(r'(?:source-|model-admission-)[0-9a-f]{64}\.lock|[0-9a-f]{32}\.worker\.lock', name):
            raise Blocked('运行目录含未知文件。'+GUIDANCE)
    # Runtime-owned files may grow as users run tasks. Reject links/nonprivate
    # nodes without interpreting or copying their contents or native history.
    for folder, dirs, files in os.walk(state, followlinks=False):
        for name in dirs+files:
            p = Path(folder)/name
            private(p.lstat(), p.is_dir() and not p.is_symlink())
    record = json.loads(read(state/'runtime/installation.json', confidential=True))
    if record.get('kind') != KIND or record.get('root') != str(ROOT):
        raise Blocked('未知的安装记录或源码位置。'+GUIDANCE)
    return record


def verify(state, record, candidate):
    if record['candidate_sha256'] != candidate: raise Blocked('源码版本已变化；不盲目复用。'+GUIDANCE)
    for name in ('operator', 'policy'):
        raw = read(state/'config'/(name+'.json'), confidential=True)
        if digest(raw) != record[name+'_sha256']: raise Blocked('运行配置已变化。'+GUIDANCE)
    op = json.loads(read(state/'config/operator.json', confidential=True))
    if ('stable' in op or op.get('kind') != 'development-sessions' or
            op.get('storage') != str(state/'runtime') or op.get('native') != str(state/'native-cache') or
            op.get('allowed_roots') != [str(state/'projects')]):
        raise Blocked('公共入口配置范围不符；不会授予 Stable 或交付权限。')
    for path, pin in op['dependencies'].items():
        if file_hash(path) != pin: raise Blocked('本机依赖/原生配置变化。'+GUIDANCE)
    return op


def service(state, record, candidate, args):
    path = state/'runtime/service.json'
    if not path.exists():
        if (state/'runtime/start-intent.json').exists(): raise Blocked('上次启动结果未确认。'+GUIDANCE)
        return None
    receipt = json.loads(read(path, confidential=True))
    if (receipt.get('kind') != KIND or receipt.get('host') != '127.0.0.1' or
            receipt.get('candidate_sha256') != candidate or
            any(receipt.get(k) != record[k] for k in ('operator_sha256', 'policy_sha256')) or
            not alive(receipt.get('process')) or
            args.port and args.port != receipt.get('port')):
        raise Blocked('受管服务的进程/候选/配置不能确认。'+GUIDANCE)
    port = receipt['port']
    if type(port) is not int or not 1 <= port <= 65535: raise Blocked('本机端口记录无效。')
    # Bind the receipt to a real listening socket held by this exact PID.
    lines = Path('/proc/net/tcp').read_text().splitlines()[1:]
    inodes = {line.split()[9] for line in lines if line.split()[1] == '0100007F:'+format(port, '04X') and line.split()[3] == '0A'}
    handles = Path('/proc')/str(receipt['process']['pid'])/'fd'
    sockets = set()
    for path in handles.iterdir():
        try: sockets.add(os.readlink(path))
        except FileNotFoundError: pass
    if not sockets.intersection({'socket:['+i+']' for i in inodes}):
        raise Blocked('服务 PID 未持有本机监听 socket。'+GUIDANCE)
    token = read(state/'runtime/workbench-capability', confidential=True).decode().strip()
    if not token or len(token) > 200: raise Blocked('访问能力记录无效。')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', '/api/status', headers={'Authorization': 'Bearer '+token})
        response = connection.getresponse()
        data = json.loads(response.read(2000001))
        if response.status != 200 or data.get('release', {}).get('channel') != 'Development' or data.get('candidate') != candidate:
            raise Blocked('现有 HTTP 服务身份不匹配。'+GUIDANCE)
    finally: connection.close()
    if not alive(receipt['process']): raise Blocked('核验期间服务已退出。'+GUIDANCE)
    return f'http://127.0.0.1:{port}/#'+token


def announce(url, args):
    print('Development 本机工作台；认证、模型推理和自动交付未获验证。', flush=True)
    if args.no_browser:
        print('仅供本人使用的私有入口（勿公开日志）：\n'+url, flush=True)
    elif not webbrowser.open(url, new=2):
        print('无法打开浏览器；请用 --no-browser 取得本人私有入口。', flush=True)


def main(argv=None):
    args = parser().parse_args(argv)
    previous_umask = None
    try:
        if not 0 <= args.port <= 65535: raise Blocked('端口须在 0–65535 范围内。')
        home = Path(clean_path(str(Path.home())))
        state = location(args, home)
        dependencies = system_preflight(home)
        profiles, local, models, pins, notices = discover(args, home)
        if any((ROOT/name).exists() or (ROOT/name).is_symlink() for name in ('.codex', '.claude', '.aws', '.ssh')):
            raise Blocked('安装目录包含可能的私有配置；请使用干净的完整公开源码副本，不读取或排除这些内容来伪造完整哈希。')
        files = manifest(ROOT)
        if any(row['mode'] == '120000' for row in files): raise Blocked('公共安装副本含符号链接；请核对完整源码。')
        candidate = digest(canonical(files))
        op = dict(version=1, kind='development-sessions', storage=str(state/'runtime'),
            native=str(state/'native-cache'), allowed_roots=[str(state/'projects')],
            profiles=profiles, dependencies={**dependencies, **pins}, native_local=local)
        policy = dict(version=1, models=models, external_steps={})
        exists = state.exists()
        if args.check:
            if exists:
                record = inspect_state(state)
                previous = verify(state, record, candidate)
                if previous != op or json.loads(read(state/'config/policy.json', confidential=True)) != policy:
                    raise Blocked('本次模型/配置选择与已有状态不同。'+GUIDANCE)
                service(state, record, candidate, args)
            for notice in notices: print(notice)
            print('CHECK OK：依赖/完整源码/配置已检查；未创建状态、启动服务或调用模型。')
            return 0
        previous_umask = os.umask(0o077)
        with directory(state, create=True) as state_fd:
            try: fcntl.flock(state_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Blocked('另一个启动正在核验此状态；未重复启动。') from None
            try:
                if exists or os.listdir(state_fd):
                    record = inspect_state(state)
                    previous = verify(state, record, candidate)
                    if previous != op or json.loads(read(state/'config/policy.json', confidential=True)) != policy:
                        raise Blocked('本次模型/配置选择与已有状态不同。'+GUIDANCE)
                    url = service(state, record, candidate, args)
                    if url:
                        announce(url, args)
                        return 0
                else:
                    for name in ('config', 'projects'): os.mkdir(name, 0o700, dir_fd=state_fd)
                    write_new(state/'config/operator.json', op)
                    write_new(state/'config/policy.json', policy)
                    record = dict(kind=KIND, root=str(ROOT), candidate_sha256=candidate,
                        operator_sha256=digest(read(state/'config/operator.json', confidential=True)),
                        policy_sha256=digest(read(state/'config/policy.json', confidential=True)))
                from studio.workbench_runtime import WorkbenchRuntime
                from studio.workbench_server import create_server
                rt = WorkbenchRuntime(state/'config/operator.json', record['operator_sha256'], candidate,
                    state/'config/policy.json', record['policy_sha256'])
                if not exists: write_new(rt.storage/'installation.json', record)
                # Storage is protected by the existing runtime. No capability,
                # process receipt or startup intent is left in the state parent.
                lock_path = rt.storage/'workbench-service.lock'
                lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                private(os.fstat(lock_fd))
                try: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(lock_fd)
                    raise Blocked('已有服务占用此运行目录；未重复启动。') from None
                server = None
                try:
                    intent = dict(kind=KIND, process=process_identity(os.getpid()))
                    write_new(rt.storage/'start-intent.json', intent)
                    server, token = create_server(rt, args.port)
                    receipt = dict(record, host='127.0.0.1', port=server.server_port, process=process_identity(os.getpid()))
                    write_new(rt.storage/'service.json', receipt)
                    (rt.storage/'start-intent.json').unlink()
                except BaseException:
                    if server: server.server_close()
                    os.close(lock_fd)
                    raise
            finally: fcntl.flock(state_fd, fcntl.LOCK_UN)
        try:
            for notice in notices: print(notice, flush=True)
            announce(f'http://127.0.0.1:{server.server_port}/#'+token, args)
            server.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            server.server_close()
            if json.loads(read(rt.storage/'service.json', confidential=True)) == receipt:
                (rt.storage/'service.json').unlink()
            os.close(lock_fd)
        return 0
    except (Blocked, OSError, ValueError, KeyError, TypeError):
        # Native errors may contain config/credential text. Only our explicit
        # Blocked diagnostics are safe to show; never print raw CLI/JSON errors.
        exc = sys.exc_info()[1]
        print('BLOCKED: '+(str(exc) if isinstance(exc, Blocked) else
              '本机配置、文件权限或服务核验失败；没有覆盖已有状态。'+GUIDANCE), file=sys.stderr)
        return 1
    finally:
        if previous_umask is not None: os.umask(previous_umask)


if __name__ == '__main__':
    raise SystemExit(main())
