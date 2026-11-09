import React, {useEffect, useRef, useState} from 'react';
import {ArrowLeft, ChevronDown, ChevronRight, FileText, Folder, RefreshCw, X} from 'lucide-react';

export function ProjectFiles({api, workspace, onClose}) {
 const [tree, setTree] = useState(() => new Map());
 const [expanded, setExpanded] = useState(new Set());
 const [preview, setPreview] = useState(null);
 const treeVersion = useRef(0);
 const previewVersion = useRef(0);
 const closeButton = useRef(null);
 const previewOpener = useRef(null);
 const previewBack = useRef(null);

 const query = path => new URLSearchParams({workspace: workspace.id, path});
 async function load(path) {
  const version = treeVersion.current;
  setTree(old => new Map(old).set(path, {loading: true}));
  try {
   const result = await api('project-files?' + query(path));
   if (version === treeVersion.current) setTree(old => new Map(old).set(path, result));
  } catch (error) {
   if (version === treeVersion.current) setTree(old => new Map(old).set(path, {error: error.message}));
  }
 }
 useEffect(() => {
  closeButton.current?.focus();
  load('');
  return () => {
   treeVersion.current++;
   previewVersion.current++;
  };
 }, []);
 function refresh() {
  treeVersion.current++;
  previewVersion.current++;
  setTree(new Map());
  setExpanded(new Set());
  setPreview(null);
  load('');
 }
 function toggle(path) {
  const opening = !expanded.has(path);
  setExpanded(old => {
   const next = new Set(old);
   if (opening) next.add(path);
   else next.delete(path);
   return next;
  });
  if (opening && (!tree.has(path) || tree.get(path).error)) load(path);
 }
 async function readFile(path, opener) {
  const version = ++previewVersion.current;
  previewOpener.current = opener;
  setPreview({path, loading: true});
  try {
   const result = await api('project-file?' + query(path));
   if (version === previewVersion.current) setPreview(result);
  } catch (error) {
   if (version === previewVersion.current) setPreview({path, error: error.message});
  }
 }
 useEffect(() => {
  if (preview?.path) previewBack.current?.focus();
 }, [preview?.path]);
 function back() {
  previewVersion.current++;
  setPreview(null);
  requestAnimationFrame(() => {
   const opener = previewOpener.current;
   if (opener?.getClientRects().length) opener.focus();
   else closeButton.current?.focus();
  });
 }
 function rows(path) {
  const node = tree.get(path);
  if (!node || node.loading) return <p className="muted small" role="status">正在读取目录…</p>;
  if (node.error) return <p className="error-text" role="alert">{node.error}</p>;
  return <>
   {node.entries.length === 0 ? <p className="muted small">此处没有可浏览的条目</p> :
    <ul>{node.entries.map(entry => <li key={entry.path}>
     {entry.directory ?
      <><button aria-expanded={expanded.has(entry.path)} onClick={() => toggle(entry.path)}>
       {expanded.has(entry.path) ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}
       <Folder size={15}/><span>{entry.name}</span>
      </button>{expanded.has(entry.path) && rows(entry.path)}</> :
      <button aria-label={'预览文件 ' + entry.path} onClick={event => readFile(entry.path, event.currentTarget)}>
       <FileText size={15}/><span>{entry.name}</span>
      </button>}
    </li>)}</ul>}
   {node.truncated && <p className="muted small">目录较大，只检查前 512 项。</p>}
  </>;
 }
 return <aside className={'project-panel' + (preview ? ' has-preview' : '')} aria-label="项目文件"
  onKeyDown={event => {
   if (event.key === 'Escape' && preview) {
    event.stopPropagation();
    back();
   }
  }}>
  <header className="project-panel-header">
   <div><h2>项目文件</h2><p className="muted small">{workspace.name} · 只读</p></div>
   <button aria-label="刷新项目文件" title="刷新项目文件" onClick={refresh}><RefreshCw size={16}/></button>
   <button ref={closeButton} aria-label="关闭项目文件" title="关闭项目文件" onClick={onClose}><X size={16}/></button>
  </header>
  <div className="project-panel-content">
   <nav className="project-tree" aria-label="项目文件树">{rows('')}</nav>
   {preview && <section className="project-preview" aria-label="文件预览">
    <header><button ref={previewBack} aria-label="返回文件树" title="返回文件树" onClick={back}><ArrowLeft size={16}/></button>
     <h3>{preview.path}</h3></header>
    {preview.loading ? <p className="muted small" role="status">正在读取文件…</p> :
     preview.error ? <p className="error-text" role="alert">{preview.error}</p> :
     <pre tabIndex={0}>{preview.text || <span className="muted">空文件</span>}</pre>}
   </section>}
  </div>
 </aside>;
}
