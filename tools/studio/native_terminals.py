"""Bounded native PTYs. HTTP selects an authorized handle, never a shell command."""
from __future__ import annotations
import atexit
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import secrets
import selectors
import shlex
import sqlite3
import struct
import subprocess
import sys
import termios
import threading
import time

from orchestration.common import Blocked, canonical, clean_env, digest, tree_hash, workspace_hash
from orchestration.privacy import credentials
from orchestration.process import group_alive, stop
from orchestration.store import process_identity
from .graph import Invalid
from .server import Conflict
from .terminal_text import TerminalText

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED', 'RECOVERY_REQUIRED'}


def dimensions(cols, rows):
    if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 300 or not 5 <= rows <= 100:
        raise Invalid('终端尺寸超出范围')
    return cols, rows


class NativeTerminals:
    def __init__(self, runtime, sessions):
        self.rt, self.sessions = runtime, sessions
        self.entries, self.lock = {}, threading.RLock()
        atexit.register(self.shutdown)

    def prepare(self, data):
        if not {'workspace','handle'} <= set(data) or set(data)-{'workspace','handle','auto_verify'} or type(data.get('auto_verify',False)) is not bool: raise Invalid('终端只能选择已授权的会话，不能提交命令或路径')
        project, row = self.rt.session_center.resolve(data['workspace'], data['handle'])
        binding, reason = self.rt.session_center._binding(row, project)
        if not binding or binding['provider'] not in ('codex', 'claude'):
            raise Blocked(reason or '该 Provider 尚未支持原生终端')
        if self.sessions.busy(binding, row['native']['session_id']): raise Blocked('此会话已有写入者，请先停止原回合或连接已有终端')
        selected=self.rt.node_binding(data['workspace'],dict(profile=binding['runtime_profile'],
            model=binding['model'],effort=binding['effort'],mode='Ask',permission='read',skills=binding.get('skill_ids',[]),subagents=binding.get('subagents','disabled')),admission=not data.get('auto_verify',False))
        from .model_admission import ModelAdmission
        check = ModelAdmission(self.rt).pending_check(selected,include_valid=True) if data.get('auto_verify') else None
        value = dict(purpose='native-terminal', workspace=data['workspace'], handle=data['handle'],
            provider=binding['provider'], model=binding['model'], effort=binding['effort'],
            cwd=row['native']['cwd'], session_id=row['native']['session_id'], native_sha256=row['native']['sha256'],
            home=binding['home'], source_sha256=workspace_hash(project['path']), candidate_sha256=self.rt.candidate_sha256,
            execution_binding=selected['binding'],admission_checks=[check] if check else [],
            operator_sha256=self.rt.operator_sha256, policy_sha256=self.rt.policy_sha,
            human_questions=['在原生只读CLI中打开此明确会话；使用原目录和HOME，并独占该会话写入锁。'])
        return self.rt.db.prepare(value)

    def start(self, data):
        if set(data) - {'id', 'sha256', 'answer', 'cols', 'rows'}: raise Invalid('终端启动参数不受支持')
        cols, rows = dimensions(data.get('cols', 100), data.get('rows', 28))
        with self.lock:
            try:
                prior = self.rt.flow.get('terminal-permit', data['id'])
                if prior['sha256'] != data['sha256']: raise Conflict('终端重试身份不一致')
                return self.snapshot(prior['workspace'], prior['terminal'])
            except Invalid: pass
            value, receipt = self.rt.db.consume(data['id'], data['sha256'], [data['answer']])
            if value.get('purpose') != 'native-terminal' or any(value[k] != actual for k, actual in (
                    ('candidate_sha256', self.rt.candidate_sha256), ('operator_sha256', self.rt.operator_sha256), ('policy_sha256', self.rt.policy_sha))):
                raise Blocked('终端授权不属于当前运行器')
            project, row = self.rt.session_center.resolve(value['workspace'], value['handle'])
            binding, reason = self.rt.session_center._binding(row, project)
            if not binding or self.sessions.busy(binding, value['session_id']): raise Blocked(reason or '原生会话已占用')
            if (binding['home'], row['native']['cwd'], row['native']['session_id'], row['native']['sha256']) != (
                    value['home'], value['cwd'], value['session_id'], value['native_sha256']): raise Conflict('原生身份或历史在确认后变化')
            if workspace_hash(project['path']) != value['source_sha256']: raise Conflict('工作区在终端预览后变化，请重新确认')
            from .model_admission import ModelAdmission
            verification=ModelAdmission(self.rt).require(value['execution_binding'], project['path'])
            if any(e['home'] == binding['home'] and e['proc'].poll() is None and
                   (binding.get('execution_backend')!='native-local' or e.get('session_id')==value['session_id'])
                   for e in self.entries.values()): raise Blocked('同一原生会话已有终端')
            if sum(e['proc'].poll() is None for e in self.entries.values()) >= 4: raise Blocked('已达到本机4个原生终端的上限')
            for old in list(self.entries):
                if len(self.entries) < 16: break
                if self.entries[old]['proc'].poll() is not None: del self.entries[old]
            key = secrets.token_hex(16)
            record = dict(id=key, workspace=value['workspace'], handle=value['handle'], provider=value['provider'],
                execution_backend=binding.get('execution_backend','isolated'),
                cwd=value['cwd'], session_id=value['session_id'], model=value['model'], effort=value['effort'], state='STARTING', created_at=time.time(),
                    input_sequence=0, input_owner='workbench', input_generation=0,
                    authorization_sha256=digest(canonical(receipt)), candidate_sha256=self.rt.candidate_sha256)
            with self.rt.flow.tx() as db:
                db.execute('INSERT INTO records VALUES(?,?,?)', ('terminal', key, canonical(record).decode()))
                db.execute('INSERT INTO records VALUES(?,?,?)', ('terminal-permit', data['id'], canonical(dict(
                    sha256=data['sha256'], terminal=key, workspace=value['workspace'])).decode()))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            argv = [sys.executable, '-B', str(Path(__file__).parents[1] / 'devflower_native_terminal.py')]
            for name, item in dict(operator=str(self.rt.operator_file), operator_sha256=self.rt.operator_sha256,
                    candidate_sha256=self.rt.candidate_sha256, policy=str(self.rt.policy_file), policy_sha256=self.rt.policy_sha,
                    workspace=value['workspace'], handle=value['handle']).items():
                argv += ['--' + name.replace('_', '-'), item]
            env = clean_env(); env.update(TERM='xterm-256color', COLORTERM='truecolor',
                                         DEVFLOWER_TERMINAL_PARENT_PID=str(os.getpid()),
                                         DEVFLOWER_TERMINAL_PARENT_START=process_identity(os.getpid())['start'])
            process=None;identity=None;entry=None
            try:
                process = subprocess.Popen(argv, cwd=value['cwd'], env=env, stdin=slave, stdout=slave, stderr=slave,
                                           start_new_session=True, close_fds=True)
                self.rt.children['terminal:'+key]=process
                os.set_blocking(master, False)
                identity = process_identity(process.pid)
                if identity is None:raise Blocked('原生终端进程身份未确认')
                secret = credentials(binding['home'], [x[0] for x in binding.get('readonly_files', [])])
                entry = dict(proc=process, master=master, process=identity, home=binding['home'], session_id=value['session_id'], text='', first=0, next=0,
                    execution_binding=value['execution_binding'], cwd=value['cwd'], admission_verification=verification,
                    total=0, filter=TerminalText(secret), inputs={}, owner='workbench', generation=0,
                    deadline=time.monotonic()+1800, state='RUNNING')
                self.entries[key] = entry
                self.rt.flow.change('terminal', key, {'state': 'RUNNING', 'process': identity})
                entry['thread']=threading.Thread(target=self._read, args=(key, entry), daemon=True, name='native-pty-'+key[:6])
                entry['thread'].start()
                self.rt.children.pop('terminal:'+key,None)  # The reader now owns it.
            except BaseException:
                from .spawn_ownership import reap_unpublished,failure_record
                reaped=process is None
                try:
                    if process is not None:reap_unpublished(process,identity);reaped=True
                finally:
                    if reaped:
                        self.entries.pop(key,None);self.rt.children.pop('terminal:'+key,None)
                    elif entry is not None:entry['state']='RECOVERY_REQUIRED'
                    # A reader that actually started owns its FD until finally.
                    thread=entry.get('thread') if entry else None
                    if thread is None or thread.ident is None:os.close(master)
                failure_record(lambda:self.rt.flow.change('terminal',key,dict(
                    state='RECOVERY_REQUIRED' if process is not None else 'FAILED',
                    reason='原生终端启动未确认；自有进程已回收，未自动重试')))
                raise
            finally: os.close(slave)
            return self.snapshot(value['workspace'], key)

    def _append(self, entry, raw, final=False):
        text = entry['filter'].feed(raw, final)
        entry['text'] += text; entry['next'] += len(text)
        if len(entry['text']) > 262144:
            entry['first'] += len(entry['text'])-262144; entry['text'] = entry['text'][-262144:]

    def _read(self, key, entry):
        selector = selectors.DefaultSelector(); selector.register(entry['master'], selectors.EVENT_READ)
        reason = None
        try:
            while entry['proc'].poll() is None:
                if time.monotonic() > entry['deadline'] or entry['total'] > 16*1024*1024:
                    reason = '终端达到30分钟或输出预算，已停止本终端'; stop(entry['process']); break
                for _, _ in selector.select(.1):
                    try: raw = os.read(entry['master'], 65536)
                    except OSError: raw = b''
                    if not raw: break
                    with self.lock: entry['total'] += len(raw); self._append(entry, raw)
            # Drain the real PTY once more; raw bytes never become framework log files.
            with self.lock:
                try:
                    while raw := os.read(entry['master'], 65536): self._append(entry, raw)
                except OSError: pass
                self._append(entry, b'', True)
                code = entry['proc'].wait(timeout=3)
                state = ('RECOVERY_REQUIRED' if entry['state'] == 'RECOVERY_REQUIRED' else
                         'CANCELLED' if entry.get('cancelled') else 'FAILED' if reason or code else 'COMPLETED')
                entry['state'] = state
                if state in ('FAILED','RECOVERY_REQUIRED'):
                    from .model_admission import ModelAdmission
                    ModelAdmission(self.rt).invalidate_verification(entry['execution_binding'],entry['cwd'],entry.get('admission_verification'))
                self.rt.flow.change('terminal', key, {'state': state, 'exit': code, 'reason': reason,
                    'finished_at': time.time(), 'output_bytes': entry['total']})
        except BaseException:
            stop(entry['process'])
            with self.lock:
                entry['state'] = 'RECOVERY_REQUIRED'
                self.rt.flow.change('terminal', key, {'state': 'RECOVERY_REQUIRED', 'reason': '终端连接结果未确认；未自动重发输入'})
        finally:
            selector.close(); os.close(entry['master'])
            if entry.get('attach'): entry['attach'].close()

    def snapshot(self, workspace, key, after=0):
        with self.lock:
            row = self.rt.flow.get('terminal', key)
            if row['workspace'] != workspace: raise Blocked('终端不属于此工作区')
            if type(after) is not int or after < 0: raise Invalid('终端输出位置无效')
            entry = self.entries.get(key)
            if not entry:
                return dict(row, state='RECOVERY_REQUIRED' if row['state'] not in TERMINAL else row['state'],
                            output='', next=after, reset=False, process_alive=group_alive(row.get('process')))
            reset = after < entry['first'] or after > entry['next']
            start = entry['first'] if reset else after
            from .terminal_phase import terminal_phase
            return dict(row, state=entry['state'], output=entry['text'][start-entry['first']:], next=entry['next'], reset=reset,
                        process_alive=entry['proc'].poll() is None, **terminal_phase(row['provider'],entry['text'],entry['proc'].poll() is None))

    def listing(self, workspace):
        self.rt.session_center.workspace(workspace)
        result=[]
        for row in self.rt.flow.list('terminal'):
            if row['workspace']!=workspace:continue
            result.append({k:v for k,v in self.snapshot(workspace,row['id']).items() if k!='output'})
            if len(result)>=20:break
        return result

    def _writer(self, entry, owner, generation):
        # Old clients have generation zero. After any handoff they must refresh
        # and explicitly claim; delayed requests can never regain the keyboard.
        if type(generation) is not int or generation != entry['generation'] or owner != entry['owner']:
            raise Conflict('输入已由另一端接管，请刷新后明确接管；未发送此次按键')

    def claim(self, workspace, key, generation, owner='workbench'):
        with self.lock:
            row = self.snapshot(workspace, key); entry = self.entries.get(key)
            if not entry or entry['proc'].poll() is not None or entry['state'] != 'RUNNING':
                raise Blocked('原生进程没有可接管的活动连接')
            if type(generation) is not int or generation != entry['generation']:
                raise Conflict('接管状态已变化，请重新读取，未强行抢占')
            if row.get('input_status') in ('SENDING', 'UNKNOWN'):
                raise Blocked('上次输入结果未确认，不允许接管或重发')
            next_generation = entry['generation'] + 1
            self.rt.flow.change('terminal', key, dict(input_generation=next_generation,
                input_owner='workbench' if owner == 'workbench' else 'local-terminal'))
            entry['owner'], entry['generation'] = owner, next_generation
            return self.snapshot(workspace, key)

    def release(self, workspace, key, generation, owner):
        with self.lock:
            self.snapshot(workspace, key); entry = self.entries.get(key)
            if not entry: raise Blocked('接管连接不存在')
            self._writer(entry, owner, generation)
            self.rt.flow.change('terminal', key, dict(input_generation=generation+1, input_owner='unclaimed'))
            entry['owner'], entry['generation'] = None, generation+1
            return self.snapshot(workspace, key)

    def detach(self, workspace, key, owner):
        with self.lock:
            entry = self.entries.get(key)
            if not entry or entry.get('owner') != owner: return
            try:
                self.release(workspace, key, entry['generation'], owner)
            except (OSError, sqlite3.Error, Blocked):
                # Keep input disabled if the durable release cannot be saved.
                entry['owner'] = None

    def share(self, workspace, key):
        with self.lock:
            row = self.snapshot(workspace, key); entry = self.entries.get(key)
            if not entry or entry['proc'].poll() is not None or entry['state'] != 'RUNNING':
                raise Blocked('请先打开此会话的原生终端；不会另启CLI或复制历史')
            from .native_attach import NativeAttach
            if not entry.get('attach'):
                entry['attach'] = NativeAttach(self, workspace, key)
            script = Path(__file__).parents[1] / 'devflower_attach.py'
            return dict(terminal=key, provider=row['provider'], session_id=row['session_id'],
                command=shlex.join([sys.executable, '-B', str(script), str(entry['attach'].path)]),
                mechanism='same-supervised-pty', desktop_connected=False,
                desktop_reason=('当前 Codex 桌面端未连接此PTY；该命令仅连接本地原生CLI，不代表桌面同步。'
                                if row['provider']=='codex' else None))

    def input(self, workspace, key, sequence, text, owner='workbench', generation=0):
        if not isinstance(text, str) or not 0 < len(text.encode()) <= 16384: raise Invalid('终端输入超过预算')
        with self.lock:
            row = self.snapshot(workspace, key); entry = self.entries.get(key)
            if not entry or entry['state'] != 'RUNNING' or entry['proc'].poll() is not None: raise Blocked('终端未运行，未重发输入')
            self._writer(entry, owner, generation)
            if type(sequence) is not int: raise Invalid('终端输入需要明确序号')
            sha = digest(text.encode())
            if sequence in entry['inputs']:
                if entry['inputs'][sequence] != sha: raise Conflict('重复输入序号的内容不同')
                if row.get('input_status')!='SENT':raise Blocked('上次终端输入结果未确认，不重发')
                return dict(accepted=True, duplicate=True, input_sequence=row['input_sequence'])
            if sequence != row['input_sequence'] + 1: raise Conflict('终端输入次序未确认；请重新连接，不要盲目重发')
            # Persist intent before touching the PTY; lost replies cannot replay input.
            self.rt.flow.change('terminal',key,{'input_sequence':sequence,'last_input_sha256':sha,'input_status':'SENDING'})
            entry['inputs'][sequence]=sha
            try:
                raw=text.encode();sent=os.write(entry['master'],raw)
                if sent != len(raw):raise OSError('partial PTY input')
                self.rt.flow.change('terminal',key,{'input_status':'SENT'})
            except (OSError,sqlite3.Error):
                entry['state']='RECOVERY_REQUIRED';stop(entry['process'])
                try:self.rt.flow.change('terminal',key,{'state':'RECOVERY_REQUIRED','input_status':'UNKNOWN'})
                except sqlite3.Error:pass
                raise Blocked('终端输入结果未确认，已停止，未自动重发') from None
            if len(entry['inputs'])>128:del entry['inputs'][min(entry['inputs'])]
            return dict(accepted=True,duplicate=False,input_sequence=sequence)

    def resize(self, workspace, key, cols, rows, owner='workbench', generation=0):
        cols, rows = dimensions(cols, rows)
        with self.lock:
            self.snapshot(workspace, key); entry=self.entries.get(key)
            if not entry or entry['proc'].poll() is not None: raise Blocked('终端没有活动连接')
            self._writer(entry, owner, generation)
            fcntl.ioctl(entry['master'],termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
        return dict(resized=True)

    def close(self, workspace, key):
        with self.lock:
            self.snapshot(workspace,key);entry=self.entries.get(key)
            if entry and entry['proc'].poll() is None:entry['cancelled']=True;stop(entry['process'])
        if entry and entry.get('thread') and entry['thread'].ident is not None and entry['thread'] is not threading.current_thread():entry['thread'].join(timeout=5)
        return dict(stopped=bool(entry),history_preserved=True)

    def shutdown(self):
        for entry in list(self.entries.values()):
            if entry['proc'].poll() is None:stop(entry['process'])
        for entry in list(self.entries.values()):
            if entry.get('thread') and entry['thread'].ident is not None and entry['thread'] is not threading.current_thread():entry['thread'].join(timeout=5)
