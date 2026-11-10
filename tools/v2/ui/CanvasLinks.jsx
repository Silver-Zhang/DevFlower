import React,{useState} from 'react';
import {ArrowRight} from 'lucide-react';
import {connectionIssue,defaultEdgeMode,defaultTransfer,nodeKind,ports,transferModes} from './canvasModel.mjs';

export function NodeConnections({node,graph,onConnect,Field}){
 const outputs=ports(node,'outputs');
 const [sourcePort,setSourcePort]=useState(outputs[0]?.[0]||'');
 const [targetId,setTargetId]=useState('');
 const [targetPort,setTargetPort]=useState('');
 if(!outputs.length)return null;
 const targets=graph.nodes.filter(item=>item.id!==node.id&&ports(item,'inputs').length);
 const target=targets.find(item=>item.id===targetId),inputs=target?ports(target,'inputs'):[];
 const selectedSource=outputs.some(([id])=>id===sourcePort)?sourcePort:outputs[0][0];
 const selectedInput=inputs.some(([id])=>id===targetPort)?targetPort:inputs[0]?.[0];
 const candidate={source:node.id,source_port:selectedSource,target:targetId,target_port:selectedInput};
 const issue=target?connectionIssue(graph,candidate):'';
 const agent=nodeKind(node)==='agent';
 return <section className="connect-section">
  <h3>{agent?'连接下游 Agent':'连接下游节点'}</h3>
  <p>从输出端口拖向输入端口，或在这里选择。</p>
  {outputs.length>1&&<Field label="源输出端口"><select value={selectedSource} onChange={e=>setSourcePort(e.target.value)}>{outputs.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field>}
  <select aria-label={agent?'下游 Agent':'下游节点'} value={targetId} onChange={e=>{setTargetId(e.target.value);setTargetPort('')}}>
   <option value="">选择下游节点</option>{targets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
  </select>
  {inputs.length>1&&<Field label="目标输入端口"><select value={selectedInput} onChange={e=>setTargetPort(e.target.value)}>{inputs.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></Field>}
  {issue&&<p className="error-text">{issue}</p>}
  <button type="button" disabled={!target||Boolean(issue)} onClick={()=>onConnect(candidate)}><ArrowRight size={16}/>{agent?'连接 Agent':'连接节点'}</button>
 </section>;
}

export function EdgeDraftConfig({edge,source,target,onUpdate,Field}){
 const mode=defaultEdgeMode(source,target);
 const transfer=edge.transfer||{...defaultTransfer,mode};
 const selectedSource=edge.source_port??'out',selectedTarget=edge.target_port??'in';
 return <>
  <Field label="源输出端口"><select value={selectedSource} onChange={e=>onUpdate({source_port:e.target.value})}>
   {ports(source,'outputs').map(([id,label])=><option key={id} value={id}>{label}</option>)}
  </select></Field>
  <Field label="目标输入端口"><select value={selectedTarget} onChange={e=>onUpdate({target_port:e.target.value})}>
   {ports(target,'inputs').map(([id,label])=><option key={id} value={id}>{label}</option>)}
  </select></Field>
  <Field label="交接内容"><select value={transfer.mode} onChange={e=>onUpdate({transfer:{...transfer,mode:e.target.value}})}>
   {Object.entries(transferModes).map(([id,label])=><option key={id} value={id}>{label}</option>)}
  </select></Field>
  <Field label="交接字符上限"><input type="number" min="1" max="20000" value={transfer.max_chars}
   onChange={e=>{if(e.target.value!=='')onUpdate({transfer:{...transfer,max_chars:Math.min(20000,Math.max(1,Math.trunc(Number(e.target.value))))}})}}/></Field>
  <p className="field-note">{transfer.mode===mode&&transfer.max_chars===16000?'使用当前节点类型的默认交接。':'按所选公开内容和字符上限交接。'} 草稿没有实际交接数据。</p>
 </>;
}
