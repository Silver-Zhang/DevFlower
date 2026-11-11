"""Typed graph dispatch over the V2 run, activation and event records."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time

from orchestration.common import Blocked, canonical, digest
from orchestration.process import group_alive
from studio.agent_sessions import safe_text
from studio.graph import Invalid
from studio.workbench_data import public_data
from studio.workbench_scheduler import Scheduler, merge_output, writes

from .designer_graph import condition, handoff, input_value, output_node, select_branch
from .data_handoff import verify_artifacts
from .run_inspector import RunInspector

TERMINAL = {'COMPLETED', 'FAILED', 'SKIPPED', 'CANCELLED', 'RECOVERY_REQUIRED'}
PENDING = {'PENDING', 'READY', 'WAITING'}


def activation_key(run, path, iteration_path):
    return digest(canonical([run, path, iteration_path]))


def choose_inputs(incoming, upstream, strategy=None):
    def eligible(edge):
        row = upstream[edge['source']]
        port = edge.get('source_port')
        return row['state'] == 'COMPLETED' and row.get('selected_port', port) == port

    if strategy == 'any':
        for edge in incoming:
            row = upstream[edge['source']]
            if row['state'] not in TERMINAL:
                return [row['node']], [], None
            if eligible(edge):
                return [], [edge], None
        return [], [], 'ANY 汇合没有成功的上游输入。'

    waiting = [upstream[edge['source']]['node'] for edge in incoming
               if upstream[edge['source']]['state'] not in TERMINAL]
    if waiting:
        return waiting, [], None
    chosen = [edge for edge in incoming if eligible(edge)]
    if any(upstream[edge['source']]['state'] in ('FAILED', 'CANCELLED') for edge in incoming):
        return [], [], '上游未成功完成。'
    if strategy == 'all' and len(chosen) != len(incoming):
        return [], [], 'ALL 汇合缺少已选中的上游输入。'
    if incoming and not chosen:
        return [], [], '此分支未被选中。'
    return [], chosen, None


class DesignerScheduler(Scheduler):
    def run_graph(self):
        try:
            self.rt.check_operator()
            run = self.controls()
            self.workspace = run.get('workspace')
            resumed = run.get('worker_generation', 0) > 0
            initial_state = 'PAUSED' if run.get('paused') else 'WAITING_CONFIRMATION' if run.get('waiting_approval') else 'RUNNING'
            self.db.change('run', self.run, {'state': initial_state},
                           event={'kind': 'designer-worker-reentered' if resumed else 'run-started'})
            self.active = {}
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='df-v2-agent') as self.pool:
                seed = {'answer': run['task'], 'result': {'task': run['task']}}
                while True:
                    control = self.controls()
                    self.collect()
                    if control.get('stop_reason'):
                        raise Blocked(control['stop_reason'])
                    if control.get('waiting_approval'):
                        self.advance_approval(control['waiting_approval'])
                    elif not control.get('paused'):
                        done, output = self.tick(run['graph'], '', [], seed)
                        if done:
                            if output is None:
                                raise Blocked('没有可达的成功输出。')
                            break
                    time.sleep(.05)
            failures = [row['id'] for row in self.db.list('activation')
                        if row['run'] == self.run and row['state'] == 'FAILED']
            self.finish_run('COMPLETED', output=output, partial_failures=failures)
        except (Blocked, Invalid, OSError, ValueError, KeyError) as failure:
            self.finish_failure(failure)
        except Exception as failure:
            from studio.workbench_failures import abort_unexpected
            abort_unexpected(self.rt, self.run, failure)
            raise

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
        self.finish_run(state, reason=safe_text(str(failure)), waiting_approval=None)

    def collect(self):
        for key, (future, _) in list(self.active.items()):
            if future.done():
                future.result()
                del self.active[key]
                if self.db.get('activation', key)['state'] == 'RECOVERY_REQUIRED':
                    raise Blocked('存在未确认的 Provider 效果，已停止后续调度。')

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
                                            if 'decision' in updates else {}),
                                         **{key: updates[key] for key in ('loop_round', 'loop_exit', 'merge_policy',
                                            'merge_activations', 'gate_verdict', 'verification_sha256',
                                            'candidate_sha256', 'passed') if key in updates}))

    def ready(self, row, node, inputs, expected):
        now = time.time()
        with self.db.tx() as tx:
            current = json.loads(tx.execute("SELECT data FROM records WHERE kind='activation' AND id=?",
                                            (row['id'],)).fetchone()[0])
            if current['state'] not in ('PENDING', 'WAITING') or current['id'] != row['id']:
                raise Blocked('激活状态已变化，未重复准备交接。')
            current.update(state='READY', waiting=[], inputs=inputs, expected=expected, received=len(inputs))
            tx.execute("UPDATE records SET data=? WHERE kind='activation' AND id=?",
                       (canonical(current).decode(), row['id']))
            event = dict(run=self.run, node=row['node'], activation=row['id'], iteration_path=row['iteration_path'], at=now)
            tx.execute('INSERT INTO events(run,data) VALUES(?,?)', (self.run, canonical(dict(
                event, kind='node-ready', state='READY')).decode()))
            for item in inputs:
                data = dict(event, **item, kind='handoff-ready' if node['kind'] == 'agent' else 'handoff-received')
                data['activation'] = item['activation']
                tx.execute('INSERT INTO events(run,data) VALUES(?,?)', (self.run, canonical(data).decode()))
        return current

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
            if state == 'RUNNING' and node['kind'] == 'loop':
                self.advance_loop(node, row, prefix)
                continue
            if state == 'WAITING_CONFIRMATION' and node['kind'] == 'human':
                continue
            if state in ('RUNNING', 'STARTING'):
                if row['id'] not in self.active:
                    raise Blocked('执行中的激活缺少当前所有权；不会重发。')
                continue
            if state not in PENDING:
                raise Blocked('当前激活状态无法安全调度。')
            if state != 'READY':
                incoming = [edge for edge in graph['edges'] if edge['target'] == ident]
                waiting, eligible, reason = choose_inputs(incoming, rows, node.get('strategy'))
                if waiting:
                    if state != 'WAITING' or waiting != row.get('waiting'):
                        self.transition(row, 'WAITING', waiting=waiting, expected=len(incoming))
                    continue
                if reason:
                    self.transition(row, 'SKIPPED', reason=reason, finished_at=time.time())
                    continue
                try:
                    inputs = self.inputs(row, eligible, rows)
                except (Invalid, ValueError, Blocked) as failure:
                    self.transition(row, 'FAILED', event_kind='handoff-contract-failed',
                                    reason=safe_text(str(failure)), finished_at=time.time())
                    continue
                row = self.ready(row, node, inputs, len(incoming))
            if node['kind'] == 'agent' and not self.can_dispatch(node):
                continue
            inputs = row['inputs']
            if node['kind'] == 'human':
                self.begin(row, approval=node)
                return False, None
            started = self.begin(row)
            if not started:
                return False, None
            if node['kind'] == 'loop':
                self.transition(started, 'RUNNING', loop_round=0, loop_seed=input_value(inputs, seed), loop_results=[])
            elif node['kind'] == 'agent':
                self.active[row['id']] = (self.pool.submit(self.execute, node, started, inputs, prefix, seed), node)
            else:
                self.execute(node, started, inputs, prefix, seed)
        return False, None

    def begin(self, row, approval=None):
        with self.db.tx() as tx:
            run = self.record(tx, 'run', self.run)
            if any(run.get(key) for key in ('paused', 'waiting_approval', 'cancel_requested', 'stop_reason')):
                return None
            for key in self.active:
                if self.record(tx, 'activation', key)['state'] == 'RECOVERY_REQUIRED':
                    raise Blocked('存在未确认的 Provider 效果，已停止后续调度。')
            current = self.record(tx, 'activation', row['id'])
            if current != row or row['state'] != 'READY':
                raise Blocked('激活状态已变化，未重复调度。')
            now = time.time()
            current.update(state='STARTING', started_at=now)
            event = 'node-starting'
            if approval is not None:
                current.update(state='WAITING_CONFIRMATION', question=approval['question'], policy=approval['policy'],
                               approval_deadline=now + approval['timeout'])
                run.update(state='WAITING_CONFIRMATION', waiting_approval=row['id'])
                self.write(tx, 'run', run)
                event = 'human-confirmation-required'
            self.write(tx, 'activation', current)
            self.control_event(tx, current, event, now)
        return current

    @staticmethod
    def record(tx, kind, key):
        return json.loads(tx.execute('SELECT data FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()[0])

    @staticmethod
    def write(tx, kind, row):
        tx.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (canonical(row).decode(), kind, row['id']))

    def control_event(self, tx, row, kind, now, **details):
        tx.execute('INSERT INTO events(run,data) VALUES(?,?)', (self.run, canonical(dict(
            kind=kind, run=self.run, node=row['node'], activation=row['id'], iteration_path=row['iteration_path'],
            state=row['state'], at=now, **details)).decode()))

    def advance_approval(self, key):
        failure = None
        with self.db.tx() as tx:
            run = self.record(tx, 'run', self.run)
            row = self.record(tx, 'activation', key)
            if run.get('cancel_requested'):
                return
            if run.get('waiting_approval') != key or row['state'] != 'WAITING_CONFIRMATION':
                raise Blocked('审批检查点与运行身份不同。')
            decision = row.get('approval_decision')
            now = time.time()
            if not decision and now < row['approval_deadline']:
                return
            if decision and (not isinstance(decision, dict) or decision.get('decision') not in ('approve', 'reject')
                             or (decision.get('actor'), decision.get('run'), decision.get('activation')) != (
                                 f'local-user:{os.geteuid()}', self.run, key)
                             or decision.get('definition_id') != run['definition_id']
                             or decision.get('workspace') != run['workspace']
                             or type(decision.get('at')) not in (float, int)
                             or not row['started_at'] <= decision['at'] < row['approval_deadline']):
                raise Blocked('持久审批记录与本机操作或激活身份不同。')
            if decision and decision['decision'] == 'approve':
                # Only persisted, authenticated human decisions can unblock a run.
                output = input_value(row['inputs'], {'answer': '', 'result': {}})
                row.update(state='COMPLETED', output=public_data(output), selected_port='approved', finished_at=now)
                event = 'human-approved'
            else:
                failure = '用户拒绝审批，已停止后续调度。' if decision else '人工审批已超时，已停止后续调度。'
                row.update(state='FAILED', reason=failure, finished_at=now)
                run['stop_reason'] = failure
                event = 'human-rejected' if decision else 'human-timeout'
            run.update(waiting_approval=None, state='PAUSED' if run.get('paused') else 'RUNNING')
            self.write(tx, 'activation', row)
            self.write(tx, 'run', run)
            self.control_event(tx, row, event, now, approval_decision=decision)
        if failure:
            raise Blocked(failure)

    def advance_loop(self, node, row, prefix):
        results = row['loop_results']
        round_number = row['loop_round']
        if round_number == len(results):
            if results and (results[-1]['decision']['matched'] or round_number == node['max_rounds']):
                exit_reason = 'condition' if results[-1]['decision']['matched'] else 'limit'
                output = public_data(dict(row['loop_seed'], loop={'rounds': results, 'exit': exit_reason}))
                if node.get('development') and exit_reason=='limit':
                    reason = '研发循环已达轮次上限，交付 Gate 未给出 ACCEPT。'
                    self.transition(row, 'FAILED', event_kind='loop-limit-reached', output=output,
                                    loop_exit=exit_reason, reason=reason, finished_at=time.time())
                    raise Blocked(reason)
                self.transition(row, 'COMPLETED', event_kind='loop-completed', output=output,
                                loop_exit=exit_reason, finished_at=time.time())
                return
            round_number += 1
            row = self.transition(row, 'RUNNING', event_kind='loop-round-started', loop_round=round_number)
        if round_number != len(results) + 1 or round_number > node['max_rounds']:
            raise Blocked('循环检查点轮次无效。')
        path = [*row['iteration_path'], round_number]
        done, output = self.tick(node['body'], prefix + node['id'] + '/', path, row['loop_seed'])
        if not done:
            return
        try:
            if output is None:
                raise Invalid('循环子图没有成功输出。')
            decision = condition(output, node['until'])
        except Invalid as failure:
            self.transition(row, 'FAILED', reason=safe_text(str(failure)), finished_at=time.time())
            return
        record = dict(round=round_number, iteration_path=path, decision=decision,
                      input_sha256=digest(canonical(row['loop_seed'])), output_sha256=digest(canonical(output)))
        self.transition(row, 'RUNNING', event_kind='loop-round-completed', loop_seed=output,
                        loop_results=[*results, record], loop_round=round_number)

    def can_dispatch(self, node):
        return len(self.active) < 2 and not any(writes(other) for _, other in self.active.values()) and (
            not writes(node) or not self.active)

    def inputs(self, row, edges, upstream):
        values = []
        for edge in edges:
            source = upstream[edge['source']]
            output = source['output']
            try:
                if edge['transfer']=='artifacts':
                    verify_artifacts(self.rt.store,self.workspace,self.run,source,output.get('artifacts'))
                elif output.get('artifacts'):
                    output = dict(output,artifacts=[])
                if edge['transfer']=='fields':
                    output = RunInspector(self.rt.store).clean(output)
                content, evidence = handoff(output, edge, control=row['kind'] != 'agent')
            except (Invalid,ValueError,Blocked) as error:
                raise Invalid('连线 '+edge['id']+'：'+str(error)) from error
            content_hash = digest(canonical(content))
            values.append(dict(edge=edge['id'], source=source['node'], iteration=source['iteration'],
                               iteration_path=source['iteration_path'], activation=source['id'],
                               source_name=source['name'], target_name=row['name'], content=content,
                               source_round=source['iteration_path'], target_round=row['iteration_path'],
                               target_activation=row['id'], target_node=row['node'],
                               content_sha256=content_hash, source_content_sha256=content_hash, fields=edge['transfer'],
                               handoff_id=digest(canonical([self.run, edge['id'], source['id'], row['id']])),
                               received_at=time.time(), **evidence))
        return values

    def execute(self, node, row, inputs, prefix, seed):
        try:
            evidence = {}
            event_kind = None
            if node['kind'] == 'agent':
                output = self.executor(node, row['id'], inputs, prefix, row['iteration'])
            elif node['kind'] == 'start':
                output = dict(seed)
                if 'initial_data' in node:
                    output['initial_data' if row['iteration_path'] else 'result'] = node['initial_data']
                if node.get('input_instructions'):
                    output['instructions'] = node['input_instructions']
            elif node['kind'] == 'tool':
                output, event_kind, evidence = self.execute_tool(node, row)
            elif node['kind'] == 'result':
                output = output_node(node, input_value(inputs, seed))
                if node.get('output_instructions'):
                    evidence['output_instructions'] = safe_text(node['output_instructions'])
            elif node['kind'] == 'merge':
                output = merge_output([item for item in inputs if item['fields'] != 'none'])
                evidence = dict(merge_policy=node['strategy'], merge_activations=[item['activation'] for item in inputs])
                event_kind = 'merge-completed'
            elif node['kind'] in ('condition', 'router', 'validation'):
                output = input_value(inputs, seed)
                port, decision, event_kind = select_branch(node, output)
                evidence = dict(selected_port=port, decision=decision)
            else:
                raise Invalid('冻结的节点类型无法执行。')
            current = self.db.get('activation', row['id'])
            contract = current.get('data_contract')
            if contract and contract['state']=='FAILED':
                self.transition(current,'FAILED',event_kind='output-contract-failed',output=public_data(output),
                                reason=contract['reason'],finished_at=time.time())
                return
            self.transition(current, 'COMPLETED', event_kind=event_kind, output=public_data(output),
                            finished_at=time.time(), **evidence)
        except Exception as failure:
            current = self.db.get('activation', row['id'])
            uncertain = current.get('effect_uncertain') or current.get('process') and group_alive(current['process'])
            stopped = self.db.get('run', self.run).get('cancel_requested')
            state = 'RECOVERY_REQUIRED' if uncertain else 'CANCELLED' if stopped else 'FAILED'
            self.transition(current, state, reason=safe_text(str(failure)), finished_at=time.time())

    def execute_tool(self, node, row):
        kind = node.get('tool_type')
        if kind in ('verify-json','delivery-gate'):
            from .development_evidence import delivery_gate, verify_candidate
            if kind=='verify-json':
                output = verify_candidate(self.rt,self.run,row,node)
                event = 'development-verified'
            else:
                output = delivery_gate(self.rt,self.run,row,node)
                event = 'delivery-gate-decided'
            result = output['result']
            evidence = {key:result[key] for key in ('gate_verdict','verification_sha256','candidate_sha256','passed')
                        if key in result}
            return output, event, evidence
        from pathlib import Path
        from orchestration.privacy import credentials, redact
        from .project_files import read_project
        value = read_project(self.rt.store, self.workspace, node['path'], preview=True)
        text = redact(value['text'], credentials(Path.home()))
        return {'answer':text, 'result':{'text':text,'path':value['path'],'readonly':True}}, None, {}

    def combine_outputs(self, results):
        return {'answer': '\n\n'.join(str(result.get('answer', '')) for result in results),
                'result': {'outputs': results}} if results else None
