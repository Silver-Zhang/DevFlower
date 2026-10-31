import React from 'react';
import {BaseEdge,getBezierPath,EdgeLabelRenderer} from '@xyflow/react';
export function HandoffEdge(props){
 const [path,x,y]=getBezierPath({...props,curvature:.35});
 const event=props.data?.pulse;
 return <><BaseEdge id={props.id} path={path} style={props.style} markerEnd={props.markerEnd} interactionWidth={26}/>{event&&<path key={event.run+':'+event.seq} d={path} fill="none" className="handoff-pulse" data-event={event.seq} data-run={event.run} aria-hidden="true"/>}{props.data?.onInsert&&<EdgeLabelRenderer><button className="edge-insert nodrag nopan" aria-label="在连线上添加节点" style={{transform:`translate(-50%, -190%) translate(${x}px,${y}px)`}} onClick={props.data.onInsert}>+</button></EdgeLabelRenderer>}{props.data?.issues?.length>0&&<EdgeLabelRenderer><span className="inspection-edge nodrag nopan" title={props.data.issues.map(i=>i.message).join('\n')} style={{transform:`translate(-50%, 30%) translate(${x}px,${y}px)`}}>问题 · {props.data.issues.length}</span></EdgeLabelRenderer>}{props.label&&<EdgeLabelRenderer><span className="edge-label nodrag nopan" style={{transform:`translate(-50%, -50%) translate(${x}px,${y}px)`}}>{props.label}</span></EdgeLabelRenderer>}</>;
}
