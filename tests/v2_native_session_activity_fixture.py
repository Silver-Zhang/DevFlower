"""Synthetic native activity records with deliberately private metadata and bodies."""
from v2_native_sessions_fixture import SID, codex, codex_text, fixtures, records

A = '2020-01-02T02:00:00+02:00'
B = '2020-01-01T23:00:00Z'
FUTURE = '9999-01-01T00:00:00Z'
RAW_ARG = 'RAW_ACTIVITY_ARGUMENT /fixture/private/path'
RAW_RESULT = 'RAW_ACTIVITY_RESULT https://outside.invalid/hidden'
SECRET = 'ghp_SYNTHETIC_ACTIVITY_SECRET'
BAD_NAME = 'SYNTHETIC_VENDOR_TOOL'


def call(kind, key=None, name=None, at=None, *, envelope=None, **payload):
    body = {'type': kind, **payload}
    if key is not None:
        body['call_id'] = key
    if name is not None:
        body['name'] = name
    record = {'type': 'response_item', 'payload': body, **(envelope or {})}
    if at is not None:
        record['timestamp'] = at
    return record


def use(key, name, **fields):
    return {'type': 'tool_use', 'id': key, 'name': name, 'input': {'secret': RAW_ARG}, **fields}


def returned(key, **fields):
    return {'type': 'tool_result', 'tool_use_id': key, 'content': RAW_RESULT, 'is_error': True, **fields}


def claude_event(kind, blocks, at=None, *, role=True, message=None, **envelope):
    body = {'content': blocks, **(message or {})}
    if role is True:
        body['role'] = kind
    elif role is not False:
        body['role'] = role
    record = {'type': kind, 'message': body, **envelope}
    if at is not None:
        record['timestamp'] = at
    return record


def codex_examples():
    return [
        codex_text('user', '**SYNTHETIC_CODEX_TRANSCRIPT**'),
        call('function_call', 'public-pair', 'read_file', A, arguments=RAW_ARG + SECRET),
        call('custom_tool_call', 'public-start', 'functions.apply_patch', '2020-01-01T12:00:00'),
        call('function_call_output', 'public-pair', at=B, output=RAW_RESULT),
        call('function_call_output', 'public-orphan', at=A, output=RAW_RESULT),
        call('function_call', name='rg', at=B),
        call('function_call_output', 'illegal id with spaces', at=A),
        call('function_call_output', 'public-reverse', at=A),
        call('function_call', 'public-reverse', 'grep', B),
        call('function_call', 'public-duplicate', 'glob', A),
        call('function_call', 'public-duplicate', 'glob', A),
        call('function_call_output', 'public-repeat-output', at=A),
        call('function_call_output', 'public-repeat-output', at=A),
        call('function_call', 'public-mixed-family', 'list_files', A),
        call('custom_tool_call_output', 'public-mixed-family', at=A),
        call('custom_tool_call', 'public-unknown-name', BAD_NAME, FUTURE),
        call('function_call', 'public-command', 'functions.exec_command', 'invalid date'),
        call('function_call', 'public-malformed-name', '', at=A),
        call('function_call', 'public-overlong-name', 'x' * 257),
        call('function_call_output', 'private-late-start', at=A),
        call('function_call', 'private-late-start', 'read_file', A, envelope={'channel': 'analysis'}),
        call('function_call', 'private-late-return', 'rg', A),
        call('function_call_output', 'private-late-return', recipient='functions.hidden'),
        call('custom_tool_call_output', 'private-mcp-mixed', at=A),
        call('function_call', 'private-mcp-mixed', 'MCP__Hidden'),
        call('function_call', 'private-mcp-source', 'grep', source={'mcp': 'synthetic'}),
        call('function_call_output', 'private-mcp-source'),
        call('function_call_output', 'private-mcp-source-string'),
        call('function_call', 'private-mcp-source-string', 'grep', tool_source='MCP'),
        call('function_call', 'private-agent-parent', 'read_file', parent_agent_id='fixture-parent'),
        call('function_call_output', 'private-agent-parent'),
        call('function_call', 'private-child-id', 'read_file', subagent_id='fixture-child'),
        call('function_call_output', 'private-child-id'),
        call('function_call', 'private-flag', 'exec_command', envelope={'isSidechain': True}),
        call('function_call_output', 'private-flag'),
        call('function_call', 'private-mcp-dot', 'McP.Hidden'),
        call('function_call_output', 'private-mcp-dot'),
        call('function_call', 'private-mcp-colon', 'mcp:hidden'),
        call('function_call_output', 'private-mcp-colon'),
        call('mcp_tool_call', 'unknown-kind-orphan', 'mcp__ignored'),
        call('function_call_output', 'unknown-kind-orphan', at=B, output={
            'type': 'function_call', 'call_id': 'private-flag', 'name': 'read_file', 'text': RAW_RESULT,
        }),
        call('reasoning', 'never-read-reasoning', name='read_file', summary=SECRET),
        {'type': 'function_call', 'payload': {'call_id': 'wrong-envelope', 'name': 'read_file'}},
        codex_text('assistant', 'PRIVATE_ACTIVITY_REASONING', channel='analysis'),
    ]


