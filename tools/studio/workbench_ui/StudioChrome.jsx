import React,{useState} from 'react';
import {FolderOpen,MessageSquare,Workflow,Plus,ChevronRight,Blocks,MousePointer2,Trash2,Sparkles,X} from 'lucide-react';

// DevFlower workspace navigation around the existing runtime.
// Licensed Sim interaction hooks are separately attributed under vendor/sim.
export function LegacyDraftCopies({drafts,onCopy}) {
 if(!drafts.length)return null;
 return <details className="legacy-drafts"><summary>未分配的旧工作流（仅可新建副本）</summary><p>尚未证明工作区归属。原件与历史保持不变，不能在此删除或接管。</p>{drafts.map(d=><button key={d.id} aria-label={'在当前工作区新建副本 '+d.graph.name} onClick={()=>onCopy(d)}>{d.graph.name} · 在当前工作区新建副本</button>)}</details>;
}

export function WorkspaceNavigation({migration,workspace,drafts,legacy=[],current,onFolder,onChat,onWorkflows,onDocument,onDelete}) {
 const owned=drafts.filter(d=>d.workspace===workspace?.id&&d.workspace);
 return <>
  <nav className="workspace-navigation" aria-label="工作空间导航">
   <button onClick={onChat}><MessageSquare size={15}/>开始对话</button>
   <button onClick={onFolder}><FolderOpen size={15}/>打开文件夹</button>
   <button onClick={onWorkflows}><Workflow size={15}/>工作流<ChevronRight size={13}/></button>
  </nav>
  <section className="workspace-workflows" aria-label="工作流快捷列表">
   <div className="workspace-section-title"><span>工作流</span><button aria-label="查看所有工作流" onClick={onWorkflows}><Plus size={14}/></button></div>
   {!workspace?<p>打开文件夹以开始工作</p>:!owned.length?<p>从空白画布开始，或选择示例</p>:owned.slice(0,5).map(d=><div className="workflow-list-row" key={d.id}><button aria-label={'切换至工作流 '+d.graph.name} aria-current={d.id===current?'page':undefined} onClick={()=>onDocument(d)}><Workflow size={13}/><span>{d.graph.name}</span></button><button className="delete-workflow" aria-label={'删除工作流 '+d.graph.name} title="删除工作流草稿" onClick={()=>onDelete(d)}><Trash2 size={14}/></button></div>)}
   {workspace?.project_storage&&<p className="project-storage-note" title={workspace.project_storage.path}>工作流与历史 · {workspace.project_storage.location==='host'?'本机记录区':'.devflower'}<br/><small>{migration?.state==='RUNNING'?'正在整理旧记录…':migration?.error||'项目内保存，凭据仍在本机'}</small></p>}
   {workspace&&<LegacyDraftCopies drafts={legacy} onCopy={onDocument}/>}
  </section>
 </>;
}

export function StudioTools({onClose,kinds,disabled,onAdd,onLibrary,palette,library,inspecting,onTools,assistantOpen,onAssistant,assistant}) {
 const [query,setQuery]=useState('');
 return <aside className="studio-tools" aria-label="工作流工具">
  <nav className="studio-tools-tabs" aria-label="工作台侧栏"><button aria-label="查看节点工具库" className={!assistantOpen&&!library?'active':''} aria-pressed={!assistantOpen&&!library} onClick={onTools}><Blocks size={14}/>工具</button><button className={library?'active':''} aria-pressed={Boolean(library)} onClick={onLibrary}>示例模板</button><button className={assistantOpen?'active':''} aria-pressed={Boolean(assistantOpen)} onClick={onAssistant}><Sparkles size={14}/>AI 助手</button><button className="tools-close" title="收起侧栏，专注画布" aria-label="收起工作台侧栏" onClick={onClose}><X size={15}/></button></nav>
  {assistant&&<div className="assistant-pane" hidden={!assistantOpen}>{assistant}</div>}
  {!assistantOpen&&!palette&&!library&&!inspecting&&<>
   <div className="studio-tools-heading"><strong>添加节点</strong><small>选择模块，然后在画布连接</small></div>
   <label className="node-library-search"><input aria-label="筛选工具库节点" placeholder="搜索节点…" value={query} onChange={e=>setQuery(e.target.value)}/></label><div className="studio-node-library">{Object.entries(kinds).filter(([key,[name]])=>(key+name).toLowerCase().includes(query.toLowerCase())).map(([key,[name,Icon]])=><button key={key} draggable={!disabled} onDragStart={e=>{e.dataTransfer.setData('application/x-devflower-node',key);e.dataTransfer.effectAllowed='copy'}} disabled={disabled} onClick={()=>onAdd(key)}><span className={'library-node-icon '+key}><Icon size={16}/></span><span>{name}<small>{({agent:'原生会话与模型',condition:'IF / ELSE',router:'规则与语义分类',merge:'等待上游结果',loop:'有界迭代',human:'等待真实确认',verify:'运行验证合同',delivery:'审核与交付门禁',start:'任务与初始输入',output:'汇总与产物',result:'完成当前流程'})[key]}</small></span><Plus size={13}/></button>)}</div>
   <div className="studio-tools-hint"><MousePointer2 size={16}/><p>选择节点配置参数，<br/>选择连线检查数据交接。</p></div>
  </>}
 </aside>;
}
