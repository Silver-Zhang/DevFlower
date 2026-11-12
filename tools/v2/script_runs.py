"""Publish and observe standalone Python artifacts from explicitly native drafts."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
import time

from devflower_export import ExportError, compile_workflow, read_saved
from orchestration.common import canonical
from studio.graph import Invalid
from studio.server import Conflict
from workflow_script_runtime import WorkflowError, _evidence_file, _run_directory

from .documents import validate_draft
from .store import private_directory

KIND = 'script-run'
EXECUTION = 'native-cli'


def compile_native(document, records, root):
    nodes = [node for node in document['nodes'] if node.get('kind', 'agent') == 'agent']
    if any(records.get(node.get('agent_id'), {}).get('config', {}).get('execution') != EXECUTION
           for node in nodes):
        raise ExportError('脚本运行需要每个 Agent 节点绑定已保存的原生 CLI 配置；受管工作流请使用原有运行入口。')
    return compile_workflow(document, records, root, native_config=True)


def compile_saved(store, workspace, draft_id, revision, *, native_config, native_only=False):
    space = store.workspace(workspace)
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise Invalid('需要当前已保存的工作流版本。')
    document, records, root = read_saved(store.state / 'v2.sqlite', workspace, draft_id, expected_revision=revision)
    if root != space['path']:
        raise Conflict('工作空间目录已变化，请重新打开。')
    filename, source = (compile_native(document, records, root) if native_only else
                        compile_workflow(document, records, root, native_config=native_config))
    return document, records, root, dict(filename=filename, source=source, sha256=sha256(source.encode()).hexdigest())


class ScriptRuns:
    def __init__(self, store, agents):
        self.store = store
        self.agents = agents

    def check(self, workspace, document):
        root = self.store.workspace(workspace)['path']
        document = validate_draft(document)
        try:
            records = {node['agent_id']: self.agents.get(workspace, node['agent_id'], node['agent_revision'])
                       for node in document['nodes'] if 'agent_id' in node}
            compile_native(document, records, root)
        except (ExportError, Invalid, Conflict, OSError, ValueError) as error:
            return dict(issues=[dict(message=str(error))], execution_available=False)
        return dict(issues=[], execution_available=True, message='原生脚本编译通过。检查未启动 CLI。')

    def launch(self, workspace, draft_id, revision, intent_key, task, *, task_files=None):
        self.store.workspace(workspace)
        if not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{32}', draft_id):
            raise Invalid('草稿身份无效。')
        if not isinstance(intent_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', intent_key):
            raise Invalid('启动意图身份无效。')
        if not isinstance(task, str) or len(task) > 20000 or '\0' in task:
            raise Invalid('本次任务需要 20000 字符以内的文本。')
        if task_files not in (None, []):
            raise Invalid('原生脚本不附加文件内容。可在任务中引用项目相对路径，由 CLI 按原生权限读取。')
        payload = dict(workspace=workspace, draft_id=draft_id, revision=revision, task=task, execution=EXECUTION)
        prior = self.store.launch_intent(workspace, intent_key, payload)
        if prior:
            return self.snapshot(workspace, prior['run_id'])
        document, records, root, artifact = compile_saved(
            self.store, workspace, draft_id, revision, native_config=True, native_only=True)
        task_bytes = task.encode('utf-8')
        source_bytes = artifact['source'].encode('utf-8')
        artifact_sha = artifact['sha256']
        agent_versions = {node['id']: dict(id=node['agent_id'], revision=node['agent_revision'],
                          provider=records[node['agent_id']]['config']['provider'])
                          for node in document['nodes'] if 'agent_id' in node}

        def publish(db, run_id):
            row = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (draft_id,)).fetchone()
            current = json.loads(row[0]) if row else {}
            if (current.get('revision'), current.get('workspace')) != (revision, workspace):
                raise Conflict('草稿在启动前已变化。')
            for key, record in records.items():
                row = db.execute("SELECT data FROM records WHERE kind='agent-config' AND id=?", (key,)).fetchone()
                if not row or json.loads(row[0]) != record:
                    raise Conflict('Agent 配置在启动前已变化。')
            folder = self.folder(run_id, create=True)
            for filename, content in ((artifact['filename'], source_bytes), ('task.txt', task_bytes)):
                with os.fdopen(os.open(folder / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            run = dict(id=run_id, workspace=workspace, draft_id=draft_id, draft_revision=revision,
                       name=document['name'], launch_intent=intent_key, created_at=time.time(), execution=EXECUTION)
            db.execute('INSERT INTO records VALUES(?,?,?)', (KIND, run_id, canonical(dict(
                run=run, document=document, agents=agent_versions,
                artifact=dict(filename=artifact['filename'], sha256=artifact_sha, size=len(source_bytes)))).decode()))

        reservation = self.store.reserve_launch(workspace, intent_key, payload, artifact_sha, publish=publish)
        if reservation['created']:
            self.spawn(self.folder(reservation['run_id']), artifact['filename'], root)
        return self.snapshot(workspace, reservation['run_id'])

    def folder(self, run_id, *, create=False):
        parent = self.store.state / 'script-runs'
        if create:
            private_directory(parent)
            (parent / run_id).mkdir(mode=0o700)
        return parent / run_id

    @staticmethod
    def command(folder, filename, *args):
        return [sys.executable, '-I', '-B', str(folder / filename),
                '--run-dir', str(folder / 'evidence'), *args]

    def spawn(self, folder, filename, root):
        command = self.command(folder, filename, '--workspace', root, '--task-file', str(folder / 'task.txt'))
        with os.fdopen(os.open(folder / 'launcher.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as log:
            try:
                process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=log, start_new_session=True)
            except OSError as error:
                log.write(('Python 启动失败：' + str(error) + '\n').encode('utf-8', errors='replace'))
                return
        # Reap while the GUI is present. A normal server exit leaves the session
        # and its redirected descriptors alone. No PID is stored or reused.
        Thread(target=process.wait, daemon=True).start()

    def get(self, workspace, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise Invalid('运行身份无效。')
        try:
            record = self.store.db.get(KIND, run_id)
        except Invalid:
            return None
        self.store.workspace(workspace)
        if record['run']['workspace'] != workspace:
            raise Conflict('运行不属于当前工作空间。')
        return record

    def list(self, workspace):
        self.store.workspace(workspace)
        return [record['run'] for record in self.store.db.list(KIND) if record['run']['workspace'] == workspace]

    @staticmethod
    def artifact(folder, artifact):
        filename = artifact['filename']
        if Path(filename).name != filename or not filename.endswith('.py'):
            raise WorkflowError('脚本文件名无效。')
        with _run_directory(folder.parent), _run_directory(folder) as fd, _evidence_file(fd, filename) as stream:
            content = stream.read(artifact['size'] + 1)
        if sha256(content).hexdigest() != artifact['sha256']:
            raise WorkflowError('保存的脚本字节已变化。')
        return content

    @staticmethod
    def excerpt(fd, name, *, tail=False):
        try:
            with _evidence_file(fd, name) as stream:
                size = os.fstat(stream.fileno()).st_size
                if tail:
                    stream.seek(max(0, size - 32000))
                value = stream.read(32000)
        except FileNotFoundError:
            return None
        return dict(text=value.decode('utf-8', errors='replace'), truncated=size > 32000)

    def source(self, record):
        artifact = record['artifact']
        try:
            source = self.artifact(self.folder(record['run']['id']), artifact)
        except (OSError, WorkflowError) as error:
            raise Conflict(str(error)) from None
        return dict(filename=artifact['filename'], sha256=artifact['sha256'], source=source.decode('utf-8'))

    def evidence(self, record, node=None):
        if node is not None and node not in record['agents']:
            raise Invalid('节点不属于本次脚本运行。')
        folder = self.folder(record['run']['id'])
        try:
            self.artifact(folder, record['artifact'])
            with _run_directory(folder / 'evidence') as fd:
                if node is None:
                    return dict(result=self.excerpt(fd, 'result.txt'))
                return dict(prompt=self.excerpt(fd, node + '/prompt.txt'),
                            answer=self.excerpt(fd, node + '/answer.txt'),
                            stderr=self.excerpt(fd, node + '/stderr.log', tail=True))
        except FileNotFoundError:
            return {}
        except (OSError, WorkflowError):
            raise Conflict('本次脚本的运行证据暂时不可读取。') from None

    def snapshot(self, workspace, run_id, *, record=None, cancel=False):
        record = record or self.get(workspace, run_id)
        if record is None:
            raise Invalid('找不到此脚本运行。')
        run, artifact = record['run'], record['artifact']
        folder = self.folder(run_id)
        try:
            self.artifact(folder, artifact)
            result = subprocess.run(self.command(folder, artifact['filename'], '--cancel' if cancel else '--status',
                                                 '--details'), cwd=folder, stdin=subprocess.DEVNULL,
                                    capture_output=True, check=True, timeout=10)
            evidence = json.loads(result.stdout)
            if evidence['script_sha256'] != artifact['sha256']:
                raise WorkflowError('运行证据与保存的脚本不一致。')
        except (OSError, subprocess.SubprocessError, ValueError, WorkflowError) as error:
            reason = '尚未读到该脚本的可核验状态。'
            try:
                with _run_directory(folder.parent), _run_directory(folder) as fd:
                    launch = self.excerpt(fd, 'launcher.log', tail=True)
                if launch and launch['text']:
                    reason = launch['text']
            except (OSError, WorkflowError):
                pass
            if isinstance(error, WorkflowError):
                reason = str(error)
            elif isinstance(error, subprocess.CalledProcessError) and (folder / 'evidence').is_dir():
                reason = error.stderr.decode('utf-8', errors='replace').strip() or reason
            evidence = dict(status='RECOVERY_REQUIRED', reason=reason)
        return dict(run=run, state=evidence['status'], script=dict(
            artifact=artifact, document=record['document'], agents=record['agents'], **evidence))
