import React,{useEffect,useRef,useState} from 'react';
import {Star,Trash2,RotateCcw,ChevronRight,X,Save} from 'lucide-react';

export function callBound(graph){
 return (graph?.nodes||[]).reduce((count,node)=>count+(node.kind==='agent'?1:node.kind==='loop'?
  (node.config?.max_iterations||node.max_rounds||5)*callBound(node.config?.body||node.body):0),0);
}

function ClassicTemplate({item,busy,onUse,onFavorite}){
 const {graph,use,relation,setup,inputs,outputs,scenarios,reference}=item;
 return <article className="classic-template">
  <div className="classic-heading"><h5>{graph.name}</h5>
   <span className="workbench-state" data-state={reference?'NEEDS_CONFIG':'READY'}>
    {reference?'设计参考 · 暂不支持编译':'绑定 Agent 后可编译'}
   </span>
  </div>
  <p>{use}</p>
  <details><summary>查看流程与配置</summary>
   <dl><dt>节点关系</dt><dd>{relation}</dd><dt>配置</dt><dd>{setup}</dd>
    <dt>输入</dt><dd>{inputs}</dd><dt>输出</dt><dd>{outputs}</dd>
    <dt>测试场景</dt><dd>{scenarios}</dd></dl>
  </details>
  <div className="classic-template-actions">
   <button aria-label={'复制模板 '+graph.name} disabled={busy} onClick={()=>onUse(graph)}>
    {reference?'复制为设计草稿':'新建草稿副本'}<ChevronRight size={14}/>
   </button>
   {!reference&&<button aria-label={'收藏内置模板 '+graph.name} title="收藏到我的模板"
    disabled={busy} onClick={()=>onFavorite(item)}><Star size={14}/>收藏</button>}
  </div>
 </article>;
}

function withoutTemplateCommands(graph){
 const commands=[];
 function clean(document){
  return {...document,nodes:document.nodes.map(node=>{
   if(node.config?.tool==='command'&&Object.hasOwn(node.config,'argv')){
    const {argv,...config}=node.config;
    commands.push(node.name+' ('+node.id+')');
    return {...node,config};
   }
   if(node.config?.body)return {...node,config:{...node.config,body:clean(node.config.body)}};
   return node;
  })};
 }
 const document=clean(graph);
 return {document,commands};
}

