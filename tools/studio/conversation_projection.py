"""Readable, ordered turns backed by exact Controller/native session identities."""
from collections import Counter
from orchestration.privacy import redact
from .agent_sessions import safe_text


def conversation_turns(manager, workspace, row, related, messages, secrets, *, original_messages=()):
    text=lambda value:safe_text(redact(value,secrets)) if isinstance(value,str) else ''
    native_answers=Counter(m['text'].strip() for m in messages if m['role']=='assistant')
    # Request all validated results here. Legacy execution_results still separately
    # suppresses text already present in native history for its original API.
    answers={r['id']:r for r in manager.execution_results(workspace,row,related,[],'',secrets)}
    turns=[]; operations=[]
    def origin(answer):
        if answer and native_answers[answer.strip()]:
            native_answers[answer.strip()]-=1
            return 'native-text'
        return 'saved-runtime-result'
    for a,chat in related:
        run=manager.rt.snapshot(a['run']);answer=answers.get(a['id'])
        state=a['state']
        if state=='COMPLETED' and not answer or state not in ('COMPLETED','FAILED','CANCELLED','SKIPPED') and run['state']=='RECOVERY_REQUIRED':state='UNKNOWN'
        parts=redact(a.get('request_sections',[]),secrets)
        turns.append(dict(id=a['id'],kind='workflow',created_at=a.get('started_at',0),
            user_text=text(run['run'].get('task')),name=text(a.get('name')),node=a['node'],run=a['run'],
            iteration_path=a.get('iteration_path',[]),state=state,run_state=run['state'],
            request_sections=parts,answer=answer['text'] if answer else None,
            answer_source=origin(answer['text']) if answer else None))
    for op in manager.rt.db.listing('session-operation',filters={'workspace':workspace},limit=None):
        if op.get('diagnostic'):continue
        binding=op['binding'];receipt=op.get('receipt') or {}
        identity=dict(id=op['id'],session_id=op.get('native_session') or receipt.get('session_id') or binding.get('session_id'),requested_session=op.get('requested_session'))
        if not manager.matching_operation(workspace,row,identity):continue
        operations.append(op)
        current=manager.rt.session_center.operation(op['id'],workspace);state=current['state']
        process=receipt.get('process',{})
        valid=(state=='COMPLETED' and isinstance(op.get('reply'),str) and
            receipt.get('session_id')==row['native']['session_id'] and
            receipt.get('provider')==binding['provider'] and
            receipt.get('home')==binding['home'] and receipt.get('cwd')==op['cwd'] and
            receipt.get('requested_model')==binding['model'] and receipt.get('requested_effort')==binding['effort'] and
            process.get('exit')==0 and all(process.get(k) is False for k in ('cancelled','timeout','overflow')))
        answer=text(op['reply']) if valid else None
        turns.append(dict(id=op['id'],kind='chat',created_at=op['created_at'],
            user_text=text(op.get('user_prompt',op.get('prompt'))),name='对话',state='UNKNOWN' if state=='COMPLETED' and not valid else state,
            answer=answer,answer_source=origin(answer),reason=text(op.get('reason'))))
    turns.extend(native_turns(related,operations,messages,original_messages,bool(turns)))
    return sorted(turns,key=lambda turn:(turn['created_at'],turn['id']))


def native_turns(related,operations,messages,original_messages,has_managed):
    """Keep genuine freeform native turns beside Controller-backed results.

    Exact byte ranges hide workflow protocol. Process timestamps hide managed
    chat protocol; pre-metadata records fall back to their exact full request,
    never matching only a short human question. Native text is presentation,
    not a completion/Gate receipt and carries no invented lifecycle state.
    """
    from datetime import datetime
    import math
    def seconds(value):
        if type(value) in (int,float):return value if math.isfinite(value) and 0<=value<=32503680000 else None
        if isinstance(value,str):
            try:
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                return parsed.timestamp() if parsed.tzinfo is not None else None
            except (ValueError,OverflowError,OSError):pass
        return None
    ranges=[];windows=[];patterns=[]
    for a,_ in related:
        start,end=a.get('native_history_offset'),a.get('native_history_end')
        if type(start) is int and type(end) is int and 0<=start<=end:ranges.append((start,end))
        elif isinstance(a.get('actual_request'),str) and a['actual_request']:patterns.append(a['actual_request'])
    for op in operations:
        start,end=seconds(op.get('native_started_at')),seconds(op.get('native_finished_at'))
        if start is not None and end is not None and end>=start:windows.append((start,end))
        elif isinstance(op.get('prompt'),str) and op['prompt']:patterns.append(op['prompt'])
    raw={m.get('native_offset'):m.get('text','') for m in original_messages if type(m.get('native_offset')) is int}
    remaining=Counter(patterns)
    result=[];managed_request=False
    for index,m in enumerate(messages):
        offset=m.get('native_offset');at=seconds(m.get('native_at'));body=raw.get(offset,m['text'])
        if m['role']=='user':
            matched=next((p for p,n in remaining.items() if n and p in body),None)
            managed_request=matched is not None
            if matched:remaining[matched]-=1
        bounded=(type(offset) is int and any(start<=offset<end for start,end in ranges) or
            at is not None and any(start<=at<=end for start,end in windows))
        if bounded or managed_request:continue
        if m['role']=='assistant' and m['text'].strip()=='No response requested.':continue
        # Old mixed histories without reliable clocks/ranges remain accessible
        # in the advanced native log rather than fabricated chronological turns.
        if at is None and has_managed:continue
        pos=offset if type(offset) is int else index
        result.append(dict(id='native-'+format(pos,'016x'),kind='native',created_at=at or 0,
            native_offset=offset,messages=[{k:m[k] for k in ('role','text')}]))
    return result
