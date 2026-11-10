import React,{useEffect,useRef,useState} from 'react';
import {GitBranch,GitPullRequest,RefreshCw,X} from 'lucide-react';

const route=(name,query)=>'git-platform/'+name+'?'+new URLSearchParams(query);
const uid=()=>crypto.randomUUID().replaceAll('-','');
const reviewLabel={AWAITING_INDEPENDENT_REVIEW:'等待独立审核',ACCEPT:'ACCEPT',REJECT:'REJECT',
 ACCEPT_WITH_LIMITATIONS:'ACCEPT_WITH_LIMITATIONS'};
const ciLabel={success:'通过',failure:'失败',failed:'失败',running:'运行中',in_progress:'运行中',
 completed:'已完成',queued:'等待',pending:'等待',cancelled:'已取消',canceled:'已取消',
 skipped:'已跳过',timed_out:'超时',neutral:'中性',manual:'等待人工',unknown:'未确认'};
const state=value=>ciLabel[value]||value||'未报告';
const stamp=value=>value?new Date(value).toLocaleString():'未报告';
function Link({item,children}){
 return item?.url?<a href={item.url} target="_blank" rel="noopener noreferrer">{children}</a>:null;
}
function Commit({value}){
 return <code className="git-sha">{value||'未报告'}</code>;
}
function CheckStatus({row}){
 return <span className={'git-state'+(['failure','failed','timed_out'].includes(row.conclusion||row.state)?' failed':'')}
  title={[row.state,row.conclusion].filter(Boolean).join(' / ')}>{state(row.conclusion||row.state)}</span>;
}

