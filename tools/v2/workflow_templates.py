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


def starter():
    nodes = [dict(id='Start', name='需求', kind='start', position={'x': 80, 'y': 140},
                  config_version=1, config={}),
             dict(id='Agent', name='Agent', kind='agent', position={'x': 400, 'y': 140}),
             dict(id='Output', name='交付结果', kind='output', position={'x': 720, 'y': 140},
                  config_version=1, config={'format': 'text'})]
    edges = [dict(id='Input', source='Start', target='Agent', source_port='out', target_port='in'),
             dict(id='Result', source='Agent', target='Output', source_port='out', target_port='in')]
    return validate_draft(dict(version=1, name='需求、Agent 与结果', nodes=nodes, edges=edges))


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
        return dict(templates=[json.loads(raw) for raw, in rows], examples=[starter()])

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
