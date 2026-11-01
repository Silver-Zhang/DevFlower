import React,{useEffect,useRef,useState} from 'react';

// The server selects approved endpoints and credentials. No browser-supplied URL.
export function ModelSelector({api,profile,model,effort,onChange,onCatalog,disabled=false,workspace,mode='Ask',permission,tools,timeout=300,context,onSelecting}){
 const [catalog,setCatalog]=useState(profile),[pending,setPending]=useState(false),[error,setError]=useState('');
 const version=useRef(0);
 // Selection changes only the bound draft synchronously. Start/send validates
 // the discovered model and current configuration on the server.
 function choose(values){setError('');onChange(values)}
 async function load(refresh=false){const v=++version.current;setPending(true);setError('');try{
  let result=await api('/api/models',{profile:profile.id,refresh,workspace:workspace?.id});
  if(v===version.current&&!refresh&&(!result.attempted_at||result.stale))result=await api('/api/models',{profile:profile.id,refresh:true,workspace:workspace?.id});
  if(v!==version.current)return;setCatalog(result);onCatalog?.(result);
 }catch(e){if(v===version.current)setError(e.message)}finally{if(v===version.current)setPending(false)}}
 useEffect(()=>{setCatalog(profile);if(profile?.id)load(false);return()=>{version.current++}},[profile?.id,workspace?.id]);
 const current=catalog?.id===profile?.id?catalog:profile;
 // A draft can carry a node whose execution tool is missing or not configured;
 // show the repair path instead of querying a profile that does not exist.
 if(!profile?.id)return <section className="model-picker" aria-label="模型发现"><p className="hint">尚未配置执行工具：请先在「执行工具」中选择可用工具，再选择模型与推理强度。</p></section>;
 const models=current?.models||[],selected=models.find(m=>m.id===model);
 const reasoning=m=>m?.selectable_efforts?.length?m.selectable_efforts:m?.native_efforts?.length?m.native_efforts:m?.efforts?.length?m.efforts:[profile?.effort].filter(Boolean);
 const efforts=reasoning(selected);
 return <section className="model-picker" aria-label="模型发现">
  <label className="field"><span>模型</span><select aria-label="模型" disabled={disabled||pending} value={model||''} onChange={e=>{const m=models.find(x=>x.id===e.target.value);choose({model:m.id,effort:reasoning(m).includes(effort)?effort:reasoning(m)[0]||''})}}>
   {!selected&&<option value={model||''}>{model||'请选择模型'}（目录尚未确认）</option>}
   {models.map(m=><option key={m.id} value={m.id} >{m.name||m.id}</option>)}
  </select></label>
  <label className="field"><span>推理强度</span><select aria-label="推理强度" disabled={disabled||pending} value={effort||''} onChange={e=>choose({model,effort:e.target.value})}>{!efforts.includes(effort)&&<option value={effort||''}>{effort||'unknown'}</option>}{efforts.map(x=><option key={x}>{x}</option>)}</select></label>
  <button type="button" className="refresh-models" aria-busy={pending} disabled={pending||disabled} onClick={()=>load(true)}>刷新模型</button>
  {pending&&<small role="status" aria-label="模型读取状态">正在读取模型目录…</small>}
  {(error||current?.error)&&<p role="alert">{error||current.error}</p>}
  <details className="model-connection-details"><summary>模型目录详情</summary><p>模型直接来自当前工具或账号目录；选择即可使用。调用失败会显示真实错误，模型选择不会改变工作区权限。</p>
  <div className="catalog-meta"><span role="status">{current?.refresh_pending?'另一个刷新正在进行，请稍后再读':current?.stale?'旧数据／未发现实时目录':'目录已获取'} · {current?.source||'尚未查询'}</span><small>更新：{current?.updated_at?new Date(current.updated_at*1000).toLocaleString('zh-CN'):'尚无成功查询'}</small>
  <small>目录不是调用成功证明。实际物理后端与生效 effort：unknown；以原生回合回执分别核对。</small></div>
 </details></section>
}
