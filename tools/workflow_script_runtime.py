"""Small standard-library CLI driver embedded in exported workflow scripts.

This module does not import the DevFlower server, SQLite, V2 Runtime or a model
SDK. Native CLI configuration remains authoritative. Logs are local/private;
they can contain project source and native CLI output and are not sanitized.
"""
import argparse
from collections import defaultdict
from contextlib import contextmanager, nullcontext
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
import re
import select
import uuid


from workflow_output_contract import (OUTPUT_BYTES, OutputContractError, candidate_record, output_json,
                                      check_output_fields, output_field, save_candidate_file, validate_structured_answer)  # EMBED_OUTPUT_CONTRACT


class WorkflowError(RuntimeError):
    pass


class ProcessCleanupUnconfirmed(WorkflowError):
    pass


class ProcessNotStarted(OSError):
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


def workflow_environment():
    return {key: value for key, value in os.environ.items() if key != 'DEEPSEEK_API_KEY'}


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
    steps = manifest.get('steps') or [agent['id'] for agent in manifest['agents']]
    condition = manifest.get('condition')
    control = condition or manifest.get('router')
    if not control:
        return steps, None
    branches = control['branches']
    branch_nodes = branch_node_ids(control)
    prefix = [node for node in steps if node not in branch_nodes]
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


def failed_checks(events, tool_ids):
    return [dict(node=row['node'], returncode=row['returncode']) for row in events
            if row['kind'] == 'NODE_COMPLETED' and 'iteration' not in row
            and row['node'] in tool_ids and row['returncode'] != 0]


@contextmanager
def owner_lease(directory, manifest):
    if manifest.get('compiler_version', 0) < 15:
        yield None
        return
    import fcntl
    with (directory / 'owner.lease').open('xb') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        yield lease.fileno()


