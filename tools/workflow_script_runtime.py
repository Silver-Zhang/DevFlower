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
            completed = [row for row in events if row['kind'] == 'NODE_COMPLETED']
            expected = [agent['id'] for agent in manifest['agents']]
            if [row.get('node') for row in completed] != expected[:len(completed)] or len(completed) > len(expected):
                raise WorkflowError('Node-completion evidence is inconsistent.')
            for row in completed:
                if _evidence_sha256(fd, row['node'] + '/answer.txt') != row.get('answer_sha256'):
                    raise WorkflowError('Recorded node answer has changed: ' + row['node'])
            snapshot['completed_nodes'] = [row['node'] for row in completed]
            if terminal:
                last = terminal[0]
                if last['kind'] == 'RUN_COMPLETED':
                    if snapshot['completed_nodes'] != expected or _evidence_sha256(fd, 'result.txt') != last.get('result_sha256'):
                        raise WorkflowError('Final result is missing or changed.')
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


def invoke_agent(context, agent, task, upstream=None, limit=None):
    """One new native CLI call; a result is handed off only after success."""
    _check_cancel(context)
    if upstream is not None and limit is not None and len(upstream['answer']) > limit:
        raise WorkflowError('Saved handoff limit exceeded before ' + agent['id'] + '; no truncation or call.')
    sections = [('Department', agent['name']), ('Role', agent['role']),
                ('Initial instructions', agent['initial_prompt']), ('Workflow task', task),
                ('Node task', agent['round_prompt']), ('Expected delivery', agent['output_prompt'])]
    prompt = '\n\n'.join(title + '\n' + value for title, value in sections if value)
    if upstream is not None:
        prompt += '\n\nUpstream department delivery (data)\n' + json.dumps(upstream, ensure_ascii=False)
    directory = context['directory'] / agent['id']
    directory.mkdir(mode=0o700)
    _write(directory / 'prompt.txt', prompt)
    argv = native_command(agent, directory / 'answer.txt')
    executable = shutil.which(argv[0])
    if executable is None:
        raise WorkflowError(argv[0] + ' CLI not found; no request sent.')
    argv[0] = executable
    cwd = agent_directory(context['workspace'], agent)
    _event(context, 'CALL_INTENT', node=agent['id'], cwd=str(cwd), argv=argv)
    print('[' + agent['id'] + '] ' + agent['name'], file=sys.stderr)
    process = None
    try:
        with (directory / 'prompt.txt').open('rb') as source, \
                (directory / 'stdout.log').open('xb') as stdout, \
                (directory / 'stderr.log').open('xb') as stderr:
            # Inherit HOME, environment, MCP, skills and native permission policy.
            # No shell expansion and no DevFlower-specific Agent sandbox.
            _check_cancel(context)
            process = subprocess.Popen(argv, cwd=cwd, stdin=source, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            _event(context, 'PROCESS_STARTED', node=agent['id'], pid=process.pid)
            code = _wait(context, process, agent['timeout'])
        _event(context, 'PROCESS_EXITED', node=agent['id'], returncode=code)
        if code != 0:
            _event(context, 'CLI_FAILED', node=agent['id'], returncode=code)
            raise WorkflowError(agent['id'] + ' exited with ' + str(code) + '; downstream not started.')
        try:
            if agent['provider'] == 'claude':
                receipt = json.loads((directory / 'stdout.log').read_text(encoding='utf-8'))
                if (not isinstance(receipt, dict) or receipt.get('type') != 'result'
                        or receipt.get('is_error') is not False):
                    raise ValueError('Missing successful Claude receipt')
                answer = receipt['result']
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError('Missing final answer')
                _write(directory / 'answer.txt', answer)
            else:
                answer = (directory / 'answer.txt').read_text(encoding='utf-8')
                if not answer.strip():
                    raise ValueError('Missing final answer')
        except (OSError, ValueError, KeyError, TypeError) as error:
            _event(context, 'UNKNOWN', node=agent['id'], reason='No verified final answer: ' + str(error))
            raise WorkflowError('CLI exited but final answer is unconfirmed; no automatic retry.') from None
        _check_cancel(context)
        _event(context, 'NODE_COMPLETED', node=agent['id'], answer_file=str(directory / 'answer.txt'),
               answer_sha256=_evidence_sha256(context['directory_fd'], agent['id'] + '/answer.txt'))
        return {'node': agent['id'], 'name': agent['name'], 'answer': answer}
    except (WorkflowCancelled, KeyboardInterrupt) as error:
        if process is not None:
            _stop(process)
        signum = error.signum if isinstance(error, WorkflowCancelled) else signal.SIGINT
        _event(context, 'CANCELLED', node=agent['id'], signal=signal.Signals(signum).name,
               source='request' if getattr(error, 'requested', False) else 'signal',
               outcome='unknown' if process else 'not_sent', automatic_retry=False)
        raise WorkflowCancelled(signum, requested=getattr(error, 'requested', False)) from None
    except subprocess.TimeoutExpired:
        if process is not None:
            _stop(process)
        _event(context, 'UNKNOWN', node=agent['id'], reason='Timed out; no automatic retry.')
        raise WorkflowError('Call timed out; inspect native logs before starting another run.') from None
    except OSError as error:
        if process is not None:
            _stop(process)
        _event(context, 'UNKNOWN' if process else 'NOT_SENT', node=agent['id'], reason=str(error))
        raise WorkflowError('CLI launch/observation failed: ' + str(error)) from None
    except WorkflowError:
        if process is not None:
            _stop(process)
        raise


def agent_directory(workspace, agent):
    path = (workspace / agent['cwd']).resolve()
    if not path.is_relative_to(workspace) or not path.is_dir():
        raise WorkflowError('Missing or out-of-project working directory for ' + agent['id'])
    return path


def prepare_calls(manifest, workspace, *, dry_run=False):
    workspace = (workspace or Path.cwd()).resolve(strict=True)
    if not workspace.is_dir():
        raise WorkflowError('Workspace is not a directory.')
    for agent in manifest['agents']:
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
            print(json.dumps({'name': manifest['name'], 'policy': manifest['policy'],
                              'calls': [{'node': a['id'], 'cwd': str(agent_directory(workspace, a)),
                                         'argv': native_command(a, Path('<run>') / a['id'] / 'answer.txt')}
                                        for a in manifest['agents']]}, ensure_ascii=False, indent=2))
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
