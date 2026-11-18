import React,{useEffect,useRef,useState} from 'react';
import {downloadFile} from './HistoryArchives.jsx';
import {sha256} from './draftTransfer.js';

export async function downloadPython(artifact, current=()=>true) {
 const {filename,source}=artifact;
 if(typeof source!=='string'||typeof filename!=='string'||!filename.endsWith('.py')||/[\/\\\0]/.test(filename))
  throw Error('脚本文件身份无效。');
 const bytes=new TextEncoder().encode(source);
 if(await sha256(bytes)!==artifact.sha256)throw Error('脚本字节校验失败。');
 if(current())downloadFile(bytes,filename,'text/x-python;charset=utf-8');
}

export function PythonPreview({artifact,onClose}) {
 const dialog=useRef(null);
 const [error,setError]=useState('');
 useEffect(()=>{dialog.current.showModal()},[]);
 return <dialog ref={dialog} className="run-summary-file" aria-label="Python 脚本预览"
  onCancel={event=>{event.preventDefault();onClose()}} onKeyDown={event=>{if(event.key==='Escape')event.stopPropagation()}}>
  <header><h2>{artifact.filename}</h2><button onClick={onClose}>关闭预览</button></header>
  <p>SHA-256 <code>{artifact.sha256}</code></p>
  <pre>{artifact.source}</pre>
  {error&&<p role="alert">{error}</p>}
  <footer><button onClick={()=>downloadPython(artifact).catch(failure=>setError(failure.message))}>下载 Python</button></footer>
 </dialog>;
}

function EvidenceText({label,value}) {
 if(!value)return null;
 return <section><h3>{label}</h3><pre aria-label={label}>{value.text}</pre>
  {value.truncated&&<p className="field-note">此处显示部分内容，完整文件在本机证据目录。</p>}
 </section>;
}

const loopStates={NOT_STARTED:'未开始',STARTING:'准备调用',RUNNING:'运行中',VERIFYING:'核验中',
 COMPLETED:'已完成',PASSED:'通过',FAILED:'失败',INVALID:'候选已变化',SKIPPED:'未执行',CANCELLED:'已取消',UNKNOWN:'待核验'};
const loopDecisions={LOOP_CONTINUED:'继续返工',LOOP_ACCEPTED:'接受',LOOP_LIMIT_REACHED:'达到迭代上限'};
function roundActors(round){
 const actors=Object.entries(round.agents);
 if(round.validation)actors.splice(1,0,[round.validation.node,round.validation]);
 return actors;
}

