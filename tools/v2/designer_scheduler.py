"""Typed graph dispatch over the V2 run, activation and event records."""
from concurrent.futures import ThreadPoolExecutor
import json
import time

from orchestration.common import Blocked, canonical, digest
from orchestration.process import group_alive
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.workbench_data import public_data
from studio.workbench_scheduler import Scheduler, writes

from .designer_graph import handoff, input_value, output_node, select_branch

TERMINAL = {'COMPLETED', 'FAILED', 'SKIPPED', 'CANCELLED', 'RECOVERY_REQUIRED'}
PENDING = {'PENDING', 'READY', 'WAITING'}


def activation_key(run, path, iteration_path):
    return digest(canonical([run, path, iteration_path]))


class DesignerScheduler(Scheduler):
    def run_graph(self):
        try:
            self.rt.check_operator()
            run = self.controls()
            resumed = run.get('worker_generation', 0) > 0
            self.db.change('run', self.run, {'state': 'RUNNING'},
                           event={'kind': 'designer-worker-reentered' if resumed else 'run-started'})
            self.active = {}
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='df-v2-agent') as self.pool:
                seed = {'answer': run['task'], 'result': {'task': run['task']}}
                while True:
                    control = self.controls()
                    self.collect()
                    if not control.get('paused'):
                        done, output = self.tick(run['graph'], '', [], seed)
                        if done:
                            if output is None:
                                raise Blocked('没有可达的成功输出。')
                            break
                    time.sleep(.05)
            self.finish_run('COMPLETED', output=output)
        except (Blocked, Invalid, OSError, ValueError, KeyError) as failure:
            self.finish_failure(failure)

    def finish_run(self, state, **data):
        with self.db.tx() as tx:
            run = json.loads(tx.execute("SELECT data FROM records WHERE kind='run' AND id=?", (self.run,)).fetchone()[0])
            if state != 'RECOVERY_REQUIRED' and run.get('cancel_requested'):
                state = 'CANCELLED'
                data.pop('output', None)
            run.update(state=state, finished_at=time.time(), **data)
            tx.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (canonical(run).decode(), self.run))
            tx.execute('INSERT INTO events(run,data) VALUES(?,?)', (self.run, canonical(dict(
                kind='run-finished', run=self.run, state=state, at=time.time())).decode()))

    def finish_failure(self, failure):
        run = self.db.get('run', self.run)
        rows = [row for row in self.db.list('activation') if row['run'] == self.run]
        uncertain = any(row['state'] == 'RECOVERY_REQUIRED' or row.get('effect_uncertain') or
                        row.get('process') and group_alive(row['process']) for row in rows)
        state = 'RECOVERY_REQUIRED' if uncertain else 'CANCELLED' if run.get('cancel_requested') else 'FAILED'
        for row in rows:
            if row['state'] not in TERMINAL:
                if row.get('effect_uncertain') or row.get('process') and group_alive(row['process']):
                    self.transition(row, 'RECOVERY_REQUIRED', reason='原生效果待核验。')
                else:
                    self.transition(row, 'CANCELLED' if state == 'CANCELLED' else 'SKIPPED', reason='运行已停止调度。')
        self.finish_run(state, reason=safe_text(str(failure)))

    def collect(self):
        for key, (future, _) in list(self.active.items()):
            if future.done():
                future.result()
                del self.active[key]

    def graph_rows(self, graph, prefix, iteration_path):
        iteration = iteration_path[-1] if iteration_path else 0
        keys = {node['id']: activation_key(self.run, prefix + node['id'], iteration_path) for node in graph['nodes']}
        with self.db.tx(read=True) as tx:
            placeholders = ','.join('?' for _ in keys)
            saved = dict(tx.execute("SELECT id,data FROM records WHERE kind='activation' AND id IN (" +
                                    placeholders + ")", tuple(keys.values())))
        if len(saved) != len(keys):
            with self.db.tx() as tx:
                for node in graph['nodes']:
                    ident, kind = node['id'], node['kind']
                    key, path = keys[ident], prefix + ident
                    if key in saved:
                        continue
                    row = dict(id=key, run=self.run, node=path, iteration=iteration, round=iteration or 1,
                               iteration_path=iteration_path, attempt=1, state='PENDING', kind=kind, name=node['name'])
                    raw = canonical(row).decode()
                    inserted = tx.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)', ('activation', key, raw))
                    if inserted.rowcount != 1:
                        raise Blocked('激活已被另一调度器占用。')
                    tx.execute('INSERT INTO events(run,data) VALUES(?,?)', (self.run, canonical(dict(
                        kind='node-pending', run=self.run, node=path, activation=key, iteration_path=iteration_path,
                        state='PENDING', at=time.time())).decode()))
                    saved[key] = raw
        rows = {}
        for node in graph['nodes']:
            ident = node['id']
            row = json.loads(saved[keys[ident]])
            if (row.get('run'), row.get('node'), row.get('iteration_path'), row.get('kind')) != (
                    self.run, prefix + ident, iteration_path, node['kind']):
                raise Blocked('持久激活与冻结节点身份不同。')
            rows[ident] = row
        return rows

    def transition(self, row, state, *, event_kind=None, **updates):
        name = {'WAITING_CONFIRMATION': 'human-confirmation-required', 'COMPLETED': 'node-completed',
                'RECOVERY_REQUIRED': 'node-recovery-required'}.get(state, 'node-' + state.lower())
        return self.db.change('activation', row['id'], dict(state=state, **updates), states={row['state']},
                              event=dict(kind=event_kind or name, state=state, activation=row['id'],
                                         iteration_path=row['iteration_path'], **(
                                            {'decision': updates['decision'], 'port': updates['selected_port']}
                                            if 'decision' in updates else {})))

    def tick(self, graph, prefix, iteration_path, seed):
        nodes = {n['id']: n for n in graph['nodes']}
        rows = self.graph_rows(graph, prefix, iteration_path)
        if any(row['state'] == 'RECOVERY_REQUIRED' for row in rows.values()):
            raise Blocked('存在未确认的 Provider 效果，已停止后续调度。')
        if all(row['state'] in TERMINAL for row in rows.values()):
            results = [rows[key]['output'] for key, node in nodes.items()
                       if node['kind'] == 'result' and rows[key]['state'] == 'COMPLETED']
            return True, results[0] if len(results) == 1 else self.combine_outputs(results)
        for ident, node in nodes.items():
            row = rows[ident]
            state = row['state']
            if state in TERMINAL:
                continue
            if state in ('RUNNING', 'STARTING'):
                if row['id'] not in self.active:
                    raise Blocked('执行中的激活缺少当前所有权；不会重发。')
                continue
            if state not in PENDING:
                raise Blocked('当前激活状态无法安全调度。')
            incoming = [edge for edge in graph['edges'] if edge['target'] == ident]
            waiting = [rows[edge['source']]['node'] for edge in incoming if rows[edge['source']]['state'] not in TERMINAL]
            if waiting:
                if state != 'WAITING' or waiting != row.get('waiting'):
                    self.transition(row, 'WAITING', waiting=waiting, expected=len(incoming))
                continue
            eligible = [edge for edge in incoming if rows[edge['source']]['state'] == 'COMPLETED'
                        and rows[edge['source']].get('selected_port', edge.get('source_port')) == edge.get('source_port')]
            failed = [edge for edge in incoming if rows[edge['source']]['state'] in ('FAILED', 'CANCELLED')]
            if failed or incoming and not eligible:
                reason = '上游未成功完成。' if failed else '此分支未被选中。'
                self.transition(row, 'SKIPPED', reason=reason)
                continue
            if node['kind'] == 'agent' and not self.can_dispatch(node):
                continue
            try:
                inputs = self.inputs(row, eligible, rows)
            except (Invalid, ValueError) as failure:
                self.transition(row, 'FAILED', reason=safe_text(str(failure)), finished_at=time.time())
                continue
            if state != 'READY':
                row = self.transition(row, 'READY', waiting=[], inputs=inputs, expected=len(incoming), received=len(inputs))
            started = self.transition(row, 'STARTING', started_at=time.time())
            for item in inputs:
                self.db.event(self.run, 'handoff-ready' if node['kind'] == 'agent' else 'handoff-received',
                              node=started['node'], **item)
            if node['kind'] == 'agent':
                self.active[row['id']] = (self.pool.submit(self.execute, node, started, inputs, prefix, seed), node)
            else:
                self.execute(node, started, inputs, prefix, seed)
        return False, None

    def can_dispatch(self, node):
        return len(self.active) < 2 and not any(writes(other) for _, other in self.active.values()) and (
            not writes(node) or not self.active)

    def inputs(self, row, edges, upstream):
        values = []
        for edge in edges:
            source = upstream[edge['source']]
            content = handoff(source['output'], edge)
            content_hash = digest(canonical(content))
            values.append(dict(edge=edge['id'], source=source['node'], iteration=source['iteration'],
                               iteration_path=source['iteration_path'], activation=source['id'],
                               source_name=source['name'], target_name=row['name'], content=content,
                               source_round=source['iteration_path'], target_round=row['iteration_path'],
                               target_activation=row['id'], target_node=row['node'],
                               content_sha256=content_hash, source_content_sha256=content_hash, fields=edge['transfer'],
                               handoff_id=digest(canonical([self.run, edge['id'], source['id'], row['id']])),
                               received_at=time.time()))
        return values

    def execute(self, node, row, inputs, prefix, seed):
        try:
            evidence = {}
            event_kind = None
            if node['kind'] == 'agent':
                output = self.executor(node, row['id'], inputs, prefix, row['iteration'])
            elif node['kind'] == 'start':
                output = dict(seed, result=node['initial_data']) if 'initial_data' in node else dict(seed)
                if node.get('input_instructions'):
                    output['instructions'] = node['input_instructions']
            elif node['kind'] == 'result':
                output = output_node(node, input_value(inputs, seed))
            elif node['kind'] in ('condition', 'router', 'validation'):
                output = input_value(inputs, seed)
                port, decision, event_kind = select_branch(node, output)
                evidence = dict(selected_port=port, decision=decision)
            else:
                raise Invalid('冻结的节点类型无法执行。')
            current = self.db.get('activation', row['id'])
            self.transition(current, 'COMPLETED', event_kind=event_kind, output=public_data(output),
                            finished_at=time.time(), **evidence)
        except Exception as failure:
            current = self.db.get('activation', row['id'])
            uncertain = current.get('effect_uncertain') or current.get('process') and group_alive(current['process'])
            stopped = self.db.get('run', self.run).get('cancel_requested')
            state = 'RECOVERY_REQUIRED' if uncertain else 'CANCELLED' if stopped else 'FAILED'
            self.transition(current, state, reason=safe_text(str(failure)), finished_at=time.time())

    def combine_outputs(self, results):
        return {'answer': '\n\n'.join(str(result.get('answer', '')) for result in results),
                'result': {'outputs': results}} if results else None
