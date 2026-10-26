"""Project-owned designs and portable, explicitly non-authoritative reading copies.

Execution leases, permits, native HOME, credentials and Runtime truth stay in the
host store. Nothing loaded here can become a receipt, process or authorization.
"""
from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

from orchestration.common import Blocked, canonical, digest
from .graph import Invalid
from .session_sources import opened
from .workbench_store import WorkbenchStore

MARKER = dict(version=1, kind='devflower-project', authority=False)
LIMIT = 8 * 1024 * 1024


class ProjectFiles:
    def __init__(self, runtime, workspace):
        self.rt = runtime
        self.workspace = workspace
        self.project = runtime.session_center.workspace(workspace)
        if not self.project.get('path'): raise Blocked('工作区身份未确认')
        self.base = Path(self.project.get('records_directory',self.project['path']))
        self.root = self.base / '.devflower'

    @contextmanager
    def directory(self, create=False):
        # Anchored descriptors prevent project-controlled ancestor replacement
        # from redirecting writes to a credential or another project.
        with opened(self.base, directory=True) as parent:
            if create:
                try: os.mkdir('.devflower', 0o700, dir_fd=parent)
                except FileExistsError: pass
            fd = os.open('.devflower', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise Blocked('.devflower 必须属于当前用户，且不能允许其他用户写入')
            try: marker = self.read(fd, 'project.json')
            except FileNotFoundError:
                if not create or os.listdir(fd):
                    raise Blocked('此目录已有未识别的 .devflower；保留原文件，未覆盖，请先检查内容') from None
                self.write(fd, 'project.json', MARKER)
                self.write_bytes(fd, '.gitignore', b'*\n')
                marker = MARKER
            if marker != MARKER: raise Blocked('.devflower 的格式不受支持，未覆盖原文件')
            yield fd
        finally: os.close(fd)

    @staticmethod
    def read(fd, name):
        if '/' in name or name in ('.', '..'): raise Invalid('工作区记录名称无效')
        # O_PATH first: never open a FIFO, device or hardlinked credential.
        pin = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(pin)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT:
                raise Blocked('工作区记录必须为有大小限制的普通文件，不能使用链接')
            with open('/proc/self/fd/' + str(pin), 'rb') as stream:
                raw = stream.read(LIMIT + 1)
                after = os.fstat(stream.fileno())
            if len(raw) > LIMIT or (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise Blocked('工作区记录读取期间变化，请重试')
            return json.loads(raw)
        finally: os.close(pin)

    @staticmethod
    def write_bytes(fd, name, raw):
        if '/' in name or name in ('.', '..') or len(raw) > LIMIT: raise Invalid('工作区记录超出限制')
        try:
            old = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(old.st_mode) or old.st_nlink != 1:
                raise Blocked('工作区记录不能覆盖链接或特殊文件')
        except FileNotFoundError: pass
        temporary = '.write-' + secrets.token_hex(16)
        target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(target, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try: os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError: pass

    @classmethod
    def write(cls, fd, name, value):
        raw = canonical(value) + b'\n'
        try:
            if cls.read(fd, name) == value: return
        except FileNotFoundError: pass
        cls.write_bytes(fd, name, raw)

    @contextmanager
    def library(self):
        with self.directory() as fd:
            # SQLite opens below the pinned directory, never a re-resolved path.
            yield WorkbenchStore('/proc/self/fd/' + str(fd) + '/library.sqlite', scoped_drafts=True)

    def initialize(self):
        with self.directory(create=True): pass
        # Old hosted designs are copied, not removed. New local revisions win;
        # reopening must never replace them with a stale hosted cache.
        with self.library() as local, local.tx() as db:
            for kind in ('draft', 'workflow-template', 'workflow-template-version'):
                for value in self.rt.flow.list(kind):
                    if value.get('workspace') != self.workspace: continue
                    key = value['id'] if kind != 'workflow-template-version' else value['id'] + ':' + value['revision']
                    if kind == 'draft' and db.execute("SELECT 1 FROM records WHERE kind='deleted-draft' AND id=?", (key,)).fetchone(): continue
                    db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)', (kind, key, canonical(value).decode()))
            # Preserve the immutable revisions too, including deleted drafts.
            with self.rt.flow.tx(read=True) as source:
                for key, revision, raw in source.execute('SELECT id,revision,data FROM drafts'):
                    value = json.loads(raw)
                    if value.get('workspace') == self.workspace:
                        db.execute('INSERT OR IGNORE INTO drafts VALUES(?,?,?)', (key, revision, raw))
            # Moving/copying a folder imports designs, never its native writers.
            for kind, key, raw in db.execute("SELECT kind,id,data FROM records WHERE kind IN ('draft','workflow-template','workflow-template-version')").fetchall():
                value = json.loads(raw)
                if value.get('workspace') == self.workspace: continue
                from .workbench_graph import validate_graph
                graph = copy.deepcopy(value['graph'])
                def clean(g):
                    for n in g['nodes']:
                        for field in ('context_from','context_refs','human_answer','receipt'): n.pop(field, None)
                        if n['kind'] == 'agent':
                            n['session'] = 'new';n.pop('skills',None)
                        if n['kind'] == 'loop': clean(n['body'])
                clean(graph); validate_graph(graph)
                value.update(workspace=self.workspace, graph=graph)
                if kind == 'draft': value['revision'] = digest(canonical(graph))
                db.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (canonical(value).decode(), kind, key))
                if kind == 'draft':
                    db.execute('INSERT OR IGNORE INTO drafts VALUES(?,?,?)', (key, value['revision'], canonical(value).decode()))
        return dict(path=str(self.root), format_version=1, designs='project-local', history='reading-copies',
                    execution_authority='host-only', native_history='original-location', git_ignored=True)

    def save_draft(self, key, graph, expected):
        with self.library() as store:
            try: deleted = store.get('deleted-draft', key).get('deleted')
            except Invalid: deleted = False
            if deleted:
                from .server import Conflict
                raise Conflict('此工作流已删除，请使用新身份保存副本；旧请求不能复活原件')
            value = store.save(key, graph, expected, self.workspace)
        # Compatibility cache only. Runtime checks the project-local design.
        try: old = self.rt.flow.get('draft', key)
        except Invalid: old = None
        if old is None or old.get('workspace') == self.workspace:
            self.rt.flow.put('draft', key, value)
        return value

    def delete_draft(self, key, expected):
        # The local store has no authoritative runs: check the host BEFORE
        # deleting. Unknown/recovery-required operations remain blocking.
        from .server import Conflict
        for run in self.rt.flow.list('run'):
            if (run.get('workspace') == self.workspace and
                    (run.get('draft_id') == key or not run.get('draft_id') and run.get('graph_sha256') == expected) and
                    run.get('state') not in ('COMPLETED','FAILED','CANCELLED')):
                raise Conflict('此工作流仍有运行或待核验任务，请先处理运行')
        with self.library() as store, store.tx() as db:
            raw = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (key,)).fetchone()
            row = json.loads(raw[0]) if raw else {}
            if row.get('revision') != expected or row.get('workspace') != self.workspace:
                raise Conflict('工作流归属或版本已变化，请刷新后重试')
            db.execute("DELETE FROM records WHERE kind='draft' AND id=?", (key,))
            db.execute("INSERT OR REPLACE INTO records VALUES('deleted-draft',?,?)", (key, canonical(dict(deleted=True)).decode()))
            value = dict(id=key, deleted=True, history_preserved=True)
        with self.rt.flow.tx() as db:
            db.execute("DELETE FROM records WHERE kind='draft' AND id=? AND json_extract(data,'$.workspace')=?", (key, self.workspace))
        return value

    def save_archive(self, kind, key, value):
        if kind not in ('session', 'assistant', 'run'): raise Invalid('记录类型无效')
        from .canvas_assistant import redact_visible
        data = redact_visible(self.rt, copy.deepcopy(value))
        # No authority objects, auth sources, HOME or raw protocol are exported.
        envelope = dict(version=1, kind=kind, key=key, source_workspace=self.workspace,
                        source_candidate=self.rt.candidate_sha256, authority=False, data=data)
        name = kind + '-' + digest(key.encode()) + '.json'
        with self.directory() as fd:
            lock = os.open('.history.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                st = os.fstat(lock)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1: raise Blocked('项目记录锁不是私有普通文件')
                fcntl.flock(lock, fcntl.LOCK_EX)
                try: old = self.read(fd, name)
                except FileNotFoundError: old = None
                if old and old.get('source_workspace') == self.workspace:
                    prior = old.get('data', {})
                    if kind == 'assistant' and prior.get('revision',0) > data.get('revision',0): return
                    if kind == 'run' and prior.get('event_sequence',0) > data.get('event_sequence',0): return
                self.write(fd, name, envelope)
            finally: os.close(lock)

    def archives(self, kind):
        rows = []
        with self.directory() as fd:
            names = sorted(n for n in os.listdir(fd) if n.startswith(kind + '-') and n.endswith('.json'))
            if len(names) > 2000: raise Invalid('本项目历史超过2000条，请分项目管理；原记录仍保留')
            for name in names:
                v = self.read(fd, name)
                if (not isinstance(v, dict) or v.get('version') != 1 or v.get('kind') != kind or v.get('authority') is not False
                        or not isinstance(v.get('key'), str) or name != kind + '-' + digest(v['key'].encode()) + '.json'
                        or not isinstance(v.get('data'), dict)):
                    raise Invalid('项目历史格式损坏，未作为真实运行导入')
                rows.append(v)
        return rows

    def archive(self, kind, key):
        for v in self.archives(kind):
            if v['key'] == key: return v
        raise Invalid('找不到此项目保存的历史')

    def assistant(self, row):
        self.save_archive('assistant', row['id'], {k: row[k] for k in
            ('id', 'title', 'messages', 'created_at', 'updated_at', 'collection', 'revision') if k in row})

    def session(self, row, history):
        self.save_archive('session', row['handle'], dict(
            session={k: row[k] for k in ('handle', 'title', 'provider', 'session_id', 'updated', 'collection',
                'node', 'node_name', 'workflow', 'workflow_id', 'workflow_group_id', 'execution') if k in row},
            messages=history['messages'], conversation_turns=history.get('conversation_turns', []),
            activity=history.get('activity', []), summaries=history.get('summaries', []),
            execution_results=history.get('execution_results', []),
            native_identity=dict(provider=row['provider'], session_id=row['session_id']),
            history_status=history.get('history_status', 'saved-visible-history')))

    def capture_sessions(self, *, run=None, cancelled=lambda:False):
        from .workbench_sessions import WorkbenchSessions
        sessions = WorkbenchSessions(self.rt)
        count = 0
        for view in ('active','archived','trash'):
            for row in sessions.listing(self.workspace, view)['sessions']:
                if cancelled(): return count
                if row.get('saved_history'): continue
                if run and row.get('workflow_id') != run: continue
                history = sessions.history(self.workspace, row['handle'])
                self.session(row, history); count += 1
        return count

    def run(self, run):
        from .run_journal import run_conversation
        from .workbench_sessions import WorkbenchSessions
        value = run_conversation(WorkbenchSessions(self.rt), self.workspace, run)
        row = self.rt.flow.get('run', run)
        value['created_at'] = row.get('created_at')
        self.save_archive('run', run, value)


def local_files(rt, workspace):
    return ProjectFiles(rt, workspace) if hasattr(rt, 'flow') and hasattr(rt, 'project_files') else None


def archive_completed(rt, workspace, *, run=None, assistant=None):
    """Best effort copy after authoritative completion; never changes its verdict.

    A failure is durable and user-visible; it never replays the native operation.
    """
    if not hasattr(rt, 'project_files'): return
    try:
        files = rt.project_files(workspace)
        if run: files.run(run)
        if assistant: files.assistant(rt.flow.get('canvas-conversation', assistant))
        if not assistant: files.capture_sessions(run=run)
        rt.db.put('project-archive-status', workspace, dict(workspace=workspace, error=None, at=time.time()))
    except Exception:
        rt.db.put('project-archive-status', workspace, dict(workspace=workspace,
            error='本轮已记录在本机；写入项目 .devflower 失败，请检查目录权限并重新打开工作区。未重发模型请求。', at=time.time()))


class ProjectSync:
    """Bounded background migration; opening a folder never waits for all chats.

    It only projects existing records. Restarting this idempotent copier cannot
    restart an Agent or adopt a saved receipt as live Runtime truth.
    """
    def __init__(self, rt):
        self.rt = rt; self.lock = threading.Lock(); self.workers = {}; self.failed = set(); self.cancel = threading.Event()

    def start(self, workspace):
        with self.lock:
            if self.cancel.is_set() or workspace in self.workers and workspace not in self.failed: return
            self.failed.discard(workspace)
            self.rt.db.put('project-migration', workspace, dict(workspace=workspace, state='RUNNING', error=None))
            thread = threading.Thread(target=self.copy, args=(workspace,), daemon=True, name='project-history-copy')
            self.workers[workspace] = thread; thread.start()

    def copy(self, workspace):
        try:
            files = self.rt.project_files(workspace)
            for row in self.rt.flow.list('run'):
                if self.cancel.is_set(): return
                if row.get('workspace') == workspace and row.get('state') in ('COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED'):
                    files.run(row['id'])
            count = files.capture_sessions(cancelled=self.cancel.is_set)
            if not self.cancel.is_set():
                self.rt.db.put('project-migration', workspace, dict(workspace=workspace, state='COMPLETED', sessions=count, error=None))
        except Exception:
            self.rt.db.put('project-migration', workspace, dict(workspace=workspace, state='FAILED',
                error='部分旧记录尚未保存到项目；本机原件保留。请重新打开项目重试，或先查看原记录。'))
            with self.lock: self.failed.add(workspace)

    def close(self):
        self.cancel.set()
        with self.lock: threads = list(self.workers.values())
        for thread in threads: thread.join(timeout=10)
