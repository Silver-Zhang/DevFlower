import React,{useEffect,useState} from 'react';
import {ArrowRight,GitBranch,X} from 'lucide-react';

const deliveryLabels={result:'最终结果',structured:'结构化交付',artifact:'冻结文件'};
const configureLabel=(row)=>row?[row.provider,row.profile_revision?.slice(0,8)].filter(Boolean).join(' · '):'—';

function VersionDetails({data}){
 const previous=data.previous_version;
 const current=data.current;
 const changes=[];
 for(const [label,old,next] of [['',previous.agents,current.agents],['Loop / ',previous.loop_agents,current.loop_agents]]){
  for(const node of new Set([...Object.keys(old),...Object.keys(next)])){
   const before=old[node],after=next[node];
   if(before?.profile_id!==after?.id||before?.profile_revision!==after?.revision||before?.provider!==after?.provider)
    changes.push([label+node,configureLabel(before),configureLabel(after&&{...after,profile_revision:after.revision})]);
  }
 }
 return <details className="continuation-versions"><summary>查看两个版本的差异</summary>
  <dl><dt>前轮 · {previous.name}</dt><dd><code>{data.previous_revision}</code></dd>
   <dd><code>{data.previous_hash}</code></dd>
   <dt>当前 · {current.name}</dt><dd><code>{current.revision}</code></dd><dd><code>{current.sha256}</code></dd></dl>
  {previous.source_sha256!==current.revision&&<p>工作流定义的保存版本有变化。</p>}
  {data.changes?.length>0&&<ul>{data.changes.map((change,index)=><li key={index}>
   {change.kind==='nodes'?'节点':'连线'} · {change.name} · {({added:'新增',removed:'移除',updated:'更新'})[change.change]}
  </li>)}</ul>}
  {changes.length>0&&<ul>{changes.map(([node,before,after])=><li key={node}>
   {node} · {before} → {after}
  </li>)}</ul>}
 </details>;
}

export function RunContinuation({api,run,busy,attempt,onStart,onClose}){
 const [data,setData]=useState(null),[error,setError]=useState('');
 const [version,setVersion]=useState(''),[selection,setSelection]=useState([]),[task,setTask]=useState('');
 useEffect(()=>{
  let active=true;
  api('script-run/continuation?'+new URLSearchParams({workspace:run.workspace,id:run.id})).then(result=>{
   if(!active)return;
   setData(result);
   if(!result.changed&&!result.current_error)setVersion('previous');
  }).catch(failure=>{if(active)setError(failure.message)});
  return()=>{active=false};
 },[api,run.id,run.workspace]);
 function toggle(key){
  setSelection(items=>items.includes(key)?items.filter(item=>item!==key):[...items,key]);
 }
 async function submit(event){
  event.preventDefault();
  setError('');
  const target=version==='current'?data.current:{revision:data.previous_revision,sha256:data.previous_hash};
  try{
   await onStart({draft_id:data.draft_id,revision:target.revision,task,execution:'native-cli',
    continue_from:{run_id:run.id,selection,version,sha256:target.sha256}});
  }catch(failure){setError(failure.message)}
 }
 return <aside className="project-panel run-continuation" aria-label="继续下一轮">
  <header className="project-panel-header"><GitBranch size={18} aria-hidden="true"/>
   <div><small>{run.name}</small><h2>继续下一轮</h2></div>
   <button aria-label="关闭续接" onClick={onClose}><X size={16}/></button>
  </header>
  {error&&<p className="continuation-error error-text" role="alert">{error}</p>}
  {!data&&!error&&<p className="continuation-error" role="status">正在核验前轮交付…</p>}
  {data&&<form onSubmit={submit}>
   <div className="continuation-body">
    <p className="field-note">系列 <code>{data.series.id.slice(0,8)}</code> · 第 {data.series.round+1} 轮</p>
    <fieldset disabled={busy||Boolean(attempt)}>
     <legend>本轮续接成果</legend>
     <p className="field-note">勾选要交给下一轮的内容。文件将从前轮的冻结副本读取。</p>
     {data.options.map(item=><label className="continuation-choice" key={item.id}>
      <input type="checkbox" checked={selection.includes(item.id)} onChange={()=>toggle(item.id)}/>
      <span><strong>{deliveryLabels[item.kind]}{item.node&&' · '+item.node}</strong>
       {item.kind!=='result'&&<small>{item.label}</small>}
       {item.kind==='artifact'&&<code title={item.sha256}>{item.bytes} B · SHA-256 {item.sha256}</code>}
      </span>
     </label>)}
    </fieldset>
    {data.changed&&data.current?<fieldset disabled={busy||Boolean(attempt)}>
     <legend>选择执行版本</legend>
     <p className="field-note">当前草稿编译后的脚本与前轮不同。</p>
     <label className="continuation-choice"><input type="radio" name="version" value="previous"
      checked={version==='previous'} onChange={()=>setVersion('previous')}/>使用前轮冻结版本</label>
     <label className="continuation-choice"><input type="radio" name="version" value="current"
      checked={version==='current'} onChange={()=>setVersion('current')}/>使用当前已保存版本</label>
     <VersionDetails data={data}/>
    </fieldset>:data.current_error?<fieldset disabled={busy||Boolean(attempt)}>
     <legend>确认冻结版本</legend>
     <p className="error-text">{data.current_error}</p>
     <label className="continuation-choice"><input type="checkbox" checked={version==='previous'}
      onChange={event=>setVersion(event.target.checked?'previous':'')}/>使用前轮冻结版本</label>
     <code>{data.previous_hash}</code>
    </fieldset>:<p className="field-note">沿用前轮的 Python 与 Agent 配置版本。</p>}
    <label className="field"><span>本轮新增任务</span><textarea required rows={4} maxLength={20000}
     disabled={busy||Boolean(attempt)} value={task} onChange={event=>setTask(event.target.value)}/></label>
   </div>
   <footer><button type="submit" className="primary" disabled={busy||(!attempt&&(!version||!task.trim()||!selection.length))}>
    <ArrowRight size={16}/>{busy?'核验启动中…':attempt?'核验本次启动':'启动新一轮'}</button></footer>
  </form>}
 </aside>;
}
