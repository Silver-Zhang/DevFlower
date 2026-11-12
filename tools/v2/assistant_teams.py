"""Validate assistant team plans and commit their Agent/draft revisions together."""
from copy import deepcopy
import json

from orchestration.common import Blocked, canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from .agent_records import KIND as AGENTS
from .assistant_proposals import _has_approval, parse_envelope, proposal
from .assistant_repairs import source_binding
from .canvas_structure import ID, fields, node_kind, text
from .definitions import normalize_graph
from .designer_graph import document_nodes, is_designer_graph
from .development_evidence import test_path, verification_entry_sha
from .development_graph import EXIT, contains_development
from .documents import check_chain, validate_draft
from .native_history_config import unique_pairs
from .project_files import read_project

KIND = 'assistant-team-apply'
SLOTS = {'Developer', 'Tester', 'Reviewer'}
PERMISSIONS = ('none', 'workspace-read', 'workspace-write')
ROLE_TEXT = {'name', 'role', 'responsibilities', 'background', 'goal', 'initial_prompt', 'round_prompt', 'handoff_prompt'}
PLAN_KEYS = {'version', 'kind', 'summary', 'name', 'agents', 'rounds'}
TEAM_INSTRUCTIONS = """生成可执行的 Agent 团队提案。只返回一个 JSON 对象，无额外文字。
字段为 version:1、kind、summary、name、rounds（整数 1–5）、agents（1–6 项）。
agents 每项必须包含 slot（字母开头的唯一 ID）、name、role、responsibilities、background、goal、
initial_prompt、round_prompt、handoff_prompt、skills（最多 5 个仅建议的名称）、permission（仅建议 none / workspace-read / workspace-write）。
名称、角色、背景、目标和所有 Prompt 用字符串。responsibilities 可为字符串或最多 8 项字符串的数组。
所有提示词需结合用户的具体工作需求。给出实际交接内容、失败反馈与成功条件。模型、工作目录、会话和权限由 DevFlower 固定或供用户选择，不要在 JSON 添加相关字段。
开发、测试、审核三角色和失败返工的需求使用 kind:"development"，固定三个 slot Developer、Tester、Reviewer。不要输出 workflow 或 operations，DevFlower 会生成有界循环与交付节点。
如果用户明确提供了已附加的验证 Python 文件及期望值，可以添加 verification_suggestion:{path,expected}。expected 是实际 JSON 值，不能猜测文件或预期结果。
其他新团队使用 kind:"create" 并附 workflow（V2 version:1、name、nodes、edges）。
workflow 的 Agent 节点只允许 id、name、kind:"agent"、position:{x,y}、slot、可选 output_contract。
其他节点用 id、name、kind、position:{x,y}、config_version:1、config。支持 start、output、condition、router、merge、loop、validation。condition 用 config.expression:{field,op,value}；router 用 config.rules:[{id,label,test:{field,op,value}}]；merge 用 config.strategy:"selected"；loop 用 config.max_iterations、exit_condition、body（同样的子图）；output 用 config.format:"text" 或 "json"。边包含 id、source、target、source_port、target_port，可加 transfer:{mode:"envelope",max_chars:16000}。默认 out / in，condition 为 true / false，router 为规则 ID 或 fallback，validation 为 pass / fail。
修改附加草稿使用 kind:"update"，附 operations（现有画布编辑格式）和 bindings（将已有或新增 Agent 节点 ID 映射到此次 agents 的 slot）。勿修改工具、审批或受保护的研发闭环。绑定已有 Agent 节点会新建一个配置版本供用户确认；不直接编辑已有 Agent。
operations 每条包含 op 与 id。支持 add-node（kind、name、可选 config）、update-node（name 或 config）、
remove-node、connect（source、target、可选 source_port、target_port、transfer）、remove-edge。
使用提供的已有节点、连线和端口身份。bindings 的键是要替换或新增的 Agent 节点身份，值是新 Agent 的 slot。
"""


def clean_text(value, limit, label):
    text(value, limit, label)
    if not value.strip():
        raise Invalid('团队提案需要填写' + label + '。')
    return value.strip()


