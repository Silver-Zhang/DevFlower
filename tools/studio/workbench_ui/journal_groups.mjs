export function workflowGroups(runs){
 const groups=new Map();
 for(const r of runs){
  const key=r.draft_id?'draft:'+r.draft_id:'run:'+r.id;
  if(!groups.has(key))groups.set(key,{id:key,name:r.workflow_name||r.name||'工作流',runs:[]});
  groups.get(key).runs.push(r);
 }
 return [...groups.values()].map(g=>({...g,runs:g.runs.sort((a,b)=>(b.created_at||0)-(a.created_at||0))}));
}
export function nodeGroups(entries){
 const groups=new Map();
 for(const e of entries){
  const key=e.node||'workflow';
  if(!groups.has(key))groups.set(key,{id:key,name:e.node?e.name:'任务与流程',entries:[]});
  groups.get(key).entries.push(e);
 }
 return [...groups.values()].map(group=>{
  const rounds=new Map(),entries=[];
  for(const e of group.entries){
   if(!e.activation||!['input','assistant','visible','tool','summary'].includes(e.kind)){entries.push(e);continue;}
   if(!rounds.has(e.activation)){
    const round={...e,id:'activation:'+e.activation,kind:'activation',at:null,members:[]};
    rounds.set(e.activation,round);entries.push(round);
   }
   const round=rounds.get(e.activation);round.members.push(e);
   if(e.kind==='assistant'&&e.at!=null)round.at=e.at;
   else if(round.at==null&&e.at!=null)round.at=e.at;
  }
  return {...group,entries};
 });
}
