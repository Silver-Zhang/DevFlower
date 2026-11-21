"""Stage native canvas proposals and commit their explicit application as one transaction."""
import json

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from devflower_export import agent_record, document_agents
from workbench.assistant.proposals import MAX_OPERATIONS, parse_envelope, proposal, validate_proposal_envelope
from workbench.canvas.canvas_structure import ID, fields, node_kind, text
from workbench.canvas.designer_graph import document_nodes
from workbench.runs.development_graph import configured_tool
from workbench.runs.script_runs import EXECUTION

APPLICATION = 'assistant-native-apply'


def inventory(assistant, workspace):
    fields = ('name', 'role', 'provider', 'cwd', 'model', 'effort')
    agents = [row for row in assistant.agents.list(workspace)['agents'] if row['config'].get('execution') == EXECUTION]
    return assistant.inspector.clean([
        dict(id=row['id'], revision=row['revision'], **{key: row['config'].get(key) for key in fields})
        for row in agents[:20]
    ])



def visible_graph(inspector, document):
    public = inspector.clean(document)
    for node, view in zip(document['nodes'], public['nodes']):
        config = node.get('config', {})
        if config.get('tool') == 'command' and 'argv' in config:
            # Explicit workflow commands are editable canvas data. Do not publish
            # process argv or inherited environment from the run evidence here.
            view['config']['argv'] = [inspector.text(arg) for arg in config['argv']]
        body = config.get('body')
        if body is not None:
            view['config']['body'] = visible_graph(inspector, body)
    return public

def correction(assistant, previous, reply):
    """Retain independently valid operations and record every rejected index."""
    value = parse_envelope(reply)
    operations = value.get('operations') if isinstance(value, dict) else None
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise Invalid('原回执没有可核对的操作清单。请重新生成提案。')
    if len(reply.encode()) > 15000:
        raise Invalid('原回复超过定向纠正可完整保留的 15,000 字节。请拆分需求后重新生成，当前草稿仍可编辑。')
    profiles = value.get('profiles', [])
    if not isinstance(profiles, list):
        profiles = []
    retained, rejected = [], []
    retained_profiles = []
    unlocked = {key: item for key, item in previous.items() if key != 'correction_lock'}
    for index, operation in enumerate(operations, 1):
        trial = retained + [operation]
        raw_trial = canonical(trial).decode()
        used = [item for item in profiles if isinstance(item, dict) and isinstance(item.get('id'), str)
                and json.dumps(item['id'], ensure_ascii=False) in raw_trial]
        try:
            stage(assistant, unlocked, canonical(dict(summary='校验可保留的修改', operations=trial,
                                                     profiles=used)).decode())
        except (Invalid, Conflict, ValueError, TypeError, KeyError, RecursionError) as error:
            reason = str(error) if isinstance(error, (Invalid, Conflict)) else '操作依赖或数据未通过校验。'
            rejected.append(dict(index=index, reason=assistant.inspector.text(reason)[:240]))
        else:
            retained = trial
            retained_profiles = used
    locked = dict(operations=retained, profiles=retained_profiles)
    prior_lock = previous.get('correction_lock') or {}
    original_request = prior_lock.get('original_request', previous['text'])
    if prior_lock.get('rename_only'):
        # A rejected correction cannot widen a previously verified rename.
        locked = prior_lock
    elif 'name' in value:
        try:
            stage(assistant, unlocked, canonical(dict(summary='校验可保留的名称',
                                                     name=value['name'], operations=[])).decode())
        except (Invalid, Conflict, UnicodeError):
            pass
        else:
            locked['name'] = value['name']
            if not operations and value.get('profiles', []) == []:
                locked.update(rename_only=True, original_request=original_request)
    return locked, dict(previous=previous['id'], original_request=original_request, original_reply=reply,
                        retained_count=len(locked['operations']), rejected=rejected)


def require_correction_lock(value, locked):
    if not locked:
        return
    if 'name' in locked and (not isinstance(value, dict) or value.get('name') != locked['name']):
        raise Invalid('纠正回复必须保留已核验的工作流名称（name）。')
    if locked.get('rename_only') and (value.get('operations') != [] or value.get('profiles', []) != []):
        raise Invalid('纠正范围仅限工作流重命名。请保留空的 operations，且不添加 profiles。')
    operations = value.get('operations') if isinstance(value, dict) else None
    profiles = value.get('profiles', []) if isinstance(value, dict) else []
    if not isinstance(operations, list) or not isinstance(profiles, list):
        raise Invalid('纠正回复缺少完整操作或 Agent 配置。')
    remaining = iter(operations)
    if any(not any(operation == preserved for operation in remaining) for preserved in locked['operations']):
        raise Invalid('纠正回复遗漏或改动了已核验的操作。请原样保留这些操作及其顺序。')
    if any(profile not in profiles for profile in locked['profiles']):
        raise Invalid('纠正回复改动了已核验操作所绑定的 Agent 配置。')