export function ScriptRunMonitor({api,snapshot,trusted,selected}) {
 const {run,script,state}=snapshot;
 const [choice,setChoice]=useState(selected||'');
 const [evidence,setEvidence]=useState(null);
 const [error,setError]=useState('');
 const events=script.events||[];
 const loop=script.loop;
 const [iteration,selectedNode]=loop?choice.split('/'):[null,choice];
 const round=loop?.iterations.find(item=>String(item.iteration)===iteration);
 const tool=round?.validation?.node===selectedNode&&Boolean(selectedNode);
 const node=(tool||Object.hasOwn(script.agents,selectedNode))?selectedNode:'';
 const agent=node?script.agents[node]:null;
 const completed=(loop?round?.completed_nodes:script.completed_nodes)||[];
 const facts=events.filter(event=>event.node===node&&(!loop||String(event.iteration)===iteration));
 const command=facts.find(event=>event.kind==='CALL_INTENT');
 const exit=facts.find(event=>event.kind==='PROCESS_EXITED');
 const reviews=events.filter(event=>event.kind==='NODE_COMPLETED'&&event.verdict&&completed.includes(event.node));
 const router=script.router;
 const decision=script.condition||router;
 const skippedPorts=Object.keys(decision?.branches||{}).filter(port=>port!==decision.selected_port);
 const skipped=skippedPorts.flatMap(port=>decision.branches[port]);
 const started=events.filter(event=>event.kind==='PROCESS_STARTED');
 const body=script.document.nodes.find(item=>item.id===loop?.node)?.config.body;
 const nameOf=id=>body?.nodes.find(item=>item.id===id)?.name||script.document.nodes.find(item=>item.id===id)?.name||id;
 const rules=script.document.nodes.find(item=>item.id===router?.node)?.config.rules||[];
 const labelPort=port=>router?(port==='fallback'?'默认分支（fallback）':(rules.find(rule=>rule.id===port)?.label||port)+'（'+port+'）'):
  port==='true'?'满足（true）':'不满足（false）';
 const pathLabel=port=>decision.branches[port].map(nameOf).join(' → ');
 const path=new URLSearchParams({workspace:run.workspace,id:run.id,...(node?{node}:{}),
  ...(node&&round?{iteration}:{} )}).toString();
 useEffect(()=>{
  if(!trusted)return;
  let current=true;
  api('script-run/evidence?'+path).then(value=>{
   if(current){setEvidence({path,value});setError('')}
  }).catch(failure=>{if(current)setError(failure.message)});
  return()=>{current=false};
 },[path,state,events.length,trusted]);
 const shown=evidence?.path===path?evidence.value:null;
 async function download(){
  setError('');
  try{await downloadPython(await api('script-run/source?'+new URLSearchParams({workspace:run.workspace,id:run.id})))}
  catch(failure){setError(failure.message)}
 }
 return <div className="inspector-content run-evidence" aria-label="原生脚本运行证据">
  <p><strong>{script.artifact.filename}</strong> <button disabled={!trusted} onClick={download}>下载本次运行脚本</button></p>
  <details><summary>脚本与配置版本</summary><pre>{JSON.stringify({
   sha256:script.artifact.sha256,draft_revision:run.draft_revision,agents:script.agents,evidence_directory:script.directory
  },null,2)}</pre></details>
  {error&&<p className="error-text" role="alert">{error}</p>}
  <label className="field"><span>查看证据</span><select value={node?choice:''} onChange={event=>setChoice(event.target.value)}>
   <option value="">最终结果</option>
   {loop?loop.iterations.flatMap(item=>roundActors(item).map(([id])=>{
    const key=item.iteration+'/'+id;
    return <option key={key} value={key}>第 {item.iteration} 轮 · {nameOf(id)}</option>;
   })):script.document.nodes.filter(item=>Object.hasOwn(script.agents,item.id)).map(item=><option key={item.id} value={item.id}>
    {item.name}{skipped.includes(item.id)?'（分支未选中）':''}
   </option>)}
  </select></label>
  {node?<div>
   <p>{tool?'验证命令':agent.provider==='codex'?'Codex CLI':'Claude Code'}{exit?' · 退出码 '+exit.returncode:''}</p>
   {tool&&command&&<pre aria-label="实际命令与工作目录">{JSON.stringify({argv:command.argv,cwd:command.cwd},null,2)}</pre>}
   <EvidenceText label="实际请求（含上游交接）" value={shown?.prompt}/>
   <EvidenceText label={completed.includes(node)?'最终回答':'已保存的回答文本'} value={shown?.answer}/>
   {completed.includes(node)&&(shown?.structured||shown?.candidate)&&<details><summary>结构化交付与候选文件</summary>
    <EvidenceText label="结构化结果" value={shown?.structured}/>
    <EvidenceText label="候选清单" value={shown?.candidate}/>
   </details>}
   <details open={tool?true:undefined}><summary>调用与错误记录</summary>
    {!tool&&command&&<pre>{JSON.stringify({argv:command.argv,cwd:command.cwd},null,2)}</pre>}
    {facts.length>0&&<ol>{facts.map((event,index)=><li key={index}>
     {event.at} · {event.kind}{event.reason?' · '+event.reason:''}
    </li>)}</ol>}
    <EvidenceText label={tool?'验证 stdout':'原生 CLI stdout（隐藏敏感值）'} value={shown?.stdout}/>
    <EvidenceText label={tool?'验证 stderr':'原生 CLI stderr（隐藏敏感值）'} value={shown?.stderr}/>
   </details>
   {!command&&<p>{skipped.includes(node)?'此 Agent 所在分支未选中。':'尚无 CLI 调用记录。'}</p>}
  </div>:<div>
   {loop&&<section aria-label="循环轮次">
    <h3>{nameOf(loop.node)} · 最多 {loop.max_iterations} 轮</h3>
    {loop.iterations.map(item=><section key={item.iteration} aria-label={'第 '+item.iteration+' 轮'}>
     <h4>第 {item.iteration} 轮{item.historical?' · 历史候选':''}
      {item.disposition?' · '+loopDecisions[item.disposition]:''}</h4>
     <ul>{roundActors(item).map(([id,actor])=><li key={id}>
      <button type="button" onClick={()=>setChoice(item.iteration+'/'+id)}>{nameOf(id)}</button>
      {' · '+loopStates[actor.state]}
      {actor===item.validation&&Number.isInteger(actor.returncode)&&' · 退出码 '+actor.returncode}
     </li>)}</ul>
     {item.candidate_sha256&&<p>候选摘要 <code>{item.candidate_sha256}</code></p>}
     {item.review&&<div>
      <p>审核 <code>{item.review.verdict}</code> · {item.review.summary}</p>
      {item.review.issues.length>0&&<ul>{item.review.issues.map((issue,index)=><li key={index}>
       <code>{issue.file}</code> · {issue.severity} · {issue.description} · {issue.suggestion}
      </li>)}</ul>}
     </div>}
    </section>)}
    {script.candidate_current===false&&<p>最终候选已变化，原审核保留为历史记录。</p>}
   </section>}
   {decision&&<section aria-label={router?'路由分支':'条件分支'}>
    <h3>{nameOf(decision.node)}</h3>
    {router?<><p>上游 {nameOf(router.source_node)} · <button type="button"
     onClick={()=>setChoice(router.source_node)}>查看上游完整交付</button></p>
     <EvidenceText label="上游参与路由的结构化输入" value={{text:router.input_json}}/>
     <p>{router.matched_rule?'命中规则 '+labelPort(router.matched_rule):'未命中规则，使用 fallback'}</p>
     <p>选中端口 <code>{router.selected_port}</code></p>
    </>:<>
     <p>判断结果 <code>{decision.input_verdict}</code> · 选中 {labelPort(decision.selected_port)}</p>
     <p>候选摘要 <code>{decision.candidate_sha256}</code></p>
     {script.candidate_current===false&&<p>当前候选已变化。分支记录对应判断时的候选版本。</p>}
    </>}
    <p>选中路径 {pathLabel(decision.selected_port)}</p>
    {skippedPorts.map(port=><p key={port}>未选中 {labelPort(port)} · {pathLabel(port)}</p>)}
    <p>实际启动 {started.map(event=>nameOf(event.node)).join(' → ')}</p>
   </section>}
   {reviews.map(review=><p key={review.node}>
    {nameOf(review.node)} · {review.verdict==='accept'?'接受':'需要返工'}
    {state!=='COMPLETED'||script.candidate_current===false?'（历史审核记录）':''} · <code>{review.candidate_sha256}</code>
   </p>)}
   {state==='COMPLETED'?<EvidenceText label="工作流最终回答" value={shown?.result}/>:<p>尚无已核验的最终结果。</p>}
   {events.length>0&&<details><summary>运行日志</summary><ol>{events.map((event,index)=><li key={index}>
    {event.at} · {event.kind}{event.node?' · '+event.node:''}
    {event.iteration?' · 第 '+event.iteration+' 轮':''}
    {typeof event.returncode==='number'?' · 退出码 '+event.returncode:''}
   </li>)}</ol></details>}
  </div>}
 </div>;
}
