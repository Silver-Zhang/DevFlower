import React,{useLayoutEffect} from 'react';
import {Handle,Position,useUpdateNodeInternals} from '@xyflow/react';
import {CheckCheck,FileCheck2,Flag,GitBranch,GitMerge,Repeat2,Settings2,Terminal,UserCheck,Workflow} from 'lucide-react';
import {PredicateEditor} from '../../studio/workbench_ui/RouterEditor.jsx';
import {canvasTypes,developmentExit,developmentRoles,draftConfigIssue,emptyPredicate,nodeKind,nodeType,ports,runtimeBlock} from './canvasModel.mjs';

const predicateLimits={field:200,value:2000};
const icons={start:Flag,agent:Workflow,condition:GitBranch,router:GitBranch,merge:GitMerge,loop:Repeat2,
 approval:UserCheck,tool:Terminal,validation:FileCheck2,output:CheckCheck};
export const nodeKinds=Object.fromEntries(Object.entries(canvasTypes).map(([id,spec])=>[id,[spec.label,icons[id],spec.english]]));

function PortRows({node,direction}){
 const input=direction==='inputs';
 const entries=ports(node,direction);
 return <div className={'node-ports '+direction}>
  {entries.map(([port,label])=><div className="node-port" key={port} title={label}>
   <Handle id={port} type={input?'target':'source'} position={input?Position.Left:Position.Right}
    aria-label={node.name+' '+(nodeKind(node)==='agent'?(input?'输入连接点':'输出连接点'):label+' '+(input?'输入端口':'输出端口'))}/>
   <span>{label}</span>
  </div>)}
 </div>;
}

export function CanvasNodeCard({id,data,selected}){
 const n=data.node,kind=nodeKind(n),type=nodeType(n),Icon=icons[kind],agent=kind==='agent';
 const issue=data.referenceIssue||draftConfigIssue(n);
 const portsKey=JSON.stringify([ports(n,'inputs'),ports(n,'outputs')]);
 const update=useUpdateNodeInternals();
 useLayoutEffect(()=>{update(id)},[id,portsKey,update]);
 return <article className={'agent-card canvas-node'+(selected?' selected':'')} data-kind={kind}>
  <header><span className="agent-icon"><Icon size={18}/></span><div><small>{type.english}</small>
   <strong title={n.name}>{n.name||'未命名节点'}</strong></div><Settings2 size={15}/></header>
  {agent&&<p>{n.agent_id?'已保存的 Agent 配置':n.model||(n.kind==='agent'?'选择已保存的 Agent 配置':'选择 Provider 与模型')}</p>}
  <div className={'canvas-node-ports'+(agent?' agent-ports':'')}>
   <PortRows node={n} direction="inputs"/><PortRows node={n} direction="outputs"/>
  </div>
  <footer>
   <span>{data.activation?data.stateLabel:issue?'待配置':agent&&n.agent_id?'已绑定':'已配置'}</span>
   <span title={issue||undefined}>{data.activation?data.activation.input_sent_sha256?'已写入 CLI':'冻结运行':issue||(agent?(n.agent_id?'固定版本':'独立会话'):'草稿')}</span>
  </footer>
 </article>;
}

function RouterRules({node,onChange,connected,Field}){
 const rules=node.config.rules||[];
 function change(id,patch){onChange({rules:rules.map(rule=>rule.id===id?{...rule,...patch}:rule)})}
 function add(){
  const id='r'+crypto.randomUUID().replaceAll('-','');
  onChange({rules:[...rules,{id,label:'分支 '+(rules.length+1),test:{...emptyPredicate,value:''}}]});
 }
 return <section className="canvas-router">
  <p className="field-note">按顺序编辑声明字段与比较值。默认分支有独立端口。修改分支名称会保留已有连线。</p>
  {rules.map((rule,index)=><fieldset key={rule.id}>
   <legend>规则 {index+1}</legend>
   <Field label={'分支名称 '+(index+1)}><input maxLength={100} value={rule.label} onChange={e=>change(rule.id,{label:e.target.value})}/></Field>
   <PredicateEditor nullable Field={Field} limits={predicateLimits} value={rule.test} labels={{field:'路由字段 '+(index+1),op:'路由运算符 '+(index+1)}} onChange={test=>change(rule.id,{test})}/>
   {connected.has(rule.id)&&<p className="field-note">此分支已有连线。请先删除连线，再移除规则。</p>}
   <button type="button" disabled={connected.has(rule.id)} onClick={()=>onChange({rules:rules.filter(item=>item.id!==rule.id)})}>移除规则 {index+1}</button>
  </fieldset>)}
  <button type="button" disabled={rules.length>=16} onClick={add}>添加分支规则</button>
 </section>;
}

function CompoundPredicate({value,onChange,Field,depth=0}){
 const modes=['all','any','not'];
 const mode=modes.find(item=>item in value)||'leaf';
 const leaf=()=>({...emptyPredicate});
 function changeMode(next){
  if(next==='leaf')onChange(leaf());
  else if(next==='not')onChange({not:leaf()});
  else onChange({[next]:[leaf(),leaf()]});
 }
 return <>
  {depth<4&&<Field label="条件组合"><select value={mode} onChange={event=>changeMode(event.target.value)}>
   <option value="leaf">单条比较</option><option value="all">全部满足</option><option value="any">任一满足</option>
   <option value="not">反转条件</option>
  </select></Field>}
  {mode==='leaf'?<PredicateEditor nullable Field={Field} limits={predicateLimits} value={value} onChange={onChange}/>:
   mode==='not'?<CompoundPredicate value={value.not} onChange={next=>onChange({not:next})} Field={Field} depth={depth+1}/>:
   <>
    {value[mode].map((item,index)=><fieldset className="canvas-predicate" key={index}><legend>判定 {index+1}</legend>
     <CompoundPredicate value={item} onChange={next=>onChange({[mode]:value[mode].map((entry,i)=>i===index?next:entry)})}
      Field={Field} depth={depth+1}/>
     <button type="button" disabled={value[mode].length<=1} onClick={()=>onChange({[mode]:value[mode].filter((_,i)=>i!==index)})}>移除判定 {index+1}</button>
    </fieldset>)}
    <button type="button" disabled={value[mode].length>=8} onClick={()=>onChange({[mode]:[...value[mode],leaf()]})}>添加判定</button>
   </>}
 </>;
}

