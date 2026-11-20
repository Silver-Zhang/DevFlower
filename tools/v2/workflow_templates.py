"""Reusable designs in the existing V2 store, with no execution authority."""
import json
import re
import secrets
import time

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict
from studio.workflow_library import TEMPLATE_OPERATIONS, template_labels

from .documents import validate_draft
from .run_inspector import RunInspector

KIND = 'workflow-template'
IDENT = re.compile(r'[0-9a-f]{32}')
LIMIT = 100


def reusable(document):
    graph = validate_draft(document)
    for node in graph['nodes']:
        if 'agent_id' in node:
            del node['agent_id'], node['agent_revision']
            node['kind'] = 'agent'
        if node.get('kind') == 'loop' and 'body' in node['config']:
            node['config']['body'] = reusable(node['config']['body'])
    return validate_draft(graph)


def example(name, steps, links=None, *, native=True):
    nodes = []
    for index, (ident, label, kind, settings) in enumerate(steps):
        node = dict(id=ident, name=label, kind=kind,
                    position=dict(x=80 + 305 * (index % 4), y=130 + 185 * (index // 4)))
        node.update(settings if kind == 'agent' else dict(config_version=1, config=settings))
        nodes.append(node)
    if links is None:
        links = [(a['id'], b['id'], 'out') for a, b in zip(nodes, nodes[1:])]
    edges = [dict(id='Edge'+str(index), source=source, target=target, source_port=port, target_port='in')
             for index, (source, target, port) in enumerate(links, 1)]
    return validate_draft(dict(version=1, name=name, nodes=nodes, edges=edges, **({'execution':'native-cli'} if native else {})))


def starter():
    nodes = [dict(id='Start', name='需求', kind='start', position={'x': 80, 'y': 140},
                  config_version=1, config={}),
             dict(id='Agent', name='Agent', kind='agent', position={'x': 400, 'y': 140}),
             dict(id='Output', name='交付结果', kind='output', position={'x': 720, 'y': 140},
                  config_version=1, config={'format': 'text'})]
    edges = [dict(id='Input', source='Start', target='Agent', source_port='out', target_port='in'),
             dict(id='Result', source='Agent', target='Output', source_port='out', target_port='in')]
    return validate_draft(dict(version=1, name='需求、Agent 与结果', nodes=nodes, edges=edges))


def sequential_example():
    return example('双 Agent 顺序协作', [
        ('Start', '需求', 'start', {}),
        ('Agent', '调研', 'agent', dict(task_prompt='调查本次任务，整理可核验的来源和发现。')),
        ('Analysis', '汇总', 'agent', dict(task_prompt='综合上游发现，回答目标并标出证据不足之处。')),
        ('Output', '交付结果', 'output', {})])


def router_example():
    path = 'result.data.needs_followup'
    return example('Router 条件分流', [
        ('Start', '接收问题', 'start', {}),
        ('Agent', '分类', 'agent', dict(
            task_prompt='判断当前问题是否需要跟进。将布尔结果写入 result.data.needs_followup。',
            native_output=dict(version=1, kind='result'),
            output_contract=[dict(field=path, type='boolean', required=True)])),
        ('Router', '是否跟进', 'router', dict(rules=[
            dict(id='Follow', label='需要跟进', test=dict(field=path, op='equals', value=True))])),
        ('Followup', '记录待跟进', 'output', {}),
        ('Done', '直接归档', 'output', {})],
        [('Start', 'Agent', 'out'), ('Agent', 'Router', 'out'),
         ('Router', 'Followup', 'Follow'), ('Router', 'Done', 'fallback')])


def loop_example():
    body = example('整理、验证与审核', [
        ('Start', '接收任务', 'start', {}),
        ('Agent', '整理产物', 'agent', dict(
            task_prompt='根据任务整理交付内容，写入 delivery.txt，并在交付中申报该文件。收到反馈时修订原交付。',
            native_output=dict(version=1, kind='result'), artifact_paths=['delivery.txt'])),
        ('Verify', '验证产物', 'tool', dict(tool='command', permission='none', cwd='.', timeout=30,
            argv=['python3', '-c', 'from pathlib import Path; import sys; sys.exit(0 if Path("delivery.txt").read_text().strip() else 1)'])),
        ('Review', '审核产物', 'agent', dict(task_prompt='依据任务目标审核交付完整性，说明需要修改之处。',
                                             native_output=dict(version=1, kind='review'))),
        ('Output', '交付结果', 'output', {})])
    return example('带验证命令的有界 Loop', [
        ('Start', '需求', 'start', {}),
        ('Loop', '最多三轮核验与返工', 'loop', dict(body=body, max_iterations=3,
            exit_condition=dict(field='result.verdict', op='equals', value='accept'))),
        ('Output', '经过审核的结果', 'output', {})])


class WorkflowTemplates:
    def __init__(self, store):
        self.store = store

    def listing(self, workspace):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as db:
            rows = db.execute("""
                SELECT json_remove(data,'$.graph') FROM records WHERE kind=? AND json_extract(data,'$.workspace')=?
                ORDER BY json_extract(data,'$.updated_at') DESC, id LIMIT ?
            """, (KIND, workspace, LIMIT)).fetchall()
        return dict(templates=[json.loads(raw) for raw, in rows], examples=[sequential_example(), router_example(), loop_example(), starter()])

    @staticmethod
    def metadata(row):
        return {key: value for key, value in row.items() if key != 'graph'}

    def get(self, workspace, key, revision):
        self.store.workspace(workspace)
        with self.store.db.tx(read=True) as db:
            row = self.exact(db, workspace, key, revision)
            if row['collection'] != 'active':
                raise Conflict('请先恢复模板。')
            if digest(canonical(row['graph'])) != row['graph_sha256']:
                raise Conflict('模板内容无法核验。')
            return row

    def exact(self, db, workspace, key, revision):
        if not all(isinstance(value, str) and IDENT.fullmatch(value) for value in (key, revision)):
            raise Invalid('请选择明确的模板版本。')
        record = db.execute('SELECT data FROM records WHERE kind=? AND id=?', (KIND, key)).fetchone()
        row = json.loads(record[0]) if record else {}
        if row.get('workspace') != workspace or row.get('revision') != revision:
            raise Conflict('模板归属或版本已变化，请刷新模板库。')
        return row

    @staticmethod
    def publish(db, row):
        raw = canonical(row).decode()
        db.execute('INSERT INTO records VALUES(?,?,?)', (KIND+'-version', row['id']+':'+row['revision'], raw))
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)', (KIND, row['id'], raw))

    def save(self, data):
        if set(data) != {'workspace', 'document', 'name', 'description', 'id', 'expected'}:
            raise Invalid('请填写模板名称、说明和画布。')
        workspace, key, expected = (data[key] for key in ('workspace', 'id', 'expected'))
        self.store.workspace(workspace)
        name, description = template_labels(data['name'], data['description'])
        if key is None and expected is not None:
            raise Invalid('新模板不能携带其他模板版本。')
        description = description.strip()
        graph = reusable(data['document'])
        graph['name'] = name
        from .data_handoff import task_literals
        from orchestration.privacy import redact
        inspector = RunInspector(self.store)
        literals = task_literals()
        raw = canonical(graph).decode()
        if inspector.clean(graph) != graph or redact(raw, literals) != raw:
            raise Invalid('画布含有受保护内容，请清理后再保存模板。')
        name, description = (inspector.text(redact(value, literals)) for value in (name, description))
        graph = validate_draft(dict(graph, name=name))
        now = time.time()
        with self.store.db.tx() as db:
            if key is not None:
                old = self.exact(db, workspace, key, expected)
                if old['collection'] != 'active':
                    raise Conflict('请先恢复模板。')
            else:
                count, = db.execute("""SELECT count(*) FROM records WHERE kind=?
                    AND json_extract(data,'$.workspace')=?""", (KIND, workspace)).fetchone()
                if count >= LIMIT:
                    raise Conflict('当前工作区已达到模板数量上限。')
                key, old = secrets.token_hex(16), {}
            row = dict(id=key, workspace=workspace, name=name, description=description, graph=graph,
                       favorite=old.get('favorite', True), collection='active', created_at=old.get('created_at', now),
                       updated_at=now, revision=secrets.token_hex(16), graph_sha256=digest(canonical(graph)))
            self.publish(db, row)
        return self.metadata(row)

    def manage(self, data):
        if set(data) != {'workspace', 'id', 'expected', 'operation'} or data['operation'] not in TEMPLATE_OPERATIONS:
            raise Invalid('请选择明确的模板操作。')
        workspace = data['workspace']
        self.store.workspace(workspace)
        with self.store.db.tx() as db:
            row = self.exact(db, workspace, data['id'], data['expected'])
            operation = data['operation']
            if operation in ('favorite', 'unfavorite'):
                if row['collection'] != 'active':
                    raise Conflict('请先恢复模板。')
                row['favorite'] = operation == 'favorite'
            else:
                row['collection'] = 'trash' if operation == 'trash' else 'active'
            row.update(revision=secrets.token_hex(16), updated_at=time.time())
            db.execute('UPDATE records SET data=? WHERE kind=? AND id=?',
                       (canonical(row).decode(), KIND, row['id']))
        return self.metadata(row)
