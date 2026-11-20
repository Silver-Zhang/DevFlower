"""Stage native canvas proposals and commit their explicit application as one transaction."""
import json

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict

from devflower_export import agent_record, document_agents
from .assistant_proposals import parse_envelope, proposal
from .canvas_structure import ID, fields, node_kind, text
from .designer_graph import document_nodes
from .development_graph import configured_tool
from .script_runs import EXECUTION

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

def stage(assistant, turn, reply):
    value = parse_envelope(reply)
    if isinstance(value, dict) and set(value) == {'answer'}:
        text(value['answer'], 12000, '助手回答')
        if not value['answer'].strip():
            raise Invalid('助手回答为空。')
        return {'answer': value['answer']}
    base = turn['base']
    if not base:
        raise Invalid('打开可编辑草稿后再请求工作流修改。')
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
    if not legacy:
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
    result = dict(summary=result['summary'], before=before, after=after, agents=new,
                  bindings=[current[pair] for pair in sorted(after_refs) if pair in current],
                  check=checks, base_sha256=base['sha256'])
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
