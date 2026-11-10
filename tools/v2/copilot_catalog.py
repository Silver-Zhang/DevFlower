"""Opt-in, in-memory Copilot metadata service independent of execution."""
from dataclasses import dataclass
import json
import os
from threading import Lock
import time

from orchestration.common import Blocked

from .copilot_profile import IdentityChanged, PROFILE_ID, load_profile
from .copilot_transport import CatalogFault, refresh_metadata
from .native_history_config import unique_pairs


TTL = 300
ERRORS = {
    'COPILOT_NOT_CONFIGURED': (200, '尚未配置 GitHub Copilot 模型目录'),
    'COPILOT_NOT_DISCOVERED': (200, '尚未查询模型目录，请手动刷新'),
    'COPILOT_STALE': (200, '模型目录已过期，请手动刷新'),
    'COPILOT_INVALID_REQUEST': (400, '刷新请求无效，请重新打开模型目录'),
    'COPILOT_PROFILE_UNKNOWN': (404, '此 Copilot 配置当前不可用，请核对启动配置'),
    'COPILOT_BUSY': (409, '目录正在刷新，请稍后再试'),
    'COPILOT_IDENTITY_CHANGED': (409, 'Copilot 配置身份已变化，请核对后重启'),
    'COPILOT_AUTH_REQUIRED': (409, '未核验 Copilot 账户，请核对专用登录配置'),
    'COPILOT_ISOLATION_UNAVAILABLE': (503, '本机隔离不可用，目录未查询'),
    'COPILOT_PROTOCOL_ERROR': (503, 'Copilot 目录接口未通过核验'),
    'COPILOT_DIRECTORY_INVALID': (503, 'Copilot 返回空或无效模型目录'),
    'COPILOT_TIMEOUT': (503, 'Copilot 模型目录查询超时，可手动重试'),
    'COPILOT_UNAVAILABLE': (503, 'Copilot 模型目录暂不可用，请核对配置'),
}


@dataclass(frozen=True)
class Observation:
    rows: tuple
    unix: float
    monotonic: float


class CopilotCatalog:
    def __init__(self, profile=None, *, configured=False, transport=refresh_metadata):
        self.profile = profile
        self.configured = configured or profile is not None
        self.transport = transport
        self._lock = Lock()
        self._refreshing = Lock()
        self._changed = False
        self._observation = None
        self._error = None

    @classmethod
    def from_path(cls, path, store, *, native_sessions=None, transport=refresh_metadata):
        if path is None:
            return cls(transport=transport)
        try:
            profile = load_profile(os.fspath(path), store, native_sessions)
        except (IdentityChanged, TypeError):
            return cls(configured=True, transport=transport)
        return cls(profile, transport=transport)

    def _current(self):
        if not self._changed:
            try:
                self.profile.verify()
            except (ValueError, OSError, Blocked):
                self._changed = True
                self._observation = None
                self._error = None
        return not self._changed

    def _view(self, code=None, auth='unknown', *, reveal=True):
        observation = self._observation if reveal else None
        return dict(
            provider='copilot', profile_id=self.profile.profile_id if reveal and self.profile else None,
            source='copilot-cli-models-list' if observation else None,
            availability='available' if code is None else
                'unconfigured' if code == 'COPILOT_NOT_CONFIGURED' else 'unavailable',
            auth_status='authenticated' if code is None else auth,
            models=[dict(row) for row in observation.rows] if observation else [],
            observed_at=observation.unix if observation else None,
            fresh=code is None, stale=code is not None and observation is not None,
            effort_source='unknown', execution_available=False, inference_verified=False,
            code=code, error=ERRORS[code][1] if code else None,
        )

    def get(self):
        with self._lock:
            if self.profile is None:
                return self._view('COPILOT_UNAVAILABLE' if self.configured else 'COPILOT_NOT_CONFIGURED')
            if not self._current():
                return self._view('COPILOT_IDENTITY_CHANGED')
            if self._error:
                return self._view(*self._error)
            if self._observation is None:
                return self._view('COPILOT_NOT_DISCOVERED')
            if time.monotonic() - self._observation.monotonic >= TTL:
                return self._view('COPILOT_STALE')
            return self._view()

    def refresh(self, profile_id):
        with self._lock:
            if self.profile is None or self.profile.profile_id != profile_id:
                return 404, self._view('COPILOT_PROFILE_UNKNOWN', reveal=False)
            if not self._current():
                return 409, self._view('COPILOT_IDENTITY_CHANGED')
            if not self._refreshing.acquire(blocking=False):
                return 409, self._view('COPILOT_BUSY')
        try:
            error = None
            try:
                models = self.transport(self.profile)
            except CatalogFault as exc:
                error = (exc.code, exc.auth_status)
            with self._lock:
                if not self._current() or (error and error[0] == 'COPILOT_IDENTITY_CHANGED'):
                    self._changed = True
                    self._observation = None
                    self._error = None
                    return 409, self._view('COPILOT_IDENTITY_CHANGED')
                if error:
                    self._error = error
                    if error[0] == 'COPILOT_AUTH_REQUIRED':
                        self._observation = None
                    return ERRORS[error[0]][0], self._view(*error)
                self._observation = Observation(tuple(models), time.time(), time.monotonic())
                self._error = None
                return 200, self._view()
        finally:
            self._refreshing.release()

    def request(self, target, headers, stream, *, post):
        try:
            lengths = headers.get_all('Content-Length', [])
            if '?' in target or headers.get_all('Transfer-Encoding', []) or headers.get_all('Content-Encoding', []):
                raise ValueError()
            if not post:
                if lengths not in ([], ['0']):
                    raise ValueError()
                return 200, self.get()
            types = headers.get_all('Content-Type', [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or len(types) != 1 or types[0].split(';', 1)[0].strip().lower() != 'application/json'):
                raise ValueError()
            length = int(lengths[0])
            if not 1 <= length <= 256:
                raise ValueError()
            raw = stream.read(length)
            if len(raw) != length:
                raise ValueError()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
            if (not isinstance(value, dict) or set(value) != {'profile_id'}
                    or not isinstance(value['profile_id'], str) or not PROFILE_ID.fullmatch(value['profile_id'])):
                raise ValueError()
        except (ValueError, OSError, RecursionError):
            with self._lock:
                result = self._view('COPILOT_INVALID_REQUEST', reveal=False)
            return 400, result
        return self.refresh(value['profile_id'])
