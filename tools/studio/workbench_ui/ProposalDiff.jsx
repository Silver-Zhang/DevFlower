import React from 'react';
// The proposal remains data until the user chooses Apply. The server also checks the base graph.
export function ProposalDiff({before,after}){
 const a=new Map((before?.nodes||[]).map(n=>[n.id,n])),b=new Map((after?.nodes||[]).map(n=>[n.id,n]));
 const added=[...b.values()].filter(n=>!a.has(n.id)).map(n=>n.name||n.id);
 const removed=[...a.values()].filter(n=>!b.has(n.id)).map(n=>n.name||n.id);
 const changed=[...b.values()].filter(n=>a.has(n.id)&&JSON.stringify(a.get(n.id))!==JSON.stringify(n)).map(n=>n.name||n.id);
 const edgeChange=JSON.stringify(before?.edges||[])!==JSON.stringify(after?.edges||[]);
 return <div className="proposal-diff" aria-label="画布变更预览"><p>新增：{added.length?added.join('、'):'无'}</p><p>移除：{removed.length?removed.join('、'):'无'}</p><p>修改：{changed.length?changed.join('、'):'无'}</p><p>连线：{edgeChange?'有变化':'无变化'}</p></div>;
}
