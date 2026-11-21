"""Reusable designs in the existing V2 store, with no execution authority."""
import json
import re
import secrets
import time

from orchestration.common import canonical, digest
from studio.graph import Invalid
from studio.server import Conflict
from studio.workflow_library import TEMPLATE_OPERATIONS, template_labels

from workbench.canvas.documents import validate_draft
from workbench.runs.run_inspector import RunInspector

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


def classic_templates():
    start = ('Start', '本次任务', 'start', {})
    output = ('Output', '交付', 'output', {})
    accept = dict(field='result.verdict', op='equals', value='accept')

    def agent(key, label, task, **contract):
        return key, label, 'agent', dict(task_prompt=task, **contract)

    def delivery():
        return agent('Develop', '实现', '实现本次任务并将可核对的成果写入 delivery.txt，申报该文件。',
                     native_output=dict(version=1, kind='result'), artifact_paths=['delivery.txt'])

    def review():
        return agent('Review', '候选审核', '检查候选文件与目标，说明测试证据和待修问题。',
                     native_output=dict(version=1, kind='review'))

    def entry(graph, use, relation, setup, inputs, outputs, scenarios, *, reference=False):
        return dict(graph=graph, use=use, relation=relation, setup=setup, inputs=inputs,
                    outputs=outputs, scenarios=scenarios, reference=reference)

    plan = agent('Plan', '计划', '根据任务与验收标准，明确改动范围及验证步骤。')
    verify = ('Verify', '验证命令', 'tool', dict(tool='command', permission='none', cwd='.', timeout=30,
        argv=['python3', '-c', 'from pathlib import Path; raise SystemExit(0 if Path("delivery.txt").read_text().strip() else 1)']))
    correction = agent('Correct', '纠正', '按上一节点的审核反馈修订工作副本并报告改动。')
    intake = 'result.data.department'
    routing = [
        dict(id=key, label=label, test=dict(field=intake, op='equals', value=value))
        for key, label, value in [('Docs', '文档部门', 'docs'), ('Engineering', '研发部门', 'engineering')]
    ]
    classify = agent('Classify', '分类', '按任务选择 docs、engineering 或 other，填写 result.data.department。',
        native_output=dict(version=1, kind='result'), output_contract=[dict(field=intake, type='string', required=True)])
    docs = agent('DocsTeam', '文档部门', '研读任务，交付文档改动建议。')
    engineering = agent('EngineeringTeam', '研发部门', '根据分类结果提出有测试步骤的研发方案。')
    update = agent('Update', '本轮进展', '结合用户新增任务与明确继承的上轮成果，更新交付。把最新结果写入 progress.txt 并申报。',
                   native_output=dict(version=1, kind='result'), artifact_paths=['progress.txt'])
    return [
        entry(example('双 Agent 顺序研发', [start, delivery(), review(), output]),
              '将实现和独立审核交给不同角色。', '任务 → 实现 → 审核 → 交付',
              '绑定实现和审核 Agent。按项目修改产物路径和节点任务。',
              '需求与验收条件', '候选文件、SHA-256、审核结论', '多 Agent 顺序研发'),
        entry(example('规划、实现、测试与审核', [start, plan, delivery(), verify, review(), output]),
              '为有确定性检查的交付建立逐步核对记录。', '计划 → 实现 → 验证命令 → 审核',
              '绑定计划、实现、审核 Agent。将命令与产物路径改成项目实际配置。',
              '任务与验证标准', '命令退出码、候选文件、审核结果', '规划 → 开发 → 测试 → 审核'),
        entry(example('按审核结论分支', [start, delivery(), review(), ('Condition', '审核通过？', 'condition',
              dict(expression=accept)), output, correction, ('Revised', '修订反馈', 'output', {})],
              [('Start', 'Develop', 'out'), ('Develop', 'Review', 'out'), ('Review', 'Condition', 'out'),
               ('Condition', 'Output', 'true'), ('Condition', 'Correct', 'false'), ('Correct', 'Revised', 'out')]),
              '通过后交付，需要返工时进入纠正节点。', '实现 → 审核 → 通过交付 / 纠正反馈',
              '绑定三个 Agent，填写实际产物路径和纠正范围。',
              '交付需求与明确验收条件', '实际分支、审核结论或纠正反馈', '条件审核与纠正分支'),
        entry(example('分类后交给相关部门', [start, classify, ('Router', '部门路由', 'router', dict(rules=routing)),
              docs, ('DocsOut', '文档结果', 'output', {}), engineering, ('CodeOut', '研发结果', 'output', {}),
              ('Manual', '留待人工分派', 'output', {})],
              [('Start', 'Classify', 'out'), ('Classify', 'Router', 'out'), ('Router', 'DocsTeam', 'Docs'),
               ('DocsTeam', 'DocsOut', 'out'), ('Router', 'EngineeringTeam', 'Engineering'),
               ('EngineeringTeam', 'CodeOut', 'out'), ('Router', 'Manual', 'fallback')]),
              '从工单类别选择唯一部门，保留未命中的分派记录。', '分类 → 文档部门 / 研发部门 / 默认结果',
              '绑定分类和部门 Agent。需要自定义部门时同步更新节点任务、字段规则和路由分支。',
              '待分类任务', '已选分支与部门答复', '分类路由与多个部门'),
        entry(loop_example(), '验证命令失败或审核要求修改时，在限定次数内返工。',
              '整理 → 验证 → 审核 → 接受或返工', '在 Loop 内绑定生产与审核 Agent，配置真实验证命令和迭代上限。',
              '任务、产物路径与验证判据', '每轮文件摘要、命令结论和审核结果', 'Tool 测试失败后的返工、有界 Loop 多轮修复'),
        entry(example('科研调研、分析、审核与汇报', [start,
              agent('Research', '调研', '从用户提供的资料中整理可核验的发现与出处。'),
              agent('Analyze', '分析', '分析证据，说明解释范围与数据缺口。'),
              agent('Review', '证据审核', '核对引文与结论是否一致，标出证据不足处。'),
              agent('Report', '汇报', '综合前三步，整理有来源、结论和局限的研究简报。'), output]),
              '按角色分开收集证据、分析和写作。', '调研 → 分析 → 证据审核 → 汇报',
              '绑定调研、分析、审阅、汇报 Agent。提供可访问的资料和引用要求。',
              '科学问题、范围和资料', '带引用和局限说明的简报', '科研调研 → 分析 → 审核 → 汇报'),
        entry(example('继承前轮成果的进展更新', [start, update, output]),
              '用独立的新任务连续推进同一项目。', '本轮任务 → 更新进展 → 交付 → 显式续接下一轮',
              '绑定进展 Agent。首轮完成后，勾选上一轮可用成果，再输入下一轮任务。',
              '每轮新增任务及选择继承的结果或文件', '分轮保存的进展文件和结构化摘要', '跨任务三轮续接'),
        entry(example('多级控制节点组合', [start, classify,
              ('Router', '一级部门路由', 'router', dict(rules=routing[:1])),
              ('Second', '二级路由', 'router', dict(rules=routing[1:])), output,
              ('DocsOut', '文档结果', 'output', {}), ('Manual', '待分派', 'output', {})],
              [('Start', 'Classify', 'out'), ('Classify', 'Router', 'out'), ('Router', 'DocsOut', 'Docs'),
               ('Router', 'Second', 'fallback'), ('Second', 'Output', 'Engineering'), ('Second', 'Manual', 'fallback')]),
              '比较串联路由的设计。', '一级路由 → 二级路由 → 各自结果',
              '仅供设计，原生 Python 编译器不支持图内多个控制节点。',
              '需要两级分派的任务', '设计参考', '多级控制节点组合', reference=True),
        entry(example('并行 Agent 与汇合', [start, docs, engineering,
              ('Merge', '合并两份交付', 'merge', dict(strategy='all')), output],
              [('Start', 'DocsTeam', 'out'), ('Start', 'EngineeringTeam', 'out'), ('DocsTeam', 'Merge', 'out'),
               ('EngineeringTeam', 'Merge', 'out'), ('Merge', 'Output', 'out')]),
              '比较多个独立角色各自产出后再汇合的设计。', '任务 → 并行部门 → 汇合 → 交付',
              '仅供设计，原生 Python 编译器不支持并行或 Merge。',
              '可独立处理的任务', '设计参考', '分支汇合或并行 Agent', reference=True),
    ]


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
        return dict(templates=[json.loads(raw) for raw, in rows],
                    examples=[sequential_example(), router_example(), loop_example(), starter()],
                    classics=classic_templates())

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
        from workbench.runs.data_handoff import task_literals
        from orchestration.privacy import redact
        inspector = RunInspector(self.store)
        literals = task_literals()
        raw = canonical(graph).decode()
        if inspector.clean(graph) != graph or redact(raw, literals) != raw:
            paths = []

            def locate(value, path):
                if len(paths) >= 8:
                    return
                if isinstance(value, dict):
                    public = inspector.clean(value)
                    for key, item in value.items():
                        location = path + '.' + inspector.text(key)
                        if key not in public:
                            paths.append(location)
                        elif key == 'nodes' and isinstance(item, list):
                            for node in item:
                                locate(node, path + '/节点 ' + inspector.text(node['name']) + ' (' + node['id'] + ')')
                        else:
                            locate(item, location)
                elif isinstance(value, list):
                    for index, item in enumerate(value, 1):
                        locate(item, f'{path}[{index}]')
                elif isinstance(value, str) and (inspector.text(value) != value or redact(value, literals) != value):
                    paths.append(path)

            locate(graph, '画布')
            fields = '、'.join(paths[:8]) or '画布字段'
            raise Invalid(f'模板不能包含以下受保护字段：{fields}。请清理这些字段后再保存。')
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
