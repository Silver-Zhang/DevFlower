import React,{useEffect,useRef,useState} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {History,Plus,Settings2,Sparkles,X} from 'lucide-react';
import {ProposalDiff} from '../../studio/workbench_ui/ProposalDiff.jsx';
import {PanelEmpty} from './WorkbenchFeedback.jsx';
import {AssistantTeam} from './AssistantTeam.jsx';
import {AssistantRepair} from './AssistantRepair.jsx';
import {AssistantSourcePicker} from './AssistantSourcePicker.jsx';
import {AssistantModelSettings,choicesFor,emptySelection,providerNames} from './AssistantModelSettings.jsx';

const uid=()=>crypto.randomUUID().replaceAll('-','');
const active=new Set(['SUBMITTING','RESERVED','RUNNING']);
const labels={SUBMITTING:'正在登记本次发送',RESERVED:'等待原生执行',RUNNING:'正在回答',
 COMPLETED:'已收到原生回执',NOT_SENT:'未发送',FAILED:'原生终结失败',RECOVERY_REQUIRED:'发送结果待核验'};
const outcomes={VERIFICATION_FAILED:'校验失败',REJECT:'Gate 拒绝',ACCEPT_WITH_LIMITATIONS:'Gate 有条件接受'};
const route=(name,query)=>'assistant/'+name+(query?'?'+new URLSearchParams(query):'');
const eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const pendingRequests=new Map();
const probeText='连接测试：请只回复 DEVFLOWER_ASSISTANT_OK。';
const pendingKey=(key,id)=>key+':pending:'+id;
function loadPending(key){
 const intent=sessionStorage.getItem(key);
 return pendingRequests.get(key)||(intent?{intent}:null);
}
function clearPending(key){
 pendingRequests.delete(key);sessionStorage.removeItem(key);
}

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
   {row.diagnostic_outcome&&<p>{outcomes[row.diagnostic_outcome]}{row.event_seq!=null&&' · 事件 #'+row.event_seq}</p>}
   {row.reason&&<p>{row.reason}{row.feedback_truncated&&' …'}</p>}<p>{row.advice}</p>
   {row.upstream.length>0&&<details><summary>实际记录的上游交接</summary><pre>{JSON.stringify(row.upstream,null,2)}</pre></details>}
   {row.evidence&&Object.keys(row.evidence).length>0&&<details><summary>实际请求、配置与错误回执</summary>
    {row.evidence.configuration&&<pre>{JSON.stringify(row.evidence.configuration,null,2)}</pre>}
    {row.evidence.actual_request&&<><strong>实际 Prompt</strong><pre>{row.evidence.actual_request.text}</pre></>}
    {row.evidence.inputs&&<><strong>实际交接内容</strong><pre>{row.evidence.inputs.text}</pre></>}
    {row.evidence.failure&&<pre>{JSON.stringify(row.evidence.failure,null,2)}</pre>}
    {row.evidence.provider_receipt&&<pre>{JSON.stringify(row.evidence.provider_receipt,null,2)}</pre>}
   </details>}
   {row.inputs_paged&&<p>大输入可在运行检查器中分段查看。</p>}
  </article>)}
 </section>;
}