def parse_plan(reply):
    value = parse_envelope(reply)
    if not isinstance(value, dict) or not isinstance(value.get('kind'), str):
        raise Invalid('助手未返回团队提案。')
    kind = value['kind']
    extra = {'development': set(), 'create': {'workflow'}, 'update': {'operations','bindings'}}.get(kind)
    if extra is None:
        raise Invalid('团队提案类型无效。')
    optional = {'verification_suggestion'} if kind == 'development' else set()
    fields(value, PLAN_KEYS | extra | optional, PLAN_KEYS | extra)
    if 'verification_suggestion' in value:
        suggested = value['verification_suggestion']
        fields(suggested, {'path','expected'}, {'path','expected'})
        try:
            test_path(suggested['path'])
            if len(canonical(suggested['expected'])) > 4000:
                raise ValueError()
        except (Blocked, ValueError, RecursionError, UnicodeError):
            raise Invalid('建议的验证文件或期望 JSON 无效。') from None
    if (type(value['version']) is not int or value['version'] != 1
            or type(value['rounds']) is not int or not 1 <= value['rounds'] <= 5
            or not isinstance(value['agents'],list) or not 1 <= len(value['agents']) <= 6):
        raise Invalid('团队提案版本、Agent 数量或轮次超出支持范围。')
    value['summary'] = clean_text(value['summary'], 1000, '说明')
    value['name'] = clean_text(value['name'], 160, '工作流名称')
    names, slots = set(), set()
    for row in value['agents']:
        fields(row, ROLE_TEXT | {'slot','skills','permission'}, ROLE_TEXT | {'slot','skills','permission'})
        slot = row['slot']
        if not isinstance(slot,str) or not ID.fullmatch(slot) or slot in slots:
            raise Invalid('提案 Agent 身份无效或重复。')
        responsibilities = row['responsibilities']
        if isinstance(responsibilities,list):
            if not 1 <= len(responsibilities) <= 8:
                raise Invalid('职责列表需要 1–8 项。')
            row['responsibilities'] = '\n'.join('- '+clean_text(item,480,'职责') for item in responsibilities)
        for key in ROLE_TEXT:
            row[key] = clean_text(row[key], 160 if key in ('name','role') else 2400, key)
        name = row['name'].casefold()
        if name in names:
            raise Invalid('同一团队的 Agent 名称必须不同。')
        if not isinstance(row['skills'],list) or len(row['skills']) > 5:
            raise Invalid('每个 Agent 最多建议五个 Skill 名称。')
        row['skills'] = [clean_text(item, 120, 'Skill 建议') for item in row['skills']]
        if row['permission'] not in PERMISSIONS:
            raise Invalid('提案权限建议超出支持范围。')
        slots.add(slot)
        names.add(name)
    if kind == 'development' and slots != SLOTS:
        raise Invalid('研发团队必须包含 Developer、Tester 和 Reviewer。')
    if kind == 'update':
        if (not isinstance(value['bindings'],dict) or not value['bindings']
                or any(not isinstance(slot,str) or slot not in slots for slot in value['bindings'].values())
                or set(value['bindings'].values()) != slots
                or not isinstance(value['operations'],list)):
            raise Invalid('每个新 Agent 都需要绑定当前草稿的明确节点。')
    return value


def typed(ident, name, kind, x, y=120, **config):
    return dict(id=ident,name=name,kind=kind,position={'x':x,'y':y},config_version=1,config=config)


def edge(source, target, port='out'):
    return dict(id=source+'To'+target,source=source,target=target,source_port=port,target_port='in',
                transfer={'mode':'envelope','max_chars':16000})


def link(row, saved):
    for field in ('provider','model','effort','initial_prompt','round_prompt','output_prompt','permission','timeout'):
        row.pop(field,None)
    row.update(kind='agent',agent_id=saved['id'],agent_revision=saved['revision'])


