"""Small standard-library CLI driver embedded in exported workflow scripts.

This module does not import the DevFlower server, SQLite, V2 Runtime or a model
SDK. Native CLI configuration remains authoritative. Logs are local/private;
they can contain project source and native CLI output and are not sanitized.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


class WorkflowError(RuntimeError):
    pass


class WorkflowCancelled(WorkflowError):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('Cancelled by ' + signal.Signals(signum).name
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


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(value)


def _event(context, kind, **data):
    value = dict(kind=kind, at=datetime.now(timezone.utc).isoformat(), **data)
    with (context['directory'] / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


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
    cwd = context['workspace'] / agent['cwd']
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
        _event(context, 'NODE_COMPLETED', node=agent['id'], answer_file=str(directory / 'answer.txt'))
        return {'node': agent['id'], 'name': agent['name'], 'answer': answer}
    except (WorkflowCancelled, KeyboardInterrupt) as error:
        if process is not None:
            _stop(process)
        signum = error.signum if isinstance(error, WorkflowCancelled) else signal.SIGINT
        _event(context, 'CANCELLED', node=agent['id'], signal=signal.Signals(signum).name,
               outcome='unknown' if process else 'not_sent', automatic_retry=False)
        raise WorkflowCancelled(signum) from None
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


def script_main(manifest, run_workflow):
    parser = argparse.ArgumentParser(description=manifest['name'] + ' (native CLI workflow)')
    parser.add_argument('--workspace', type=Path, default=Path.cwd(), help='project root; defaults to current directory')
    parser.add_argument('--task', default='', help='task supplied to all departments')
    parser.add_argument('--task-file', type=Path, help='UTF-8 task file; cannot combine with --task')
    parser.add_argument('--run-dir', type=Path, help='new private evidence directory (must not already exist)')
    parser.add_argument('--dry-run', action='store_true', help='show commands; no CLI, model or file changes')
    args = parser.parse_args()
    try:
        if os.name != 'posix':
            raise WorkflowError('This first export driver is validated for POSIX/Linux only.')
        if args.task and args.task_file:
            raise WorkflowError('Choose --task or --task-file, not both.')
        workspace = args.workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise WorkflowError('Workspace is not a directory.')
        # Verify all departments before dispatching any of them.
        for agent in manifest['agents']:
            if not (workspace / agent['cwd']).is_dir():
                raise WorkflowError('Missing working directory for ' + agent['id'])
            if not args.dry_run and shutil.which(agent['provider']) is None:
                raise WorkflowError('Missing CLI: ' + agent['provider'])
        if args.dry_run:
            print(json.dumps({'name': manifest['name'], 'policy': manifest['policy'],
                              'calls': [{'node': a['id'], 'cwd': str(workspace / a['cwd']),
                                         'argv': native_command(a, Path('<run>') / a['id'] / 'answer.txt')}
                                        for a in manifest['agents']]}, ensure_ascii=False, indent=2))
            return 0
        task = args.task_file.read_text(encoding='utf-8') if args.task_file else args.task
        task = '\n\n'.join(part for part in (manifest['input'], task) if part)
        with _cancellation_signals() as cancellation:
            if args.run_dir:
                directory = args.run_dir.absolute()
                directory.mkdir(mode=0o700, parents=False, exist_ok=False)
            else:
                directory = Path(tempfile.mkdtemp(prefix='devflower-script-'))
            context = {'workspace': workspace, 'directory': directory, 'cancellation': cancellation}
            print('Private run evidence: ' + str(directory), file=sys.stderr)
            _write(directory / 'workflow.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            _event(context, 'RUN_STARTED', workflow=manifest['name'])
            try:
                _check_cancel(context)
                answer = run_workflow(context, task)
                _check_cancel(context)
                _write(directory / 'result.txt', answer['answer'])
                _check_cancel(context)
                _event(context, 'RUN_COMPLETED', result_file=str(directory / 'result.txt'))
            except (WorkflowError, OSError) as error:
                _event(context, 'RUN_STOPPED', reason=str(error), automatic_retry=False,
                       status='CANCELLED' if isinstance(error, WorkflowCancelled) else 'STOPPED')
                raise
        print(answer['answer'])
        return 0
    except (WorkflowError, OSError, UnicodeError) as error:
        print('Workflow stopped: ' + str(error), file=sys.stderr)
        return 128 + error.signum if isinstance(error, WorkflowCancelled) else 1
