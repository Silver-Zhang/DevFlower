"""Read back the controller's checked JSON result for one Tool activation."""
import json
import os

from orchestration.common import canonical, digest, save
from orchestration.privacy import redact
from studio.graph import Invalid
from studio.server import Conflict
from studio.session_sources import opened

from .data_handoff import task_literals
from .run_inspector import IDENT, RUN, RunInspector

FORMAT = 'devflower.v2.tool-result'
MAX_BYTES = 16000


def artifact_path(state, run, activation):
    return state/'flow-artifacts'/run/activation/'tool-result.json'


def publish(rt, run, activation, report, verification_sha256):
    value = report['asserted_json']
    raw = canonical(value).decode()
    if RunInspector(rt.store).clean(value) != value or redact(raw, task_literals()) != raw:
        return {'available': False, 'reason': 'JSON 校验已通过，结果含受保护内容。'}
    content = dict(format=FORMAT, version=1, run=run['id'], activation=activation['id'],
                   value=value, verification_sha256=verification_sha256,
                   stdout_sha256=report['process']['sha256']['stdout'])
    encoded = canonical(content)
    if len(encoded) > MAX_BYTES:
        return {'available': False, 'reason': 'JSON 校验已通过，结果超过公开产物上限。'}
    save(artifact_path(rt.store.state, run['id'], activation['id']), content)
    return {'available': True, 'sha256': digest(encoded), 'bytes': len(encoded)}


def read(store, workspace, run, activation):
    store.workspace(workspace)
    if (not isinstance(run, str) or not RUN.fullmatch(run)
            or not isinstance(activation, str) or not IDENT.fullmatch(activation)):
        raise Invalid('请指定运行和工具激活。')
    item = store.db.get('run', run)
    node = store.db.get('activation', activation)
    if (item['workspace'] != workspace or node['run'] != run or node['kind'] != 'tool'
            or node['state'] != 'COMPLETED'
            or item.get('cancel_requested_at', float('inf')) <= node.get('finished_at', 0)):
        raise Conflict('工具产物不属于当前运行。')
    result = node.get('output', {}).get('result', {})
    ref = result.get('tool_artifact') or {}
    if ref.get('available') is not True:
        raise Invalid('此激活没有可读取的 JSON 产物。')
    try:
        with opened(artifact_path(store.state, run, activation)) as fd:
            if not 0 < os.fstat(fd).st_size <= MAX_BYTES:
                raise ValueError()
            content = json.loads(os.read(fd, MAX_BYTES+1))
        raw = canonical(content)
        if (len(raw) != ref['bytes'] or digest(raw) != ref['sha256']
                or content['format'] != FORMAT or (content['run'],content['activation']) != (run,activation)
                or content['verification_sha256'] != result['verification_sha256']
                or RunInspector(store).clean(content) != content
                or redact(raw.decode(), task_literals()) != raw.decode()):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise Conflict('工具产物与原激活的校验摘要不符。') from None
    return dict(content, sha256=ref['sha256'], bytes=ref['bytes'])