def development_plan(plan, agents, verification):
    accepted = {'field':'result.accepted','op':'equals','value':True}
    body_name = '开发、测试与审核'
    roles = []
    for index, slot in enumerate(('Developer','Tester','Reviewer')):
        row = dict(id=slot,name=agents[slot]['config']['name'],position={'x':360+index*310,'y':120})
        link(row, agents[slot])
        roles.append(row)
    developer, tester, reviewer = roles
    start = typed('RoundInput','本轮输入与上一轮反馈','start',60)
    if verification:
        reviewer['position']['x'] = 1290
        body = [start,developer,tester,typed('Verify','独立 JSON 测试','tool',980,tool='verify-json',
                permission='workspace-read',path=verification['path'],expected_json=canonical(verification['expected']).decode()),
                reviewer,typed('Gate','独立交付 Gate','tool',1600,tool='delivery-gate',permission='none',
                developer_node='Developer',tester_node='Tester',reviewer_node='Reviewer',verification_node='Verify'),
                typed('RoundOutput','本轮交付判定','output',1910)]
        edges = [edge(left['id'],right['id']) for left,right in zip(body,body[1:])]
        exit_condition = EXIT
    else:
        for row in (tester, reviewer):
            row['output_contract'] = [{'field':'result.accepted','type':'boolean','required':True},
                                      {'field':'result.feedback','type':'string','required':True}]
        reviewer['position']['x'] = 1290
        body = [start,developer,tester,typed('TestsPassed','Tester 报告通过？','condition',980,expression=accepted),
                reviewer,typed('Reviewed','Reviewer 结论','output',1600),
                typed('TestsFailed','失败反馈返回 Developer','output',1290,350)]
        edges = [edge('RoundInput','Developer'),edge('Developer','Tester'),edge('Tester','TestsPassed'),
                 edge('TestsPassed','Reviewer','true'),edge('TestsPassed','TestsFailed','false'),
                 edge('Reviewer','Reviewed')]
        exit_condition = accepted
    nodes = [typed('Start','研发需求','start',60),
             typed('RepairLoop','有界返工循环','loop',370,max_iterations=plan['rounds'],exit_condition=exit_condition,
                   body=dict(version=1,name=body_name,nodes=body,edges=edges))]
    if verification:
        nodes.append(typed('Finish','交付结果','output',680,format='json'))
        links = [edge('Start','RepairLoop'),edge('RepairLoop','Finish')]
    else:
        nodes.extend([typed('ReviewedOK','Reviewer 接受？','condition',680,expression=accepted),
                      typed('Finish','模型审核通过','output',990),
                      typed('RoundLimit','达到轮次上限，仍需返工','output',990,350)])
        links = [edge('Start','RepairLoop'),edge('RepairLoop','ReviewedOK'),
                 edge('ReviewedOK','Finish','true'),edge('ReviewedOK','RoundLimit','false')]
    return dict(version=1,name=plan['name'],nodes=nodes,edges=links)


def bind_new_graph(document, agents, used, *, depth=0):
    if depth > 2:
        raise Invalid('团队工作流最多嵌套两层。')
    fields(document, {'version','name','nodes','edges','viewport'}, {'version','name','nodes','edges'})
    if not isinstance(document['nodes'],list) or len(document['nodes']) > 64:
        raise Invalid('团队工作流节点超出支持范围。')
    for row in document['nodes']:
        if not isinstance(row,dict):
            raise Invalid('团队工作流节点无效。')
        kind = node_kind(row)
        if not isinstance(kind,str) or kind not in ('agent','start','output','condition','router','merge','loop','validation'):
            raise Invalid('新增团队工作流只允许无副作用的流程节点。')
        if kind == 'agent':
            fields(row, {'id','name','kind','position','slot','output_contract'}, {'id','name','kind','position','slot'})
            slot = row.pop('slot')
            if not isinstance(slot,str) or slot not in agents:
                raise Invalid('工作流引用了未定义的 Agent。')
            link(row, agents[slot])
            used.add(slot)
        if kind == 'loop':
            config = row.get('config')
            if not isinstance(config,dict) or 'body' not in config:
                raise Invalid('请选择有效的循环子图。')
            bind_new_graph(config['body'], agents, used, depth=depth+1)


def make_document(plan, base, agents, verification):
    if plan['kind'] == 'development':
        return validate_draft(development_plan(plan,agents,verification))
    if plan['kind'] == 'create':
        candidate, used = deepcopy(plan['workflow']), set()
        bind_new_graph(candidate, agents, used)
        if used != agents.keys():
            raise Invalid('团队中的每个 Agent 都必须连接到工作流。')
        return validate_draft(dict(candidate,name=plan['name']))
    if not base or contains_development(base['document']) or _has_approval(base['document']):
        raise Invalid('更新团队需附加普通草稿。受保护的 Gate 和人工审批需在画布中明确编辑。')
    document = validate_draft(base['document'])
    if plan['operations']:
        changes = canonical(dict(summary=plan['summary'],operations=plan['operations'])).decode()
        document = proposal(document, changes)['document']
    nodes = {node['id']:node for node in document['nodes']}
    for ident, slot in plan['bindings'].items():
        if ident not in nodes or node_kind(nodes[ident]) != 'agent':
            raise Invalid('只能将团队配置绑定到明确的 Agent 节点。')
        link(nodes[ident], agents[slot])
    return validate_draft(document)


