"""Synthetic native layouts for V2 reader tests. No real account is used."""
import json
from pathlib import Path

SID = '11111111-2222-4333-8444-555555555555'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def records(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    return path


def codex_text(role, value, **fields):
    return {'type': 'response_item', 'payload': {
        'type': 'message', 'role': role, 'content': [
            {'type': 'input_text' if role == 'user' else 'output_text', 'text': value}], **fields}}


def codex(root, cwd, sid=SID, *, suffix='', rows=()):
    path = root / '2026' / '11' / '10' / f'rollout-2026-11-10T12-30-00-{sid}{suffix}.jsonl'
    return records(path, [
        {'type': 'session_meta', 'payload': {'id': sid, 'cwd': str(cwd)}, 'timestamp': '2026-11-10T12:30:00Z'},
        *rows,
    ])


def claude(root, cwd, sid=SID, *, rows=()):
    return records(root / 'synthetic-project' / (sid + '.jsonl'), [
        {'type': 'user', 'cwd': str(cwd), 'sessionId': sid, 'timestamp': '2026-11-10T12:31:00Z',
         'message': {'role': 'user', 'content': list(rows)}},
    ])


def fixtures(base):
    base = Path(base)
    projects = base / 'projects'
    a, b, empty = projects / 'A', projects / 'B', projects / 'Empty'
    for workspace in (a, b, empty):
        workspace.mkdir(parents=True)
    work = a / 'module'
    work.mkdir()
    home = base / 'fake-home'
    codex_root, claude_root = home / '.codex' / 'sessions', home / '.claude' / 'projects'
    main = codex(codex_root, a, rows=[
        codex_text('user', 'DEFAULT_CODEX_A <b>plain</b>'),
        codex_text('user', 'PRIVATE_USER_CHANNEL', channel='analysis'),
        dict(codex_text('user', 'PRIVATE_TOP_LEVEL_CHANNEL'), channel='analysis'),
        codex_text('user', 'PRIVATE_TOOL_RECIPIENT', recipient='functions.synthetic'),
        codex_text('assistant', 'PRIVATE_ASSISTANT', phase='analysis'),
        codex_text('assistant', 'DEFAULT_CODEX_FINAL Authorization: ghp_SYNTHETIC_DONT_SHOW'),
    ])
    codex(codex_root, b, OTHER, rows=[codex_text('user', 'DEFAULT_CODEX_B')])
    claude(claude_root, a, rows=[
        {'type': 'text', 'text': 'DEFAULT_CLAUDE_A'},
        {'type': 'thinking', 'thinking': 'PRIVATE_CLAUDE_THINKING'},
        {'type': 'tool_result', 'content': 'PRIVATE_CLAUDE_TOOL'},
    ])
    nested = a / 'nested' / '.claude' / 'projects'
    claude(nested, work, rows=[{'type': 'text', 'text': 'NESTED_CLAUDE_A'}])
    isolated = base / 'isolated' / 'native'
    codex(isolated, a, rows=[codex_text('user', 'ISOLATED_CODEX_A')])
    unsupported = empty / 'nested' / '.codex' / 'sessions' / '2026' / '11' / '10'
    unsupported.mkdir(parents=True)
    (unsupported / f'rollout-2026-11-10T12-30-00-{SID}.jsonl').write_text('partial', encoding='utf-8')
    (b / '.codex').mkdir()
    (b / '.codex' / 'settings.json').write_text('SETTINGS_WITHOUT_HISTORY', encoding='utf-8')
    (home / '.codex' / 'auth.json').write_text('AUTH_SENTINEL_NEVER_READ', encoding='utf-8')
    (home / '.claude' / 'settings.json').write_text('SETTINGS_SENTINEL_NEVER_READ', encoding='utf-8')
    config = base / 'history.json'
    config.write_text(json.dumps({
        'version': 1, 'discover_defaults': True, 'discover_nested': True,
        'profiles': [{'id': 'reviewer', 'provider': 'codex', 'anchor': str(isolated.parent), 'root': str(isolated)}],
    }), encoding='utf-8')
    config.chmod(0o600)
    return {'projects': projects, 'a': a, 'b': b, 'empty': empty, 'cwd': work, 'home': home, 'main': main,
            'codex_root': codex_root, 'claude_root': claude_root, 'nested': nested,
            'isolated': isolated, 'config': config}


def snapshot(*roots):
    return {
        (str(root), str(path.relative_to(root))): (path.lstat().st_mode, path.lstat().st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None)
        for root in roots for path in (root, *sorted(root.rglob('*')))
    }