def inspect_run(manifest, directory, *, cancel=False, details=False, expected_sha256=None, historical=False):
    """Observe the same artifact, never replay it. Cancellation is cooperative.

    The live script holds a directory lock. No stored PID is ever signalled;
    after an owner crash an unfinished run remains RECOVERY_REQUIRED, even if
    a native child or remote request might still be running.
    """
    import fcntl
    directory = Path(directory).absolute()
    with _run_directory(directory) as fd:
        # The execution owner locks the directory before publishing its manifest.
        # A status or cancel request must not compete for ownership before then.
        try:
            saved = json.loads(_evidence_bytes(fd, 'workflow.json'))
        except (ValueError, UnicodeError, RecursionError):
            raise WorkflowError('Invalid run manifest; execution outcome is unknown.') from None
        if saved != manifest:
            raise WorkflowError('Run belongs to a different workflow artifact.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
        else:
            active = False
            fcntl.flock(fd, fcntl.LOCK_UN)
        snapshot = dict(status='RECOVERY_REQUIRED', active=active, automatic_retry=False,
                        directory=str(directory), script_sha256=expected_sha256 or _script_sha256(), completed_nodes=[])
        try:
            if manifest.get('compiler_version', 0) >= 15:
                with _evidence_file(fd, 'owner.lease') as lease:
                    try:
                        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        owner_active = True
                    else:
                        owner_active = False
                snapshot.update(owner_active=owner_active, supervisor_active=active and not owner_active)
            else:
                owner_active = active
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
            pending_process = None
            for event in events:
                if event['kind'] in ('PROCESS_SUPERVISOR', 'PROCESS_STARTED'):
                    pending_process = {key: event[key] for key in ('node', 'pid', 'supervisor_pid', 'iteration', 'start_ticks', 'boot_id')
                                       if key in event}
                    if 'pid' in pending_process:
                        pending_process['pgid'] = pending_process['pid']
                elif event['kind'] in ('PROCESS_EXITED', 'NODE_COMPLETED', 'NOT_SENT'):
                    pending_process = None
            if pending_process and not owner_active:
                snapshot['last_process_observation'] = pending_process
            if manifest.get('compiler_version', 0) >= 13:
                inspect_series(directory, fd, manifest, events, snapshot)
            business_outcomes = manifest.get('compiler_version', 0) >= 14
            terminal_kinds = ('RUN_COMPLETED', 'RUN_STOPPED', 'RUN_CHECK_FAILED') if business_outcomes else (
                'RUN_COMPLETED', 'RUN_STOPPED')
            terminal = [row for row in events if row['kind'] in terminal_kinds]
            if terminal and (len(terminal) != 1 or terminal[0] is not events[-1]):
                raise WorkflowError('Terminal journal evidence is inconsistent.')
            if cancel and owner_active and active and not terminal:
                try:
                    request = os.open('cancel.request', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=fd)
                except FileExistsError:
                    pass  # Repeated requests never restart or signal another process.
                else:
                    os.close(request)
                    os.fsync(fd)
            snapshot['cancel_requested'] = _cancel_requested(fd)
            outer = [row for row in events if 'iteration' not in row]
            completed = [row for row in outer if row['kind'] == 'NODE_COMPLETED']
            calls = defaultdict(list)
            for row in outer:
                if row['kind'] in ('CALL_INTENT', 'PROCESS_STARTED', 'PROCESS_EXITED', 'NODE_COMPLETED'):
                    calls[row['node']].append(row)
            condition, router = manifest.get('condition'), manifest.get('router')
            expected, decision = recorded_path(manifest, events)
            if [row.get('node') for row in completed] != expected[:len(completed)]:
                raise WorkflowError('Node-completion evidence is inconsistent.')
            loop = manifest.get('loop')
            accepted = None
            if loop:
                snapshot['steps'] = expected
                iterations, step = [], 0
                for event in events:
                    if 'iteration' in event or 'node' in event:
                        node = event['loop_node'] if 'iteration' in event else event['node']
                        if step == len(expected) or node != expected[step]:
                            raise WorkflowError('Workflow node event is out of order: ' + node)
                        if 'iteration' in event:
                            iterations.append(event)
                        elif event['kind'] == 'NODE_COMPLETED':
                            step += 1
                accepted = inspect_loop(loop, fd, iterations, snapshot)
            reviews = []
            producer = None
            agents = {agent['id']: agent for agent in manifest['agents']}
            tools = manifest.get('tools', {})
            for row in completed:
                node = row['node']
                if loop and node == loop['node']:
                    if not accepted or (row.get('accepted_iteration'), row.get('answer_sha256'),
                                        row.get('candidate_sha256')) != (
                            accepted[0], accepted[1], accepted[2]['candidate_sha256']):
                        raise WorkflowError('Loop delivery is not bound to its accepted round.')
                    producer = accepted[2]
                    reviews.append((node, producer))
                    continue
                if _evidence_sha256(fd, node + '/answer.txt') != row.get('answer_sha256'):
                    raise WorkflowError('Recorded node answer has changed: ' + node)
                call = calls[node]
                if ([event['kind'] for event in call] != ['CALL_INTENT', 'PROCESS_STARTED', 'PROCESS_EXITED', 'NODE_COMPLETED']
                        or call[2].get('returncode') != (row['returncode'] if node in tools else 0)):
                    raise WorkflowError('Node completion does not match its recorded process: ' + node)
                if node in tools:
                    result = read_structured_evidence(fd, row, 'structured.json', 'answer_sha256')['result']
                    if (result['returncode'] != row['returncode']
                            or result['success'] is not (row['returncode'] == 0)):
                        raise WorkflowError('Tool outcome does not match its process: ' + node)
                    for channel in ('stdout', 'stderr'):
                        if _evidence_sha256(fd, node + '/' + channel + '.log') != row.get(channel + '_sha256'):
                            raise WorkflowError('Tool log has changed: ' + channel)
                    if 'upstream_sha256' in row:
                        upstream = read_structured_evidence(fd, row, 'upstream.json', 'upstream_sha256')
                        if (not accepted or upstream['candidate'] != accepted[2]
                                or sha256(upstream['answer'].encode()).hexdigest() != accepted[1]):
                            raise WorkflowError('Tool input does not match the accepted Loop delivery.')
                    if 'candidate_sha256' not in row:
                        producer = None
                    elif not producer or row['candidate_sha256'] != producer['candidate_sha256']:
                        raise WorkflowError('Tool handoff does not match the upstream candidate: ' + node)
                    continue
                agent = agents[node]
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
                        if historical and not candidate_is_current(directory / node / 'candidate-files', producer):
                            raise WorkflowError('Stored candidate snapshot has changed: ' + node)
                    else:
                        producer = None
                else:
                    producer = None
            snapshot['completed_nodes'] = [row['node'] for row in completed]
            if terminal:
                last = terminal[0]
                if last['kind'] in ('RUN_COMPLETED', 'RUN_CHECK_FAILED'):
                    if snapshot['completed_nodes'] != expected or _evidence_sha256(fd, 'result.txt') != last.get('result_sha256'):
                        raise WorkflowError('Final result is missing or changed.')
                    if (condition or router) and not decision:
                        raise WorkflowError('Final result is not bound to a selected branch.')
                    if not completed or last['result_sha256'] != completed[-1]['answer_sha256']:
                        raise WorkflowError('Final result does not match the last node.')
                    candidates_current = True
                    if reviews:
                        [workspace] = [event['workspace'] for event in events if event['kind'] == 'CANDIDATE_WORKSPACE']
                    for node, candidate in reviews:
                        try:
                            current = candidate_is_current(workspace, candidate)
                        except OutputContractError:
                            current = False
                        if not current:
                            candidates_current = False
                            if not historical and (not decision or not condition or node != condition['reviewer']):
                                snapshot['candidate_current'] = False
                                if loop and node == loop['node']:
                                    snapshot['loop']['iterations'][-1]['historical'] = True
                                raise WorkflowError('Reviewed candidate no longer matches current files: ' + node)
                    if reviews:
                        snapshot['candidate_current'] = candidates_current
                    if business_outcomes:
                        failures = failed_checks(completed, tools)
                        if (last.get('failed_checks') != failures
                                or (last['kind'] == 'RUN_CHECK_FAILED') != bool(failures)):
                            raise WorkflowError('Business outcome does not match the verified command results.')
                        snapshot['failed_checks'] = failures
                    status = 'CHECK_FAILED' if last['kind'] == 'RUN_CHECK_FAILED' else 'COMPLETED'
                    snapshot.update(status=status, result_file=str(directory / 'result.txt'))
                elif last.get('status') in ('CANCELLED', 'STOPPED', 'RECOVERY_REQUIRED'):
                    snapshot.update(status=last['status'], reason=last.get('reason', ''))
                    if business_outcomes:
                        snapshot['failed_checks'] = failed_checks(completed, tools)
                else:
                    raise WorkflowError('Unknown terminal state.')
            elif active and owner_active:
                snapshot['status'] = 'CANCEL_REQUESTED' if snapshot['cancel_requested'] else 'RUNNING'
            elif active:
                snapshot['reason'] = 'Script owner is absent; the process supervisor is still active. Wait for cleanup and verify before restarting.'
            else:
                snapshot['reason'] = ('Script owner is absent without a terminal record; native effects are unknown. '
                                      'Residual processes may require manual verification before restarting.')
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


def _group_members(group):
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            state, _, process_group, *_ = (entry / 'stat').read_bytes().rsplit(b') ', 1)[1].split()
        except (OSError, ValueError, IndexError):
            continue
        if process_group == group and state not in (b'Z', b'X'):
            members.append(entry.name)
    return members


def _guarded_group(process):
    # Keep the direct child's identity reserved until after the final signal.
    group = str(process.pid).encode()
    for signum, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 1)):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + grace
        while _group_members(group) and time.monotonic() < deadline:
            time.sleep(.05)
        if not _group_members(group):
            process.wait()
            return
    raise WorkflowError('Launch group may still contain live processes; manual verification required.')


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


