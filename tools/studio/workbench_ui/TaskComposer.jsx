import React from 'react';
import {ArrowUp,LockKeyhole} from 'lucide-react';

export function TaskComposer({task,onChange,readonly,onRun,calls,state,disabled}){
 return <section className="task-input" aria-label="任务输入区">
  <div className="task-input-label"><strong>{readonly?'本次运行任务':'接下来想完成什么？'}</strong><small>{readonly?<><LockKeyhole size={12}/>冻结快照</>:'描述目标，然后开始运行 · Ctrl / ⌘ + Enter'}</small></div>
  <div className="task-input-editor"><textarea aria-label="本次任务" readOnly={readonly} value={task} onChange={e=>onChange(e.target.value)} placeholder="例如：检查这个项目的登录流程，修复问题并补充测试…" rows={2} onKeyDown={e=>{if(!readonly&&!disabled&&(e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();onRun()}}}/>{!readonly&&<button className="primary task-send" aria-label="提交本次任务" title="提交本次任务 · Ctrl / ⌘ + Enter" disabled={disabled} onClick={onRun}><ArrowUp size={17}/></button>}</div>
  {readonly&&<small className="task-run-meta">{calls} 次原生调用 · {state}</small>}
 </section>;
}
