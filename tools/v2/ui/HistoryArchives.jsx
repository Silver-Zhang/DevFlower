import React,{useEffect,useRef,useState} from 'react';
import {X} from 'lucide-react';

const maxBytes=1508192;
const query=(route,values)=>route+'?'+new URLSearchParams(values);
const readable=value=>typeof value==='string'?value:JSON.stringify(value,null,2);

export function downloadFile(bytes,filename,type='application/json'){
 const url=URL.createObjectURL(new Blob([bytes],{type}));
 try{
  const link=document.createElement('a');
  link.href=url;
  link.download=filename;
  link.click();
 }finally{setTimeout(()=>URL.revokeObjectURL(url),0)}
}

export function HistoryArchives({api,workspace,onClose}){
 const [items,setItems]=useState([]),[selected,setSelected]=useState(null),[activation,setActivation]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const input=useRef(null),live=useRef(true);
 const scope={workspace:workspace.id};
 async function list(){
  const value=await api(query('history-archives',scope));
  if(live.current)setItems(value.archives);
 }
 useEffect(()=>{
  live.current=true;
  list().catch(failure=>{if(live.current)setError(failure.message)});
  return()=>{live.current=false};
 },[]);
 async function perform(action){
  setBusy(true);setError('');
  try{await action()}catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 async function open(id){
  const value=await api(query('history-archive',{...scope,id}));
  if(!live.current)return;
  setSelected(value);setActivation(value.detail.activations[0]?.id||'');
 }
 async function importFile(event){
  const file=event.target.files?.[0];
  event.target.value='';
  if(!file)return;
  await perform(async()=>{
   if(file.size<1||file.size>maxBytes)throw Error('运行历史归档超过文件大小上限。');
   const bytes=await file.arrayBuffer();
   if(!live.current)return;
   const result=await api(query('history-archives/import',scope),bytes,{raw:true});
   if(!live.current)return;
   await list();await open(result.id);
  });
 }
 const node=selected?.detail.activations.find(row=>row.id===activation);
 return <aside className="project-panel history-archives" aria-label="运行历史归档">
  <header className="project-panel-header"><h2>运行历史归档</h2><button aria-label="关闭运行历史归档" onClick={onClose}><X size={16}/></button></header>
  <div className="archive-controls">
   <button disabled={busy} onClick={()=>input.current.click()}>导入运行历史归档</button>
   <input hidden type="file" accept=".json,application/json" ref={input} aria-label="选择运行历史归档" onChange={importFile}/>
  </div>
  {error&&<p role="alert" className="error-text">{error}</p>}
  {!items.length&&<p className="field-note">可以从运行视图导出归档，再在任意已打开的工作区读取。</p>}
  {items.length>0&&<label className="field archive-picker"><span>已导入的归档</span>
   <select aria-label="已导入的归档" value={selected?.id||''} disabled={busy} onChange={event=>perform(()=>open(event.target.value))}>
    <option value="" disabled>选择归档</option>
    {items.map(row=><option key={row.id} value={row.id}>{row.source_run.slice(0,12)} · {row.source_state} · {new Date(row.imported_at*1000).toLocaleString()}</option>)}
   </select></label>}
  {selected&&<article className="archive-evidence" aria-label="只读归档内容">
   <p className="session-note">外部文件 · 只读。已校验文件摘要，来源由文件声明。</p>
   <dl><dt>源工作区</dt><dd>{selected.source_workspace}</dd><dt>源运行</dt><dd>{selected.source_run}</dd>
    <dt>归档 SHA-256</dt><dd>{selected.sha256}</dd></dl>
   {selected.detail.coverage.complete_public_snapshot!==true&&<p className="field-note">源导出包含有界省略。详见覆盖信息。</p>}
   {selected.detail.run.output!=null&&<details><summary>运行输出</summary><pre>{readable(selected.detail.run.output)}</pre></details>}
   {selected.detail.activations.length>0&&<label className="field"><span>归档激活</span>
    <select aria-label="归档激活" value={activation} onChange={event=>setActivation(event.target.value)}>
     {selected.detail.activations.map(row=><option key={row.id} value={row.id}>{row.name||row.node||row.id} · {row.iteration_path?.join('/')||'1'} · {row.state}</option>)}
    </select></label>}
   {node&&<details><summary>节点记录与回执</summary><pre>{readable(node)}</pre></details>}
   <details><summary>事件记录 · {selected.detail.events.length}</summary>
    <ol>{selected.detail.events.map(event=><li key={event.seq}><details><summary>#{event.seq} {event.kind} {event.node}</summary>
     <pre>{readable(event)}</pre></details></li>)}</ol>
   </details>
   <details><summary>覆盖信息</summary><pre>{readable(selected.detail.coverage)}</pre></details>
  </article>}
 </aside>;
}
