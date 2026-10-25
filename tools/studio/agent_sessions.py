"""Native session capabilities and bounded, visible-message-only history.

Paths are an internal adapter interface. HTTP callers must use SessionCenter's
workspace-bound opaque handles. Native transcripts are never rewritten.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re

from .graph import Invalid
from .session_sources import LIMITS, Refused, SnapshotReader, opened
from orchestration.privacy import redact


def capabilities(provider):
    supported = provider in ('codex', 'claude')
    return dict(supported=supported, discover=supported, read=supported,
                create=supported, resume=supported, hide=supported, unhide=supported,
                native_archive=False, native_delete=False, models=supported,
                effort=supported, subagents=False, parallel_dag=False)


def session_identity(provider, native_id, storage_profile, cwd, workspace):
    """Keep every approved identity component, including its storage profile."""
    return hashlib.sha256(json.dumps([provider, native_id, storage_profile, str(cwd), str(workspace)],
                                    separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def safe_text(value):
    """Conservative display redaction, including values absent from auth files."""
    value = re.sub(r'-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?(?:-----END [^-]+-----|$)',
                   '[REDACTED]', value, flags=re.S)
    value = re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]{8,}|github_pat_[a-z0-9_]+|'
                   r'AKIA[A-Z0-9]{16}|eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)\b', '[REDACTED]', value)
    value = re.sub(r'(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]+', r'\1 [REDACTED]', value)
    value = re.sub(r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|
                   auth[_-]?token|token|password|passwd|secret|authorization|cookie)["']?\s*[:=]\s*)
                   (?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''', r'\1[REDACTED]', value)
    value = re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[REDACTED]@', value)
    # Terminal escapes and bidi controls cannot become UI instructions.
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]', '', value)


def _visible(obj, provider):
    if not isinstance(obj, dict):
        return None
    if provider == 'codex':
        if obj.get('type') != 'response_item':
            return None
        message = obj.get('payload', {})
        if not isinstance(message, dict) or message.get('type') != 'message':
            return None
        role = message.get('role')
        if role == 'assistant':
            if (message.get('phase') not in (None, 'final_answer') or
                    message.get('channel') not in (None, 'final') or
                    message.get('recipient') not in (None, 'all')):
                return None
        kinds = {'input_text'} if role == 'user' else {'output_text'}
    else:
        if obj.get('type') not in ('user', 'assistant') or obj.get('isMeta') or obj.get('isSidechain'):
            return None
        message = obj.get('message', {})
        if not isinstance(message, dict):
            return None
        role = message.get('role', obj['type'])
        if role != obj['type']:
            return None
        kinds = {'text'}
    if role not in ('user', 'assistant'):
        return None
    content = message.get('content', [])
    if isinstance(content, str) and provider == 'claude':
        parts = [content]
    elif isinstance(content, list):
        parts = [p['text'] for p in content if isinstance(p, dict) and p.get('type') in kinds
                 and isinstance(p.get('text'), str)]
    else:
        parts = []
    # Claude command/system wrappers and tool results are not visible chat.
    parts = [p for p in parts if not re.search(r'<(?:local-command|command-name|system-reminder|tool_result)', p)]
    text = safe_text('\n'.join(parts)).strip()
    return dict(role=role, text=text) if text else None


def conversation(provider, path, limit=100, query='', *, expected_sha256=None, secrets=(), start_offset=0, end_offset=None, include_metadata=False):
    if provider not in ('codex', 'claude'):
        raise Invalid('Provider history is unsupported')
    if type(limit) is not int or not 1 <= limit <= 500 or not isinstance(query, str) or len(query) > 500:
        raise Invalid('History limit must be 1–500 and search at most 500 characters')
    rows = []
    content_hash = hashlib.sha256()
    if type(start_offset) is not int or start_offset<0 or end_offset is not None and (type(end_offset) is not int or end_offset<start_offset):
        raise Invalid('原生回合记录范围无效')
    try:
        with opened(Path(path)) as fd:
            before = os.fstat(fd)
            if before.st_size > LIMITS['file_bytes']:
                raise Refused('oversized')
            if start_offset>before.st_size or end_offset is not None and end_offset>before.st_size:
                raise Refused('changed_native_turn_range')
            position=0
            with io.BufferedReader(SnapshotReader(fd, before.st_size)) as stream:
                while True:
                    line = stream.readline(LIMITS['line_bytes'] + 1)
                    if not line:
                        break
                    if len(line) > LIMITS['line_bytes'] or not line.endswith(b'\n'):
                        raise Refused('partial_or_oversized_line')
                    content_hash.update(line)
                    position+=len(line)
                    if position<=start_offset or end_offset is not None and position-len(line)>=end_offset:continue
                    if position-len(line)<start_offset or end_offset is not None and position>end_offset:
                        raise Refused('non_boundary_native_turn_range')
                    obj = json.loads(line)
                    row = _visible(obj, provider)
                    if row:
                        row = redact(row, secrets)
                    if row and (not query or query.casefold() in row['text'].casefold()):
                        if include_metadata:row=dict(row,native_offset=position-len(line),native_end=position,native_at=obj.get('timestamp'))
                        rows.append(row)
                        if len(rows) > limit:
                            rows.pop(0)
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise Refused('changed_during_read')
            if expected_sha256 is not None and content_hash.hexdigest() != expected_sha256:
                raise Refused('changed_since_authorized_scan')
    except (OSError, Refused, ValueError, UnicodeError, RecursionError):
        raise Invalid('History unavailable, changed, or exceeds the bounded read budget') from None
    return rows


def nested_profiles(workspace, *, depth=4, entries=1200):
    """Find actual native roots inside one workspace, never crawl HOME or links."""
    root = Path(workspace)
    if root == Path.home() or root == Path('/'):
        return [], dict(status='not_scanned', reason='broad_home_or_root')
    result, count, partial = [], 0, False
    excluded = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache'}

    def walk(folder, level):
        nonlocal count, partial
        if level > depth:
            partial = True
            return
        try:
            with opened(folder, directory=True) as fd, os.scandir(fd) as children:
                for ent in children:
                    count += 1
                    if count > entries:
                        partial = True
                        return
                    if ent.is_symlink() or not ent.is_dir(follow_symlinks=False) or ent.name in excluded:
                        continue
                    child = folder / ent.name
                    if ent.name in ('.codex', '.claude'):
                        provider = ent.name[1:]
                        try:
                            with opened(child / ('sessions' if provider == 'codex' else 'projects'), directory=True):
                                pass
                        except (OSError, Refused):
                            continue
                        result.append(dict(provider=provider, config_root=str(child), home=None, origin='nested'))
                    else:
                        walk(child, level + 1)
        except (OSError, Refused):
            partial = True
    walk(root, 0)
    return result[:32], dict(status='partial' if partial or len(result) > 32 else 'complete', entries=count)