def check_verification(assistant, workspace, turn, plan, permissions, verification):
    if verification is None:
        return None
    fields(verification, {'path','expected_json'}, {'path','expected_json'})
    path, expected = verification['path'], verification['expected_json']
    if (plan['kind'] != 'development' or permissions.get('Developer') != 'workspace-write'
            or not isinstance(path,str) or path not in {item['path'] for item in turn['context']['files']}
            or not isinstance(expected,str) or len(expected.encode()) > 4000):
        raise Invalid('独立测试需要明确选择已附加的 Python 文件和开发写入权限。')
    try:
        def nonfinite(_):
            raise ValueError()
        value = json.loads(expected,object_pairs_hook=unique_pairs,parse_constant=nonfinite)
    except (ValueError,RecursionError):
        raise Invalid('独立测试需要有效的期望 JSON。') from None
    source = source_binding(assistant.store,workspace)
    approved = read_project(assistant.store,workspace,path,fingerprint=True)
    entry = verification_entry_sha(assistant.store.workspace(workspace)['path'], path)
    if entry != approved['sha256']:
        raise Conflict('所选测试文件已变化。')
    return dict(path=path,expected=value,entry_sha256=entry,**source)


def model_config(assistant, workspace, turn, permissions):
    config = turn.get('config')
    if not config:
        raise Invalid('团队提案需要来自已配置的原生 CLI 调用。')
    selection = {key:config[key] for key in ('provider','native_profile','model','effort')} | {'identity':None}
    _, current, _ = assistant.selected_config(workspace, selection)
    if current['binding_sha256'] != config['binding_sha256']:
        raise Conflict('模型或原生配置在提案后已变化。请重新生成。')
    registry = assistant.model_operations.registry
    provider, profile = current['provider'], current['native_profile']
    home = registry.homes.get(provider,profile)
    catalog = registry.catalog_row(provider,registry.adapter(provider,profile),profile if home else None)
    available = ['none',*[value for value in PERMISSIONS[1:] if value in catalog['permissions']]]
    if provider == 'claude' and not home:
        approved = getattr(assistant.operations, 'profiles', {})
        if provider not in approved:
            raise Invalid('团队中的默认 Claude Agent 需使用已启用的原生会话配置，或选择已登记的独立 Claude HOME。')
    if not set(permissions.values()) <= set(available):
        raise Invalid('所选 CLI / HOME 不支持此工作区权限。')
    return current, available


