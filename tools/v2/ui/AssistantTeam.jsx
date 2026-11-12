import React,{useState} from 'react';
import {Controls,MarkerType,Position,ReactFlow,ReactFlowProvider} from '@xyflow/react';
import {ProposalDiff} from '../../studio/workbench_ui/ProposalDiff.jsx';
import {providerNames} from './AssistantModelSettings.jsx';

const permissions={'none':'无工具','workspace-read':'工作区只读','workspace-write':'工作区读写'};
const kinds={start:'开始',agent:'Agent',tool:'工具',condition:'条件',router:'路由',merge:'汇合',loop:'循环',output:'输出',validation:'验证'};

function GraphReview({graph}){
 const names=Object.fromEntries(graph.nodes.map(node=>[node.id,node.name]));
 return <>
  <ul>{graph.nodes.map(node=><li key={node.id}><strong>{node.name}</strong> · {kinds[node.kind]||'Agent'}
   {node.config?.max_iterations&&' · 最多 '+node.config.max_iterations+' 轮'}
   {node.config?.body&&<GraphReview graph={node.config.body}/>}
  </li>)}</ul>
  <ul>{graph.edges.map(edge=><li key={edge.id}>{names[edge.source]} → {names[edge.target]}
   {edge.source_port&&edge.source_port!=='out'?' · '+edge.source_port:''} · {edge.transfer?.mode||'默认交接'}</li>)}</ul>
 </>;
}

function diagrams(graph,path='工作流'){
 const sections=[{id:path,graph}];
 for(const node of graph.nodes){
  if(node.config?.body)sections.push(...diagrams(node.config.body,path+' / '+node.name));
 }
 return sections;
}

function WorkflowDiagram({graph}){
 const sections=diagrams(graph);
 const [selected,setSelected]=useState('');
 const current=sections.find(section=>section.id===selected)||sections[0];
 const nodes=current.graph.nodes.map(node=>({id:node.id,position:node.position,draggable:false,selectable:false,
  sourcePosition:Position.Right,targetPosition:Position.Left,className:'assistant-diagram-node',
  data:{label:<>{node.name}<small>{kinds[node.kind]||'Agent'}</small></>}}));
 const edges=current.graph.edges.map(edge=>({id:edge.id,source:edge.source,target:edge.target,
  label:edge.source_port==='out'?undefined:edge.source_port,markerEnd:{type:MarkerType.ArrowClosed}}));
 return <section aria-label="团队工作流图">
  {sections.length>1&&<label>预览范围<select aria-label="预览工作流范围" value={current.id}
   onChange={event=>setSelected(event.target.value)}>{sections.map(section=><option key={section.id}>{section.id}</option>)}</select></label>}
  <div className="assistant-team-diagram">
   <ReactFlowProvider key={current.id}><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{padding:.17,maxZoom:1}}
    nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} zoomOnScroll={false} minZoom={.1}
    preventScrolling={false} proOptions={{hideAttribution:true}}>
    <Controls showInteractive={false}/>
   </ReactFlow></ReactFlowProvider>
  </div>
 </section>;
}

function BindingReview({bindings}){
 return <details><summary>Agent 绑定变更</summary>
  {bindings.map(change=><article key={change.node}>
   <strong>{change.node}</strong>
   {[[change.before,'原绑定'],[change.after,'新绑定']].map(([value,label])=><div key={label}>
    <p>{label} {value?value.name+' · '+value.revision.slice(0,12):'无'}</p>
    {value&&<p>{providerNames[value.provider]} · {value.model||'原生默认'} · {value.effort||'原生默认'} · {permissions[value.permission]}</p>}
    {change.before&&value&&<details><summary>{label}的 Prompt</summary>
     <p>{value.initial_prompt}</p><p>{value.round_prompt}</p><p>{value.output_prompt}</p>
    </details>}
   </div>)}
  </article>)}
 </details>;
}

function AgentReview({item}){
 const config=item.row.config;
 return <details>
  <summary>新建 {config.name} · {config.role}</summary>
  <dl>
   <dt>配置版本</dt><dd><code>{item.row.revision.slice(0,12)}</code></dd>
   <dt>模型</dt><dd>{providerNames[config.provider]} · {config.model||'原生默认'} · {config.effort||'原生默认'}</dd>
   <dt>原生配置</dt><dd>{config.native_profile||'本机默认 HOME'}</dd>
   <dt>工具权限</dt><dd>{config.mode} · {permissions[config.permission]}</dd>
   <dt>工作目录</dt><dd>{config.cwd}</dd>
   <dt>会话</dt><dd>每次激活新建 · 单次 {config.timeout} 秒</dd>
   <dt>Skills 建议</dt><dd>{item.skill_suggestions.join('、')||'无建议'} · 已启用 {config.skills.length} 项</dd>
   <dt>职责</dt><dd>{config.responsibilities}</dd>
   <dt>背景</dt><dd>{config.background}</dd>
   <dt>目标</dt><dd>{config.goal}</dd>
   <dt>初始 Prompt</dt><dd>{config.initial_prompt}</dd>
   <dt>每轮 Prompt</dt><dd>{config.round_prompt}</dd>
   <dt>交接 Prompt</dt><dd>{config.output_prompt}</dd>
  </dl>
 </details>;
}

