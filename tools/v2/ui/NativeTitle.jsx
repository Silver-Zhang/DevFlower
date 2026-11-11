import React,{useRef,useState} from 'react';

export function NativeTitle({api,workspace,handle,revision}) {
 const scope={workspace,handle,revision};
 const key='devflower-v2-native-title:'+workspace+':'+handle;
 const [current,setCurrent]=useState(null),[title,setTitle]=useState('');
 const [intent,setIntent]=useState(()=>sessionStorage.getItem(key));
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const flight=useRef(false);
 async function action(fn) {
  if(flight.current)return;
  flight.current=true;setBusy(true);setError('');
  try {await fn()} catch(failure) {setError(failure.message)}
  finally {flight.current=false;setBusy(false)}
 }
 function clear() {
  setIntent(null);
  sessionStorage.removeItem(key);
 }
 function result(value) {
  if(value.state==='COMPLETED'){
   setCurrent(value);setTitle(value.native_title||'');setNotice('原生标题已核验。');clear();
  } else if(value.state==='UNREGISTERED'){
   setNotice('本机未登记这次标题修改。');clear();
  } else {
   setNotice(value.reason||'标题修改结果待核验。');
  }
 }
 async function read() {
  const value=await api('session-title?'+new URLSearchParams(scope));
  setCurrent(value);setTitle(value.native_title||'');
 }
 async function rename(event) {
  event.preventDefault();
  if(intent||!current)return;
  const id=crypto.randomUUID();
  setIntent(id);sessionStorage.setItem(key,id);
  try {
   result(await api('session-title',{...scope,intent_key:id,title,expected_title_revision:current.title_revision}));
  } catch(failure) {
   if(failure.status&&failure.status<500&&!failure.uncertain)clear();
   throw failure;
  }
 }
 return <details className="native-title" aria-label="Codex 原生标题">
  <summary>Codex 原生标题</summary>
  <button disabled={busy} onClick={()=>action(read)}>读取当前原生标题</button>
  {current&&<form onSubmit={event=>action(()=>rename(event))}>
   <label className="field"><span>原生标题</span><input maxLength={160} value={title} disabled={busy||!!intent}
    onChange={event=>setTitle(event.target.value)}/></label>
   <button className="primary" type="submit" disabled={busy||!!intent||!title.trim()||title.trim()===current.native_title}>
    修改 Codex 原生标题</button>
  </form>}
  {intent&&<button disabled={busy} onClick={()=>action(async()=>{
   const value=await api('session-title-operation?'+new URLSearchParams({workspace,intent_key:intent}));
   result(value);
  })}>核验本次标题修改</button>}
  {notice&&<p role="status" className="field-note">{notice}</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
 </details>;
}
