const empty=[];

export function scriptRunEvents(events=empty){
 const byNode=new Map();
 const started=[];
 for(const event of events){
  if(event.kind==='PROCESS_STARTED')started.push(event);
  if(event.node){
   const key=(event.iteration??'')+'/'+event.node;
   if(!byNode.has(key))byNode.set(key,[]);
   byNode.get(key).push(event);
  }
 }
 return {events,started,forNode:(node,iteration)=>byNode.get((iteration??'')+'/'+node)||empty};
}
