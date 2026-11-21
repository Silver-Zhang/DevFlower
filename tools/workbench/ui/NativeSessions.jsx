import React, {useEffect, useImperativeHandle, useRef, useState} from 'react';
import {ArrowLeft, MessageSquareText, RefreshCw, X} from 'lucide-react';
import {VisibleHistory} from './VisibleHistory.jsx';
import {NativeActivity} from './NativeActivity.jsx';
import {NativeMessages, isVisibleMessage} from './NativeMessages.jsx';
import {NativeComposer} from './NativeComposer.jsx';
import {NativePty} from './NativePty.jsx';
import {NativeTitle} from './NativeTitle.jsx';
import {PanelEmpty} from './WorkbenchFeedback.jsx';
import {groupKeyFor} from '../../studio/workbench_ui/session_groups.mjs';

const PROVIDERS = {codex: 'Codex', claude: 'Claude Code'};
const ACTIVITY_GROUPS = [['day', '近 24 小时'], ['week', '24 小时至 7 天'], ['older', '更早'], ['unknown', '活动时间未知']];
const DAY = 24 * 60 * 60 * 1000;
const NAME_ORDER = new Intl.Collator('zh-CN', {numeric: true, sensitivity: 'base'});
const COVERAGE = {
 source_unavailable: '部分已登记来源不可读或已变化',
 source_alias: '检测到不安全的来源别名',
 source_budget: '会话来源数量超过预算',
 nested_unavailable: '部分项目子目录无法安全检查',
 nested_config_unavailable: '部分项目原生配置无法核验',
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

const COLLECTIONS = [['current', '当前'], ['hidden', '隐藏'], ['archived', '归档'], ['trash', '回收站']];
const DEFAULT_VIEW = {alias: null, hidden: false, collection: 'active', view_revision: null};
const ACTIONS = {
 rename: '设置本地别名', hide: '隐藏', unhide: '取消隐藏',
 archive: '归档', trash: '移入回收站', restore: '恢复到当前列表',
};

function belongs(view, collection) {
 if (view.collection !== 'active') return collection === view.collection;
 return collection === (view.hidden ? 'hidden' : 'current');
}

function ViewDialog({action, initialAlias, onCancel, onConfirm}) {
 const dialog = useRef(null);
 const [alias, setAlias] = useState(initialAlias || '');
 useEffect(() => {
  dialog.current.showModal();
 }, []);
 const rename = action === 'rename';
 return <dialog ref={dialog} className="native-view-dialog" aria-labelledby="native-view-title"
  onCancel={event => { event.preventDefault(); onCancel(); }}
  onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation(); }}>
  <form onSubmit={event => { event.preventDefault(); onConfirm(rename ? alias : undefined); }}>
   <h3 id="native-view-title">{ACTIONS[action]}</h3>
   <p>仅影响 DevFlower 列表。</p>
   {rename && <label className="field"><span>本地别名</span><input autoFocus value={alias} maxLength={320}
     onChange={event => setAlias(event.target.value)}/></label>}
   <div className="native-view-dialog-buttons">
    <button type="button" autoFocus={!rename} onClick={onCancel}>取消</button>
    <button type="submit" className="primary" disabled={rename && (!alias.trim() || [...alias.trim()].length > 160)}>
     {rename ? '保存别名' : action === 'archive' ? '确认归档' : '确认移入回收站'}
    </button>
   </div>
  </form>
 </dialog>;
}

function GroupTrashDialog({dialog, busy, error, onCancel, onConfirm}) {
 const ref = useRef(null);
 useEffect(() => { ref.current.showModal(); }, []);
 return <dialog ref={ref} className="native-view-dialog" aria-label="工作流会话整理"
  onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}
  onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation(); }}>
  <h3>整组移入回收站</h3>
  <p>将 {dialog.count} 个已核验的工作流会话移入 DevFlower 回收站，可逐个恢复。此操作保留原生历史和运行记录。</p>
  {error && <p className="error-text" role="alert">{error}</p>}
  <div className="native-view-dialog-buttons">
   <button autoFocus disabled={busy} onClick={onCancel}>取消</button>
   <button className="primary" disabled={busy} onClick={onConfirm}>确认整组移入</button>
  </div>
 </dialog>;
}

