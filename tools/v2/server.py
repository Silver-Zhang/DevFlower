"""Loopback-only V2 drafts, metadata and explicit frozen execution boundary."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from devflower_export import ExportError
from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked
from .agent_records import AgentRecords
from .assistant import Assistant
from .codex_terminals import CodexTerminals, KEY as TERMINAL_ID
from .copilot_catalog import CopilotCatalog
from .documents import check_chain
from .git_platform import GitPlatform, parameters as git_platform_parameters
from .git_delivery import GitDelivery
from .history_archives import HistoryArchives, MAX_BYTES as HISTORY_ARCHIVE_BYTES
from .local_delivery import package as local_delivery_package, preview as local_delivery_preview
from .draft_transfer import DraftTransferError, read_request, validate_archive
from .native_session_activity import NativeSessionActivity, activity_parameters
from .native_session_pages import MAX_SEARCH_BODY, NativeSessionPages, page_parameters, search_parameters
from .native_sessions import NativeHistoryError, request_parameters
from .native_session_title import NativeSessionTitles
from .native_session_views import MAX_ACTION_BYTES, NativeSessionViews, WorkflowSessionTrash, action_parameters, list_parameters
from .project_files import read_project
from .session_identity import SessionRefused
from .session_lease import CODEX_COORDINATION_VERSION
from .script_runs import EXECUTION, ScriptRuns, compile_saved
from .native_history_config import unique_pairs
from .run_inspector import RunInspector, parameters as inspector_parameters
from .tool_artifacts import read as read_tool_artifact
from .workflow_templates import WorkflowTemplates
from .run_summary import RunSummaryError, read_run_summary, request_parameters as run_summary_parameters

STATIC = Path(__file__).parent / 'static'


def create_server(store, port=0, *, runtime=None, native_sessions=None, copilot_catalog=None, session_operations=None,
                  git_delivery_targets=()):
    token = secrets.token_urlsafe(32)
    catalog = CopilotCatalog() if copilot_catalog is None else copilot_catalog
    if native_sessions is not None and runtime is not None:
        native_sessions.workflow_links = bool(runtime.provider or 'claude' in getattr(session_operations, 'profiles', {})
                                              or store.db.list('node-session'))
    native_views = NativeSessionViews(native_sessions) if native_sessions is not None else None
    workflow_trash = (WorkflowSessionTrash(native_views) if native_views and native_sessions.workflow_links
                      and getattr(runtime.provider, 'version', None) == CODEX_COORDINATION_VERSION else None)
    native_pages = NativeSessionPages(native_sessions) if native_sessions is not None else None
    native_activity = NativeSessionActivity(native_sessions) if native_sessions is not None else None
    terminals = CodexTerminals(session_operations) if CodexTerminals.available(session_operations) else None
    titles = (NativeSessionTitles(session_operations) if session_operations
              and 'codex' in getattr(session_operations,'profiles',{})
              and getattr(session_operations.codex,'version',None)==CODEX_COORDINATION_VERSION else None)
    inspector = RunInspector(store)
    archives = HistoryArchives(store, inspector)
    templates = WorkflowTemplates(store)
    assistant = Assistant(store, session_operations, runtime)
    git_platform = GitPlatform(store,runtime)
    homes = getattr(getattr(getattr(runtime, 'cli_providers', None), 'homes', None), 'entries', {})
    git_delivery = GitDelivery(git_platform, git_delivery_targets,
                               native_homes=[row['home'] for row in homes.values()] if git_delivery_targets else ())
    agents = getattr(runtime, 'agents', None) or AgentRecords(store)
    agents.session_operations = session_operations
    scripts = ScriptRuns(store, agents)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # URLs can carry private workspace paths; no request-body logging.

        def reply(self, status, value, mime='application/json; charset=utf-8'):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self, post=False):
            path = urlsplit(self.path).path
            host = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != host or self.headers.get('Origin') not in (None, 'http://' + host):
                return self.reply(403, {'error': '请求来源不匹配'})
            if path.startswith('/api/'):
                if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                    return self.reply(401, {'error': '本机访问凭证已失效；请从启动入口重新打开'})
            try:
                if not path.startswith('/api/') and not post:
                    files = {'/': ('index.html', 'text/html; charset=utf-8'),
                             '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                             '/app.css': ('app.css', 'text/css; charset=utf-8')}
                    if path not in files:
                        return self.reply(404, {'error': '入口不存在'})
                    filename, mime = files[path]
                    return self.reply(200, (STATIC / filename).read_bytes(), mime)
                if path == '/api/v2/provider-catalog/copilot':
                    return self.reply(*catalog.request(self.path, self.headers, self.rfile, post=post))
                if path.startswith('/api/v2/native-pty/'):
                    if terminals is None:
                        return self.reply(404, {'error': '当前主机或操作配置尚未启用原生终端。'})
                    route = path.removeprefix('/api/v2/native-pty/')
                    query = urlsplit(self.path).query
                    if post:
                        lengths = self.headers.get_all('Content-Length', [])
                        size = int(lengths[0]) if len(lengths) == 1 else 0
                        if (query or not 0 < size <= 24576 or self.headers.get('Transfer-Encoding')
                                or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'):
                            raise Invalid('终端请求需要有界的 JSON 对象。')
                        raw = self.rfile.read(size)
                        if len(raw) != size:
                            raise Invalid('终端请求不完整。')
                        data = json.loads(raw, object_pairs_hook=unique_pairs)
                        if not isinstance(data, dict):
                            raise Invalid('终端请求需要 JSON 对象。')
                        return self.reply(200, terminals.action(route, data))
                    params = parse_qs(query, keep_blank_values=True, max_num_fields=3)
                    if any(len(values) != 1 for values in params.values()):
                        raise Invalid('终端查询身份不明确。')
                    if route=='run-target' and set(params)=={'workspace','run','activation'}:
                        return self.reply(200,terminals.run_target(params['workspace'][0],params['run'][0],params['activation'][0]))
                    if route == 'list' and set(params) == {'workspace'}:
                        return self.reply(200, terminals.listing(params['workspace'][0]))
                    if (route == 'output' and set(params) == {'workspace', 'id', 'after'}
                            and TERMINAL_ID.fullmatch(params['id'][0])):
                        return self.reply(200, terminals.snapshot(params['workspace'][0], params['id'][0], int(params['after'][0])))
                    raise Invalid('终端查询需要当前工作区和明确身份。')
                if path in ('/api/v2/session-send', '/api/v2/session-operation', '/api/v2/workflow-session-trash'):
                    group_trash = path == '/api/v2/workflow-session-trash'
                    if group_trash and workflow_trash is None or not group_trash and session_operations is None:
                        return self.reply(404, {'error': '本机尚未启用此原生会话操作。'})
                    if post and path != '/api/v2/session-operation':
                        lengths = self.headers.get_all('Content-Length', [])
                        size = int(lengths[0]) if len(lengths) == 1 else 0
                        if (not 0 < size <= (8192 if group_trash else 125000) or '?' in self.path or self.headers.get('Transfer-Encoding')
                                or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'):
                            raise Invalid('会话发送需要有界的 JSON 请求。')
                        raw = self.rfile.read(size)
                        if len(raw) != size:
                            raise Invalid('会话发送请求不完整。')
                        data = json.loads(raw, object_pairs_hook=unique_pairs)
                        if not isinstance(data, dict):
                            raise Invalid('会话发送需要 JSON 对象。')
                        return self.reply(200, workflow_trash.request(data) if group_trash else session_operations.send(data))
                    if not post and path == '/api/v2/session-operation':
                        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=2)
                        if set(params) != {'workspace', 'intent_key'} or any(len(value) != 1 for value in params.values()):
                            raise Invalid('会话操作查询需要明确的工作空间和发送意图。')
                        return self.reply(200, session_operations.operation(params['workspace'][0], params['intent_key'][0]))
                    return self.reply(405, {'error': '会话操作方法无效。'})
                if post and path == '/api/v2/drafts/validate':
                    workspace, raw = read_request(urlsplit(self.path).query, self.headers, self.rfile)
                    return self.reply(200, validate_archive(store, workspace, raw))
                if post and path == '/api/v2/history-archives/import':
                    workspace, raw = read_request(urlsplit(self.path).query, self.headers, self.rfile,
                                                  max_bytes=HISTORY_ARCHIVE_BYTES)
                    return self.reply(200, archives.import_file(workspace, raw))
                if not post and path == '/api/v2/history-archives/export':
                    args = inspector_parameters(urlsplit(self.path).query, self.headers, 'export')
                    return self.reply(200, archives.export(**args))
                if path.startswith('/api/v2/git-platform/'):
                    route = path.removeprefix('/api/v2/git-platform/')
                    query = urlsplit(self.path).query
                    if not post:
                        params = git_platform_parameters(route,query,self.headers)
                        return self.reply(200,git_platform.read(route,**params))
                    if route != 'review':
                        return self.reply(405,{'error':'此处仅能创建本机审核任务。远端发布需要独立交付 Gate。'})
                    lengths = self.headers.get_all('Content-Length',[])
                    size = int(lengths[0]) if len(lengths)==1 else 0
                    if (query or not 0<size<=4096 or self.headers.get_all('Transfer-Encoding',[])
                            or self.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
                        raise Invalid('审核任务需要有界的 JSON 对象。')
                    raw = self.rfile.read(size)
                    if len(raw)!=size:
                        raise Invalid('审核任务请求不完整。')
                    return self.reply(200,git_platform.review(json.loads(raw,object_pairs_hook=unique_pairs)))
                if path.startswith('/api/v2/assistant/'):
                    route = path.rsplit('/', 1)[1]
                    if path != '/api/v2/assistant/' + route:
                        raise Invalid('助手操作路径无效。')
                    query = urlsplit(self.path).query
                    lengths = self.headers.get_all('Content-Length', [])
                    if self.headers.get_all('Transfer-Encoding', []):
                        raise Invalid('助手请求需要有界正文。')
                    if post and route in ('preview', 'send', 'apply', 'configure', 'refresh_models', 'sources', 'source_excerpt',
                                              'team_preview', 'team_confirm', 'preview_repair', 'launch_repair'):
                        size = int(lengths[0]) if len(lengths) == 1 else 0
                        if (query or not 0 < size <= 440000 or
                                self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
                            raise Invalid('助手操作需要明确的 JSON 对象。')
                        raw = self.rfile.read(size)
                        if len(raw) != size:
                            raise Invalid('助手请求不完整。')
                        data = json.loads(raw, object_pairs_hook=unique_pairs)
                        return self.reply(200, getattr(assistant, route)(data))
                    if not post and route in ('history', 'conversation', 'diagnosis', 'settings', 'repair_status', 'repair_intent'):
                        if len(query)>1024 or lengths not in ([], ['0']):
                            raise Invalid('助手查询身份无效。')
                        values = parse_qs(query, keep_blank_values=True, strict_parsing=True,
                                          max_num_fields=3 if route in ('repair_status','repair_intent') else 2)
                        required = {'workspace', 'conversation'} if route == 'conversation' else (
                            {'workspace', 'run'} if route in ('diagnosis','repair_status','repair_intent') else {'workspace'})
                        if route=='repair_intent':
                            required.add('intent')
                        extras = {'history':{'before'},'repair_status':{'verification'}}
                        allowed = required | extras.get(route,set())
                        if not required <= values.keys() <= allowed or any(len(v)!=1 for v in values.values()):
                            raise Invalid('助手查询需要明确的工作区与对象。')
                        return self.reply(200, getattr(assistant, route)(**{key:value[0] for key,value in values.items()}))
                    return self.reply(405, {'error': '助手操作方法无效。'})
                if path in ('/api/v2/run-inspector/events', '/api/v2/run-inspector/payload',
                            '/api/v2/run-inspector/export'):
                    if post:
                        return self.reply(405, {'error': '运行检查只接受读取请求。'})
                    route = path.rsplit('/', 1)[1]
                    args = inspector_parameters(urlsplit(self.path).query, self.headers, route)
                    return self.reply(200, getattr(inspector, route)(**args))
                if not post and path == '/api/v2/run-summary':
                    workspace, run = run_summary_parameters(urlsplit(self.path).query, self.headers)
                    return self.reply(200, read_run_summary(store, workspace, run))
                if not post and path == '/api/v2/native-session-activity':
                    params = activity_parameters(urlsplit(self.path).query)
                    if native_activity is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    return self.reply(200, native_activity.read(**params))
                if not post and path == '/api/v2/native-session-page':
                    request = page_parameters(urlsplit(self.path).query)
                    if native_pages is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    return self.reply(200, native_pages.read(request))
                if post and path == '/api/v2/native-session-search':
                    try:
                        lengths = self.headers.get_all('Content-Length', [])
                        size = int(lengths[0]) if len(lengths) == 1 else 0
                    except ValueError:
                        size = 0
                    if (not 0 < size <= MAX_SEARCH_BODY or '?' in self.path or self.headers.get('Transfer-Encoding')
                            or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json'):
                        raise NativeHistoryError('NATIVE_SEARCH_INVALID')
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise NativeHistoryError('NATIVE_SEARCH_INVALID')
                    request = search_parameters(raw)
                    if native_pages is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    return self.reply(200, native_pages.read(request))
                if post and path == '/api/v2/native-session-view':
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        if (not 0 < size <= MAX_ACTION_BYTES or urlsplit(self.path).query
                                or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                            raise ValueError()
                        action = action_parameters(self.rfile.read(size))
                    except ValueError:
                        raise NativeHistoryError('NATIVE_VIEW_INVALID') from None
                    if native_views is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    return self.reply(200, native_views.change(action))
                if not post and path == '/api/v2/native-session-views':
                    try:
                        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True,
                                          errors='strict', max_num_fields=2)
                        workspace, revision = list_parameters(params)
                    except ValueError:
                        raise NativeHistoryError('NATIVE_VIEW_INVALID') from None
                    if native_views is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    return self.reply(200, native_views.listing(workspace, revision))
                data = {}
                if post:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 500000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise Invalid('请求需要有界的 JSON 对象')
                    data = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(data, dict):
                        raise Invalid('请求需要 JSON 对象')
                query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
                if not post and path == '/api/v2/status':
                    result = {'stage': 'M2' if runtime else 'M1', 'channel': 'Development',
                              'execution_supported': True, 'provider_available': bool(runtime and runtime.provider),
                              'execution_available': None if runtime and runtime.provider else False,
                              'native_history_available': native_sessions is not None,
                              'native_session_writes': session_operations.status() if session_operations else [],
                              'native_codex_terminal': terminals is not None, 'native_codex_title': titles is not None,
                              'native_workflow_links': bool(native_sessions and native_sessions.workflow_links),
                              'native_workflow_trash': workflow_trash is not None,
                              'execution_check': 'native capability and permissions are checked on explicit launch',
                              'workspaces': [row for row in store.db.list('workspace')],
                              'roots': list(map(str, store.roots)), 'environment': store.environment,
                              'provider': runtime.catalog() if runtime else None}
                elif not post and path == '/api/v2/folders':
                    result = store.browse(query.get('path'))
                elif post and path == '/api/v2/workspaces/open' and set(data) in ({'path'}, {'path', 'expected_workspace'}):
                    if 'expected_workspace' in data:
                        result = store.workspace(data['expected_workspace'])
                        if result['path'] != data['path']:
                            raise Conflict('最近工作空间与登记文件夹不一致；请重新选择')
                    else:
                        result = store.open_workspace(data['path'])
                elif not post and path in ('/api/v2/project-files', '/api/v2/project-file', '/api/v2/task-file'):
                    params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, errors='strict')
                    if 'workspace' not in params or set(params) - {'workspace', 'path'} or any(
                            len(values) != 1 for values in params.values()):
                        raise Invalid('读取项目文件需要工作空间身份和可选相对路径')
                    workspace, relative = params['workspace'][0], params.get('path',[''])[0]
                    if path=='/api/v2/task-file':
                        from .data_handoff import task_file
                        result = task_file(store,workspace,relative)
                    else:
                        result = read_project(store,workspace,relative,preview=path=='/api/v2/project-file')
                elif not post and path in ('/api/v2/native-sessions', '/api/v2/native-session'):
                    transcript = path == '/api/v2/native-session'
                    try:
                        params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, errors='strict',
                                          max_num_fields=3)
                    except ValueError:
                        raise NativeHistoryError('NATIVE_QUERY_INVALID') from None
                    params = request_parameters(params, transcript=transcript)
                    if native_sessions is None:
                        raise NativeHistoryError('NATIVE_HISTORY_DISABLED')
                    result = native_sessions.read(**params) if transcript else native_sessions.listing(**params)
                    if not transcript and session_operations is not None:
                        session_operations.add_continue_eligibility(result)
                elif not post and path == '/api/v2/drafts':
                    result = store.drafts(query.get('workspace'))
                elif not post and path == '/api/v2/draft':
                    result = store.draft(query.get('workspace'), query.get('id'))
                elif post and path == '/api/v2/drafts/save' and set(data) == {'workspace', 'id', 'expected', 'document'}:
                    result = store.save(data['workspace'], data['id'], data['expected'], data['document'])
                elif post and path == '/api/v2/drafts/export-python' and not query and set(data) == {
                        'workspace', 'id', 'revision', 'native_config'}:
                    _, _, _, artifact = compile_saved(store, data['workspace'], data['id'], data['revision'],
                                                       native_config=data['native_config'])
                    result = dict(workspace=data['workspace'], id=data['id'], revision=data['revision'], **artifact)
                elif not post and path == '/api/v2/workflow-templates' and set(query) == {'workspace'}:
                    result = templates.listing(query['workspace'])
                elif not post and path == '/api/v2/workflow-template' and set(query) == {'workspace', 'id', 'revision'}:
                    result = templates.get(query['workspace'], query['id'], query['revision'])
                elif post and path == '/api/v2/workflow-templates/save' and not query:
                    result = templates.save(data)
                elif post and path == '/api/v2/workflow-templates/manage' and not query:
                    result = templates.manage(data)
                elif not post and path == '/api/v2/history-archives' and set(query) == {'workspace'}:
                    result = archives.listing(query['workspace'])
                elif not post and path == '/api/v2/history-archive' and set(query) == {'workspace', 'id'}:
                    result = archives.read(query['workspace'], query['id'])
                elif not post and path == '/api/v2/tool-artifact' and set(query) == {'workspace', 'run', 'activation'}:
                    result = read_tool_artifact(store, **query)
                elif post and path == '/api/v2/check' and set(data) == {'workspace', 'document', 'execution'}:
                    if data['execution'] != EXECUTION:
                        raise Invalid('执行方式无效。')
                    result = scripts.check(data['workspace'], data['document'])
                elif post and path == '/api/v2/check' and set(data) == {'workspace', 'document'}:
                    store.workspace(data['workspace'])
                    result = runtime.inspect(data['workspace'],data['document']) if runtime else check_chain(data['document'])
                    if not runtime:
                        result['issues'].extend(agents.reference_issues(data['workspace'], data['document']['nodes']))
                elif post and path == '/api/v2/providers/discover' and data == {} and runtime:
                    result = runtime.catalog(refresh=True)
                elif not post and path == '/api/v2/cli-providers' and not query and runtime:
                    result = runtime.cli_providers.listing()
                elif post and path == '/api/v2/cli-providers/discover' and not query and set(data) in (
                        {'provider'}, {'provider', 'native_profile'}) and runtime:
                    result = runtime.cli_providers.discover(data['provider'],data.get('native_profile'))
                elif not post and path == '/api/v2/agents' and set(query) == {'workspace'}:
                    result = agents.list(query['workspace'])
                elif post and path == '/api/v2/agents/save' and not query and set(data) == {
                        'workspace', 'id', 'expected', 'config', 'session_revision'}:
                    result = agents.save(data['workspace'], data['id'], data['expected'], data['config'], data['session_revision'])
                elif post and path == '/api/v2/agents/remove' and not query and set(data) == {'workspace', 'id', 'expected'}:
                    result = agents.remove(data['workspace'], data['id'], data['expected'])
                elif post and path == '/api/v2/agents/launch' and runtime and not query and set(data) == {
                        'workspace', 'id', 'revision', 'intent_key', 'task'}:
                    result = runtime.launch_agent(data['workspace'], data['id'], data['revision'], data['intent_key'], data['task'])
                elif post and path == '/api/v2/agents/plan-transition' and runtime and not query and set(data) == {
                        'workspace', 'run', 'node', 'intent_key', 'authorization'}:
                    result = runtime.launch_plan_agent(data['workspace'], data['run'], data['node'],
                                                       data['intent_key'], data['authorization'])
                elif not post and path == '/api/v2/agent-skills' and set(query) == {'workspace', 'provider', 'cwd'}:
                    result = agents.skills(query['workspace'], query['provider'], query['cwd'])
                elif not post and path=='/api/v2/agent-sessions' and {'workspace','provider','cwd'}<=set(query)<={
                        'workspace','provider','cwd','native_profile'}:
                    result = agents.sessions(query['workspace'],query['provider'],query['cwd'],query.get('native_profile'))
                elif path=='/api/v2/session-title' and titles:
                    if post and not query:
                        result = titles.rename(data)
                    elif not post and set(query)=={'workspace','handle','revision'}:
                        result = titles.read(query['workspace'],query['handle'],query['revision'])
                    else:
                        raise Invalid('原生标题需要明确的会话身份。')
                elif not post and path=='/api/v2/session-title-operation' and titles and set(query)=={'workspace','intent_key'}:
                    result = titles.operation(query['workspace'],query['intent_key'],verify=True)
                elif not post and path == '/api/v2/runs':
                    workspace = query.get('workspace')
                    result = scripts.list(workspace)
                    if runtime:
                        result.extend(inspector.clean(runtime.runs(workspace)))
                elif not post and path in ('/api/v2/script-run/evidence', '/api/v2/script-run/source'):
                    required = {'workspace', 'id'}
                    extra = {'node'} if path.endswith('/evidence') else set()
                    if not required <= query.keys() <= required | extra:
                        raise Invalid('读取脚本证据需要明确的工作空间和运行身份。')
                    record = scripts.get(query['workspace'], query['id'])
                    if record is None:
                        raise Invalid('找不到此脚本运行。')
                    if path.endswith('/evidence'):
                        result = scripts.evidence(record, query.get('node'))
                        if stderr := result.get('stderr'):
                            result['stderr'] = dict(stderr, text=inspector.text(stderr['text']))
                    else:
                        result = scripts.source(record)
                elif not post and path == '/api/v2/local-delivery/preview' and runtime and set(query) == {'workspace', 'run'}:
                    result = local_delivery_preview(git_platform, **query)
                    result = dict(result, git_targets=sorted(git_delivery.targets),
                                  git_ref='refs/heads/work/devflower-' + query['run'])
                elif post and path == '/api/v2/local-delivery' and runtime and not query:
                    return self.reply(200, local_delivery_package(git_platform, data), 'application/zip')
                elif post and path == '/api/v2/local-delivery/git' and runtime and not query:
                    result = git_delivery.publish(data)
                elif not post and path == '/api/v2/local-delivery/git-operation' and set(query) == {'workspace', 'intent_key'}:
                    result = git_delivery.read(**query)
                elif not post and path == '/api/v2/run':
                    workspace, run_id = query.get('workspace'), query.get('id')
                    record = scripts.get(workspace, run_id)
                    if record is not None:
                        result = scripts.snapshot(workspace, run_id, record=record)
                    elif runtime:
                        result = runtime.snapshot(workspace, run_id)
                    else:
                        raise Invalid('找不到此运行。')
                elif post and path == '/api/v2/launch' and 'execution' in data:
                    required = {'workspace', 'draft_id', 'revision', 'intent_key', 'task', 'execution'}
                    if (not required <= set(data) <= required | {'task_files'} or data['execution'] != EXECUTION):
                        raise Invalid('脚本启动需要明确的工作空间、草稿版本、任务和原生执行意图。')
                    result = scripts.launch(**{key: value for key, value in data.items() if key != 'execution'})
                elif post and path == '/api/v2/launch' and runtime:
                    required = {'workspace','draft_id','revision','intent_key','task'}
                    if not required <= set(data) <= required | {'task_files'}:
                        raise Invalid('启动需要明确的工作空间、草稿版本、本次任务与启动意图')
                    options = {}
                    if 'task_files' in data:
                        from .data_handoff import task_selection
                        options['task_files'] = task_selection(data['task_files'])
                    result = runtime.launch(data['workspace'],data['draft_id'],data['revision'],data['intent_key'],data['task'],
                                            **options)
                elif post and path == '/api/v2/resume' and runtime and set(data) == {'workspace', 'run_id'}:
                    result = runtime.resume(data['workspace'], data['run_id'])
                elif post and path == '/api/v2/approval' and runtime and set(data) == {
                        'workspace', 'run_id', 'activation', 'decision'}:
                    result = runtime.control_designer(data['workspace'], data['run_id'],
                                                      activation=data['activation'], decision=data['decision'])
                elif post and path == '/api/v2/pause' and runtime and set(data) == {'workspace', 'run_id', 'paused'}:
                    result = runtime.control_designer(data['workspace'], data['run_id'], paused=data['paused'])
                elif post and path == '/api/v2/cancel' and set(data) == {'workspace', 'run_id'}:
                    workspace, run_id = data['workspace'], data['run_id']
                    record = scripts.get(workspace, run_id)
                    if record is not None:
                        result = scripts.snapshot(workspace, run_id, record=record, cancel=True)
                    elif runtime:
                        result = runtime.cancel(workspace, run_id)
                    else:
                        raise Invalid('找不到此运行。')
                else:
                    return self.reply(404, {'error': '此 V2 阶段没有该操作；未启动任务'})
                if isinstance(result, dict) and {'run', 'definition', 'nodes', 'events'} <= result.keys():
                    result = inspector.snapshot(result)
                self.reply(200, result)
            except (NativeHistoryError, DraftTransferError, RunSummaryError) as exc:
                self.reply(exc.status, {'code': exc.code, 'error': str(exc)})
            except SessionRefused as exc:
                self.reply(409, {'code': exc.code, 'error': str(exc)})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except Blocked as exc:
                self.reply(409, {'error':str(exc)})
            except ExportError as exc:
                self.reply(400, {'error': str(exc)})
            except (Invalid, ValueError, TypeError, KeyError, RecursionError):
                # Domain errors are written for the user; parser exceptions are not.
                import sys
                exc = sys.exception()
                self.reply(400, {'error': str(exc) if isinstance(exc, Invalid) else '请求格式无效，请刷新并重试'})
            except (BrokenPipeError, ConnectionResetError):
                return  # A closed browser request does not control the workflow.
            except (OSError, sqlite3.OperationalError):
                self.reply(503, {'error': '本机读写失败，当前草稿仍保留在编辑器；请核对目录与权限后重试'})

        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch(True)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            try:
                if terminals is not None:
                    terminals.shutdown()
            finally:
                super().server_close()

    server = Server(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, token
