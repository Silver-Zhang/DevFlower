import React, {useEffect, useRef, useState} from 'react';
import {Terminal as XTerm} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

const ACTIVE = new Set(['STARTING', 'RUNNING', 'RECOVERY_REQUIRED']);

export function NativePty({api, workspace, scope, catalog, refreshCatalog, onClose}) {
 const node = useRef(null);
 const xterm = useRef(null);
 const rowRef = useRef(null);
 const alive = useRef(true);
 const owner = useRef('browser:' + crypto.randomUUID().replaceAll('-', ''));
 const generation = useRef(null);
 const sequence = useRef(0);
 const queue = useRef(Promise.resolve());
 const cursor = useRef(0);
 const busyRef = useRef(false);
 const [row, setRow] = useState(null);
 const [choices, setChoices] = useState([]);
 const [error, setError] = useState('');
 const [command, setCommand] = useState('');
 const [busy, setBusy] = useState(false);
 const [model, setModel] = useState('');
 const [effort, setEffort] = useState('');
 const storageKey = scope ? 'df-native-pty:' + workspace.id + ':' + scope.handle : null;
 const [attempt, setAttempt] = useState(() => {
  try { return storageKey ? JSON.parse(sessionStorage.getItem(storageKey)) : null; }
  catch { return null; }
 });
 const models = catalog?.stale ? [] : catalog?.models || [];
 const efforts = (models.find(item => item.id === model)?.efforts || []).filter(value => value !== 'ultra');
 const request = (route, data) => api('native-pty/' + route, data);
 const owned = value => value?.state === 'RUNNING' && value.input_owner === 'workbench'
  && generation.current === value.input_generation;

 function project(value) {
  rowRef.current = value;
  if (xterm.current) xterm.current.options.disableStdin = !owned(value);
  setRow(value);
 }

 function select(value) {
  generation.current = null;
  cursor.current = 0;
  xterm.current?.reset();
  setCommand('');
  project(value);
 }

 async function inspect() {
  const value = await request('list?' + new URLSearchParams({workspace: workspace.id}));
  if (!alive.current) return;
  const active = value.terminals.filter(item => ACTIVE.has(item.state) && (!scope || item.handle === scope.handle));
  setChoices(active);
  if (!rowRef.current && active.length === 1) select(active[0]);
 }

 async function action(fn) {
  if (busyRef.current) return;
  busyRef.current = true;
  setBusy(true);
  setError('');
  try { await fn(); }
  catch (failure) { if (alive.current) setError(failure.message); }
  finally {
   busyRef.current = false;
   if (alive.current) setBusy(false);
  }
 }

 useEffect(() => {
  alive.current = true;
  const term = new XTerm({fontSize: 12, scrollback: 1500, disableStdin: true, allowProposedApi: false});
  const fit = new FitAddon();
  term.loadAddon(fit);
  term.open(node.current);
  xterm.current = term;
  const observer = new ResizeObserver(() => {
   if (!alive.current) return;
   fit.fit();
   const value = rowRef.current;
   if (owned(value)) request('resize', {...control(value), ...size(term)}).catch(() => {});
  });
  observer.observe(node.current);
  const input = term.onData(data => {
   const value = rowRef.current;
   if (!owned(value)) return;
   const captured = value.input_generation;
   const parts = Array.from(data);
   for (let i = 0; i < parts.length; i += 512) {
    const text = parts.slice(i, i + 512).join('');
    queue.current = queue.current.then(async () => {
     const current = rowRef.current;
     if (!alive.current || current?.id !== value.id || !owned(current) || generation.current !== captured) return;
     const next = sequence.current + 1;
     try {
      await request('input', {...control(current), sequence: next, text});
      if (generation.current === captured) sequence.current = next;
     } catch (failure) {
      if (alive.current && generation.current === captured) {
       generation.current = null;
       term.options.disableStdin = true;
       setError('按键结果未确认，请重新核对。' + failure.message);
      }
     }
    });
   }
  });
  inspect().catch(failure => { if (alive.current) setError(failure.message); });
  return () => {
   alive.current = false;
   const value = rowRef.current;
   if (owned(value)) request('release', control(value)).catch(() => {});
   generation.current = null;
   observer.disconnect();
   input.dispose();
   term.dispose();
   xterm.current = null;
  };
 }, []);

 useEffect(() => {
  if (!row?.id) return;
  const id = row.id;
  let mounted = true, pending = false;
  async function poll() {
   if (!mounted || pending) return;
   pending = true;
   try {
    const value = await request('output?' + new URLSearchParams({workspace: workspace.id, id, after: cursor.current}));
    if (!mounted || !alive.current || rowRef.current?.id !== id) return;
    if (value.reset) xterm.current?.reset();
    if (value.output) xterm.current?.write(value.output);
    cursor.current = value.next;
    project(value);
   } catch (failure) {
    if (mounted && alive.current) {
     generation.current = null;
     if (xterm.current) xterm.current.options.disableStdin = true;
     setError(failure.message);
    }
   } finally { pending = false; }
  }
  poll();
  const timer = setInterval(poll, 250);
  return () => { mounted = false; clearInterval(timer); };
 }, [row?.id]);

 function control(value) {
  return {workspace: workspace.id, id: value.id, owner: owner.current, generation: value.input_generation};
 }

 async function start() {
  const terminal = xterm.current;
  if (!terminal || !scope) return;
  const data = attempt || {workspace: workspace.id, handle: scope.handle, revision: scope.revision,
   intent_key: crypto.randomUUID(), model, effort, ...size(terminal)};
  const save = value => {
   if (storageKey) {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
   }
   if (alive.current) setAttempt(value);
  };
  save(data);
  try {
   const value = await request('start', data);
   save(null);
   if (alive.current) select(value);
  } catch (failure) {
   if (!failure.uncertain && failure.status && failure.status < 500) save(null);
   throw failure;
  }
 }

 async function claim() {
  const value = await request('claim', control(rowRef.current));
  if (!alive.current) return;
  generation.current = value.input_generation;
  sequence.current = value.input_sequence;
  project(value);
  xterm.current?.focus();
 }

 return <section className="native-pty" aria-label="原生终端">
  <header><h3>Codex 原生终端</h3><button onClick={onClose}>关闭终端视图</button></header>
  {!row && choices.length > 1 && <label className="field"><span>打开已有终端</span>
   <select aria-label="打开已有终端" defaultValue="" onChange={event => {
    const choice = choices.find(item => item.id === event.target.value);
    if (choice) select(choice);
   }}><option value="">选择已开的终端</option>{choices.map((item, i) =>
    <option key={item.id} value={item.id}>终端 {i + 1} · {item.state}</option>)}</select></label>}
  {scope && (!row || !ACTIVE.has(row.state)) && <div className="native-pty-actions">
   <label className="field"><span>终端模型</span><select aria-label="终端模型" value={model} disabled={!!attempt}
    onChange={event => {setModel(event.target.value); setEffort('');}}>
    <option value="">选择模型</option>{models.map(item => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
   </select></label>
   <label className="field"><span>终端强度</span><select aria-label="终端强度" value={effort} disabled={!!attempt}
    onChange={event => setEffort(event.target.value)}>
    <option value="">选择强度</option>{efforts.map(item => <option key={item}>{item}</option>)}
   </select></label>
   <button disabled={busy} onClick={() => action(refreshCatalog)}>刷新模型目录</button>
   <button className="primary" disabled={busy || (!attempt && (!model || !efforts.includes(effort)))}
    onClick={() => action(start)}>{attempt ? '核对上次启动' : '打开工作区读写终端'}</button>
  </div>}
  {scope && (!row || ['STARTING', 'RUNNING'].includes(row.state)) &&
   <p className="muted small">授权 Codex 读写当前工作区。关闭视图会保留终端，可重新连接。</p>}
  {!row && !scope && <p className="muted small">选择本工作空间的已有终端。</p>}
  {row && <p role="status" className="muted small">{row.reason || ({
   STARTING: '正在核验并启动终端…', RUNNING: owned(row) ? '此浏览器持有键盘' :
    row.input_owner === 'local-terminal' ? '本机连接持有键盘' :
    row.input_owner === 'workbench' ? '另一浏览器持有键盘' : '观察终端，接管后可输入',
   RECOVERY_REQUIRED: '终端结果待核验', COMPLETED: '终端已退出', FAILED: '终端未能启动', CANCELLED: '终端已停止'
  })[row.state]}</p>}
  <div ref={node} className="native-pty-screen" aria-label="终端输出"/>
  <div className="native-pty-actions">
   <button disabled={busy} onClick={() => action(inspect)}>查找已有终端</button>
   {row?.state === 'RUNNING' && <>
    <button disabled={busy || owned(row)} onClick={() => action(claim)}>接管键盘</button>
    <button disabled={busy} onClick={() => action(async () => {
     const value = await request('share', {workspace: workspace.id, id: row.id});
     if (alive.current) setCommand(value.command);
    })}>本机连接</button>
   </>}
   {['STARTING', 'RUNNING'].includes(row?.state) &&
    <button disabled={busy} onClick={() => action(() => request('stop', {workspace: workspace.id, id: row.id}))}>停止此终端</button>}
  </div>
  {command && <label className="field"><span>本机连接命令</span><input value={command} readOnly/></label>}
  {error && <p role="alert" className="error-text">{error}</p>}
 </section>;
}

function size(term) {
 return {cols: Math.max(20, Math.min(300, term.cols)), rows: Math.max(5, Math.min(100, term.rows))};
}
