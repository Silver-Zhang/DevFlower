import React, {useEffect, useRef, useState} from 'react';
import {X} from 'lucide-react';

const PATH = '/api/v2/provider-catalog/copilot';
const UNAVAILABLE = 'Copilot 模型目录暂不可用，请核对配置';
const STALE = '模型目录已过期，请手动刷新';
const AUTH = new Map([['unknown','未核验'],['unauthenticated','未登录'],['authenticated','已核验']]);
const AVAILABILITY = new Map([['unconfigured','未配置'],['unavailable','暂不可用'],['available','可用']]);
const PROFILE = /^[0-9a-f]{16,48}$/;

function asStale(value, error) {
 return {...value, fresh:false, stale:Boolean(value?.models?.length), auth_status:'unknown',
  availability:'unavailable', error};
}

export function ProviderCatalog({token, isCurrent, onClose}) {
 const [result, setResult] = useState(null);
 const [waiting, setWaiting] = useState(true);
 const operation = useRef(null);
 const closeButton = useRef(null);

 async function read(profile) {
  if (operation.current || !isCurrent()) return;
  const ticket = {};
  operation.current = ticket;
  setWaiting(true);
  const current = () => operation.current === ticket && isCurrent();
  try {
   const headers = {Authorization:'Bearer ' + token};
   const options = profile ? {method:'POST', headers:{...headers,'Content-Type':'application/json'},
    body:JSON.stringify({profile_id:profile})} : {headers};
   const response = await fetch(PATH, options);
   const value = await response.json();
   if (!current()) return;
   if (value.provider !== 'copilot' || !Array.isArray(value.models) ||
       !(value.profile_id === null || (typeof value.profile_id === 'string' && PROFILE.test(value.profile_id)))) throw Error();
   if (profile && value.profile_id !== profile) {
    setResult(asStale(null, value.profile_id === null && value.code === 'COPILOT_PROFILE_UNKNOWN' ?
      '此 Copilot 配置当前不可用，请核对启动配置' : UNAVAILABLE));
   } else setResult(value);
  } catch {
   if (current()) setResult(old => asStale(old, UNAVAILABLE));
  } finally {
   if (current()) {
    operation.current = null;
    setWaiting(false);
   }
  }
 }

 useEffect(() => {
  closeButton.current?.focus();
  read();
  return () => {operation.current = null};
 }, []);
 useEffect(() => {
  if (!result?.fresh || !Number.isFinite(result.observed_at)) return;
  const timer = setTimeout(() => {
   if (isCurrent()) setResult(old => old === result ? asStale(old, STALE) : old);
  }, Math.max(0, Math.min(300000, 300000 + result.observed_at * 1000 - Date.now())));
  return () => clearTimeout(timer);
 }, [result]);

 const profile = result?.profile_id;
 const canRefresh = typeof profile === 'string' && PROFILE.test(profile) && result.code !== 'COPILOT_IDENTITY_CHANGED';
 const observed = Number.isFinite(result?.observed_at) ? new Date(result.observed_at * 1000) : null;
 const time = observed && Number.isFinite(observed.valueOf()) ? observed.toLocaleString() : '尚未查询';
 return <aside className="project-panel provider-catalog-panel" aria-label="Provider 模型目录">
  <header className="project-panel-header">
   <div><small>仅目录查询</small><h2>Provider 模型目录</h2></div>
   <button ref={closeButton} aria-label="关闭 Provider 模型目录" onClick={onClose}><X size={17}/></button>
  </header>
  <div className="provider-catalog-content">
   <h3>GitHub Copilot</h3>
   <dl>
    <dt>配置</dt><dd>{AVAILABILITY.get(result?.availability) || '正在读取'}</dd>
    <dt>认证</dt><dd>{AUTH.get(result?.auth_status) || '未核验'}</dd>
    <dt>来源</dt><dd>{result?.source === 'copilot-cli-models-list' ? 'Copilot CLI · models.list' : '尚无目录'}</dd>
    <dt>观察时间</dt><dd>{time}</dd>
    <dt>推理强度</dt><dd>未知</dd>
   </dl>
   <button disabled={waiting || !canRefresh} onClick={()=>read(profile)}>刷新 GitHub Copilot 目录</button>
   {waiting&&<p role="status">正在读取目录状态…</p>}
   {result?.stale&&<p className="field-note">下列目录为上次观察，已过期或当前未核验。</p>}
   {result?.error&&<p role="status" className="field-note">{result.error}</p>}
   {Boolean(result?.models?.length)&&<ul aria-label="Copilot 模型">
    {result.models.map(row => <li key={row.id}><code>{row.id}</code>{row.name&&row.name!==row.id&&<span>{row.name}</span>}</li>)}
   </ul>}
  </div>
 </aside>;
}
