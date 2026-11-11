import React,{useEffect,useRef,useState} from 'react';
import {downloadFile} from './HistoryArchives.jsx';

export function LocalDelivery({api,workspace,run}){
 const [value,setValue]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
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
  {error&&!value&&<small role="alert" className="error-text">{error}</small>}
  {value&&<dialog ref={dialog} className="dialog local-delivery" aria-label="确认下载本机交付包"
   onCancel={event=>{event.preventDefault();if(!busy)close()}}
   onKeyDown={event=>{if(event.key==='Escape')event.stopPropagation()}}>
   <h2>下载本机交付包</h2>
   <p>将独立 Gate 已接受的候选源码打包。提交和远端分支保持原状态。</p>
   <dl><dt>候选 SHA-256</dt><dd>{value.gate.candidate_sha256}</dd>
    <dt>Git HEAD</dt><dd>{value.gate.git_head}</dd>
    <dt>Gate 激活</dt><dd>{value.gate_activation}</dd></dl>
   {error&&<p role="alert" className="error-text">{error}</p>}
   <footer><button autoFocus disabled={busy} onClick={close}>取消</button>
    <button className="primary" disabled={busy} onClick={confirm}>{busy?'正在核验…':'确认下载 ZIP'}</button></footer>
  </dialog>}
 </>;
}
