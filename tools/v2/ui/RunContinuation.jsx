import React,{useEffect,useState} from 'react';
import {ArrowRight,GitBranch,X} from 'lucide-react';

const deliveryLabels={result:'最终结果',structured:'结构化交付',artifact:'冻结文件'};
const fieldLabels={name:'名称',task_prompt:'节点任务',provider:'Provider',model:'模型',effort:'推理强度',
 role:'角色',round_prompt:'长期指令',output_prompt:'交付要求',agent_revision:'配置版本',
 native_output:'结构化输出',source:'来源节点',source_port:'来源端口',target:'目标节点',
 target_port:'目标端口',transfer:'交接设置',argv:'命令参数',cwd:'工作目录',timeout:'超时',
 expression:'条件',rules:'路由规则',exit_condition:'退出条件',max_iterations:'轮数上限',body:'循环子流程'};
const describe=value=>value==null?'—':typeof value==='string'?value:JSON.stringify(value,null,2);

function FieldDifference({field,before,after}){
 const fields=field==='config'?new Set([...Object.keys(before||{}),...Object.keys(after||{})]):null;
 if(fields)return [...fields].filter(key=>describe(before?.[key])!==describe(after?.[key])).map(key=>
  <FieldDifference key={key} field={key} before={before?.[key]} after={after?.[key]}/>);
 return <dl className="version-field"><dt>{fieldLabels[field]||field}</dt>
  <dd><small>前轮</small><pre>{describe(before)}</pre></dd>
  <dd><small>当前</small><pre>{describe(after)}</pre></dd></dl>;
}

function VersionDetails({data}){
 const previous=data.previous_version,current=data.current;
 const changes=[];
 for(const [frozen,prior,next] of [[previous.agents,data.previous_agents,current.agents],
  [previous.loop_agents,data.previous_loop_agents,current.loop_agents]]){
  for(const node of new Set([...Object.keys(frozen),...Object.keys(next)])){
   const old=frozen[node],stored=prior?.[node],after=next[node];
   const before=old?{...old,...stored,agent_revision:old.profile_revision}:null;
   const fields=['provider','model','effort','agent_revision','role','round_prompt','output_prompt','cwd']
    .filter(key=>(!old||!after||Object.hasOwn(before,key))&&describe(before?.[key])!==describe(
     key==='agent_revision'?after?.revision:after?.[key]));
   if(fields.length)changes.push({node,fields,before,after});
  }
 }
 return <details className="continuation-versions"><summary>查看两个版本的差异</summary>
  {data.changes?.map((change,index)=><section key={index}><h3>
   {change.kind==='nodes'?'节点':'连线'} · {change.name} · {({added:'新增',removed:'移除',updated:'更新'})[change.change]}</h3>
   {change.fields.map(field=><FieldDifference key={field.key} field={field.key} before={field.before} after={field.after}/>)}
  </section>)}
  {changes.map(({node,fields,before,after},index)=><section key={index}><h3>Agent 配置 · {node}</h3>
   {fields.map(field=><FieldDifference key={field} field={field} before={before?.[field]}
    after={field==='agent_revision'?after?.revision:after?.[field]}/>)}
  </section>)}
  <details><summary>版本核验摘要</summary><dl><dt>前轮 · {previous.name}</dt><dd><code>{data.previous_revision}</code></dd>
   <dd><code>{data.previous_hash}</code></dd>
   <dt>当前 · {current.name}</dt><dd><code>{current.revision}</code></dd><dd><code>{current.sha256}</code></dd></dl></details>
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
