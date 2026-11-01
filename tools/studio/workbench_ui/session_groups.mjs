export const timeOf=value=>typeof value==='number'?(value<1e12?value*1000:value):Date.parse(value);

export function recentBucket(session,now=Date.now()){
 const at=timeOf(session?.updated);
 if(!Number.isFinite(at))return '活动时间未知';
 const days=(now-at)/86400000;
 return days<1?'今天':days<7?'最近 7 天':'更早';
}

export function groupKeyFor(session,groupBy,now=Date.now()){
 if(groupBy==='workflow')return session.workflow_group_id||session.workflow_id||'unlinked';
 if(groupBy==='recent')return recentBucket(session,now);
 return session.provider;
}
