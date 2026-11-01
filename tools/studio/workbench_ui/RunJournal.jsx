import React,{useEffect,useRef,useState} from 'react';
import {providerLabel} from './provider_label.mjs';
import Markdown from './DocumentLinks.jsx';
import {InputCards,ReadableContent} from './ConversationView.jsx';
import {ToolActivity} from './ToolActivity.jsx';
import {StatusBadge} from './RuntimeOverview.jsx';
import {Download,Workflow,Plus} from 'lucide-react';
import {workflowGroups,nodeGroups} from './journal_groups.mjs';
const events={'agent-completed':'完成本轮回答','agent-started':'开始执行','activation-running':'开始处理','completed':'工作流完成','node-completed':'节点完成','control-pause':'暂停调度','control-resume':'继续调度','control-stop':'停止运行','loop-round':'进入下一轮','skipped':'跳过此分支'};
// Terminal structured values must retain every public sibling of answer/reply.
function ResultContent({value}){
 if(Array.isArray(value))return <div>{value.map((v,i)=><ResultContent key={i} value={v}/>)}</div>;
 if(value&&typeof value==='object')return <div className="readable-fields">{Object.entries(value).map(([key,v])=><section key={key}><small>{key}</small><ResultContent value={v}/></section>)}</div>;
 return <ReadableContent value={value}/>;
}
function AgentInput({sections}){return <><InputCards sections={sections}/><details><summary>给此 Agent 的指令</summary>{sections?.filter(s=>!s.title.startsWith('上游输入')).map((s,i)=><section key={i}><small>{s.title}</small><Markdown>{s.text}</Markdown></section>)}</details></>}
function EntryContent({entry:e}){
 if(e.kind==='activation')return <>
  {e.members.filter(m=>m.kind==='input').map(m=><details key={m.id}><summary>收到任务与上游输入</summary><AgentInput sections={m.sections}/></details>)}
  {e.members.filter(m=>m.kind==='assistant'||m.kind==='visible').map(m=><section key={m.id}><Markdown>{m.text}</Markdown>{m.kind==='visible'&&<small>实时可见输出，尚未形成完成回执</small>}</section>)}
  <ToolActivity items={e.members.filter(m=>m.kind==='tool').map(m=>m.tool)}/>
  {e.members.filter(m=>m.kind==='summary').map(m=><details key={m.id}><summary>模型提供的公开推理摘要</summary><Markdown>{m.text}</Markdown></details>)}
 </>;
 if(e.kind==='result')return <>{e.text&&<p role="status">{e.text}</p>}{e.content&&Object.entries(e.content).map(([key,value])=><section key={key}><small>{{answer:'最终回答',result:'结构化结果',artifacts:'产物',evidence:'依据',issues:'待处理问题',summary:'摘要'}[key]||key}</small><ResultContent value={value}/></section>)}</>;
 if(e.kind==='input')return <><small>收到任务与上游输入</small><AgentInput sections={e.sections}/></>;
 if(e.kind==='tool')return <ToolActivity items={[e.tool]}/>;
 if(e.kind==='handoff')return <details className="journal-handoff"><summary>交给「{e.target}」</summary><ReadableContent value={e.content}/></details>;
 if(e.kind==='summary')return <details><summary>模型提供的公开推理摘要</summary><Markdown>{e.text}</Markdown></details>;
 if(e.kind==='event')return <small>{events[e.event_kind]||({'activation-completed':'节点完成','activation-created':'节点等待输入','run-finished':'本轮结束'})[e.event_kind]||'运行事件'}{e.text?' · '+e.text:''}</small>;
 return <><Markdown>{e.text}</Markdown>{e.kind==='visible'&&<small>实时可见输出，尚未形成完成回执</small>}</>;
}
export function RunJournal({api,workspace,run,runs,onSelect,onNext,onCanvas}){
 const [value,setValue]=useState(null),[error,setError]=useState(''),[showEvents,setShowEvents]=useState(false),[node,setNode]=useState(''),[folds,setFolds]=useState({});const log=useRef(null),follow=useRef(true);
 useEffect(()=>{let active=true,busy=false;setValue(null);setError('');setNode('');async function read(){if(busy||!run)return;busy=true;try{const v=await api('/api/run-conversation?workspace='+workspace.id+'&run='+run);if(active){if(v.run!==run||v.workspace!==workspace.id)throw Error('记录身份未确认');setValue(old=>JSON.stringify(old)===JSON.stringify(v)?old:v);setError('')}}catch(e){if(active){setError(e.message);setValue(null)}}finally{busy=false}}read();const timer=setInterval(read,2000);return()=>{active=false;clearInterval(timer)}},[run,workspace.id]);
 useEffect(()=>{if(follow.current&&log.current)log.current.scrollTop=log.current.scrollHeight},[value]);
 function download(){const raw=JSON.stringify(value,null,2),url=URL.createObjectURL(new Blob([raw],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='DevFlower-'+run+'.json';a.click();URL.revokeObjectURL(url)}
 const workflows=workflowGroups(runs),sections=nodeGroups(value?.entries||[]);
 return <section className="run-journal" aria-label="整轮任务对话"><header><div><h2>{value?.name||'任务记录'}</h2><p>按工作流、任务和 Agent 查看记录 · 阅读历史不会调用模型</p></div><div><button onClick={onCanvas}><Workflow size={14}/>画布</button><button disabled={!value} onClick={download}><Download size={14}/>导出记录</button><button disabled={!value||!onNext} onClick={onNext}><Plus size={14}/>用此流程开始下一任务</button></div></header><div className="journal-workspace"><nav className="journal-navigation" aria-label="按工作流查看任务"><h3>工作流记录</h3>{workflows.map(g=><section key={g.id}><button className="journal-workflow" aria-expanded={!folds[g.id]} onClick={()=>setFolds(v=>({...v,[g.id]:!v[g.id]}))}><Workflow size={14}/>{g.name}<small>{g.runs.length} 次任务</small></button>{!folds[g.id]&&g.runs.map(r=><button key={r.id} className={'journal-task '+(run===r.id?'selected':'')} aria-current={run===r.id?'page':undefined} onClick={()=>onSelect(r.id)}><strong>{r.task?.slice(0,80)||'未命名任务'}</strong><small>{new Date(r.created_at*1000).toLocaleString('zh-CN')}</small></button>)}</section>)}{!runs.length&&<p>工作流运行后自动保存在这里。</p>}</nav><div className="journal-detail"><div className="run-journal-picker">{value&&<StatusBadge state={value.state}/>}<label className="journal-events-toggle"><input type="checkbox" checked={showEvents} onChange={e=>setShowEvents(e.target.checked)}/>显示运行事件</label></div><nav className="journal-node-tabs" aria-label="按 Agent 查看记录"><button className={!node?'active':''} onClick={()=>setNode('')}>流程全貌</button>{sections.map(g=><button key={g.id} className={node===g.id?'active':''} onClick={()=>setNode(g.id)}>{g.name}</button>)}</nav>{error&&<p role="alert">状态待确认 · {error}</p>}<div className="run-journal-messages" role="log" aria-label="任务群聊记录" ref={log} onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<90}}>{!run&&<p>开始一次任务后，这里会自动保存整轮记录。</p>}{value?.notices.map((n,i)=><p className="history-limit" key={i}>{n}</p>)}{sections.filter(g=>!node||node===g.id).map(g=><section className="journal-node-section" key={g.id}><h3>{g.name}</h3>{g.entries.filter(e=>showEvents||e.kind!=='event'||e.text).map(e=><article className={'journal-entry '+e.kind} key={e.id} data-entry-id={e.id}><header><strong>{e.name}</strong><span>{e.provider?providerLabel(e.provider):''}</span>{e.round?.length>0&&<small>第 {e.round.join(' / ')} 轮</small>}<time>{e.at?new Date(e.at*1000).toLocaleString('zh-CN'):'未记录时间'}</time></header><EntryContent entry={e}/>{e.activation&&<details className="identity-evidence"><summary>来源身份</summary><small>运行 {run} · 节点 {e.node} · 回合 {e.activation}</small></details>}</article>)}</section>)}</div></div></div></section>
}
