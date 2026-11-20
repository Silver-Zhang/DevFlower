"""Real Chromium and HTTP workflow editing with a scripted no-inference assistant receipt."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
import time
from unittest.mock import patch

from test_v2_assistant_native import connect, node, profile
from v2_browser import redact_trace
from v2.assistant_operations import AssistantOperations
from v2.cli_providers import CATALOG, empty
from v2.server import create_server
from v2.session_operations import KIND, operation_id
from v2.store import DraftStore
from studio.graph import Invalid


def main():
    parser = argparse.ArgumentParser()
    for field in ('artifacts', 'chromium', 'playwright-module'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    out = Path(args.artifacts).resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    project = out / 'projects' / 'Fresh'
    project.mkdir(parents=True)
    store = DraftStore(out / 'state', [project.parent])
    workspace = store.open_workspace(str(project))['id']
    model = dict(id='fixture-native-model', efforts=['low'], source='cli_catalog')
    store.db.put(CATALOG, 'codex', dict(empty('codex', 'Codex CLI'), refreshed_at=time.time(), authenticated=False,
        models=[model], default_model=model, default_effort='low', modes={'Ask': {'enabled': True}}))
    calls = []

    def configure(_operations, workspace_id, selection):
        assert workspace_id == workspace
        return dict(project=workspace_id, provider=selection['provider']), dict(selection,
            requested_model=model['id'], requested_effort='low', binding_sha256='e'*64, permission='none')

    def send(_operations, binding, intent, prompt, request_sha):
        assert binding['project'] == workspace and len(request_sha) == 64
        text = prompt.rsplit('用户本次明确请求\n', 1)[1]
        calls.append(text)
        if text == '创建两个使用同一配置的任务节点':
            reply = dict(summary='两个节点共用一项原生配置', profiles=[profile('Research')],
                operations=[node('First','agent',dict(profile='Research',task_prompt='FIRST_BROWSER_TASK')),
                            node('Second','agent',dict(profile='Research',task_prompt='SECOND_BROWSER_TASK')),
                            connect('Handoff','First','Second')])
        elif text == '把第二个改为 Claude 并在前面插入版本检查':
            reply = dict(summary='更新配置并插入 Tool', profiles=[profile('ClaudeReview','claude')], operations=[
                dict(op='update-node', id='Second', name='Claude Reviewer',
                     config=dict(profile='ClaudeReview',task_prompt='CHECK_BROWSER_TASK')),
                dict(op='remove-edge',id='Handoff'),node('Version','tool',dict(tool='command',argv=['python3','-V'])),
                connect('ToTool','First','Version'),connect('ToReview','Version','Second')])
        elif text == '恢复明确未发送的请求':
            if calls.count(text) == 1:
                raise Invalid('验收夹具：本机认证预检拒绝。')
            reply = dict(answer='已收到明确重试的合成回执')
        else:
            raise AssertionError(text)
        store.db.put(KIND, operation_id(workspace,intent), dict(workspace=workspace,intent_key=intent,
            provider='codex', authorization=AssistantOperations.authority, state='COMPLETED', result_handle='fixture',
            reply=json.dumps(reply, ensure_ascii=False), receipt=dict(provider='codex',resolved_model=model['id'])))

    with patch.object(AssistantOperations,'config',configure), patch.object(AssistantOperations,'send_bound',send):
        server, token = create_server(store, runtime_factory=lambda: (_ for _ in ()).throw(
            AssertionError('native assistant must remain independent')), codex_executable='/fixture/missing')
        Thread(target=server.serve_forever,daemon=True).start()
        env = dict(os.environ,V2_URL=f'http://127.0.0.1:{server.server_port}/#{token}', V2_PROJECT=str(project),
                   CHROMIUM=args.chromium,PLAYWRIGHT_MODULE=args.playwright_module)
        try:
            result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs')), str(out)], env=env, timeout=90)
            status = dict(browser_exit=result.returncode, synthetic_assistant_turns=len(calls), real_model_calls=0,
                          draft_count=len(store.drafts(workspace)), managed_run_count=len(store.db.list('run')),
                          native_run_count=len(store.db.list('script-run')))
            print(json.dumps(status))
            return int(bool(result.returncode or len(calls)!=4 or status['draft_count']!=2 or
                            status['managed_run_count'] or status['native_run_count']))
        finally:
            server.shutdown()
            server.server_close()
            for trace in out.glob('*.zip'):
                redact_trace(trace)


if __name__ == '__main__':
    raise SystemExit(main())