export function TypedNodeConfig({node,onChange,connected,bodyChoices,siblingNodes,Field}){
 const type=nodeType(node);
 return <>
  <p className="session-note">{runtimeBlock(node)||'此节点由工作流调度器按已保存配置执行。'}</p>
  {Object.entries(type.fields).map(([key,field])=>{
   const tool=nodeKind(node)==='tool'?node.config.tool:null;
   const jsonTool=['verify-json','check-json'].includes(tool);
   if(developmentRoles.includes(key)&&tool!=='delivery-gate'||key==='expected_json'&&!jsonTool||
      key==='path'&&tool==='delivery-gate')return null;
   const value=node.config[key]??field.default??'';
   if(developmentRoles.includes(key)){
    const verification=key==='verification_node';
    const candidates=siblingNodes.filter(item=>verification?item.config?.tool==='verify-json':nodeKind(item)==='agent');
    const used=new Set(developmentRoles.filter(role=>role!==key).map(role=>node.config[role]));
    return <Field key={key} label={field.label.replace(' ID','')}><select value={value}
     onChange={event=>onChange({[key]:event.target.value})}>
     <option value="">选择同一流程的节点</option>
     {value&&!candidates.some(item=>item.id===value)&&<option value={value}>先前节点已不可用</option>}
     {candidates.map(item=><option key={item.id} value={item.id} disabled={used.has(item.id)}>
      {item.name||item.id}（{item.id}）</option>)}
    </select></Field>;
   }
   if(field.type==='graph')return <div key={key}>
    <Field label={field.label} note="先单独保存子工作流。这里固定一份图，后续编辑原草稿不会改变此循环。">
     <select value="" onChange={event=>{const draft=bodyChoices.find(row=>row.id===event.target.value);
      if(draft){
       const development=draft.graph.nodes.some(item=>item.config?.tool==='delivery-gate');
       onChange({[key]:structuredClone(draft.graph),...(development?{exit_condition:developmentExit}:{})});
      }}}>
      <option value="">{value?'替换已固定子工作流':'选择已保存工作流'}</option>
      {bodyChoices.map(row=><option key={row.id} value={row.id}>{row.graph.name}</option>)}
     </select>
    </Field>
    {value&&<p className="field-note">{value.name} · {value.nodes.length} 个节点 <button type="button"
      onClick={()=>onChange({[key]:undefined})}>移除循环子工作流</button></p>}
   </div>;
   if(field.type==='predicate')return <fieldset className="canvas-predicate" key={key}>
    <legend>{field.label}</legend>
    <CompoundPredicate Field={Field} value={value||emptyPredicate} onChange={test=>onChange({[key]:test})}/>
   </fieldset>;
   if(field.type==='choice')return <Field key={key} label={field.label}><select value={value} onChange={e=>{
    const chosen=e.target.value;
    onChange({[key]:chosen,...(key==='tool'&&['verify-json','check-json'].includes(chosen)?{permission:'workspace-read'}:
     key==='tool'&&chosen==='delivery-gate'?{permission:'none'}:{})});
   }}>
    {Object.entries(field.options).map(([option,label])=><option key={option} value={option}>{label}</option>)}
   </select></Field>;
   if(field.type==='integer')return <Field key={key} label={field.label}><input type="number" min={field.min} max={field.max} value={value}
    onChange={e=>{if(e.target.value!=='')onChange({[key]:Math.max(field.min,Math.min(field.max,Math.trunc(Number(e.target.value))))})}}/></Field>;
   return <Field key={key} label={key==='path'&&jsonTool?'工作区 Python 测试入口':field.label}><textarea placeholder={nodeKind(node)==='validation'?'{"field":"result.ok","op":"equals","value":true}':undefined} maxLength={20000} rows={3} value={value} onChange={e=>onChange({[key]:e.target.value})}/></Field>;
  })}
  {['verify-json','check-json'].includes(node.config.tool)&&<p className="field-note">启动时固定测试入口。验证器在隔离候选上运行该脚本并比较实际 JSON 输出。</p>}
  {node.config.tool==='check-json'&&<p className="field-note">通过后可在运行检查器读取实际 JSON 产物。验证器不写入工作区。</p>}
  {node.config.tool==='delivery-gate'&&<p className="field-note">开发、测试、确定性测试与独立审核必须来自同一轮。只有 ACCEPT 会结束研发循环。</p>}
  {nodeKind(node)==='approval'&&<p className="field-note">等待期间停止启动后续节点。只有本机用户可批准。拒绝或超时会结束运行。</p>}
  {nodeKind(node)==='merge'&&<p className="field-note">ALL 等待全部输入。ANY 按已保存的连线顺序选择成功输入，较早的输入仍在执行时会等待。</p>}
  {nodeKind(node)==='router'&&<RouterRules node={node} onChange={onChange} connected={connected} Field={Field}/>}
  {draftConfigIssue(node)&&<p className="error-text">{draftConfigIssue(node)}</p>}
 </>;
}