def stage(assistant, workspace, turn, plan, permissions, verification):
    slots = {row['slot'] for row in plan['agents']}
    if not isinstance(permissions,dict) or permissions.keys() != slots or any(value not in PERMISSIONS for value in permissions.values()):
        raise Invalid('请逐一明确选择团队 Agent 的工具权限。')
    config, available = model_config(assistant, workspace, turn, permissions)
    verification = check_verification(assistant,workspace,turn,plan,permissions,verification)
    if verification and (config['provider']!='codex' or any(permissions[slot]=='workspace-write' for slot in SLOTS-{'Developer'})):
        raise Invalid('独立交付 Gate 需要写入的 Codex Developer 和只读或无工具的 Tester、Reviewer。')
    service = assistant.runtime.agents
    cwd = assistant.store.workspace(workspace)['path']
    prepared, choices = {}, []
    registry = assistant.model_operations.registry
    adapter = registry.adapter(config['provider'],config['native_profile'])
    for agent in plan['agents']:
        slot = agent['slot']
        permission = permissions[slot]
        role = 'developer' if slot=='Developer' else 'reviewer' if slot=='Reviewer' else 'custom'
        output = agent['handoff_prompt']
        if plan['kind']=='development' and slot=='Reviewer' and verification:
            output += '\n本轮独立审核回执必须为 JSON 对象，精确含 verdict（ACCEPT、REJECT 或 ACCEPT_WITH_LIMITATIONS）、candidate_sha256（复制验证回执的候选摘要）、feedback（具体审核依据和待修复项）。'
        if plan['kind']=='development' and slot in ('Tester','Reviewer') and not verification:
            output += '\n结构化结果必须含 accepted（布尔值）和 feedback（可执行的反馈文本）。测试或审核证据不足时 accepted=false，并说明缺失的证据。'
        model = config['requested_model']
        source = ('native_default' if model is None else 'catalog' if config['source']=='cli_catalog' else 'manual')
        value = dict({key:agent[key] for key in ROLE_TEXT-{'handoff_prompt'}}, output_prompt=output,
                     template=role, provider=config['provider'], model=model, model_source=source,
                     effort=config['requested_effort'], native_profile=config['native_profile'],
                     cwd=cwd, mode='Agent' if permission=='workspace-write' else 'Ask', permission=permission,
                     session={'kind':'new'}, skills=[], subagents='disabled', timeout=300, max_budget_usd=None)
        key = digest(canonical([KIND,workspace,turn['id'],slot]))[:32]
        # Reuse the Agent save validation and provider's actual permission admission.
        adapter.binding(workspace,value)
        result = service.prepare_save(workspace,key,None,value)
        prepared[slot] = result
        choices.append(dict(slot=slot, operation='create', row=result, suggested_permission=agent['permission'],
                            skill_suggestions=agent['skills']))
    base = turn['base'] if plan['kind']=='update' else None
    graph = make_document(plan,base,prepared,verification)
    if issues := check_chain(graph)['issues']:
        raise Invalid(issues[0]['message'])
    agents_by_id = {row['id']:row for row in prepared.values()}
    references, before_bindings, after_bindings = {}, {}, {}

    def capture(document, bindings):
        for path, node in document_nodes(document):
            if 'agent_id' not in node:
                continue
            agent_id, revision = node['agent_id'], node['agent_revision']
            row = agents_by_id.get(agent_id)
            if not row:
                row = service.get(workspace,agent_id,revision)
                references[agent_id] = row
            config = row['config']
            bindings[path] = dict(id=agent_id,revision=revision,**{key:config.get(key) for key in (
                'name','provider','model','effort','native_profile','mode','permission','initial_prompt',
                'round_prompt','output_prompt')})

    if base:
        capture(base['document'],before_bindings)
    capture(graph,after_bindings)
    bindings = [dict(node=path,before=before_bindings.get(path),after=after_bindings.get(path))
                for path in sorted(before_bindings.keys() | after_bindings.keys())
                if before_bindings.get(path) != after_bindings.get(path)]
    def runtime_config(node, _):
        return (agents_by_id.get(node.get('agent_id')) or references.get(node.get('agent_id')) or {'config':node})['config']
    budget = normalize_graph(graph,runtime_config,designer=is_designer_graph(graph))['budget']
    key = (base and base['id']) or digest(canonical([KIND,workspace,turn['id'],'draft']))[:32]
    expected = base['revision'] if base else None
    key, graph = assistant.store.prepare_save(workspace,key,expected,graph)
    before = base['document'] if base else None
    value = dict(summary=plan['summary'],kind=plan['kind'],agents=choices,permissions=permissions,
                 available_permissions=available,before=before,after=graph,bindings=bindings,
                 verification=verification,budget=budget,
                 target=dict(id=key,revision=expected), binding_sha256=config['binding_sha256'],
                 reference_revisions={ident:row['revision'] for ident,row in references.items()})
    return dict(value,sha256=digest(canonical(value)))


def confirmation(assistant, workspace, turn, data, staged):
    request_sha = digest(canonical(data))
    with assistant.store.db.tx() as db:
        applied = read_application(db,workspace,turn['id'],request_sha)
        if applied:
            return applied
        if data['preview_sha256'] != staged['sha256']:
            raise Conflict('团队配置、授权或草稿已变化，请重新预览。')
        for ident, revision in staged['reference_revisions'].items():
            raw = db.execute('SELECT data FROM records WHERE kind=? AND id=?',(AGENTS,ident)).fetchone()
            row = json.loads(raw[0]) if raw else {}
            if (row.get('workspace'),row.get('revision')) != (workspace,revision):
                raise Conflict('已绑定的 Agent 版本已变化。请重新预览。')
        service = assistant.runtime.agents
        agents = [service.save_prepared(db,item['row'],None) for item in staged['agents']]
        target = staged['target']
        draft = assistant.store.db.save_draft_in(db,target['id'],staged['after'],target['revision'],workspace)
        result = dict(agents=agents,draft=draft,preview_sha256=staged['sha256'],updated=staged['before'] is not None)
        db.execute('INSERT INTO records VALUES(?,?,?)',(KIND,turn['id'],canonical(
            dict(workspace=workspace,request_sha256=request_sha,result=result)).decode()))
        return result


def read_application(db, workspace, turn, request_sha=None):
    raw = db.execute('SELECT data FROM records WHERE kind=? AND id=?',(KIND,turn)).fetchone()
    if not raw:
        return None
    row = json.loads(raw[0])
    if row['workspace']!=workspace or request_sha is not None and row['request_sha256']!=request_sha:
        raise Conflict('此团队提案已确认，保存请求与原选择不同。')
    return row['result']
