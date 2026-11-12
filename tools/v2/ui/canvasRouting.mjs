// V2 ports face right and left. Leave room outside cards for the arrow and
// handoff label, and keep the route derived from the live canvas geometry.
const clearance = 18;
const lead = 25;

export function routingBounds(nodes, measurements) {
 return nodes.flatMap(({id, position})=>{
  const size=measurements.get(id);
  if(!size)return [];
  return [{id,left:position.x-clearance,right:position.x+size.width+clearance,
   top:position.y-clearance,bottom:position.y+size.height+clearance}];
 });
}

function intersects(a,b,rect){
 return Math.max(a.x,b.x)>rect.left&&Math.min(a.x,b.x)<rect.right&&
  Math.max(a.y,b.y)>rect.top&&Math.min(a.y,b.y)<rect.bottom;
}

function forwardCurveHits(a,b,rect){
 if(!intersects(a,b,rect))return false;
 // For a forward right-to-left port pair, React Flow's two Bezier controls
 // share the midpoint x and have the same y as their respective endpoints.
 const yAt=x=>{
  const ratio=(x-a.x)/(b.x-a.x);
  let lower=0,upper=1;
  for(let i=0;i<16;i++){
   const t=(lower+upper)/2;
   if(t*(1.5+t*(t-1.5))<ratio)lower=t;
   else upper=t;
  }
  const t=(lower+upper)/2;
  return a.y+(b.y-a.y)*t*t*(3-2*t);
 };
 const first=yAt(Math.max(a.x,rect.left)),last=yAt(Math.min(b.x,rect.right));
 return Math.max(first,last)>rect.top&&Math.min(first,last)<rect.bottom;
}

export function detourEdge({source,target,sourceX,sourceY,targetX,targetY},bounds){
 const a={x:sourceX,y:sourceY},b={x:targetX,y:targetY};
 const other=bounds.filter(rect=>rect.id!==source&&rect.id!==target);
 if(a.x<b.x&&!other.some(rect=>forwardCurveHits(a,b,rect)))return;
 const from={x:a.x+lead,y:a.y},to={x:b.x-lead,y:b.y};
 if(bounds.some(rect=>rect.id!==source&&intersects(a,from,rect)||
                       rect.id!==target&&intersects(to,b,rect)))return;
 const middle=(a.y+b.y)/2;
 const candidates=[middle,a.y,b.y,...bounds.flatMap(rect=>[rect.top,rect.bottom])];
 const levels=[...new Set(candidates)].sort((x,y)=>
  (Math.abs(a.y-x)+Math.abs(b.y-x))-(Math.abs(a.y-y)+Math.abs(b.y-y))||
  Math.abs(middle-x)-Math.abs(middle-y));
 for(const y of levels){
  const first={x:from.x,y},last={x:to.x,y};
  if(bounds.some(rect=>intersects(from,first,rect)||intersects(first,last,rect)||intersects(last,to,rect)))continue;
  const points=[a,from,first,last,to,b].filter((point,index,all)=>
   !index||point.x!==all[index-1].x||point.y!==all[index-1].y);
  return [points.map((point,index)=>(index?'L':'M')+point.x+','+point.y).join(' '),
   (first.x+last.x)/2,y];
 }
}