export function AssistantTeam({api,workspace,conversation,turn,draft,busy,perform,onSaved,onOpenDraft}){
 const [preview,setPreview]=useState(null),[chosen,setChosen]=useState(null);
 const [testPath,setTestPath]=useState(''),[expected,setExpected]=useState('');
 const [approved,setApproved]=useState(false),[result,setResult]=useState(null);
 const saved=result?.draft||turn.team.saved_draft;
 const suggested=turn.team.verification_suggestion;
 const selection={workspace,conversation,turn:turn.id,draft:turn.team.kind==='update'?draft:null,permissions:chosen,
  verification:testPath?{path:testPath,expected_json:expected}:null};
 const current=preview?.request&&JSON.stringify(preview.request)===JSON.stringify(selection);
 async function review(initial=false){
  const request=initial?{...selection,permissions:null,verification:null}:selection;
  const next=await api('assistant/team_preview',request);
  if(next.applied){setResult(next.applied);return}
  if(initial)setChosen(next.permissions);
  setPreview({...next,request:initial?null:request});
  setApproved(false);
 }
 async function confirm(){
  if(!approved||!current)return;
  const data={...selection,preview_sha256:preview.sha256,authorization:'confirm-team-configuration'};
  const value=await onSaved(data);
  setResult(value);
 }
 return <section className="assistant-proposal" aria-label="Agent 团队提案">
  <h3>{turn.team.name}</h3><p>{turn.team.summary}</p>
  {saved?<><p role="status">Agent 与工作流已保存。</p>
   <button disabled={busy} onClick={()=>onOpenDraft(saved)}>打开已保存工作流</button></>:
   !preview?<button className="primary" disabled={busy} onClick={()=>perform(()=>review(true))}>查看团队提案</button>:<>
    <p>{preview.before?'更新当前工作流草稿':'新建工作流'} · {preview.agents.length} 个新 Agent · 最多 {preview.budget.calls} 次模型调用 / {preview.budget.seconds} 秒</p>
    <WorkflowDiagram graph={preview.after}/>
    {preview.before&&<ProposalDiff before={preview.before} after={preview.after}/>}
    {preview.bindings.length>0&&<BindingReview bindings={preview.bindings}/>}
    <details><summary>工作流节点、分支与交接</summary><GraphReview graph={preview.after}/></details>
    {preview.agents.map(item=><AgentReview key={item.slot} item={item}/>)}
    {turn.team.kind==='development'&&<p className="field-note">{preview.verification?
     '独立验证入口 '+preview.verification.path+' · '+preview.verification.branch+' · '+preview.verification.entry_sha256.slice(0,12):
     '此预览采用模型测试与审核。确定性交付 Gate 需要附加工作区 Python 测试，并填写期望 JSON。'}</p>}
    <fieldset disabled={busy}>
     <legend>选择团队权限</legend>
     {preview.agents.map(item=><label key={item.slot} className="assistant-run-choice">{item.row.config.name}
      <select aria-label={item.row.config.name+' 权限'} value={chosen[item.slot]} onChange={event=>{
       setChosen({...chosen,[item.slot]:event.target.value});setApproved(false);
       if(item.slot==='Developer'&&event.target.value!=='workspace-write')setTestPath('');
      }}>
       {preview.available_permissions.map(key=><option key={key} value={key}>{permissions[key]}</option>)}
      </select><small>建议 {permissions[item.suggested_permission]}</small>
     </label>)}
     {turn.team.kind==='development'&&(chosen.Developer==='workspace-write'?<div>
      {suggested&&<button onClick={()=>{
       setTestPath(suggested.path);setExpected(JSON.stringify(suggested.expected,null,2));setApproved(false);
      }}>采用建议的验证文件和期望值</button>}
      <label>独立 Python 验证入口<select aria-label="独立 Python 验证入口" value={testPath}
       onChange={event=>{setTestPath(event.target.value);setApproved(false)}}>
       <option value="">采用模型判断</option>{turn.team.files.filter(path=>path.endsWith('.py')).map(path=><option key={path}>{path}</option>)}
      </select></label>
      {testPath&&<label>期望 JSON<textarea aria-label="验证期望 JSON" value={expected} maxLength={4000}
       onChange={event=>{setExpected(event.target.value);setApproved(false)}}/></label>}
      <p className="field-note">验证器以 <code>python3 -I -B</code> 运行。请用明确的本地文件路径加载同目录代码。</p>
      {!turn.team.files.some(path=>path.endsWith('.py'))&&<p className="field-note">先附加工作区内的 Python 验证文件，再生成提案。</p>}
     </div>:<p className="field-note">为 Developer 选择工作区读写权限后，可以配置独立验证入口。</p>)}
     <button onClick={()=>perform(()=>review())} disabled={!!testPath&&!expected.trim()}>预览授权后的配置</button>
     {current&&<>
      <label className="assistant-checkbox"><input type="checkbox" checked={approved} onChange={event=>setApproved(event.target.checked)}/>
       确认以上 Agent、工作流版本与所选权限</label>
      <button className="primary" disabled={!approved} onClick={()=>perform(confirm)}>确认创建与保存</button>
     </>}
    </fieldset>
   </>}
 </section>;
}
