import React,{useEffect,useRef,useState} from 'react';
import {downloadFile} from './HistoryArchives.jsx';

const deliveryStatus={COMPLETED:'已核验 Git 交付',RECOVERY_REQUIRED:'Git 交付待核验',
 UNREGISTERED:'本机尚无该意图记录',FAILED_BEFORE_PUBLISH:'分支登记前交付失败'};

export function LocalDelivery({api,workspace,run}){
 const [value,setValue]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const storageKey='devflower-v2-git-delivery:'+workspace+':'+run;
 const [target,setTarget]=useState(''),[intent,setIntent]=useState(()=>sessionStorage.getItem(storageKey));
 const [receipt,setReceipt]=useState(null);
 const dialog=useRef(null),live=useRef(true);
 useEffect(()=>()=>{live.current=false},[]);
 useEffect(()=>{if(value&&dialog.current&&!dialog.current.open)dialog.current.showModal()},[value]);
 async function preview(){
  setBusy(true);setError('');
  try{
   const result=await api('local-delivery/preview?'+new URLSearchParams({workspace,run}));
   if(!live.current)return;
   if(result.state!=='LOCAL_GATE_ACCEPTED'||result.run!==run)throw Error(result.reason);
   setValue(result);
  }catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 function close(){setValue(null);setError('')}
 function record(result){
  setReceipt(result);
  if(['COMPLETED','UNREGISTERED','FAILED_BEFORE_PUBLISH'].includes(result.state)){
   sessionStorage.removeItem(storageKey);setIntent(null);
  }
 }
 async function deliver(){
  if(busy||!value||!target||intent)return;
  setBusy(true);setError('');
  const id=crypto.randomUUID();
  sessionStorage.setItem(storageKey,id);setIntent(id);
  try{
   const result=await api('local-delivery/git',{workspace,run,activation:value.gate_activation,
    candidate_sha256:value.gate.candidate_sha256,target,intent_key:id,authorization:'confirm-local-git-delivery'});
   if(live.current)record(result);
  }catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 async function readDelivery(){
  setBusy(true);setError('');
  try{
   const result=await api('local-delivery/git-operation?'+new URLSearchParams({workspace,intent_key:intent}));
   if(live.current)record(result);
  }catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 async function confirm(){
  if(busy||!value)return;
  setBusy(true);setError('');
  try{
   const bytes=await api('local-delivery',{workspace,run,activation:value.gate_activation,
    candidate_sha256:value.gate.candidate_sha256,authorization:'confirm-local-candidate-download'},{binary:true});
   if(live.current){downloadFile(bytes,'devflower-candidate-'+value.gate.candidate_sha256.slice(0,12)+'.zip','application/zip');close()}
  }catch(failure){if(live.current)setError(failure.message)}
  finally{if(live.current)setBusy(false)}
 }
 return <>
  <button disabled={busy} onClick={preview}>本机交付包</button>
  {!value&&intent&&<button disabled={busy} onClick={readDelivery}>核对 Git 交付</button>}
  {!value&&receipt&&<small role="status">{deliveryStatus[receipt.state]} {receipt.commit&&<code>{receipt.commit}</code>}</small>}
  {error&&!value&&<small role="alert" className="error-text">{error}</small>}
  {value&&<dialog ref={dialog} className="dialog local-delivery" aria-label="确认下载本机交付包"
   onCancel={event=>{event.preventDefault();if(!busy)close()}}
   onKeyDown={event=>{if(event.key==='Escape')event.stopPropagation()}}>
   <h2>下载本机交付包</h2>
   <p>将独立 Gate 已接受的候选源码打包。提交和远端分支保持原状态。</p>
   <dl><dt>候选 SHA-256</dt><dd>{value.gate.candidate_sha256}</dd>
    <dt>Git HEAD</dt><dd>{value.gate.git_head}</dd>
    <dt>Gate 激活</dt><dd>{value.gate_activation}</dd></dl>
   {value.git_targets?.length>0&&<label className="field"><span>已登记的裸 Git 交付目标</span>
    <select value={target} onChange={event=>setTarget(event.target.value)} disabled={busy||!!intent}>
     <option value="">选择 Git 仓库</option>
     {value.git_targets.map(name=><option key={name} value={name}>{name}</option>)}
    </select></label>}
   {target&&<p className="field-note">新工作分支 <code>{value.git_ref}</code></p>}
   {receipt&&<p role="status" className="field-note">{deliveryStatus[receipt.state]} {receipt.commit&&<code>{receipt.commit}</code>}</p>}
   {error&&<p role="alert" className="error-text">{error}</p>}
   <footer><button autoFocus disabled={busy} onClick={close}>取消</button>
    {intent?<button disabled={busy} onClick={readDelivery}>核对 Git 交付</button>:
     target&&<button disabled={busy||receipt?.state==='COMPLETED'} onClick={deliver}>确认交付到裸 Git</button>}
    <button className="primary" disabled={busy} onClick={confirm}>{busy?'正在核验…':'确认下载 ZIP'}</button></footer>
  </dialog>}
 </>;
}