def _supervise_process(argv, cwd, source, stdout, stderr, lifeline, report, directory, tool):
    process = None
    try:
        os.setsid()
        # Signals sent directly to the supervisor must take the cleanup path.
        interrupted = False

        def interrupt(_signum, _frame):
            nonlocal interrupted
            interrupted = True

        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        if os.read(lifeline, 1) != b'1' or interrupted:
            return
        try:
            process = subprocess.Popen(argv, cwd=cwd, stdin=source, stdout=stdout, stderr=stderr,
                                       start_new_session=True, env=workflow_environment())
        except OSError as error:
            os.write(report, f'error {error.errno or 0}\n'.encode())
            return
        stat_fields = (Path('/proc') / str(process.pid) / 'stat').read_bytes().rsplit(b') ', 1)[1].split()
        ticks = int(stat_fields[19])
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        os.write(report, f'pid {process.pid} {ticks} {boot_id}\n'.encode())
        while not interrupted and not select.select([lifeline], [], [], .05)[0]:
            status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if status is not None:
                code = status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
                if tool:
                    _guarded_group(process)
                    process = None
                    os.write(report, f'exit {code}\n'.encode())
                    return
                os.write(report, f'exit {code}\n'.encode())
                while not interrupted and not select.select([lifeline], [], [], .05)[0]:
                    pass
                if not interrupted and os.read(lifeline, 1) == b'A':
                    process.wait()
                    process = None
                return
    except (OSError, ValueError):
        pass
    finally:
        if process is not None:
            try:
                _guarded_group(process)
                _write(directory / 'process-group-stopped', 'launch group signalled and child reaped\n')
            except (OSError, WorkflowError):
                try:
                    _write(directory / 'process-group-unconfirmed', 'Launch group cleanup could not be verified.\n')
                except OSError:
                    pass


def _supervised_wait(context, node, argv, cwd, directory, timeout, source, stdout, stderr, tool):
    control_read, control_write = os.pipe()
    result_read, result_write = os.pipe()
    try:
        supervisor = os.fork()
    except BaseException:
        for fd in (control_read, control_write, result_read, result_write):
            os.close(fd)
        raise
    if supervisor == 0:
        if context.get('owner_lease') is not None:
            os.close(context['owner_lease'])
        with open(os.devnull, 'rb+') as silent:
            for fd in (0, 1, 2):
                os.dup2(silent.fileno(), fd)
        os.close(control_write)
        os.close(result_read)
        try:
            _supervise_process(argv, cwd, source, stdout, stderr, control_read, result_write, directory, tool)
        finally:
            os._exit(0)
    os.close(control_read)
    os.close(result_write)
    try:
        _event(context, 'PROCESS_SUPERVISOR', node=node, supervisor_pid=supervisor)
        os.write(control_write, b'1')
        deadline = time.monotonic() + timeout if timeout is not None else None
        with os.fdopen(result_read, 'rb', buffering=0, closefd=False) as channel:
            while True:
                _check_cancel(context)
                if select.select([result_read], [], [], .05)[0]:
                    result = channel.readline(100).split()
                    if not result:
                        raise OSError('Process supervisor ended without an exit receipt; inspect the launch group.')
                    kind, value, *identity = result
                    if kind == b'exit':
                        if not tool:
                            os.write(control_write, b'A')
                        return int(value)
                    if kind == b'error':
                        raise ProcessNotStarted(int(value), os.strerror(int(value)))
                    _event(context, 'PROCESS_STARTED', node=node, pid=int(value), supervisor_pid=supervisor,
                           start_ticks=int(identity[0]), boot_id=identity[1].decode('ascii'))
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout)
    finally:
        # EOF also occurs when the workflow owner is killed without executing
        # this block. The supervisor inherits the run lock through cleanup.
        os.close(control_write)
        _, wait_status = os.waitpid(supervisor, 0)
        os.close(result_read)
        if wait_status or (directory / 'process-group-unconfirmed').is_file():
            raise ProcessCleanupUnconfirmed('Process supervisor cleanup could not be verified. Processes may still be running; manually verify the recorded PID, PGID, and start identity before restarting.')


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
    example = dict(result=dict(schema_version=1, summary='<summary>', data={}), artifacts=spec['allowed_paths'][:1])
    return ('Return only raw JSON, no markdown fences or extra fields. Format: ' + output_json(example) +
            '\nAllowed workspace-relative artifact paths: ' + output_json(spec['allowed_paths'])
            + '. When paths are declared, report at least one actual delivery file. '
            'The top-level artifacts array contains path strings only. Do not use path objects. '
            'Report no file hashes. Field rules: ' + output_json(spec['fields']))


def write_structured_evidence(directory, filename, value):
    raw = output_json(value)
    _write(directory / filename, raw)
    return sha256(raw.encode('utf-8')).hexdigest()


def candidate_is_current(workspace, candidate):
    paths = [file['path'] for file in candidate['manifest']['files']]
    return candidate_record(workspace, paths) == candidate


def take_continuation(context):
    # Loop iterations share this one dispatch slot with the outer workflow.
    return context.get('continuation', {}).pop('entry', None)


@contextmanager
def command_process(context, tool, prefix, upstream=None):
    _check_cancel(context)
    try:
        cwd = agent_directory(context['workspace'], tool)
    except WorkflowError as error:
        _event(context, 'NOT_SENT', node=tool['id'], reason=str(error), failure_kind='WORKING_DIRECTORY')
        raise
    directory = context['directory'] / prefix
    directory.mkdir(mode=0o700)
    delivery = directory / 'upstream.json'
    if continuation := take_continuation(context):
        upstream = dict(upstream or {}, task=context['task'], continuation=continuation)
    if upstream is not None:
        _write(delivery, output_json(upstream))
    source = delivery.open('rb') if upstream is not None else nullcontext(subprocess.DEVNULL)
    with source as stdin, observed_process(context, tool['id'], tool['argv'], cwd, directory,
                                          tool['timeout'], source=stdin, tool=True) as code:
        _check_cancel(context)
        yield code


