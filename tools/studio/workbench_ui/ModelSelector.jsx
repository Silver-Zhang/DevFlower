import React,{useEffect,useRef,useState} from 'react';

// The server selects approved endpoints and credentials. No browser-supplied URL.
export function ModelSelector({api,profile,model,effort,onChange,onCatalog,disabled=false,workspace,mode='Ask',permission,tools,timeout=300,context,onSelecting}){
 const [catalog,setCatalog]=useState(profile),[pending,setPending]=useState(false),[error,setError]=useState('');
 const version=useRef(0);
 const [choosing,setChoosing]=useState(null);const pickVersion=useRef(0),pickOwner=useRef('');pickOwner.current=profile?.id+':'+workspace?.id;
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
  if(v===version.current&&!refresh&&(!result.attempted_at||result.stale))result=await api('/api/models',{profile:profile.id,refresh:true,workspace:workspace?.id});
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
  <details className="model-connection-details"><summary>模型目录详情</summary><p>模型直接来自当前工具或账号目录；选择即可使用。调用失败会显示真实错误，模型选择不会改变工作区权限。</p>
  <div className="catalog-meta"><span role="status">{current?.refresh_pending?'另一个刷新正在进行，请稍后再读':current?.stale?'旧数据／未发现实时目录':'目录已获取'} · {current?.source||'尚未查询'}</span><small>更新：{current?.updated_at?new Date(current.updated_at*1000).toLocaleString('zh-CN'):'尚无成功查询'}</small>
  <small>目录不是调用成功证明。实际物理后端与生效 effort：unknown；以原生回合回执分别核对。</small></div>
 </details></section>
}
