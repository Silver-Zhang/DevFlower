"""Token-gated loopback UI; bootstrap token is never returned by HTTP."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from orchestration.common import Blocked
from .graph import canonical, Invalid
from .projection import Reader, Unavailable
from .server import Conflict
from .usable_graph import template, node, compile_plan, upgrade_draft

STATIC = Path(__file__).parent / "static"
MAX_BODY = 262144


def handler(runtime, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def respond(self, code, value, content_type="application/json; charset=utf-8"):
            body = canonical(value) if content_type.startswith("application/json") else value
            self.send_response(code)
            for k, v in {
                "Content-Type": content_type, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            }.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.route(False)

        def do_POST(self):
            self.route(True)

        def route(self, post):
            try:
                host = self.headers.get("Host", "")
                if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                    raise PermissionError("Invalid Host")
                origin = self.headers.get("Origin")
                if (origin is not None and origin != "http://" + host) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise PermissionError("Cross-origin request refused")
                parsed = urlsplit(self.path)
                path, query = parsed.path, parse_qs(parsed.query)
                if any(len(values) != 1 for values in query.values()):
                    raise Invalid('Duplicate query fields are unsupported')
                files = {"/": ("usable.html", "text/html; charset=utf-8"),
                         "/usable.js": ("usable.js", "text/javascript; charset=utf-8"),
                         "/usable_canvas.js": ("usable_canvas.js", "text/javascript; charset=utf-8"),
                         "/usable_sessions.js": ("usable_sessions.js", "text/javascript; charset=utf-8"),
                         "/usable.css": ("usable.css", "text/css; charset=utf-8"),
                         "/theme.css": ("theme.css", "text/css; charset=utf-8")}
                if not post and path in files:
                    name, mime = files[path]
                    return self.respond(200, (STATIC / name).read_bytes(), mime)
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    raise PermissionError("Open the private URL printed by Stable")
                session_only = getattr(runtime, 'session_only', False)
                if session_only and path not in {'/api/status', '/api/template', '/api/node', '/api/workspace',
                        '/api/sessions', '/api/session/history', '/api/session/operation', '/api/session/register',
                        '/api/session/visibility', '/api/session/prepare', '/api/session/authorize', '/api/session/cancel',
                        '/api/session/archive', '/api/session/delete', '/api/session/unarchive',
                        '/api/draft', '/api/compile', '/api/migrate-workflow'}:
                    raise Blocked('Development session host cannot authorize a Controller run, gate, deliver, or promote Stable')
                data = {}
                if post:
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise Invalid("JSON data required")
                    if self.headers.get("Transfer-Encoding"):
                        raise Invalid("Transfer encoding unsupported")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        raise Invalid("Request exceeds budget")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise Invalid("Expected object")
                if not post:
                    if path == "/api/status":
                        runtime.check_operator()
                        runs = []
                        if not session_only:
                            try:
                                runs = Reader(runtime.control).list_runs()
                            except Unavailable:
                                pass
                        value = dict(stable=None if session_only else runtime.trust.data["candidate_sha256"],
                                     candidate=runtime.candidate_sha256 if session_only else None,
                                     execution_context='development-sessions' if session_only else 'stable', profiles=runtime.profiles(),
                                     verification_profiles=[{"id": key, "cases": [c["id"] for c in cases]}
                                         for key, cases in runtime.operator.get("verification_profiles", {}).items()],
                                     workspaces=runtime.db.listing("workspace"), drafts=(runtime.db.listing("draft",filters={"workspace":query["workspace"][0]}) if query.get("workspace") and runtime.db.get("workspace",query["workspace"][0]) else []), runs=runs)
                    elif path == "/api/template":
                        value = template(query.get("kind", ["blank"])[0])
                    elif path == "/api/node":
                        value = node(query.get("kind", ["developer"])[0])
                    elif path == "/api/sessions":
                        if set(query) - {'workspace', 'hidden'}:
                            raise Invalid('Unsupported session query fields')
                        value = runtime.session_center.discover(query.get('workspace', [''])[0], query.get('hidden', ['false'])[0] == 'true')
                    elif path == '/api/session/history':
                        if set(query) - {'workspace', 'handle', 'query', 'limit'} or any(len(v) != 1 for v in query.values()):
                            raise Invalid('History requires opaque session handles, never paths')
                        value = runtime.session_center.history(query.get('workspace', [''])[0], query.get('handle', [''])[0],
                            query.get('query', [''])[0], int(query.get('limit', ['100'])[0]))
                    elif path == '/api/session/operation':
                        if set(query) != {'workspace', 'id'}:
                            raise Invalid('Operation identity and workspace required')
                        value = runtime.session_center.operation(query['id'][0], query['workspace'][0])
                    elif path == "/api/run":
                        value = runtime.snapshot(query.get("id", [""])[0])
                    elif path == "/api/diff":
                        value = runtime.difference(query.get("id", [""])[0])
                    else:
                        return self.respond(404, {"error": "Unknown route"})
                else:
                    if path == "/api/workspace" and set(data) == {"path"}:
                        value = runtime.open_workspace(data["path"])
                    elif path == '/api/session/register' and set(data) == {'workspace', 'provider', 'config_root'}:
                        value = runtime.session_center.register(data['workspace'], data['provider'], data['config_root'])
                    elif path == '/api/session/visibility' and set(data) == {'workspace', 'handle', 'hidden'}:
                        value = runtime.session_center.visibility(data['workspace'], data['handle'], data['hidden'])
                    elif path == '/api/session/prepare':
                        value = runtime.session_center.prepare(data)
                    elif path == '/api/session/authorize' and set(data) == {'permit', 'sha256', 'answer'}:
                        value = runtime.session_center.launch(data['permit'], data['sha256'], data['answer'])
                    elif path == '/api/session/cancel' and set(data) == {'workspace', 'id'}:
                        value = runtime.session_center.cancel(data['workspace'], data['id'])
                    elif path in ('/api/session/archive', '/api/session/delete', '/api/session/unarchive'):
                        raise Blocked('Native archive/delete is unsupported; original files and sub-sessions are never modified. Use DevFlower hide/unhide.')
                    elif path == "/api/draft" and set(data) in ({"document", "id", "expected", "workspace"},{"document", "id", "expected", "task", "workspace"}):
                        if not isinstance(data["workspace"],str) or not data["workspace"].strip():
                            raise Invalid("保存任务卡需要明确的已登记工作区；不接受null或空值")
                        value = runtime.db.save_draft(data["document"], data["id"], data["expected"],task=data.get("task"),workspace=data["workspace"])
                    elif path == "/api/migrate-workflow" and set(data) == {"document"}:
                        value = upgrade_draft(data["document"])
                    elif path == "/api/compile" and set(data) == {"document"}:
                        value = compile_plan(data["document"])
                    elif path == "/api/issue" and set(data) == {"url"}:
                        value = runtime.github.issue(data["url"])
                    elif path == "/api/prepare":
                        value = runtime.prepare(data)
                    elif path == "/api/authorize-run" and set(data) == {"permit", "sha256", "answers"}:
                        value = runtime.authorize_start(data["permit"], data["sha256"], data["answers"])
                    elif path == "/api/resume" and set(data) == {"run_id"}:
                        value = runtime.launch(data["run_id"])
                    elif path == "/api/cancel" and set(data) == {"run_id"}:
                        value = runtime.cancel(data["run_id"])
                    elif path == "/api/approve-result" and set(data) == {"run_id", "answer"}:
                        # Existing fixed C/G4 runs retain their original route.
                        value = runtime.approve_result(data["run_id"], data["answer"])
                    elif path == "/api/approve-result" and set(data) == {"run_id", "answers", "expected"}:
                        value = runtime.approve_result(data["run_id"], data["answers"], data["expected"])
                    else:
                        return self.respond(404, {"error": "Unknown route or unsupported fields"})
                self.respond(200, value)
            except PermissionError as exc:
                self.respond(403, {"error": str(exc)})
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except (Blocked, Invalid, Unavailable) as exc:
                self.respond(400, {"error": str(exc)})
            except (ValueError, KeyError, TypeError):
                self.respond(400, {"error": "Invalid request data"})
            except (BrokenPipeError, ConnectionResetError):
                # Browser navigation may close an in-flight snapshot connection.
                pass
            except Exception:
                self.respond(500, {"error": "Local runtime error; inspect operator evidence"})
    return Handler


def serve(runtime, port):
    # Persistent private token survives service restart, never enters source,
    # HTTP responses, model homes, subprocess environment or access logs.
    path = getattr(runtime, 'browser_directory', runtime.authority) / "browser-token"
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise Blocked("Invalid browser capability file")
        token = path.read_text().strip()
    else:
        token = secrets.token_urlsafe(32)
        path.write_text(token)
        path.chmod(0o600)
    if len(token) < 40:
        raise Blocked("Invalid browser capability")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(runtime, token))
    server.daemon_threads = True
    print(f"http://127.0.0.1:{server.server_port}/#token={token}", flush=True)
    server.serve_forever()
