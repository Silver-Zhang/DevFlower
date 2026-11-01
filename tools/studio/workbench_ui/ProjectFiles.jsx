import React,{useCallback,useEffect,useState} from 'react';
import {AlertCircle,ChevronDown,ChevronRight,FileText,Folder,FolderOpen,RefreshCw,X} from 'lucide-react';
import './project-files.css';

// Read-only project browser. The server owns path validation, symlink/hardlink
// refusal, protected directories and preview budgets; this view only renders.
export function ProjectFiles({api,workspace,onClose}){
 const [tree,setTree]=useState({}),[open,setOpen]=useState(()=>new Set()),[preview,setPreview]=useState(null),[busy,setBusy]=useState('');
 const load=useCallback(async path=>{
  setBusy(path);
  try{
   const r=await api('/api/project-files?workspace='+workspace.id+'&path='+encodeURIComponent(path));
   setTree(old=>({...old,[path]:r}));
  }catch(e){setTree(old=>({...old,[path]:{entries:[],error:e.message}}))}
  finally{setBusy('')}
 },[api,workspace.id]);
 useEffect(()=>{setTree({});setOpen(new Set());setPreview(null);load('')},[workspace.id,load]);
 const toggle=async path=>{
  setOpen(old=>{const next=new Set(old);next.has(path)?next.delete(path):next.add(path);return next});
  if(!tree[path]&&path!=='')await load(path);
 };
 const show=async path=>{
  setPreview({path,loading:true});
  try{const r=await api('/api/project-file?workspace='+workspace.id+'&path='+encodeURIComponent(path));setPreview({...r,loading:false})}
  catch(e){setPreview({path,error:e.message,loading:false})}
 };
 const rows=(path,depth)=>{
  const node=tree[path];
  if(!node)return null;
  if(node.error)return <p className="project-files-error" role="alert"><AlertCircle size={13}/>{node.error}</p>;
  return <><ul className="project-file-list">{node.entries.map(entry=>
   <li key={entry.path}>
    {entry.directory?
     <button className="project-row" style={{paddingLeft:8+depth*14}} aria-expanded={open.has(entry.path)} onClick={()=>toggle(entry.path)}>
      {open.has(entry.path)?<ChevronDown size={13}/>:<ChevronRight size={13}/>}{open.has(entry.path)?<FolderOpen size={14}/>:<Folder size={14}/>}<span>{entry.name}</span>
     </button>:
     <button className={'project-row '+(preview?.path===entry.path?'active':'')} style={{paddingLeft:8+depth*14+13}} aria-label={'预览文件 '+entry.path} onClick={()=>show(entry.path)}>
      <FileText size={14}/><span>{entry.name}</span><small>{entry.size}</small>
     </button>}
    {entry.directory&&open.has(entry.path)&&(busy===entry.path?<p className="project-files-note">正在读取…</p>:rows(entry.path,depth+1))}
   </li>)}
   {!node.entries.length&&<li className="project-files-note">此目录为空</li>}
  </ul>{node.truncated&&<p className="project-files-note">条目过多，仅显示前 512 项</p>}</>;
 };
 return <section className="project-files" aria-label="项目文件">
  <header><strong>项目文件</strong><small>{workspace.path}</small>
   <button aria-label="刷新项目文件" onClick={()=>{setTree({});setOpen(new Set());load('')}}><RefreshCw size={13}/></button>
   <button aria-label="关闭项目文件" onClick={onClose}><X size={14}/></button></header>
  <div className="project-tree">{busy===''&&!tree['']?<p className="project-files-note">正在读取…</p>:rows('',0)}</div>
  {preview&&<div className="project-preview" aria-label="文件预览">
   <header><strong>{preview.path}</strong><button aria-label="关闭文件预览" onClick={()=>setPreview(null)}><X size={13}/></button></header>
   {preview.loading?<p className="project-files-note">正在读取…</p>:preview.error?<p className="project-files-error" role="alert"><AlertCircle size={13}/>{preview.error}</p>:<pre>{preview.text}</pre>}
  </div>}
 </section>;
}
