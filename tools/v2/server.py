"""Loopback-only V2 draft HTTP boundary. No Provider or legacy runtime endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

from studio.graph import Invalid
from studio.server import Conflict
from .documents import check_chain

STATIC = Path(__file__).parent / 'static'


def create_server(store, port=0):
    token = secrets.token_urlsafe(32)

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
                    result = {'stage': 'M1', 'channel': 'Development', 'execution_available': False,
                              'workspaces': [row for row in store.db.list('workspace')],
                              'roots': list(map(str, store.roots)), 'environment': store.environment}
                elif not post and path == '/api/v2/folders':
                    result = store.browse(query.get('path'))
                elif post and path == '/api/v2/workspaces/open' and set(data) == {'path'}:
                    result = store.open_workspace(data['path'])
                elif not post and path == '/api/v2/drafts':
                    result = store.drafts(query.get('workspace'))
                elif not post and path == '/api/v2/draft':
                    result = store.draft(query.get('workspace'), query.get('id'))
                elif post and path == '/api/v2/drafts/save' and set(data) == {'workspace', 'id', 'expected', 'document'}:
                    result = store.save(data['workspace'], data['id'], data['expected'], data['document'])
                elif post and path == '/api/v2/check' and set(data) == {'workspace', 'document'}:
                    store.workspace(data['workspace'])
                    result = check_chain(data['document'])
                else:
                    return self.reply(404, {'error': '此 V2 阶段没有该操作；未启动任务'})
                self.reply(200, result)
            except Conflict as exc:
                self.reply(409, {'error': str(exc)})
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