def command_logs(context, prefix):
    summaries, checksums = {}, {}
    fd = context['directory_fd']
    for channel in ('stdout', 'stderr'):
        relative = prefix + '/' + channel + '.log'
        checksums[channel + '_sha256'] = _evidence_sha256(fd, relative)
        with _evidence_file(fd, relative) as stream:
            end = stream.seek(0, os.SEEK_END)
            stream.seek(max(end - 2048, 0))
            summaries[channel] = ('[tail] ' if end > 2048 else '') + stream.read(2048).decode('utf-8', errors='replace')
    return summaries, checksums


def invoke_tool(context, tool, upstream, limit):
    node = tool['id']
    if upstream is not None and limit is not None and len(upstream['answer']) > limit:
        raise WorkflowError('Saved Tool handoff limit exceeded; command did not run.')
    loop_input = upstream if upstream and 'accepted_iteration' in upstream else None
    with command_process(context, tool, node, loop_input) as code:
        summaries, hashes = command_logs(context, node)
        if loop_input is not None:
            hashes['upstream_sha256'] = _evidence_sha256(context['directory_fd'], node + '/upstream.json')
        result = dict(tool='command', argv=tool['argv'], cwd=tool['cwd'], returncode=code, success=code == 0, **summaries)
        envelope = {'result': result}
        directory = context['directory'] / node
        checksum = write_structured_evidence(directory, 'structured.json', envelope)
        answer = output_json(envelope)
        _write(directory / 'answer.txt', answer)
        delivery = dict(node=node, name=tool['name'], answer=answer, structured=envelope)
        if upstream and 'candidate' in upstream:
            delivery['candidate'] = upstream['candidate']
            hashes['candidate_sha256'] = delivery['candidate']['candidate_sha256']
        _check_cancel(context)
        _event(context, 'NODE_COMPLETED', node=node, returncode=code, answer_sha256=checksum, **hashes)
        if code and 'failed_checks' in context:
            context['failed_checks'].append(dict(node=node, returncode=code))
        return delivery


def run_validation(context, tool, produced):
    node, candidate = tool['id'], produced['candidate']
    paths = [item['path'] for item in candidate['manifest']['files']]
    if tool['limit'] is not None and len(produced['answer']) > tool['limit']:
        raise WorkflowError('Saved Tool handoff limit exceeded; validation did not run.')
    try:
        before = candidate_record(context['workspace'], paths)
        if before != candidate:
            raise WorkflowError('Candidate changed before validation.')
    except (OutputContractError, WorkflowError) as error:
        _event(context, 'NOT_SENT', node=node, reason=str(error))
        raise WorkflowError(str(error)) from None
    prefix = f"iterations/{context['iteration']}/{node}"
    with command_process(context, tool, prefix) as code:
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
        summaries, hashes = command_logs(context, prefix)
        _check_cancel(context)
        _event(context, 'VALIDATION_COMPLETED', node=node, **facts, **hashes)
        return dict(returncode=code, candidate_sha256=before['candidate_sha256'],
                    reason=f'Validation failed (exit {code}).' if code else 'Validation passed.', **summaries)


def inspect_loop(loop, fd, events, snapshot):
    developer, reviewer = (agent['id'] for agent in loop['agents'])
    tool = loop.get('validation', {}).get('id')
    rounds = []
    snapshot['loop'] = dict(node=loop['node'], max_iterations=loop['max_iterations'], iterations=rounds)
    current = None
    candidate = review_answer_sha = None
    for event in events:
        kind = event['kind']
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
    if current and current['disposition'] == 'LOOP_ACCEPTED':
        return current['iteration'], review_answer_sha, candidate


def run_loop(context, loop, task, upstream, limit):
    developer, reviewer = loop['agents']
    if upstream is not None and limit is not None and len(upstream['answer']) > limit:
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
        produced = invoke_agent(current, developer, task, upstream, loop['task_limit'], feedback=feedback)
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
            _check_cancel(context)
            _event(current, 'LOOP_ACCEPTED', **facts)
            answer_hash = _evidence_sha256(context['directory_fd'], f"iterations/{iteration}/{reviewer['id']}/answer.txt")
            _event(context, 'NODE_COMPLETED', node=loop['node'], accepted_iteration=iteration,
                   answer_sha256=answer_hash, **facts)
            return dict(audited, node=loop['node'], name=loop['name'], candidate=candidate,
                        accepted_iteration=iteration, reviewer_node=reviewer['id'])
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
        snapshot = (directory.parent if 'iteration' in context else directory) / 'candidate-files'
        observed = candidate_record(workspace, envelope['artifacts'], snapshot=snapshot)
        facts['candidate_sha256_evidence'] = write_structured_evidence(directory, 'candidate.json', observed)
        facts['candidate_sha256'] = observed['candidate_sha256']
        delivery['candidate'] = observed
    facts['structured_sha256'] = write_structured_evidence(directory, 'structured.json', envelope)
    return delivery, facts


@contextmanager
def observed_process(context, node, argv, cwd, directory, timeout, *, source=subprocess.DEVNULL, tool=False, request=None):
    _event(context, 'CALL_INTENT', node=node, cwd=str(cwd), argv=argv, **(request or {}))
    process = None
    supervised = False
    try:
        with (directory / 'stdout.log').open('xb') as stdout, (directory / 'stderr.log').open('xb') as stderr:
            # Keep the assistant credential outside Workflow Agent and Tool processes.
            _check_cancel(context)
            supervised = context.get('process_supervision') and sys.platform.startswith('linux')
            if supervised:
                code = _supervised_wait(context, node, argv, cwd, directory, timeout, source, stdout, stderr, tool)
            else:
                process = subprocess.Popen(argv, cwd=cwd, stdin=source, stdout=stdout,
                                           stderr=stderr, start_new_session=True,
                                           env=workflow_environment())
                _event(context, 'PROCESS_STARTED', node=node, pid=process.pid)
                code = _wait(context, process, timeout)
        if tool and process is not None:
            _stop(process)
        _event(context, 'PROCESS_EXITED', node=node, returncode=code)
        yield code
    except (WorkflowCancelled, KeyboardInterrupt) as error:
        if process is not None:
            _stop(process)
        signum = error.signum if isinstance(error, WorkflowCancelled) else signal.SIGINT
        _event(context, 'CANCELLED', node=node, signal=signal.Signals(signum).name,
               source='request' if getattr(error, 'requested', False) else 'signal',
               outcome='unknown' if process or supervised else 'not_sent', automatic_retry=False)
        raise WorkflowCancelled(signum, requested=getattr(error, 'requested', False)) from None
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop(process)
        _event(context, 'UNKNOWN', node=node, reason='Timed out; no automatic retry.', failure_kind='TIMEOUT')
        raise WorkflowError('Call timed out; inspect native logs before starting another run.') from None
    except OSError as error:
        if process is not None:
            _stop(process)
        possible_process = process is not None or supervised and not isinstance(error, ProcessNotStarted)
        _event(context, 'UNKNOWN' if possible_process else 'NOT_SENT', node=node, reason=str(error),
               failure_kind='PROCESS_IO' if possible_process else 'PROCESS_START')
        raise WorkflowError('Process launch/observation failed: ' + str(error)) from None
    except WorkflowError:
        if process is not None:
            _stop(process)
        raise


