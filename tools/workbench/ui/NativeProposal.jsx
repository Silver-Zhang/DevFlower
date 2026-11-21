import React from 'react';
import {WorkflowDiagram} from './AssistantTeam.jsx';
import {ProposalDiff} from '../../studio/workbench_ui/ProposalDiff.jsx';
import {nodeType} from './canvasModel.mjs';

const statuses={compilable:'可编译',unsupported:'暂不支持编译',configuration_error:'配置错误'};

function GraphDetails({graph,bindings}){
 const {nodes,edges}=graph;
 const byId=new Map(nodes.map(node=>[node.id,node]));
 if(!nodes.length)return <p>空白画布</p>;
 return <>
  <ul className="proposal-nodes">{nodes.map(node=>{
   const profile=bindings.get(node.agent_id);
   return <li key={node.id}>
    <strong>{node.name}</strong> <span className="field-note">· {nodeType(node).label}</span>
    {profile&&<p>{profile.config.name} · {profile.config.provider==='claude'?'Claude Code':'Codex CLI'} · <code>{profile.revision.slice(0,8)}</code></p>}
    {node.task_prompt&&<p>{node.task_prompt}</p>}
    {node.config?.argv&&<pre aria-label={node.name+' 命令参数'}>{JSON.stringify(node.config.argv)} · {node.config.cwd||'.'}</pre>}
    {node.config?.body&&<details><summary>查看循环子流程</summary><GraphDetails graph={node.config.body} bindings={bindings}/></details>}
   </li>
  })}</ul>
  <details><summary>查看 {edges.length} 条连线</summary><ul>{edges.map(edge=>
   <li key={edge.id}>{byId.get(edge.source)?.name} ({edge.source_port||'out'}) → {byId.get(edge.target)?.name}</li>
  )}</ul></details>
 </>;
}

export function NativeProposal({value}){
 const bindings=new Map(value.bindings.map(row=>[row.id,row]));
 return <>
  <p><strong>{statuses[value.check.status]}</strong> · {value.check.execution_available?'可导出独立 Python':'可保留为设计草稿'}</p>
  {value.check.issues.length>0&&<ul className="proposal-issues">{value.check.issues.map((issue,index)=><li key={index}>{issue.message}</li>)}</ul>}
  <ProposalDiff before={value.before} after={value.after}/>
  {!!value.agents.length&&<div><strong>将创建 {value.agents.length} 个 Agent 配置</strong>
   <ul>{value.agents.map(row=><li key={row.id}>{row.config.name} · {row.config.provider==='claude'?'Claude Code':'Codex CLI'}<p>{row.config.role}</p></li>)}</ul>
  </div>}
  <WorkflowDiagram graph={value.after} label="提案画布预览"/>
  <GraphDetails graph={value.after} bindings={bindings}/>
 </>;
}
