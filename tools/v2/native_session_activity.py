"""Read-only metadata for recognized tool records in one verified transcript."""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from threading import Lock
import time
from urllib.parse import parse_qs

from orchestration.common import Blocked

from .native_sessions import (
    FILE_BYTES, MAX_BODY_BYTES, NativeHistoryError, StopScan, TranscriptLineLimit,
    bounded_lines, request_parameters, require_file, stamp,
)
from .session_identity import SessionRefused

MAX_QUERY_BYTES = 1024
MAX_EVENTS = 2048
DISPLAY_LIMIT = 100
READ_SECONDS = 5
OTHER = '其他'
IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,256}')
ISO_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}'
                      r'(?::[0-9]{2}(?:\.[0-9]{1,9})?)?(?:Z|[+-][0-9]{2}:[0-5][0-9])')
CODEX_TYPES = {
    'function_call': ('function', True), 'function_call_output': ('function', False),
    'custom_tool_call': ('custom', True), 'custom_tool_call_output': ('custom', False),
}
CATEGORIES = {
    'codex': {'read_file': '读取', 'grep': '搜索', 'rg': '搜索', 'glob': '搜索', 'list_files': '搜索',
              'apply_patch': '编辑', 'functions.apply_patch': '编辑', 'exec_command': '命令',
              'shell_command': '命令', 'functions.exec_command': '命令'},
    'claude': {'Read': '读取', 'Grep': '搜索', 'Glob': '搜索', 'Edit': '编辑', 'Write': '编辑',
               'MultiEdit': '编辑', 'NotebookEdit': '编辑', 'Bash': '命令'},
}
PRIVATE_FIELDS = (
    'isMeta', 'is_meta', 'meta', 'isSidechain', 'is_sidechain', 'sidechain',
    'isSubagent', 'isSubAgent', 'is_subagent', 'is_sub_agent', 'subagent', 'subAgent', 'sub_agent',
    'subagentId', 'subAgentId', 'subagent_id', 'sub_agent_id',
    'parentAgentId', 'parent_agent_id', 'parentAgent', 'parent_agent', 'parentThreadId', 'parent_thread_id',
    'isMcp', 'isMCP', 'is_mcp', 'mcp', 'mcp_server', 'mcpServer', 'mcp_server_name', 'mcpServerName',
    'mcp_source', 'mcpSource',
)
CHILD_SOURCES = frozenset(('subagent', 'sub_agent', 'subAgent', 'sub-agent'))
PRIVATE_SOURCES = CHILD_SOURCES | {'mcp', 'mcp_tool', 'mcpTool', 'mcptool',
                                   'hook', 'permission', 'progress', 'reasoning', 'thinking'}


def activity_parameters(query):
    try:
        if len(query.encode('utf-8')) > MAX_QUERY_BYTES:
            raise ValueError()
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=3)
        return request_parameters(values, transcript=True)
    except ValueError:
        raise NativeHistoryError('NATIVE_ACTIVITY_INVALID') from None


def source_matches(value, choices):
    if isinstance(value, str):
        return value in choices or value.lower() in choices
    return isinstance(value, dict) and any(key in value for key in choices)


def mcp_name(name):
    return isinstance(name, str) and name[:5].lower().startswith(('mcp__', 'mcp:', 'mcp.'))


def private_event(record):
    return (record.get('channel') not in (None, '', 'final')
            or record.get('phase') not in (None, '', 'final_answer')
            or record.get('recipient') not in (None, '', 'all')
            or any(record.get(key) for key in PRIVATE_FIELDS)
            or any(source_matches(record.get(key), PRIVATE_SOURCES)
                   for key in ('source', 'origin', 'tool_source', 'toolSource'))
            or mcp_name(record.get('name')))


def timestamp(value, reference):
    if not isinstance(value, str) or not ISO_TIME.fullmatch(value):
        return None
    try:
        utc = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    return utc.isoformat().replace('+00:00', 'Z') if utc <= reference else None


def activity_blocks(record, provider, checkpoint):
    """Only the documented outer records and immediate tool blocks qualify."""
    kind = record.get('type')
    if provider == 'codex':
        payload = record.get('payload')
        if kind != 'response_item' or not isinstance(payload, dict):
            return
        tool_type = payload.get('type')
        if isinstance(tool_type, str) and tool_type in CODEX_TYPES:
            family, start = CODEX_TYPES[tool_type]
            yield family, start, payload.get('call_id'), payload.get('name'), private_event(record) or private_event(payload)
        return

    message = record.get('message')
    if kind not in ('assistant', 'user') or not isinstance(message, dict) or message.get('role', kind) != kind:
        return
    content = message.get('content')
    if not isinstance(content, list):
        return
    start = kind == 'assistant'
    wanted, id_field = ('tool_use', 'id') if start else ('tool_result', 'tool_use_id')
    private = private_event(record) or private_event(message)
    for block in content:
        checkpoint()
        if isinstance(block, dict) and block.get('type') == wanted:
            yield 'claude', start, block.get(id_field), block.get('name'), private or private_event(block)


def row(category, observation, started_at=None, returned_at=None):
    return {'category': category, 'observation': observation, 'started_at': started_at, 'returned_at': returned_at}


@dataclass(slots=True)
class Evidence:
    family: str | None
    position: int
    category: str = OTHER
    starts: int = 0
    returns: int = 0
    first_start: int = 0
    first_return: int = 0
    started_at: str | None = None
    returned_at: str | None = None

    def observe(self, family, start, category, at, position):
        if self.family != family:
            self.family = None
        if start:
            if self.starts == 0:
                self.category = category
                self.started_at, self.first_start = at, position
            self.starts += 1
        else:
            if self.returns == 0:
                self.returned_at, self.first_return = at, position
            self.returns += 1

    def public_row(self):
        if (self.family is None or self.starts > 1 or self.returns > 1
                or self.starts and self.returns and self.first_return < self.first_start):
            return row(OTHER, 'ambiguous')
        if self.starts:
            return row(self.category, 'return_observed' if self.returns else 'start_only',
                       self.started_at, self.returned_at)
        return row(OTHER, 'return_only', returned_at=self.returned_at)