export function Assistant({api,workspace,storageKey,providers,catalog,onDiscover,draft,files,onFiles,
 run,onRun,runs,onApply,onSaveTeam,onOpenDraft,onOpenRun,onClose}){
 const storeKey='devflower-v2-assistant:'+storageKey+':'+workspace.id;
 const [conversation,setConversation]=useState(()=>localStorage.getItem(storeKey)||uid());
 const [view,setView]=useState('chat'),[record,setRecord]=useState(null),[history,setHistory]=useState([]);
 const [before,setBefore]=useState(null),[diagnosis,setDiagnosis]=useState(null);
 const [settings,setSettings]=useState(null),[selection,setSelection]=useState(emptySelection);
 const [legacy,setLegacy]=useState(providers.length>0),[legacyProvider,setLegacyProvider]=useState(providers.includes('codex')?'codex':providers[0]||'');
 const [legacyModel,setLegacyModel]=useState(''),[legacyEffort,setLegacyEffort]=useState('');
 const {catalog:configuredCatalog,chosen,canSend}=choicesFor(settings,selection);
 const [mode,setMode]=useState(run?'diagnosis':'chat'),[attachDraft,setAttachDraft]=useState(false);
 const [inventory,setInventory]=useState(true);
 const [text,setText]=useState(''),[preview,setPreview]=useState(null),[pending,setPending]=useState(null);
 const [retry,setRetry]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(false),sequence=useRef(0),loadFlight=useRef(false),loaded=useRef(null);
 const operation=useRef(false),closing=useRef(null),latest=useRef(null),pendingRef=useRef(null);
 const selectedModel=catalog?.models?.find(item=>item.id===legacyModel);
 const config=legacy?{provider:legacyProvider,model:legacyProvider==='codex'?legacyModel:null,
  effort:legacyProvider==='codex'?legacyEffort:null}:selection;
 const payload={workspace:workspace.id,conversation,...config,mode:mode==='diagnosis'?'chat':mode,
  text,draft:attachDraft?draft:null,files,run:run||null,...(legacy?{}:{inventory})};
 latest.current=payload;
 const turn=record?.turns?.at(-1),running=active.has(turn?.state),unknown=turn?.state==='RECOVERY_REQUIRED';
 const matching=preview&&(preview.payload.mode==='probe'?
  ['provider','native_profile','model','effort'].every(key=>preview.payload[key]===selection[key]):
  eq(preview.payload,payload));
 const intentStore=pendingKey(storeKey,conversation);
 useEffect(()=>{
  alive.current=true;closing.current?.focus();
  api(route('settings',{workspace:workspace.id})).then(result=>{
   if(!alive.current)return;
   setSettings(result);
   if(result.preferences){setSelection(result.preferences);setLegacy(false)}
   else if(!providers.length&&!run&&!loadPending(pendingKey(storeKey,conversation)))setView('settings');
  },failure=>{if(alive.current)setError(failure.message)});
  return()=>{alive.current=false;sequence.current++};
 },[]);
 useEffect(()=>{
  localStorage.setItem(storeKey,conversation);
  const stored=loadPending(intentStore);
  sequence.current++;loaded.current=null;setRecord(null);pendingRef.current=stored;setPending(stored);setRetry(false);loadFlight.current=false;
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
  if(!running||view==='history')return;
  const timer=setInterval(()=>read(conversation).catch(failure=>alive.current&&setError(failure.message)),1100);
  return()=>clearInterval(timer);
 },[running,view,conversation]);
 useEffect(()=>{
  if(!draft&&attachDraft){setAttachDraft(false);if(mode==='proposal')setMode('chat')}
 },[Boolean(draft)]);
 async function read(id=conversation){
  if(loadFlight.current)return loaded.current;
  const ticket=sequence.current;loadFlight.current=true;
  try{
   const value=await api(route('conversation',{workspace:workspace.id,conversation:id}));
   if(alive.current&&ticket===sequence.current){
    loaded.current=value;setRecord(value);
    if(pendingRef.current&&value.turns.some(item=>item.intent===pendingRef.current.intent)){
     clearPending(pendingKey(storeKey,id));pendingRef.current=null;setPending(null);setRetry(false);setPreview(null);setText('');
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
 async function showPreview(test=false){
  const current=structuredClone(test?{workspace:workspace.id,conversation,...selection,identity:null,
   mode:'probe',text:probeText,draft:null,files:[],run:null}:payload);
  const result=await api(route('preview'),current);
  if(alive.current&&(test||eq(latest.current,current))){
   setPreview({...result,payload:current});
   if(test)setView('chat');
  }
 }
 function changeConfig(value){
  setSelection(value);setLegacy(false);setPreview(null);
 }
 async function refreshModels(){
  const result=await api(route('refresh_models'),{workspace:workspace.id,provider:selection.provider,native_profile:selection.native_profile});
  if(alive.current){setSettings(result);setLegacy(false)}
 }
 async function saveConfig(){
  const result=await api(route('configure'),{workspace:workspace.id,...selection});
  if(alive.current){setSettings(result);setLegacy(false);setNotice('已保存当前工作空间的助手偏好。')}
 }
 async function submit(submitted){
  pendingRequests.set(intentStore,submitted);sessionStorage.setItem(intentStore,submitted.intent);
  pendingRef.current=submitted;setPending(submitted);setRetry(false);
  try{
   await api(route('send'),submitted);
   if(alive.current){setPreview(null);setText('');await read()}
  }catch(failure){
   if(!failure.uncertain&&Number.isInteger(failure.status)&&failure.status<500){
    clearPending(intentStore);
    if(alive.current){pendingRef.current=null;setPending(null)}
   }
   throw failure;
  }
 }
 async function send(){
  if(matching)await submit({...preview.payload,intent:uid(),preview_sha256:preview.sha256});
 }
 async function verifyPending(){
  const result=await read();
  if(result&&pending&&!result.turns.some(item=>item.intent===pending.intent)){
   if(pending.preview_sha256)setRetry(true);
   else setNotice('当前未查到原意图的回执。仍可重新核对，或另开对话。');
  }
 }
 const valid=(legacy?!!legacyProvider&&(legacyProvider!=='codex'||(legacyModel&&legacyEffort)):canSend)
  &&text.trim()&&(!attachDraft||draft)&&(mode!=='proposal'||attachDraft)&&(mode!=='diagnosis'||run);
 return <aside className="project-panel v2-assistant" aria-label="AI 助手" onKeyDown={event=>{
  if(event.key==='Escape'){event.stopPropagation();onClose()}
 }}>
  <header className="project-panel-header">
   <div><h2><Sparkles size={15}/> AI 助手</h2><p className="muted small">{workspace.name} · 独立对话</p></div>
   <button aria-label="新建助手对话" title="新建助手对话" disabled={busy} onClick={()=>switchTo(uid())}><Plus size={16}/></button>
   <button aria-label="助手会话历史" title="助手会话历史" aria-pressed={view==='history'} disabled={busy}
    onClick={()=>{setView(view==='history'?'chat':'history');if(view!=='history')perform(()=>loadHistory())}}><History size={16}/></button>
   <button aria-label="助手模型设置" title="助手模型设置" aria-pressed={view==='settings'} disabled={busy}
    onClick={()=>setView(view==='settings'?'chat':'settings')}><Settings2 size={16}/></button>
   <button ref={closing} aria-label="关闭 AI 助手" title="关闭 AI 助手" onClick={onClose}><X size={16}/></button>
  </header>
  {view==='settings'?<div className="v2-assistant-scroll">
   <AssistantModelSettings settings={settings} selection={selection} onChange={changeConfig} turn={turn}
    busy={busy||running||unknown||!!pending} onRefresh={()=>perform(refreshModels)} onSave={()=>perform(saveConfig)}
    onTest={()=>perform(()=>showPreview(true))}/>
  </div>:view==='history'?<section className="assistant-history" aria-label="助手会话列表">
   {!history.length&&<PanelEmpty icon={History} title="还没有助手对话"><p>切回对话即可开始提问或准备画布提案。</p></PanelEmpty>}
   {history.map(row=><button key={row.id} onClick={()=>switchTo(row.id)}><span>{row.title}</span>
    <small>{new Date(row.updated_at*1000).toLocaleString()}</small></button>)}
   {before&&<button disabled={busy} onClick={()=>perform(()=>loadHistory(before))}>更早的助手对话</button>}
  </section>:<>
   <div className="v2-assistant-scroll">
    <Diagnosis value={diagnosis}/>
    {diagnosis&&<AssistantRepair key={diagnosis.run} api={api} workspace={workspace.id} diagnosis={diagnosis}
     agents={settings?.identities||[]} files={files} runs={runs} perform={perform} busy={busy} onRun={onOpenRun}/>}
    <section role="log" aria-label="助手对话消息">
     {!record?.turns?.length&&!matching&&<PanelEmpty icon={Sparkles} title="与助手一起准备工作流"
      actions={(providers.length>0||canSend)&&<button disabled={busy||running||unknown||!!pending}
       onClick={()=>setText(mode==='team'?'创建 Developer、Tester 和 Reviewer 团队。测试失败返回 Developer，审核通过后结束。':
        mode==='proposal'?'检查当前草稿的节点与交接，并建议一项可以审阅的改动。':
        mode==='diagnosis'?'为什么这个工作流执行失败了？请根据运行证据定位问题。':
        '这个项目有哪些 Agent 和工作流？请结合当前草稿说明运行逻辑。')}>使用示例问题</button>}>
      <p>{providers.length||canSend?'提出问题或准备画布提案。发送前可以核对附件和内容。':
       '可先阅读运行诊断。发送消息前，请在助手模型设置中刷新目录并选择原生 CLI。'}</p>
     </PanelEmpty>}
     {record?.turns?.map(item=><article className="v2-assistant-turn" key={item.id}>
      <p className="assistant-user">{item.text}</p>
      <small>{item.provider} · {labels[item.state]||item.state}</small>
      {item.reply&&(['proposal','team'].includes(item.mode)?<details><summary>查看原生提案回复</summary><pre>{item.reply}</pre></details>:
       <Markdown skipHtml remarkPlugins={[remarkGfm]}>{item.reply}</Markdown>)}
      {item.receipt&&<details><summary>Provider 回执</summary><dl>
       <dt>请求模型 / 强度</dt><dd>{item.receipt.requested_model||'原生默认'} / {item.receipt.requested_effort||'原生默认'}</dd>
       <dt>实际模型 / 强度</dt><dd>{item.receipt.resolved_model||'未报告'} / {item.receipt.resolved_effort||'未报告'}</dd>
       <dt>原生会话</dt><dd><code>{item.receipt.session_id}</code></dd>
      </dl></details>}
      {item.reply_truncated&&<p className="field-note">回复显示已按字节预算截取。</p>}
      {item.reason&&<p role="status">{item.reason}</p>}
      {item.prompt&&<details><summary>{item.state==='COMPLETED'?'核验回执对应的可见输入':'本次确认的可见输入'}</summary><pre>{item.prompt}</pre>
       <small>SHA-256 <code>{item.prompt_sha256}</code></small></details>}
     </article>)}
    </section>
    {turn?.team&&<AssistantTeam key={turn.id} api={api} workspace={workspace.id} conversation={conversation}
     turn={turn} draft={draft} busy={busy} perform={perform} onSaved={onSaveTeam} onOpenDraft={onOpenDraft}/>}
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
     <h3>{preview.payload.mode==='probe'?'真实推理测试':'发送预览'}</h3>
     <p>将经本机 {providerNames[preview.payload.provider]} 使用已配置的服务。{preview.payload.mode==='probe'?'测试可能计费。':''}</p>
     {preview.context.config&&<p>{preview.context.config.native_profile||'默认 HOME'} · {preview.context.config.requested_model||'原生默认模型'} · {preview.context.config.requested_effort||'原生默认强度'}</p>}
     <p>附加草稿 {preview.context.draft?'1':'0'}、文件 {preview.context.files.length}、运行 {preview.context.run?'1':'0'}、已核验历史 {preview.context.previous_turns} 轮。</p>
     {preview.context.inventory&&<p>已附加 {preview.context.inventory.agents} 个 Agent、{preview.context.inventory.workflows} 个工作流的概览。</p>}
     {preview.context.files.length>0&&<ul>{preview.context.files.map(file=><li key={file.path}>
      {file.path}:{file.start_line}–{file.end_line}{file.truncated?' · 节选':''}</li>)}</ul>}
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
     {legacy?<label>Provider<select aria-label="助手 Provider" value={legacyProvider} disabled={busy||running} onChange={event=>setLegacyProvider(event.target.value)}>
      {providers.map(key=><option key={key} value={key}>{providerNames[key]}</option>)}
     </select></label>:<div><small>当前模型</small><br/>
      <button disabled={busy} onClick={()=>setView('settings')}>{providerNames[selection.provider]} · {chosen?.id||'原生默认'}</button>
      {!configuredCatalog?.refreshed_at&&<small> · 待配置</small>}</div>}
     <label>工作方式<select aria-label="助手工作方式" value={mode} disabled={busy||running} onChange={event=>{
      setMode(event.target.value);if(event.target.value==='proposal')setAttachDraft(true)
     }}><option value="chat">对话与项目理解</option><option value="team" disabled={legacy}>创建或修改 Agent 团队</option>
      <option value="proposal" disabled={!draft}>画布提案</option><option value="diagnosis">运行诊断与授权修复</option></select></label>
    </div>
    {legacy&&legacyProvider==='codex'&&<div className="assistant-selects">
     <label>模型<select aria-label="助手模型" value={legacyModel} disabled={busy||running} onChange={event=>{setLegacyModel(event.target.value);setLegacyEffort('')}}>
      <option value="">请选择</option>{catalog?.models?.map(row=><option key={row.id} value={row.id}>{row.name||row.id}</option>)}
     </select></label>
     <label>推理强度<select aria-label="助手推理强度" value={legacyEffort} disabled={busy||running} onChange={event=>setLegacyEffort(event.target.value)}>
      <option value="">请选择</option>{(selectedModel?.efforts||[]).filter(key=>key!=='ultra').map(key=><option key={key}>{key}</option>)}
     </select></label>
     <button disabled={busy||running} onClick={()=>perform(onDiscover)}>刷新模型</button>
    </div>}
    <details open={mode==='diagnosis'?true:undefined}>
     <summary>本次上下文（{files.length} 个文件{run?'、1 次运行':''}{attachDraft?'、当前草稿':''}）</summary>
     <label className="assistant-checkbox"><input type="checkbox" disabled={!draft||mode==='proposal'||busy||running}
      checked={attachDraft} onChange={event=>setAttachDraft(event.target.checked)}/>附加当前草稿</label>
     {!legacy&&<label className="assistant-checkbox"><input type="checkbox" disabled={busy||running}
      checked={inventory} onChange={event=>setInventory(event.target.checked)}/>附加工作区 Agent 与工作流概览</label>}
     <label className="assistant-run-choice">运行证据<select aria-label="附加运行证据" value={run||''} disabled={busy||running} onChange={event=>onRun(event.target.value||null)}>
      <option value="">不附加</option>{runs.map(row=><option key={row.id} value={row.id}>{row.name} · {new Date(row.created_at*1000).toLocaleTimeString()}</option>)}
     </select></label>
     <AssistantSourcePicker api={api} workspace={workspace.id} files={files} onFiles={onFiles}
      busy={busy||running} perform={perform}/>
    </details>
    <textarea aria-label="助手消息" value={text} rows={3} maxLength={8000} disabled={busy||running||unknown||!!pending}
     onChange={event=>setText(event.target.value)} placeholder="输入问题或画布修改要求"/>
    <button className="primary assistant-preview-action" disabled={!valid||busy||running||unknown||!!pending} onClick={()=>perform(()=>showPreview())}>预览本次发送</button>
   </section>
  </>}
  {error&&<p className="error-text assistant-feedback" role="alert">{error}</p>}
  {notice&&<p className="assistant-feedback" role="status">{notice}</p>}
 </aside>;
}
