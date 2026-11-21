import {pasteFragment, selectedFragment} from '../../studio/workbench_ui/canvas_editing.mjs';

export const canvasFragment = (graph, ids) => selectedFragment(graph, ids, {includeStart: true});
export const coordinate = number => Math.min(100000, Math.max(-100000, number));

export function pasteCanvasFragment(graph, fragment, makeId) {
 if (graph.nodes.length + fragment.nodes.length > 64 || graph.edges.length + fragment.edges.length > 128)
  throw Error('粘贴后将超过 64 个节点或 128 条连线。');
 return pasteFragment(graph, fragment, makeId, 40, node => {
  // Frozen Agent references may point at one managed native session. Rebind the
  // copy explicitly instead of silently granting a second resume entry.
  delete node.agent_id;
  delete node.agent_revision;
  node.name = [...node.name].slice(0, 160).join('');
  node.position = {x: coordinate(node.position.x), y: coordinate(node.position.y)};
 });
}

export function applySelection(current, changes) {
 const next = new Set(current);
 for (const change of changes) {
  if (change.selected) next.add(change.id);
  else next.delete(change.id);
 }
 return [...next];
}

export function layoutCanvas(graph, measurements) {
 const remaining=new Map(graph.nodes.map(node=>[node.id,0]));
 const children=new Map(graph.nodes.map(node=>[node.id,[]]));
 const rank=new Map(graph.nodes.map(node=>[node.id,0]));
 for(const {source,target} of graph.edges){
  children.get(source).push(target);
  remaining.set(target,remaining.get(target)+1);
 }
 const queue=graph.nodes.filter(node=>!remaining.get(node.id)).map(node=>node.id);
 for(const id of queue){
  for(const target of children.get(id)){
   rank.set(target,Math.max(rank.get(target),rank.get(id)+1));
   remaining.set(target,remaining.get(target)-1);
   if(!remaining.get(target))queue.push(target);
  }
 }
 // Legacy drafts can contain an invalid cycle. Its nodes still need places
 // on the canvas without treating the layout as a runtime validation.
 const lastRank=Math.max(0,...rank.values())+1;
 const columns=Array.from({length:lastRank+1},()=>[]);
 for(const node of graph.nodes)columns[remaining.get(node.id)?lastRank:rank.get(node.id)].push(node);
 const size=node=>measurements.get(node.id)||{width:238,height:132};
 const groups=columns.filter(column=>column.length);
 const widths=groups.map(column=>Math.max(...column.map(node=>size(node).width)));
 const heights=groups.map(column=>column.reduce((sum,node)=>sum+size(node).height,0)+(column.length-1)*68);
 const height=Math.max(0,...heights),width=widths.reduce((sum,value)=>sum+value,0)+(groups.length-1)*94;
 const origin=axis=>Math.min(...graph.nodes.map(node=>node.position[axis]));
 let x=coordinate(Math.min(origin('x'),100000-width));
 const top=coordinate(Math.min(origin('y'),100000-height)),positions=new Map();
 groups.forEach((column,index)=>{
  let y=top+(height-heights[index])/2;
  for(const node of column){
   positions.set(node.id,{x:Math.round(x),y:Math.round(y)});
   y+=size(node).height+68;
  }
  x+=widths[index]+94;
 });
 return {...graph,nodes:graph.nodes.map(node=>({...node,position:positions.get(node.id)}))};
}
