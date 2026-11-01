"""Bounded concurrent pipe draining, real identities, intent-before-spawn."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time
from .common import Blocked, clean_env
from .store import alive, process_identity
from . import sandbox


def group_alive(record):
    if not record or record['pgid'] != record['pid']:
        return False
    leader = process_identity(record['pid'])
    if leader and leader['start'] != record['start']:
        return False
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            member = process_identity(int(path.name))
            if member and member['state'] != 'Z' and member['pgid'] == record['pid'] and member['sid'] == record['pid'] and int(member['start']) >= int(record['start']):
                return True
    return False


def stop(record):
    if not group_alive(record):
        return False
    os.killpg(record['pid'], signal.SIGTERM)
    end = time.monotonic() + 1
    while group_alive(record) and time.monotonic() < end:
        time.sleep(.02)
    if group_alive(record):
        os.killpg(record['pid'], signal.SIGKILL)
    return True


def execute(argv, cwd, home, read=(), write=(), timeout=60, cap=1048576,
            store=None, run=None, input_text='', env_extra=None, isolate=True, backend='landlock', context=None, readonly_files=(), on_stdout=None, readonly_paths=(), readonly_dirs=(), hidden_paths=(), on_stderr=None):
    # Native session HOME persists; scratch is short enough for AF_UNIX sockets
    # and belongs to exactly one invocation. Cleanup cannot erase sessions.
    parent_scratch = tempfile.gettempdir()
    if len(os.fsencode(parent_scratch)) > 50:
        parent_scratch = '/tmp'
    with tempfile.TemporaryDirectory(prefix='dfp-', dir=parent_scratch) as scratch:
        return _execute(argv, cwd, home, read, write, timeout, cap, store, run,
                        input_text, env_extra, isolate, backend, context, readonly_files, scratch, on_stdout, readonly_paths, readonly_dirs, hidden_paths, on_stderr)


def _execute(argv, cwd, home, read, write, timeout, cap, store, run,
             input_text, env_extra, isolate, backend, context, readonly_files, scratch, on_stdout, readonly_paths=(), readonly_dirs=(), hidden_paths=(), on_stderr=None):
    if not argv or not isinstance(argv, list):
        raise Blocked('argv required')
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    env = clean_env(home)
    env.update(env_extra or {})
    env['TMPDIR'] = scratch
    parent = os.getpid()
    if (readonly_files or readonly_dirs or hidden_paths) and (backend != 'bwrap' or not isolate):
        raise Blocked('read-only credential overlays require bwrap')
    from .common import workspace_metadata
    metadata = workspace_metadata(cwd, check_git=True)
    if metadata and backend != 'native-local' and (backend != 'bwrap' or not isolate):
        raise Blocked('项目记录隔离需要强制的外层执行保护')
    overlays = []
    for source, relative in readonly_files:
        from .common import relative as safe_relative
        safe_relative(relative)
        source, dest = Path(source), home / relative
        if not source.is_absolute() or not source.is_file() or source.is_symlink():
            raise Blocked('credential overlay must name a regular operator file')
        if not dest.resolve().is_relative_to(home.resolve()) or dest.is_symlink():
            raise Blocked('credential overlay escapes dedicated HOME')
        dest.parent.mkdir(parents=True, exist_ok=True)
        overlays.append((source, dest))
    directory_overlays=[]
    for source,relative in readonly_dirs:
        if relative not in ('.agents/skills','.codex/skills','.claude/skills'):raise Blocked('unsupported native directory overlay')
        source=Path(source);dest=home/relative
        from studio.session_sources import opened
        with opened(source,directory=True):pass
        if not dest.resolve().is_relative_to(home.resolve()) or dest.is_symlink():raise Blocked('native directory overlay escapes HOME')
        dest.mkdir(parents=True,exist_ok=True)
        with opened(dest,directory=True):pass
        directory_overlays.append((str(source),str(dest)))
    for path in hidden_paths:
        if str(Path(path).relative_to(cwd)) not in ('.agents/skills','.codex/skills','.claude/skills'):raise Blocked('unsupported hidden native path')
        from studio.session_sources import opened
        with opened(path,directory=True):pass
    if backend == 'bwrap':
        wrapped = ['/usr/bin/bwrap', '--die-with-parent', '--unshare-user', '--unshare-pid',
                   '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev',
                   '--tmpfs', '/tmp']
        runtime = [p for p in sandbox.runtime_paths() if not p.startswith(('/proc/', '/dev/'))]
        paths = sorted(set(runtime + list(read)), key=lambda p: (len(Path(p).parts), str(p)))
        for p in paths:
            if Path(p).exists():
                wrapped += ['--ro-bind', str(Path(p).resolve()), str(p)]
        for p in [str(home), scratch, *write]:
            wrapped += ['--bind', str(Path(p).resolve()), str(p)]
        for p in readonly_paths:
            if Path(p).is_symlink():
                raise Blocked('protected native metadata cannot be a symlink')
            if Path(p).exists():
                wrapped += ['--ro-bind', str(Path(p).resolve()), str(p)]
        for source, dest in overlays:
            wrapped += ['--ro-bind', str(source), str(dest)]
        for source,dest in directory_overlays:
            wrapped += ['--ro-bind',source,dest]
        for path in hidden_paths:
            wrapped += ['--tmpfs',path,'--remount-ro',path]
        if metadata:
            # An empty read-only mount hides conversations and prevents writes,
            # including from a workspace-write Agent. The real DB stays hosted.
            wrapped += ['--tmpfs', metadata, '--remount-ro', metadata]
        command = [str(Path(argv[0]).resolve()) if Path(argv[0]).is_absolute() else argv[0], *argv[1:]]
        argv = wrapped + ['--remount-ro', '/', '--remount-ro', '/tmp', '--chdir', str(cwd), '--', *command]
    elif backend == 'native-local' and not isolate:
        pass  # Explicit host-approved native client; no added HOME/container.
    elif backend != 'landlock':
        raise Blocked('unknown isolation backend')

    def setup():
        # No orphan starts if controller dies in the spawn-to-record window.
        if sandbox.LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent:
            os._exit(125)
        if isolate and backend == 'landlock':
            sandbox.enforce(sandbox.runtime_paths() + list(read), ['/dev/null', str(home), scratch] + list(write))

    op = store.intent(run, 'PROCESS', {'argv': argv, 'cwd': str(cwd), 'context': context or {}}) if store else None
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, close_fds=True, preexec_fn=setup)
    try:
        # Record the physical spawn before any fallible journal publication.
        # This optional in-memory flag never grants execution authority.
        if store is not None and hasattr(store, 'process_started'):
            store.process_started = True
        record = process_identity(proc.pid)
        if store:
            store.finish(op, 'RUNNING', process=record)
    except BaseException:
        # Popen owns this unreaped child and its newly created session. Its
        # PID cannot be reused before wait(), even if no record was published.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        raise
    # Input is bounded and nonblocking too: no prompt-vs-output pipe deadlock.
    pending = memoryview(input_text.encode())
    sent_bytes = 0
    selector = selectors.DefaultSelector()
    for pipe, name in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, name)
    os.set_blocking(proc.stdin.fileno(), False)
    if pending:
        selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
    else:
        proc.stdin.close()
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    hashes = {k: hashlib.sha256() for k in buffers}
    counts = dict.fromkeys(buffers, 0)
    timed_out = overflow = cancelled = False
    start = time.monotonic()
    try:
        while selector.get_map():
            if time.monotonic() - start > timeout:
                timed_out = True
                stop(record)
                break
            if store and store.get(run)['data'].get('cancel_requested'):
                cancelled = True
                stop(record)
                break
            for key, _ in selector.select(.1):
                if key.data == 'stdin':
                    try:
                        written = os.write(key.fd, pending[:8192])
                        sent_bytes += written
                        pending = pending[written:]
                    except BrokenPipeError:
                        pending = pending[:0]
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        if sent_bytes == len(input_text.encode()) and store and callable(getattr(store, 'input_sent', None)):
                            store.input_sent(op, sent_bytes, hashlib.sha256(input_text.encode()).hexdigest())
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                name = key.data
                hashes[name].update(chunk)
                counts[name] += len(chunk)
                if sum(counts.values()) > cap:
                    overflow = True
                    stop(record)
                    break
                buffers[name].extend(chunk)
                if name == 'stdout' and on_stdout is not None:
                    on_stdout(chunk)
                if name == 'stderr' and on_stderr is not None:
                    on_stderr(chunk)
            if overflow:
                break
        if proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                timed_out = True
                stop(record)
        code = proc.wait(timeout=3)
        if group_alive(record):
            # A background child cannot turn an incomplete operation into success.
            stop(record)
            timed_out = True
    except BaseException:
        stop(record)
        proc.wait(timeout=3)
        if store:
            store.finish(op,'EXITED',exit=proc.returncode,process=record,timeout=timed_out,overflow=overflow,cancelled=cancelled,duration=round(time.monotonic()-start,3))
        raise
    finally:
        selector.close()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.close()
    result = {'operation_id': op, 'exit': code, 'process': record, 'timeout': timed_out,
              'overflow': overflow, 'cancelled': cancelled,
              'bytes': counts, 'sha256': {k: h.hexdigest() for k, h in hashes.items()},
              'duration': round(time.monotonic() - start, 3)}
    if store:
        store.finish(op, 'EXITED', **result)
    # Raw streams stay in memory only: no hidden reasoning/transcript disk logs.
    return result, bytes(buffers['stdout']), bytes(buffers['stderr'])
