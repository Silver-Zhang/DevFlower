import React, {useEffect, useRef, useState} from 'react';
import {ArrowLeft, MessageSquareText, RefreshCw, X} from 'lucide-react';

const PROVIDERS = {codex: 'Codex', claude: 'Claude Code'};
const COVERAGE = {
 source_unavailable: '部分已登记来源不可读或已变化',
 source_alias: '检测到不安全的来源别名',
 source_budget: '会话来源数量超过预算',
 nested_unavailable: '部分项目子目录无法安全检查',
 nested_depth_budget: '项目嵌套发现达到深度上限',
 entry_budget: '目录项数量达到上限',
 file_budget: '会话文件数量达到上限',
 byte_budget: '读取字节数达到上限',
 time_budget: '扫描达到时间上限',
 unsupported_layout: '部分记录格式暂未支持',
 transcript_unavailable: '部分记录不可读取、已变化或超过单文件预算',
 cwd_unverified: '部分记录的原工作目录无法核实',
 identity_conflict: '检测到同一来源内的会话身份冲突',
 no_sources: '尚未登记可扫描的来源',
};
const ACCESS = {readable: '只读', unverified: '归属或覆盖待核验', conflict: '会话身份冲突'};

function sessionTime(value) {
 if (!value) return '时间未知';
 const date = new Date(value);
 return Number.isFinite(date.getTime()) ? date.toLocaleString() : '时间未知';
}

