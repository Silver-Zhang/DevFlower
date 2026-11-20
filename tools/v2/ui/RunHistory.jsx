import React,{useEffect,useMemo,useState} from 'react';

const PAGE_SIZE=12;
const FAILED=new Set(['CHECK_FAILED','STOPPED','FAILED']);

export function RunHistory({api,workspace,runs,stateLabel,onOpen}){
 const groups=useMemo(()=>{
  const grouped=new Map();
  for(const row of [...runs].sort((a,b)=>b.created_at-a.created_at)){
   const key=row.series?.id||row.id;
   if(!grouped.has(key))grouped.set(key,[]);
   grouped.get(key).push(row);
  }
  return [...grouped];
 },[runs]);
 const [groupPage,setGroupPage]=useState(0),[openGroup,setOpenGroup]=useState(undefined);
 const [roundPage,setRoundPage]=useState(0),[failedOnly,setFailedOnly]=useState(false);
 const [summary,setSummary]=useState({});
 const [loading,setLoading]=useState(false),[error,setError]=useState('');
 const visible=groups.slice(groupPage*PAGE_SIZE,(groupPage+1)*PAGE_SIZE);
 const active=openGroup===undefined?visible.find(([,rows])=>rows[0].execution==='native-cli')?.[0]:openGroup;
 const activeRows=visible.find(([key])=>key===active)?.[1]||[];
 const rows=activeRows.slice(roundPage*PAGE_SIZE,(roundPage+1)*PAGE_SIZE);
 const ids=rows.filter(row=>row.execution==='native-cli').map(row=>row.id).join(',');
 useEffect(()=>{
  let current=true;
  setError('');setLoading(Boolean(ids));
  if(ids)api('script-run/summaries?'+new URLSearchParams({workspace,ids})).then(results=>{
   if(current)setSummary(value=>({...value,...Object.fromEntries(results.map(row=>[row.id,row]))}));
  }).catch(failure=>{if(current)setError(failure.message)}).finally(()=>{if(current)setLoading(false)});
  return()=>{current=false};
 },[api,workspace,ids,runs]);

 function renderRow(row){
  const info=summary[row.id];
  const state=info?.state||row.state;
  if(failedOnly&&!FAILED.has(state))return null;
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
 function changeGroupPage(offset){
  setGroupPage(page=>page+offset);setOpenGroup(undefined);setRoundPage(0);
 }
 return <div className="workspace-run-rows">
  <label><input type="checkbox" checked={failedOnly} onChange={event=>setFailedOnly(event.target.checked)}/> 只看失败</label>
  {failedOnly&&<p className="field-note">展开所需系列查看已核验的失败，可逐页查询更早的轮次。</p>}
  {loading&&<p role="status">正在核验本页运行结果…</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
  {visible.map(([key,group])=>{
   const head=group[0];
   if(head.execution!=='native-cli')return renderRow(head);
   return <details key={key} open={active===key}>
    <summary onClick={event=>{
     event.preventDefault();setOpenGroup(active===key?null:key);setRoundPage(0);
    }}>{head.name}{head.series?' · 系列 '+key.slice(0,8)+' · 共 '+group.length+' 轮':' · 原生脚本'}</summary>
    {active===key&&<>
     {rows.map(renderRow)}
     {failedOnly&&!loading&&!rows.some(row=>FAILED.has(summary[row.id]?.state||row.state))&&
      <p className="field-note">本页没有已确认的失败记录。</p>}
     {group.length>PAGE_SIZE&&<p>
      <button type="button" disabled={roundPage===0} onClick={()=>setRoundPage(page=>page-1)}>较新轮次</button>
      {' 第 '+(roundPage+1)+' 页 '}
      <button type="button" disabled={(roundPage+1)*PAGE_SIZE>=group.length} onClick={()=>setRoundPage(page=>page+1)}>较早轮次</button>
     </p>}
    </>}
   </details>;
  })}
  {!groups.length&&<p className="field-note">尚无运行记录</p>}
  {groups.length>PAGE_SIZE&&<p>
   <button type="button" disabled={groupPage===0} onClick={()=>changeGroupPage(-1)}>较新系列</button>
   {' 第 '+(groupPage+1)+' 页 '}
   <button type="button" disabled={(groupPage+1)*PAGE_SIZE>=groups.length} onClick={()=>changeGroupPage(1)}>较早系列</button>
  </p>}
 </div>;
}