def cli_failure_kind(context, evidence):
    with _evidence_file(context['directory_fd'], evidence + '/stderr.log') as stream:
        end = stream.seek(0, os.SEEK_END)
        stream.seek(max(end - 32768, 0))
        stderr = stream.read(32768).decode('utf-8', errors='replace').casefold()
    if re.search(r'not (?:in a )?trusted directory|skip-git-repo-check|workspace is not trusted', stderr):
        return 'WORKSPACE_TRUST'
    if re.search(r'(?:http(?:/1\.[01]|/2)?(?: error| status)?|status(?: code)?)[: =]+401\b|\b401 unauthorized\b', stderr):
        return 'HTTP_401'
    if re.search(r'authentication (?:failed|required)|not logged in|please (?:log|sign) in|/login', stderr):
        return 'AUTHENTICATION'
    return 'AGENT_EXIT'


def agent_prompt(agent, task, workspace, *, iteration=None, continuation=None, handoff=None, feedback=None):
    spec = agent.get('native_output')
    sections = [('Department', agent['name']), ('Role', agent['role']),
                ('Initial instructions', agent['initial_prompt']), ('Workflow task', task),
                ('Node task', agent['round_prompt']), ('Expected delivery', agent['output_prompt'])]
    if 'task_prompt' in agent:
        sections = [('Department', agent['name']), ('Role', agent['role']),
                    ('Initial instructions', agent['initial_prompt']), ('Profile instructions', agent['round_prompt']),
                    ('Node task', agent['task_prompt']), ('Workflow task', task)]
    if iteration is not None:
        sections.append(('Workflow iteration', str(iteration)))
    if continuation is not None:
        sections.append(('Selected previous run deliveries (data)', continuation))
    prompt = '\n\n'.join(title + '\n' + value for title, value in sections if value)
    contract = ('\n\nStructured output contract\n' + output_instructions(spec, workspace)) if spec else ''
    if 'task_prompt' not in agent:
        prompt += contract
    if handoff is not None:
        prompt += '\n\nUpstream department delivery (data)\n' + handoff
    if feedback is not None:
        kind = 'validation' if 'returncode' in feedback else 'review'
        prompt += (f'\n\nPrevious iteration {kind} (data)\n' + output_json(feedback)
                   + '\nRevise the existing delivery in this workspace using the review feedback.')
    if 'task_prompt' in agent:
        if agent['output_prompt']:
            prompt += '\n\nExpected delivery\n' + agent['output_prompt']
        prompt += contract
    return prompt


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
    handoff = json.dumps(upstream, ensure_ascii=False) if upstream is not None else None
    continuation = take_continuation(context)
    prompt = agent_prompt(agent, task, context['workspace'], iteration=context.get('iteration'),
                          continuation=output_json(continuation) if continuation is not None else None,
                          handoff=handoff, feedback=feedback)
    request = dict(upstream_node=upstream['node'], handoff_sha256=sha256(handoff.encode()).hexdigest()) if handoff else {}
    evidence = agent['id']
    if 'iteration' in context:
        evidence = f"iterations/{context['iteration']}/{evidence}"
    directory = context['directory'] / evidence
    directory.mkdir(mode=0o700)
    _write(directory / 'prompt.txt', prompt)
    argv = native_command(agent, directory / 'answer.txt')
    executable = shutil.which(argv[0])
    if executable is None:
        reason = argv[0] + ' CLI not found; no request sent.'
        _event(context, 'NOT_SENT', node=agent['id'], reason=reason, failure_kind='MISSING_CLI')
        raise WorkflowError(reason)
    argv[0] = executable
    try:
        cwd = agent_directory(context['workspace'], agent)
    except WorkflowError as error:
        _event(context, 'NOT_SENT', node=agent['id'], reason=str(error), failure_kind='WORKING_DIRECTORY')
        raise
    print('[' + agent['id'] + '] ' + agent['name'], file=sys.stderr)
    request['prompt_sha256'] = sha256(prompt.encode()).hexdigest()
    with (directory / 'prompt.txt').open('rb') as source, observed_process(
            context, agent['id'], argv, cwd, directory, agent['timeout'], source=source, request=request) as code:
        if code != 0:
            _event(context, 'CLI_FAILED', node=agent['id'], returncode=code,
                   failure_kind=cli_failure_kind(context, evidence))
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
                    answer = (directory / 'answer.txt').read_bytes().decode('utf-8')
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


