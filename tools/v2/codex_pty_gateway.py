"""One authenticated Unix WebSocket to one typed Codex app-server session."""
import base64
import hashlib
import json
import os
import re
import secrets
import select
import socket
import struct
import time

from orchestration.common import Blocked, canonical
from orchestration.privacy import redact
from orchestration.store import process_identity
from studio.agent_sessions import safe_text

from .native_history_config import unique_pairs

FRAME_LIMIT = 2_000_000
WIRE_LIMIT = 48_000_000
GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
PROFILE = 'devflower'
HISTORY = {'thread/turns/list', 'thread/items/list'}
EFFORTS = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
STATUS = {'completed', 'interrupted', 'failed', 'inProgress'}
SAFE_ID = re.compile(r'[A-Za-z0-9_.:-]{1,160}')


class Refused(ValueError):
    pass


def decoded(raw):
    result = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(result, dict):
        raise Refused()
    return result


def request_id(value):
    return type(value) is int and 0 <= value < (1 << 53) or isinstance(value, str) and SAFE_ID.fullmatch(value)


def safe_id(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise Refused()
    return value


def bounded_int(value, *, minimum=0, maximum=(1 << 53) - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Refused()
    return value


def nothing(value):
    return value is None or value == [] or value == {}


class CodexProtocol:
    """Every forwarded request is reconstructed. Replies never relay raw JSON."""
    def __init__(self, binding, session, literals, on_effect, *, profile=PROFILE, on_resume=lambda: None, on_ready=lambda: None,
                 on_shell_denied=lambda: None):
        self.binding, self.session, self.literals, self.on_effect = binding, session, literals, on_effect
        self.on_resume, self.on_ready = on_resume, on_ready
        self.on_shell_denied = on_shell_denied
        self.profile = profile
        self.pending, self.cursors = {}, {}
        self.turn_ids = set()
        self.request_no = 0
        self.initialized = self.resumed = self.resume_sent = False
        self.effect = False
        self.active_turn = None
        self.notice_turn = None

    def text(self, value, limit=60000):
        if not isinstance(value, str):
            raise Refused()
        return safe_text(redact(value[:limit], self.literals))

    def cursor(self, method, raw):
        if raw is None:
            return None
        if not isinstance(raw, str) or not 0 < len(raw) <= 4096 or len(self.cursors) >= 512:
            raise Refused()
        key = secrets.token_hex(16)
        self.cursors[key] = (method, raw)
        return key

    def input(self, envelope):
        if set(envelope) - {'jsonrpc', 'id', 'method', 'params'} or envelope.get('jsonrpc', '2.0') != '2.0':
            raise Refused()
        method, ident = envelope.get('method'), envelope.get('id')
        params = envelope.get('params')
        if params is None:
            params = {}
        if not isinstance(params, dict) or not isinstance(method, str):
            raise Refused()
        if method == 'initialized':
            if ident is not None or params or not self.initialized:
                raise Refused()
            return {'method': method}
        if not request_id(ident) or len(self.pending) >= 24 or any(value[0] == ident for value in self.pending.values()):
            raise Refused()
        if method == 'initialize' and not self.initialized and not self.pending:
            fixed = {'clientInfo': {'name': 'devflower_v2_terminal', 'version': '1'},
                     'capabilities': {'experimentalApi': True}}
        elif not self.initialized:
            raise Refused()
        elif method == 'skills/list':
            cwd = self.binding['cwd']
            if (set(params) - {'cwds', 'forceReload'} or params.get('cwds', []) not in ([], [cwd])
                    or type(params.get('forceReload', False)) is not bool):
                raise Refused()
            # Skills are disabled for this terminal. Do not ask app-server to scan disk.
            return {'id': ident, 'result': {'data': [{'cwd': cwd, 'skills': [], 'errors': []}]}}
        elif method in ('account/read', 'model/list', 'configRequirements/read', 'collaborationMode/list'):
            fixed = {'refreshToken': False} if method == 'account/read' else {'limit': 100} if method == 'model/list' else {}
        else:
            if params.get('threadId') != self.session:
                raise Refused()
            if method == 'thread/shellCommand':
                self.on_shell_denied()
                raise Refused()
            fixed = {'threadId': self.session}
            if method == 'thread/read':
                fixed['includeTurns'] = False
            elif method == 'thread/resume':
                if self.resume_sent or params.get('cwd') not in (None, self.binding['cwd']):
                    raise Refused()
                self.no_overrides(params, {'threadId', 'cwd', 'excludeTurns', 'initialTurnsPage'})
                fixed.update(cwd=self.binding['cwd'], excludeTurns=True, permissions=self.profile, approvalPolicy='never',
                             runtimeWorkspaceRoots=[self.binding['cwd']], model=self.binding['model'])
                self.on_resume()
                self.resume_sent = True
            elif method in HISTORY and self.resumed:
                limit = params.get('limit')
                fixed['limit'] = 30 if limit is None else bounded_int(limit, minimum=1, maximum=100)
                order = params.get('sortDirection')
                if order is not None:
                    if order not in ('asc', 'desc'):
                        raise Refused()
                    fixed['sortDirection'] = order
                if method == 'thread/items/list' and params.get('turnId') is not None:
                    turn_id = safe_id(params['turnId'])
                    if turn_id not in self.turn_ids:
                        raise Refused()
                    fixed['turnId'] = turn_id
                if params.get('cursor') is not None:
                    cursor = self.cursors.get(params['cursor']) if isinstance(params['cursor'], str) else None
                    if not cursor or cursor[0] != method:
                        raise Refused()
                    fixed['cursor'] = cursor[1]
            elif method == 'thread/goal/get' and self.resumed:
                pass
            elif method == 'turn/start' and self.resumed and not self.effect:
                defaults = {'turnTrigger': 'user', 'runtimeWorkspaceRoots': [self.binding['cwd']],
                            'approvalPolicy': 'never', 'approvalsReviewer': 'user',
                            'model': self.binding['model'], 'serviceTier': 'default', 'effort': self.binding['effort']}
                self.no_overrides(params, {'threadId', 'cwd', 'input', 'clientUserMessageId'}, defaults)
                if params.get('cwd') not in (None, self.binding['cwd']):
                    raise Refused()
                items = params.get('input')
                if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
                    raise Refused()
                item = items[0]
                text = item.get('text')
                if (set(item) - {'type', 'text', 'text_elements'} or item.get('type') != 'text'
                        or not nothing(item.get('text_elements')) or not isinstance(text, str)
                        or not 0 < len(text.encode()) <= 60000 or '\0' in text):
                    raise Refused()
                fixed.update(input=[{'type': 'text', 'text': text, 'text_elements': []}],
                             cwd=self.binding['cwd'], permissions=self.profile, runtimeWorkspaceRoots=[self.binding['cwd']],
                             model=self.binding['model'], effort=self.binding['effort'], approvalPolicy='never')
                self.on_effect(True)
                self.effect = True
                self.active_turn = self.notice_turn = None
            elif method == 'turn/interrupt' and self.effect and self.active_turn and params.get('turnId') == self.active_turn:
                fixed['turnId'] = self.active_turn
            else:
                raise Refused()
        self.request_no += 1
        self.pending[self.request_no] = (ident, method)
        return {'id': self.request_no, 'method': method, 'params': fixed}

    def no_overrides(self, params, structural, defaults=None):
        defaults = defaults or {}
        if any(not nothing(value) and (key not in defaults or value != defaults[key])
               for key, value in params.items() if key not in structural):
            raise Refused()

    def item(self, value):
        if not isinstance(value, dict):
            raise Refused()
        kind = value.get('type')
        if kind == 'agentMessage':
            phase = value.get('phase')
            return {'type': kind, 'id': safe_id(value.get('id')), 'text': self.text(value.get('text')),
                    'phase': phase if phase in ('commentary', 'final_answer') else None}
        if kind == 'userMessage':
            if not isinstance(value.get('content'), list):
                raise Refused()
            return {'type': kind, 'id': safe_id(value.get('id')), 'content': [
                {'type': 'text', 'text': self.text(item['text']), 'text_elements': []}
                for item in value['content'] if isinstance(item, dict) and item.get('type') == 'text'
            ]}
        return None

    def turn(self, value):
        if not isinstance(value, dict) or value.get('status') not in STATUS or not isinstance(value.get('items'), list):
            raise Refused()
        result = {'id': safe_id(value.get('id')), 'status': value['status'],
                  'items': [item for raw in value['items'] if (item := self.item(raw)) is not None],
                  'itemsView': value.get('itemsView') if value.get('itemsView') in ('notLoaded', 'summary', 'full') else 'full',
                  'error': None, 'rootTurnId': None}
        for key in ('startedAt', 'completedAt', 'durationMs'):
            result[key] = value.get(key) if type(value.get(key)) is int else None
        if len(self.turn_ids) < 2048:
            self.turn_ids.add(result['id'])
        return result

    def thread(self, raw):
        if (not isinstance(raw, dict) or raw.get('id') != self.session or raw.get('sessionId') != self.session
                or raw.get('cwd') != self.binding['cwd'] or raw.get('ephemeral') is not False
                or raw.get('historyMode') not in ('legacy', 'paginated')):
            raise Refused()
        result = {key: bounded_int(raw.get(key)) for key in ('createdAt', 'updatedAt')}
        status = raw.get('status')
        status = status.get('type') if isinstance(status, dict) else None
        if status not in ('notLoaded', 'idle'):
            raise Refused()
        result.update(id=self.session, sessionId=self.session, cwd=self.binding['cwd'], path=None, gitInfo=None,
                      forkedFromId=None, parentThreadId=None, section=None, sectionEnteredAt=None, projectId=None,
                      historyMode=raw['historyMode'], ephemeral=False, cliVersion=self.text(raw.get('cliVersion'), 40),
                      model=self.binding['model'], reasoningEffort=self.binding['effort'], recencyAt=None,
                      originator=None, threadSource=None, agentNickname=None, agentRole=None, turns=[],
                      source=raw['source'] if raw.get('source') in ('cli', 'vscode', 'exec', 'appServer', 'unknown') else 'unknown',
                      modelProvider=self.text(raw.get('modelProvider', ''), 120), status={'type': status},
                      preview=self.text(raw.get('preview', ''), 300),
                      name=self.text(raw['name'], 160) if isinstance(raw.get('name'), str) else None)
        return result

    def model(self, value):
        efforts = [dict(reasoningEffort=item['reasoningEffort'], description='')
                   for item in value.get('supportedReasoningEfforts', []) if isinstance(item, dict)
                   and item.get('reasoningEffort') in EFFORTS]
        effort = value.get('defaultReasoningEffort')
        return dict(id=self.binding['model'], model=self.binding['model'],
                    displayName=self.binding['model'], description='', upgrade=None, upgradeInfo=None,
                    availabilityNux=None, modelSpecialty=None, hidden=False, supportedReasoningEfforts=efforts,
                    defaultReasoningEffort=effort if effort in EFFORTS else self.binding['effort'], inputModalities=['text'],
                    supportsPersonality=False, multiAgentVersion=None, additionalSpeedTiers=[],
                    serviceTiers=[], defaultServiceTier=None, availableAccessPrograms=None, isDefault=True)

    def result(self, method, raw):
        if not isinstance(raw, dict):
            raise Refused()
        if method == 'initialize':
            self.initialized = True
            return dict(userAgent='DevFlower Codex 0.163.0', codexHome='/tmp/df-ui/.codex', platformFamily='unix', platformOs='linux')
        if method == 'account/read':
            account = raw.get('account')
            if isinstance(account, dict):
                kind = account.get('type')
                if kind == 'chatgpt':
                    account = {'type': kind, 'email': None, 'planType': 'unknown'}
                elif kind == 'apiKey':
                    account = {'type': kind}
                else:
                    account = None
            else:
                account = None
            return dict(account=account, requiresOpenaiAuth=raw.get('requiresOpenaiAuth') is True)
        if method == 'model/list':
            return dict(data=[self.model(row) for row in raw.get('data', []) if isinstance(row, dict)
                        and self.binding['model'] in (row.get('id'), row.get('model'))][:1], nextCursor=None)
        if method == 'configRequirements/read':
            if raw.get('requirements') is not None:
                raise Refused()
            return dict(requirements=None, supportsIndependentSpeedModes=None)
        if method == 'collaborationMode/list':
            return {'data': []}
        if method in ('thread/read', 'thread/resume'):
            thread = self.thread(raw.get('thread'))
            if method == 'thread/read':
                return {'thread': thread}
            profile = raw.get('activePermissionProfile')
            if (not isinstance(profile, dict) or profile.get('id') != self.profile or profile.get('extends') is not None
                    or raw.get('cwd') != self.binding['cwd']
                    or raw.get('runtimeWorkspaceRoots') != [self.binding['cwd']]
                    or raw.get('approvalPolicy') != 'never' or raw.get('model') != self.binding['model']):
                raise Refused()
            self.on_ready()
            self.resumed = True
            return dict(thread=thread, model=self.binding['model'], modelProvider=thread['modelProvider'],
                        cwd=self.binding['cwd'], approvalPolicy='never', approvalsReviewer='user', serviceTier=None,
                        disabledPluginIds=[], instructionSources=[], runtimeWorkspaceRoots=[self.binding['cwd']],
                        activePermissionProfile={'id': self.profile, 'extends': None}, collaborationMode=None,
                        sandbox={'type': 'workspaceWrite', 'writableRoots': [], 'networkAccess': False,
                                 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True},
                        reasoningEffort=self.binding['effort'],
                        turnsBackwardsCursor=self.cursor('thread/turns/list', raw.get('turnsBackwardsCursor')),
                        itemsBackwardsCursor=self.cursor('thread/items/list', raw.get('itemsBackwardsCursor')))
        if method in HISTORY:
            data = []
            for entry in raw.get('data', []):
                if method == 'thread/turns/list':
                    data.append(self.turn(entry))
                elif isinstance(entry, dict) and (item := self.item(entry.get('item'))) is not None:
                    data.append({'item': item, 'turnId': safe_id(entry.get('turnId')), 'startedAtMs': None, 'completedAtMs': None})
            return dict(data=data, nextCursor=self.cursor(method, raw.get('nextCursor')),
                        backwardsCursor=self.cursor(method, raw.get('backwardsCursor')))
        if method == 'thread/goal/get':
            return {'goal': None}
        if method == 'turn/start':
            turn = self.turn(raw.get('turn'))
            if self.notice_turn is not None and self.notice_turn != turn['id']:
                raise Refused()
            self.active_turn = turn['id']
            return {'turn': turn}
        if method == 'turn/interrupt':
            return {}
        raise Refused()

    def backend(self, value):
        method, ident = value.get('method'), value.get('id')
        if method is None:
            if type(ident) is not int or ident not in self.pending:
                raise Refused()
            front, request = self.pending.pop(ident)
            if 'error' in value:
                # Keep the turn uncertain whenever its send was already durable.
                return {'id': front, 'error': {'code': -32000, 'message': '原生请求未确认。'}}
            return {'id': front, 'result': self.result(request, value.get('result'))}
        if ident is not None:
            # App-server tool/approval prompts can never open another channel.
            raise Refused()
        params = value.get('params')
        if not isinstance(params, dict) or params.get('threadId') != self.session:
            return None
        if method in ('turn/started', 'turn/completed') and self.effect:
            turn = self.turn(params.get('turn'))
            expected = self.active_turn or self.notice_turn
            if expected is not None and expected != turn['id'] or method == 'turn/completed' and expected is None:
                raise Refused()
            self.notice_turn = turn['id']
            if method == 'turn/completed':
                if turn['status'] not in ('completed', 'failed', 'interrupted'):
                    raise Refused()
                self.on_effect(False)
                self.effect = False
            return {'method': method, 'params': {'threadId': self.session, 'turn': turn}}
        if method in ('item/started', 'item/completed') and self.effect:
            turn_id = safe_id(params.get('turnId'))
            if turn_id != (self.active_turn or self.notice_turn):
                return None
            item = self.item(params.get('item'))
            if item is None:
                return None
            return {'method': method, 'params': {'threadId': self.session, 'turnId': turn_id, 'item': item}}
        return None


class UnixGateway:
    def __init__(self, path):
        self.path = path
        self.listener = socket.socket(socket.AF_UNIX)
        self.client = None
        try:
            self.listener.bind(str(path))
            path.chmod(0o600)
            self.listener.listen(1)
            self.listener.settimeout(.25)
        except BaseException:
            self.listener.close()
            raise

    def close(self):
        for channel in (self.client, self.listener):
            if channel is not None:
                channel.close()

    def open(self, owner, stopped):
        deadline = time.monotonic() + 12
        while True:
            if stopped() or time.monotonic() > deadline:
                raise Refused()
            try:
                client, _ = self.listener.accept()
                break
            except TimeoutError:
                continue
        self.client = client
        self.listener.close()
        peer, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        identity = process_identity(peer)
        if (uid != os.getuid() or not identity or identity['sid'] != owner['pid'] or
                identity['pgid'] != owner['pid'] or identity['host'] != owner['host']
                or identity['boot_id'] != owner['boot_id'] or int(identity['start']) < int(owner['start'])):
            raise Refused()
        client.settimeout(3)
        raw = bytearray()
        while not raw.endswith(b'\r\n\r\n'):
            if len(raw) >= 8192 or not (data := client.recv(1)):
                raise Refused()
            raw.extend(data)
        start, *lines = bytes(raw[:-4]).split(b'\r\n')
        headers = {}
        for line in lines:
            name, separator, item = line.partition(b':')
            name = name.lower()
            if not separator or name in headers:
                raise Refused()
            headers[name] = item.strip()
        key = headers.get(b'sec-websocket-key', b'')
        if (start != b'GET /rpc HTTP/1.1' or headers.get(b'host') != b'localhost'
                or headers.get(b'upgrade', b'').lower() != b'websocket'
                or b'upgrade' not in headers.get(b'connection', b'').lower()
                or headers.get(b'sec-websocket-version') != b'13' or len(base64.b64decode(key, validate=True)) != 16):
            raise Refused()
        accept = base64.b64encode(hashlib.sha1(key + GUID, usedforsecurity=False).digest())
        client.sendall(b'HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n'
                       b'Sec-WebSocket-Accept: ' + accept + b'\r\n\r\n')

    def exact(self, count):
        out = bytearray()
        deadline = time.monotonic() + 3
        while len(out) < count:
            if time.monotonic() > deadline or not (data := self.client.recv(count - len(out))):
                raise EOFError()
            out.extend(data)
        return bytes(out)

    def frame(self):
        flags, size = self.exact(2)
        if flags not in (0x81, 0x88, 0x89, 0x8a) or not size & 128:
            raise Refused()
        size &= 127
        if size >= 126:
            size = int.from_bytes(self.exact(2 if size == 126 else 8), 'big')
        if size > FRAME_LIMIT or flags != 0x81 and size > 125:
            raise Refused()
        mask, payload = self.exact(4), self.exact(size)
        return flags, bytes(value ^ mask[i % 4] for i, value in enumerate(payload))

    def send(self, payload, flags=0x81):
        size = len(payload)
        if size > FRAME_LIMIT:
            raise Refused()
        prefix = bytes((flags, size)) if size < 126 else bytes((flags, 126 if size < 65536 else 127)) + size.to_bytes(2 if size < 65536 else 8, 'big')
        self.client.sendall(prefix + payload)

    def serve(self, backend, protocol, stopped):
        pending = bytearray()
        total = 0
        while not stopped():
            readable, _, _ = select.select((self.client, backend.stdout), (), (), .2)
            if self.client in readable:
                kind, raw = self.frame()
                total += len(raw)
                if kind == 0x88:
                    return
                if kind == 0x89:
                    self.send(raw, 0x8a)
                elif kind == 0x81:
                    request = decoded(raw)
                    try:
                        request = protocol.input(request)
                    except Refused:
                        if request_id(request.get('id')):
                            self.send(canonical({'id': request['id'], 'error': {'code': -32600, 'message': 'DevFlower terminal scope denied'}}))
                        continue
                    if 'result' in request:
                        self.send(canonical(request))
                    else:
                        backend.stdin.write(canonical(request) + b'\n')
                        backend.stdin.flush()
            if backend.stdout in readable:
                raw = os.read(backend.stdout.fileno(), 65536)
                if not raw:
                    raise EOFError()
                total += len(raw)
                pending.extend(raw)
                if len(pending) > FRAME_LIMIT:
                    raise Refused()
                while b'\n' in pending:
                    line, _, rest = pending.partition(b'\n')
                    pending[:] = rest
                    value = protocol.backend(decoded(line))
                    if value is not None:
                        self.send(canonical(value))
            if total > WIRE_LIMIT:
                raise Blocked('原生终端协议超出预算。')
