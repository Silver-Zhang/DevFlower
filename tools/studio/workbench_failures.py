"""Fail closed on unexpected worker errors without replaying native effects."""
from pathlib import Path
import json
import os
import stat
import tempfile
import time
from orchestration.common import canonical, digest, Blocked
from orchestration.store import alive, lease
from .graph import Invalid
import sqlite3
import traceback
from orchestration.process import group_alive, stop
from orchestration.store import process_identity

TERMINAL={'COMPLETED','FAILED','CANCELLED','SKIPPED','RECOVERY_REQUIRED'}


def _recovery_path(runtime, run):
    return Path(runtime.flow.path).parent / ('recovery-' + digest(run.encode()) + '.json')


def recovery_notice(runtime, run):
    """A durable fail-closed notice, never proof of success or permission to replay."""
    path = _recovery_path(runtime, run)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > 4096:
            raise Blocked('恢复提示文件身份异常，禁止恢复执行')
        value = json.loads(os.read(fd, 4097))
    finally:
        os.close(fd)
    row = runtime.flow.get('run', run)
    if (value.get('version') != 1 or value.get('run') != run
            or value.get('created_at') != row.get('created_at')):
        raise Blocked('恢复提示与运行身份不一致，禁止恢复执行')
    return value


def _persist_recovery_notice(runtime, run, name):
    row = runtime.flow.get('run', run)
    path = _recovery_path(runtime, run)
    value = dict(version=1, run=run, created_at=row.get('created_at'), error_class=name,
                 reason='运行器已遇到异常；持久化写入可能未完成，需核对进程和回执，未自动重发。')
    fd, temporary = tempfile.mkstemp(prefix='.recovery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def _process_busy(record, *, group=False):
    if record is None:
        return False
    me=process_identity(os.getpid())
    if (not isinstance(record,dict) or not me
            or not {'host','boot_id','pid','start','pgid','sid'} <= set(record)
            or record['host'] != me['host']
            or any(type(record[k]) is not int or record[k]<=0 for k in ('pid','pgid','sid'))
            or not isinstance(record['start'],str) or not record['start'].isdigit()
            or not isinstance(record['boot_id'],str) or not record['boot_id']):
        raise Blocked('原进程身份或宿主未确认；未修改结局，也未重发')
    if record['boot_id'] != me['boot_id']:
        return False  # Same host, a prior boot cannot still own a live process.
    if group and (record['pgid'] != record['pid'] or record['sid'] != record['pid']):
        raise Blocked('原进程组身份未确认；未修改结局，也未重发')
    return group_alive(record) if group else alive(record)


def reconcile_interrupted(runtime, run):
    """Explicit local bookkeeping only; never launch, signal or invent a receipt."""
    notice = recovery_notice(runtime, run)
    if not notice:
        return
    # Serialize against worker startup/continuation, in addition to PID identity.
    with lease(Path(runtime.flow.path).parent / (run + '.worker.lock')):
        row = runtime.flow.get('run', run)
        if _process_busy(row.get('worker')):
            raise Blocked('原运行器仍在运行；未接管，也未启动第二个写入者')
        activations = [n for n in runtime.flow.list('activation') if n.get('run') == run]
        steps = [n for n in runtime.flow.list('native-step') if n.get('run') == run and n.get('kind') == 'PROCESS']
        if any(n.get('status')=='RUNNING' and not n.get('data',{}).get('process') for n in steps):
            raise Blocked('运行中的原生进程缺少身份；不能证明已退出，保留异常提示且不重发')
        try:
            native_busy = any(_process_busy(n.get('process'),group=True) for n in activations) or any(_process_busy(n.get('data', {}).get('process'),group=True) for n in steps)
        except (KeyError,TypeError,ValueError):
            raise Blocked('原生进程身份不完整；未修改结局，也未重发')
        if native_busy:
            raise Blocked('原生进程仍在运行或身份未确认；未修改结局，也未重发')
        # One transaction: if a writer still holds SQLite, keep the sidecar.
        with runtime.flow.tx(timeout=.2) as db:
            for kind, key, raw in db.execute("SELECT kind,id,data FROM records WHERE kind IN ('run','activation','native-step')").fetchall():
                data = json.loads(raw)
                if (kind == 'run' and key != run) or (kind != 'run' and data.get('run') != run):
                    continue
                if kind == 'run':
                    if data['state'] not in ('COMPLETED','FAILED','CANCELLED'):
                        data.update(state='RECOVERY_REQUIRED', reason=notice['reason'], recovery_reconciled_at=time.time())
                elif kind == 'activation' and data['state'] not in TERMINAL:
                    data.update(state='RECOVERY_REQUIRED', reason=notice['reason'])
                elif kind == 'native-step' and data.get('kind') == 'PROCESS' and data.get('status') == 'RUNNING':
                    data.update(status='RECOVERY_REQUIRED', data=dict(data.get('data', {}), observed_alive=False, outcome='unknown', reconciled_at=time.time()))
                db.execute('UPDATE records SET data=? WHERE kind=? AND id=?', (canonical(data).decode(), kind, key))
        _recovery_path(runtime, run).unlink()


def abort_unexpected(runtime,run,error):
    """Only this run's verified process groups may be stopped.

    Persist public error class/frames, never an exception's arbitrary payload,
    Provider output, credentials, or hidden reasoning. Completed receipts stay.
    """
    if runtime.flow.get('run',run).get('unexpected_error'):
        return  # First failure already persisted and owned groups were checked.
    name=type(error).__name__
    reason='运行器异常（'+name+'）；本次已停止调度，需核验原生回执，未自动重发。'
    frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name)
            for f in traceback.extract_tb(error.__traceback__)][-12:]
    import time
    diagnostic={}
    code=getattr(error,'sqlite_errorcode',None)
    if isinstance(error,sqlite3.Error) and type(code) is int and 0<=code<2**31:
        diagnostic['sqlite_errorcode']=code
        diagnostic['sqlite_errorname']={5:'SQLITE_BUSY',6:'SQLITE_LOCKED',8:'SQLITE_READONLY',
            10:'SQLITE_IOERR',11:'SQLITE_CORRUPT',13:'SQLITE_FULL',14:'SQLITE_CANTOPEN',
            19:'SQLITE_CONSTRAINT'}.get(code&255,'SQLITE_ERROR_OTHER')
    # The separate durable notice survives SQLite writer exhaustion. Cleanup
    # must precede any possibly blocked database update.
    try:_persist_recovery_notice(runtime,run,name)
    except OSError:pass  # The worker still stops its own effects and attempts SQLite.
    me=process_identity(os.getpid())
    for owned_row in runtime.flow.list('activation'):
        process=owned_row.get('process')
        if owned_row.get('run')==run and process and me and all(process.get(k)==me[k] for k in ('host','boot_id')):
            try:
                if group_alive(process):stop(process)
            except (OSError,KeyError,ValueError):pass
    runtime.flow.change('run',run,dict(state='RECOVERY_REQUIRED',reason=reason,unexpected_error=name,
                        error_frames=frames,finished_at=time.time(),**diagnostic),event={'kind':'worker-error','error_class':name})
    for row in runtime.flow.list('activation'):
        if row['run']!=run:continue
        if row['state'] not in TERMINAL:
            runtime.flow.change('activation',row['id'],dict(state='RECOVERY_REQUIRED' if row['state'] in ('STARTING','RUNNING') else 'SKIPPED',
                reason=reason,error_class=name,finished_at=time.time()),event={'kind':'node-failed','error_class':name})


def reconcile_native_steps(runtime,run):
    """Persist dead-process uncertainty after an owned worker has stopped."""
    import time
    for row in runtime.flow.list('native-step'):
        process=row.get('data',{}).get('process')
        if row.get('run')!=run or row.get('kind')!='PROCESS' or row.get('status')!='RUNNING' or not process:
            continue
        if not group_alive(process):
            runtime.flow.change('native-step',row['id'],dict(status='RECOVERY_REQUIRED',data=dict(row['data'],
                observed_alive=False,reconciled_at=time.time(),outcome='unknown',
                reconciliation_reason='原进程已不在运行，缺少可靠退出回执；未自动重发')))