function listText(value) {
 return value.normalize('NFKC').toLowerCase();
}

function collectionRows(list, preference, collection, query) {
 if (!list) return [];
 const counts = new Map();
 return list.sessions.flatMap((row, index) => {
  const view = preference(row.handle);
  if (!belongs(view, collection)) return [];
  const number = (counts.get(row.provider) || 0) + 1;
  counts.set(row.provider, number);
  const providerName = PROVIDERS[row.provider];
  const name = view.alias || providerName + ' 会话 ' + number;
  const matches = !query || [name, providerName, ...row.source_labels].some(value => listText(value).includes(query));
  const epoch = typeof row.updated_at === 'string' ? Date.parse(row.updated_at) : NaN;
  const time = Number.isFinite(epoch) && epoch <= list.referenceTime ? epoch : null;
  return [{...row, index, name, matches, time, hidden: view.hidden}];
 });
}

function activityGroup(time, referenceTime) {
 if (time === null) return 'unknown';
 const age = referenceTime - time;
 return age < DAY ? 'day' : age < 7 * DAY ? 'week' : 'older';
}

function compareRows(a, b, order) {
 if (order === 'name') return NAME_ORDER.compare(a.name, b.name) || a.index - b.index;
 if (a.time === b.time) return a.index - b.index;
 if (a.time === null) return 1;
 if (b.time === null) return -1;
 return order === 'earliest' ? a.time - b.time : b.time - a.time;
}

