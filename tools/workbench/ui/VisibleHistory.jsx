import React, {useEffect, useRef, useState} from 'react';
import {NativeMessages} from './NativeMessages.jsx';

const STACK_LIMIT = 16;
const FORBIDDEN_QUERY_CHAR = /[\p{Cc}\p{Cf}\p{Cs}]/u;

export function VisibleHistory({api, scope, isCurrent, format, fullHistory, activityButton, windowSearch, sessionActions, children}) {
 const [query, setQuery] = useState('');
 const [page, setPage] = useState(null);
 const generation = useRef(0);
 const heading = useRef(null);
 const browseButton = useRef(null);
 const search = useRef(null);
 const queryLength = [...query].length;
 const canSearch = queryLength >= 1 && queryLength <= 200 && query.trim() && !FORBIDDEN_QUERY_CHAR.test(query);

 useEffect(() => () => { generation.current++; }, []);

 async function read(mode, cursor, stack, submitted) {
  const request = ++generation.current;
  const pending = {mode, cursor, stack, submitted, loading: true};
  const current = () => isCurrent() && generation.current === request;
  if (search.current) search.current.open = false;
  setPage(pending);
  requestAnimationFrame(() => { if (current()) heading.current?.focus(); });
  try {
   const params = {...scope, ...(cursor === null ? {} : {cursor})};
   const result = mode === 'browse'
    ? await api('native-session-page?' + new URLSearchParams(params))
    : await api('native-session-search', {...params, query: submitted});
   if (!current()) return;
   if (result.workspace !== scope.workspace || result.handle !== scope.handle || result.revision !== scope.revision
       || result.mode !== mode || result.coverage !== 'verified_complete' || result.readonly !== true) {
    throw new Error('无法核实当前会话的结果。请重新打开会话。');
   }
   setPage({...pending, loading: false, result});
  } catch (error) {
   if (current()) setPage({...pending, loading: false, error: error.message});
  }
 }

 function openWindow() {
  generation.current++;
  setPage(null);
  browseButton.current?.focus();
 }

 const browsing = page?.mode === 'browse';
 return <>
  <div className="native-reader-tools" aria-label="正文工具">
   {activityButton}
   {fullHistory && <button ref={browseButton} onClick={() => read('browse', null, [], null)}>浏览更早消息</button>}
   <details ref={search} className="native-message-search" name="native-reader-tools"><summary>查找消息</summary>
    <div className="native-reader-disclosure">
     {!page && windowSearch}
     {fullHistory && <form onSubmit={event => { event.preventDefault(); if (canSearch) read('search', null, [], query); }}>
      <label className="field"><span>搜索整个可见会话</span>
       <input type="search" maxLength={400} value={query} onChange={event => setQuery(event.target.value)}/>
      </label>
      <button type="submit" disabled={!canSearch}>搜索整个可见会话</button>
     </form>}
    </div>
   </details>
   {sessionActions}
   {page && <button onClick={openWindow}>返回默认窗口</button>}
  </div>
  {!page ? children : <section aria-labelledby="native-history-heading" className="native-history-page">
   <div className="native-history-summary">
    <h3 id="native-history-heading" ref={heading} tabIndex={-1}>{browsing ? '会话快照分页' : '可见会话搜索结果'}</h3>
    <p className="muted small">{browsing ? '从最近一页向更早阅读，每页按原顺序显示。' : '从最早匹配向后阅读。命中消息的全部可见片段按顺序显示。'}</p>
    <p className="muted small">结果绑定当前会话快照。长消息可跨页，片段编号用于接续阅读。</p>
    {page.loading && <p role="status" className="muted small">正在核验完整会话快照…</p>}
    {page.error && <p role="alert" className="error-text">{page.error} 可返回会话列表重新打开。</p>}
    {page.result && <p role="status" className="muted small">已完整核验当前快照的可见历史。</p>}
    <div className="native-history-buttons">
     {page.stack.length > 0 && <button disabled={page.loading} onClick={() => read(page.mode, page.stack.at(-1),
      page.stack.slice(0, -1), page.submitted)}>{browsing ? '较新一页' : '上一页搜索结果'}</button>}
     {page.result?.has_more && <button onClick={() => read(page.mode, page.result.next_cursor,
      [...page.stack, page.cursor].slice(-STACK_LIMIT), page.submitted)}>
      {browsing ? '更早一页' : '下一页搜索结果'}</button>}
    </div>
   </div>
   {page.result && <NativeMessages label="已核验消息片段" messages={page.result.messages}
    format={format} paged empty={browsing ? '此快照没有可显示的消息。' : '此快照的可见会话没有匹配内容。'}/>}
  </section>}
 </>;
}