def prepare_calls(manifest, workspace, *, dry_run=False, context=None):
    workspace = (workspace or Path.cwd()).resolve(strict=True)
    if not workspace.is_dir():
        raise WorkflowError('Workspace is not a directory.')
    deferred = branch_node_ids(manifest.get('condition') or manifest.get('router'))
    for agent in [*manifest['agents'], *manifest.get('loop', {}).get('agents', [])]:
        # Branch prerequisites are checked on invocation, only for the selected path.
        if agent['id'] in deferred:
            continue
        try:
            agent_directory(workspace, agent)
        except WorkflowError as error:
            if context:
                _event(context, 'PREFLIGHT_FAILED', failed_node=agent['id'], reason=str(error),
                       failure_kind='WORKING_DIRECTORY')
            raise
        if not dry_run and shutil.which(agent['provider']) is None:
            reason = 'Missing CLI: ' + agent['provider']
            if context:
                _event(context, 'PREFLIGHT_FAILED', failed_node=agent['id'], reason=reason, failure_kind='MISSING_CLI')
            raise WorkflowError(reason)
    return workspace


SERIES_HASH = re.compile(r'[0-9a-f]{64}')
SERIES_ID = re.compile(r'[0-9a-f]{32}')


def series_workspace(workspace):
    info = workspace.stat()
    return {'path': str(workspace), 'device': info.st_dev, 'inode': info.st_ino}


def previous_delivery(manifest, directory, artifact_hash):
    directory = directory.absolute()
    with _run_directory(directory) as fd:
        saved = json.loads(_evidence_bytes(fd, 'workflow.json'))
        if not isinstance(saved, dict) or saved.get('compiler_version', 0) < 13:
            raise WorkflowError('This run predates verified continuation evidence.')
        state = inspect_run(saved, directory, details=True, expected_sha256=artifact_hash, historical=True)
        if state['status'] != 'COMPLETED' or state['active']:
            raise WorkflowError('Previous run is not a verified completion: ' + state.get('reason', state['status']))
        events = state['events']
        if (manifest['compiler_version'] >= 14 and saved['compiler_version'] < 14
                and failed_checks(events, saved.get('tools', {}))):
            raise WorkflowError('The previous frozen script recorded an unhandled check failure under its legacy completion contract.')
        binding = [event['workspace'] for event in events if event['kind'] == 'SERIES_WORKSPACE']
        if len(binding) != 1:
            raise WorkflowError('Previous run has no unique workspace binding.')
        result_hash = events[-1]['result_sha256']
        options = {'result': dict(kind='result', sha256=result_hash, source='result.txt',
                                  label='Final result')}
        accepted = state.get('loop', {}).get('iterations', [])
        accepted = accepted[-1]['iteration'] if accepted and accepted[-1]['disposition'] == 'LOOP_ACCEPTED' else None
        for event in events:
            if event['kind'] != 'NODE_COMPLETED' or ('iteration' in event and event['iteration'] != accepted):
                continue
            node = event['node']
            iteration = event.get('iteration')
            prefix = f'iterations/{iteration}/' if iteration else ''
            key = f'{iteration}/{node}' if iteration else node
            if event.get('structured_sha256'):
                value = read_structured_evidence(fd, event, 'structured.json', 'structured_sha256', prefix=prefix)
                options['structured:' + key] = dict(kind='structured', node=node, label=value['result']['summary'],
                    sha256=event['structured_sha256'], source=prefix + node + '/structured.json')
            if event.get('candidate_sha256_evidence'):
                candidate = read_structured_evidence(fd, event, 'candidate.json', 'candidate_sha256_evidence', prefix=prefix)
                candidate_path = prefix + ('candidate-files' if iteration else node + '/candidate-files')
                for index, file in enumerate(candidate['manifest']['files']):
                    # The candidate validator has already checked every frozen file, including its path.
                    options[f'artifact:{key}:{index}'] = dict(kind='artifact', node=node, label=file['path'],
                        **file, source=candidate_path + '/' + file['path'])
    return dict(series=state['series'], workspace=binding[0], previous_hash=state['script_sha256'],
                previous_version=series_version(saved), current_version=series_version(manifest)), options


def series_version(manifest):
    def versions(agents):
        return {agent['id']: {key: agent.get(key) for key in ('profile_id', 'profile_revision', 'provider',
                                                           'model', 'effort')} for agent in agents}

    return dict(name=manifest['name'], source_sha256=manifest['source_sha256'], compiler_version=manifest['compiler_version'],
                agents=versions(manifest['agents']), loop_agents=versions(manifest.get('loop', {}).get('agents', [])))


def series_context(manifest, args, workspace, *, listing=False):
    current_hash = _script_sha256()
    previous_hash = args.previous_script_sha256 or current_hash
    if not SERIES_HASH.fullmatch(previous_hash):
        raise WorkflowError('Previous script SHA-256 must be a complete lowercase hexadecimal digest.')
    public, options = previous_delivery(manifest, args.continue_from, previous_hash)
    if series_workspace(workspace) != public.pop('workspace'):
        raise WorkflowError('Previous run belongs to a different workspace.')
    public.update(current_hash=current_hash, changed=previous_hash != current_hash)
    public['options'] = [dict(id=key, **{k: v for k, v in item.items() if k != 'source'})
                         for key, item in options.items()]
    if listing:
        return public
    if public['changed'] and args.accept_version != current_hash:
        raise WorkflowError('Script version changed. Use --continuation-options to see both versions and '
                            'pass --accept-version with the chosen script SHA-256.')
    if args.accept_version is not None and args.accept_version != current_hash:
        raise WorkflowError('Chosen script SHA-256 does not match this artifact.')
    selection = args.inherit
    if not 1 <= len(selection) <= 128 or len(selection) != len(set(selection)) or any(key not in options for key in selection):
        raise WorkflowError('Choose distinct verified deliveries with --inherit. Use --continuation-options to list them.')
    parent = public['series']
    run_id = args.run_id or uuid.uuid4().hex
    if run_id == parent['run_id']:
        raise WorkflowError('Continuation requires a new run identity.')
    series = dict(id=parent['id'], round=parent['round'] + 1, run_id=run_id, previous_run_id=parent['run_id'])
    deliveries = []
    with _run_directory(args.continue_from.absolute()) as fd:
        total = 0
        for key in selection:
            item = options[key]
            raw = _evidence_bytes(fd, item['source'])
            total += len(raw)
            if sha256(raw).hexdigest() != item['sha256'] or total > 8_000_000:
                raise WorkflowError('Selected delivery has changed or exceeds the 8000000-byte selection budget.')
            deliveries.append((key, item, raw))
    return series, dict(previous_run_id=parent['run_id'], previous_script_sha256=previous_hash,
                            previous_version=public['previous_version'], current_script_sha256=current_hash,
                            current_version=public['current_version']), deliveries


