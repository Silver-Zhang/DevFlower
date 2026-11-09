"""Verified in-memory pagination and search of one visible native transcript."""
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import secrets
from threading import Lock
import time
import unicodedata
from urllib.parse import parse_qs

from orchestration.common import Blocked

from .native_history_config import unique_pairs
from .native_sessions import (
    FILE_BYTES, HANDLE, MAX_BODY_BYTES, MAX_MESSAGE_CHARS, REVISION, WORKSPACE,
    NativeHistoryError, StopScan, TranscriptLineLimit, bounded_lines, require_file, stamp, visible_message,
)
from .session_identity import SessionRefused

MAX_PAGE_QUERY = 1024
MAX_SEARCH_BODY = 4096
PAGE_PARTS = 50
SEARCH_MESSAGES = 20_000
SEARCH_PARTS = 32_000
SEARCH_CHARS = 4_194_304
SEARCH_MATCHES = 10_000
READ_SECONDS = 5
CURSOR_LIMIT = 256
SNAPSHOT_CURSOR_LIMIT = 64
TOKEN = re.compile(r'[0-9a-f]{32}')
TOKEN_PLACEHOLDER = '0' * 32
NO_CURSOR = object()
SCOPE_PATTERNS = {'workspace': WORKSPACE, 'handle': HANDLE, 'revision': REVISION}


@dataclass(frozen=True, slots=True)
class PageRequest:
    workspace: str
    handle: str
    revision: str
    cursor: object
    query: str | None

    @property
    def mode(self):
        return 'browse' if self.query is None else 'search'


def parse_scope(data, *, search=False):
    code = 'NATIVE_SEARCH_INVALID' if search else 'NATIVE_PAGE_INVALID'
    required = set(SCOPE_PATTERNS) | ({'query'} if search else set())
    if (not isinstance(data, dict) or set(data) - {'cursor'} != required
            or any(not isinstance(data[key], str) or not pattern.fullmatch(data[key])
                   for key, pattern in SCOPE_PATTERNS.items())):
        raise NativeHistoryError(code)
    query = data.get('query')
    if search:
        if (not isinstance(query, str) or not 1 <= len(query) <= 200 or not query.strip()
                or any(unicodedata.category(char) in ('Cc', 'Cf', 'Cs') for char in query)):
            raise NativeHistoryError(code)
        query = query.casefold()
        if len(query) > 400:
            raise NativeHistoryError(code)
    return PageRequest(data['workspace'], data['handle'], data['revision'], data.get('cursor', NO_CURSOR), query)


def page_parameters(query):
    try:
        if len(query.encode('utf-8')) > MAX_PAGE_QUERY:
            raise ValueError()
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=4)
        if any(len(parts) != 1 for parts in values.values()):
            raise ValueError()
    except ValueError:
        raise NativeHistoryError('NATIVE_PAGE_INVALID') from None
    return parse_scope({key: parts[0] for key, parts in values.items()})


def search_parameters(raw):
    def invalid_constant(_):
        raise ValueError()

    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise NativeHistoryError('NATIVE_SEARCH_INVALID') from None
    return parse_scope(data, search=True)


def response(request, messages, cursor):
    return {'workspace': request.workspace, 'handle': request.handle, 'revision': request.revision,
            'mode': request.mode, 'messages': messages, 'next_cursor': cursor, 'has_more': cursor is not None,
            'coverage': 'verified_complete', 'readonly': True}


def wire_bytes(value):
    # The HTTP handler sends these exact bytes for successful history pages.
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


class VisiblePage:
    def __init__(self, request, position):
        self.browse = request.mode == 'browse'
        self.position = position
        self.parts = deque()
        self.more = False
        # Empty-list envelope with a token reserves the longest actual response.
        # Between list elements json.dumps emits exactly two separator bytes.
        self.limit = MAX_BODY_BYTES - len(wire_bytes(response(request, [], TOKEN_PLACEHOLDER)))
        self.size = 0

    def append(self, position, part):
        if self.position is not None and (position >= self.position if self.browse else position <= self.position):
            return
        if not self.browse and self.more:
            return
        size = len(wire_bytes(part))
        if size > self.limit:
            raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
        if not self.browse and (len(self.parts) == PAGE_PARTS or self.size + 2 + size > self.limit):
            self.more = True
            return
        self.size += size + (2 if self.parts else 0)
        self.parts.append((position, part, size))
        while len(self.parts) > PAGE_PARTS or self.size > self.limit:
            _, _, removed = self.parts.popleft()
            self.size -= removed + 2
            self.more = True

    def finish(self):
        boundary = None
        if self.more:
            boundary = self.parts[0 if self.browse else -1][0]
        return [part for _, part, _ in self.parts], boundary


