"""The fresh V2 SQLite database is the sole authoritative draft store."""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict
from studio.workbench_store import WorkbenchStore
from .documents import validate_draft
from .workspace_select import select_existing_workspace, state_file_allowed


def private_directory(value):
    path = Path(value).absolute()
    for parent in [*reversed(path.parents), path]:
        if parent.is_symlink():
            raise Invalid('状态目录不能包含符号链接')
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    meta = path.stat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
        raise Invalid('V2 状态目录须为当前用户的私有目录（权限 0700）')
    return path


class DraftStore:
    def __init__(self, state, allowed_roots):
        source = Path(__file__).resolve().parents[2]
        requested = Path(state).absolute()
        if requested.is_relative_to(source) or source.is_relative_to(requested):
            raise Invalid('V2 状态目录必须放在产品源码目录之外')
        self.state = private_directory(state)
        if any(not state_file_allowed(item.name) for item in self.state.iterdir()):
            raise Invalid('此目录含有其他状态或用户文件；请选择全新 V2 状态目录')
        self.roots = [Path(root).resolve(strict=True) for root in allowed_roots]
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise Invalid('请明确允许浏览的测试工作空间根目录')
        if any(root == self.state or root.is_relative_to(self.state) or self.state.is_relative_to(root) for root in self.roots):
            raise Invalid('V2 状态目录与工作空间根目录必须分离')
        self.db = WorkbenchStore(self.state / 'v2.sqlite', scoped_drafts=True, annotate_events=True)
        with self.db.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS launch_intents(
                workspace TEXT, intent_key TEXT, payload_sha TEXT, run_id TEXT UNIQUE,
                definition_id TEXT, state TEXT, created REAL,
                PRIMARY KEY(workspace,intent_key))''')
        with self.db.tx() as db:
            row = db.execute("SELECT data FROM records WHERE kind='environment' AND id='local'").fetchone()
            if row:
                self.environment = json.loads(row[0])
            else:
                self.environment = {'id': secrets.token_hex(16), 'platform': 'linux', 'execution_available': False}
                db.execute('INSERT INTO records VALUES(?,?,?)', ('environment', 'local', canonical(self.environment).decode()))

    def directory(self, value):
        if not isinstance(value, str) or not Path(value).is_absolute() or '\x00' in value:
            raise Invalid('请选择允许根目录内的绝对文件夹路径')
        path = Path(value)
        if any(part.startswith('.') for part in path.parts if part not in ('.', '/')):
            raise Invalid('隐藏目录与原生账户目录不向 V2 开放')
        try:
            for parent in [path, *path.parents]:
                if parent.is_symlink():
                    raise Invalid('请选择实际文件夹，不跟随符号链接')
            resolved = path.resolve(strict=True)
            if not any(resolved.is_relative_to(root) for root in self.roots) or not resolved.is_dir():
                raise Invalid('文件夹不在启动时允许的工作空间根目录内')
            meta = resolved.stat()
            if not os.access(resolved, os.R_OK | os.X_OK):
                raise Invalid('当前用户无法读取此文件夹')
        except OSError:
            raise Invalid('文件夹不存在或无法读取；请重新选择') from None
        return resolved, meta

    def browse(self, value=None):
        path, _ = self.directory(value or str(self.roots[0]))
        entries = []
        try:
            for item in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                if len(entries) >= 300:
                    break
                if not item.name.startswith('.') and not item.is_symlink() and item.is_dir():
                    entries.append({'name': item.name, 'path': str(item)})
        except OSError:
            raise Invalid('无法浏览此文件夹，请核对读取权限') from None
        parent = str(path.parent) if any(path.parent.is_relative_to(root) for root in self.roots) else None
        return {'path': str(path), 'parent': parent, 'entries': entries, 'roots': list(map(str, self.roots))}

    def open_workspace(self, value):
        path, meta = self.directory(value)
        identity = digest(canonical([str(path), meta.st_dev, meta.st_ino]))
        with self.db.tx() as db:
            row = db.execute("SELECT data FROM records WHERE kind='workspace' AND id=?", (identity,)).fetchone()
            if row:
                return json.loads(row[0])
            result = {'id': identity, 'path': str(path), 'name': path.name or str(path),
                      'execution_environment_id': self.environment['id'], 'created_at': time.time()}
            db.execute('INSERT INTO records VALUES(?,?,?)', ('workspace', identity, canonical(result).decode()))
        return result

    def workspace(self, key, *, existing_only=False):
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
            raise Invalid('工作空间身份无效；请重新打开工作空间')
        row = json.loads(select_existing_workspace(self.state, key)) if existing_only else self.db.get('workspace', key)
        path, meta = self.directory(row['path'])
        if digest(canonical([str(path), meta.st_dev, meta.st_ino])) != key:
            raise Conflict('工作空间文件夹身份已变化；请重新打开')
        return row

    def drafts(self, workspace):
        self.workspace(workspace)
        return [row for row in self.db.list('draft') if row.get('workspace') == workspace]

    def draft(self, workspace, key):
        self.workspace(workspace)
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{32}', key):
            raise Invalid('草稿身份无效；请从当前工作空间重新选择')
        row = self.db.get('draft', key)
        if row.get('workspace') != workspace:
            raise Conflict('草稿不属于当前工作空间')
        return row

    def save(self, workspace, key, expected, document):
        key, graph = self.prepare_save(workspace, key, expected, document)
        return self.db.save(key, graph, expected, workspace)

    def prepare_save(self, workspace, key, expected, document):
        self.workspace(workspace)
        if key is not None and (not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key)):
            raise Invalid('草稿身份无效')
        if expected is not None and (not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected)):
            raise Invalid('草稿版本无效')
        if key is None and expected is not None:
            raise Invalid('新草稿不能携带旧版本')
        return key or secrets.token_hex(16), validate_draft(document)

    def launch_intent(self, workspace, intent_key, payload):
        """Resolve an already reserved request without reopening the current draft."""
        with self.db.tx(read=True) as db:
            return self._launch_intent(db, workspace, intent_key, digest(canonical(payload)))

    @staticmethod
    def _launch_intent(db, workspace, intent_key, payload_sha):
        row = db.execute('SELECT payload_sha,run_id,definition_id,state FROM launch_intents WHERE workspace=? AND intent_key=?',
                         (workspace, intent_key)).fetchone()
        if row:
            if row[0] != payload_sha:
                raise Conflict('重复启动键对应不同的请求')
            return {'run_id': row[1], 'definition_id': row[2], 'state': row[3], 'created': False}

    def reserve_launch(self, workspace, intent_key, payload, definition_id, *, publish=None):
        """Commit the launch identity and its optional run record before spawning."""
        self.workspace(workspace)
        if not isinstance(intent_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', intent_key):
            raise Invalid('启动意图身份无效')
        if not isinstance(definition_id, str) or not re.fullmatch(r'[0-9a-f]{64}', definition_id):
            raise Invalid('执行定义身份无效')
        sha = digest(canonical(payload))
        with self.db.tx() as db:
            prior = self._launch_intent(db, workspace, intent_key, sha)
            if prior:
                if prior['definition_id'] != definition_id:
                    raise Conflict('重复启动键对应不同的执行定义')
                return prior
            run = secrets.token_hex(16)
            if publish is not None:
                publish(db, run)
            db.execute('INSERT INTO launch_intents VALUES(?,?,?,?,?,?,?)',
                       (workspace, intent_key, sha, run, definition_id, 'RESERVED', time.time()))
        return {'run_id': run, 'definition_id': definition_id, 'state': 'RESERVED', 'created': True}
