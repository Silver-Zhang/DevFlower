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
 return [...groups.values()];
}
