"""Read Claude Code's native SDK initialization without starting a turn."""
import json
import os
import re
import secrets
import selectors
import subprocess
import time

from orchestration.common import Blocked, cli_probe_env
from studio.local_claude_adapter import chat_only_args, claude_secrets
from studio.native_codex_image import sealed_executable

from .native_history_config import unique_pairs

MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,155}(?:\[1m\])?')
LIMIT = 300_000
TIMEOUT = 12


def model_id(value, literals=()):
    if (isinstance(value, str) and MODEL.fullmatch(value) and '://' not in value
            and not any(secret and secret in value for secret in literals)):
        return value
    return None


def project_catalog(response, efforts, literals=()):
    if not isinstance(response, dict) or not isinstance(response.get('models'), list) or len(response['models']) > 128:
        raise Blocked('Claude CLI 未返回有界的原生模型目录。')
    models, seen = [], set()
    default = None
    for row in response['models']:
        if not isinstance(row, dict):
            continue
        ident = model_id(row.get('value'), literals)
        if not ident or ident in seen:
            continue
        seen.add(ident)
        resolved = model_id(row.get('resolvedModel'), literals)
        levels = row.get('supportedEffortLevels')
        supported = [effort for effort in efforts if effort in levels] if isinstance(levels, list) else []
        if ident == 'default':
            default = resolved
        models.append(dict(id=ident, name=ident, efforts=supported, resolved_model=resolved))
    if not models:
        raise Blocked('Claude CLI 未返回可显示的原生模型。')
    return {'models': models, 'menu_default_model': default}


def receive_reply(process, request_id):
    pending = bytearray()
    total = 0
    deadline = time.monotonic() + TIMEOUT
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while (remaining := deadline - time.monotonic()) > 0:
            if not selector.select(remaining):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            total += len(chunk)
            if not chunk or total > LIMIT:
                break
            pending.extend(chunk)
            while b'\n' in pending:
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                value = json.loads(line, object_pairs_hook=unique_pairs)
                if not isinstance(value, dict) or value.get('type') != 'control_response':
                    continue
                response = value.get('response')
                if not isinstance(response, dict) or response.get('request_id') != request_id:
                    continue
                if response.get('subtype') == 'success':
                    return response.get('response')
                raise Blocked('Claude CLI 已拒绝原生模型目录初始化。')
    raise Blocked('Claude CLI 未确认原生模型目录。')


def observe_native_catalog(profile, cwd, environment):
    executable = profile['executable']
    args = [executable, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--verbose', '--no-session-persistence', *chat_only_args()]
    request_id = secrets.token_hex(16)
    request = {'type': 'control_request', 'request_id': request_id,
               'request': {'subtype': 'initialize', 'hooks': None}}
    env = dict(cli_probe_env(profile['home']), **environment)
    try:
        with sealed_executable(executable, 'devflower-claude-catalog',
                               expected_sha256=profile['_executable_sha256']) as (fd, _):
            with subprocess.Popen(args, executable=f'/proc/self/fd/{fd}', pass_fds=(fd,), cwd=cwd, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  start_new_session=True) as process:
                try:
                    process.stdin.write(json.dumps(request).encode() + b'\n')
                    process.stdin.flush()
                    response = receive_reply(process, request_id)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        return project_catalog(response, profile['efforts'], claude_secrets(profile, environment))
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        raise Blocked('Claude 原生模型发现未完成。') from None