def stage(assistant, turn, reply):
    value = parse_envelope(reply)
    require_correction_lock(value, turn.get('correction_lock'))
    if isinstance(value, dict) and set(value) == {'answer'}:
        text(value['answer'], 12000, '助手回答')
        if not value['answer'].strip():
            raise Invalid('助手回答为空。')
        return {'answer': value['answer']}
    base = turn['base']
    if not base:
        raise Invalid('打开可编辑草稿后再请求工作流修改。')
    validate_proposal_envelope(value, native=True)
    workspace = turn['workspace']
    root = assistant.store.workspace(workspace)['path']
    profiles = {item['id']: item for item in turn['native_profiles']}
    new = []
    entries = value.get('profiles', []) if isinstance(value, dict) else []
    if not isinstance(entries, list) or len(entries) > 12:
        raise Invalid('一次最多提议 12 个新的原生 Agent。')
    for part in entries:
        fields(part, {'id', 'name', 'role', 'provider'}, {'id', 'name', 'role', 'provider'})
        alias = part['id']
        if not isinstance(alias, str) or not ID.fullmatch(alias) or alias in profiles:
            raise Invalid('提案的新 Agent 别名无效或重复。')
        key = digest(canonical([turn['id'], alias]))[:32]
        config = dict(execution=EXECUTION, name=part['name'], role=part['role'], provider=part['provider'],
                      cwd=root, round_prompt='', output_prompt='')
        row = assistant.agents.prepare_save(workspace, key, None, config)
        new.append(row)
        profiles[alias] = row
    before = base['document']
    native_ids = {row['id'] for row in assistant.agents.list(workspace)['agents']
                  if row['config'].get('execution') == EXECUTION}
    managed_nodes = any(node_kind(node) == 'approval' or configured_tool(node) not in (None, '', 'command')
                        for _, node in document_nodes(before))
    legacy = managed_nodes or any(('agent_id' in node and node['agent_id'] not in native_ids) or 'provider' in node
                                  for node in document_agents(before))
    if legacy:
        operations = value.get('operations') if isinstance(value, dict) else None
        if (new or not isinstance(operations, list) or any(not isinstance(op, dict) or op.get('op') != 'update-node'
                                                           or 'config' in op for op in operations)):
            raise Invalid('此草稿含受管节点或未核验的 Agent。请在画布中明确调整执行路径。')
    result = proposal(before, reply, native_profiles=profiles)
    after = result['document']
    if not legacy and value['operations']:
        after['execution'] = EXECUTION
    before_refs = references(before)
    after_refs = references(after)
    added = {(row['id'], row['revision']) for row in new}
    listed = {(row['id'], row['revision']) for row in turn['native_profiles']}
    if not (after_refs - before_refs <= added | listed) or not added <= after_refs:
        raise Invalid('提案必须引用已提供的配置版本，每个新 Agent 必须绑定到节点。')

    # Reuse the exact profile snapshots that were available to the model.
    for key, revision in sorted((after_refs - before_refs) - added):
        assistant.agents.get(workspace, key, revision)
    current = {(row['id'], row['revision']): row for row in assistant.agents.list(workspace)['agents']}
    with assistant.store.db.tx(read=True) as db:
        for pair in after_refs & before_refs:
            if row := agent_record(db, *pair):
                if row['workspace'] == workspace:
                    current[pair] = row
    current.update({(row['id'], row['revision']): row for row in new})
    checks = assistant.scripts.check(workspace, after, proposed=new)
    previous_check = assistant.scripts.check(workspace, before)
    check_regression = previous_check['execution_available'] and not checks['execution_available']
    result = dict(summary=result['summary'], before=before, after=after, agents=new,
                  bindings=[current[pair] for pair in sorted(after_refs) if pair in current],
                  check=checks, check_regression=check_regression, base_sha256=base['sha256'])
    return dict(result, sha256=digest(canonical(result)))


def references(document):
    return {(node['agent_id'], node['agent_revision']) for node in document_agents(document) if 'agent_id' in node}


def applied(db, turn, request_sha=None):
    raw = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (APPLICATION, turn['id'])).fetchone()
    if raw:
        row = json.loads(raw[0])
        if row['workspace'] != turn['workspace'] or request_sha is not None and row['request_sha256'] != request_sha:
            raise Conflict('本提案已使用其他确认参数。')
        return row['result']
    return None


def apply(assistant, turn, data):
    request_sha = digest(canonical(data))
    with assistant.store.db.tx(read=True) as db:
        if result := applied(db, turn, request_sha):
            return result
    base = turn['base']
    try:
        current = assistant.draft(turn['workspace'], data['draft'])
        if not current or not base or any(current[key] != base[key] for key in ('id', 'revision', 'sha256')):
            raise Conflict('草稿自生成提案后已变化，请重新生成。')
        operation = assistant.native_status(turn)
        if operation['state'] != 'COMPLETED':
            raise Conflict('尚无已核验的原生提案回复。')
        staged = stage(assistant, turn, assistant.inspector.text(operation['reply']))
        if staged.get('sha256') != data['preview_sha256']:
            raise Conflict('画布或 Agent 配置已变化，请重新生成提案。')
        if staged['check_regression'] and data.get('accept_design_only') is not True:
            raise Conflict('此修改会使当前可编译工作流仅能保存为设计。请确认这一变更，或先纠正提案。')
    except (Conflict, Invalid):
        with assistant.store.db.tx(read=True) as db:
            if result := applied(db, turn, request_sha):
                return result
        raise

    workspace = turn['workspace']
    key = base['id'] or digest(canonical(['draft', turn['id']]))[:32]
    key, graph = assistant.store.prepare_save(workspace, key, base['revision'], staged['after'])
    with assistant.store.db.tx() as db:
        if result := applied(db, turn, request_sha):
            return result
        added = {row['id'] for row in staged['agents']}
        for row in staged['bindings']:
            if row['id'] in added:
                continue
            if agent_record(db, row['id'], row['revision']) != row:
                raise Conflict('已绑定的 Agent 配置已变化，请重新生成。')
        for row in staged['agents']:
            assistant.agents.save_prepared(db, row, None)
        draft = assistant.store.db.save_draft_in(db, key, graph, base['revision'], workspace)
        result = dict(draft=draft, check=staged['check'], base_sha256=base['sha256'])
        report = dict(workspace=workspace, request_sha256=request_sha, result=result)
        db.execute('INSERT INTO records VALUES(?,?,?)', (APPLICATION, turn['id'], canonical(report).decode()))
    return result
