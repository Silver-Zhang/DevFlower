"""Transactional events and immutable run activations, separate from old Controller authority."""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
import time
from orchestration.common import canonical, digest
from .graph import Invalid
from .server import Conflict


class WorkbenchStore:
    def __init__(self, path, *, scoped_drafts=False):
        # The workspace API requires explicit ownership. The default retains
        # the offline legacy store's unscoped revision/CAS compatibility.
        self.scoped_drafts = scoped_drafts
        self.path = Path(path)
        for p in (self.path, *(Path(str(self.path)+suffix) for suffix in ('-wal','-shm','-journal'))):
            if p.is_symlink() or p.exists() and (not p.is_file() or p.stat().st_nlink != 1):
                raise Invalid('运行数据库及日志需要私有常规文件')
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            if db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise Invalid('运行数据库需要 WAL 读写隔离')
            db.executescript('''CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));
              CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run TEXT,data TEXT);
              CREATE TABLE IF NOT EXISTS drafts(id TEXT,revision TEXT,data TEXT,PRIMARY KEY(id,revision));''')
        self.path.chmod(0o600)

    @contextmanager
    def tx(self, *, read=False):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            # Polling uses a committed snapshot, never reserves the sole writer.
            db.execute('BEGIN' if read else 'BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()

    def get(self, kind, key):
        with self.tx(read=True) as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        if not row: raise Invalid('找不到此记录，请刷新')
        return json.loads(row[0])

    def list(self, kind):
        with self.tx(read=True) as db:
            rows = db.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid DESC', (kind,)).fetchall()
        return [json.loads(x[0]) for x in rows]

    def put(self, kind, key, data):
        with self.tx() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (kind, key, canonical(data).decode()))
        return data

    def change(self, kind, key, changes, *, states=None, event=None):
        with self.tx() as db:
            row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
            if not row: raise Invalid('记录不存在')
            data = json.loads(row[0])
            if states is not None and data['state'] not in states: raise Conflict('状态已变化，请刷新')
            data.update(changes)
            db.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (canonical(data).decode(), kind, key))
            if event:
                value = dict(event, at=time.time(), run=data.get('run', key), node=data.get('node'),
                             iteration=data.get('iteration', 0), attempt=data.get('attempt', 1))
                db.execute('INSERT INTO events(run,data) VALUES(?,?)', (value['run'], canonical(value).decode()))
        return data

    def event(self, run, kind, **data):
        value = dict(data, run=run, kind=kind, at=time.time())
        with self.tx() as db:
            cur = db.execute('INSERT INTO events(run,data) VALUES(?,?)', (run, canonical(value).decode()))
        return dict(value, seq=cur.lastrowid)

    def events(self, run, after=0):
        with self.tx(read=True) as db:
            rows = db.execute('SELECT seq,data FROM events WHERE run=? AND seq>? ORDER BY seq LIMIT 1000', (run, after)).fetchall()
        return [dict(json.loads(data), seq=seq) for seq, data in rows]

    def save(self, key, graph, expected, workspace=None):
        if (workspace is not None and (not isinstance(workspace,str) or not workspace.strip()) or
                self.scoped_drafts and workspace is None):
            raise Invalid('保存需要明确的工作区；未分配的旧工作流只能新建工作区副本')
        revision = digest(canonical(graph)); value = dict(id=key, revision=revision, graph=graph)
        if workspace is not None: value['workspace'] = workspace
        with self.tx() as db:
            old = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (key,)).fetchone()
            if (json.loads(old[0])['revision'] if old else None) != expected:
                raise Conflict('工作流已由另一处保存；请重新加载后比较')
            if old and json.loads(old[0]).get('workspace') != workspace:
                raise Conflict('工作流归属未匹配；未分配的旧工作流只能新建副本，不能接管原件')
            db.execute('INSERT OR IGNORE INTO drafts VALUES(?,?,?)', (key, revision, canonical(value).decode()))
            db.execute("INSERT OR REPLACE INTO records VALUES('draft',?,?)", (key, canonical(value).decode()))
        return value

    def delete_draft(self, key, expected, workspace=None):
        """Delete the current draft with CAS; immutable revisions/runs remain."""
        if not isinstance(key, str) or not isinstance(expected, str):
            raise Invalid('删除工作流需要明确的草稿和版本')
        if not isinstance(workspace,str) or not workspace.strip():
            raise Invalid('删除工作流需要明确的所属工作区；不能全局删除旧草稿')
        with self.tx() as db:
            row = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (key,)).fetchone()
            if not row or json.loads(row[0])['revision'] != expected:
                raise Conflict('工作流版本已变化，请刷新后重新确认删除')
            if json.loads(row[0]).get('workspace') != workspace:
                raise Conflict('工作流归属未匹配；不能删除其他工作区或未分配的旧草稿')
            for raw, in db.execute("SELECT data FROM records WHERE kind='run'"):
                run = json.loads(raw)
                if (run.get('draft_id') == key or not run.get('draft_id') and run.get('graph_sha256')==expected) and run['state'] not in {'COMPLETED','FAILED','CANCELLED'}:
                    raise Conflict('此工作流仍有运行或待核验任务，请先处理运行')
            db.execute("DELETE FROM records WHERE kind='draft' AND id=?", (key,))
        return dict(id=key, deleted=True, history_preserved=True)
