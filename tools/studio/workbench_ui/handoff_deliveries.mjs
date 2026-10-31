const stages={'handoff-ready':1,'handoff-sent':2,'handoff-received':2};
export function handoffDeliveries(events,run){
 const grouped=new Map();
 for(const event of events){
  if(event.run!==run||!stages[event.kind])continue;
  const key=JSON.stringify([event.run,event.edge,event.activation,event.target_activation??null,event.node,event.target_round||event.iteration_path||[],event.attempt||1,event.content_sha256]);
  const prior=grouped.get(key);
  if(!prior||stages[event.kind]>stages[prior.kind]||stages[event.kind]===stages[prior.kind]&&event.seq>prior.seq)grouped.set(key,event);
 }
 return [...grouped.values()];
}
