"""Bounded Copilot metadata wire client using sealed inputs and owned capsules."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
import unicodedata

from studio.agent_sessions import safe_text

from .copilot_profile import IdentityChanged
from .native_history_config import unique_pairs


DEADLINE = 25
BROKER_SECONDS = 5
MAX_HEADER = 128
MAX_FRAME = 256 * 1024
MAX_OUTPUT = 512 * 1024
MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}')
FRAME = re.compile(rb'Content-Length: ([0-9]+)\r\n\r\n')
TOKEN = re.compile(rb'[!-~]{1,4096}\n?')
PATH_TEXT = re.compile(r"""(?:^|[\s('"\[=:])(?:/|~/|[A-Za-z]:[\\/])|(?:^|\s)\.\.?/|file:""", re.I)
HOSTS = frozenset(('github.com', 'https://github.com', 'https://github.com/'))
PROGRAM = '/tool/program'
HOME = '/home/catalog'
AUTH = '/auth'
TOKEN_ENV = 'COPILOT_GITHUB_TOKEN'


class CatalogFault(Exception):
    def __init__(self, code, auth_status='unknown'):
        self.code = code
        self.auth_status = auth_status
        super().__init__(code)


def _deadline(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError()
    return left


def _ready(fd, events, deadline):
    with selectors.DefaultSelector() as poll:
        poll.register(fd, events)
        if not poll.select(_deadline(deadline)):
            raise TimeoutError()


def _read(fd, limit, deadline):
    _ready(fd, selectors.EVENT_READ, deadline)
    return os.read(fd, limit)


def _write(fd, raw, deadline):
    while raw:
        _ready(fd, selectors.EVENT_WRITE, deadline)
        raw = raw[os.write(fd, raw):]


def _stop(process):
    deadline = time.monotonic() + 3
    if process.poll() is None:
        for sig, limit in ((signal.SIGTERM, .5), (signal.SIGKILL, 2.5)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=min(limit, _deadline(deadline)))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.poll() is None:
            raise CatalogFault('COPILOT_UNAVAILABLE')


def _sandbox(image, credential, ready, network):
    command = [
        '/usr/bin/bwrap', '--die-with-parent', '--unshare-user', '--unshare-pid',
        '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--dir', '/home', '--perms', '0700', '--tmpfs', HOME,
        '--perms', '0700', '--tmpfs', '/work', '--dir', '/tool',
        '--info-fd', str(ready), '--perms', '0500', '--file', str(image), PROGRAM,
    ]
    if not network:
        command.append('--unshare-net')
    for value in ('/lib', '/lib64', '/usr/lib', '/usr/lib64', '/etc/ld.so.cache'):
        path = Path(value)
        if path.exists():
            command.extend(('--ro-bind', str(path.resolve()), value))
    if credential is not None:
        command.extend(('--dir', AUTH, '--perms', '0400', '--ro-bind-data', str(credential), AUTH + '/hosts.yml'))
    elif network:
        for value in ('/etc/ssl/certs', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf'):
            path = Path(value)
            if path.exists():
                command.extend(('--ro-bind', str(path.resolve()), value))
    command.extend(('--remount-ro', '/', '--chdir', '/work', '--', PROGRAM))
    return command


@contextmanager
def _capsule(image, args, deadline, *, credential=None, token=None, network=False):
    env = {'HOME': HOME, 'TMPDIR': '/tmp', 'PATH': '/tool', 'LC_ALL': 'C.UTF-8',
           'XDG_CONFIG_HOME': HOME + '/config', 'XDG_CACHE_HOME': HOME + '/cache',
           'XDG_STATE_HOME': HOME + '/state'}
    if credential is not None:
        env['GH_CONFIG_DIR'] = AUTH
    if token is not None:
        env.update({TOKEN_ENV: token, 'COPILOT_HOME': HOME + '/copilot', 'COPILOT_DISABLE_KEYTAR': '1'})
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    process = None
    try:
        try:
            fds = (image, status_write) if credential is None else (image, status_write, credential)
            process = subprocess.Popen(_sandbox(image, credential, status_write, network) + args, pass_fds=fds,
                                       env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, bufsize=0)
        except OSError:
            raise CatalogFault('COPILOT_ISOLATION_UNAVAILABLE') from None
        finally:
            os.close(status_write)
        if not _read(status_read, 1024, deadline):
            raise CatalogFault('COPILOT_ISOLATION_UNAVAILABLE')
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        yield process
    finally:
        os.close(status_read)
        if process is not None:
            try:
                _stop(process)
            finally:
                process.stdin.close()
                process.stdout.close()


def _broker(gh, hosts, login, deadline):
    deadline = min(deadline, time.monotonic() + BROKER_SECONDS)
    args = ['auth', 'token', '--hostname', 'github.com', '--user', login, '--no-refresh']
    with _capsule(gh, args, deadline, credential=hosts) as process:
        process.stdin.close()
        raw = bytearray()
        while True:
            chunk = _read(process.stdout.fileno(), 4098 - len(raw), deadline)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 4097:
                raise CatalogFault('COPILOT_AUTH_REQUIRED')
        if process.wait(timeout=_deadline(deadline)) != 0 or not TOKEN.fullmatch(raw):
            raise CatalogFault('COPILOT_AUTH_REQUIRED')
        return bytes(raw).removesuffix(b'\n').decode('ascii')


def _invalid_constant(_):
    raise ValueError()


class MetadataWire:
    def __init__(self, process, deadline):
        self.process = process
        self.deadline = deadline
        self.pending = bytearray()
        self.received = 0

    def _fill(self):
        allowance = min(65536, MAX_OUTPUT + 1 - self.received)
        block = _read(self.process.stdout.fileno(), allowance, self.deadline)
        self.received += len(block)
        if not block or self.received > MAX_OUTPUT:
            raise CatalogFault('COPILOT_PROTOCOL_ERROR')
        self.pending.extend(block)

    def _response(self, request_id):
        separator = self.pending.find(b'\r\n\r\n')
        while separator == -1:
            if len(self.pending) >= MAX_HEADER:
                raise CatalogFault('COPILOT_PROTOCOL_ERROR')
            self._fill()
            separator = self.pending.find(b'\r\n\r\n')
        end = separator + 4
        header = FRAME.fullmatch(self.pending[:end]) if end <= MAX_HEADER else None
        if header is None:
            raise CatalogFault('COPILOT_PROTOCOL_ERROR')
        length = int(header[1])
        if not 0 < length <= MAX_FRAME:
            raise CatalogFault('COPILOT_PROTOCOL_ERROR')
        while len(self.pending) < end + length:
            self._fill()
        try:
            value = json.loads(self.pending[end:end + length].decode('utf-8'),
                               object_pairs_hook=unique_pairs, parse_constant=_invalid_constant)
            if (not isinstance(value, dict) or set(value) != {'jsonrpc', 'id', 'result'} or
                    value['jsonrpc'] != '2.0' or type(value['id']) is not int or value['id'] != request_id or
                    not isinstance(value['result'], dict)):
                raise ValueError()
        except (ValueError, RecursionError):
            raise CatalogFault('COPILOT_PROTOCOL_ERROR') from None
        del self.pending[:end + length]
        if self.pending:
            raise CatalogFault('COPILOT_PROTOCOL_ERROR')
        return value['result']

    def request(self, request_id, method, params):
        # Reject any unsolicited output already available before sending.
        with selectors.DefaultSelector() as poll:
            poll.register(self.process.stdout, selectors.EVENT_READ)
            if poll.select(0):
                raise CatalogFault('COPILOT_PROTOCOL_ERROR')
        raw = json.dumps(dict(jsonrpc='2.0', id=request_id, method=method, params=params),
                         separators=(',', ':')).encode()
        _write(self.process.stdin.fileno(), f'Content-Length: {len(raw)}\r\n\r\n'.encode() + raw, self.deadline)
        return self._response(request_id)

    def finish(self):
        self.process.stdin.close()
        if (_read(self.process.stdout.fileno(), 1, self.deadline) or
                self.process.wait(timeout=_deadline(self.deadline)) != 0):
            raise CatalogFault('COPILOT_PROTOCOL_ERROR')


def _private_text(text, profile, token):
    lowered = text.lower()
    return (token.lower() in lowered or profile.login.lower() in lowered or
            any(str(source.path).lower() in lowered for source in
                (profile.registration, profile.hosts, profile.gh, profile.copilot)) or
            safe_text(text) != text)


def _directory(value, profile, token):
    models = value.get('models')
    if not isinstance(models, list) or not 1 <= len(models) <= 200:
        raise CatalogFault('COPILOT_DIRECTORY_INVALID', 'authenticated')
    result, used = [], set()
    for entry in models:
        model = entry.get('id') if isinstance(entry, dict) else None
        if (not isinstance(model, str) or not MODEL_ID.fullmatch(model) or model in used or
                ':/' in model or any(part in ('.', '..') for part in model.split('/')) or
                _private_text(model, profile, token)):
            raise CatalogFault('COPILOT_DIRECTORY_INVALID', 'authenticated')
        used.add(model)
        row = {'id': model}
        name = entry.get('name')
        if (isinstance(name, str) and 1 <= len(name) <= 160 and
                all(unicodedata.category(char) not in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp') for char in name) and
                len(name.encode('utf-8')) <= 640 and not PATH_TEXT.search(name) and
                not _private_text(name, profile, token)):
            row['name'] = name
        result.append(row)
    return result


def query_catalog(process, profile, token, deadline):
    wire = MetadataWire(process, deadline)
    connected = wire.request(1, 'connect', {'supportedTaskKinds': [],
        'clientInfo': {'extensionName': 'DevFlower', 'extensionVersion': '0.1'}})
    if type(connected.get('protocolVersion')) is not int or connected['protocolVersion'] != 3:
        raise CatalogFault('COPILOT_PROTOCOL_ERROR')
    auth = wire.request(2, 'auth.getStatus', {})
    signed_in = auth.get('isAuthenticated')
    if type(signed_in) is not bool:
        raise CatalogFault('COPILOT_PROTOCOL_ERROR')
    if not signed_in:
        raise CatalogFault('COPILOT_AUTH_REQUIRED', 'unauthenticated')
    login, host = auth.get('login'), auth.get('host')
    if (not isinstance(login, str) or not login.isascii() or login.lower() != profile.login.lower() or
            not isinstance(host, str) or host not in HOSTS):
        raise CatalogFault('COPILOT_AUTH_REQUIRED')
    value = wire.request(3, 'models.list', {})
    wire.finish()
    return _directory(value, profile, token)


def refresh_metadata(profile, *, network=True):
    deadline = time.monotonic() + DEADLINE
    try:
        with ExitStack() as inputs:
            gh = inputs.enter_context(profile.gh.sealed(deadline))
            hosts = inputs.enter_context(profile.hosts.sealed(deadline))
            copilot = inputs.enter_context(profile.copilot.sealed(deadline))
            profile.verify()
            token = _broker(gh, hosts, profile.login, deadline)
            profile.verify()
            args = ['--headless', '--stdio', '--no-auto-update', '--log-level', 'none',
                    '--no-auto-login', '--no-custom-instructions', '--disable-builtin-mcps',
                    '--auth-token-env', TOKEN_ENV, '--secret-env-vars', TOKEN_ENV]
            with _capsule(copilot, args, deadline, token=token, network=network) as process:
                models = query_catalog(process, profile, token, deadline)
            profile.verify()
            _deadline(deadline)
            return models
    except IdentityChanged:
        raise CatalogFault('COPILOT_IDENTITY_CHANGED') from None
    except (TimeoutError, subprocess.TimeoutExpired):
        raise CatalogFault('COPILOT_TIMEOUT') from None
    except (BrokenPipeError, ConnectionResetError):
        raise CatalogFault('COPILOT_PROTOCOL_ERROR') from None
    except (OSError, ValueError):
        raise CatalogFault('COPILOT_UNAVAILABLE') from None
