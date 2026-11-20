import React,{useEffect,useMemo,useState} from 'react';

const PAGE_SIZE=12;
const FAILED=new Set(['CHECK_FAILED','STOPPED','FAILED']);

export function RunHistory({api,workspace,runs,stateLabel,onOpen}){
 const ordered=useMemo(()=>[...runs].sort((a,b)=>b.created_at-a.created_at),[runs]);
 const [page,setPage]=useState(0),[failedOnly,setFailedOnly]=useState(false);
 const [summary,setSummary]=useState({}),[expanded,setExpanded]=useState({});
 const [loading,setLoading]=useState(false),[error,setError]=useState('');
 const visible=ordered.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
 const ids=visible.filter(row=>row.execution==='native-cli').map(row=>row.id).join(',');
 useEffect(()=>{
  let current=true;
  setError('');setLoading(Boolean(ids));
  if(ids)api('script-run/summaries?'+new URLSearchParams({workspace,ids})).then(rows=>{
   if(current)setSummary(value=>({...value,...Object.fromEntries(rows.map(row=>[row.id,row]))}));
  }).catch(failure=>{if(current)setError(failure.message)}).finally(()=>{if(current)setLoading(false)});
  return()=>{current=false};
 },[api,workspace,ids,runs]);
 const groups=new Map();
 for(const row of visible){
  if(failedOnly&&!FAILED.has(summary[row.id]?.state||row.state))continue;
  const key=row.series?.id||row.id;
  if(!groups.has(key))groups.set(key,[]);
  groups.get(key).push(row);
 }
 function renderRow(row){
  const info=summary[row.id];
  const state=info?.state||row.state;
  return <button type="button" className="workflow-link" key={row.id} onClick={()=>onOpen(row)}>
   <span><strong>{state?stateLabel(state):'待核验'}</strong> · {row.name}
    <small>{row.execution==='native-cli'?'原生脚本':'受管运行'}
     {row.series&&' · 第 '+row.series.round+' 轮'} · {new Date(row.created_at*1000).toLocaleString()}</small>
    {info?.failures.length>0&&<small className="error-text">{info.failures.map(item=>
     item.name+(item.returncode!=null?'（退出 '+item.returncode+'）':'')).join('、')}</small>}
    {info?.task&&<small>本轮任务 · {info.task}</small>}
   </span>
  </button>;
 }
 return <div className="workspace-run-rows">
  <label><input type="checkbox" checked={failedOnly} onChange={event=>setFailedOnly(event.target.checked)}/> 只看本页失败</label>
  {loading&&<p role="status">正在核验本页运行结果…</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
  {[...groups].map(([key,rows])=>rows[0].series?<details key={key} open={expanded[key]??true}
   onToggle={event=>{const open=event.currentTarget.open;if(expanded[key]!==open)setExpanded(value=>({...value,[key]:open}));}}>
   <summary>{rows[0].name} · 系列 {key.slice(0,8)} · 本页 {rows.length} 轮</summary>
   {rows.map(renderRow)}
  </details>:renderRow(rows[0]))}
  {!groups.size&&!loading&&<p className="field-note">{failedOnly?'本页没有已确认的失败记录。':'尚无运行记录'}</p>}
  {ordered.length>PAGE_SIZE&&<p>
   <button type="button" disabled={page===0} onClick={()=>setPage(value=>value-1)}>较新记录</button>
   {' 第 '+(page+1)+' 页 '}
   <button type="button" disabled={(page+1)*PAGE_SIZE>=ordered.length} onClick={()=>setPage(value=>value+1)}>较早记录</button>
  </p>}
 </div>;
}