def project_page(view, ref, provider, literals, request, position, checkpoint):
    if ref.stamp[4] > FILE_BYTES:
        raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
    page = VisiblePage(request, position)
    checksum = hashlib.sha256()
    ordinal = visible = chars = matches = 0
    search = request.mode == 'search'
    with view.directory(ref.relative.parent) as directory, view.regular(ref.relative.name, directory) as fd:
        if stamp(os.fstat(fd)) != ref.stamp:
            raise SessionRefused('ROOT_CHANGED')
        for line in bounded_lines(fd, ref.stamp[4], checksum, checkpoint=checkpoint):
            checkpoint()
            message = visible_message(json.loads(line.decode('utf-8')), provider, literals)
            checkpoint()
            if message is None:
                continue
            value = message['text']
            count = (len(value) + MAX_MESSAGE_CHARS - 1) // MAX_MESSAGE_CHARS
            if search:
                visible += 1
                chars += len(value)
                if visible > SEARCH_MESSAGES or chars > SEARCH_CHARS or ordinal + count > SEARCH_PARTS:
                    raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
                if request.query not in value.casefold():
                    ordinal += count
                    continue
                matches += 1
                if matches > SEARCH_MATCHES:
                    raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
            for index in range(count):
                checkpoint()
                start = index * MAX_MESSAGE_CHARS
                page.append(ordinal, {'role': message['role'], 'text': value[start:start + MAX_MESSAGE_CHARS],
                                      'part_index': index + 1, 'part_count': count})
                ordinal += 1
        checkpoint()
        if stamp(os.fstat(fd)) != ref.stamp or checksum.hexdigest() != ref.sha:
            raise SessionRefused('ROOT_CHANGED')
    require_file(view, ref)
    return page.finish()


@dataclass(slots=True)
class Cursor:
    scope: tuple
    position: int
    expires: float
    next_token: str | None = None


class NativeSessionPages:
    def __init__(self, reader):
        self.reader = reader
        self.cursors = OrderedDict()
        self.query_key = secrets.token_bytes(32)
        self.proof_guard = Lock()

    @contextmanager
    def _database_check(self, deadline):
        # The existing read-only SQLite SELECT can briefly create sidecars.
        # Keep only these short checks from interfering with sibling page reads.
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.proof_guard.acquire(timeout=remaining):
            raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
        try:
            yield
        finally:
            self.proof_guard.release()

    def _scope(self, request):
        query_tag = None if request.query is None else hmac.digest(self.query_key, request.query.encode('utf-8'), 'sha256')
        return request.workspace, request.handle, request.revision, request.mode, query_tag

    def _lookup(self, token, scope):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise NativeHistoryError('NATIVE_CURSOR_INVALID')
        cursor = self.cursors.get(token)
        if cursor is None or cursor.expires <= time.monotonic() or cursor.scope != scope:
            raise NativeHistoryError('NATIVE_CURSOR_INVALID')
        return cursor

    def _finish(self, request, scope, messages, boundary, expires, checkpoint):
        # Prepare the entire response before changing the cache. This also
        # makes replay and publication atomic under the reader's guard.
        parent = None if request.cursor is NO_CURSOR else self._lookup(request.cursor, scope)
        successor = parent.next_token if parent is not None else None
        mint = boundary is not None and successor is None
        token = secrets.token_hex(16) if mint else successor
        evict = self._evictions(request) if mint else set()
        body = wire_bytes(response(request, messages, token))
        if len(body) > MAX_BODY_BYTES or (mint and token in self.cursors):
            raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')
        if time.monotonic() >= expires:
            raise NativeHistoryError('NATIVE_REVISION_STALE')
        if successor is not None:
            self._lookup(successor, scope)
        checkpoint()
        if mint:
            for old in evict:
                del self.cursors[old]
            self.cursors[token] = Cursor(scope, boundary, expires)
            if parent is not None:
                parent.next_token = token
        return body

    def _evictions(self, request):
        now = time.monotonic()
        evict = {token for token, cursor in self.cursors.items() if cursor.expires <= now}
        local = [token for token, cursor in self.cursors.items() if token not in evict
                 and cursor.scope[0] == request.workspace and cursor.scope[2] == request.revision]
        if len(local) >= SNAPSHOT_CURSOR_LIMIT:
            evict.add(next(token for token in local if token != request.cursor))
        if len(self.cursors) - len(evict) >= CURSOR_LIMIT:
            evict.add(next(token for token in self.cursors if token not in evict and token != request.cursor))
        return evict

    def read(self, request):
        deadline = time.monotonic() + READ_SECONDS

        def checkpoint():
            if time.monotonic() >= deadline:
                raise NativeHistoryError('NATIVE_VISIBLE_BUDGET')

        workspace, handle, revision = request.workspace, request.handle, request.revision
        with self._database_check(deadline):
            path, identity, proof = self.reader.workspace(workspace)
        scope = self._scope(request)
        with self.reader.guard:
            snapshot = self.reader._snapshot(workspace, revision)
            record = snapshot.reads.get(handle)
            if record is None:
                raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE')
            if identity != snapshot.workspace_identity:
                raise NativeHistoryError('NATIVE_REVISION_STALE')
            cursor = None if request.cursor is NO_CURSOR else self._lookup(request.cursor, scope)
        checkpoint()
        if not self.reader.read_slots.acquire(blocking=False):
            raise NativeHistoryError('NATIVE_SCAN_BUSY')
        try:
            with self.reader.verified_record(workspace, handle, path, identity, record,
                                             deadline=deadline) as (view, locator):
                messages, boundary = project_page(view, record.ref, locator.provider, self.reader.literals,
                                                  request, cursor.position if cursor else None, checkpoint)
            checkpoint()
            with self._database_check(deadline):
                self.reader.require_proof(proof)
                with self.reader.guard:
                    self.reader._snapshot(workspace, revision)
                    return self._finish(request, scope, messages, boundary, snapshot.expires, checkpoint)
        except NativeHistoryError:
            raise
        except (StopScan, TranscriptLineLimit):
            raise NativeHistoryError('NATIVE_VISIBLE_BUDGET') from None
        except UnicodeError:
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE') from None
        except (OSError, ValueError, TypeError, Blocked, RecursionError):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None
        finally:
            self.reader.read_slots.release()