export function WorkflowLibrary({api,workspace,examples,graph,onUse,onClose,startSaving=false,onUseTemplate,searchable=false,autoFocusClose=false}){
 const [library,setLibrary]=useState(null);
 const [query,setQuery]=useState(''),[view,setView]=useState('all'),[saving,setSaving]=useState(startSaving);
 const [name,setName]=useState(graph?.name||''),[description,setDescription]=useState('');
 const [cleanPreview,setCleanPreview]=useState(null);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 const live=useRef(true);

 async function load(){
  try{
   const result=await api('/api/workflow-library?workspace='+workspace.id);
   if(live.current)setLibrary(result);
  }catch(failure){if(live.current)setError(failure.message)}
 }
 useEffect(()=>{live.current=true;load();return()=>{live.current=false}},[]);
 async function perform(fn){
  if(busy)return;
  setBusy(true);setError('');
  try{await fn();await load()}
  catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 function storeTemplate(graph,name,description){
  return api('/api/workflow-library/save',{workspace:workspace.id,graph,name,description});
 }
 function manage(template,operation){
  return perform(()=>api('/api/workflow-library/manage',{workspace:workspace.id,id:template.id,expected:template.revision,operation}));
 }
 const matches=value=>!searchable||value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
 const rows=(library?.templates||[]).filter(t=>(view==='trash'?t.collection==='trash':
  t.collection==='active'&&(view!=='favorites'||t.favorite))&&matches(t.name+' '+t.description));
 const classics=(library?.classics||[]).filter(item=>matches([item.graph.name,item.use,item.scenarios].join(' ')));
 const builtins=(library?.examples||examples).filter(item=>matches(item.name)&&
  !library?.classics?.some(template=>template.graph.name===item.name));
 return <section className="example-library floating workflow-library" aria-label="工作流模板">
  <h3>示例与我的模板<button autoFocus={autoFocusClose} aria-label="关闭示例库" onClick={onClose}><X size={14}/></button></h3>
  {searchable&&<input className="template-search" aria-label="搜索模板" placeholder="按名称或说明搜索"
   value={query} onChange={event=>setQuery(event.target.value)}/>}
  <nav>{[['all','全部'],['favorites','收藏'],['trash','回收站']].map(([key,label])=><button key={key}
   aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}</nav>
  <button className="library-save" onClick={()=>setSaving(!saving)}><Star size={14}/>收藏当前工作流为模板</button>
  {saving&&<form className="library-form" onSubmit={event=>{
   event.preventDefault();perform(async()=>{await storeTemplate(graph,name,description);setSaving(false);setView('favorites')});
  }}>
   <label>模板名称<input aria-label="模板名称" value={name} onChange={event=>setName(event.target.value)} maxLength={100}/></label>
   <label>说明<textarea aria-label="模板说明" value={description} onChange={event=>setDescription(event.target.value)} maxLength={1000}/></label>
   <small>{onUseTemplate?'保存画布与节点配置。新草稿需要重新绑定已保存的 Agent。':'保存流程和提示词；不复制原生会话、任务、附件权限或运行批准。'}</small>
   <button disabled={busy||!name.trim()||!graph?.nodes?.length}><Save size={13}/>保存模板</button>
   {onUseTemplate&&error.includes('.config.argv')&&graph?.nodes?.length>0&&
    <button type="button" disabled={busy} onClick={()=>setCleanPreview({graph,...withoutTemplateCommands(graph)})}>预览移除命令参数的模板</button>}
   {cleanPreview?.graph===graph&&cleanPreview.commands.length>0&&<section aria-label="模板清理预览">
    <p>下面节点的命令参数将从模板移除。使用模板后，可以为这些节点填写新参数。</p>
    <ul>{cleanPreview.commands.map((name,index)=><li key={index}>{name} · config.argv</li>)}</ul>
    <button type="button" disabled={busy||!name.trim()} onClick={()=>perform(async()=>{
     await storeTemplate(cleanPreview.document,name,description);setCleanPreview(null);setSaving(false);setView('favorites');
    })}>确认保存清理后的模板</button>
   </section>}
  </form>}
  {error&&<p role="alert">{error}</p>}
  <div className="library-scroll">
   {rows.length>0&&<h4>我的模板</h4>}
   {rows.map(template=><article className="template-card" key={template.id}>
    <button className="template-use" disabled={template.collection==='trash'||Boolean(onUseTemplate)&&busy}
     onClick={()=>onUseTemplate?perform(()=>onUseTemplate(template)):onUse(template.graph)}>
     <strong>{template.name}</strong><small>{template.description||'我的工作流模板'}
      {template.graph&&' · 最多 '+callBound(template.graph)+' 次 Agent 调用'}</small>
     <span>新建草稿副本<ChevronRight size={13}/></span>
    </button>
    <div className="template-actions">{template.collection==='trash'?
     <button aria-label={'恢复模板 '+template.name} onClick={()=>manage(template,'restore')}><RotateCcw size={14}/></button>:
     <>
      <button aria-label={'收藏模板 '+template.name} aria-pressed={template.favorite}
       onClick={()=>manage(template,template.favorite?'unfavorite':'favorite')}><Star size={14}/></button>
      <button aria-label={'删除模板 '+template.name} onClick={()=>manage(template,'trash')}><Trash2 size={14}/></button>
     </>}
    </div>
   </article>)}
   {view==='all'&&classics.some(item=>!item.reference)&&<>
    <h4>内置经典工作流</h4>
    {classics.filter(item=>!item.reference).map(item=><ClassicTemplate key={item.graph.name} item={item}
     busy={busy} onUse={onUse} onFavorite={()=>perform(async()=>{
      await storeTemplate(item.graph,item.graph.name,item.use);setView('favorites');
     })}/>)}
   </>}
   {view==='all'&&builtins.length>0&&<>
    <h4>起始示例</h4>
    {builtins.map(item=><button key={item.name} className="built-in-template" onClick={()=>onUse(item)}>
     <span><strong>{item.name}</strong><small>最多 {callBound(item)} 次 Agent 调用 · 运行前确认预算</small></span><ChevronRight size={15}/>
    </button>)}
   </>}
   {view==='all'&&classics.some(item=>item.reference)&&<>
    <h4>设计参考</h4>
    {classics.filter(item=>item.reference).map(item=><ClassicTemplate key={item.graph.name} item={item} busy={busy} onUse={onUse}/>)}
   </>}
   {view==='favorites'&&!rows.length&&!query&&<p>把常用画布保存为模板，下次直接使用。</p>}
   {query&&!rows.length&&(view!=='all'||!builtins.length&&!classics.length)&&<p>没有符合搜索的模板。</p>}
  </div>
 </section>;
}
