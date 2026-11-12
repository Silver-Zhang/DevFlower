// V2 ports face right and left. Leave room outside cards for the arrow and
// handoff label, and keep the route derived from the live canvas geometry.
const clearance = 18;
const lead = 25;

const laneGap = 16;

export function canvasGeometry(state){
 const nodes=[];
 for(const node of state.nodeLookup.values()){
  if(node.hidden||!node.measured.width||!node.measured.height)continue;
  const {positionAbsolute,handleBounds}=node.internals;
  nodes.push({id:node.id,x:positionAbsolute.x,y:positionAbsolute.y,
   width:node.measured.width,height:node.measured.height,handleBounds});
 }
 return nodes;
}

export function sameCanvasGeometry(a,b){
 return a.length===b.length&&a.every((node,index)=>{
  const other=b[index];
  return node.id===other.id&&node.x===other.x&&node.y===other.y&&
   node.width===other.width&&node.height===other.height&&node.handleBounds===other.handleBounds;
 });
}

export function allNodesOutside(state){
 if(!state.panZoom||!state.width||!state.height||!state.nodeLookup.size)return false;
 const [left,top,zoom]=state.transform;
 let measured=false;
 for(const node of state.nodeLookup.values()){
  if(node.hidden)continue;
  const {width,height}=node.measured;
  if(!width||!height)return false;
  measured=true;
  const {x,y}=node.internals.positionAbsolute;
  const screenX=left+x*zoom,screenY=top+y*zoom;
  if(screenX<state.width&&screenY<state.height&&screenX+width*zoom>0&&screenY+height*zoom>0)return false;
 }
 return measured;
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

function crossings(span,route){
 return [route.from,route.to].filter(({x,y})=>x>span.left&&x<span.right&&
  span.y>Math.min(y,route.y)&&span.y<Math.max(y,route.y)).length;
}

function detourEdge(source,target,a,b,bounds,lanes){
 const other=bounds.filter(rect=>rect.id!==source&&rect.id!==target);
 if(a.x<b.x&&!other.some(rect=>forwardCurveHits(a,b,rect)))return;
 const from={x:a.x+lead,y:a.y},to={x:b.x-lead,y:b.y};
 if(bounds.some(rect=>rect.id!==source&&intersects(a,from,rect)||
                       rect.id!==target&&intersects(to,b,rect)))return;
 const left=Math.min(from.x,to.x),right=Math.max(from.x,to.x);
 const overlapping=lanes.filter(lane=>lane.left<right&&lane.right>left);
 const middle=(a.y+b.y)/2;
 const candidates=[middle,a.y,b.y,...bounds.flatMap(rect=>[rect.top,rect.bottom]),
  ...overlapping.flatMap(lane=>[lane.y-laneGap,lane.y+laneGap])];
 const levels=[...new Set(candidates)].sort((x,y)=>
  (Math.abs(a.y-x)+Math.abs(b.y-x))-(Math.abs(a.y-y)+Math.abs(b.y-y))||
  Math.abs(middle-x)-Math.abs(middle-y));
 let firstOpen,best,bestCrossings=Infinity;
 for(const y of levels){
  const first={x:from.x,y},last={x:to.x,y};
  if(bounds.some(rect=>intersects(from,first,rect)||intersects(first,last,rect)||intersects(last,to,rect)))continue;
  const candidate={left,right,y,from,to};
  firstOpen??=candidate;
  if(overlapping.some(lane=>Math.abs(y-lane.y)<laneGap))continue;
  const count=overlapping.reduce((sum,lane)=>sum+crossings(lane,candidate)+crossings(candidate,lane),0);
  if(count<bestCrossings){best=candidate;bestCrossings=count}
  if(!count)break;
 }
 const route=best??firstOpen;
 if(!route)return;
 lanes.push(route);
 const {y}=route;
 const points=[a,from,{x:from.x,y},{x:to.x,y},to,b].filter((point,index,all)=>
  !index||point.x!==all[index-1].x||point.y!==all[index-1].y);
 return [points.map((point,index)=>(index?'L':'M')+point.x+','+point.y).join(' '),
  (from.x+to.x)/2,y];
}

function portPoint(node,kind,id){
 const port=node?.handleBounds?.[kind]?.find(handle=>handle.id===id);
 if(!port)return;
 // V2 CanvasNodeCard places all source handles on the right and targets left.
 return {x:node.x+port.x+(kind==='source'?port.width:0),y:node.y+port.y+port.height/2};
}

export function routeEdges(edges,nodes){
 const byId=new Map(nodes.map(node=>[node.id,node]));
 const bounds=nodes.map(({id,x,y,width,height})=>({id,left:x-clearance,right:x+width+clearance,
  top:y-clearance,bottom:y+height+clearance}));
 const lanes=[],routes=new Map();
 for(const edge of edges){
  const {source,target}=edge;
  const from=portPoint(byId.get(source),'source',edge.source_port??'out');
  const to=portPoint(byId.get(target),'target',edge.target_port??'in');
  if(!from||!to)continue;
  const route=detourEdge(source,target,from,to,bounds,lanes);
  if(route)routes.set(edge.id,route);
 }
 return routes;
}
