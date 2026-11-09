"""Loopback-only V2 drafts, metadata and explicit frozen execution boundary."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from studio.graph import Invalid
from studio.server import Conflict
from orchestration.common import Blocked
from .documents import check_chain
from .native_session_pages import MAX_SEARCH_BODY, NativeSessionPages, page_parameters, search_parameters
from .native_sessions import NativeHistoryError, request_parameters
from .native_session_views import MAX_ACTION_BYTES, NativeSessionViews, action_parameters, list_parameters
from .project_files import read_project

STATIC = Path(__file__).parent / 'static'


def create_server(store, port=0, *, runtime=None, native_sessions=None):
    token = secrets.token_urlsafe(32)
    native_views = NativeSessionViews(native_sessions) if native_sessions is not None else None
    native_pages = NativeSessionPages(native_sessions) if native_sessions is not None else None

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
                              'execution_supported': bool(runtime), 'provider_available': bool(runtime and runtime.provider),
                              'execution_available': None if runtime and runtime.provider else False,
                              'native_history_available': native_sessions is not None,
                              'execution_check': 'native capability and permissions are checked on explicit launch',
                              'workspaces': [row for row in store.db.list('workspace')],
                              'roots': list(map(str, store.roots)), 'environment': store.environment,
                              'provider': runtime.catalog() if runtime else None}
                elif not post and path == '/api/v2/folders':
                    result = store.browse(query.get('path'))
                elif post and path == '/api/v2/workspaces/open' and set(data) == {'path'}:
                    result = store.open_workspace(data['path'])
                elif not post and path in ('/api/v2/project-files', '/api/v2/project-file'):
                    params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, errors='strict')
                    if 'workspace' not in params or set(params) - {'workspace', 'path'} or any(
                            len(values) != 1 for values in params.values()):
                        raise Invalid('读取项目文件需要工作空间身份和可选相对路径')
                    result = read_project(store, params['workspace'][0], params.get('path', [''])[0],
                                          preview=path == '/api/v2/project-file')
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
                elif not post and path == '/api/v2/drafts':
                    result = store.drafts(query.get('workspace'))
                elif not post and path == '/api/v2/draft':
                    result = store.draft(query.get('workspace'), query.get('id'))
                elif post and path == '/api/v2/drafts/save' and set(data) == {'workspace', 'id', 'expected', 'document'}:
                    result = store.save(data['workspace'], data['id'], data['expected'], data['document'])
                elif post and path == '/api/v2/check' and set(data) == {'workspace', 'document'}:
                    store.workspace(data['workspace'])
                    result = runtime.inspect(data['workspace'],data['document']) if runtime else check_chain(data['document'])
                elif post and path == '/api/v2/providers/discover' and data == {} and runtime:
                    result = runtime.catalog(refresh=True)
                elif not post and path == '/api/v2/runs' and runtime:
                    result = runtime.runs(query.get('workspace'))
                elif not post and path == '/api/v2/run' and runtime:
                    result = runtime.snapshot(query.get('workspace'),query.get('id'))
                elif post and path == '/api/v2/launch' and runtime:
                    if set(data) != {'workspace','draft_id','revision','intent_key','task'}:
                        raise Invalid('启动需要明确的工作空间、草稿版本、本次任务与启动意图')
                    result = runtime.launch(data['workspace'],data['draft_id'],data['revision'],data['intent_key'],data['task'])
                elif post and path == '/api/v2/cancel' and runtime and set(data)=={'workspace','run_id'}:
                    result=runtime.cancel(data['workspace'],data['run_id'])
                else:
                    return self.reply(404, {'error': '此 V2 阶段没有该操作；未启动任务'})
                self.reply(200, result)
            except NativeHistoryError as exc:
                self.reply(exc.status, {'code': exc.code, 'error': str(exc)})
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
            except Blocked as exc:
                self.reply(409, {'error':str(exc)})
            except (Invalid, ValueError, TypeError, KeyError, RecursionError):
                # Domain errors are written for the user; parser exceptions are not.
                import sys
                exc = sys.exception()
                self.reply(400, {'error': str(exc) if isinstance(exc, Invalid) else '请求格式无效，请刷新并重试'})
            except (OSError, sqlite3.OperationalError):
                self.reply(503, {'error': '本机读写失败，当前草稿仍保留在编辑器；请核对目录与权限后重试'})

        def do_GET(self): self.dispatch()
        def do_POST(self): self.dispatch(True)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server, token
