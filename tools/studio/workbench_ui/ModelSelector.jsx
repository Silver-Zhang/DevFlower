import React,{useEffect,useRef,useState} from 'react';

// The server selects approved endpoints and credentials. No browser-supplied URL.
export function ModelSelector({api,profile,model,effort,onChange,onCatalog,disabled=false,workspace,mode='Ask',permission,tools,timeout=300,context,onSelecting}){
 const [catalog,setCatalog]=useState(profile),[pending,setPending]=useState(false),[error,setError]=useState('');
 const version=useRef(0),selectionVersion=useRef(0),statusVersion=useRef(0),checkingRef=useRef(false),verifyButton=useRef(null);
 const [choosing,setChoosing]=useState(null);const pickVersion=useRef(0),pickOwner=useRef('');pickOwner.current=profile?.id+':'+workspace?.id;
 const [admission,setAdmission]=useState(null),[permit,setPermit]=useState(null),[answer,setAnswer]=useState(''),[checking,setChecking]=useState(false);
 const selection={workspace:workspace?.id,profile:profile?.id,model,effort,mode,permission:permission||(mode==='Agent'?'workspace-write':'read'),timeout,...(tools?{tools}:{}),...(context?{context}:{})};
 const selectionKey=JSON.stringify(selection);
 useEffect(()=>{const ticket=++selectionVersion.current;checkingRef.current=false;setAdmission(null);setPermit(null);setAnswer('');setChecking(false);
  async function readStatus(){if(!workspace?.id||!profile?.id||checkingRef.current)return;const read=++statusVersion.current;try{const value=await api('/api/model-admission/status',selection);if(ticket===selectionVersion.current&&read===statusVersion.current)setAdmission(value)}catch(e){if(ticket===selectionVersion.current&&read===statusVersion.current)setAdmission(null)}}
  readStatus();const timer=setInterval(readStatus,15000);
  return()=>{selectionVersion.current++;statusVersion.current++;clearInterval(timer)}
 },[selectionKey]);
 function cancelCheck(){setPermit(null);verifyButton.current?.focus()}
 async function prepareCheck(){const ticket=selectionVersion.current;setError('');try{const value=await api('/api/model-admission/prepare',selection);if(ticket===selectionVersion.current){setPermit(value);setAnswer('')}}catch(e){if(ticket===selectionVersion.current)setError(e.message)}}
 async function verify(){const ticket=selectionVersion.current;checkingRef.current=true;statusVersion.current++;setChecking(true);setError('');try{const value=await api('/api/model-admission/verify',{id:permit.id,sha256:permit.sha256,answer});if(ticket===selectionVersion.current){setAdmission(value);setPermit(null)}}catch(e){if(ticket===selectionVersion.current){setAdmission(null);setError(e.message);setPermit(null)}}finally{if(ticket===selectionVersion.current){checkingRef.current=false;setChecking(false)}}}
 async function choose(values){
  const ticket=++pickVersion.current,owner=pickOwner.current,m=(current?.models||[]).find(x=>x.id===values.model);
  const known=(values.model===profile.model&&values.effort===profile.effort)||(m?.operator_registered&&m.efforts?.includes(values.effort));
  setError('');setChoosing(values);onSelecting?.(true);
  try{
   if(!known)await api('/api/model-selection',{workspace:workspace?.id,profile:profile.id,...values});
   if(ticket!==pickVersion.current||owner!==pickOwner.current)return;
   onChange(values);if(!known)await load(false);
  }catch(e){if(ticket===pickVersion.current&&owner===pickOwner.current)setError(e.message)}
  finally{if(ticket===pickVersion.current){setChoosing(null);onSelecting?.(false)}}
 }
 useEffect(()=>()=>{pickVersion.current++;onSelecting?.(false)},[profile?.id,workspace?.id]);
 async function load(refresh=false){const v=++version.current;setPending(true);setError('');try{
  let result=await api('/api/models',{profile:profile.id,refresh,workspace:workspace?.id});
  if(v===version.current&&!refresh&&!result.attempted_at)result=await api('/api/models',{profile:profile.id,refresh:true,workspace:workspace?.id});
  if(v!==version.current)return;setCatalog(result);onCatalog?.(result);
 }catch(e){if(v===version.current)setError(e.message)}finally{if(v===version.current)setPending(false)}}
 useEffect(()=>{setCatalog(profile);load(false);return()=>{version.current++}},[profile?.id,workspace?.id]);
 const current=catalog?.id===profile?.id?catalog:profile;
 const models=current?.models||[],selected=models.find(m=>m.id===model);
 const reasoning=m=>m?.selectable_efforts?.length?m.selectable_efforts:m?.native_efforts?.length?m.native_efforts:m?.efforts?.length?m.efforts:[profile?.effort].filter(Boolean);
 const efforts=reasoning(selected);
 return <section className="model-picker" aria-label="模型发现">
  <label className="field"><span>模型</span><select aria-label="模型" disabled={disabled||pending||Boolean(choosing)} value={choosing?.model||model||''} onChange={e=>{const m=models.find(x=>x.id===e.target.value);choose({model:m.id,effort:reasoning(m).includes(effort)?effort:reasoning(m)[0]||''})}}>
   {!selected&&<option value={model||''}>{model||'请选择模型'}（目录尚未确认）</option>}
   {models.map(m=><option key={m.id} value={m.id} >{m.name||m.id}</option>)}
  </select></label>
  <label className="field"><span>推理强度</span><select aria-label="推理强度" disabled={disabled||pending||Boolean(choosing)} value={effort||''} onChange={e=>choose({model,effort:e.target.value})}>{!efforts.includes(effort)&&<option value={effort||''}>{effort||'unknown'}</option>}{efforts.map(x=><option key={x}>{x}</option>)}</select></label>
  {choosing&&<small role="status">正在选择模型…</small>}
  <button type="button" className="refresh-models" aria-busy={pending} disabled={pending||disabled} onClick={()=>load(true)}>刷新模型</button>
  {pending&&<small role="status" aria-label="模型读取状态">正在读取模型目录…</small>}
  {(error||current?.error)&&<p role="alert">{error||current.error}</p>}
  <details className="model-connection-details"><summary>连接详情与手动检查</summary><p>模型直接来自当前工具或账号目录；选择即可使用。调用失败会显示真实错误，模型选择不会改变工作区权限。</p>
  <div className="admission-status" aria-label="执行准入">
   <p role="status">{admission?.execution_allowed&&admission.expires_at*1000>Date.now()?`当前配置已通过原生执行验证 · 有效至 ${new Date(admission.expires_at*1000).toLocaleTimeString()}`:'当前配置执行能力待验证；模型目录和登记不会授予执行权限。'}</p>
   <button ref={verifyButton} type="button" disabled={disabled||checking||!workspace?.id||!selected?.operator_registered} onClick={prepareCheck}>{checking?'原生验证中…':'验证执行'}</button>
   {permit&&<section data-local-escape aria-label="确认执行验证" onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancelCheck()}}}><p>{permit.data.human_questions[0]} 当前配置：{profile.id} · {model} / {effort} · {mode}。只验证此配置，有效 5 分钟。</p><label>验证确认意见<input autoFocus aria-label="执行验证确认意见" value={answer} onChange={e=>setAnswer(e.target.value)}/></label><button type="button" disabled={!answer.trim()||checking} onClick={verify}>确认并验证执行</button><button type="button" onClick={cancelCheck}>{checking?'关闭验证视图（调用继续）':'取消验证'}</button></section>}
  </div>
  <div className="catalog-meta"><span role="status">{current?.refresh_pending?'另一个刷新正在进行，请稍后再读':current?.stale?'旧数据／未发现实时目录':'目录已获取'} · {current?.source||'尚未查询'}</span><small>更新：{current?.updated_at?new Date(current.updated_at*1000).toLocaleString('zh-CN'):'尚无成功查询'}</small>
  <small>目录不是调用成功证明。实际物理后端与生效 effort：unknown；以原生回合回执分别核对。</small></div>
 </details></section>
}