def save_continuation(context, identity, deliveries):
    copied = []
    selected = context['directory'] / '.inherited'
    selected.mkdir(mode=0o700)
    for index, (key, item, raw) in enumerate(deliveries, 1):
        entry = dict(id=key, **{k: v for k, v in item.items() if k != 'source'})
        if item['kind'] == 'artifact':
            filename = f'{index}/{item["path"]}'
            with _run_directory(selected) as fd:
                save_candidate_file(fd, filename, raw)
            entry['file'] = str(selected / filename)
        else:
            entry['value'] = json.loads(raw) if item['kind'] == 'structured' else raw.decode('utf-8')
        copied.append(entry)
    continuation = dict(identity, deliveries=copied)
    encoded = output_json(continuation)
    if len(encoded.encode()) > 1_000_000:
        raise WorkflowError('Selected text deliveries exceed the 1000000-byte context budget.')
    _write(context['directory'] / 'continuation.json', encoded)
    context['continuation'] = {'entry': continuation}
    _event(context, 'SERIES_CONTINUED', sha256=sha256(encoded.encode()).hexdigest())


def inspect_series(directory, fd, manifest, events, snapshot):
    start = events[0]
    series = start.get('series')
    if (not isinstance(series, dict) or set(series) != {'id', 'round', 'run_id', 'previous_run_id'}
            or any(not isinstance(series[key], str) or not SERIES_ID.fullmatch(series[key])
                   for key in ('id', 'run_id'))
            or type(series['round']) is not int or series['round'] < 1
            or start.get('manifest_sha256') != sha256(output_json(manifest).encode()).hexdigest()
            or start.get('task_sha256') != _evidence_sha256(fd, 'task.txt')):
        raise WorkflowError('Series identity or workflow manifest is inconsistent.')
    parent = series['previous_run_id']
    if (series['round'] == 1 and (parent is not None or series['id'] != series['run_id'])
            or series['round'] > 1 and (not isinstance(parent, str) or not SERIES_ID.fullmatch(parent)
                                       or parent == series['run_id'])):
        raise WorkflowError('Series parent identity is inconsistent.')
    snapshot['series'] = series
    handoff = [event for event in events if event['kind'] == 'SERIES_CONTINUED']
    finished = events[-1]['kind'] == 'RUN_COMPLETED' or (
        manifest.get('compiler_version', 0) >= 14 and events[-1]['kind'] == 'RUN_CHECK_FAILED')
    if len(handoff) > 1 or (handoff and not parent) or (finished and parent and not handoff):
        raise WorkflowError('Series handoff evidence is inconsistent.')
    if handoff:
        value = _evidence_bytes(fd, 'continuation.json')
        if sha256(value).hexdigest() != handoff[0].get('sha256'):
            raise WorkflowError('Series handoff evidence has changed.')
        transfer = json.loads(value)
        if (transfer['previous_run_id'] != parent or transfer['current_script_sha256'] != snapshot['script_sha256']
                or transfer['current_version'] != series_version(manifest) or not transfer['deliveries']):
            raise WorkflowError('Series handoff is bound to different versions or runs.')
        for entry in transfer['deliveries']:
            if entry['kind'] == 'artifact':
                path = Path(entry['file']).relative_to(directory / '.inherited')
                actual = sha256(_evidence_bytes(fd, '.inherited/' + path.as_posix())).hexdigest()
                if actual != entry['sha256']:
                    raise WorkflowError('Inherited file has changed.')
        snapshot['continuation'] = dict(previous_run_id=parent, previous_version=transfer['previous_version'],
                                        current_version=transfer['current_version'],
                                        deliveries=[{k: v for k, v in entry.items() if k != 'value'}
                                                    for entry in transfer['deliveries']])



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
    mode.add_argument('--continuation-options', action='store_true', help='list verified predecessor deliveries and versions')
    parser.add_argument('--details', action='store_true', help='include the actual journal in --status/--cancel JSON')
    parser.add_argument('--historical', action='store_true', help='verify completed evidence against frozen files in --status')
    parser.add_argument('--continue-from', type=Path, help='private evidence directory of the completed previous run')
    parser.add_argument('--previous-script-sha256', help='previous full script digest when versions differ')
    parser.add_argument('--accept-version', help='chosen script full digest when versions differ')
    parser.add_argument('--inherit', action='append', default=[], help='verified delivery ID to carry over; repeat to select more')
    parser.add_argument('--run-id', help='explicit independent run identity (32 lowercase hexadecimal characters)')
    args = parser.parse_args()
    try:
        if os.name != 'posix':
            raise WorkflowError('This first export driver is validated for POSIX/Linux only.')
        continuation_args = bool(args.continue_from or args.inherit or args.previous_script_sha256 or args.accept_version)
        if args.run_id is not None and not SERIES_ID.fullmatch(args.run_id):
            raise WorkflowError('Run ID must be 32 lowercase hexadecimal characters.')
        if args.historical and not args.status:
            raise WorkflowError('--historical requires --status.')
        if args.status or args.cancel:
            if not args.run_dir or args.workspace is not None or args.task or args.task_file or continuation_args or args.run_id:
                raise WorkflowError('--status/--cancel require --run-dir and accept no execution inputs.')
            snapshot = inspect_run(manifest, args.run_dir, cancel=args.cancel, details=args.details,
                                   historical=args.historical)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            if args.cancel and manifest.get('compiler_version', 0) >= 15:
                return 0 if snapshot['status'] in ('CANCEL_REQUESTED', 'CANCELLED') else 1
            return 0
        if args.details:
            raise WorkflowError('--details requires --status or --cancel.')
        if args.task and args.task_file:
            raise WorkflowError('Choose --task or --task-file, not both.')
        if continuation_args and not args.continue_from:
            raise WorkflowError('Continuation inputs require --continue-from.')
        if args.continuation_options:
            if not args.continue_from or args.task or args.task_file or args.run_dir or args.inherit or args.accept_version or args.run_id:
                raise WorkflowError('--continuation-options accepts only previous run, script digest and workspace.')
            workspace = (args.workspace or Path.cwd()).resolve(strict=True)
            print(json.dumps(series_context(manifest, args, workspace, listing=True), ensure_ascii=False, indent=2))
            return 0
        if args.dry_run:
            if continuation_args or args.run_id:
                raise WorkflowError('Use --continuation-options to verify a previous run without execution.')
            workspace = prepare_calls(manifest, args.workspace, dry_run=True)
            deferred = branch_node_ids(manifest.get('condition') or manifest.get('router'))

            def agent_call(agent, prefix=Path('<run>')):
                cwd = workspace / agent['cwd'] if agent['id'] in deferred else agent_directory(workspace, agent)
                return dict(node=agent['id'], cwd=str(cwd),
                            argv=native_command(agent, prefix / agent['id'] / 'answer.txt'))

            def tool_call(tool):
                return dict(node=tool['id'], argv=tool['argv'], cwd=str(workspace / tool['cwd']))

            plan = {'name': manifest['name'], 'policy': manifest['policy'],
                    'calls': [agent_call(agent) for agent in manifest['agents']]}
            plan.update({kind: manifest[kind] for kind in ('condition', 'router', 'loop') if kind in manifest})
            if 'steps' in manifest:
                by_node = {call['node']: [call] for call in plan['calls']}
                by_node.update({node: [tool_call(tool)] for node, tool in manifest.get('tools', {}).items()})
                if loop := manifest.get('loop'):
                    actors = [dict(agent_call(agent, Path('<run>/iterations/<round>')), loop_node=loop['node'])
                              for agent in loop['agents']]
                    if tool := loop.get('validation'):
                        actors.insert(1, dict(tool_call(tool), loop_node=loop['node']))
                    by_node[loop['node']] = actors
                plan['calls'] = [call for node in manifest['steps'] for call in by_node[node]]
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        task = args.task_file.read_text(encoding='utf-8') if args.task_file else args.task
        prior = None
        if args.continue_from:
            if not task.strip():
                raise WorkflowError('Continuation requires an explicit new task.')
            workspace = (args.workspace or Path.cwd()).resolve(strict=True)
            expected_workspace = series_workspace(workspace)
            prior = series_context(manifest, args, workspace)
        run_id = args.run_id or uuid.uuid4().hex
        series = prior[0] if prior else dict(id=run_id, round=1, run_id=run_id, previous_run_id=None)
        with _cancellation_signals() as cancellation:
            if args.run_dir:
                directory = args.run_dir.absolute()
                directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            else:
                directory = Path(tempfile.mkdtemp(prefix='devflower-script-'))
            with _run_directory(directory) as owner, owner_lease(directory, manifest) as lease:
                import fcntl
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                context = {'directory': directory, 'cancellation': cancellation, 'directory_fd': owner, 'owner_lease': lease}
                context['process_supervision'] = manifest.get('compiler_version', 0) >= 15
                if manifest.get('compiler_version', 0) >= 14:
                    context['failed_checks'] = []
                print('Private run evidence: ' + str(directory), file=sys.stderr)
                _write(directory / 'workflow.json', json.dumps(manifest, ensure_ascii=False, indent=2))
                _write(directory / 'task.txt', task)
                _event(context, 'RUN_STARTED', workflow=manifest['name'], script_sha256=_script_sha256(),
                       manifest_sha256=sha256(output_json(manifest).encode()).hexdigest(),
                       task_sha256=sha256(task.encode()).hexdigest(), series=series)
                try:
                    _check_cancel(context)
                    context['workspace'] = prepare_calls(manifest, args.workspace, context=context)
                    workspace_identity = series_workspace(context['workspace'])
                    _event(context, 'SERIES_WORKSPACE', workspace=workspace_identity)
                    if prior:
                        if workspace_identity != expected_workspace:
                            raise WorkflowError('Workspace changed between continuation validation and launch.')
                        save_continuation(context, prior[1], prior[2])
                    if 'loop' in manifest or any(agent.get('native_output', {}).get('kind') == 'review'
                                                 for agent in manifest['agents']):
                        _event(context, 'CANDIDATE_WORKSPACE', workspace=str(context['workspace']))
                    task = '\n\n'.join(part for part in (manifest['input'], task) if part)
                    context['task'] = task
                    answer = run_workflow(context, task)
                    _check_cancel(context)
                    _write(directory / 'result.txt', answer['answer'])
                    _check_cancel(context)
                    outcome = dict(result_file=str(directory / 'result.txt'),
                                   result_sha256=_evidence_sha256(owner, 'result.txt'))
                    if 'failed_checks' in context:
                        outcome['failed_checks'] = context['failed_checks']
                    _event(context, 'RUN_CHECK_FAILED' if context.get('failed_checks') else 'RUN_COMPLETED',
                           **outcome)
                except (WorkflowError, OSError, UnicodeError) as error:
                    _event(context, 'RUN_STOPPED', reason=str(error), automatic_retry=False,
                           status=('CANCELLED' if isinstance(error, WorkflowCancelled) else 'RECOVERY_REQUIRED'
                                   if isinstance(error, ProcessCleanupUnconfirmed) else 'STOPPED'))
                    raise
        print(answer['answer'])
        if failures := context.get('failed_checks'):
            print('Workflow checks failed: ' + ', '.join(
                f"{row['node']} (exit {row['returncode']})" for row in failures), file=sys.stderr)
            return 1
        return 0
    except (WorkflowError, OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        print('Workflow stopped: ' + str(error), file=sys.stderr)
        return 128 + error.signum if isinstance(error, WorkflowCancelled) else 1
