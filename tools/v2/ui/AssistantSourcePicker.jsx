import React,{useState} from 'react';

export const filePath=file=>typeof file==='string'?file:file.path;
export const fileStart=file=>typeof file==='string'?1:file.start;

export function AssistantSourcePicker({api,workspace,files,onFiles,busy,perform}){
 const [directory,setDirectory]=useState(''),[listing,setListing]=useState(null);
 const [path,setPath]=useState(''),[query,setQuery]=useState(''),[matches,setMatches]=useState(null);
 const [excerpt,setExcerpt]=useState(null);
 const attached=value=>files.some(file=>filePath(file)===value);
 const add=(value,start=1)=>{
  if(value&&!attached(value)&&files.length<3)onFiles([...files,start===1?value:{path:value,start}]);
 };
 async function browse(next){
  const result=await api('project-files?'+new URLSearchParams({workspace,path:next}));
  setDirectory(result.path);setListing(result);setMatches(null);
 }
 async function search(){
  setMatches(await api('assistant/sources',{workspace,query,directory:directory||null}));
 }
 async function read(value,start=1){
  setExcerpt(await api('assistant/source_excerpt',{workspace,path:value,start}));
 }
 return <>
  <div className="assistant-file-input">
   <input aria-label="工作区相对文件路径" placeholder="例如 docs/plan.md" maxLength={512}
    value={path} disabled={busy} onChange={event=>setPath(event.target.value)}/>
   <button disabled={!path.trim()||files.length>=3||busy} onClick={()=>{add(path.trim());setPath('')}}>附加文件</button>
  </div>
  <div className="assistant-file-input">
   <input aria-label="文件名或源码符号" placeholder="文件名或源码符号" maxLength={120} value={query} disabled={busy}
    onChange={event=>setQuery(event.target.value)}/>
   <button disabled={!query.trim()||busy} onClick={()=>perform(search)}>搜索源码</button>
   <button disabled={busy} onClick={()=>perform(()=>browse(''))}>浏览项目目录</button>
  </div>
  {matches&&<section aria-label="源码查询结果">
   {!matches.matches.length&&<p>当前搜索范围没有匹配项。</p>}
   {matches.truncated&&<p className="field-note">搜索达到读取上限，可先进入更小的目录。</p>}
   <ul>{matches.matches.map(item=><li key={item.path+':'+(item.line||0)}>
    <button disabled={busy} onClick={()=>perform(()=>read(item.path,Math.max(1,(item.line||1)-3)))}>
     {item.path}{item.line&&':'+item.line}
    </button>
   </li>)}</ul>
  </section>}
  {listing&&!matches&&<section aria-label="项目文件浏览">
   <p>{directory||'项目根目录'}</p>
   {directory&&<button disabled={busy} onClick={()=>perform(()=>browse(directory.split('/').slice(0,-1).join('/')))}>上级目录</button>}
   <ul>{listing.entries.slice(0,24).map(item=><li key={item.path}><button disabled={busy} onClick={()=>perform(
    ()=>item.directory?browse(item.path):read(item.path))}>{item.name}{item.directory?'/':''}</button></li>)}</ul>
   {(listing.truncated||listing.entries.length>24)&&<p className="field-note">目录仅显示前 24 项，可搜索文件名。</p>}
  </section>}
  {excerpt&&<section className="assistant-preview" aria-label="源码行号预览">
   <h3>{excerpt.path}:{excerpt.start_line}–{excerpt.end_line}</h3>
   <pre>{excerpt.text}</pre>
   <button disabled={busy||attached(excerpt.path)||files.length>=3} onClick={()=>add(excerpt.path,excerpt.start_line)}>附加此片段</button>
   {excerpt.start_line>1&&<button disabled={busy} onClick={()=>perform(()=>read(excerpt.path,Math.max(1,excerpt.start_line-80)))}>上一段</button>}
   {excerpt.end_line<excerpt.total_lines&&<button disabled={busy} onClick={()=>perform(()=>read(excerpt.path,excerpt.end_line+1))}>下一段</button>}
   <button onClick={()=>setExcerpt(null)}>关闭源码预览</button>
  </section>}
  <ul>{files.map(file=><li key={filePath(file)}><span>{filePath(file)}{fileStart(file)>1&&':'+fileStart(file)}</span>
   <button aria-label={'移除附件 '+filePath(file)} disabled={busy} onClick={()=>onFiles(files.filter(item=>item!==file))}>移除</button></li>)}</ul>
 </>;
}
