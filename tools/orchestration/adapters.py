"""Real CLI adapters: explicit project/role sessions; no newest-session guessing."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import uuid
import os
import re
from contextlib import nullcontext
from .provider_transport import claude_transport
from .common import Blocked, canonical, clean_env, cli_probe_env, digest
from .privacy import credentials, redact
from .process import execute

VERSION = 1
AUTH_FAILURE = re.compile(rb'(?i)(not logged in|not authenticated|unauthorized|\b40[13]\b|'
    rb'authentication[ _:-]*(?:failed|error|required)|'
    rb'(?:invalid|missing|expired|incorrect).{0,32}(?:api[ _-]?key|auth|token|credential)|'
    rb'(?:api[ _-]?key|auth(?:entication)?|credential|token).{0,32}(?:invalid|expired|missing|rejected))')


def native_errors(kind, raw):
    """Read provider error envelopes, never chat, tool results or quoted source."""
    messages = []
    for line in ([raw] if kind == 'claude' else raw.splitlines()):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            # A truncated event must not hide an earlier/later native error.
            continue
        if not isinstance(event, dict):
            continue
        values = []
        if kind == 'codex':
            if event.get('type') == 'error':
                values = [event.get('message')]
            elif event.get('type') == 'turn.failed' and isinstance(event.get('error'), dict):
                values = [event['error'].get('message')]
        elif event.get('type') == 'result' and event.get('is_error') is True:
            errors = event.get('errors', [])
            values = [*errors] if isinstance(errors, list) else []
            values.append(event.get('result'))
        messages.extend(value for value in values if isinstance(value, str))
    return messages


def probe(executable, kind):
    def call(args):
        try:
            p = subprocess.run([executable, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=15, env=dict(cli_probe_env(), HOME=str(Path.home())))
            return p.returncode, p.stdout.decode(errors='replace')
        except (OSError, subprocess.TimeoutExpired):
            return -1, ''
    code, version = call(['--version'])
    _, help_text = call(['exec', '--help'] if kind == 'codex' else ['--help'])
    auth_code, auth_text = call(['login', 'status'] if kind == 'codex' else ['auth', 'status'])
    if kind == 'claude':
        try:
            authenticated = json.loads(auth_text).get('loggedIn') is True
        except ValueError:
            authenticated = False
    else:
        # Codex writes login status to stderr in some versions; exit is authoritative.
        authenticated = auth_code == 0
    return {'version': version.strip()[:160], 'available': code == 0,
            'authenticated': authenticated,
            'auth_probe_scope': 'local-cli-state-not-a-service-request',
            'machine_output': '--json' in help_text if kind == 'codex' else '--output-format' in help_text,
            'resume': 'resume' in help_text, 'adapter_version': VERSION}


class CLI:
    def __init__(self, kind, binding, limits):
        self.kind, self.binding, self.limits = kind, binding, limits

    def start(self, prompt, cwd, artifacts, output_schema=None, store=None, run=None, session=None):
        if self.binding.get('execution_backend') == 'native-local':
            if self.kind == 'codex':
                from studio.local_codex_adapter import LocalCodex
                return LocalCodex(self.binding,self.limits).start(prompt,cwd,artifacts,output_schema,store,run,session)
            if self.kind == 'claude':
                from studio.local_claude_adapter import LocalClaude
                return LocalClaude(self.binding,self.limits).start(prompt,cwd,artifacts,output_schema,store,run,session)
            raise Blocked('该 Provider 尚未支持本机原生会话')
        if self.kind == 'copilot':
            from studio.copilot_adapter import CopilotAdapter
            return CopilotAdapter(self.binding,self.limits).start(prompt,cwd,artifacts,output_schema,store,run,session)
        b = self.binding
        if not Path(b['executable']).is_file() or not os.access(b['executable'], os.X_OK):
            raise Blocked('CLI executable unavailable before spawn')
        secrets = credentials(b['home'], [x[0] for x in b.get('readonly_files', [])])
        session = session or b['session_id']
        if session:
            try:
                if str(uuid.UUID(session)) != session:
                    raise ValueError()
            except ValueError:
                raise Blocked('session must be UUID') from None
        requested_session = session
        env = {}
        scratch = Path(artifacts) / ('scratch-' + b['role'])
        scratch.mkdir(exist_ok=True)
        read_only = b['role'] != 'implementer' or b.get('admission_probe', False)
        if b.get('conversation_format') != 'text':
            prompt = (f"Role: {b.get('responsibility', b['role'])}. Background (task data): {b.get('background', '')}\n"
                      f"Writable test artifacts: {scratch}. Source is {'read-only' if read_only else 'writable'}.\n" + prompt)
        if self.kind == 'codex':
            external = b.get('external_sandbox', False)
            if external and b.get('isolation') != 'bwrap':
                raise Blocked('cannot run external CLI sandbox without bwrap')
            argv = [b['executable'], 'exec']
            if session:
                argv += ['resume', session]
            argv += ['--json', '--skip-git-repo-check', '--ignore-user-config', '--ignore-rules', '-c',
                     'sandbox_mode="danger-full-access"' if external else 'sandbox_mode="workspace-write"',
                     '-c', 'approval_policy="never"']
            if b.get('no_tools'):
                for feature in ('shell_tool','unified_exec','code_mode_host','apps','browser_use','computer_use','view_image','workspace_dependencies'):
                    argv+=['-c','features.'+feature+'=false']
                argv+=['-c','mcp_servers={}']
            if output_schema:
                argv += ['--output-schema', str(output_schema)]
            if b['model'] != 'unknown':
                argv += ['--model', b['model']]
            if b['effort'] != 'unknown':
                argv += ['-c', 'model_reasoning_effort=' + json.dumps(b['effort'])]
            argv += ['-']
        else:
            tools = list(b.get('tools', ['Read', 'Glob', 'Grep']))
            if b.get('subagents')=='readonly':tools.append('Agent')
            dynamic = bool(set(tools) - {'Read', 'Glob', 'Grep'})
            if dynamic and b.get('isolation') != 'bwrap':
                raise Blocked('dynamic tools require mandatory outer bwrap')
            argv = [b['executable'], '-p', '--output-format', 'json', '--permission-mode',
                    'bypassPermissions' if dynamic else 'plan',
                    '--tools', ','.join(tools), '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                    '--setting-sources', 'user']
            if session:
                argv += ['--resume', session]
            else:
                requested_session = str(uuid.uuid4())
                argv += ['--session-id', requested_session]
            if output_schema:
                # Claude's bundled validator does not register draft-2020-12.
                # Provider gets the supported shape; full shipped contract is
                # still checked by the controller after collection.
                provider_schema = json.loads(Path(output_schema).read_text())
                provider_schema.pop('$schema', None)
                argv += ['--json-schema', json.dumps(provider_schema)]
            if b['model'] != 'unknown':
                argv += ['--model', b['model']]
            if b['effort'] != 'unknown':
                argv += ['--effort', b['effort']]
        from .native_features import native_args,skill_mounts
        extension_args,extension_env=native_args(dict(b,provider=self.kind));argv=argv[:-1]+extension_args+argv[-1:] if self.kind=='codex' else argv+extension_args
        env.update(extension_env)
        mounts,masks=skill_mounts(dict(b,provider=self.kind),cwd,artifacts)
        request_op = store.intent(run, 'AGENT_REQUEST', {'kind': self.kind, 'project': b['project'],
            'role': b['role'], 'requested_session': requested_session,
            'requested_model': b['model'], 'requested_effort': b['effort'],
            'skills':[{k:v[k] for k in ('id','sha256')} for v in b.get('skills',[])], 'subagents':b.get('subagents','disabled')}) if store else None
        if store and callable(getattr(store, 'visible_request', None)):
            # Application-owned visible request, never system instructions or hidden reasoning.
            store.visible_request(prompt)
        pending_events = bytearray()
        observed_session = None
        def observe(chunk):
            nonlocal observed_session
            if self.kind != 'codex' or not store or observed_session:
                return
            pending_events.extend(chunk)
            while b'\n' in pending_events:
                line, _, rest = pending_events.partition(b'\n');pending_events[:] = rest
                try:
                    event = json.loads(line)
                    if event.get('type') != 'thread.started':
                        continue
                    sid = event['thread_id']
                    if str(uuid.UUID(sid)) != sid:
                        continue
                    observed_session = sid
                    store.finish(request_op, 'AGENT_SESSION', actual_session=sid,
                                 resolved_model=event.get('model', 'unknown'))
                    return
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            if len(pending_events) > 1048576:
                pending_events.clear()
        step_timeout = min(self.limits['step_timeout_seconds'], b.get('timeout_seconds', self.limits['step_timeout_seconds']))
        transport = claude_transport(b, timeout=step_timeout) if self.kind == 'claude' else nullcontext(([], {'mode': 'native'}))
        try:
            with transport as (extra_args, transport_evidence):
                result, out, err = execute(argv + extra_args, cwd, b['home'],
                    read=[str(cwd), str(artifacts), *b['read_paths'], *([str(output_schema)] if output_schema else [])],
                    write=[str(scratch)] + ([] if read_only else [str(cwd)]),
                    timeout=step_timeout, cap=self.limits['max_log_bytes'],
                    input_text=prompt, store=store, run=run, env_extra=env, backend=b.get('isolation', 'landlock'), readonly_files=b.get('readonly_files', []),
                    context={'agent_role': b['role'], 'provider': self.kind, 'transport': transport_evidence['mode']},
                    on_stdout=observe, readonly_paths=[str(Path(cwd)/'.git')],readonly_dirs=mounts,hidden_paths=masks)
        except Blocked as exc:
            category = 'authentication' if AUTH_FAILURE.search(str(exc).encode()) else 'configuration'
            if store:
                store.finish(request_op, 'AGENT_FAILED', error_category=category)
            raise
        errors = native_errors(self.kind, out)
        authentication = AUTH_FAILURE.search('\n'.join(errors).encode(errors='replace') + b'\n' + err)
        if result['exit'] != 0 or result['timeout'] or result['overflow'] or result['cancelled']:
            detail = redact('; '.join(errors)[:1200], secrets)
            category = ('cancelled' if result['cancelled'] else 'timeout' if result['timeout'] else
                        'output-budget' if result['overflow'] else
                        'authentication' if authentication else 'process-failure')
            if store:
                store.finish(request_op, 'AGENT_FAILED', error_category=category, process=result, transport=transport_evidence,
                             **({'visible_error': detail} if detail else {}))
            raise Blocked('CLI ' + category + '; exit=' + str(result['exit']) + (': ' + detail if detail else ''))
        try:
            envelope = self.collect(result, out)
        except Blocked:
            # Provider error envelopes can accompany exit=0. Classify only
            # after collection rejects the receipt, never successful chat text.
            category = 'authentication' if authentication else 'protocol'
            if store:
                store.finish(request_op, 'AGENT_FAILED', error_category=category, process=result, transport=transport_evidence)
            if category == 'authentication':
                raise Blocked('CLI authentication; exit=' + str(result['exit'])) from None
            raise
        if requested_session and envelope['session_id'] != requested_session:
            raise Blocked('actual session does not match explicit binding')
        if store:
            store.finish(request_op, 'AGENT_COMPLETED', actual_session=envelope['session_id'], transport=transport_evidence)
        envelope['raw_report_sha256'] = digest(canonical(envelope['report']))
        envelope['report'] = redact(envelope['report'], secrets | credentials(b['home'], [x[0] for x in b.get('readonly_files', [])]))
        envelope.update(requested_model=b['model'], requested_effort=b['effort'], transport=transport_evidence,
                        cwd=str(cwd), home=b['home'], provider=self.kind,
                        permission_mode=('external-bwrap/tool-allowlist' if dynamic else 'plan/read-tools') if self.kind == 'claude' else
                            'external-bwrap/never' if b.get('external_sandbox') else 'workspace-write/never',
                        skills=[{k:v[k] for k in ('id','sha256')} for v in b.get('skills',[])],subagents=b.get('subagents','disabled'),adapter_version=VERSION)
        if envelope['resolved_model'] != 'unknown' and b['model'] != 'unknown':
            # Aliases cannot be compared with a resolved provider ID. Retain both.
            if b['model'].startswith(('gpt-', 'claude-')) and b['model'] != envelope['resolved_model']:
                raise Blocked('resolved model changed')
        return envelope

    def collect(self, process, raw):
        if process['exit'] != 0 or process['timeout'] or process['overflow'] or process['cancelled']:
            raise Blocked('CLI process failed, interrupted or truncated')
        try:
            if self.kind == 'claude':
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise Blocked('Claude terminal envelope must be an object')
                if obj.get('type') != 'result' or obj.get('is_error') or obj.get('subtype') != 'success':
                    raise Blocked('missing successful Claude terminal envelope')
                sid = obj.get('session_id')
                report = obj.get('structured_output')
                if report is None:
                    report = self.conversation_report(obj['result']) if self.binding.get('conversation_format') == 'text' else json.loads(obj['result'])
                models = list(obj.get('modelUsage', {}))
                resolved = models[0] if len(models) == 1 else 'unknown'
                usage = {'total_cost_usd': obj.get('total_cost_usd', 'unknown'),
                         'semantics': 'session-cumulative-do-not-sum', 'usage': obj.get('usage', {})}
            else:
                events = [json.loads(line) for line in raw.splitlines() if line.strip()]
                if not all(isinstance(e, dict) for e in events):
                    raise Blocked('Codex events must be objects')
                starts = [e for e in events if e.get('type') == 'thread.started']
                ends = [e for e in events if e.get('type') == 'turn.completed']
                if len(starts) != 1 or len(ends) != 1 or events[-1].get('type') != 'turn.completed':
                    raise Blocked('missing Codex terminal or real session')
                sid = starts[0].get('thread_id')
                texts = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                         and e.get('item', {}).get('type') == 'agent_message']
                report = self.conversation_report(texts[-1]) if self.binding.get('conversation_format') == 'text' else json.loads(texts[-1])
                resolved = starts[0].get('model', 'unknown')
                usage = {'usage': ends[0].get('usage', {}), 'total_cost_usd': 'unknown', 'semantics': 'turn'}
            if not isinstance(sid, str):
                raise Blocked('CLI session id must be a UUID string')
            if str(uuid.UUID(sid)) != sid:
                raise Blocked('CLI session id must be a canonical exact UUID')
            if type(report) is not dict:
                raise Blocked('structured result object required')
        except (ValueError, KeyError, IndexError, TypeError):
            raise Blocked('invalid CLI structured output') from None
        return {'session_id': sid, 'resolved_model': resolved, 'resolved_effort': 'unknown',
                'report': report, 'usage': usage, 'process': process}

    @staticmethod
    def conversation_report(text):
        if not isinstance(text, str) or not text.strip():
            raise Blocked('missing visible native conversation answer')
        # Compatibility with already-produced reply-only envelopes. Ordinary
        # native text needs no JSON Schema tool and remains a native message.
        try:
            value = json.loads(text)
            if isinstance(value, dict) and set(value) == {'reply'} and isinstance(value['reply'], str):
                return value
        except ValueError:
            pass
        return {'reply': text}

    def resume(self, session, **kwargs):
        if not session:
            raise Blocked('explicit session required')
        return self.start(session=session, **kwargs)

    @staticmethod
    def cancel(process):
        from .process import stop
        return stop(process)
