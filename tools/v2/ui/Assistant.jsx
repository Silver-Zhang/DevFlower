import React,{useEffect,useRef,useState} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {History,Plus,Sparkles,X} from 'lucide-react';
import {ProposalDiff} from '../../studio/workbench_ui/ProposalDiff.jsx';

const uid=()=>crypto.randomUUID().replaceAll('-','');
const active=new Set(['SUBMITTING','RESERVED','RUNNING']);
const labels={SUBMITTING:'正在登记本次发送',RESERVED:'等待原生执行',RUNNING:'正在回答',
 COMPLETED:'已收到原生回执',NOT_SENT:'未发送',RECOVERY_REQUIRED:'发送结果待核验'};
const route=(name,query)=>'assistant/'+name+(query?'?'+new URLSearchParams(query):'');
const eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

function Diagnosis({value}){
 if(!value)return null;
 return <section className="assistant-diagnosis" aria-label="运行只读诊断">
  <h3>运行诊断</h3><p>{value.state} · {value.reason||'无额外运行原因'}</p>
  {value.resume_available&&<p>此检查点可以从运行状态区显式恢复。</p>}
  {!value.nodes.length&&<p>此运行未记录失败、跳过或待核验节点。</p>}
  {value.recent_error_events?.length>0&&<details><summary>运行检查器中的错误事件</summary>
   <ul>{value.recent_error_events.map(event=><li key={event.seq}>#{event.seq} · {event.node||'运行级'} · {event.kind}</li>)}</ul>
  </details>}
  {value.nodes.map(row=><article key={row.activation}>
   <strong>{row.node} · {row.state}{row.iteration_path.length?' · 第 '+row.iteration_path.join('/')+' 轮':''}</strong>
   {row.reason&&<p>{row.reason}</p>}<p>{row.advice}</p>
   {row.upstream.length>0&&<details><summary>实际记录的上游交接</summary><pre>{JSON.stringify(row.upstream,null,2)}</pre></details>}
   {row.inputs_paged&&<p>大输入可在运行检查器中分段查看。</p>}
  </article>)}
 </section>;
}

export function Assistant({api,workspace,storageKey,providers,catalog,onDiscover,draft,files,onFiles,
 run,onRun,runs,onApply,onClose}){
 const storeKey='devflower-v2-assistant:'+storageKey+':'+workspace.id;
 const [conversation,setConversation]=useState(()=>localStorage.getItem(storeKey)||uid());
 const [view,setView]=useState('chat'),[record,setRecord]=useState(null),[history,setHistory]=useState([]);
 const [before,setBefore]=useState(null),[diagnosis,setDiagnosis]=useState(null);
 const [provider,setProvider]=useState(providers.includes('codex')?'codex':providers[0]||'');
 useEffect(()=>{if(!providers.includes(provider))setProvider(providers[0]||'')},[providers,provider]);
 const [model,setModel]=useState(''),[effort,setEffort]=useState('');
 const [mode,setMode]=useState('chat'),[attachDraft,setAttachDraft]=useState(false),[path,setPath]=useState('');
 const [text,setText]=useState(''),[preview,setPreview]=useState(null),[pending,setPending]=useState(null);
 const [retry,setRetry]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(false),sequence=useRef(0),loadFlight=useRef(false),loaded=useRef(null);
 const operation=useRef(false),closing=useRef(null),latest=useRef(null),pendingRef=useRef(null);
 const selectedModel=catalog?.models?.find(item=>item.id===model);
 const payload={workspace:workspace.id,conversation,provider,model:provider==='codex'?model:null,
  effort:provider==='codex'?effort:null,mode,text,draft:attachDraft?draft:null,files,run:run||null};
 latest.current=payload;
 const turn=record?.turns?.at(-1),running=active.has(turn?.state),unknown=turn?.state==='RECOVERY_REQUIRED';
 const matching=preview&&eq(preview.payload,payload);
 useEffect(()=>{
  alive.current=true;closing.current?.focus();
  return()=>{alive.current=false;sequence.current++};
 },[]);
 useEffect(()=>{
  localStorage.setItem(storeKey,conversation);
  sequence.current++;loaded.current=null;setRecord(null);pendingRef.current=null;setPending(null);setRetry(false);loadFlight.current=false;
  read(conversation).catch(failure=>alive.current&&setError(failure.message));
 },[conversation]);
 useEffect(()=>{
  if(!run){setDiagnosis(null);return}
  let current=true;
  api(route('diagnosis',{workspace:workspace.id,run})).then(result=>{
   if(current)setDiagnosis(result)
  },failure=>{if(current)setError(failure.message)});
  return()=>{current=false;setDiagnosis(null)};
 },[run]);
 useEffect(()=>{
  if(!running||view!=='chat')return;
  const timer=setInterval(()=>read(conversation).catch(failure=>alive.current&&setError(failure.message)),1100);
  return()=>clearInterval(timer);
 },[running,view,conversation]);
 useEffect(()=>{
  if(!draft&&attachDraft){setAttachDraft(false);setMode('chat')}
 },[Boolean(draft)]);
 async function read(id=conversation){
  if(loadFlight.current)return loaded.current;
  const ticket=sequence.current;loadFlight.current=true;
  try{
   const value=await api(route('conversation',{workspace:workspace.id,conversation:id}));
   if(alive.current&&ticket===sequence.current){
    loaded.current=value;setRecord(value);
    if(pendingRef.current&&value.turns.some(item=>item.intent===pendingRef.current.intent)){
     pendingRef.current=null;setPending(null);setRetry(false);setPreview(null);setText('');
    }
   }
   return value;
  }finally{if(ticket===sequence.current)loadFlight.current=false}
 }
 async function perform(fn){
  if(operation.current)return;
  operation.current=true;setBusy(true);setError('');setNotice('');
  try{await fn()}
  catch(failure){if(alive.current)setError(failure.message)}
  finally{operation.current=false;if(alive.current)setBusy(false)}
 }
 function switchTo(id){
  setView('chat');setText('');setPreview(null);setPending(null);setError('');setNotice('');setConversation(id);
 }
 async function loadHistory(cursor=null){
  const result=await api(route('history',{workspace:workspace.id,...(cursor?{before:cursor}:{})}));
  if(alive.current){setHistory(rows=>cursor?[...rows,...result.conversations]:result.conversations);setBefore(result.before)}
 }
 async function showPreview(){
  const current=structuredClone(payload);
  const result=await api(route('preview'),current);
  if(alive.current&&eq(latest.current,current))setPreview({...result,payload:current});
 }
 async function submit(submitted){
  pendingRef.current=submitted;setPending(submitted);setRetry(false);
  try{
   await api(route('send'),submitted);
   if(alive.current){setPreview(null);setText('');await read()}
  }catch(failure){
   if(alive.current&&!failure.uncertain&&Number.isInteger(failure.status)&&failure.status<500){
    pendingRef.current=null;setPending(null);
   }
   throw failure;
  }
 }
 async function send(){
  if(matching)await submit({...preview.payload,intent:uid(),preview_sha256:preview.sha256});
 }
 async function verifyPending(){
  const result=await read();
  if(result&&pending&&!result.turns.some(item=>item.intent===pending.intent))setRetry(true);
 }
 function addFile(){
  const value=path.trim();
  if(value&&!files.includes(value)&&files.length<3){onFiles([...files,value]);setPath('')}
 }
 const valid=!!provider&&text.trim()&&(!attachDraft||draft)&&(mode!=='proposal'||attachDraft)
  &&(provider!=='codex'||(model&&effort));
 return <aside className="project-panel v2-assistant" aria-label="AI 助手" onKeyDown={event=>{
  if(event.key==='Escape'){event.stopPropagation();onClose()}
 }}>
  <header className="project-panel-header">
   <div><h2><Sparkles size={15}/> AI 助手</h2><p className="muted small">{workspace.name} · 独立对话</p></div>
   <button aria-label="新建助手对话" title="新建助手对话" disabled={busy} onClick={()=>switchTo(uid())}><Plus size={16}/></button>
   <button aria-label="助手会话历史" title="助手会话历史" aria-pressed={view==='history'} disabled={busy}
    onClick={()=>{setView(view==='history'?'chat':'history');if(view!=='history')perform(()=>loadHistory())}}><History size={16}/></button>
   <button ref={closing} aria-label="关闭 AI 助手" title="关闭 AI 助手" onClick={onClose}><X size={16}/></button>
  </header>
  {view==='history'?<section className="assistant-history" aria-label="助手会话列表">
   {!history.length&&<p>当前工作空间没有助手对话。</p>}
   {history.map(row=><button key={row.id} onClick={()=>switchTo(row.id)}><span>{row.title}</span>
    <small>{new Date(row.updated_at*1000).toLocaleString()}</small></button>)}
   {before&&<button disabled={busy} onClick={()=>perform(()=>loadHistory(before))}>更早的助手对话</button>}
  </section>:<>
   <div className="v2-assistant-scroll">
    <Diagnosis value={diagnosis}/>
    <section role="log" aria-label="助手对话消息">
     {!record?.turns?.length&&<p className="field-note">可以询问工作流，或生成一份待审阅的画布提案。</p>}
     {record?.turns?.map(item=><article className="v2-assistant-turn" key={item.id}>
      <p className="assistant-user">{item.text}</p>
      <small>{item.provider} · {labels[item.state]||item.state}</small>
      {item.reply&&(item.mode==='proposal'?<details><summary>查看原生提案回复</summary><pre>{item.reply}</pre></details>:
       <Markdown skipHtml remarkPlugins={[remarkGfm]}>{item.reply}</Markdown>)}
      {item.reply_truncated&&<p className="field-note">回复显示已按字节预算截取。</p>}
      {item.reason&&<p role="status">{item.reason}</p>}
      {item.prompt&&<details><summary>{item.state==='COMPLETED'?'核验回执对应的可见输入':'本次确认的可见输入'}</summary><pre>{item.prompt}</pre>
       <small>SHA-256 <code>{item.prompt_sha256}</code></small></details>}
     </article>)}
    </section>
    {turn?.proposal_error&&<p role="alert" className="error-text">{turn.proposal_error}</p>}
    {turn?.proposal&&<section className="assistant-proposal" aria-label="画布提案">
     <h3>画布提案</h3><p>{turn.proposal.summary}</p>
     <ProposalDiff before={turn.proposal.before} after={turn.proposal.after}/>
     <details><summary>展开变更前后内容</summary><pre>{JSON.stringify({before:turn.proposal.before,after:turn.proposal.after},null,2)}</pre></details>
     <button className="primary" disabled={!draft||busy} onClick={()=>perform(async()=>{
      await onApply({workspace:workspace.id,conversation,turn:turn.id,draft});
      if(alive.current)setNotice('已应用到当前草稿，可以撤销。')
     })}>应用到画布草稿</button>
    </section>}
    {matching&&<section className="assistant-preview" aria-label="本次发送预览">
     <h3>发送预览</h3><p>将经 {provider==='codex'?'本机 Codex CLI':'本机 Claude CLI'} 使用已配置的服务。</p>
     <p>附加草稿 {preview.context.draft?'1':'0'}、文件 {preview.context.files.length}、运行 {preview.context.run?'1':'0'}、已核验历史 {preview.context.previous_turns} 轮。</p>
     <details open><summary>本次发送的可见内容</summary><pre>{preview.prompt}</pre></details>
     <button className="primary" disabled={busy||!!pending} onClick={()=>perform(send)}>确认发送</button>
     <button disabled={busy} onClick={()=>setPreview(null)}>关闭发送预览</button>
    </section>}
   </div>
   <section className="assistant-composer" aria-label="助手发送">
    {pending&&<button disabled={busy} onClick={()=>perform(verifyPending)}>核对本次发送</button>}
    {pending&&<p className="field-note">正在核对原意图。查询不会再次发送。</p>}
    {pending&&retry&&<button disabled={busy} onClick={()=>perform(()=>submit(pending))}>重新提交原意图</button>}
    {unknown&&<p className="field-note">本对话的发送结果待核验。可以另开对话。</p>}
    <div className="assistant-selects">
     <label>Provider<select aria-label="助手 Provider" value={provider} disabled={busy||running} onChange={event=>setProvider(event.target.value)}>
      {!providers.length&&<option value="">原生发送未启用</option>}
      {providers.map(key=><option key={key} value={key}>{key==='codex'?'Codex CLI':'Claude CLI'}</option>)}
     </select></label>
     <label>工作方式<select aria-label="助手工作方式" value={mode} disabled={busy||running} onChange={event=>{
      setMode(event.target.value);if(event.target.value==='proposal')setAttachDraft(true)
     }}><option value="chat">独立问答</option><option value="proposal" disabled={!draft}>画布提案</option></select></label>
    </div>
    {provider==='codex'&&<div className="assistant-selects">
     <label>模型<select aria-label="助手模型" value={model} disabled={busy||running} onChange={event=>{setModel(event.target.value);setEffort('')}}>
      <option value="">请选择</option>{catalog?.models?.map(row=><option key={row.id} value={row.id}>{row.name||row.id}</option>)}
     </select></label>
     <label>推理强度<select aria-label="助手推理强度" value={effort} disabled={busy||running} onChange={event=>setEffort(event.target.value)}>
      <option value="">请选择</option>{(selectedModel?.efforts||[]).filter(key=>key!=='ultra').map(key=><option key={key}>{key}</option>)}
     </select></label>
     <button disabled={busy||running} onClick={()=>perform(onDiscover)}>刷新模型</button>
    </div>}
    <details>
     <summary>本次上下文（{files.length} 个文件{run?'、1 次运行':''}{attachDraft?'、当前草稿':''}）</summary>
     <label className="assistant-checkbox"><input type="checkbox" disabled={!draft||mode==='proposal'||busy||running}
      checked={attachDraft} onChange={event=>setAttachDraft(event.target.checked)}/>附加当前草稿</label>
     <label className="assistant-run-choice">运行证据<select aria-label="附加运行证据" value={run||''} disabled={busy||running} onChange={event=>onRun(event.target.value||null)}>
      <option value="">不附加</option>{runs.map(row=><option key={row.id} value={row.id}>{row.name} · {new Date(row.created_at*1000).toLocaleTimeString()}</option>)}
     </select></label>
     <div className="assistant-file-input"><input aria-label="工作区相对文件路径" placeholder="例如 docs/plan.md" maxLength={512}
      value={path} disabled={busy||running} onChange={event=>setPath(event.target.value)}/>
      <button disabled={!path.trim()||files.length>=3||busy||running} onClick={addFile}>附加文件</button></div>
     <ul>{files.map(file=><li key={file}><span>{file}</span><button aria-label={'移除附件 '+file} disabled={busy||running}
      onClick={()=>onFiles(files.filter(item=>item!==file))}>移除</button></li>)}</ul>
    </details>
    <textarea aria-label="助手消息" value={text} rows={3} maxLength={8000} disabled={busy||running||unknown||!!pending}
     onChange={event=>setText(event.target.value)} placeholder="输入问题或画布修改要求"/>
    <button disabled={!valid||busy||running||unknown||!!pending} onClick={()=>perform(showPreview)}>预览本次发送</button>
   </section>
  </>}
  {error&&<p className="error-text assistant-feedback" role="alert">{error}</p>}
  {notice&&<p className="assistant-feedback" role="status">{notice}</p>}
 </aside>;
}
