// Read-only presentation of persisted evidence. Never changes Runtime decisions.
export const STATUS = {
 DRAFT:['草稿','draft','○'],PENDING:['等待输入','pending','○'],QUEUED:['等待调度','pending','○'],
 STARTING:['准备启动','running','◷'],RUNNING:['正在运行','running','▶'],PAUSED:['调度已暂停','waiting','Ⅱ'],
 WAITING_CONFIRMATION:['等待人工确认','waiting','◇'],WAITING_INPUT:['等待输入','waiting','◷'],
 COMPLETED:['已完成','passed','✓'],PASSED:['已通过','passed','✓'],FAILED:['失败','failed','!'],
 CANCELLED:['已取消','cancelled','■'],SKIPPED:['已跳过','skipped','↷'],
 RECOVERY_REQUIRED:['状态待确认','unknown','?'],UNKNOWN:['状态待确认','unknown','?'],BUSY:['原环境占用中','waiting','◈']
};
export const statusInfo = state => STATUS[state] || [state==='运行未确认'?'状态待确认':state||'草稿','unknown','?'];
export const roundText = a => (a?.iteration_path?.length ? a.iteration_path : [a?.iteration || 1]).join(' / ');
export function duration(start,end){
 if(!Number.isFinite(start))return '耗时未知';
 const seconds=Math.max(0,Math.floor((Number.isFinite(end)?end:Date.now()/1000)-start));
 return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
}
export function stateDuration(start,end,state){
 if(Number.isFinite(end))return duration(start,end);
 return ['PENDING','QUEUED','STARTING','RUNNING','PAUSED','WAITING_CONFIRMATION','WAITING_INPUT','BUSY'].includes(state)?duration(start):'耗时未知';
}
export function latestActivation(nodes,path){
 return nodes.filter(a=>a.node===path).sort((a,b)=>{
  const x=a.iteration_path||[a.iteration||0],y=b.iteration_path||[b.iteration||0];
  for(let i=0;i<Math.max(x.length,y.length);i++)if((x[i]||0)!==(y[i]||0))return (y[i]||0)-(x[i]||0);
  return (b.started_at||0)-(a.started_at||0);
 })[0];
}
export function projectRun(snapshot,connection,runId){
 const trusted=Boolean(snapshot?.run?.id===runId&&connection==='connected'&&snapshot.state!=='RECOVERY_REQUIRED');
 const graph=snapshot?.run?.id===runId?snapshot.run.graph:null;
 const walk=(g,p='')=>(g?.nodes||[]).flatMap(n=>[{...n,path:p+n.id},...(n.body?walk(n.body,p+n.id+'/'):[])]);
 const nodes=walk(graph),activations=(snapshot?.nodes||[]).filter(a=>!a.run||a.run===runId);
 const latest=nodes.map(n=>({node:n,activation:latestActivation(activations,n.path)}));
 const active=trusted?latest.filter(x=>['RUNNING','STARTING'].includes(x.activation?.state)):[];
 const waiting=trusted?latest.filter(x=>x.activation?.state==='WAITING_CONFIRMATION'||
  !['COMPLETED','FAILED','SKIPPED','CANCELLED','RECOVERY_REQUIRED'].includes(x.activation?.state)&&x.activation?.waiting?.length):[];
 const done=trusted?latest.filter(x=>['COMPLETED','SKIPPED','CANCELLED'].includes(x.activation?.state)).length:0;
 return {trusted,graph,latest,active,waiting,done,total:nodes.length,
  state:trusted?(snapshot.run.paused?'PAUSED':snapshot.state):'UNKNOWN',
  reason:trusted?(snapshot.run.reason||latest.find(x=>['FAILED','RECOVERY_REQUIRED'].includes(x.activation?.state))?.activation.reason||''):'连接或运行身份尚未核验。正在读取同一运行，不会重复启动 Agent。'};
}
export function edgeEvidence(events,edge,prefix,runId){
 return events.filter(e=>e.run===runId&&e.edge===edge.id&&e.source===prefix+edge.source&&e.node===prefix+edge.target&&['handoff-sent','handoff-received'].includes(e.kind)).at(-1);
}
export function preflight(graph,workspace,task){
 if(!workspace)return {message:'先打开一个工作空间，再开始运行。'};
 if(!graph?.nodes?.length)return {message:'画布还是空白。添加节点或选择一个示例。'};
 const walk=(g,p='')=>{for(const n of g.nodes){const path=p+n.id;
  if(n.kind==='agent')for(const [key,label] of [['profile','执行工具'],['model','模型'],['effort','推理强度'],['round_prompt','每轮提示词']])
   if(!String(n[key]||'').trim())return {node:path,field:key,message:`「${n.name}」尚未配置${label}，已为你定位。`};
  if(n.body){const issue=walk(n.body,path+'/');if(issue)return issue;}
 }return null;};
 return walk(graph)||(!task.trim()?{message:'请在画布下方填写本次任务，说明要完成什么。'}:null);
}
