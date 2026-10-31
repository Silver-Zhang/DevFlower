// Native Claude's accounting is session cumulative. Codex's is per turn.
// Tokens and Provider-reported USD are independent reports, never a bill.
export function usageSummary(nodes){
 const turns=nodes.filter(n=>n.kind==='agent'&&n.state==='COMPLETED');
 const accounts=new Map(),costs=new Map();let missing=0,costMissing=0;
 const number=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
 const retain=(map,key,n,value)=>{const prior=map.get(key);if(!prior||(n.finished_at||0)>=prior.at)map.set(key,{at:n.finished_at||0,...value})};
 for(const n of turns){
  const r=n.output?.receipt,u=r?.usage,parts=u?.usage;
  const key=u?.semantics==='turn'?JSON.stringify(['turn',n.run,n.id]):u?.semantics==='session-cumulative-do-not-sum'&&typeof r.usage_scope==='string'&&r.usage_scope?JSON.stringify(['session',r.usage_scope]):null;
  if(!key){missing++;costMissing++;continue}
  if(!parts||!number(parts.input_tokens)||!number(parts.output_tokens))missing++;
  else retain(accounts,key,n,{input:parts.input_tokens,output:parts.output_tokens});
  // A missing cost must not hide known tokens (or vice versa). For cumulative
  // sessions keep the latest actually reported amount, not the sum of rounds.
  if(!number(u.total_cost_usd))costMissing++;
  else retain(costs,key,n,{cost:u.total_cost_usd});
 }
 const values=[...accounts.values()],cost=[...costs.values()].reduce((s,u)=>s+u.cost,0);
 return {input:values.reduce((s,u)=>s+u.input,0),output:values.reduce((s,u)=>s+u.output,0),missing,reported:values.length,complete:turns.length>0&&missing===0,
  cost:costs.size&&Number.isFinite(cost)?cost:null,costReported:costs.size,costMissing,costComplete:turns.length>0&&costMissing===0&&Number.isFinite(cost)};
}
