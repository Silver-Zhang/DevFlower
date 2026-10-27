"""Transactional Studio workspace, immutable drafts and single-use human permits."""
from contextlib import closing
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from .graph import canonical, digest, Invalid
from .server import Conflict
from .usable_graph import draft


class Storage:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "usable.sqlite"
        for p in (self.path, *(Path(str(self.path) + s) for s in ("-wal", "-shm", "-journal"))):
            if p.is_symlink() or p.exists() and (not p.is_file() or p.stat().st_nlink != 1):
                raise Invalid("Studio storage must contain private regular files")
        self.lock = threading.RLock()
        with self.db() as db, db:
            # History scans must not block a concurrent save/permit commit.
            # Keep FULL durability; only reader/writer concurrency changes.
            if db.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise Invalid('Studio storage requires WAL reader/writer isolation')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS revisions(id TEXT,revision TEXT,data TEXT,at REAL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS permits(id TEXT PRIMARY KEY,hash TEXT,data TEXT,used INTEGER DEFAULT 0,at REAL);
                CREATE TABLE IF NOT EXISTS launches(permit TEXT PRIMARY KEY,run TEXT,state TEXT,data TEXT);
            """)
        self.path.chmod(0o600)

    def db(self):
        return closing(sqlite3.connect(self.path, timeout=10))

    def put(self, kind, key, data):
        value = canonical(data).decode()
        for attempt in range(3):
            try:
                with self.lock, self.db() as db, db:
                    db.execute("INSERT OR REPLACE INTO objects "
                               "SELECT ?,?,? WHERE NOT EXISTS "
                               "(SELECT 1 FROM objects WHERE kind=? AND id=? AND data=?)",
                               (kind, key, value, kind, key, value))
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, 'sqlite_errorcode', None)
                if type(code) is not int or code & 255 != sqlite3.SQLITE_BUSY or attempt == 2:
                    raise
                # Only an atomic metadata upsert retries, after rollback/close.
                # Never repeat the caller's native Agent or permit consumption.
                time.sleep(.05 * (2 ** attempt))

    def get(self, kind, key):
        with self.db() as db:
            row = db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        if not row:
            raise Invalid("Unknown " + kind)
        return json.loads(row[0])

    def listing(self, kind, *, filters=None, limit=100):
        conditions, params = ['kind=?'], [kind]
        for field, value in (filters or {}).items():
            conditions.append('json_extract(data, ?) = ?')
            params.extend(['$.' + field, value])
        query = 'SELECT data FROM objects WHERE ' + ' AND '.join(conditions) + ' ORDER BY rowid DESC'
        if limit is not None:
            query += ' LIMIT ?'
            params.append(limit)
        with self.db() as db:
            rows = db.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, kind, key, changes, states=None):
        """Merge atomically across the HTTP service and detached session worker."""
        with self.lock, self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM objects WHERE kind=? AND id=?', (kind, key)).fetchone()
            if not row:
                raise Invalid('Unknown ' + kind)
            data = json.loads(row[0])
            if states is not None and data.get('state') not in states:
                raise Conflict('Operation state changed; reload before acting')
            data.update(changes)
            db.execute('UPDATE objects SET data=? WHERE kind=? AND id=?', (canonical(data).decode(), kind, key))
        return data

    def save_draft(self, document, identity=None, expected=None):
        document = draft(document)
        if identity is not None and (not isinstance(identity, str) or len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity)):
            raise Invalid("Invalid draft identity")
        identity = identity or secrets.token_hex(16)
        revision = digest(document)
        with self.lock, self.db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM objects WHERE kind='draft' AND id=?", (identity,)).fetchone()
            if (json.loads(row[0])["revision"] if row else None) != expected:
                raise Conflict("Draft changed; reload its current revision")
            value = dict(id=identity, revision=revision, document=document)
            db.execute("INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)",
                       (identity, revision, canonical(value).decode(), time.time()))
            db.execute("INSERT OR REPLACE INTO objects VALUES('draft',?,?)", (identity, canonical(value).decode()))
        return value

    def prepare(self, data):
        key, value_hash = secrets.token_hex(16), digest(data)
        with self.lock, self.db() as db, db:
            db.execute("INSERT INTO permits(id,hash,data,at) VALUES(?,?,?,?)",
                       (key, value_hash, canonical(data).decode(), time.time()))
        return {"id": key, "sha256": value_hash, "data": data}

    def consume(self, key, expected, acknowledgements):
        with self.lock, self.db() as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT hash,data,used,at FROM permits WHERE id=?", (key,)).fetchone()
            if not row or row[0] != expected or row[2] or time.time() - row[3] > 3600:
                raise Conflict("Authorization preview expired, changed or already used")
            data = json.loads(row[1])
            if data.get("interaction") == "click":
                raise Invalid("This action requires its structured click record")
            questions = data["human_questions"]
            if not isinstance(acknowledgements, list) or len(acknowledgements) != len(questions) or any(
                    not isinstance(x, str) or not x.strip() or len(x) > 4000 for x in acknowledgements):
                raise Invalid("Every Human Gate needs an explicit human response")
            db.execute("UPDATE permits SET used=1 WHERE id=?", (key,))
            receipt = dict(actor="user", answers=acknowledgements, at=time.time(), preview_sha256=expected)
            db.execute("INSERT INTO launches VALUES(?,NULL,'AUTHORIZED',?)", (key, canonical(receipt).decode()))
        return data, receipt

    def consume_click(self, key, expected, action):
        """Consume a frozen direct-action permit without inventing a human answer."""
        with self.lock, self.db() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT hash,data,used,at FROM permits WHERE id=?', (key,)).fetchone()
            if not row or row[0] != expected or row[2] or time.time() - row[3] > 3600:
                raise Conflict('Authorization preview expired, changed or already used')
            data = json.loads(row[1])
            if data.get('interaction') != 'click' or data.get('click_action') != action:
                raise Invalid('Action does not match the frozen authorization')
            receipt = dict(actor='user', action=action, at=time.time(), preview_sha256=expected)
            db.execute('UPDATE permits SET used=1 WHERE id=?', (key,))
            db.execute("INSERT INTO launches VALUES(?,NULL,'AUTHORIZED',?)", (key, canonical(receipt).decode()))
        return data, receipt

    def launched(self, permit, run, state, **details):
        with self.lock, self.db() as db, db:
            row = db.execute("SELECT data FROM launches WHERE permit=?", (permit,)).fetchone()
            if not row:
                raise Invalid("Missing human authorization")
            data = dict(json.loads(row[0]), **details)
            db.execute("UPDATE launches SET run=?,state=?,data=? WHERE permit=?",
                       (run, state, canonical(data).decode(), permit))
