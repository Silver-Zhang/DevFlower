"""Frozen topology and role admission for deterministic development gates."""
from orchestration.common import Blocked
from studio.graph import Invalid
from .canvas_structure import ID, json_input, node_kind

VERIFY = 'verify-json'
CHECK = 'check-json'
GATE = 'delivery-gate'
TOOLS = {VERIFY, GATE}
ROLES = ('developer_node','tester_node','reviewer_node','verification_node')
EXIT = {'field':'result.gate_verdict','op':'equals','value':'ACCEPT'}


def configured_tool(node):
    return node['config'].get('tool') if node_kind(node)=='tool' else None


def contains_development(document):
    return any(configured_tool(node) in TOOLS or node_kind(node)=='loop'
               and node['config'].get('body') and contains_development(node['config']['body'])
               for node in document['nodes'])


def one(graph, kind):
    values = [node for node in graph['nodes'] if node_kind(node)==kind]
    if len(values)!=1:
        raise Invalid('研发闭环需要明确且唯一的开始、循环与输出。')
    return values[0]


def chain(graph, nodes):
    expected = list(zip((node['id'] for node in nodes), (node['id'] for node in nodes[1:])))
    actual = [(edge['source'],edge['target']) for edge in graph['edges']]
    if len(graph['nodes'])!=len(nodes) or sorted(actual)!=sorted(expected):
        raise Invalid('研发闭环需要完整顺序连线，不能跳过测试、审核或交付 Gate。')
    for edge in graph['edges']:
        mode = edge.get('transfer', {}).get('mode')
        if mode not in (None,'envelope') or edge.get('transfer', {}).get('max_chars',16000)<12000:
            raise Invalid('研发闭环连线需要完整节点数据及至少 12000 字符的交接预算。')


def tool_options(node):
    config = node['config']
    tool = config.get('tool')
    if tool in (VERIFY, CHECK):
        from .project_files import checked_path
        if config.get('permission')!='workspace-read' or not checked_path(config.get('path','')).parts:
            raise Invalid('JSON 测试需要固定工作区相对文件路径与只读权限。')
        try:
            raw = config.get('expected_json','')
            if len(raw.encode())>4000:
                raise ValueError()
            value = json_input(raw)
        except (ValueError, RecursionError):
            raise Invalid('请填写 4000 字节以内的期望 JSON。') from None
        return {'path':config['path'],'expected_json':value}
    if tool == 'write-file':
        from .tool_outputs import output_path
        if config.get('permission') != 'workspace-write':
            raise Invalid('写入 JSON 结果需要明确选择工作区读写权限。')
        return {'path': output_path(config.get('path', '')).as_posix()}
    if tool==GATE:
        if config.get('permission','none')!='none' or any(not ID.fullmatch(config.get(key,'')) for key in ROLES):
            raise Invalid('交付 Gate 需要固定开发、测试、审核、确定性测试节点身份，权限选择未授予。')
        return {key:config[key] for key in ROLES}
    raise Invalid('研发证据工具类型无效。')


def layout(document):
    if not contains_development(document):
        return None
    root_start, loop, root_output = (one(document,kind) for kind in ('start','loop','output'))
    chain(document,[root_start,loop,root_output])
    body = loop['config'].get('body')
    if not body or loop['config'].get('exit_condition')!=EXIT:
        raise Invalid('研发循环只在 result.gate_verdict 等于 ACCEPT 时退出。')
    gates = [node for node in body['nodes'] if configured_tool(node)==GATE]
    if len(gates)!=1:
        raise Invalid('每轮需要唯一的交付 Gate。')
    gate, = gates
    roles = tool_options(gate)
    nodes = {node['id']:node for node in body['nodes']}
    values = [nodes.get(roles[key]) for key in ROLES]
    if any(node is None for node in values) or len({node['id'] for node in values})!=4:
        raise Invalid('交付 Gate 的四个角色必须是同一轮中的不同节点。')
    developer,tester,reviewer,verification = values
    agents = (developer,tester,reviewer)
    if (any(node_kind(node)!='agent' or 'agent_id' not in node for node in agents)
            or len({node['agent_id'] for node in agents})!=3 or configured_tool(verification)!=VERIFY):
        raise Invalid('开发、测试和审核需要三个独立保存的 Agent，及一个确定性 JSON 测试。')
    tool_options(verification)
    chain(body,[one(body,'start'),developer,tester,verification,reviewer,gate,one(body,'output')])
    return dict(loop=loop['id'], gate=gate['id'], **roles)


def issues(document):
    try:
        layout(document)
    except (Invalid, Blocked) as failure:
        return [dict(code='DEVELOPMENT_LAYOUT_INVALID', message=str(failure))]
    return []


def require_roles(layout, configured, bindings, cwd):
    for role in ('developer_node','tester_node','reviewer_node'):
        path = layout['loop']+'/'+layout[role]
        config = configured[path]['config']
        binding = bindings[path]
        if binding.get('cwd')!=cwd or binding.get('session_id'):
            raise Invalid('研发 Agent 必须在当前 Git 根目录中使用独立新会话。')
        if role=='developer_node':
            if (config.get('provider'),config.get('mode'),config.get('permission'),binding.get('v2_agent_scope'))!=(
                    'codex','Agent','workspace-write','workspace-write'):
                raise Invalid('开发 Agent 需要获准在工作区写入的 Codex Agent 模式。')
        elif config.get('mode') not in ('Ask','Plan') or config.get('permission') not in ('none','workspace-read'):
            raise Invalid('测试和独立审核 Agent 只能使用 Ask 或 Plan，只读或无工具权限。')


def freeze_source(store, workspace, document, roles, configured, bindings):
    from .development_evidence import verification_entry_sha
    from .git_workspace import bind_workspace
    source = bind_workspace(store,workspace,require_origin=False)
    if not source.head or not source.branch:
        raise Invalid('研发闭环需要已登记的 Git 分支与初始提交。')
    cwd = store.workspace(workspace)['path']
    require_roles(roles,configured,bindings,cwd)
    loop = next(node for node in document['nodes'] if node['id']==roles['loop'])
    verification = next(node for node in loop['config']['body']['nodes'] if node['id']==roles['verification_node'])
    return dict(head=source.head,branch=source.branch,binding=source.identity), verification_entry_sha(
        cwd,verification['config']['path'])