def claude_examples():
    return [
        {'type': 'user', 'message': {'role': 'user', 'content': 'SYNTHETIC_CLAUDE_TRANSCRIPT'}},
        claude_event('assistant', [use('claude-pair', 'Read'), use('claude-editor', 'NotebookEdit')], A),
        claude_event('user', [returned('claude-pair')], B, role=False),
        claude_event('assistant', [use('claude-search', 'Grep'), use('claude-command', 'Bash')], FUTURE),
        claude_event('user', [returned('claude-orphan', content=[use('nested-hidden', 'Read')])], A),
        claude_event('assistant', [use(None, 'CUSTOM_CLAUDE_TOOL')], B),
        claude_event('user', [returned('claude-backward')], A),
        claude_event('assistant', [use('claude-backward', 'Glob')], B),
        claude_event('assistant', [use('claude-duplicate', 'MultiEdit'), use('claude-duplicate', 'MultiEdit')], A),
        claude_event('assistant', [use('claude-private', 'Read')], isSidechain=True),
        claude_event('user', [returned('claude-private')]),
        claude_event('user', [returned('claude-private-meta')], A),
        claude_event('assistant', [use('claude-private-meta', 'Read')], message={'isMeta': True}),
        claude_event('assistant', [use('claude-mcp', 'mCp__example')]),
        claude_event('user', [returned('claude-mcp')]),
        claude_event('assistant', [use('claude-parent', 'Read', parentAgentId='fixture')]),
        claude_event('user', [returned('claude-parent')]),
        claude_event('assistant', [use('mismatched-role', 'Read')], role='user'),
        claude_event('user', [returned('mismatched-role')], B),
        claude_event('assistant', [returned('wrong-role-shape')]),
        claude_event('user', [use('wrong-role-shape', 'Read')]),
        claude_event('assistant', [{'type': 'thinking', 'text': SECRET}, {'type': 'progress', 'text': RAW_ARG}]),
    ]


def activity_fixtures(base):
    data = fixtures(base)
    data['main'] = codex(data['codex_root'], data['a'], rows=codex_examples())
    data['claude_main'] = records(data['claude_root'] / 'synthetic-project' / (SID + '.jsonl'), [
        {'type': 'user', 'cwd': str(data['a']), 'sessionId': SID,
         'message': {'role': 'user', 'content': '合成 Claude'}},
        *claude_examples(),
    ])
    data['many'] = codex(data['isolated'], data['a'], rows=[
        codex_text('user', 'SYNTHETIC_PARTIAL_TRANSCRIPT'),
        *[call('function_call', f'count-{i}', 'read_file' if i == 0 else 'exec_command', A) for i in range(101)],
    ])
    return data
