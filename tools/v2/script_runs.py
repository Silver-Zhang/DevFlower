"""Publish and observe standalone Python artifacts from explicitly native drafts."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
import time

from devflower_export import (ExportError, UnsupportedGraph, agent_record, bound_record, compile_agent, compile_start, compile_workflow,
                              document_agents, read_saved, reference_key, workflow_paths)
from orchestration.common import canonical
from studio.graph import Invalid
from studio.server import Conflict
from workflow_script_runtime import SERIES_HASH, WorkflowError, _evidence_file, _run_directory, agent_prompt, failed_checks, workflow_environment

from .documents import validate_draft
from .store import private_directory

KIND = 'script-run'
EXECUTION = 'native-cli'


def compile_native(document, records, root):
    for node in document_agents(document):
        row = bound_record(records, node)
        if row.get('config', {}).get('execution') != EXECUTION:
            raise ExportError(node['id'] + '：请选择已保存的原生 CLI Agent 配置；受管工作流可使用原有运行入口。')
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

    def prompt_preview(self, workspace, document, ident, task, loop=None):
        root = Path(self.store.workspace(workspace)['path'])
        document = validate_draft(document)
        if not isinstance(task, str) or len(task) > 20000 or '\0' in task:
            raise Invalid('预览需要有效的任务文本。')
        graph = document
        parent = None
        if loop is not None:
            parent = next((node for node in document['nodes'] if node['id'] == loop and node.get('kind') == 'loop'), {})
            graph = parent.get('config', {}).get('body')
            if not graph:
                raise Invalid('找不到固定的循环子流程。')
        node = next((row for row in graph['nodes'] if row['id'] == ident and row.get('kind', 'agent') == 'agent'), None)
        if not node or 'agent_id' not in node:
            raise Invalid('请先为此节点绑定原生 CLI Agent。')
        key = reference_key(node)
        record = self.agents.get_native_version(workspace, *key)
        try:
            agent = compile_agent(node, {key: record}, root, {}, {}, preview=True)
            def initial(canvas):
                start = next((row for row in canvas['nodes'] if row.get('kind') == 'start'), None)
                return compile_start(start) if start else ''

            def receives(canvas, target):
                sources = {row['id']: row for row in canvas['nodes']}
                return any(edge['target'] == target and edge.get('transfer', {}).get('mode') != 'none'
                           and sources[edge['source']].get('kind') != 'start' for edge in canvas['edges'])

            entry_task = '\n\n'.join(part for part in (initial(graph) if parent else '', initial(document),
                                     task or '〔启动时填写的本次任务〕') if part)
            body_entry = bool(parent and any(edge['target'] == ident and any(
                row['id'] == edge['source'] and row.get('kind') == 'start' for row in graph['nodes'])
                for edge in graph['edges']))
            handoff = receives(document, loop) if body_entry else receives(graph, ident)
            return dict(node=ident, revision=record['revision'], prompt=agent_prompt(
                agent, entry_task, root, iteration='〔实际循环轮次〕' if loop else None,
                handoff='〔运行时核验后的上游交接〕' if handoff else None,
                feedback={'review': '〔仅返工轮次收到的核验证据〕'} if body_entry else None))
        except (ExportError, ValueError) as error:
            raise Invalid(str(error)) from None

    def check(self, workspace, document, *, proposed=()):
        root = self.store.workspace(workspace)['path']
        document = validate_draft(document)
        loops = [node for node in document['nodes'] if node.get('kind') == 'loop']
        branches = [node for node in document['nodes'] if node.get('kind') in ('router', 'condition')]
        if loops and branches:
            names = '、'.join(node['name'] for node in loops + branches)
            return dict(status='unsupported', execution_available=False,
                        message='暂不支持在同一原生脚本中组合循环与分支。可以保留草稿。',
                        issues=[dict(node_id=node['id'], message=names + '：请将分支和循环拆成独立工作流。')
                                for node in loops + branches])
        records = {(row['id'], row['revision']): row for row in proposed}
        issues = []
        for node in document_agents(document):
            try:
                key = reference_key(node)
                row = records.get(key) or (self.agents.get_native_version(workspace, *key) if key[0] else {})
                if row.get('revision') != node.get('agent_revision') or row.get('config', {}).get('execution') != EXECUTION:
                    raise Invalid('请选择已保存的原生 CLI Agent 配置。')
                records[key] = row
            except (Invalid, Conflict) as error:
                issues.append(dict(node_id=node['id'], message=node['name'] + '：' + str(error)))
        status = 'configuration_error'
        try:
            if issues:
                workflow_paths(document)
            else:
                compile_workflow(document, records, root, native_config=True)
                return dict(issues=[], execution_available=True, status='compilable',
                            message='原生脚本编译通过。检查未启动 CLI。')
        except (ExportError, Invalid, Conflict, OSError, ValueError) as error:
            if isinstance(error, UnsupportedGraph):
                status = 'unsupported'
            location = next((node['id'] for node in document['nodes']
                             if re.search(r'(?<![A-Za-z0-9_-])'+ re.escape(node['id']) + r'(?![A-Za-z0-9_-])', str(error))), None)
            issues.insert(0, dict(message=str(error), **({'node_id':location} if location else {})))
        message = '暂不支持编译 · 可以保存为设计草稿。' if status == 'unsupported' else '配置错误 · 可以保存草稿，修正后再编译。'
        return dict(issues=issues, execution_available=False, status=status, message=message)

    def launch(self, workspace, draft_id, revision, intent_key, task, *, task_files=None, continue_from=None):
        root = self.store.workspace(workspace)['path']
        if not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{32}', draft_id):
            raise Invalid('草稿身份无效。')
        if not isinstance(intent_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', intent_key):
            raise Invalid('启动意图身份无效。')
        if not isinstance(task, str) or len(task) > 20000 or '\0' in task:
            raise Invalid('本次任务需要 20000 字符以内的文本。')
        if task_files not in (None, []):
            raise Invalid('原生脚本不附加文件内容。可在任务中引用项目相对路径，由 CLI 按原生权限读取。')
        payload = dict(workspace=workspace, draft_id=draft_id, revision=revision, task=task, execution=EXECUTION)
        if continue_from is not None:
            if (not isinstance(continue_from, dict) or set(continue_from) != {'run_id', 'selection', 'version', 'sha256'}
                    or continue_from['version'] not in ('previous', 'current')
                    or not isinstance(continue_from['sha256'], str) or not SERIES_HASH.fullmatch(continue_from['sha256'])
                    or not isinstance(continue_from['selection'], list) or not 1 <= len(continue_from['selection']) <= 128
                    or any(not isinstance(key, str) or not 0 < len(key) <= 128 for key in continue_from['selection'])
                    or not task.strip()):
                raise Invalid('续接需要新任务、明确的脚本版本和所选的前轮成果。')
            payload['continue_from'] = continue_from
        prior = self.store.launch_intent(workspace, intent_key, payload)
        if prior:
            return self.snapshot(workspace, prior['run_id'])
        frozen = None
        metadata = None
        if continue_from:
            previous = self.get(workspace, continue_from['run_id'])
            if not previous or previous['run']['draft_id'] != draft_id:
                raise Conflict('前轮必须属于当前工作空间中的同一工作流。')
            metadata = self.verified_continuation(previous, root)
            keys = set(continue_from['selection'])
            if len(keys) != len(continue_from['selection']) or not keys <= {item['id'] for item in metadata['options']}:
                raise Conflict('所选成果无法核验，请重新读取前轮。')
            if continue_from['version'] == 'previous':
                if revision != previous['run']['draft_revision']:
                    raise Conflict('所选前轮冻结版本已变化。')
                frozen = previous
        if frozen:
            document, records, artifact = frozen['document'], {}, self.source(frozen)
            agent_versions, loop_versions = frozen['agents'], frozen.get('loop_agents', {})
        else:
            document, records, root, artifact = compile_saved(
                self.store, workspace, draft_id, revision, native_config=True, native_only=True)
            agent_versions, loop_versions = self.versions(document, records)
        task_bytes = task.encode('utf-8')
        source_bytes = artifact['source'].encode('utf-8')
        artifact_sha = artifact['sha256']
        if continue_from and artifact_sha != continue_from['sha256']:
            raise Conflict('实际脚本与您确认的版本不一致，请重新查看。')

        def publish(db, run_id):
            if not frozen:
                row = db.execute("SELECT data FROM records WHERE kind='draft' AND id=?", (draft_id,)).fetchone()
                current = json.loads(row[0]) if row else {}
                if (current.get('revision'), current.get('workspace')) != (revision, workspace):
                    raise Conflict('草稿在启动前已变化。')
            for key, record in records.items():
                if agent_record(db, *key) != record:
                    raise Conflict('Agent 配置在启动前已变化。')
            folder = self.folder(run_id, create=True)
            for filename, content in ((artifact['filename'], source_bytes), ('task.txt', task_bytes)):
                with os.fdopen(os.open(folder / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            run = dict(id=run_id, workspace=workspace, draft_id=draft_id, draft_revision=revision,
                       name=document['name'], launch_intent=intent_key, created_at=time.time(), execution=EXECUTION)
            parent = metadata['series'] if metadata else {}
            run['series'] = dict(id=parent.get('id', run_id), round=parent.get('round', 0) + 1, run_id=run_id,
                                 previous_run_id=parent.get('run_id'))
            db.execute('INSERT INTO records VALUES(?,?,?)', (KIND, run_id, canonical(dict(
                run=run, document=document, agents=agent_versions, loop_agents=loop_versions,
                artifact=dict(filename=artifact['filename'], sha256=artifact_sha, size=len(source_bytes)))).decode()))

        reservation = self.store.reserve_launch(workspace, intent_key, payload, artifact_sha, publish=publish)
        if reservation['created']:
            run_id = reservation['run_id']
            arguments = ['--run-id', run_id]
            if continue_from:
                arguments += ['--continue-from', str(self.folder(continue_from['run_id']) / 'evidence'),
                              '--previous-script-sha256', metadata['previous_hash'], '--accept-version', artifact_sha]
                for key in continue_from['selection']:
                    arguments += ['--inherit', key]
            self.spawn(self.folder(run_id), artifact['filename'], root, *arguments)
        return self.snapshot(workspace, reservation['run_id'])

    @staticmethod
    def versions(document, records):
        def versions(nodes):
            fields = ('provider', 'model', 'effort', 'role', 'round_prompt', 'output_prompt', 'cwd')
            return {node['id']: dict(id=node['agent_id'], revision=node['agent_revision'],
                    **{key: bound_record(records, node)['config'].get(key) for key in fields})
                    for node in nodes if 'agent_id' in node}

        body = next((node['config']['body'] for node in document['nodes'] if node.get('kind') == 'loop'), None)
        return versions(document['nodes']), versions(body['nodes']) if body else {}

    def folder(self, run_id, *, create=False):
        parent = self.store.state / 'script-runs'
        if create:
            private_directory(parent)
            (parent / run_id).mkdir(mode=0o700)
        return parent / run_id

    @staticmethod
    def command(folder, filename, *args, with_run_dir=True):
        return [sys.executable, '-I', '-B', str(folder / filename),
                *(['--run-dir', str(folder / 'evidence')] if with_run_dir else []), *args]

    def spawn(self, folder, filename, root, *args):
        command = self.command(folder, filename, '--workspace', root, '--task-file', str(folder / 'task.txt'), *args)
        with os.fdopen(os.open(folder / 'launcher.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as log:
            try:
                process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=log, start_new_session=True,
                                           env=workflow_environment())
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

    def summaries(self, workspace, ids):
        if not 1 <= len(ids) <= 12 or len(ids) != len(set(ids)):
            raise Invalid('每次可核对 1 至 12 条原生运行记录。')
        records = [record for run_id in ids if (record := self.get(workspace, run_id))]
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda record: self.summary(workspace, record), records))

    def summary(self, workspace, record):
        run = record['run']
        snapshot = self.snapshot(workspace, run['id'], record=record)
        script = snapshot['script']
        names = {item['id']: item['name'] for item in record['document']['nodes']}
        failures = script.get('failed_checks', [])
        if not failures and snapshot['state'] == 'STOPPED':
            for event in reversed(script.get('events', [])):
                if event['kind'] in ('CLI_FAILED', 'NOT_SENT', 'PREFLIGHT_FAILED', 'UNKNOWN',
                                     'OUTPUT_CONTRACT_FAILED', 'LOOP_LIMIT_REACHED'):
                    ident = event.get('loop_node') or event.get('node') or event.get('failed_node')
                    if ident:
                        failures = [dict(node=ident, returncode=event.get('returncode'))]
                    break
        try:
            with _run_directory(self.folder(run['id'])) as fd:
                task = self.excerpt(fd, 'task.txt')
        except (OSError, WorkflowError):
            task = None
        return dict(id=run['id'], state=snapshot['state'], failures=[dict(row, name=names.get(row['node'], row['node']))
                    for row in failures], task=task['text'][:360] if task else '')

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

    def read_script(self, folder, artifact, *arguments, with_run_dir=True):
        self.artifact(folder, artifact)
        result = subprocess.run(self.command(folder, artifact['filename'], *arguments, with_run_dir=with_run_dir),
                                cwd=folder, stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10,
                                env=workflow_environment())
        return json.loads(result.stdout)

    def verified_continuation(self, record, root):
        run, artifact = record['run'], record['artifact']
        if not run.get('series'):
            raise Conflict('本次运行早于工作流续接协议，仍可查看原始脚本与证据。')
        evidence = self.snapshot(run['workspace'], run['id'], record=record)['script']
        if evidence['status'] == 'CHECK_FAILED':
            raise Conflict('前轮存在未处理的检查失败。修复后重新运行，才能继续下一轮。')
        if evidence['status'] != 'COMPLETED' and not evidence.get('historical_completion'):
            raise Conflict('前轮尚未核验为成功完成，不能继续下一轮。')
        folder = self.folder(run['id'])
        try:
            result = self.read_script(folder, artifact, '--continuation-options',
                '--continue-from', str(folder / 'evidence'), '--workspace', root, with_run_dir=False)
        except subprocess.CalledProcessError as error:
            raise Conflict(error.stderr.decode('utf-8', errors='replace').strip()) from None
        except (OSError, subprocess.SubprocessError, ValueError, WorkflowError):
            raise Conflict('暂时无法核验前轮脚本与成果。') from None
        if result.get('series') != run['series'] or result.get('previous_hash') != artifact['sha256']:
            raise Conflict('前轮证据与冻结的运行身份不一致。')
        return result

    def continuation_options(self, workspace, run_id):
        record = self.get(workspace, run_id)
        if not record:
            raise Invalid('找不到此脚本运行。')
        root = self.store.workspace(workspace)['path']
        result = self.verified_continuation(record, root)
        run = record['run']
        result['draft_id'] = run['draft_id']
        result['previous_revision'] = run['draft_revision']
        result['previous_agents'] = record['agents']
        result['previous_loop_agents'] = record.get('loop_agents', {})
        try:
            draft = self.store.draft(workspace, run['draft_id'])
            document, records, _, artifact = compile_saved(
                self.store, workspace, run['draft_id'], draft['revision'], native_config=True, native_only=True)
            agents, loop_agents = self.versions(document, records)
            result['current'] = dict(name=document['name'], revision=draft['revision'], sha256=artifact['sha256'],
                                     agents=agents, loop_agents=loop_agents)
            result['changed'] = artifact['sha256'] != result['previous_hash']
            previous = record['document']
            changes = []
            for key in ('nodes', 'edges'):
                old = {item['id']: item for item in previous[key]}
                new = {item['id']: item for item in document[key]}
                for ident in dict.fromkeys((*old, *new)):
                    before, after = old.get(ident), new.get(ident)
                    if before != after:
                        item = after or before
                        name = (item['name'] if key == 'nodes'
                                else item['source'] + ' → ' + item['target'])
                        fields = ('name', 'task_prompt', 'provider', 'model', 'effort', 'round_prompt',
                                  'output_prompt', 'agent_revision', 'native_output', 'config') if key == 'nodes' else (
                                      'source', 'source_port', 'target', 'target_port', 'transfer')
                        before, after = before or {}, after or {}
                        changes.append(dict(kind=key, name=name,
                                            change='updated' if before and after else 'added' if after else 'removed',
                                            fields=[dict(key=field, before=before.get(field), after=after.get(field))
                                                    for field in fields if before.get(field) != after.get(field)]))
            result['changes'] = changes
        except (ExportError, Invalid, Conflict, OSError, ValueError) as error:
            result['current_error'] = str(error)
        return result

    def evidence(self, record, node=None, iteration=None):
        nodes = record['document']['nodes']
        loop = next((item for item in nodes if item.get('kind') == 'loop'), None)
        prefix = ''
        if iteration is not None:
            if (node is None or not loop or not isinstance(iteration, str) or not re.fullmatch(r'[1-5]', iteration)
                    or int(iteration) > loop['config'].get('max_iterations', 3)):
                raise Invalid('请选择本次循环的有效轮次。')
            prefix = 'iterations/' + iteration + '/'
            nodes = loop['config']['body']['nodes']
        actor = next((item for item in nodes if item['id'] == node), None)
        tool = actor and actor.get('kind') == 'tool' and actor['config'].get('tool') == 'command'
        if node is not None and not (actor and (tool or actor.get('kind', 'agent') == 'agent')):
            raise Invalid('节点不属于本次脚本运行。')
        folder = self.folder(record['run']['id'])
        try:
            self.artifact(folder, record['artifact'])
            with _run_directory(folder / 'evidence') as fd:
                if node is None:
                    return dict(result=self.excerpt(fd, 'result.txt'))
                base = prefix + node + '/'
                result = dict(prompt=self.excerpt(fd, base + 'prompt.txt'),
                              answer=self.excerpt(fd, base + 'answer.txt'),
                              structured=self.excerpt(fd, base + 'structured.json'),
                              candidate=self.excerpt(fd, base + 'candidate.json'),
                              stdout=self.excerpt(fd, base + 'stdout.log'),
                              stderr=self.excerpt(fd, base + 'stderr.log', tail=True))
                if tool and not prefix:
                    result['upstream'] = self.excerpt(fd, base + 'upstream.json')
                if prefix:
                    if not result['candidate']:
                        result['candidate'] = self.excerpt(fd, base + 'candidate-after.json')
                return result
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
            flags = ['--cancel' if cancel else '--status', '--details']
            evidence = self.read_script(folder, artifact, *flags)
            if (not cancel and run.get('series') and not evidence.get('active') and
                    (evidence['status'] == 'COMPLETED' or evidence.get('candidate_current') is False)):
                historical = self.read_script(folder, artifact, *flags, '--historical')
                if evidence['status'] == 'COMPLETED' or historical.get('status') == 'CHECK_FAILED':
                    evidence = historical
                elif historical.get('status') == 'COMPLETED' and historical.get('series') == run['series']:
                    evidence['historical_completion'] = True
            if (evidence['status'] == 'COMPLETED' or evidence.get('historical_completion')) and 'failed_checks' not in evidence:
                legacy = [item['id'] for item in record['document']['nodes']
                          if item.get('kind') == 'tool' and item.get('config', {}).get('tool') == 'command']
                proof = historical if evidence.get('historical_completion') else evidence
                failures = failed_checks(proof.get('events', []), legacy)
                if failures:
                    evidence = dict(proof, status='CHECK_FAILED', failed_checks=failures, legacy_tool_outcome=True)
            if evidence['script_sha256'] != artifact['sha256']:
                raise WorkflowError('运行证据与保存的脚本不一致。')
            if run.get('series') and evidence.get('series') != run['series']:
                raise WorkflowError('运行证据与保存的系列身份不一致。')
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
            artifact=artifact, document=record['document'], agents=record['agents'],
            loop_agents=record.get('loop_agents', record['agents']), **evidence))
