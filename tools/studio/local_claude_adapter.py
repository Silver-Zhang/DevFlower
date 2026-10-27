"""Claude local CLI: same canonical HOME/session, no credential overlays."""
from pathlib import Path
import json
import uuid
from orchestration.adapters import CLI,native_errors
from orchestration.common import Blocked,canonical,digest
from orchestration.privacy import credentials,redact
from orchestration.process import execute
from .native_local import native_environment


class LocalClaude:
    def __init__(self,binding,limits):self.b,self.limits=binding,limits

    def start(self,prompt,cwd,artifacts,output_schema=None,store=None,run=None,session=None):
        cwd=str(cwd)
        b=self.b;sid=session or b.get('session_id')
        # Native Claude's shell sandbox does not cover its file tools/hooks.
        # Do not claim a verified workspace-write boundary from a prompt.
        if b['role']=='implementer':raise Blocked('本机 Claude 写入边界尚未完成原生验证；当前仅开放 Ask/Plan，旧隔离配置保留')
        if sid and not isinstance(sid,str):raise Blocked('原生恢复需要明确 UUID')
        requested=sid or str(uuid.uuid4())
        args=[b['executable'],'-p','--output-format','json','--permission-mode','plan',
            '--tools','Read,Glob,Grep','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
            '--setting-sources','user','--model',b['model'],'--effort',b['effort'],
            '--resume' if sid else '--session-id',requested]
        if output_schema:
            schema=json.loads(Path(output_schema).read_text());schema.pop('$schema',None)
            args+=['--json-schema',json.dumps(schema)]
        key=store.intent(run,'AGENT_REQUEST',dict(kind='claude',project=b['project'],role=b['role'],
            requested_session=requested,requested_model=b['model'],requested_effort=b['effort'])) if store else None
        if store and callable(getattr(store,'visible_request',None)):store.visible_request(prompt)
        process,out,err=execute(args,cwd,b['home'],timeout=min(self.limits['step_timeout_seconds'],b.get('timeout_seconds',300)),
            cap=self.limits['max_log_bytes'],input_text=prompt,store=store,run=run,isolate=False,backend='native-local',
            env_extra=dict(native_environment(b),CLAUDE_CODE_MAX_RETRIES='0'),
            context={'provider':'claude','agent_role':b['role'],'transport':'native-local'})
        # Same native envelope/session verifier as the legacy CLI adapter.
        try:result=CLI('claude',b,self.limits).collect(process,out)
        except Blocked as exc:
            secret=credentials(b['home'],[p for p,_ in b.get('readonly_files',[])])
            detail=redact('; '.join(native_errors('claude',out))[:1200],secret)
            if store:store.finish(key,'AGENT_FAILED',process=process,error_category='native-local')
            raise Blocked(str(exc)+((': '+detail) if detail else '')) from None
        if result['session_id']!=requested:raise Blocked('原生 CLI 返回了不同的会话身份')
        secret=credentials(b['home'],[p for p,_ in b.get('readonly_files',[])])
        result['report']=redact(result['report'],secret)
        if store:store.finish(key,'AGENT_COMPLETED',actual_session=requested,transport={'mode':'native-local'})
        result.update(requested_model=b['model'],requested_effort=b['effort'],process=process,
            home=b['home'],cwd=cwd,provider='claude',permission_mode='native-plan/read-tools',
            transport={'mode':'native-local'},raw_report_sha256=digest(canonical(result['report'])),adapter_version='native-local/1')
        return result
