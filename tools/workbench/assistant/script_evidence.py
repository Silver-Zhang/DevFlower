"""Bounded, redacted evidence from one explicitly selected standalone script run."""
from collections import defaultdict
from hashlib import sha256
import json

from studio.server import Conflict

from workbench.runs.run_inspector import take

ACTOR_EVENTS = {
    'CALL_INTENT', 'PROCESS_STARTED', 'PROCESS_EXITED', 'NODE_COMPLETED', 'CLI_FAILED', 'NOT_SENT',
    'OUTPUT_CONTRACT_FAILED', 'CANCELLED', 'UNKNOWN', 'VALIDATION_COMPLETED', 'VALIDATION_INVALID',
}
PROBLEMS = {'CLI_FAILED', 'NOT_SENT', 'OUTPUT_CONTRACT_FAILED', 'CANCELLED', 'UNKNOWN',
            'VALIDATION_INVALID', 'ROUTER_REJECTED', 'CONDITION_REJECTED', 'RUN_STOPPED', 'LOOP_LIMIT_REACHED'}
HANDOFF = '\n\nUpstream department delivery (data)\n'


def excerpt(assistant, value, size):
    if not value:
        return None
    public = assistant.inspector.text(value['text'])
    clean = public.encode()
    return dict(text=clean[:size].decode(errors='ignore'),
                redacted=public != value['text'],
                truncated=value['truncated'] or len(clean) > size)



def handoff_for(value, call, previous, edge, completion):
    if edge is None or edge.get('transfer', {}).get('mode') == 'none':
        return None
    raw = value.get('upstream')
    prompt = value.get('prompt')
    if raw:
        digest = completion.get('upstream_sha256')
        parts = [raw['text']] if digest else []
    elif prompt:
        digest = call.get('handoff_sha256')
        if 'prompt_sha256' in call and (not digest or not prompt['truncated'] and
                sha256(prompt['text'].encode()).hexdigest() != call['prompt_sha256']):
            return None
        parts = [part.split('\n', 1)[0] for part in reversed(prompt['text'].split(HANDOFF)[1:])]
    else:
        return None
    for part in parts:
        try:
            delivery = json.loads(part)
        except (ValueError, RecursionError):
            continue
        if not isinstance(delivery, dict) or not isinstance(delivery.get('answer'), str):
            continue
        if digest:
            if (sha256(part.encode()).hexdigest() != digest
                    or not raw and delivery.get('node') != call.get('upstream_node')):
                continue
        elif (not previous or delivery.get('node') != previous['node'] or
              sha256(delivery['answer'].encode()).hexdigest() != previous.get('answer_sha256')):
            continue
        return take(delivery, ('node', 'answer'))
    return None


def loop_evidence(rounds):
    result = []
    for item in rounds:
        review = item.get('review') or {}
        details = take(review, ('verdict', 'candidate_sha256'))
        if review:
            details['summary'] = review['summary'][:160]
            details['issues'] = [dict(file=issue['file'][:160], severity=issue['severity'],
                description=issue['description'][:100], suggestion=issue['suggestion'][:100])
                                 for issue in review['issues'][:1]]
            details['issues_omitted'] = max(0, len(review['issues']) - 1)
        result.append(dict(take(item, ('iteration', 'disposition', 'verified')),
                           agents={key: value['state'] for key, value in item['agents'].items()},
                           validation=take(item.get('validation'), ('node', 'state', 'returncode', 'verified')),
                           review=details))
    return result