export function GitWorkspace({api,workspace,onClose}){
 const storageKey='devflower-v2-git-review:'+workspace.id;
 const [repo,setRepo]=useState(null),[tab,setTab]=useState('repository'),[items,setItems]=useState([]);
 const [selected,setSelected]=useState(null),[checks,setChecks]=useState(null),[jobs,setJobs]=useState(null),[log,setLog]=useState(null);
 const [approval,setApproval]=useState(null),[pending,setPending]=useState(()=>{
  try{return JSON.parse(sessionStorage.getItem(storageKey))}catch{return null}
 });
 const [notice,setNotice]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const close=useRef(null),epoch=useRef(0);
 const platform=repo?.platform,changeName=platform==='gitlab'?'MR':'PR';
 const platformName=platform==='gitlab'?'GitLab':platform==='github'?'GitHub':'Git 平台';
 const read=(name,params={})=>api(route(name,{workspace:workspace.id,
  ...(name==='repository'?{}:{binding:params.binding||repo?.binding}),...params}));
 const scope=selected&&['branches','changes'].includes(tab)?{
  target_type:tab==='branches'?'branch':'change',target:tab==='branches'?selected.name:selected.id,
  sha:tab==='branches'?selected.sha:selected.head_sha}:null;
 function clearDetail(){setSelected(null);setChecks(null);setJobs(null);setLog(null);setApproval(null)}
 async function request(load,apply){
  const ticket=++epoch.current;
  setBusy(true);setError('');setNotice('');
  try{
   const result=await load();
   if(epoch.current===ticket)apply(result);
  }catch(failure){if(epoch.current===ticket)setError(failure.message)}
  finally{if(epoch.current===ticket)setBusy(false)}
 }
 function openTab(next){
  setTab(next);setItems([]);clearDetail();
  if(next==='repository')request(()=>read(next),setRepo);
  else request(()=>read(next),result=>setItems(result.items));
 }
 useEffect(()=>{
  close.current?.focus();
  request(()=>read('repository'),setRepo);
  return()=>{epoch.current++}
 },[]);
 function choose(item){
  clearDetail();
  if(tab==='branches'){setSelected(item);return}
  const kind=tab==='changes'?'change':'issue';
  request(()=>read(kind,{number:item.id}),result=>setSelected(result.item));
 }
 function keep(value){
  if(value)sessionStorage.setItem(storageKey,JSON.stringify(value));else sessionStorage.removeItem(storageKey);
  setPending(value);
 }
 function submitReview(value){
  keep(value);
  request(()=>api('git-platform/review',value),row=>{
   keep(null);setApproval(null);setNotice('已记录本机审核任务 '+row.id.slice(0,12)+'。');
  });
 }
 function inspectReview(){
  request(()=>read('reviews',{binding:pending.binding}),result=>{
   const found=result.items.find(row=>String(row.change)===String(pending.change)&&row.head_sha===pending.head_sha);
   if(found){keep(null);setApproval(null);setNotice('已核对同一提交的本机审核任务 '+found.id.slice(0,12)+'。');if(tab==='reviews')setItems(result.items)}
   else setNotice('当前未找到该任务。可明确使用同一审核意图再次提交。')
  });
 }
 return <aside className="project-panel git-platform-panel" aria-label="Git 平台" onKeyDown={event=>{
  if(event.key==='Escape'){event.stopPropagation();if(approval)setApproval(null);else onClose()}
 }}>
  <header className="project-panel-header">
   <div><h2><GitBranch size={16}/> {platformName}</h2><p className="muted small">{repo?.repository||workspace.name}</p></div>
   <button aria-label="刷新 Git 平台" disabled={busy} onClick={()=>openTab(tab)}><RefreshCw size={15}/></button>
   <button ref={close} aria-label="关闭 Git 平台" onClick={onClose}><X size={16}/></button>
  </header>
  <nav aria-label="Git 平台视图" className="git-tabs">
   {Object.entries({repository:'仓库',branches:'分支',issues:'Issue',changes:changeName,reviews:'审核与交付'}).map(([key,label])=>
    <button key={key} aria-pressed={tab===key} disabled={!repo&&key!=='repository'} onClick={()=>openTab(key)}>{label}</button>)}
  </nav>
  <div className="git-platform-scroll">
   {busy&&<p role="status" className="field-note">正在读取或核对记录…</p>}
   {error&&<p role="alert" className="error-text">{error}</p>}
   {notice&&<p role="status" className="field-note">{notice}</p>}
   {pending&&<section className="git-review-pending" aria-label="待核对审核意图">
    <p>本机审核意图 · {changeName} #{pending.change}</p><Commit value={pending.head_sha}/>
    <div className="git-actions"><button disabled={busy} onClick={inspectReview}>核对审核任务</button>
     <button disabled={busy} onClick={()=>submitReview(pending)}>再次提交同一审核意图</button>
     <button disabled={busy} onClick={()=>{keep(null);setApproval(null)}}>关闭核对提示</button></div>
   </section>}
   {tab==='repository'&&repo&&<section className="git-overview">
    <h3>{repo.remote.name||repo.repository}</h3>
    <dl><dt>本机分支</dt><dd>{repo.local.branch||'分离 HEAD 或尚无分支'}</dd>
     <dt>本机提交</dt><dd><Commit value={repo.local.head}/></dd>
     <dt>远端默认分支</dt><dd>{repo.remote.default_branch||'未报告'}</dd>
     <dt>访问类型</dt><dd>{repo.remote.private===true?'私有':repo.remote.private===false?'公开':'未报告'}</dd></dl>
    <Link item={repo.remote}>在 {platformName} 查看仓库</Link>
    <p className="field-note">{repo.delivery.reason}</p>
   </section>}
   {tab==='reviews'&&<section className="git-reviews" aria-label="本机审核任务">
    <h3>审核与交付</h3><p className="field-note">{repo?.delivery.reason}</p>
    {!items.length&&!busy&&<p>当前仓库尚无本机审核任务。</p>}
    {items.map(item=><article key={item.id}>
     <strong>{changeName} #{item.change} · {item.title}</strong><p>{reviewLabel[item.state]||item.state}</p>
     <Commit value={item.head_sha}/><p className="field-note">{new Date(item.created_at*1000).toLocaleString()}</p>
     <Link item={item}>打开远端变更</Link>
    </article>)}
   </section>}
   {['branches','issues','changes'].includes(tab)&&<div className="git-work">
    <section className="git-records" aria-label={tab==='branches'?'远端分支':tab==='issues'?'远端 Issue':'远端变更'}>
     {!items.length&&!busy&&<p className="field-note">当前读取批次没有记录。</p>}
     {items.map(item=><button key={item.id||item.name} onClick={()=>choose(item)}
      aria-pressed={Boolean(selected&&(item.id?item.id===selected.id:item.name===selected.name))}>
      <span>{tab==='branches'?item.name:'#'+item.id+' '+item.title}</span>
      <small>{tab==='branches'?(item.protected===true?'受保护':item.protected===false?'普通分支':'保护状态未报告'):state(item.state)}</small>
     </button>)}
    </section>
    {selected&&<section className="git-detail" aria-label="Git 对象详情">
     <h3>{tab==='branches'?selected.name:'#'+selected.id+' '+selected.title}</h3>
     {scope&&<p><Commit value={scope.sha}/></p>}
     {tab==='changes'&&<p>{selected.source_branch} → {selected.target_branch} · {selected.merged?'已合并':selected.draft?'草稿':state(selected.state)}</p>}
     {tab==='issues'&&<p>{state(selected.state)} · {stamp(selected.updated_at)}</p>}
     {selected.body&&<details><summary>查看远端说明</summary><pre>{selected.body}</pre></details>}
     <Link item={selected}>在 {platformName} 查看</Link>
     {scope?.sha&&<div className="git-actions">
      <button disabled={busy} onClick={()=>{setJobs(null);setLog(null);request(()=>read('checks',scope),result=>setChecks(result.items))}}>
       {platform==='gitlab'?'查看 Pipeline':'查看 CI'}</button>
      {tab==='changes'&&<button disabled={busy||!!pending} onClick={()=>setApproval({workspace:workspace.id,binding:repo.binding,change:selected.id,
       head_sha:scope.sha,intent:uid(),authorization:'create-local-review'})}><GitPullRequest size={15}/>创建本机审核任务</button>}
     </div>}
     {approval&&<section className="git-review-pending" aria-label="确认本机审核">
      <p>固定 {repo.repository} · {changeName} #{approval.change}</p><Commit value={approval.head_sha}/>
      <p>仅保存本机审核任务，独立审核员稍后核验该提交。</p>
      <div className="git-actions"><button disabled={busy} onClick={()=>setApproval(null)}>返回</button>
       <button className="primary" disabled={busy} onClick={()=>submitReview(approval)}>确认创建本机审核任务</button></div>
     </section>}
     {checks&&<section className="git-ci" aria-label="提交检查">
      <h3>{platform==='gitlab'?'Pipeline':'CI 检查'}</h3>
      {!checks.length&&<p className="field-note">平台未返回此提交的检查记录。</p>}
      {checks.map(check=><button key={check.id} onClick={()=>{setLog(null);
       request(()=>read('jobs',{...scope,check:check.id}),result=>setJobs({check,rows:result.items}))}}>
       <span>{check.name}</span><CheckStatus row={check}/></button>)}
     </section>}
     {jobs&&<section className="git-ci" aria-label="检查作业">
      <h3>{jobs.check.name} · 作业</h3>
      {!jobs.rows.length&&<p className="field-note">平台未返回可读取作业。</p>}
      {jobs.rows.map(job=><button key={job.id} onClick={()=>request(
       ()=>read('log',{...scope,check:jobs.check.id,job:job.id}),result=>setLog({row:job,...result.log}))}>
       <span>{job.name}</span><CheckStatus row={job}/></button>)}
     </section>}
     {log&&<section className="git-log" aria-label="CI 作业日志">
      <h3>{log.row.name} · 日志</h3><p className="field-note">提交 <Commit value={log.sha}/> · 检查 {log.check_id} · 作业 {log.job_id}</p>
      {log.truncated&&<p className="field-note">日志达到读取预算，已截取。</p>}
      <pre tabIndex={0}>{log.text||'平台未返回日志文本。'}</pre><Link item={log.row}>打开远端作业</Link>
     </section>}
    </section>}
   </div>}
  </div>
 </aside>;
}
