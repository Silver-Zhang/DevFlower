"""Bounded, profile-scoped model discovery. A catalog is not an entitlement."""
from pathlib import Path
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from orchestration.common import Blocked, digest
from .graph import Invalid
from .agent_sessions import safe_text
from .session_sources import opened
import os

TTL = 300


def selectable_efforts(rt, name, profile, model):
    """CLI grammar is a selectable request, never proof of model entitlement."""
    native=list(model.get('efforts') or [])
    if native: return native, model.get('effort_source','provider')
    if profile['provider']=='claude':
        try: cli=rt.flow.get('cli-probe',name)
        except Invalid: cli={}
        if (cli.get('available') and 0<=time.time()-cli.get('at',0)<TTL and
            cli.get('executable_sha256')==digest(Path(profile['executable']).resolve().read_bytes()) and
            isinstance(cli.get('efforts'),list) and cli['efforts'] and
            all(isinstance(v,str) and v for v in cli['efforts'])):
            return list(dict.fromkeys(cli['efforts'])), 'installed CLI --help; model support requires execution check'
    return [profile['effort']], 'current profile preference; model support unknown'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked('模型目录重定向被拒绝；请核对已批准的服务地址')


def configured_env(profile):
    """Read only operator-approved overlays, never accept a browser endpoint/key."""
    env = {}
    for source, target in profile.get('readonly_files', []):
        if str(target).endswith('/.claude/settings.json') or str(target).endswith('.claude/settings.json'):
            with opened(source) as fd:
                value = json.loads(os.read(fd, 200001))
            env.update(value.get('env', {}))
    return env


def proxy_models(profile):
    env = configured_env(profile)
    base = env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/')
    key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')
    if not key:
        raise Blocked('未配置此 Claude Profile 的 API 认证；请通过本机认证入口配置后刷新')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Blocked('模型发现仅访问已批准的 HTTPS 服务地址')
    url = base + ('/models' if base.endswith('/v1') else '/v1/models')
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer '+key, 'x-api-key': key,
                                              'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as response:
            raw = response.read(1000001)
        if len(raw) > 1000000: raise Invalid('代理模型目录超过读取预算')
        value = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise Blocked(f'代理模型接口 HTTP {exc.code}；请检查认证或让中转站提供 /v1/models。预登记配置不代表完整目录') from None
    except (urllib.error.URLError, TimeoutError):
        raise Blocked('代理模型接口超时或网络不可用；可刷新或明确使用上次有效目录') from None
    if not isinstance(value, dict):
        raise Invalid('代理模型接口返回了无效响应格式；请检查 /v1/models，旧目录仅作过期缓存展示')
    if value.get('has_more'):raise Invalid('代理模型目录需要分页；当前接口未返回完整目录，请检查代理支持')
    rows = value.get('data')
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        raise Invalid('代理返回空或无效模型目录；不能据此推断只有预登记模型')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not 1 <= len(row['id']) <= 200:
            raise Invalid('代理模型目录包含无效模型身份')
        efforts = row.get('supported_reasoning_efforts', [])
        if not isinstance(efforts, list) or any(not isinstance(e, str) for e in efforts): efforts = []
        result.append(dict(id=row['id'], name=row.get('display_name') or row['id'], efforts=efforts,
                           source='proxy /v1/models', effort_source='proxy' if efforts else 'unknown',
                           inference_verified=False))
    return result


def codex_models(rpc, profile, home, cwd):
    rows, cursor, seen = [], None, set()
    for _ in range(10):
        params = {'limit': 100}
        if cursor: params['cursor'] = cursor
        response = rpc(dict(profile, home=str(home)), str(cwd), 'model/list', params)
        page = response.get('data')
        if not isinstance(page, list): raise Invalid('Codex model/list 返回无效目录')
        for row in page:
            if not isinstance(row, dict) or not isinstance(row.get('model'), str): raise Invalid('Codex 模型身份无效')
            rows.append(dict(id=row['model'], name=row.get('displayName') or row['model'],
                efforts=[e['reasoningEffort'] for e in row.get('supportedReasoningEfforts', [])],
                source='Codex app-server model/list', effort_source='Codex model/list', inference_verified=False))
        cursor = response.get('nextCursor')
        if not cursor: break
        if not isinstance(cursor, str) or cursor in seen: raise Invalid('Codex 模型分页循环或身份无效')
        seen.add(cursor)
    else: raise Invalid('Codex 模型分页超过预算；未冒充完整目录')
    if not rows: raise Invalid('Codex 返回空模型目录；请检查认证并刷新')
    return rows