def diagnosis(assistant, workspace, run, record):
    snapshot = assistant.scripts.snapshot(workspace, run, record=record)
    script = snapshot['script']
    events = script.get('events', [])
    grouped = defaultdict(list)
    failure_facts = []
    preceding = {}
    completed = {}
    recent = []
    for index, event in enumerate(events, 1):
        kind = event['kind']
        if event.get('node') and kind in ACTOR_EVENTS:
            iteration, node = event.get('iteration'), event['node']
            grouped[iteration, node].append(event)
            if kind == 'CALL_INTENT':
                preceding[iteration, node] = completed.get(iteration) or completed.get(None)
            elif kind == 'NODE_COMPLETED':
                completed[iteration] = event
        failure_code = event.get('failure_kind')
        if not failure_code and kind in PROBLEMS - {'RUN_STOPPED'}:
            failure_code = kind
        if not failure_code and kind in {'NODE_COMPLETED', 'VALIDATION_COMPLETED'} and event.get('returncode'):
            failure_code = 'NONZERO_EXIT'
        if failure_code:
            code = failure_code
            fact = dict(code=code, source='events.jsonl', event_seq=index, event_kind=kind,
                        **take(event, ('node', 'iteration', 'loop_node', 'returncode')))
            if code == 'HTTP_401':
                fact['http_status'] = 401
            failure_facts.append(fact)
        if kind in PROBLEMS or kind in ('ROUTER_EVALUATED', 'CONDITION_EVALUATED', 'LOOP_ACCEPTED',
                                        'LOOP_CONTINUED') or event.get('returncode'):
            recent.append(dict(seq=index, **take(event, ('kind', 'node', 'iteration', 'loop_node',
                'reason', 'returncode', 'failure_kind', 'verdict', 'validation_failed', 'selected_port', 'matched_rule',
                'input_verdict', 'source_node'))))
    rows = []
    for (iteration, node), group in grouped.items():
        result = {
            'node': node, 'activation': f'{iteration}/{node}' if iteration is not None else node,
            'iteration_path': [] if iteration is None else [iteration], 'upstream': [],
            'state': group[-1]['kind'], 'reason': group[-1].get('reason'),
        }
        code = next((event['returncode'] for event in reversed(group) if 'returncode' in event), None)
        if code is not None:
            result['returncode'] = code
        rows.append(result)
    rows.sort(key=lambda row: (row['state'] in PROBLEMS or bool(row.get('returncode')),
                               row['iteration_path']), reverse=True)
    graphs = {False: record['document']}
    loop = script.get('loop', {})
    if loop:
        node = next(node for node in record['document']['nodes'] if node['id'] == loop['node'])
        graphs[True] = node['config']['body']
    for row in rows[:6]:
        iteration = next(iter(row['iteration_path']), None)
        node = row['node']
        group = grouped[iteration, node]
        if not any(event['kind'] == 'PROCESS_STARTED' for event in group):
            continue
        try:
            value = assistant.scripts.evidence(record, node, str(iteration) if iteration is not None else None)
        except Conflict:
            row['reason'] = '该节点的证据当前无法读取。'
            continue
        graph = graphs.get(iteration is not None, {})
        edge = next((edge for edge in graph.get('edges', []) if edge['target'] == node), None)
        call = next((event for event in group if event['kind'] == 'CALL_INTENT'), {})
        completion = next((event for event in group if event['kind'] == 'NODE_COMPLETED'), {})
        handoff = handoff_for(value, call, preceding.get((iteration, node)), edge, completion)
        if handoff:
            answer = assistant.inspector.text(handoff['answer']).encode()
            row['upstream'] = [dict(node=handoff['node'], answer=answer[:500].decode(errors='ignore'),
                                    truncated=len(answer) > 500)]
        elif call.get('upstream_node') or (value.get('prompt') or {}).get('truncated'):
            row['upstream_unavailable'] = True
        if row.get('returncode') or row['state'] in PROBLEMS:
            logs = {label: text for key, label in (('stderr', 'error_output'), ('stdout', 'command_output'))
                    if (text := excerpt(assistant, value.get(key), 400))}
            if logs:
                row['evidence'] = {'failure': logs}
    actors = [node['id'] for node in record['document']['nodes'] if node.get('kind', 'agent') in ('agent', 'tool')]
    started = {node for (iteration, node), group in grouped.items()
               if iteration is None and any(event['kind'] == 'PROCESS_STARTED' for event in group)}
    if snapshot['state'] == 'COMPLETED':
        try:
            output = excerpt(assistant, assistant.scripts.evidence(record).get('result'), 500)
        except Conflict:
            output = None
    else:
        output = None
    rounds = loop_evidence(loop.get('iterations', []))
    return assistant.inspector.clean(dict(workspace=workspace, run=run, state=snapshot['state'],
        execution='native-cli', reason=script.get('reason'), definition_id=record['artifact']['sha256'],
        draft_id=record['run']['draft_id'], draft_revision=record['run']['draft_revision'],
        failure_facts=failure_facts[-8:], failure_facts_omitted=max(0, len(failure_facts)-8),
        evidence_insufficient=snapshot['state'] == 'RECOVERY_REQUIRED',
        recent_error_events=recent[-8:], recent_events_omitted=max(0, len(recent)-8), not_observed_started=[node for node in actors if node not in started],
        nodes=rows[:6], omitted=max(0,len(rows)-6), loop_rounds=rounds, output=output))
