import React, {useEffect, useRef, useState} from 'react';

const PROVIDERS = {codex: 'Codex', claude: 'Claude Code'};
const PENDING = new Set(['RESERVED', 'RUNNING']);

export function NativeComposer({api, workspace, providers, scope, catalog, refreshCatalog, onCompleted, onClose}) {
 const storageKey = 'devflower-v2-session-intent:' + workspace.id;
 const [intent, setIntent] = useState(() => sessionStorage.getItem(storageKey));
 const [provider, setProvider] = useState(scope?.provider || providers[0] || '');
 const [model, setModel] = useState('');
 const [effort, setEffort] = useState('');
 const [prompt, setPrompt] = useState('');
 const [working, setWorking] = useState(false);
 const [status, setStatus] = useState(null);
 const [error, setError] = useState('');
 const active = useRef(true);
 const request = useRef(false);
 const timer = useRef(null);
 const models = catalog?.stale ? [] : catalog?.models || [];
 const efforts = (models.find(row => row.id === model)?.efforts || []).filter(value => value !== 'ultra');

 useEffect(() => {
  active.current = true;
  if (intent) verify(intent);
  return () => { active.current = false; clearTimeout(timer.current); };
 }, []);

 function forget() {
  sessionStorage.removeItem(storageKey);
  setIntent(null);
 }

 function project(result) {
  if (!active.current) return;
  setStatus(result);
  if (PENDING.has(result.state)) {
   timer.current = setTimeout(() => verify(result.intent_key), 1000);
  } else if (result.state === 'COMPLETED') {
   setPrompt('');
   forget();
   onCompleted(result.handle);
  } else if (result.state === 'NOT_SENT') {
   forget();
  }
 }

 async function verify(key) {
  if (request.current || !active.current) return;
  request.current = true;
  setWorking(true);
  setError('');
  try {
   const result = await api('session-operation?' + new URLSearchParams({workspace: workspace.id, intent_key: key}));
   if (result.workspace === workspace.id && result.intent_key === key) project(result);
  } catch (failure) {
   if (active.current) setError('会话结果待核对。' + failure.message);
  } finally {
   request.current = false;
   if (active.current) setWorking(false);
  }
 }

 async function send(event) {
  event.preventDefault();
  if (request.current || intent || sessionStorage.getItem(storageKey)) return;
  request.current = true;
  const key = crypto.randomUUID();
  sessionStorage.setItem(storageKey, key);
  setIntent(key);
  setWorking(true);
  setStatus(null);
  setError('');
  try {
   const result = await api('session-send', {workspace: workspace.id, provider,
    handle: scope?.handle || null, revision: scope?.revision || null, intent_key: key, prompt,
    ...(provider === 'codex' ? {model, effort} : {})});
   if (result.workspace === workspace.id && result.intent_key === key) project(result);
  } catch (failure) {
   if (active.current) {
    if (failure.status && failure.status < 500) forget();
    setError(failure.message + (failure.uncertain ? ' 请核验本次发送。' : ''));
   }
  } finally {
   request.current = false;
   if (active.current) setWorking(false);
  }
 }

 async function refreshModels() {
  setWorking(true);
  setError('');
  try { await refreshCatalog(); }
  catch (failure) { if (active.current) setError(failure.message); }
  finally { if (active.current) setWorking(false); }
 }

 return <section className="native-composer" aria-label={scope ? '继续原生会话' : '新建原生会话'}>
  <header><h3>{scope ? '继续此原生会话' : '新建独立会话'}</h3><button onClick={onClose}>关闭输入</button></header>
  <form onSubmit={send}>
   {!scope && <label className="field"><span>会话 Provider</span>
    <select aria-label="会话 Provider" value={provider} onChange={event => setProvider(event.target.value)} disabled={working || !!intent}>
     {providers.map(id => <option value={id} key={id}>{PROVIDERS[id]}</option>)}
    </select></label>}
   {provider === 'codex' && <>
    <div className="native-composer-models">
     <label className="field"><span>请求模型</span><select aria-label="请求模型" value={model} disabled={working || !!intent}
      onChange={event => { setModel(event.target.value); setEffort(''); }}>
      <option value="">选择已发现模型</option>
      {models.map(row => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}
     </select></label>
     <label className="field"><span>请求强度</span><select aria-label="请求强度" value={effort} disabled={working || !!intent}
      onChange={event => setEffort(event.target.value)}>
      <option value="">选择强度</option>{efforts.map(value => <option key={value}>{value}</option>)}
     </select></label>
    </div>
    <button type="button" disabled={working || !!intent} onClick={refreshModels}>刷新 Codex 模型目录</button>
   </>}
   <p className="muted small">{scope ? provider === 'claude' ? '发送前核验受管历史，并在 DevFlower 租约内继续原会话。' :
    '发送时由 Codex 对原会话加锁并继续最新原生记录。' :
    '发送会在启动时批准的本机配置下创建会话，并使用当前工作空间作为原目录。'}本入口为纯对话。</p>
   <label className="field"><span>消息</span><textarea aria-label="消息" rows={4} maxLength={20000} value={prompt} disabled={!!intent}
    onChange={event => setPrompt(event.target.value)}/></label>
   <div className="native-composer-actions">
    <button type="submit" className="primary" disabled={working || !!intent || !prompt.trim() ||
     provider === 'codex' && (!efforts.includes(effort) || !model)}>{scope ? '发送到此会话' : '新建并发送'}</button>
    {intent && <button type="button" disabled={working} onClick={() => verify(intent)}>核验本次发送</button>}
    {intent && ['RECOVERY_REQUIRED', 'UNREGISTERED'].includes(status?.state) &&
     <button type="button" onClick={() => { setPrompt(''); forget(); }}>结束查询</button>}
   </div>
  </form>
  {error && <p role="alert" className="error-text">{error}</p>}
  {status && <p role="status" className="muted small">{status.reason || ({
   RESERVED: '已登记本次发送。', RUNNING: '原生回合执行中…',
   COMPLETED: '原生回执已核验。', UNREGISTERED: '当前服务未找到此发送记录。'
  })[status.state]}</p>}
 </section>;
}
