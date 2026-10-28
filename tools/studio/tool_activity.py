"""Bounded visible tool projections; never infer identity from name or order."""
from datetime import datetime
import json
import math
import re

_OMITTED=object()
_NO_TEXT='此工具没有可展示的文本结果；请核对原生证据。'

def _public_content(value, depth=0, protocol=False):
    if depth>4:return _OMITTED
    for _ in range(8):
        if not isinstance(value,str):break
        try:decoded=json.loads(value)
        except (ValueError,RecursionError):break
        if not isinstance(decoded,(str,dict,list)):break
        value=decoded
    else:
        # Never fall back to encoded protocol bytes when the decoding budget ends.
        return _OMITTED
    if isinstance(value,dict):
        kind=value.get('type')
        if protocol and 'type' in value and not isinstance(kind,str):return _OMITTED
        if isinstance(kind,str):
            if kind in {'input_text','output_text','text'}:
                return value['text'] if isinstance(value.get('text'),str) else _OMITTED
            if kind in {'tool_result','tool_use','function_call_output','custom_tool_call_output'}:
                return _public_content(value.get('content',value.get('output','')),depth+1,True)
            # Unknown typed blocks fail closed, including nested/new protocols.
            return _OMITTED
        result={}
        for key,item in value.items():
            public=_public_content(item,depth+1,protocol)
            if public is not _OMITTED:result[key]=public
        return result if result or not value else _OMITTED
    if isinstance(value,list):
        public=[_public_content(item,depth+1,protocol) for item in value]
        public=[item for item in public if item is not _OMITTED]
        if any(isinstance(item,dict) and isinstance(item.get('type'),str) for item in value):
            return '\n'.join(item for item in public if isinstance(item,str)) or _OMITTED
        return public if public or not value else _OMITTED
    return value

def visible_content(value, _depth=0, *, protocol=False):
    """Public arguments preserve ordinary data; native result protocols fail closed."""
    value=_public_content(value,_depth,protocol)
    if value is _OMITTED:return _NO_TEXT
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)

def timestamp(value):
    if type(value) in (int, float):
        return value if math.isfinite(value) and 0<=value<=253402300799 else None
    if not isinstance(value, str):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError,OverflowError):return None

def group_tools(events):
    """Input is already redacted, public, budgeted events from ONE native session."""
    rows=[];by_id={}
    calls={'tool_use','function_call','custom_tool_call'}
    for event in events:
        key=event.get('call_id')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',key):key=None
        row=by_id.get(key) if key else None
        if row is None:
            row=dict(name=event.get('name') or '工具结果',call_id=key,arguments=None,result=None,
                     started_at=None,finished_at=None,duration_seconds=None,
                     association='unknown',events=[],truncated=False)
            rows.append(row)
            if key:by_id[key]=row
        call=event['kind'] in calls;field='arguments' if call else 'result'
        text=event.get('content') or None
        if row[field] is not None and row[field]!=text:row['association']='conflict'
        else:row[field]=text
        if call:row['name']=event.get('name') or '工具调用'
        at=timestamp(event.get('at'))
        if call and row['started_at'] is None:row['started_at']=at
        if not call and row['finished_at'] is None:row['finished_at']=at
        row['events'].append(dict(kind=event['kind'],name=event.get('name'),content=text,at=at))
        row['truncated']|=event.get('truncated') is True
        if key and row['arguments'] is not None and row['result'] is not None and row['association']!='conflict':
            row['association']='matched'
            if row['started_at'] is not None and row['finished_at'] is not None and row['finished_at']>=row['started_at']:
                row['duration_seconds']=round(row['finished_at']-row['started_at'],6)
        # Keep the old readable content field for compatible history consumers.
        row['content']='\n'.join(v for v in (row['arguments'],row['result']) if isinstance(v,str))
    for row in rows:
        if row['association']=='conflict':row.update(arguments=None,result=None,duration_seconds=None,content='')
    if len(rows)>100:
        rows[-100]['history_truncated']=True
    return rows[-100:]


def journal_tools(events,secrets,state):
    """Project only persisted command events from one already-verified activation."""
    from orchestration.privacy import redact
    from .agent_sessions import safe_text
    latest={}
    for event in events[-40:] if isinstance(events,list) else []:
        if not isinstance(event,dict) or event.get('event') not in ('item.started','item.updated','item.completed'):continue
        key=event.get('id')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',key):continue
        prior=latest.get(key,{})
        latest[key]=dict(prior,**{k:v for k,v in event.items() if v not in (None,'') or k in ('event','exit_code')})
    projected=[]
    for key,event in latest.items():
        key=safe_text(redact(key,secrets))
        command=event.get('command');output=event.get('output')
        def clean(value,protocol=False):return safe_text(redact(visible_content(value,protocol=protocol),secrets))[:4000] if isinstance(value,str) else ''
        rows=[];child=event.get('tool_kind')=='managed_child';label='只读子任务' if child else '命令'
        if isinstance(command,str) and command:rows.append(dict(kind='function_call',name=label,call_id=key,content=clean(command),at=None,source='activation-journal'))
        if isinstance(output,str) and output or event['event']=='item.completed':rows.append(dict(kind='function_call_output',name=label+'结果',call_id=key,content=clean(output,True),at=None,source='activation-journal'))
        for row in group_tools(rows):
            row.update(source='activation-journal',activation_state=state,status=clean(event.get('status')),exit_code=event.get('exit_code') if type(event.get('exit_code')) is int else None)
            for ev in row['events']:ev['source']='activation-journal'
            projected.append(row)
    return projected


def merge_activity(history,journal):
    result=[dict(row,source=row.get('source','native-history'),events=[dict(e,source=e.get('source','native-history')) for e in row.get('events',[]) if isinstance(e,dict)]) for row in history if isinstance(row,dict)]
    seen={row.get('call_id'):row for row in result if row.get('call_id')}
    for row in journal:
        prior=seen.get(row.get('call_id'))
        if prior:
            prior['source']='native-history+activation-journal'
            prior['events']=[*prior.get('events',[]),*row.get('events',[])]
            conflict=any(prior.get(key) is not None and row.get(key) is not None and prior[key]!=row[key] for key in ('arguments','result'))
            for key in ('arguments','result'):
                if prior.get(key) is None:prior[key]=row.get(key)
            if conflict:prior.update(association='conflict',arguments=None,result=None,content='')
            else:prior['content']='\n'.join(v for v in (prior.get('arguments'),prior.get('result')) if isinstance(v,str))
        else:result.append(row)
    return result[-100:]
