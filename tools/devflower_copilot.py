"""Fixed isolated Copilot worker. One request, no shell, no automatic retry."""
import json,os,sys
sys.dont_write_bytecode=True
from studio.copilot_client import CopilotRPC

def main():
    raw=sys.stdin.buffer.read(200001)
    if len(raw)>200000:raise ValueError('Copilot 请求超过预算')
    value=json.loads(raw)
    with CopilotRPC(os.getcwd(),timeout=3500,executable=value['executable']) as client:
        if value['mode']=='metadata':
            result=client.catalog();result.update(available=True,machine_output=True,version=client.version,resume=True)
        else:
            prompt=value['prompt']
            answer=client.chat(value['model'],prompt,session_id=value['session_id'],resume=value['resume'])
            if value.get('conversation_format')=='text':
                from orchestration.adapters import CLI
                report=CLI.conversation_report(answer['text'])
            else:report=json.loads(answer['text'])
            from orchestration.common import digest
            result=dict(answer,report=report,provider_prompt_sha256=digest(prompt.encode()));result.pop('text')
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        secret=os.environ.get('COPILOT_GITHUB_TOKEN','')
        from studio.agent_sessions import safe_text
        message=str(exc).replace(secret,'[REDACTED]') if secret else str(exc)
        print(json.dumps({'error':safe_text(message)[:1500]},ensure_ascii=False));sys.exit(1)
