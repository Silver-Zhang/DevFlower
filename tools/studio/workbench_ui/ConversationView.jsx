import React,{useEffect,useState} from 'react';
import Markdown from './DocumentLinks.jsx';
import remarkGfm from 'remark-gfm';
import {ToolActivity} from './ToolActivity.jsx';
import {StatusBadge} from './RuntimeOverview.jsx';

const hidden=new Set(['receipt','authorization','credentials','edge','activation','target_activation','run','workflow_id','sha256','content_sha256','source_content_sha256']);
export function ReadableContent({value}){
 if(typeof value==='string')return <Markdown remarkPlugins={[remarkGfm]} skipHtml>{value}</Markdown>;
 if(value==null)return <span className="hint">无内容</span>;
 if(typeof value!=='object')return <span>{String(value)}</span>;
 if(value.truncated)return <><Markdown skipHtml>{value.text||''}</Markdown><small>已按本次交接预算截取</small></>;
 if(typeof value.answer==='string')return <Markdown remarkPlugins={[remarkGfm]} skipHtml>{value.answer}</Markdown>;
 if(typeof value.reply==='string')return <Markdown remarkPlugins={[remarkGfm]} skipHtml>{value.reply}</Markdown>;
 if(Array.isArray(value))return <div>{value.map((v,i)=><ReadableContent key={i} value={v}/>)}</div>;
 const names={task:'任务',summary:'摘要',result:'结果',artifacts:'附件',issues:'待处理问题',evidence:'依据',accepted:'是否满足要求',next:'后续建议'};
 return <div className="readable-fields">{Object.entries(value).filter(([k])=>!hidden.has(k)).map(([k,v])=><section key={k}><small>{names[k]||k}</small><ReadableContent value={v}/></section>)}</div>;
}
export function InputCards({sections,names={},actual=false}){
 const task=actual?sections?.find(s=>s.title.startsWith('本次任务目标')):null;
 const upstream=sections?.find(s=>s.title.startsWith('上游输入'));let values;
 try{values=JSON.parse(upstream?.text||'[]')}catch{return upstream?<ReadableContent value={upstream.text}/>:null}
 return <>{task&&<section className="upstream-cards" aria-label="本轮请求的任务目标"><h4>本轮请求的任务目标</h4><ReadableContent value={task.text}/></section>}{values.length>0&&<section className="upstream-cards" aria-label="本轮收到的上游内容"><h4>已带入本轮的上游内容</h4>{values.map((v,i)=><article key={i}><header>{v.source_name||names[v.source]||v.source||'上游节点'}<small>第 {(v.source_round?.length?v.source_round:[1]).join(' / ')} 轮</small></header><ReadableContent value={v.content??v}/></article>)}</section>}</>;
}
function visibleText(text){try{const v=JSON.parse(text);if(v&&typeof v==='object')return typeof v.answer==='string'?v.answer:typeof v.reply==='string'?v.reply:text}catch{}return text}
export function ConversationMessages({messages=[],answer}){
 const last=messages.filter(m=>m.role==='assistant').at(-1);
 return <>{messages.map((m,i)=><article key={i} className={'message '+m.role}><span className="message-role">{m.role==='user'?'你':'Agent'}</span><Markdown remarkPlugins={[remarkGfm]} skipHtml>{visibleText(m.text)}</Markdown></article>)}{answer&&visibleText(last?.text||'').trim()!==answer.trim()&&<article className="message assistant saved-answer"><span className="message-role">Agent · 运行保存的回答</span><Markdown remarkPlugins={[remarkGfm]} skipHtml>{answer}</Markdown></article>}</>;
}
export function ConversationTimeline({turns}){
 return <div className="conversation-timeline" aria-label="对话时间线">{turns.map(turn=>turn.kind==='native'?<section key={turn.id} data-turn-id={turn.id} className="native-conversation-message"><small className="message-origin">原生会话</small><ConversationMessages messages={turn.messages}/></section>:<section key={turn.id} data-turn-id={turn.id}>
  <article className="message user"><span className="message-role">{turn.delegated?'父 Agent 委派的任务':'你'}{turn.kind==='workflow'?' · '+(turn.name||'工作流任务'):''}</span>{turn.user_text?<Markdown remarkPlugins={[remarkGfm]} skipHtml>{turn.user_text}</Markdown>:<small>原始任务文字未保存，可查看高级原生记录。</small>}</article>
  <InputCards sections={turn.request_sections} actual={turn.actual_request_recorded}/>
  {turn.answer?<article className={'message '+(turn.kind==='workflow'&&turn.answer_source==='saved-runtime-result'?'execution-result':'assistant')} data-result-id={turn.kind==='workflow'?turn.id:undefined}><span className="message-role">Agent{turn.kind==='workflow'?' · 工作流回答':''}</span>{turn.kind==='workflow'&&<small>第 {(turn.iteration_path?.length?turn.iteration_path:[1]).join(' / ')} 轮{turn.run_state!=='COMPLETED'?' · 整体流程'+({FAILED:'失败',CANCELLED:'已取消',RECOVERY_REQUIRED:'状态待确认',RUNNING:'运行中',QUEUED:'等待中'}[turn.run_state]||'状态待确认'):''}</small>}<Markdown remarkPlugins={[remarkGfm]} skipHtml>{turn.answer}</Markdown><details className="identity-evidence"><summary>原始运行身份与来源</summary><small>{turn.answer_source==='native-text'?'原生可见文本':'结构化执行结果'} · {turn.run||turn.id} · {turn.node||'对话'}</small></details></article>:<div className="turn-status"><StatusBadge state={turn.state}/>{turn.reason&&<p>{turn.reason}</p>}{['STARTING','RUNNING','QUEUED'].includes(turn.state)&&<span>等待原生 Agent 的可见输出…</span>}</div>}
 </section>)}</div>;
}
export function NodeConversation({api,workspace,activation,run,names,onSession}){
 const [value,setValue]=useState(null),[error,setError]=useState('');
 const identity=JSON.stringify([workspace,run,activation?.id]);
 useEffect(()=>{setValue(null);setError('');if(!activation?.id)return;let active=true,busy=false,timer;const id=activation.id;
  async function read(){if(!active||busy)return;busy=true;try{const r=await api(`/api/node-conversation?workspace=${workspace}&run=${run}&activation=${id}`);
   if(active){if(r.workspace!==workspace||r.run!==run||r.activation!==id)throw Error('对话身份未确认');setValue(r);setError('');if(['COMPLETED','FAILED','CANCELLED','SKIPPED'].includes(r.state))clearInterval(timer)}}
   catch(e){if(active){setValue(null);setError('连接中断，状态和实时输出待确认。')}}finally{busy=false}}
  read();timer=setInterval(read,1500);return()=>{active=false;clearInterval(timer)}
 },[identity]);
 if(!activation)return <p className="hint">节点开始后，这里会直接显示任务和 Agent 对话。</p>;
 if(error)return <p role="alert">{error}</p>;
 if(!value)return <p role="status">正在读取本轮对话…</p>;
 const task=value.request_sections.find(s=>s.title.startsWith('每轮'))?.text;
 return <section className="node-conversation" aria-label="Agent 运行对话"><header><StatusBadge state={value.state}/>{['STARTING','RUNNING'].includes(value.state)&&<small role="status">{value.connection_check?'正在检查连接':value.process_alive?(value.activity.at(-1)?.name||'等待可见输出'):'正在准备'}</small>}{value.session&&<button onClick={()=>onSession(value.session)}>打开完整会话</button>}</header>
  <article className="message user"><span className="message-role">本次任务</span><Markdown remarkPlugins={[remarkGfm]} skipHtml>{value.task}</Markdown>{task&&task!==value.task&&<details><summary>给此 Agent 的指令</summary><Markdown skipHtml>{task}</Markdown></details>}</article>
  <InputCards sections={value.request_sections} names={names} actual={value.actual_request_recorded}/>
  <ToolActivity items={value.activity}/>
  {value.activity_history_status==='unavailable'&&<small className="hint">本轮工具历史范围无法核验；未读取其他回合补齐。</small>}
  <ConversationMessages messages={value.messages.filter(m=>m.role==='assistant'&&m.text.trim()!=='No response requested.')} answer={value.saved_answer}/>
  {value.summaries.length>0&&<details className="reasoning-summary"><summary>Provider 公开推理摘要</summary>{value.summaries.map((s,i)=><Markdown key={i} skipHtml>{s.text}</Markdown>)}</details>}
  {['STARTING','RUNNING'].includes(value.state)&&<div className="live-status" role="status"><i/>{value.connection_check?'正在检查 Agent 连接':value.process_alive?'Agent 原生进程运行中':'正在准备 Agent'} · {value.activity.length?'工具活动随原生记录更新':'等待 Provider 提供可见输出'}</div>}
  {value.history_status==='unavailable'&&<small className="hint">原生记录目前不可读；已保存的运行回答单独标明。</small>}
 </section>;
}
