"""Long synthetic Codex and Claude transcripts for visible-history tests."""
from v2_native_sessions_fixture import OTHER, SID, claude, codex, codex_text, fixtures, records

EARLIEST = 'EARLIEST Straße first-only <b>plain</b>'
REPEATED = 'same synthetic message'
PRIVATE = 'SYNTHETIC_PRIVATE_PAGE_MARKER'
SECRET = 'ghp_SYNTHETIC_PAGE_PASSWORD'
LITERAL = 'SyntheticLiteralOnly'
LONG = '🙂' * 15_995 + 'CrOsSBoundary' + '🙂' * 62_000
CLAUDE_LONG = '字"\\\n' * 15_000 + 'ClaudeLongEnd'


def page_fixtures(base):
    data = fixtures(base)
    messages = [('user', EARLIEST)]
    for index in range(1, 166):
        role = 'assistant' if index % 2 else 'user'
        value = REPEATED if index in (2, 100) else f'CODEX_VISIBLE_{index:03d}'
        messages.append((role, value))
    messages.insert(17, ('assistant', LONG))
    messages.insert(18, ('user', 'CrossBoundary CrossBoundary later match'))
    hidden = [codex_text('assistant', PRIVATE, channel='analysis'),
              codex_text('user', PRIVATE, recipient='functions.synthetic'),
              {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': PRIVATE}},
              codex_text('assistant', 'Authorization: ' + SECRET), codex_text('user', 'literal ' + LITERAL)]
    main = codex(data['codex_root'], data['a'], rows=[
        *[codex_text(role, value) for role, value in messages], *hidden,
    ])
    data['main'] = main
    messages.extend((('assistant', 'Authorization: [REDACTED]'), ('user', 'literal [REDACTED]')))
    other = [('user', f'WORKSPACE_B_VISIBLE_{i:03d}') for i in range(55)]
    codex(data['codex_root'], data['b'], OTHER, rows=[codex_text(role, value) for role, value in other])

    base_file = claude(data['claude_root'], data['a'])
    initial = {'type': 'user', 'cwd': str(data['a']), 'sessionId': SID, 'timestamp': '2026-11-10T12:31:00Z'}
    claude_messages = [('user', 'CLAUDE_EARLIEST'), ('assistant', CLAUDE_LONG)]
    claude_messages.extend(('assistant', f'CLAUDE_VISIBLE_{i:03d}') for i in range(111))
    visible = [dict(initial, type=role, message={'role': role, 'content': [{'type': 'text', 'text': value}]})
               for role, value in claude_messages]
    hidden = [dict(initial, isSidechain=True, message={'role': 'user', 'content': PRIVATE}),
              dict(initial, type='assistant', message={'role': 'assistant',
                   'content': [{'type': 'thinking', 'thinking': PRIVATE}, {'type': 'tool_use', 'input': PRIVATE}]})]
    records(base_file, [*visible, *hidden])
    return data, {'codex': messages, 'claude': claude_messages, 'other': other}