def project_activity(view, ref, provider, reference, checkpoint):
    if ref.stamp[4] > FILE_BYTES:
        raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET')
    checksum = hashlib.sha256()
    by_id, suppressed = {}, set()
    unpaired = deque(maxlen=DISPLAY_LIMIT)
    position = public_events = unpaired_count = 0
    child_source = False
    categories = CATEGORIES[provider]
    with view.directory(ref.relative.parent) as directory, view.regular(ref.relative.name, directory) as fd:
        if stamp(os.fstat(fd)) != ref.stamp:
            raise SessionRefused('ROOT_CHANGED')
        for line in bounded_lines(fd, ref.stamp[4], checksum, checkpoint=checkpoint):
            checkpoint()
            record = json.loads(line.decode('utf-8'))
            checkpoint()
            if not isinstance(record, dict) or child_source:
                continue
            if provider == 'codex' and record.get('type') == 'session_meta':
                payload = record.get('payload')
                if isinstance(payload, dict) and source_matches(payload.get('source'), CHILD_SOURCES):
                    child_source = True
                    by_id.clear()
                    suppressed.clear()
                    unpaired.clear()
                continue
            for family, start, identifier, name, private in activity_blocks(record, provider, checkpoint):
                checkpoint()
                key = (hashlib.sha256(identifier.encode('ascii')).digest()
                       if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier) else None)
                if private:
                    if key is not None:
                        suppressed.add(key)
                        old = by_id.pop(key, None)
                        if old is not None:
                            public_events -= old.starts + old.returns
                    continue
                if key in suppressed or (start and (not isinstance(name, str) or not 1 <= len(name) <= 256)):
                    continue
                at = timestamp(record.get('timestamp'), reference)
                category = categories.get(name, OTHER) if start else OTHER
                public_events += 1
                position += 1
                if key is None:
                    unpaired_count += 1
                    unpaired.append((position, row(category, 'unpaired', at if start else None, at if not start else None)))
                else:
                    item = by_id.get(key)
                    if item is None:
                        item = by_id[key] = Evidence(family, position)
                    item.observe(family, start, category, at, position)
        checkpoint()
        if stamp(os.fstat(fd)) != ref.stamp or checksum.hexdigest() != ref.sha:
            raise SessionRefused('ROOT_CHANGED')
    require_file(view, ref)
    checkpoint()
    if child_source:
        return [], False
    if public_events > MAX_EVENTS:
        raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET')
    partial = len(by_id) + unpaired_count > DISPLAY_LIMIT
    ordered = [(item.position, item.public_row()) for item in by_id.values()] + list(unpaired)
    ordered.sort(key=lambda item: item[0])
    checkpoint()
    return [activity for _, activity in ordered[-DISPLAY_LIMIT:]], partial


class NativeSessionActivity:
    def __init__(self, reader):
        self.reader = reader
        self.proof_guard = Lock()

    @contextmanager
    def _database_check(self, deadline):
        # Keep this endpoint's short SQLite proofs from creating transient
        # sidecars during a sibling's proof. File reads still use four slots.
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.proof_guard.acquire(timeout=remaining):
            raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET')
        try:
            yield
        finally:
            self.proof_guard.release()

    def read(self, workspace, handle, revision):
        reference = datetime.now(timezone.utc)
        deadline = time.monotonic() + READ_SECONDS

        def checkpoint():
            if time.monotonic() >= deadline:
                raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET')

        with self._database_check(deadline):
            path, identity, proof = self.reader.workspace(workspace)
        with self.reader.guard:
            snapshot = self.reader._snapshot(workspace, revision)
            record = snapshot.reads.get(handle)
            if record is None:
                raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE')
            if identity != snapshot.workspace_identity:
                raise NativeHistoryError('NATIVE_REVISION_STALE')
        checkpoint()
        if not self.reader.read_slots.acquire(blocking=False):
            raise NativeHistoryError('NATIVE_SCAN_BUSY')
        try:
            with self.reader.verified_record(workspace, handle, path, identity, record,
                                             deadline=deadline) as (view, locator):
                activity, partial = project_activity(view, record.ref, locator.provider, reference, checkpoint)
            checkpoint()
            body = json.dumps({
                'workspace': workspace, 'handle': handle, 'revision': revision,
                'activity': activity, 'coverage': {'state': 'partial' if partial else 'complete',
                                                   'reasons': ['display_limit'] if partial else []},
                'readonly': True,
            }, ensure_ascii=False).encode('utf-8')
            if len(body) > MAX_BODY_BYTES:
                raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET')
            with self._database_check(deadline):
                self.reader.require_proof(proof)
                with self.reader.guard:
                    self.reader._snapshot(workspace, revision)
                    checkpoint()
            return body
        except NativeHistoryError:
            raise
        except (StopScan, TranscriptLineLimit):
            raise NativeHistoryError('NATIVE_ACTIVITY_BUDGET') from None
        except UnicodeError:
            raise NativeHistoryError('NATIVE_SESSION_UNAVAILABLE') from None
        except (OSError, ValueError, TypeError, Blocked, RecursionError):
            raise NativeHistoryError('NATIVE_REVISION_STALE') from None
        finally:
            self.reader.read_slots.release()
