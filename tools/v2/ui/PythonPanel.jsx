import React,{useEffect,useMemo,useRef,useState} from 'react';
import {ChevronDown,ChevronUp,Code,Copy,Download,X} from 'lucide-react';
import {downloadFile} from './HistoryArchives.jsx';
import {sha256} from './draftTransfer.js';

async function scriptBytes({filename,source,sha256:expected}){
 if(typeof source!=='string'||typeof filename!=='string'||!filename.endsWith('.py')||/[\/\\\0]/.test(filename))
  throw Error('脚本文件身份无效。');
 const bytes=new TextEncoder().encode(source);
 if(await sha256(bytes)!==expected)throw Error('脚本字节校验失败。');
 return bytes;
}

export async function downloadPython(artifact,current=()=>true){
 const bytes=await scriptBytes(artifact);
 if(current())downloadFile(bytes,artifact.filename,'text/x-python;charset=utf-8');
}

const syntax=/(?<comment>#[^\r\n]*)|(?<string>"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(?<keyword>\b(?:and|as|assert|async|await|break|case|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|match|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b)|(?<number>\b(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)\b)/g;

function scriptLines(source){
 const lines=[{text:'',parts:[]}];
 function add(value,color){
  value.split('\n').forEach((text,index)=>{
   if(index)lines.push({text:'',parts:[]});
   if(text){
    const line=lines.at(-1);
    line.parts.push({text,color,start:line.text.length});
    line.text+=text;
   }
  });
 }
 let offset=0;
 for(const token of source.matchAll(syntax)){
  add(source.slice(offset,token.index),'');
  add(token[0],Object.keys(token.groups).find(key=>token.groups[key]));
  offset=token.index+token[0].length;
 }
 add(source.slice(offset),'');
 return lines;
}

function findMatches(lines,query){
 const hits=[];
 const byLine=new Map();
 if(!query)return {hits,byLine};
 for(const [line,{text}] of lines.entries()){
  let offset=0;
  for(;;){
   const start=text.indexOf(query,offset);
   if(start<0)break;
   offset=start+query.length;
   const match={line,start,end:offset,index:hits.length};
   hits.push(match);
   if(!byLine.has(line))byLine.set(line,[]);
   byLine.get(line).push(match);
  }
 }
 return {hits,byLine};
}

function highlight(part,matches,current){
 const end=part.start+part.text.length;
 if(!matches)return part.text;
 const chunks=[];
 let start=part.start;
 for(const hit of matches){
  if(hit.end<=start||hit.start>=end)continue;
  if(hit.start>start)chunks.push(part.text.slice(start-part.start,hit.start-part.start));
  chunks.push(<mark key={hit.index} data-current={hit.index===current}>
   {part.text.slice(Math.max(start,hit.start)-part.start,Math.min(end,hit.end)-part.start)}
  </mark>);
  start=Math.min(end,hit.end);
 }
 if(start<end)chunks.push(part.text.slice(start-part.start));
 return chunks;
}

export function PythonPanel({value,onClose,onResize,width}){
 const {artifact,loading,error}=value;
 const source=artifact?.source??'';
 const [query,setQuery]=useState(''),[current,setCurrent]=useState(0),[feedback,setFeedback]=useState('');
 const search=useRef(null),code=useRef(null),drag=useRef(null);
 const lines=useMemo(()=>scriptLines(source),[source]);
 const {hits,byLine}=useMemo(()=>findMatches(lines,query),[lines,query]);
 useEffect(()=>{if(artifact)search.current?.focus()},[artifact]);
 useEffect(()=>{
  const match=hits[current];
  if(match)code.current?.children[match.line]?.querySelector('mark[data-current=true]')
   ?.scrollIntoView({block:'nearest',inline:'nearest'});
 },[current,hits]);
 function move(step){if(hits.length)setCurrent((current+step+hits.length)%hits.length)}
 function resize(event){
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
   event.preventDefault();
   onResize(width+(event.key==='ArrowLeft'?32:-32));
  }
 }
 async function copy(){
  await scriptBytes(artifact);
  await navigator.clipboard.writeText(source);
  setFeedback('已复制完整脚本。');
 }
 function action(callback){setFeedback('');callback().catch(failure=>setFeedback(failure.message))}
 return <aside className="project-panel python-panel" role="dialog" aria-label="Python 脚本预览" onKeyDown={event=>{
  if(event.key==='Escape'){event.stopPropagation();onClose()}
  if((event.ctrlKey||event.metaKey)&&event.key==='f'&&artifact){event.preventDefault();search.current?.focus()}
 }}>
  <div className="python-divider" role="separator" tabIndex={0} aria-label="调整 Python 面板宽度" aria-orientation="vertical"
   aria-valuemin={300} aria-valuemax={860} aria-valuenow={width} onKeyDown={resize}
   onPointerDown={event=>{
    drag.current={x:event.clientX,width:event.currentTarget.parentElement.clientWidth};
    event.currentTarget.setPointerCapture(event.pointerId);
   }} onPointerMove={event=>{if(drag.current)onResize(drag.current.width+drag.current.x-event.clientX)}}
   onLostPointerCapture={()=>{drag.current=null}}/>
  <header className="project-panel-header">
   <Code size={18} aria-hidden="true"/>
   <div><small>{value.frozen?'本次运行 · 冻结脚本':value.dirty?'已保存版本 · 画布有未保存修改':'已保存工作流'}</small>
    <h2 title={artifact?.filename}>{artifact?.filename||'Python 脚本'}</h2></div>
   <button aria-label="关闭预览" title="关闭预览" onClick={onClose}><X size={17}/></button>
  </header>
  {loading&&<p className="python-message" role="status">正在读取 Python 脚本…</p>}
  {error&&<p className="python-message error-text" role="alert">{error}</p>}
  {artifact&&<>
   <div className="python-tools">
    <div className="python-search"><input ref={search} type="search" aria-label="搜索 Python 源码" placeholder="在脚本中搜索"
     value={query} onChange={event=>{setQuery(event.target.value);setCurrent(0)}}
     onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();move(event.shiftKey?-1:1)}}}/>
     {query&&<span role="status">{hits.length?current+1+' / '+hits.length:'无匹配'}</span>}
     <button aria-label="上一个匹配" disabled={!hits.length} onClick={()=>move(-1)}><ChevronUp size={15}/></button>
     <button aria-label="下一个匹配" disabled={!hits.length} onClick={()=>move(1)}><ChevronDown size={15}/></button>
    </div>
    <div className="python-actions">
     <button onClick={()=>action(copy)}><Copy size={14}/>复制 Python</button>
     <button onClick={()=>action(()=>downloadPython(artifact))}><Download size={14}/>下载 Python</button>
    </div>
    {feedback&&<p role="status">{feedback}</p>}
   </div>
   <pre className="python-source" aria-label="完整 Python 源码" tabIndex={0} ref={code}>{lines.map((line,index)=>
    <span className="python-line" key={index} data-line={index+1}>{line.parts.map((part,i)=>
     <span className={part.color?'python-token-'+part.color:undefined} key={i}>{highlight(part,byLine.get(index),current)}</span>
    )}{index+1<lines.length?'\n':''}</span>)}</pre>
   <footer><span>SHA-256</span><code>{artifact.sha256}</code></footer>
  </>}
 </aside>;
}