class ModelCatalog:
    def __init__(self, manager):
        self.manager, self.rt, self.db = manager, manager.rt, manager.db

    def get(self, name, profile, refresh=False, workspace=None):
        now = time.time()
        # Profile identity + configured credential sources. Do not share a cached
        # directory across two gateways using the same Provider name.
        key = name
        identity = digest(json.dumps(profile, sort_keys=True).encode())
        from .model_registration import profile_identity
        environment=profile_identity(profile)
        try: cached = self.db.get('model-discovery', key)
        except Invalid: cached = None
        if cached and (cached.get('profile_sha256') != identity or cached.get('environment_sha256') != environment): cached = None
        error = cached.get('error') if cached else None
        if refresh:
            try:
                if profile['provider'] == 'codex':
                    rows = codex_models(self.manager.rpc, profile, self.rt.rpc_home/name, self.rt.storage)
                elif profile['provider']=='copilot':
                    data=self.manager.rpc(dict(profile,home=str(self.rt.rpc_home/name)),str(self.rt.storage),'copilot/models',{})
                    rows=[dict(id=m['id'],name=m.get('name') or m['id'],efforts=[],source='Copilot RPC models.list',effort_source='unknown',inference_verified=False) for m in data['models']]
                    if not rows:raise Invalid('Copilot 返回空模型目录')
                else: rows = proxy_models(profile)
                cached = dict(id=key, profile_sha256=identity, environment_sha256=environment, models=rows, at=now, attempted_at=now,
                              source=rows[0]['source'], error=None)
                error = None
            except (Blocked, Invalid, OSError, ValueError, KeyError) as exc:
                error = safe_text(str(exc))
                cached = dict(cached or {}, id=key, profile_sha256=identity, environment_sha256=environment, attempted_at=now, error=error)
            self.db.put('model-discovery', key, cached)
            try:
                auth=self.manager.rpc(dict(profile,home=str(self.rt.rpc_home/name)),str(self.rt.storage),
                    'account/read' if profile['provider']=='codex' else 'copilot/auth-status' if profile['provider']=='copilot' else 'claude/auth-status',{})
                if profile['provider']=='codex':
                    auth=dict(logged_in=bool(auth.get('account')) or auth.get('requiresOpenaiAuth') is False,
                              auth_method=(auth.get('account') or {}).get('type','unknown'),service_verified=False)
                self.db.put('auth-probe',key,dict(id=key,at=now,**auth))
            except (Blocked,OSError,ValueError,KeyError):
                self.db.put('auth-probe',key,dict(id=key,at=now,logged_in=None,auth_method='unknown',service_verified=False))
        native_rows = cached.get('models') if cached else None
        rows = native_rows
        source = cached.get('source') if cached else None
        at = cached.get('at') if cached else None
        if rows is None:
            # Compatibility only, explicitly stale and never trusted as discovery.
            try:
                legacy = self.db.get('model-catalog', profile['provider'])
                rows, source = legacy['models'], '旧版缓存（来源尚未重新核验）'
            except Invalid:
                rows = self.rt.policy.get('models', {}).get(profile['provider'], [])
                source = '预登记配置（不是完整模型列表）'
        approved = {m['id']: m.get('efforts', []) for m in self.rt.policy.get('models', {}).get(profile['provider'], [])}
        approved.setdefault(profile['model'], [profile['effort']])
        if workspace:
            from .model_registration import ModelRegistration
            for model,r in ModelRegistration(self.rt).choices(workspace,name).items():approved[model]=[r['effort']]
        # Registration remains visible, without inventing a discovery row or
        # promoting a configured effort to native capability evidence.
        rows = list(rows)
        for model, efforts in approved.items():
            if not any(m['id'] == model for m in rows):
                rows.append(dict(id=model, name=model, efforts=efforts,
                                 source='explicit operator registration', effort_source='explicit operator registration'))
        fresh = at is not None and 0 <= now-at <= TTL and not error
        native_ids = {m['id'] for m in native_rows or []}
        models = []
        for m in rows:
            registered = m['id'] in approved
            native = m['id'] in native_ids
            choices,effort_source=selectable_efforts(self.rt,name,profile,m if native else {})
            models.append(dict(m, efforts=approved.get(m['id'], []),
                native_efforts=list(m.get('efforts', [])) if native else [],
                selectable_efforts=choices, selectable_effort_source=effort_source,
                operator_registered=registered, discovered=native, discovery_current=bool(native and fresh),
                execution_state='unverified' if registered else 'not-authorized',
                execution_allowed=False, verification_scope='select exact configuration to verify',
                inference_verified=False, physical_backend='unknown', resolved_effort='unknown',
                unavailable_reason='已登记，执行能力待验证' if registered else '已发现但未获执行授权'))
        try:auth=self.db.get('auth-probe',key)
        except Invalid:auth=dict(logged_in=None,auth_method='unknown',service_verified=False)
        return dict(models=models, authentication=auth, source=source, updated_at=at, attempted_at=cached.get('attempted_at') if cached else None,
                    stale=not fresh, error=error, ttl_seconds=TTL,
                    discovery_complete=bool(fresh), physical_backend='unknown')
