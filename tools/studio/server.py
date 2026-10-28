"""Loopback-only optional web app. Canvas storage is NOT controller run state.

APIs require a per-start bearer token, exact Host, and same-origin requests.
Filesystem writes are Studio revisions and a minimal session metadata index in
an explicitly selected Studio storage directory. Native sessions remain read-only. Starting an agent requires CLI-bound task + authorization;
there is no browser API for authorization, shell commands, cancel, or delivery.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
from .graph import Invalid, canonical, compile_task, digest, from_task, template, validate
from .projection import Reader, Unavailable
from .workspace_sessions import WorkspaceSessions

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / 'static'
MAX_BODY = 262144


class Conflict(ValueError):
    pass


def overlap(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def read_object(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BODY:
        raise Invalid('Invalid or oversized local configuration')
    obj = json.loads(path.read_bytes())
    if not isinstance(obj, dict):
        raise Invalid('Expected a JSON object')
    return obj


class Drafts:
    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / 'studio.sqlite'
        self.lock = threading.Lock()
        if self.path.is_symlink() or self.path.exists() and self.path.stat().st_nlink != 1:
            raise Invalid('Studio database must be a private regular file')
        for suffix in ('-wal', '-shm', '-journal'):
            if Path(str(self.path) + suffix).is_symlink():
                raise Invalid('Studio database sidecar must not be a symlink')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS revisions(
              id TEXT, revision TEXT, created REAL, document TEXT, PRIMARY KEY(id,revision));
              CREATE TABLE IF NOT EXISTS heads(id TEXT PRIMARY KEY, revision TEXT);''')
        self.path.chmod(0o600)

    def save(self, document, identity=None, expected=None):
        validate(document)
        if identity is not None and not re.fullmatch(r'[0-9a-f]{32}', str(identity)):
            raise Invalid('Invalid workflow identity')
        identity = identity or uuid.uuid4().hex
        revision = digest(document)
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision FROM heads WHERE id=?', (identity,)).fetchone()
            if (old[0] if old else None) != expected:
                raise Conflict('Draft changed in another tab. Reload before saving.')
            db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)', (identity, revision, time.time(), canonical(document).decode()))
            db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)', (identity, revision))
        return {'id': identity, 'revision': revision}

    def listing(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute('SELECT h.id,h.revision,r.created,r.document FROM heads h JOIN revisions r ON h.id=r.id AND h.revision=r.revision ORDER BY r.created DESC LIMIT 100').fetchall()
        return [{'id': i, 'revision': r, 'created': t, 'name': json.loads(d)['name']} for i, r, t, d in rows]

    def get(self, identity, revision=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            if revision is None:
                row = db.execute('SELECT revision FROM heads WHERE id=?', (identity,)).fetchone()
                revision = row[0] if row else None
            row = db.execute('SELECT document FROM revisions WHERE id=? AND revision=?', (identity, revision)).fetchone()
        if row is None:
            raise Unavailable('Workflow not found')
        return {'id': identity, 'revision': revision, 'document': json.loads(row[0])}


class Launcher:
    """One explicit launch per server lifetime; no result is inferred from Popen."""
    def __init__(self, control, task=None, authorization=None):
        self.control, self.task_path, self.auth_path = control, task, authorization
        self.lock = threading.Lock(); self.proc = None; self.used = False
        self.task = read_object(task) if task else None
        self.task_hash = digest(self.task) if task else None
        self.auth_hash = hashlib.sha256(Path(authorization).read_bytes()).hexdigest() if authorization else None

    def start(self, config_hash):
        with self.lock:
            if self.task is None or self.auth_path is None:
                raise Invalid('Browser execution was not enabled by the operator')
            if self.used:
                raise Conflict('This server launch permit has already been used; inspect run state first')
            if config_hash != self.task_hash or digest(read_object(self.task_path)) != self.task_hash:
                raise Conflict('Canvas/task differs from the operator-bound task; independent re-authorization required')
            if hashlib.sha256(Path(self.auth_path).read_bytes()).hexdigest() != self.auth_hash:
                raise Conflict('Authorization changed after Studio started')
            from orchestration.contracts import authorized
            authorized(self.task, self.auth_path)
            try:
                if any(r['config_sha256'] == self.task_hash and r['state'] not in ('COMPLETED', 'CANCELLED') for r in Reader(self.control).list_runs()):
                    raise Conflict('An existing run requires inspection; Studio never blindly retries it')
            except Unavailable:
                pass
            # Explicit fixed CLI entry; never use browser-supplied argv or a shell.
            self.used = True
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')}
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            try:
                self.proc = subprocess.Popen([sys.executable, str(ROOT / 'tools/devflower.py'), 'run',
                    '--control', str(self.control), '--config', str(self.task_path), '--authorization', str(self.auth_path)],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            except OSError:
                raise Unavailable('Controller could not start; inspect the local environment') from None
            return {'started': True, 'config_sha256': self.task_hash, 'run_id': None}

    def status(self):
        return {'enabled': self.task is not None, 'used': self.used, 'config_sha256': self.task_hash,
                'controller_exit': self.proc.poll() if self.proc else None}


class App:
    def __init__(self, workspace, control=None, task=None, authorization=None, project_roots=(), session_profiles=None, session_environ=None):
        workspace = Path(workspace).resolve()
        if overlap(workspace, ROOT) or control and overlap(workspace, control):
            raise Invalid('Studio workspace must be disjoint from the engine and control tree')
        for path in (task, authorization):
            if path and overlap(workspace, path):
                raise Invalid('Studio drafts must not contain operator configuration or authorization')
        if workspace.exists() and not (workspace / '.devflower-studio').is_file():
            raise Invalid('Choose a NEW Studio workspace; existing unrelated directories are never adopted')
        self.sessions = WorkspaceSessions(workspace, project_roots, session_profiles, session_environ, persist=False)
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = workspace / '.devflower-studio'
        if marker.is_symlink():
            raise Invalid('Invalid workspace marker')
        marker.touch(mode=0o600, exist_ok=True)
        self.sessions.initialize_storage()
        self.drafts = Drafts(workspace)
        self.reader = Reader(control)
        self.launcher = Launcher(control, task, authorization)
        self.token = secrets.token_urlsafe(32)


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'DevFlowerStudio/0.1'

        def log_message(self, *args):
            pass  # No tokens, imported configs or request paths in access logs.

        def setup(self):
            super().setup(); self.connection.settimeout(5)

        def response(self, code, value, content_type='application/json; charset=utf-8'):
            body = canonical(value) if content_type.startswith('application/json') else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers(); self.wfile.write(body)

        def check(self, api=False):
            port = self.server.server_port
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            host = self.headers.get('Host', '')
            if host not in hosts:
                self.response(403, {'error': 'Invalid Host'}); return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.response(403, {'error': 'Cross-origin request refused'}); return False
            if api and not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + app.token):
                self.response(401, {'error': 'Open the token-bearing URL printed in the terminal'}); return False
            return True

        def do_GET(self):
            self.route(False)

        def do_POST(self):
            self.route(True)

        def route(self, post):
            path = urlsplit(self.path).path
            if not self.check(path.startswith('/api/')):
                return
            try:
                body = None
                if post:
                    if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                        self.response(415, {'error': 'JSON Content-Length requests only'}); return
                    size = int(self.headers.get('Content-Length', '-1'))
                    if not 0 <= size <= MAX_BODY:
                        self.response(413, {'error': 'Request too large'}); return
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise Invalid('Expected JSON object')
                if not post and path in ('/', '/app.js', '/workspace.js', '/style.css', '/theme.css'):
                    name, mime = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/workspace.js': ('workspace.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/theme.css': ('theme.css', 'text/css; charset=utf-8')}[path]
                    self.response(200, (STATIC / name).read_bytes(), mime); return
                if not post and path == '/api/meta':
                    self.response(200, {'version': '0.1', 'profile': 'b-dual-review-v1', 'template': template(), 'launcher': app.launcher.status(), 'control_configured': app.reader.control is not None}); return
                if not post and path == '/api/workspaces':
                    self.response(200, app.sessions.listing()); return
                if not post and re.fullmatch(r'/api/workspaces/[a-f0-9]{32}/sessions', path):
                    self.response(200, app.sessions.snapshot(path.split('/')[3])); return
                if not post and path == '/api/workflows':
                    self.response(200, app.drafts.listing()); return
                if not post and re.fullmatch(r'/api/workflows/[a-f0-9]{32}', path):
                    revision = parse_qs(urlsplit(self.path).query).get('revision', [None])[0]
                    self.response(200, app.drafts.get(path.rsplit('/', 1)[1], revision)); return
                if post and path == '/api/workflows':
                    self.response(200, app.drafts.save(body['document'], body.get('id'), body.get('expected'))); return
                if post and path == '/api/validate':
                    self.response(200, {'order': validate(body['document']), 'revision': digest(body['document'])}); return
                if post and path == '/api/from-task':
                    specs = body['task'].get('policy', {}).get('verification', [])
                    if not 1 <= len(specs) <= 20:
                        raise Invalid('Studio editor supports 1–20 verification nodes; task was not imported')
                    graph = from_task(body['task']); validate(graph)
                    if body['task'].get('policy', {}).get('mode') != 'B':
                        raise Invalid('A/C are not silently converted to B')
                    self.response(200, graph); return
                if post and path == '/api/compile':
                    compiled = compile_task(body['document'], body['task'])
                    self.response(200, {'task': compiled, 'config_sha256': digest(compiled), 'workflow_revision': digest(body['document']), 'authorization_generated': False}); return
                if not post and path == '/api/runs':
                    try:
                        runs = app.reader.list_runs(); available = True
                    except Unavailable:
                        runs = []; available = False
                    self.response(200, {'runs': runs, 'available': available, 'launcher': app.launcher.status()}); return
                if not post and re.fullmatch(r'/api/runs/[a-f0-9]{32}', path):
                    self.response(200, app.reader.snapshot(path.rsplit('/', 1)[1])); return
                if post and path == '/api/start':
                    self.response(202, app.launcher.start(body.get('config_sha256'))); return
                self.response(404, {'error': 'Not found'})
            except Conflict as exc:
                self.response(409, {'error': str(exc)})
            except Unavailable as exc:
                self.response(503, {'error': str(exc)})
            except Invalid as exc:
                self.response(422, {'error': str(exc)})
            except (KeyError, TypeError, ValueError):
                self.response(422, {'error': 'Invalid document, graph or engine configuration. Validate the task and connections.'})
            except sqlite3.Error:
                self.response(503, {'error': 'Database unavailable or busy; retry without changing run state'})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                # The engine Blocked exception, filesystem failures, etc. must not
                # spill credentials, input, paths or a traceback into HTTP.
                self.response(422, {'error': 'Operation refused; check operator bindings and local prerequisites'})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='New private Studio draft directory, outside the repository/control')
    parser.add_argument('--project-root', action='append', default=[], help='Explicit business project root (repeatable); separate from Studio drafts')
    parser.add_argument('--session-profiles', help='Operator-owned JSON: explicit native storage profiles and scan budgets')
    parser.add_argument('--control', help='Existing Controller control directory to observe')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--launch-task', help='Optional pre-approved task: enables one exact browser launch')
    parser.add_argument('--authorization', help='Required with --launch-task; never sent to the browser')
    args = parser.parse_args(argv)
    if bool(args.launch_task) != bool(args.authorization) or args.launch_task and not args.control:
        parser.error('--launch-task requires --authorization and --control')
    if not 0 <= args.port <= 65535:
        parser.error('Invalid port')
    try:
        app = App(args.workspace, args.control, args.launch_task, args.authorization, args.project_root, args.session_profiles)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(app))
        print(f'DevFlower Studio: http://127.0.0.1:{server.server_port}/#token={app.token}', flush=True)
        print('Local access only. No automatic authorization, delivery, or main merge. Ctrl+C closes Studio, not an explicitly launched Controller.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (Invalid, OSError, ValueError):
        print('Studio startup refused: check directory boundaries, configuration and port.', file=sys.stderr)
        return 1
