import React,{useEffect,useState} from 'react';
import {filePath} from './AssistantSourcePicker.jsx';

const route=(query)=>'assistant/repair_status?'+new URLSearchParams(query);
const pendingRequests=new Map();
const terminal=new Set(['COMPLETED','FAILED','CANCELLED','RECOVERY_REQUIRED']);

export function AssistantRepair({api,workspace,diagnosis,agents,files,runs,perform,busy,onRun}){
 const available=agents.filter(item=>item.mode==='Agent'&&item.permission==='workspace-write'&&item.session_kind==='new');
 const failures=diagnosis.nodes.filter(item=>item.state==='FAILED'||item.diagnostic_outcome);
 const [activation,setActivation]=useState(failures[0]?.activation||''),[agent,setAgent]=useState('');
 const [task,setTask]=useState(''),[preview,setPreview]=useState(null),[approved,setApproved]=useState(false);
 const pendingKey='df-assistant-repair:'+workspace+':'+diagnosis.run;
 const [result,setResult]=useState(null),[verification,setVerification]=useState('');
 const [pending,setPending]=useState(()=>sessionStorage.getItem(pendingKey));
 const [unregistered,setUnregistered]=useState(false);
 const currentAgent=available.find(row=>row.id===agent);
 const input={workspace,run:diagnosis.run,activation,agent,revision:currentAgent?.revision||null,task,files:files.map(filePath)};
 const matching=preview&&JSON.stringify(preview.input)===JSON.stringify(input);
 function settle(value){
  setResult(value);setPending(null);setUnregistered(false);
  pendingRequests.delete(pendingKey);sessionStorage.removeItem(pendingKey);
 }
 async function checkIntent(){
  const value=await api('assistant/repair_intent?'+new URLSearchParams({workspace,run:diagnosis.run,intent:pending}));
  if(value.state==='UNREGISTERED')setUnregistered(true);
  else settle(value);
 }
 useEffect(()=>{
  let current=true;
  if(diagnosis.repair_run){
   api(route({workspace,run:diagnosis.repair_run})).then(value=>{if(current&&!pending)setResult(value)},()=>{});
  }
  return()=>{current=false};
 },[diagnosis.repair_run]);
 useEffect(()=>{
  if(!result?.run||terminal.has(result.state))return;
  let current=true;
  const timer=setInterval(async()=>{
   try{const value=await api(route({workspace,run:result.run}));if(current)setResult(value)}
   catch{ /* User can re-read the durable run after reconnecting. */ }
  },1500);
  return()=>{current=false;clearInterval(timer)};
 },[result?.run,result?.state,workspace]);
 async function review(){
  const selected=structuredClone(input);
  const value=await api('assistant/preview_repair',selected);
  setPreview({...value,input:selected,intent:pending||crypto.randomUUID().replaceAll('-','')});setApproved(false);
 }
 async function submit(request){
  pendingRequests.set(pendingKey,request);sessionStorage.setItem(pendingKey,request.intent);
  setPending(request.intent);setUnregistered(false);
  try{settle(await api('assistant/launch_repair',request))}
  catch(failure){
   if(!failure.uncertain&&Number.isInteger(failure.status)&&failure.status<500&&failure.status!==409){
    pendingRequests.delete(pendingKey);sessionStorage.removeItem(pendingKey);setPending(null);
   }
   throw failure;
  }
 }
 async function confirm(){
  await submit({...preview.input,intent:preview.intent,preview_sha256:preview.sha256,authorization:'confirm-workspace-write'});
 }
 async function readback(){
  const value=await api(route({workspace,run:result.run,...(verification?{verification}:{})}));
  setResult(value);
 }
 if(diagnosis.state!=='FAILED'||!failures.length)return null;
 return <section className="assistant-proposal" aria-label="授权修复">
  <h3>授权修复</h3>
  {result?<><p role="status">修复运行 {result.state} · <code>{result.run}</code></p>
   {result.reason&&<p>{result.reason}</p>}
   <p>原失败运行 <code>{result.source_run}</code></p>
   <ul>{result.files.map(item=><li key={item.before.path}>{item.before.path} · {
    item.changed===null?'当前指纹不可读':item.changed?'已变化':'指纹一致'}
    <details><summary>修复前后指纹</summary><p>{item.before.sha256}</p><p>{item.after?.sha256||'当前不可读'}</p></details></li>)}</ul>
   <button disabled={busy} onClick={()=>onRun(result.run)}>查看修复运行</button>
   {result.state==='COMPLETED'&&<>
    <p>使用原工作流版本启动独立验证，然后选择验证运行。</p>
    <label>独立验证运行<select value={verification} onChange={event=>setVerification(event.target.value)}>
     <option value="">不选择</option>{runs.filter(row=>row.id!==result.run&&row.id!==result.source_run&&
      row.draft_id===diagnosis.draft_id&&row.draft_revision===diagnosis.draft_revision).map(row=>
      <option key={row.id} value={row.id}>{row.name} · {row.state} · {row.id.slice(0,8)}</option>)}
    </select></label>
   </>}
   <button disabled={busy} onClick={()=>perform(readback)}>重新读取修复与验证</button>
   {result.verification&&<p role="status">验证运行 {result.verification.state} · {result.verification.run} {result.verification.reason||''}</p>}
  </>:<>
   {pending&&<div>
    <p className="field-note">{unregistered?'当前未查到该修复意图。可以核对或重新提交同一意图。':
     '本次修复提交结果待核验。'}</p>
    <button disabled={busy} onClick={()=>perform(checkIntent)}>核对原修复意图</button>
    {unregistered&&pendingRequests.has(pendingKey)&&<button disabled={busy}
     onClick={()=>perform(()=>submit(pendingRequests.get(pendingKey)))}>重新提交原修复意图</button>}
   </div>}
   {!available.length&&<p>在 Agent 管理保存一个允许工作区读写、使用新会话的 Agent 后即可预览。</p>}
   <fieldset disabled={busy||!!pending&&(!unregistered||pendingRequests.has(pendingKey))}>
    <label>失败激活<select aria-label="待修复的失败激活" value={activation} onChange={event=>setActivation(event.target.value)}>
     {failures.map(item=><option key={item.activation} value={item.activation}>{item.node} · {item.activation.slice(0,12)}</option>)}
    </select></label>
    <label>受管修复 Agent<select aria-label="受管修复 Agent" value={agent} onChange={event=>setAgent(event.target.value)}>
     <option value="">请选择已保存 Agent</option>{available.map(row=><option key={row.id} value={row.id}>{row.name} · {row.revision.slice(0,10)}</option>)}
    </select></label>
    <label>明确的修复任务<textarea aria-label="明确的修复任务" value={task} maxLength={8000} rows={3}
     onChange={event=>setTask(event.target.value)}/></label>
    <p className="field-note">本次已附加的 {files.length} 个文件将用于修复前后对照。</p>
    <button disabled={!activation||!currentAgent||!task.trim()} onClick={()=>perform(review)}>预览执行修复</button>
    {matching&&<div className="assistant-preview" aria-label="执行修复预览">
     <p>{preview.evidence.node} · {preview.evidence.reason||preview.evidence.state}</p>
     <p>{preview.agent.name} · {preview.agent.provider} · {preview.agent.permission} · <code>{preview.agent.revision.slice(0,12)}</code></p>
     <p>{preview.agent.cwd} · Git {preview.source.branch} · <code>{preview.source.head.slice(0,12)}</code></p>
     <details open><summary>实际发送的修复任务</summary><pre>{preview.execution_task}</pre></details>
     <ul>{preview.files.map(item=><li key={item.path}>{item.path} · {item.sha256.slice(0,12)}</li>)}</ul>
     <label className="assistant-checkbox"><input type="checkbox" checked={approved} onChange={event=>setApproved(event.target.checked)}/>
      授权此 Agent 按上述任务写入当前工作区</label>
     <button className="primary" disabled={!approved} onClick={()=>perform(confirm)}>确认执行修复</button>
    </div>}
   </fieldset>
  </>}
 </section>;
}
