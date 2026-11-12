import React, {useEffect, useRef, useState} from 'react';

const CATEGORIES = new Set(['读取', '搜索', '编辑', '命令', '其他']);
const OBSERVATIONS = {
 return_observed: '观察到配对返回记录',
 start_only: '仅观察到开始记录',
 return_only: '仅观察到返回记录',
 unpaired: '记录未配对',
 ambiguous: '记录关联有歧义',
};
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$/;

function validTimestamp(value) {
 return value === null || typeof value === 'string' && TIMESTAMP.test(value) && Number.isFinite(Date.parse(value));
}

function isActivity(result, scope) {
 return result && result.workspace === scope.workspace && result.handle === scope.handle && result.revision === scope.revision
  && result.readonly === true && Array.isArray(result.coverage?.reasons)
  && (result.coverage.state === 'complete' && result.coverage.reasons.length === 0
   || result.coverage.state === 'partial' && result.coverage.reasons.length === 1 && result.coverage.reasons[0] === 'display_limit')
  && Array.isArray(result.activity) && result.activity.length <= 100
  && result.activity.every(row => row && CATEGORIES.has(row.category)
   && typeof row.observation === 'string' && Object.hasOwn(OBSERVATIONS, row.observation)
   && validTimestamp(row.started_at) && validTimestamp(row.returned_at));
}

function ActivityTime({value}) {
 return value === null ? '未知' : <time dateTime={value}>{new Date(value).toLocaleString()}</time>;
}

export function NativeActivity({api, scope, isCurrent, children}) {
 const [activity, setActivity] = useState(null);
 const generation = useRef(0);
 const opener = useRef(null);
 const back = useRef(null);
 const open = activity !== null;

 useEffect(() => () => { generation.current++; }, []);
 useEffect(() => { if (open) back.current?.focus(); }, [open]);

 async function readActivity() {
  const version = ++generation.current;
  const current = () => isCurrent() && generation.current === version;
  setActivity({loading: true});
  try {
   const result = await api('native-session-activity?' + new URLSearchParams(scope));
   if (!current()) return;
   if (!isActivity(result, scope)) throw new Error('无法核实当前会话的工具活动。请重新打开会话。');
   setActivity({result});
  } catch (error) {
   if (current()) setActivity({error: error.message});
  }
 }

 function returnToTranscript() {
  const version = ++generation.current;
  setActivity(null);
  requestAnimationFrame(() => {
   if (isCurrent() && generation.current === version) opener.current?.focus();
  });
 }

 return <>
  <div hidden={open}>
   {children(<button ref={opener} onClick={readActivity}>工具活动</button>)}
  </div>
  {open && <section aria-label="原生工具活动" className="native-activity" onKeyDown={event => {
   if (event.key === 'Escape') {
    event.stopPropagation();
    returnToTranscript();
   }
  }}>
   <div className="native-activity-heading">
    <h3>工具活动</h3>
    <button ref={back} onClick={returnToTranscript}>返回正文</button>
   </div>
   <p className="muted small">仅显示已核验的原生工具记录。此处与会话正文、工作流运行分开。</p>
   {activity.loading && <p role="status" className="muted">正在核验原生工具活动…</p>}
   {activity.error && <p role="alert" className="error-text">{activity.error}</p>}
   {activity.result && <>
    <p role="status" className="muted small">{activity.result.coverage.state === 'partial' ?
     '只显示最近 100 项活动。' : '已核验当前快照的工具活动。'}</p>
    {!activity.result.activity.length ? <p className="muted">没有可显示的原生工具活动。</p> :
     <ul className="native-activity-items">{activity.result.activity.map((row, index) => <li key={index}>
      <strong>{row.category}</strong><span>{OBSERVATIONS[row.observation]}</span>
      <dl><dt>开始时间</dt><dd><ActivityTime value={row.started_at}/></dd>
       <dt>返回时间</dt><dd><ActivityTime value={row.returned_at}/></dd></dl>
     </li>)}</ul>}
   </>}
  </section>}
 </>;
}
