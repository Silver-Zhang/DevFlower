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

export function ScriptRunMonitor({api,snapshot,trusted,selected}) {
 const {run,script,state}=snapshot;
 const [choice,setChoice]=useState(selected||'');
 const [evidence,setEvidence]=useState(null);
 const [error,setError]=useState('');
 const events=script.events||[];
 const node=script.agents[choice]?choice:'';
 const agent=script.agents[node];
 const facts=events.filter(event=>event.node===node);
 const command=facts.find(event=>event.kind==='CALL_INTENT');
 const exit=facts.find(event=>event.kind==='PROCESS_EXITED');
 const path=new URLSearchParams({workspace:run.workspace,id:run.id,...(node?{node}:{})}).toString();
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
  <label className="field"><span>查看证据</span><select value={node} onChange={event=>setChoice(event.target.value)}>
   <option value="">最终结果</option>
   {script.document.nodes.filter(item=>script.agents[item.id]).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
  </select></label>
  {agent?<div>
   <p>{agent.provider==='codex'?'Codex CLI':'Claude Code'}{exit?' · 退出码 '+exit.returncode:''}</p>
   <EvidenceText label="实际请求（含上游交接）" value={shown?.prompt}/>
   <EvidenceText label={script.completed_nodes?.includes(node)?'最终回答':'已保存的回答文本'} value={shown?.answer}/>
   <details><summary>调用与错误记录</summary>
    {command&&<pre>{JSON.stringify({argv:command.argv,cwd:command.cwd},null,2)}</pre>}
    {facts.length>0&&<ol>{facts.map((event,index)=><li key={index}>
     {event.at} · {event.kind}{event.reason?' · '+event.reason:''}
    </li>)}</ol>}
    <EvidenceText label="原生 CLI stderr（隐藏敏感值）" value={shown?.stderr}/>
   </details>
   {!command&&<p>尚无 CLI 调用记录。</p>}
  </div>:<div>
   {state==='COMPLETED'?<EvidenceText label="工作流最终回答" value={shown?.result}/>:<p>尚无已核验的最终结果。</p>}
   {events.length>0&&<details><summary>运行日志</summary><ol>{events.map((event,index)=><li key={index}>
    {event.at} · {event.kind}{event.node?' · '+event.node:''}
    {typeof event.returncode==='number'?' · 退出码 '+event.returncode:''}
   </li>)}</ol></details>}
  </div>}
 </div>;
}
