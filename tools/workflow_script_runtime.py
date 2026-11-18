"""Small standard-library CLI driver embedded in exported workflow scripts.

This module does not import the DevFlower server, SQLite, V2 Runtime or a model
SDK. Native CLI configuration remains authoritative. Logs are local/private;
they can contain project source and native CLI output and are not sanitized.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


from workflow_output_contract import (OUTPUT_BYTES, OutputContractError, candidate_record, output_json,
                                      check_output_fields, output_field, validate_structured_answer)  # EMBED_OUTPUT_CONTRACT


class WorkflowError(RuntimeError):
    pass


class WorkflowCancelled(WorkflowError):
    def __init__(self, signum, *, requested=False):
        self.signum, self.requested = signum, requested
        super().__init__('Cancelled by ' + ('run-directory request' if requested else signal.Signals(signum).name)
                         + '; inspect native logs before starting another run.')


@contextmanager
def _cancellation_signals():
    state, previous = {'signal': None}, {}

    def request(signum, _frame):
        # Do not raise inside Popen before its process handle has been assigned.
        state['signal'] = state['signal'] or signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request)
        yield state
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _check_cancel(context):
    signum = context.get('cancellation', {}).get('signal')
    if signum is not None:
        raise WorkflowCancelled(signum)
    if _cancel_requested(context['directory_fd']):
        raise WorkflowCancelled(signal.SIGTERM, requested=True)


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(value)


def _event(context, kind, **data):
    if 'iteration' in context:
        data.update(iteration=context['iteration'], loop_node=context['loop_node'])
    value = dict(kind=kind, at=datetime.now(timezone.utc).isoformat(), **data)
    with (context['directory'] / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def _script_sha256():
    return sha256(Path(__file__).read_bytes()).hexdigest()


@contextmanager
def _run_directory(directory):
    """Open only the caller-owned, private run directory; never create on read."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        if meta.st_uid != os.geteuid() or (stat.S_IMODE(meta.st_mode) & 0o777) != 0o700:
            raise WorkflowError('Run evidence must be in a private directory owned by this user (0700).')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _evidence_file(directory_fd, relative):
    """Fixed evidence names only; no symlinks, pipes or unbounded metadata reads."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or '..' in parts:
        raise WorkflowError('Invalid evidence path.')
    parent = os.dup(directory_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise WorkflowError('Evidence is not a regular file.')
            yield stream
    finally:
        os.close(parent)


def _evidence_bytes(fd, name):
    with _evidence_file(fd, name) as stream:
        value = stream.read(1_000_001)
    if len(value) > 1_000_000:
        raise WorkflowError('Evidence metadata exceeds the monitor read limit.')
    return value


def _evidence_sha256(fd, name):
    with _evidence_file(fd, name) as stream:
        digest = sha256()
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()



def _cancel_requested(fd):
    try:
        meta = os.stat('cancel.request', dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(meta.st_mode):
        raise WorkflowError('Cancellation request is not a regular file.')
    return True


def review_port(verdict):
    if verdict == 'accept':
        return 'true'
    if verdict == 'needs_correction':
        return 'false'
    raise WorkflowError('Unexpected candidate review verdict.')


def recorded_path(manifest, events):
    agents = [agent['id'] for agent in manifest['agents']]
    condition = manifest.get('condition')
    control = condition or manifest.get('router')
    if not control:
        return agents, None
    branches = control['branches']
    branch_nodes = branch_node_ids(control)
    prefix = [node for node in agents if node not in branch_nodes]
    expected, completed, decision = prefix, [], None
    for event in events:
        kind, node = event['kind'], event.get('node')
        if kind in ('CONDITION_EVALUATED', 'ROUTER_EVALUATED'):
            port = event.get('selected_port')
            event_kind = 'CONDITION_EVALUATED' if condition else 'ROUTER_EVALUATED'
            if (decision or kind != event_kind or node != control['node'] or completed != prefix
                    or port not in branches or (condition and review_port(event['input_verdict']) != port)):
                raise WorkflowError('Branch decision does not match the completed input path.')
            expected = prefix + branches[port][:-1]
            decision = dict(event, branches=branches)
        if kind in ('CALL_INTENT', 'PROCESS_STARTED', 'NODE_COMPLETED') and node in branch_nodes and node not in expected:
            raise WorkflowError('Execution evidence belongs to an unselected branch: ' + node)
        if kind == 'NODE_COMPLETED':
            completed.append(node)
    return expected, decision


def inspect_run(manifest, directory, *, cancel=False, details=False):
    """Observe the same artifact, never replay it. Cancellation is cooperative.

    The live script holds a directory lock. No stored PID is ever signalled;
    after an owner crash an unfinished run remains RECOVERY_REQUIRED, even if
    a native child or remote request might still be running.
    """
    import fcntl
    directory = Path(directory).absolute()
    with _run_directory(directory) as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
        else:
            active = False
            fcntl.flock(fd, fcntl.LOCK_UN)
        try:
            saved = json.loads(_evidence_bytes(fd, 'workflow.json'))
        except (ValueError, UnicodeError, RecursionError):
            raise WorkflowError('Invalid run manifest; execution outcome is unknown.') from None
        if saved != manifest:
            raise WorkflowError('Run belongs to a different workflow artifact.')
        snapshot = dict(status='RECOVERY_REQUIRED', active=active, automatic_retry=False,
                        directory=str(directory), script_sha256=_script_sha256(), completed_nodes=[])
        try:
            raw = _evidence_bytes(fd, 'events.jsonl')
            if not raw.endswith(b'\n'):
                if not active:
                    raise WorkflowError('Incomplete journal; execution outcome is unknown.')
                raw = raw[:raw.rfind(b'\n') + 1]
            events = [json.loads(line) for line in raw.splitlines()]
            if not events or any(not isinstance(row, dict) or not isinstance(row.get('kind'), str) for row in events):
                raise WorkflowError('Missing or malformed execution journal.')
            if events[0]['kind'] != 'RUN_STARTED' or sum(row['kind'] == 'RUN_STARTED' for row in events) != 1:
                raise WorkflowError('Run-start evidence is inconsistent.')
            if events[0].get('script_sha256') != snapshot['script_sha256']:
                # Refuse before accepting a cancellation request for another script.
                raise PermissionError('Script content differs from the artifact that started this run.')
            if details:
                snapshot['events'] = events
            terminal = [row for row in events if row['kind'] in ('RUN_COMPLETED', 'RUN_STOPPED')]
            if terminal and (len(terminal) != 1 or terminal[0] is not events[-1]):
                raise WorkflowError('Terminal journal evidence is inconsistent.')
            if cancel and active and not terminal:
                try:
                    request = os.open('cancel.request', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=fd)
                except FileExistsError:
                    pass  # Repeated requests never restart or signal another process.
                else:
                    os.close(request)
                    os.fsync(fd)
            snapshot['cancel_requested'] = _cancel_requested(fd)
            if 'loop' in manifest:
                inspect_loop(manifest, fd, events, snapshot, terminal[0] if terminal else None)
                return snapshot
            completed = [row for row in events if row['kind'] == 'NODE_COMPLETED']
            condition, router = manifest.get('condition'), manifest.get('router')
            expected, decision = recorded_path(manifest, events)
            if [row.get('node') for row in completed] != expected[:len(completed)]:
                raise WorkflowError('Node-completion evidence is inconsistent.')
            reviews = []
            producer = None
            agents = {agent['id']: agent for agent in manifest['agents']}
            for row in completed:
                agent = agents[row['node']]
                if _evidence_sha256(fd, row['node'] + '/answer.txt') != row.get('answer_sha256'):
                    raise WorkflowError('Recorded node answer has changed: ' + row['node'])
                if spec := agent.get('native_output'):
                    structured = read_structured_evidence(fd, row, 'structured.json', 'structured_sha256')
                    if decision and router and row['node'] == router['source_node']:
                        inputs, match = router_match(structured, router['rules'])
                        if (decision.get('input_sha256') != row['structured_sha256']
                                or decision.get('source_node') != row['node'] or 'matched_rule' not in decision
                                or decision['matched_rule'] != match
                                or decision['selected_port'] != (match or 'fallback')):
                            raise WorkflowError('Router decision does not match the verified structured input.')
                        snapshot['router'] = dict(decision, input_json=json.dumps(inputs, ensure_ascii=False, indent=2))
                    if spec['kind'] == 'review':
                        candidate = read_review_candidate(fd, row, structured, producer)
                        if decision and condition and row['node'] == condition['reviewer']:
                            if (decision.get('candidate_sha256') != candidate['candidate_sha256']
                                    or decision['input_verdict'] != row['verdict']):
                                raise WorkflowError('Condition evidence does not match the verified candidate review.')
                            snapshot['condition'] = decision
                        reviews.append((row['node'], candidate))
                        producer = None
                    elif spec['allowed_paths']:
                        producer = read_structured_evidence(fd, row, 'candidate.json', 'candidate_sha256_evidence')
                    else:
                        producer = None
                else:
                    producer = None
            snapshot['completed_nodes'] = [row['node'] for row in completed]
            if terminal:
                last = terminal[0]
                if last['kind'] == 'RUN_COMPLETED':
                    if snapshot['completed_nodes'] != expected or _evidence_sha256(fd, 'result.txt') != last.get('result_sha256'):
                        raise WorkflowError('Final result is missing or changed.')
                    if (condition or router) and (not decision or last['result_sha256'] != completed[-1]['answer_sha256']):
                        raise WorkflowError('Final result is not bound to a selected branch.')
                    candidates_current = True
                    for node, candidate in reviews:
                        try:
                            [workspace] = [event['workspace'] for event in events if event['kind'] == 'CANDIDATE_WORKSPACE']
                            current = candidate_is_current(workspace, candidate)
                        except OutputContractError:
                            current = False
                        if not current:
                            candidates_current = False
                            if not decision or not condition or node != condition['reviewer']:
                                snapshot['candidate_current'] = False
                                raise WorkflowError('Reviewed candidate no longer matches current files: ' + node)
                    if reviews:
                        snapshot['candidate_current'] = candidates_current
                    snapshot.update(status='COMPLETED', result_file=str(directory / 'result.txt'))
                elif last.get('status') in ('CANCELLED', 'STOPPED'):
                    snapshot.update(status=last['status'], reason=last.get('reason', ''))
                else:
                    raise WorkflowError('Unknown terminal state.')
            elif active:
                snapshot['status'] = 'CANCEL_REQUESTED' if snapshot['cancel_requested'] else 'RUNNING'
            else:
                snapshot['reason'] = 'Script owner is absent without a terminal record; native effects are unknown.'
        except PermissionError:
            raise
        except (WorkflowError, OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            snapshot.update(status='RECOVERY_REQUIRED', reason=str(error))
        return snapshot


def native_command(agent, answer_path):
    """Only transport/model flags; never inject permission bypasses or tool lists."""
    provider = agent['provider']
    if provider == 'codex':
        argv = ['codex', 'exec', '--output-last-message', str(answer_path)]
        if agent['model']:
            argv += ['--model', agent['model']]
        if agent['effort']:
            argv += ['-c', 'model_reasoning_effort=' + json.dumps(agent['effort'])]
        return argv + ['-']
    if provider == 'claude':
        argv = ['claude', '-p', '--output-format', 'json']
        if agent['model']:
            argv += ['--model', agent['model']]
        if agent['effort']:
            argv += ['--effort', agent['effort']]
        if agent.get('max_budget_usd'):
            argv += ['--max-budget-usd', agent['max_budget_usd']]
        return argv
    raise WorkflowError('Unsupported CLI: ' + str(provider))


def _stop(process):
    # The leader may already have exited while its tools are still running.
    # start_new_session gives only this launch's group this process ID.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        process.poll()  # Reap our leader; do not confuse its exit with group exit.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _wait(context, process, timeout):
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        _check_cancel(context)
        if process.poll() is not None:
            return process.returncode
        remaining = deadline - time.monotonic() if deadline is not None else .1
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            process.wait(timeout=min(.1, remaining))
        except subprocess.TimeoutExpired:
            pass


def read_structured_evidence(fd, row, filename, hash_key, *, prefix=''):
    raw = _evidence_bytes(fd, prefix + row['node'] + '/' + filename)
    if sha256(raw).hexdigest() != row.get(hash_key):
        raise WorkflowError('Structured evidence is missing or changed: ' + row['node'])
    return json.loads(raw)


def read_review_candidate(fd, row, structured, producer, *, prefix=''):
    candidate = read_structured_evidence(fd, row, 'candidate-after.json', 'candidate_after_sha256', prefix=prefix)
    if (candidate != producer or row.get('candidate_sha256') != candidate['candidate_sha256']
            or row.get('verdict') != structured['result']['verdict']
            or structured['result']['candidate_sha256'] != candidate['candidate_sha256']):
        raise WorkflowError('Review evidence is inconsistent: ' + row['node'])
    return candidate


def output_instructions(spec, workspace):
    if spec['kind'] == 'review':
        format_example = ('{"result":{"schema_version":1,"verdict":"accept","candidate_sha256":"<exact digest>",'
                          '"summary":"<review summary>","issues":[]},"artifacts":[]}')
        return ('Return only raw JSON, no markdown fences or extra fields. Format: ' + format_example
                + '\nReview the actual supplied candidate files relative to ' + str(workspace)
                + '. Do not modify them. Copy the exact supplied candidate_sha256. '
                'Use accept with an empty issues list or needs_correction with at least one issue. '
                'Each issue must contain only severity (critical, major or minor), file (a candidate path), '
                'description and suggestion.')
    return ('Return only raw JSON, no markdown fences or extra fields. Format: '
            '{"result":{"schema_version":1,"summary":"<summary>","data":{}},"artifacts":[]}'
            '\nAllowed workspace-relative artifact paths: ' + output_json(spec['allowed_paths'])
            + '. When paths are declared, report at least one actual delivery file. '
            'Report no file hashes. Field rules: ' + output_json(spec['fields']))


def write_structured_evidence(directory, filename, value):
    raw = output_json(value)
    _write(directory / filename, raw)
    return sha256(raw.encode('utf-8')).hexdigest()


def candidate_is_current(workspace, candidate):
    paths = [file['path'] for file in candidate['manifest']['files']]
    return candidate_record(workspace, paths) == candidate


def run_validation(context, tool, produced):
    node, candidate = tool['id'], produced['candidate']
    paths = [item['path'] for item in candidate['manifest']['files']]
    if tool['limit'] is not None and len(produced['answer']) > tool['limit']:
        raise WorkflowError('Saved Tool handoff limit exceeded; validation did not run.')
    try:
        before = candidate_record(context['workspace'], paths)
        if before != candidate:
            raise WorkflowError('Candidate changed before validation.')
        cwd = agent_directory(context['workspace'], tool)
    except (OutputContractError, WorkflowError) as error:
        _event(context, 'NOT_SENT', node=node, reason=str(error))
        raise WorkflowError(str(error)) from None
    prefix = f"iterations/{context['iteration']}/{node}"
    directory = context['directory'] / prefix
    directory.mkdir(mode=0o700)
    with observed_process(context, node, tool['argv'], cwd, directory, tool['timeout'], tool=True) as code:
        if code < 0:
            raise WorkflowError(f'Validation terminated by signal {-code}; no retry.')
        _check_cancel(context)
        try:
            after = candidate_record(context['workspace'], paths)
        except OutputContractError:
            after = None
        facts = dict(returncode=code, candidate_sha256_before=before['candidate_sha256'],
                     candidate_sha256_after=after['candidate_sha256'] if after else None)
        if before != after:
            _event(context, 'VALIDATION_INVALID', node=node, **facts)
            raise WorkflowError('Candidate files changed during validation.')
        summaries = {}
        fd = context['directory_fd']
        for channel in ('stdout', 'stderr'):
            relative = prefix + '/' + channel + '.log'
            facts[channel + '_sha256'] = _evidence_sha256(fd, relative)
            with _evidence_file(fd, relative) as stream:
                end = stream.seek(0, os.SEEK_END)
                stream.seek(max(end - 2048, 0))
                summaries[channel] = ('[tail] ' if end > 2048 else '') + stream.read(2048).decode('utf-8', errors='replace')
        _check_cancel(context)
        _event(context, 'VALIDATION_COMPLETED', node=node, **facts)
        return dict(returncode=code, candidate_sha256=before['candidate_sha256'],
                    reason=f'Validation failed (exit {code}).' if code else 'Validation passed.', **summaries)


def inspect_loop(manifest, fd, events, snapshot, terminal):
    loop = manifest['loop']
    developer, reviewer = (agent['id'] for agent in manifest['agents'])
    tool = loop.get('validation', {}).get('id')
    rounds = []
    snapshot['loop'] = dict(node=loop['node'], max_iterations=loop['max_iterations'], iterations=rounds)
    current = None
    candidate = review_answer_sha = None
    for event in events:
        kind = event['kind']
        if kind in ('RUN_STARTED', 'RUN_STOPPED', 'RUN_COMPLETED', 'CANDIDATE_WORKSPACE'):
            continue
        if kind == 'LOOP_ITERATION_STARTED':
            count = len(rounds) + 1
            if count > loop['max_iterations'] or (current and current['disposition'] != 'LOOP_CONTINUED'):
                raise WorkflowError('Unexpected Loop iteration.')
            current = dict(iteration=count, agents={node: {'state': 'NOT_STARTED'} for node in (developer, reviewer)},
                           disposition=None, review=None, completed_nodes=[])
            if tool:
                current['validation'] = dict(node=tool, state='NOT_STARTED')
            rounds.append(current)
            candidate = review_answer_sha = None
        if (not current or type(event.get('iteration')) is not int or event['iteration'] != current['iteration']
                or event.get('loop_node') != loop['node']):
            raise WorkflowError('Loop event has an inconsistent iteration identity.')
        if kind == 'LOOP_ITERATION_STARTED':
            continue
        if current['disposition']:
            raise WorkflowError('Unexpected Loop event after a round decision.')
        validation = current.get('validation')
        failed = bool(validation and validation.get('verified') and validation['state'] == 'FAILED')
        if kind.startswith('LOOP_'):
            verdict = current['review']['verdict'] if current['review'] else None
            if ((not verdict and not failed) or event.get('candidate_sha256') != current['candidate_sha256']
                    or event.get('verdict') != verdict or event.get('validation_failed', False) != failed):
                raise WorkflowError('Loop decision does not match verified validation or review evidence.')
            if kind == 'LOOP_ITERATION_COMPLETED' and not current.get('verified'):
                if (event.get('developer_node'), event.get('reviewer_node')) != (developer, None if failed else reviewer):
                    raise WorkflowError('Loop completion has an inconsistent Agent identity.')
                current['verified'] = True
                continue
            expected = ('LOOP_ACCEPTED' if verdict == 'accept' else
                        'LOOP_LIMIT_REACHED' if len(rounds) == loop['max_iterations'] else 'LOOP_CONTINUED')
            if not current.get('verified') or kind != expected:
                raise WorkflowError('Unexpected Loop decision for the review or iteration bound.')
            current['disposition'] = kind
            continue
        completed = current['completed_nodes']
        node = event.get('node')
        expected_node = developer if not completed else tool if validation and validation['state'] != 'PASSED' else reviewer
        if len(completed) == 2 or failed or node != expected_node:
            raise WorkflowError('Loop node event is out of order.')
        is_tool = bool(tool and node == tool)
        actor = validation if is_tool else current['agents'][node]
        if kind == 'CALL_INTENT':
            if actor['state'] != 'NOT_STARTED':
                raise WorkflowError('Loop Agent has repeated call intent.')
            actor['state'] = 'STARTING'
        elif kind == 'PROCESS_STARTED':
            if actor['state'] != 'STARTING':
                raise WorkflowError('Loop Agent process has no matching call intent.')
            actor['state'] = 'RUNNING'
        elif kind == 'PROCESS_EXITED':
            if actor['state'] != 'RUNNING':
                raise WorkflowError('Loop Agent exit has no matching process start.')
            actor['returncode'] = event['returncode']
            actor['state'] = 'VERIFYING' if event['returncode'] == 0 or (is_tool and event['returncode'] > 0) else 'FAILED'
        elif kind in ('CANCELLED', 'UNKNOWN', 'NOT_SENT', 'CLI_FAILED', 'OUTPUT_CONTRACT_FAILED'):
            actor['state'] = kind if kind in ('CANCELLED', 'UNKNOWN') else 'FAILED'
        elif is_tool and kind in ('VALIDATION_COMPLETED', 'VALIDATION_INVALID'):
            before, after = event['candidate_sha256_before'], event['candidate_sha256_after']
            if (actor['state'] != 'VERIFYING' or actor['returncode'] != event['returncode']
                    or before != current['candidate_sha256'] or (before == after) != (kind == 'VALIDATION_COMPLETED')):
                raise WorkflowError('Validation outcome does not match the process or candidate.')
            actor.update(candidate_sha256_before=before, candidate_sha256_after=after)
            if kind == 'VALIDATION_INVALID':
                actor['state'] = 'INVALID'
            else:
                for channel in ('stdout', 'stderr'):
                    name = f"iterations/{current['iteration']}/{node}/{channel}.log"
                    if _evidence_sha256(fd, name) != event.get(channel + '_sha256'):
                        raise WorkflowError('Validation log has changed: ' + channel)
                actor.update(state='FAILED' if event['returncode'] else 'PASSED', verified=True)
                if event['returncode']:
                    current['agents'][reviewer]['state'] = 'SKIPPED'
        elif not is_tool and kind == 'NODE_COMPLETED':
            prefix = f"iterations/{current['iteration']}/"
            if (actor['state'] != 'VERIFYING'
                    or _evidence_sha256(fd, prefix + node + '/answer.txt') != event.get('answer_sha256')):
                raise WorkflowError('Loop Agent completion evidence has changed.')
            structured = read_structured_evidence(fd, event, 'structured.json', 'structured_sha256', prefix=prefix)
            if node == developer:
                candidate = read_structured_evidence(fd, event, 'candidate.json', 'candidate_sha256_evidence', prefix=prefix)
                frozen = Path(snapshot['directory']).resolve() / prefix / 'candidate-files'
                if not candidate_is_current(frozen, candidate):
                    raise WorkflowError('Stored Loop candidate snapshot has changed.')
                current['candidate_sha256'] = candidate['candidate_sha256']
            else:
                read_review_candidate(fd, event, structured, candidate, prefix=prefix)
                current['review'] = structured['result']
                review_answer_sha = event['answer_sha256']
            actor['state'] = 'COMPLETED'
            completed.append(node)
        else:
            raise WorkflowError('Unsupported Loop evidence event: ' + kind)
    for item in rounds[:-1]:
        item['historical'] = True
    if current and not snapshot['active']:
        actors = list(current['agents'].values()) + ([current['validation']] if tool else [])
        for actor in actors:
            if actor['state'] in ('STARTING', 'RUNNING', 'VERIFYING'):
                actor['state'] = 'UNKNOWN'
    if not terminal:
        if snapshot['active']:
            snapshot['status'] = 'CANCEL_REQUESTED' if snapshot['cancel_requested'] else 'RUNNING'
        else:
            snapshot['reason'] = 'Script owner is absent without a terminal record; native effects are unknown.'
        return
    if terminal['kind'] == 'RUN_COMPLETED':
        if (not current or current['disposition'] != 'LOOP_ACCEPTED'
                or terminal.get('result_sha256') != review_answer_sha
                or _evidence_sha256(fd, 'result.txt') != review_answer_sha):
            raise WorkflowError('Final Loop output is not bound to an accepted round.')
        [workspace] = [event['workspace'] for event in events if event['kind'] == 'CANDIDATE_WORKSPACE']
        try:
            snapshot['candidate_current'] = candidate_is_current(workspace, candidate)
        except OutputContractError:
            snapshot['candidate_current'] = False
        if not snapshot['candidate_current']:
            current['historical'] = True
            raise WorkflowError('Final accepted Loop candidate no longer matches current files.')
        snapshot.update(status='COMPLETED', completed_nodes=[loop['node']],
                        result_file=str(Path(snapshot['directory']) / 'result.txt'))
    elif terminal.get('status') in ('CANCELLED', 'STOPPED'):
        snapshot.update(status=terminal['status'], reason=terminal.get('reason', ''))
    else:
        raise WorkflowError('Unknown terminal state.')


def run_loop(context, manifest, task):
    loop = manifest['loop']
    developer, reviewer = manifest['agents']
    if loop['start_limit'] is not None and len(task) > loop['start_limit']:
        raise WorkflowError('Saved Loop input limit exceeded; no request sent.')
    task = '\n\n'.join(value for value in (loop['body_input'], task) if value)
    if loop['task_limit'] is not None and len(task) > loop['task_limit']:
        raise WorkflowError('Saved body Start limit exceeded; no request sent.')
    (context['directory'] / 'iterations').mkdir(mode=0o700)
    feedback = None
    for iteration in range(1, loop['max_iterations'] + 1):
        _check_cancel(context)
        current = dict(context, iteration=iteration, loop_node=loop['node'])
        (context['directory'] / 'iterations' / str(iteration)).mkdir(mode=0o700)
        _event(current, 'LOOP_ITERATION_STARTED')
        produced = invoke_agent(current, developer, task, feedback=feedback)
        candidate = produced['candidate']
        facts = dict(candidate_sha256=candidate['candidate_sha256'])
        validation = run_validation(current, loop['validation'], produced) if 'validation' in loop else None
        if validation and validation['returncode']:
            facts['validation_failed'] = True
            feedback = validation
        else:
            if validation:
                produced['validation'] = validation
            audited = invoke_agent(current, reviewer, task, produced, loop['review_limit'])
            review = audited['structured']['result']
            try:
                valid = candidate_is_current(context['workspace'], candidate)
            except OutputContractError:
                valid = False
            if not valid:
                raise WorkflowError('Candidate changed before Loop decision.')
            if loop['body_output_limit'] is not None and len(audited['answer']) > loop['body_output_limit']:
                raise WorkflowError('Saved body Output limit exceeded; full answer retained in node logs.')
            facts['verdict'] = review['verdict']
            feedback = {key: review[key] for key in ('summary', 'issues', 'candidate_sha256')}
        _check_cancel(context)
        _event(current, 'LOOP_ITERATION_COMPLETED', **facts, developer_node=developer['id'],
               reviewer_node=None if facts.get('validation_failed') else reviewer['id'])
        if facts.get('verdict') == 'accept':
            if loop['output_limit'] is not None and len(audited['answer']) > loop['output_limit']:
                raise WorkflowError('Saved Loop Output limit exceeded; full answer retained in node logs.')
            _check_cancel(context)
            _event(current, 'LOOP_ACCEPTED', **facts)
            return audited
        if iteration == loop['max_iterations']:
            _event(current, 'LOOP_LIMIT_REACHED', **facts)
            raise WorkflowError(f'Loop reached its {iteration}-iteration limit without acceptance.')
        _check_cancel(context)
        _event(current, 'LOOP_CONTINUED', **facts)


def evaluate_condition(context, condition, review, candidate, limit):
    """Consume the result of a successful native review in this invocation."""
    _check_cancel(context)
    node = condition['node']
    try:
        if limit is not None and len(review['answer']) > limit:
            raise WorkflowError('Saved Condition handoff limit exceeded.')
        result = review['structured']['result']
        if review['node'] != condition['reviewer'] or result['candidate_sha256'] != candidate['candidate_sha256']:
            raise WorkflowError('Condition requires the verified review for this candidate.')
        if not candidate_is_current(context['workspace'], candidate):
            raise WorkflowError('Candidate files changed before Condition evaluation.')
        port = review_port(result['verdict'])
    except (WorkflowError, OutputContractError) as error:
        _event(context, 'CONDITION_REJECTED', node=node, reason=str(error))
        raise WorkflowError(str(error)) from None
    _check_cancel(context)
    _event(context, 'CONDITION_EVALUATED', node=node, input_verdict=result['verdict'],
           selected_port=port, candidate_sha256=candidate['candidate_sha256'])
    return port == 'true'


def router_match(structured, rules):
    # A match never bypasses validation of the fields in later rules.
    check_output_fields(structured, [dict(field=rule['field'], type=rule['type'], required=True)
                                     for rule in rules])
    inputs = {rule['field']: output_field(structured, rule['field']) for rule in rules}
    match = next((rule['id'] for rule in rules if inputs[rule['field']] == rule['value']), None)
    return inputs, match


def evaluate_router(context, router, upstream, limit):
    _check_cancel(context)
    node = router['node']
    try:
        if limit is not None and len(upstream['answer']) > limit:
            raise WorkflowError('Saved Router handoff limit exceeded.')
        if upstream['node'] != router['source_node'] or 'structured' not in upstream:
            raise WorkflowError('Router requires a verified structured upstream result.')
        structured = upstream['structured']
        _, match = router_match(structured, router['rules'])
    except (WorkflowError, OutputContractError) as error:
        _event(context, 'ROUTER_REJECTED', node=node, reason=str(error))
        raise WorkflowError(str(error)) from None
    _check_cancel(context)
    port = match or 'fallback'
    _event(context, 'ROUTER_EVALUATED', node=node, source_node=upstream['node'], matched_rule=match,
           selected_port=port, input_sha256=sha256(output_json(structured).encode('utf-8')).hexdigest())
    return port


def verify_agent_output(context, directory, spec, answer, candidate):
    envelope = validate_structured_answer(answer, spec, candidate)
    workspace = context['workspace']
    delivery, facts = {'structured': envelope}, {}
    if spec['kind'] == 'review':
        observed = candidate_record(workspace, [row['path'] for row in candidate['manifest']['files']])
        facts['candidate_after_sha256'] = write_structured_evidence(directory, 'candidate-after.json', observed)
        if observed != candidate:
            raise OutputContractError('Candidate files changed during review.')
        facts.update(candidate_sha256=observed['candidate_sha256'], verdict=envelope['result']['verdict'])
    elif envelope['artifacts']:
        snapshot = directory.parent / 'candidate-files' if 'iteration' in context else None
        observed = candidate_record(workspace, envelope['artifacts'], snapshot=snapshot)
        facts['candidate_sha256_evidence'] = write_structured_evidence(directory, 'candidate.json', observed)
        facts['candidate_sha256'] = observed['candidate_sha256']
        delivery['candidate'] = observed
    facts['structured_sha256'] = write_structured_evidence(directory, 'structured.json', envelope)
    return delivery, facts


@contextmanager
def observed_process(context, node, argv, cwd, directory, timeout, *, source=subprocess.DEVNULL, tool=False):
    _event(context, 'CALL_INTENT', node=node, cwd=str(cwd), argv=argv)
    process = None
    try:
        with (directory / 'stdout.log').open('xb') as stdout, (directory / 'stderr.log').open('xb') as stderr:
            # Both native CLI and user-configured Tool commands inherit the user's environment.
            _check_cancel(context)
            process = subprocess.Popen(argv, cwd=cwd, stdin=source, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            _event(context, 'PROCESS_STARTED', node=node, pid=process.pid)
            code = _wait(context, process, timeout)
        if tool:
            _stop(process)
        _event(context, 'PROCESS_EXITED', node=node, returncode=code)
        yield code
    except (WorkflowCancelled, KeyboardInterrupt) as error:
        if process is not None:
            _stop(process)
        signum = error.signum if isinstance(error, WorkflowCancelled) else signal.SIGINT
        _event(context, 'CANCELLED', node=node, signal=signal.Signals(signum).name,
               source='request' if getattr(error, 'requested', False) else 'signal',
               outcome='unknown' if process else 'not_sent', automatic_retry=False)
        raise WorkflowCancelled(signum, requested=getattr(error, 'requested', False)) from None
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop(process)
        _event(context, 'UNKNOWN', node=node, reason='Timed out; no automatic retry.')
        raise WorkflowError('Call timed out; inspect native logs before starting another run.') from None
    except OSError as error:
        if process is not None:
            _stop(process)
        _event(context, 'UNKNOWN' if process else 'NOT_SENT', node=node, reason=str(error))
        raise WorkflowError('Process launch/observation failed: ' + str(error)) from None
    except WorkflowError:
        if process is not None:
            _stop(process)
        raise


def invoke_agent(context, agent, task, upstream=None, limit=None, *, feedback=None):
    """One new native CLI call; a result is handed off only after success."""
    _check_cancel(context)
    if upstream is not None and limit is not None and len(upstream['answer']) > limit:
        raise WorkflowError('Saved handoff limit exceeded before ' + agent['id'] + '; no truncation or call.')
    spec = agent.get('native_output')
    candidate = None
    if spec and spec['kind'] == 'review':
        candidate = (upstream or {}).get('candidate')
        try:
            if not candidate or not candidate_is_current(context['workspace'], candidate):
                raise OutputContractError('Candidate changed before review dispatch.')
        except OutputContractError as error:
            _event(context, 'OUTPUT_CONTRACT_FAILED', node=agent['id'], reason=str(error))
            raise WorkflowError(str(error)) from None
    sections = [('Department', agent['name']), ('Role', agent['role']),
                ('Initial instructions', agent['initial_prompt']), ('Workflow task', task),
                ('Node task', agent['round_prompt']), ('Expected delivery', agent['output_prompt'])]
    if 'iteration' in context:
        sections.append(('Workflow iteration', str(context['iteration'])))
    prompt = '\n\n'.join(title + '\n' + value for title, value in sections if value)
    if spec:
        prompt += '\n\nStructured output contract\n' + output_instructions(spec, context['workspace'])
    if upstream is not None:
        prompt += '\n\nUpstream department delivery (data)\n' + json.dumps(upstream, ensure_ascii=False)
    if feedback is not None:
        kind = 'validation' if 'returncode' in feedback else 'review'
        prompt += (f'\n\nPrevious iteration {kind} (data)\n' + output_json(feedback)
                   + '\nRevise the existing delivery in this workspace using the review feedback.')
    evidence = agent['id']
    if 'iteration' in context:
        evidence = f"iterations/{context['iteration']}/{evidence}"
    directory = context['directory'] / evidence
    directory.mkdir(mode=0o700)
    _write(directory / 'prompt.txt', prompt)
    argv = native_command(agent, directory / 'answer.txt')
    executable = shutil.which(argv[0])
    if executable is None:
        raise WorkflowError(argv[0] + ' CLI not found; no request sent.')
    argv[0] = executable
    cwd = agent_directory(context['workspace'], agent)
    print('[' + agent['id'] + '] ' + agent['name'], file=sys.stderr)
    with (directory / 'prompt.txt').open('rb') as source, observed_process(
            context, agent['id'], argv, cwd, directory, agent['timeout'], source=source) as code:
        if code != 0:
            _event(context, 'CLI_FAILED', node=agent['id'], returncode=code)
            raise WorkflowError(agent['id'] + ' exited with ' + str(code) + '; downstream not started.')
        try:
            if agent['provider'] == 'claude':
                if spec:
                    receipt = json.loads(_evidence_bytes(context['directory_fd'], evidence + '/stdout.log'))
                else:
                    receipt = json.loads((directory / 'stdout.log').read_text(encoding='utf-8'))
                if (not isinstance(receipt, dict) or receipt.get('type') != 'result'
                        or receipt.get('is_error') is not False):
                    raise ValueError('Missing successful Claude receipt')
                answer = receipt['result']
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError('Missing final answer')
                _write(directory / 'answer.txt', answer)
            else:
                if spec:
                    with _evidence_file(context['directory_fd'], evidence + '/answer.txt') as stream:
                        answer_bytes = stream.read(OUTPUT_BYTES + 1)
                    if len(answer_bytes) > OUTPUT_BYTES:
                        raise OutputContractError('Structured answer exceeds 80000 UTF-8 bytes.')
                    answer = answer_bytes.decode('utf-8')
                else:
                    answer = (directory / 'answer.txt').read_text(encoding='utf-8')
                if not answer.strip():
                    raise ValueError('Missing final answer')
        except OutputContractError as error:
            _event(context, 'OUTPUT_CONTRACT_FAILED', node=agent['id'], reason=str(error))
            raise WorkflowError(str(error)) from None
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            _event(context, 'UNKNOWN', node=agent['id'], reason='No verified final answer: ' + str(error))
            raise WorkflowError('CLI exited but final answer is unconfirmed; no automatic retry.') from None
        _check_cancel(context)
        delivery = {'node': agent['id'], 'name': agent['name'], 'answer': answer}
        facts = {}
        if spec:
            try:
                structured, facts = verify_agent_output(context, directory, spec, answer, candidate)
                delivery.update(structured)
            except OutputContractError as error:
                _event(context, 'OUTPUT_CONTRACT_FAILED', node=agent['id'], reason=str(error))
                raise WorkflowError(str(error)) from None
        _check_cancel(context)
        _event(context, 'NODE_COMPLETED', node=agent['id'], answer_file=str(directory / 'answer.txt'),
               answer_sha256=_evidence_sha256(context['directory_fd'], evidence + '/answer.txt'), **facts)
        return delivery


def agent_directory(workspace, agent):
    path = (workspace / agent['cwd']).resolve()
    if not path.is_relative_to(workspace) or not path.is_dir():
        raise WorkflowError('Missing or out-of-project working directory for ' + agent['id'])
    return path


def branch_node_ids(control):
    return {node for path in control['branches'].values() for node in path} if control else set()


def prepare_calls(manifest, workspace, *, dry_run=False):
    workspace = (workspace or Path.cwd()).resolve(strict=True)
    if not workspace.is_dir():
        raise WorkflowError('Workspace is not a directory.')
    deferred = branch_node_ids(manifest.get('condition') or manifest.get('router'))
    for agent in manifest['agents']:
        # Branch prerequisites are checked on invocation, only for the selected path.
        if agent['id'] in deferred:
            continue
        agent_directory(workspace, agent)
        if not dry_run and shutil.which(agent['provider']) is None:
            raise WorkflowError('Missing CLI: ' + agent['provider'])
    return workspace


def script_main(manifest, run_workflow):
    parser = argparse.ArgumentParser(description=manifest['name'] + ' (native CLI workflow)')
    parser.add_argument('--workspace', type=Path, help='project root; defaults to current directory')
    parser.add_argument('--task', default='', help='task supplied to all departments')
    parser.add_argument('--task-file', type=Path, help='UTF-8 task file; cannot combine with --task')
    parser.add_argument('--run-dir', type=Path, help='new private directory to execute, or an existing directory for --status/--cancel')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='show commands; no CLI, model or file changes')
    mode.add_argument('--status', action='store_true', help='read this artifact\'s existing run as JSON; never execute')
    mode.add_argument('--cancel', action='store_true', help='request cancellation of an active run; never replay')
    parser.add_argument('--details', action='store_true', help='include the actual journal in --status/--cancel JSON')
    args = parser.parse_args()
    try:
        if os.name != 'posix':
            raise WorkflowError('This first export driver is validated for POSIX/Linux only.')
        if args.status or args.cancel:
            if not args.run_dir or args.workspace is not None or args.task or args.task_file:
                raise WorkflowError('--status/--cancel require --run-dir and accept no execution inputs.')
            snapshot = inspect_run(manifest, args.run_dir, cancel=args.cancel, details=args.details)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0
        if args.details:
            raise WorkflowError('--details requires --status or --cancel.')
        if args.task and args.task_file:
            raise WorkflowError('Choose --task or --task-file, not both.')
        if args.dry_run:
            workspace = prepare_calls(manifest, args.workspace, dry_run=True)
            deferred = branch_node_ids(manifest.get('condition') or manifest.get('router'))
            plan = {'name': manifest['name'], 'policy': manifest['policy'],
                    'calls': [{'node': a['id'], 'cwd': str(workspace / a['cwd'] if a['id'] in deferred
                                                          else agent_directory(workspace, a)),
                               'argv': native_command(a, Path('<run>') / a['id'] / 'answer.txt')}
                              for a in manifest['agents']]}
            plan.update({kind: manifest[kind] for kind in ('condition', 'router', 'loop') if kind in manifest})
            if tool := manifest.get('loop', {}).get('validation'):
                plan['calls'].insert(1, dict(node=tool['id'], argv=tool['argv'], cwd=str(workspace / tool['cwd'])))
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        with _cancellation_signals() as cancellation:
            if args.run_dir:
                directory = args.run_dir.absolute()
                directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            else:
                directory = Path(tempfile.mkdtemp(prefix='devflower-script-'))
            with _run_directory(directory) as owner:
                import fcntl
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                context = {'directory': directory, 'cancellation': cancellation, 'directory_fd': owner}
                print('Private run evidence: ' + str(directory), file=sys.stderr)
                _write(directory / 'workflow.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                _event(context, 'RUN_STARTED', workflow=manifest['name'], script_sha256=_script_sha256())
                try:
                    _check_cancel(context)
                    context['workspace'] = prepare_calls(manifest, args.workspace)
                    if any(agent.get('native_output', {}).get('kind') == 'review' for agent in manifest['agents']):
                        _event(context, 'CANDIDATE_WORKSPACE', workspace=str(context['workspace']))
                    task = args.task_file.read_text(encoding='utf-8') if args.task_file else args.task
                    task = '\n\n'.join(part for part in (manifest['input'], task) if part)
                    answer = run_workflow(context, task)
                    _check_cancel(context)
                    _write(directory / 'result.txt', answer['answer'])
                    _check_cancel(context)
                    _event(context, 'RUN_COMPLETED', result_file=str(directory / 'result.txt'),
                           result_sha256=_evidence_sha256(owner, 'result.txt'))
                except (WorkflowError, OSError, UnicodeError) as error:
                    _event(context, 'RUN_STOPPED', reason=str(error), automatic_retry=False,
                           status='CANCELLED' if isinstance(error, WorkflowCancelled) else 'STOPPED')
                    raise
        print(answer['answer'])
        return 0
    except (WorkflowError, OSError, UnicodeError) as error:
        print('Workflow stopped: ' + str(error), file=sys.stderr)
        return 128 + error.signum if isinstance(error, WorkflowCancelled) else 1
