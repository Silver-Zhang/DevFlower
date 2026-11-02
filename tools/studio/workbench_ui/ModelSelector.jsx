import React,{useEffect,useRef,useState} from 'react';

const EFFORTS=['default','low','medium','high','xhigh','max'];

// The server selects approved endpoints and credentials. No browser-supplied URL.
export function ModelSelector({api,profile,model,effort,onChange,onCatalog,onSwitchToClaude,disabled=false,workspace,mode='Ask',permission,tools,timeout=300,context,onSelecting}){
 const [catalog,setCatalog]=useState(profile),[pending,setPending]=useState(false),[error,setError]=useState('');
 const [manualModel,setManualModel]=useState(''),[manualEffort,setManualEffort]=useState(effort||'high'),[manualNotice,setManualNotice]=useState(''),[manualBusy,setManualBusy]=useState(false);
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
 // The list came from the pre-registered policy, not from a live directory.
 const degraded=Boolean(error||current?.error||current?.source==='预登记配置（不是完整模型列表）');
 // The stored reason is the actionable launcher text from native_codex_image.
 const launcherBlocked=/静态原生二进制|启动器/.test(String(error||current?.error||''));
 async function saveManual(){
  if(manualBusy||!workspace?.id||!manualModel.trim())return;
  setManualBusy(true);setManualNotice('');
  try{
   const v=await api('/api/model-registration/manual',{workspace:workspace.id,profile:profile.id,model:manualModel.trim(),effort:manualEffort});
   choose({model:v.model,effort:v.effort});
   setManualNotice('已保存到本工作区：「'+v.model+' / '+v.effort+'」。目录不可用时属手工登记，不代表模型存在或已授权；执行前仍会做真实连接检查。');
   setManualModel('');
   await load(true);
  }catch(e){setManualNotice('保存失败：'+e.message)}finally{setManualBusy(false)}
 }
 return <section className="model-picker" aria-label="模型发现">
  <label className="field"><span>模型</span><select aria-label="模型" disabled={disabled||pending} value={model||''} onChange={e=>{const m=models.find(x=>x.id===e.target.value);choose({model:m.id,effort:reasoning(m).includes(effort)?effort:reasoning(m)[0]||''})}}>
   {!selected&&<option value={model||''}>{model||'请选择模型'}（目录尚未确认）</option>}
   {models.map(m=><option key={m.id} value={m.id} >{m.name||m.id}</option>)}
  </select></label>
  <label className="field"><span>推理强度</span><select aria-label="推理强度" disabled={disabled||pending} value={effort||''} onChange={e=>choose({model,effort:e.target.value})}>{!efforts.includes(effort)&&<option value={effort||''}>{effort||'unknown'}</option>}{efforts.map(x=><option key={x}>{x}</option>)}</select><small className="hint">低更省 token；高强度更慢，按任务选择。</small></label>
  <button type="button" className="refresh-models" aria-busy={pending} disabled={pending||disabled} onClick={()=>load(true)}>刷新模型</button>
  {pending&&<small role="status" aria-label="模型读取状态">正在读取模型目录…</small>}
  {(error||current?.error)&&<p role="alert">{error||current.error}{launcherBlocked&&onSwitchToClaude&&!disabled&&<button type="button" className="switch-provider" onClick={onSwitchToClaude}>改用 Claude Code</button>}</p>}
  <details className="model-connection-details"><summary>模型目录详情</summary><p>模型直接来自当前工具或账号目录；选择即可使用。调用失败会显示真实错误，模型选择不会改变工作区权限。</p>
  <div className="catalog-meta"><span role="status">{current?.refresh_pending?'另一个刷新正在进行，请稍后再读':current?.stale?'旧数据／未发现实时目录':'目录已获取'} · {current?.source||'尚未查询'}</span><small>更新：{current?.updated_at?new Date(current.updated_at*1000).toLocaleString('zh-CN'):'尚无成功查询'}</small>
  {current?.error&&<small role="note">为什么退回预登记：{current.error}</small>}
  {degraded&&<small role="note">此时列表只含明确登记的模型与强度，不是完整目录；修复上面的原因后点「刷新模型」即可看到真实模型与可用强度档位。</small>}
  <small>目录不是调用成功证明。实际物理后端与生效 effort：unknown；以原生回合回执分别核对。</small></div>
  {degraded&&workspace?.id&&<div className="manual-model" role="group" aria-label="手动登记模型">
   <p>目录不可用时，可以手动登记一个你确认存在的模型；它只加入本工作区的可选列表，不授予执行权限。</p>
   <label className="field"><span>模型ID</span><input aria-label="手动模型ID" value={manualModel} placeholder="例如 gpt-5.4" onChange={e=>setManualModel(e.target.value)}/></label>
   <label className="field"><span>推理强度</span><select aria-label="手动推理强度" value={manualEffort} onChange={e=>setManualEffort(e.target.value)}>{EFFORTS.map(v=><option key={v}>{v}</option>)}</select></label>
   <button type="button" disabled={manualBusy||!manualModel.trim()} onClick={saveManual}>{manualBusy?'保存中…':'保存到本工作区'}</button>
   {manualNotice&&<small role="status">{manualNotice}</small>}
  </div>}
  </details></section>;
}
