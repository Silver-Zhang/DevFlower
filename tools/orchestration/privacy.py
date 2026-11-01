"""Keep known provider credentials out of persisted final reports.

Only dedicated model-runtime files are inspected. Never print these values and
never read a model-supplied path. Raw streams are hashed, not written to disk.
"""
import json
import os
from pathlib import Path
from .common import Blocked


def credentials(home, extra_files=(), *, read_file=None, include_short=False):
    found = set()
    def walk(value, key=''):
        sensitive = any(x in key.lower().replace('-', '_') for x in
                        ('token', 'api_key', 'apikey', 'secret', 'authorization', 'password', 'passwd', 'cookie'))
        if isinstance(value, str) and sensitive and bool(value) and (include_short or len(value) >= 8):
            found.add(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
    paths = [Path(home) / name for name in ('.codex/auth.json', '.claude/.credentials.json', '.claude/settings.json')]
    for path in paths + [Path(p) for p in extra_files]:
        try:
            if read_file is not None:
                walk(json.loads(read_file(path)))
            elif path.is_file() and path.stat().st_size <= 4 * 1024 * 1024:
                walk(json.loads(path.read_bytes()))
        except (OSError, ValueError, RecursionError):
            continue
    return found


def redact(value, secrets):
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x, secrets) for x in value]
    return value


def check_tree(root, secrets):
    """Reject known runtime secrets before recording candidate paths or delivery."""
    if not secrets:
        return
    needles = [x.encode() for x in secrets]
    for folder, dirs, files in os.walk(root, followlinks=False):
        links = [d for d in dirs if (Path(folder) / d).is_symlink()]
        dirs[:] = [d for d in dirs if d not in links]
        for name in files + links:
            p = Path(folder) / name
            path_bytes = os.fsencode(str(p.relative_to(root)))
            data = os.fsencode(os.readlink(p)) if p.is_symlink() else p.read_bytes()
            if any(x in path_bytes or x in data for x in needles):
                raise Blocked('candidate contains known provider credential; no manifest or delivery recorded')