export function NativeSessions({api, workspace, enabled, isCurrent, onClose}) {
 const [list, setList] = useState(null);
 const [reading, setReading] = useState(null);
 const [loading, setLoading] = useState(enabled);
 const [listError, setListError] = useState('');
 const [provider, setProvider] = useState('');
 const [query, setQuery] = useState('');
 const listGeneration = useRef(0);
 const selectionGeneration = useRef(0);
 const closeButton = useRef(null);
 const backButton = useRef(null);
 const opener = useRef(null);

 async function refresh() {
  if (!enabled) return;
  const generation = ++listGeneration.current;
  selectionGeneration.current++;
  setLoading(true);
  setList(null);
  setReading(null);
  setListError('');
  setQuery('');
  try {
   const result = await api('native-sessions?' + new URLSearchParams({workspace: workspace.id}));
   if (isCurrent() && generation === listGeneration.current && result.workspace === workspace.id) setList(result);
  } catch (error) {
   if (isCurrent() && generation === listGeneration.current) setListError(error.message);
  } finally {
   if (isCurrent() && generation === listGeneration.current) setLoading(false);
  }
 }
 useEffect(() => {
  closeButton.current?.focus();
  refresh();
  return () => {
   listGeneration.current++;
   selectionGeneration.current++;
  };
 }, []);

 async function choose(row, button) {
  if (!list || row.access !== 'readable') return;
  const generation = ++selectionGeneration.current;
  const {revision} = list;
  const current = () => isCurrent() && generation === selectionGeneration.current;
  opener.current = button;
  setQuery('');
  setReading({row, loading: true});
  try {
   const result = await api('native-session?' + new URLSearchParams({
    workspace: workspace.id, revision, handle: row.handle
   }));
   if (current() && result.workspace === workspace.id && result.revision === revision && result.handle === row.handle) {
    setReading({row, result});
   }
  } catch (error) {
   if (current()) setReading({row, error: error.message});
  }
 }
 useEffect(() => {
  if (reading?.row.handle) backButton.current?.focus();
 }, [reading?.row.handle]);

 function back() {
  selectionGeneration.current++;
  setReading(null);
  requestAnimationFrame(() => {
   if (!isCurrent()) return;
   if (opener.current?.getClientRects().length) opener.current.focus();
   else closeButton.current?.focus();
  });
 }

 const filtered = list?.sessions.filter(row => !provider || provider === row.provider) || [];
 const messages = reading?.result?.messages || [];
 const normalized = query.trim().toLocaleLowerCase();
 const visible = messages.filter(message => !normalized || message.text.toLocaleLowerCase().includes(normalized));
 return <aside className={'project-panel native-panel' + (reading ? ' has-preview' : '')} aria-label="原生会话"
  onKeyDown={event => {
   if (event.key === 'Escape' && reading) {
    event.stopPropagation();
    back();
   }
  }}>
  <header className="project-panel-header">
   <div><h2>原生会话</h2><p className="muted small">{workspace.name} · 只读 · 占用状态未核实</p></div>
   <button aria-label="刷新原生会话" title="刷新原生会话" disabled={!enabled || loading} onClick={refresh}><RefreshCw size={16}/></button>
   <button ref={closeButton} aria-label="关闭原生会话" title="关闭原生会话" onClick={onClose}><X size={16}/></button>
  </header>
  <div className="project-panel-content">
   <nav className="project-tree native-list" aria-label="会话列表">
    {!enabled && <p className="muted">本机尚未启用原生会话只读浏览。</p>}
    {loading && <p className="muted" role="status">正在检查已批准的会话来源…</p>}
    {listError && <p className="error-text" role="alert">{listError}</p>}
    {list && <>
     <p className="native-coverage" role="status">
      {list.coverage.state === 'complete' ? '已检查登记范围' : list.coverage.state === 'partial' ? '扫描覆盖不完整' : '暂时没有可用来源'}
     </p>
     {list.coverage.reasons.length > 0 && <ul className="native-coverage-reasons">
      {list.coverage.reasons.map(reason => <li key={reason}>{COVERAGE[reason] || '部分来源未核实'}</li>)}
     </ul>}
     <label className="field"><span>Provider</span><select aria-label="筛选会话 Provider" value={provider} onChange={event => setProvider(event.target.value)}>
      <option value="">全部</option>{Object.entries(PROVIDERS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
     </select></label>
     {!filtered.length && <p className="muted">当前工作空间没有匹配的原生会话。</p>}
     {Object.entries(PROVIDERS).map(([id, name]) => {
      const group = filtered.filter(row => row.provider === id);
      return group.length > 0 && <section key={id} aria-label={name}>
       <h3>{name}</h3>
       <ul>{group.map((row, index) => <li key={row.handle}>
        <button className="native-session-item" aria-pressed={reading?.row.handle === row.handle}
         disabled={row.access !== 'readable'} onClick={event => choose(row, event.currentTarget)}>
         <MessageSquareText size={15}/>
         <span>{name} 会话 {index + 1}<small>{row.source_labels.join(' · ')} · {sessionTime(row.updated_at)}</small>
          <small>{ACCESS[row.access]}</small></span>
        </button>
       </li>)}</ul>
      </section>;
     })}
    </>}
   </nav>
   {reading && <section className="project-preview native-window" aria-label="会话正文">
    <header><button ref={backButton} aria-label="返回会话列表" title="返回会话列表" onClick={back}><ArrowLeft size={16}/></button>
     <h3>{PROVIDERS[reading.row.provider]} · 只读</h3></header>
    {reading.loading && <p className="muted" role="status">正在读取已核验的会话…</p>}
    {reading.error && <p className="error-text" role="alert">{reading.error}</p>}
    {reading.result && <>
     <div className="native-window-controls">
      <label className="field"><span>搜索当前可见窗口</span><input type="search" maxLength={200} value={query}
       onChange={event => setQuery(event.target.value)}/></label>
      <p className="muted small">{reading.result.truncated ? '当前内容已截断。' : ''}搜索仅匹配此处已加载的可见消息。</p>
     </div>
     <div aria-label="已加载消息" className="native-messages">
      {visible.length === 0 && <p className="muted small">{normalized ? '当前窗口没有匹配内容。' : '此会话没有可显示的消息。'}</p>}
      {visible.map((message, index) => <article key={index}>
       <h3>{message.role === 'user' ? '用户' : '助手'}</h3><pre>{message.text}</pre>
      </article>)}
     </div>
    </>}
   </section>}
  </div>
 </aside>;
}