export function NativeSessions({api, workspace, enabled, writeProviders = [], workflowLinks, workflowTrash, terminalAvailable, titleAvailable, catalog,
 refreshCatalog, isCurrent, onClose, onWorkflow, escapeRef}) {
 const [list, setList] = useState(null);
 const [reading, setReading] = useState(null);
 const [markdownSelection, setMarkdownSelection] = useState(null);
 const [loading, setLoading] = useState(enabled);
 const [listError, setListError] = useState('');
 const [provider, setProvider] = useState('');
 const [listQuery, setListQuery] = useState('');
 const [grouping, setGrouping] = useState('provider');
 const [order, setOrder] = useState('latest');
 const [query, setQuery] = useState('');
 const [collection, setCollection] = useState('current');
 const [views, setViews] = useState(() => new Map());
 const [dialogAction, setDialogAction] = useState(null);
 const [mutating, setMutating] = useState(false);
 const [mutationError, setMutationError] = useState('');
 const [refreshCue, setRefreshCue] = useState('');
 const [notice, setNotice] = useState('');
 const [composer, setComposer] = useState(false);
 const [terminal, setTerminal] = useState(null);
 const [collapsed, setCollapsed] = useState(() => new Set());
 const [groupDialog, setGroupDialog] = useState(null);
 const [groupError, setGroupError] = useState('');
 const [groupBusy, setGroupBusy] = useState(false);
 const restoreKey = 'devflower-v2-session-last:' + workspace.id;
 const canRestore = Boolean(writeProviders.length || workflowLinks);
 const listGeneration = useRef(0);
 const selectionGeneration = useRef(0);
 const historyGeneration = useRef(0);
 const closeButton = useRef(null);
 const backButton = useRef(null),reader = useRef(null);
 const opener = useRef(null);
 const actionOpener = useRef(null);
 const collectionGroup = useRef(null);
 const mutation = useRef(null);

 async function refresh(reopen = null) {
  if (!enabled) return;
  const generation = ++listGeneration.current;
  selectionGeneration.current++;
  setLoading(true);
  setList(null);
  setReading(null);
  setListError('');
  setMutationError('');
  setRefreshCue('');
  setNotice('');
  setDialogAction(null);
  setQuery('');
  setGroupDialog(null);
  setGroupError('');
  try {
   const current = () => isCurrent() && generation === listGeneration.current;
   const result = await api('native-sessions?' + new URLSearchParams({workspace: workspace.id}));
   if (!current() || result.workspace !== workspace.id) return;
   const preferences = await api('native-session-views?' + new URLSearchParams({
    workspace: workspace.id, revision: result.revision
   }));
   if (current() && preferences.workspace === workspace.id && preferences.revision === result.revision) {
    setViews(new Map(preferences.views.map(view => [view.handle, view])));
    setList({...result, referenceTime: Date.now()});
    const row = reopen && result.sessions.find(item => item.handle === reopen);
    if (row?.access === 'readable') {
     const stored = preferences.views.find(item => item.handle === reopen) || DEFAULT_VIEW;
     const category = stored.collection === 'active' ? stored.hidden ? 'hidden' : 'current' : stored.collection;
     setCollection(category);
     const byHandle = new Map(preferences.views.map(view => [view.handle, view]));
     const named = collectionRows({...result, referenceTime: Date.now()}, handle =>
      byHandle.get(handle) || DEFAULT_VIEW, category, '').find(item => item.handle === reopen);
     await choose(named, null, result.revision);
    }
   }
  } catch (error) {
   if (isCurrent() && generation === listGeneration.current) setListError(error.message);
  } finally {
   if (isCurrent() && generation === listGeneration.current) setLoading(false);
  }
 }
 useEffect(() => {
  closeButton.current?.focus();
  refresh(canRestore ? localStorage.getItem(restoreKey) : null);
  return () => {
   listGeneration.current++;
   selectionGeneration.current++;
  };
 }, []);

 async function choose(row, button, revision = list?.revision) {
  if (!revision || row.access !== 'readable') return;
  const generation = ++selectionGeneration.current;
  if (canRestore) localStorage.setItem(restoreKey, row.handle);
  setComposer(false);
  const current = () => isCurrent() && generation === selectionGeneration.current;
  opener.current = button;
  setDialogAction(null);
  setMutationError('');
  setNotice('');
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
  setComposer(false);
  if (canRestore) localStorage.removeItem(restoreKey);
  selectionGeneration.current++;
  setDialogAction(null);
  setReading(null);
  requestAnimationFrame(() => {
   if (!isCurrent()) return;
   if (opener.current?.getClientRects().length) opener.current.focus();
   else closeButton.current?.focus();
  });
 }

 useImperativeHandle(escapeRef, () => () => {
  if (dialogAction || groupDialog) return true;
  if (!reading) return false;
  const disclosure=reader.current?.querySelector('.native-reader-tools > details[open]');
  if(disclosure){
   disclosure.open=false;
   disclosure.querySelector('summary')?.focus();
   return true;
  }
  back();
  return true;
 });

 const preference = handle => views.get(handle) || DEFAULT_VIEW;
 const selectedView = reading ? preference(reading.row.handle) : DEFAULT_VIEW;

 function switchCollection(value) {
  setComposer(false);
  if (canRestore) localStorage.removeItem(restoreKey);
  selectionGeneration.current++;
  setCollection(value);
  setDialogAction(null);
  setReading(null);
  setNotice('');
  setMutationError('');
 }

 function filterProvider(value) {
  if (reading?.result && value && value !== reading.row.provider) historyGeneration.current++;
  setProvider(value);
 }

 function dismissDialog() {
  setDialogAction(null);
  requestAnimationFrame(() => actionOpener.current?.focus());
 }

 async function changeView(action, alias) {
  if (!list || !reading?.result || mutation.current) return;
  const {handle} = reading.row;
  const {revision} = list;
  const generation = listGeneration.current, selection = selectionGeneration.current;
  const listCurrent = () => isCurrent() && generation === listGeneration.current;
  const current = () => listCurrent() && selection === selectionGeneration.current;
  const ticket = {};
  mutation.current = ticket;
  setMutating(true);
  setDialogAction(null);
  setMutationError('');
  setNotice('');
  try {
   const result = await api('native-session-view', {
    workspace: workspace.id, handle, revision, expected_view_revision: selectedView.view_revision,
    action, ...(action === 'rename' ? {alias} : {}),
   });
   if (listCurrent() && result.workspace === workspace.id && result.revision === revision && result.view.handle === handle) {
    const next = result.view;
    setViews(old => new Map(old).set(handle, next));
    if (current()) {
     setNotice(action === 'restore' && next.hidden ? '已恢复到隐藏列表。' : 'DevFlower 列表已更新。');
     if (!belongs(next, collection)) {
      selectionGeneration.current++;
      setReading(null);
      requestAnimationFrame(() => collectionGroup.current?.querySelector('input:checked')?.focus());
     }
    }
   }
  } catch (error) {
   if (current()) setMutationError(error.message + ' 刷新列表核对后再操作。');
   else if (listCurrent()) setRefreshCue('上一项整理结果需要刷新列表核对。');
  } finally {
   if (mutation.current === ticket) {
    mutation.current = null;
    if (listCurrent()) setMutating(false);
   }
  }
 }

 function manage(action, event) {
  if (['rename', 'archive', 'trash'].includes(action)) {
   actionOpener.current = event.currentTarget;
   setDialogAction({action, handle: reading.row.handle, revision: list.revision,
    selection: selectionGeneration.current});
  } else {
   changeView(action);
  }
 }

 function confirmView(alias) {
  if (!isCurrent() || !dialogAction || reading?.row.handle !== dialogAction.handle
      || list?.revision !== dialogAction.revision || selectionGeneration.current !== dialogAction.selection) {
   dismissDialog();
   return;
  }
  changeView(dialogAction.action, alias);
 }

 const listNeedle = listText(listQuery).trim();
 const rows = collectionRows(list, preference, collection, listNeedle);
 const filtered = rows.filter(row => row.matches && (!provider || provider === row.provider));
 const activityGrouping = grouping === 'activity';
 const workflowGrouping = grouping === 'workflow';
 const groupFor = row => groupKeyFor({workflow_group_id: row.workflow?.group}, 'workflow');
 const workflowGroups = new Map();
 if (workflowGrouping) for (const row of filtered) {
  const id = groupFor(row);
  if (!workflowGroups.has(id)) workflowGroups.set(id, row.workflow?.workflow || '未关联工作流');
 }
 const groups = workflowGrouping ? [...workflowGroups] : activityGrouping ? ACTIVITY_GROUPS : Object.entries(PROVIDERS);
 const canContinue = Boolean(reading?.result && reading.row.can_request_continue && writeProviders.includes(reading.row.provider));
 const pendingSend = writeProviders.length > 0 && Boolean(sessionStorage.getItem('devflower-v2-session-intent:' + workspace.id));

 async function groupAction(group, expected) {
  if (!list || groupBusy) return;
  const generation = listGeneration.current;
  setGroupBusy(true);
  setGroupError('');
  try {
   const value = await api('workflow-session-trash', {workspace: workspace.id, revision: list.revision, group,
    ...(expected ? {expected} : {})});
   if (!isCurrent() || generation !== listGeneration.current || value.workspace !== workspace.id || value.group !== group) return;
   if (expected) {
    setViews(previous => new Map([...previous, ...value.views.map(view => [view.handle, view])]));
    if (reading && value.views.some(view => view.handle === reading.row.handle)) back();
    setGroupDialog(null);
    setNotice('已将 ' + value.count + ' 个会话移入 DevFlower 回收站，可逐个恢复。');
   } else setGroupDialog(value);
  } catch (error) {
   if (isCurrent() && generation === listGeneration.current) setGroupError(error.message);
  } finally {
   if (isCurrent()) setGroupBusy(false);
  }
 }
 const selectedOutsideFilter = reading && rows.some(row => row.handle === reading.row.handle && !row.matches);
 const messages = reading?.result?.messages;
 const normalized = query.trim().toLocaleLowerCase();
 const visible = Array.isArray(messages) ? messages.filter(message =>
  !normalized || isVisibleMessage(message) && message.text.toLocaleLowerCase().includes(normalized)) : null;
 const selection = selectionGeneration.current, listVersion = listGeneration.current, historyVersion = historyGeneration.current;
 const format = markdownSelection === selection ? 'markdown' : 'text';
 const windowSearch = reading?.result && <>
  <label className="field"><span>搜索当前可见窗口</span><input type="search" maxLength={200} value={query}
   onChange={event => setQuery(event.target.value)}/></label>
  <p className="muted small">{reading.result.truncated ? '当前内容已截断。' : ''}搜索仅匹配此处已加载的可见消息。</p>
 </>;
 return <main id="v2-native-sessions" tabIndex={-1}
  className={'project-panel native-panel' + (reading ? ' has-preview' : '')} aria-label="原生会话">
  <header className="project-panel-header">
   <div><h2>原生会话</h2><p className="muted small">{workspace.name} · {writeProviders.length ? '会话中心' : '只读 · 占用状态未核实'}</p>
    {refreshCue && <p role="status" className="muted small">{refreshCue}</p>}</div>
   {terminalAvailable && <button onClick={() => { setComposer(false); setTerminal('existing'); }}>已有终端</button>}
   {writeProviders.length > 0 && <button onClick={() => { setTerminal(null); setComposer('new'); }}>{pendingSend ? '核验上次发送' : '新建会话'}</button>}
   <button aria-label="刷新原生会话" title="刷新原生会话" disabled={!enabled || loading || mutating} onClick={() => refresh(canRestore ? reading?.row.handle : null)}><RefreshCw size={16}/></button>
   <button ref={closeButton} aria-label="关闭原生会话" title="关闭原生会话" onClick={onClose}><X size={16}/></button>
  </header>
  <div className="project-panel-content">
   <nav className="project-tree native-list" aria-label="会话列表">
    {!enabled && <PanelEmpty icon={MessageSquareText} title="添加原生会话来源">
     <p>本机尚未启用原生会话只读浏览。</p>
     <p>停止桌面服务后运行 <code>devflower-v2 configure --native-history</code>，再重新打开。</p>
    </PanelEmpty>}
    {loading && <p className="muted" role="status">正在检查已批准的会话来源…</p>}
    {listError && <p className="error-text" role="alert">{listError}</p>}
    {list && <>
     <p className="native-coverage" role="status">
      {list.coverage.state === 'complete' ? '已检查登记范围' : list.coverage.state === 'partial' ? '扫描覆盖不完整' : '暂时没有可用来源'}
     </p>
     {list.coverage.reasons.length > 0 && <ul className="native-coverage-reasons">
      {list.coverage.reasons.map(reason => <li key={reason}>{COVERAGE[reason] || '部分来源未核实'}</li>)}
     </ul>}
     {list.configurations?.length>0&&<details className="native-configurations"><summary>项目原生配置</summary>
      <ul>{list.configurations.map(item=><li key={item.provider+':'+item.path}>
       <strong>{PROVIDERS[item.provider]} · {item.path}</strong>
       <small>{item.files.join('、')} · {item.history_directory?'发现历史目录':'仅配置'}</small>
      </li>)}</ul>
     </details>}
     {groupError && <p role="alert" className="error-text">{groupError}</p>}
     {notice && <p role="status" className="muted small">{notice}</p>}
     <div className="native-list-search">
      <label className="field"><span>筛选会话列表</span>
       <input type="search" aria-describedby="native-list-filter-scope" value={listQuery}
        onChange={event => setListQuery([...event.target.value].slice(0, 200).join(''))}/>
      </label>
      <small id="native-list-filter-scope" className="muted">仅筛选会话名称、Provider 和来源。</small>
     </div>
     <fieldset className="native-collections" ref={collectionGroup}>
      <legend>会话整理分类</legend>
      {COLLECTIONS.map(([id, label]) => <label key={id}>
       <input type="radio" name="native-collection" checked={collection === id}
        onChange={() => switchCollection(id)}/><span>{label}</span>
      </label>)}
     </fieldset>
     <div className="native-list-options">
      <label className="field"><span>Provider</span>
       <select aria-label="筛选会话 Provider" value={provider} onChange={event => filterProvider(event.target.value)}>
        <option value="">全部</option>{Object.entries(PROVIDERS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
       </select>
      </label>
      <label className="field"><span>会话列表分组</span>
       <select value={grouping} onChange={event => setGrouping(event.target.value)}>
        <option value="provider">Provider</option><option value="activity">按活动时间</option>
        {workflowLinks && <option value="workflow">按工作流</option>}
       </select>
      </label>
      <label className="field"><span>会话列表排序</span>
       <select value={order} onChange={event => setOrder(event.target.value)}>
        <option value="latest">最近活动</option><option value="earliest">最早活动</option>
        <option value="name">按名称</option>
       </select>
      </label>
     </div>
     {!filtered.length && <p className="muted">{listNeedle || provider ? '当前集合没有符合筛选条件的会话。' :
      '当前工作空间没有匹配的原生会话。'}</p>}
     {groups.map(([id, name]) => {
      const group = filtered.filter(row => (workflowGrouping ? groupFor(row) : activityGrouping ?
       activityGroup(row.time, list.referenceTime) : row.provider) === id);
      group.sort((a, b) => compareRows(a, b, order));
      return group.length > 0 && <section key={id} aria-label={name}>
       {workflowGrouping ? <div className="native-workflow-group">
        <h3><button aria-expanded={!collapsed.has(id)} onClick={() => setCollapsed(previous => {
         const next = new Set(previous);
         if (next.has(id)) next.delete(id); else next.add(id);
         return next;
        })}>{name}</button></h3>
        {workflowTrash && id !== 'unlinked' && collection === 'current' && group.every(row => row.provider === 'codex') && <button disabled={groupBusy || mutating}
         onClick={() => groupAction(id)}>整组移入回收站</button>}
       </div> : <h3>{name}</h3>}
       {(!workflowGrouping || !collapsed.has(id)) && <ul>{group.map(row => <li key={row.handle}>
        <button className="native-session-item" aria-pressed={reading?.row.handle === row.handle}
         ref={button => { if (button && reading?.row.handle === row.handle) opener.current = button; }}
         disabled={row.access !== 'readable'} onClick={event => choose(row, event.currentTarget)}>
         <MessageSquareText size={15}/>
         <span>{row.name}
          <small>{row.source_labels.join(' · ')} · {row.time === null ? '时间未知' : new Date(row.time).toLocaleString()}</small>
          <small>{ACCESS[row.access]}{row.hidden ? ' · 已隐藏' : ''}</small>
          {row.workflow && <small>{row.workflow.agent}</small>}</span>
        </button>
       </li>)}</ul>}
      </section>;
     })}
    </>}
   </nav>
   {reading && <section ref={reader} className="project-preview native-window" aria-label="会话正文">
    <header><button ref={backButton} aria-label="返回会话列表" title="返回会话列表" onClick={back}><ArrowLeft size={16}/></button>
     <div className="native-reader-heading"><h3>{selectedView.alias || reading.row.name || PROVIDERS[reading.row.provider]} · 只读</h3>
      <p className="field-note">{PROVIDERS[reading.row.provider]}</p></div></header>
    {(reading.row.workflow || canContinue || reading.result && writeProviders.includes(reading.row.provider)) &&
     <div className="native-session-primary">
      {reading.row.workflow && <button onClick={() => onWorkflow(reading.row.workflow)}>
       查看工作流节点 · {reading.row.workflow.agent}</button>}
      {canContinue && <button onClick={() => { setTerminal(null); setComposer('resume'); }}>继续会话</button>}
      {reading.result && writeProviders.includes(reading.row.provider) &&
       (terminalAvailable && reading.row.provider === 'codex' && canContinue ?
        <button onClick={() => { setComposer(false); setTerminal({handle: reading.row.handle, revision: list.revision}); }}>
         打开原生终端</button> : <>
         <button disabled aria-describedby="native-terminal-blocked">打开原生终端</button>
         <small className="muted" id="native-terminal-blocked">{reading.row.provider === 'claude'
          ? 'Claude 终端接管暂不可用。' : '此 Codex 会话的终端暂未启用。'}</small>
        </>)}
     </div>}
    {reading.result && reading.row.claude_session_state && <p className="muted small">{{
     managed: 'DevFlower 受管会话。单写保证适用于参与租约的 DevFlower 操作。',
     EXTERNAL_SESSION: '外部 Claude 会话，仅可查看。',
     MANAGED_HISTORY_CHANGED: '受管会话的历史或配置已变化，仅可查看。',
     RECOVERY_REQUIRED: '上一项原生操作待核验，仅可查看。',
     unverified: 'Claude 会话来源未核验，仅可查看。'
    }[reading.row.claude_session_state]}</p>}
    {selectedOutsideFilter && <p role="status" className="muted small">当前阅读的会话不在筛选结果中。</p>}
    {reading.loading && <p className="muted" role="status">正在读取已核验的会话…</p>}
    {reading.error && <p className="error-text" role="alert">{reading.error}</p>}
    {reading.result && <NativeActivity key={selection} api={api}
     scope={{workspace: workspace.id, handle: reading.row.handle, revision: list.revision}}
     isCurrent={() => isCurrent() && selectionGeneration.current === selection && listGeneration.current === listVersion}>
     {activityButton => <>
      <VisibleHistory key={selection + ':' + historyVersion} api={api} format={format}
       fullHistory={!provider || provider === reading.row.provider}
       scope={{workspace: workspace.id, handle: reading.row.handle, revision: list.revision}}
       isCurrent={() => isCurrent() && selectionGeneration.current === selection
        && listGeneration.current === listVersion && historyGeneration.current === historyVersion}
       activityButton={activityButton} windowSearch={windowSearch}
       sessionActions={<details className="native-session-actions" name="native-reader-tools"><summary>会话操作</summary>
        <div className="native-reader-disclosure">
         {titleAvailable && reading.row.provider === 'codex' && canContinue &&
          <NativeTitle key={reading.row.handle + ':' + list.revision} api={api} workspace={workspace.id}
           handle={reading.row.handle} revision={list.revision}/>}
         {reading.row.provider === 'claude' &&
          <p className="field-note">此处支持为 Claude 会话设置 DevFlower 本地别名。</p>}
         <div className="native-view-actions" aria-label="仅在 DevFlower 整理">
          {['rename', selectedView.hidden ? 'unhide' : 'hide',
           ...(selectedView.collection === 'active' ? ['archive', 'trash'] :
              selectedView.collection === 'archived' ? ['trash', 'restore'] : ['restore'])].map(action =>
           <button key={action} disabled={mutating} onClick={event => manage(action, event)}>
            {action === 'restore' && selectedView.hidden ? '恢复到隐藏列表' : ACTIONS[action]}
           </button>)}
         </div>
         <label className="field native-message-format"><span>会话正文格式</span>
          <select value={format} onChange={event => setMarkdownSelection(event.target.value === 'markdown' ? selection : null)}>
           <option value="text">纯文本</option><option value="markdown">Markdown</option>
          </select>
         </label>
        </div>
       </details>}>
       {mutationError && <p className="error-text" role="alert">{mutationError}</p>}
       <NativeMessages label="已加载消息" messages={visible} format={format} truncated={reading.result.truncated}
        empty={normalized ? '当前窗口没有匹配内容。' : '此会话没有可显示的消息。'}/>
      </VisibleHistory>
     </>}
    </NativeActivity>}
   </section>}
  </div>
  {terminal && <NativePty key={terminal === 'existing' ? terminal : terminal.handle} api={api} workspace={workspace}
   scope={terminal === 'existing' ? null : terminal} catalog={catalog} refreshCatalog={refreshCatalog}
   onClose={() => setTerminal(null)}/>}
  {composer && <NativeComposer key={composer + (reading?.row.handle || '')} api={api} workspace={workspace}
   providers={writeProviders} scope={composer === 'resume' && reading ? {
    provider: reading.row.provider, handle: reading.row.handle, revision: list.revision
   } : null} catalog={catalog} refreshCatalog={refreshCatalog} onClose={() => setComposer(false)}
   onCompleted={handle => { setComposer(false); refresh(handle); }}/>}
  {groupDialog && <GroupTrashDialog dialog={groupDialog} busy={groupBusy} error={groupError}
   onCancel={() => setGroupDialog(null)} onConfirm={() => groupAction(groupDialog.group, groupDialog.expected)}/>}
  {dialogAction && <ViewDialog action={dialogAction.action} initialAlias={selectedView.alias} onCancel={dismissDialog}
   onConfirm={confirmView}/>}
 </main>;
}
