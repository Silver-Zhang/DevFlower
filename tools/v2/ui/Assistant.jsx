import React,{useEffect,useRef,useState} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {History,Plus,Settings2,Sparkles,X} from 'lucide-react';
import {ProposalDiff} from '../../studio/workbench_ui/ProposalDiff.jsx';
import {NativeProposal} from './NativeProposal.jsx';
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
const draftKey=(key,id)=>key+':draft:'+id;
function loadPending(key){
 const stored=sessionStorage.getItem(key);
 if(pendingRequests.has(key))return pendingRequests.get(key);
 if(!stored)return null;
 try{return JSON.parse(stored)}catch{return {intent:stored}}
}
function clearPending(key){
 pendingRequests.delete(key);sessionStorage.removeItem(key);
}

function Diagnosis({value}){
 if(!value)return null;
 return <section className="assistant-diagnosis" aria-label="运行只读诊断">
  <h3>运行诊断</h3><p>{value.state} · {value.reason||'无额外运行原因'}</p>
  {value.execution==='native-cli'&&<><p className="field-note">运行 <code>{value.run}</code></p>
   {value.failure_facts?.map(fact=><p key={fact.event_seq}>{fact.node||'运行级'} ·
    {fact.http_status?' HTTP '+fact.http_status:' '+fact.code} · {fact.source} #{fact.event_seq}</p>)}
   {!!value.failure_facts_omitted&&<p className="field-note">省略 {value.failure_facts_omitted} 条结构化错误。</p>}
   {value.evidence_insufficient&&<p className="field-note">本次冻结运行证据不足，仍需核验最终状态和原因。</p>}
   {!!value.not_observed_started?.length&&<p>未观察到进程启动的节点 · {value.not_observed_started.join('、')}</p>}
   {!!value.loop_rounds?.length&&<details><summary>循环轮次与审核结果</summary><pre>{JSON.stringify(value.loop_rounds,null,2)}</pre></details>}
  </>}
  {value.resume_available&&<p>此检查点可以从运行状态区显式恢复。</p>}
  {!value.nodes.length&&<p>{value.execution==='native-cli'?'此运行尚未读到节点进程证据。':'此运行未记录失败、跳过或待核验节点。'}</p>}
  {value.recent_error_events?.length>0&&<details><summary>运行检查器中的错误事件</summary>
   <ul>{value.recent_error_events.map(event=><li key={event.seq}>#{event.seq} · {event.node||'运行级'} · {event.kind}</li>)}</ul>
  </details>}
  {value.nodes.map(row=><article key={row.activation}>
   <strong>{row.node} · {row.state}{row.iteration_path.length?' · 第 '+row.iteration_path.join('/')+' 轮':''}</strong>
   {row.diagnostic_outcome&&<p>{outcomes[row.diagnostic_outcome]}{row.event_seq!=null&&' · 事件 #'+row.event_seq}</p>}
   {row.reason&&<p>{row.reason}{row.feedback_truncated&&' …'}</p>}
   {row.returncode!=null&&<p>进程退出码 {row.returncode}</p>}
   {row.advice&&<p>{row.advice}</p>}
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
 run,onRun,runs,onApply,onSaveTeam,onOpenDraft,onOpenRun,onClose,nativeEntry,settingsEntry,onEditRunDraft,hidden}){
 const storeKey='devflower-v2-assistant:'+storageKey+':'+workspace.id;
 const [conversation,setConversation]=useState(()=>localStorage.getItem(storeKey)||uid());
 const [view,setView]=useState(settingsEntry?'settings':'chat'),[record,setRecord]=useState(null),[history,setHistory]=useState([]);
 const [before,setBefore]=useState(null),[diagnosis,setDiagnosis]=useState(null);
 const [settings,setSettings]=useState(null),[selection,setSelection]=useState(emptySelection);
 const [legacy,setLegacy]=useState(!nativeEntry&&providers.length>0),[legacyProvider,setLegacyProvider]=useState(providers.includes('codex')?'codex':providers[0]||'');
 const [legacyModel,setLegacyModel]=useState(''),[legacyEffort,setLegacyEffort]=useState('');
 const {catalog:configuredCatalog,chosen,canSend}=choicesFor(settings,selection);
 const [mode,setMode]=useState(nativeEntry?'native':run?'diagnosis':'chat'),[attachDraft,setAttachDraft]=useState(false);
 const [inventory,setInventory]=useState(true),[correction,setCorrection]=useState(null);
 const [text,setDraftText]=useState(()=>sessionStorage.getItem(draftKey(storeKey,conversation))||''),[preview,setPreview]=useState(null),[pending,setPending]=useState(null);
 const historySelection=useRef(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(false),sequence=useRef(0),loadFlight=useRef(null);
 const operation=useRef(false),closing=useRef(null),latest=useRef(null),pendingRef=useRef(null);
 const selectedModel=catalog?.models?.find(item=>item.id===legacyModel);
 const config=legacy?{provider:legacyProvider,model:legacyProvider==='codex'?legacyModel:null,
  effort:legacyProvider==='codex'?legacyEffort:null}:selection;
 const currentDraft=mode==='native'?draft:attachDraft?draft:null;
 const payload={workspace:workspace.id,conversation,...config,mode:mode==='diagnosis'?'chat':mode,
  text,draft:currentDraft,files,run:run||null,...(legacy?{}:{inventory}),
  ...(correction&&mode==='native'?{correction}:{})};
 latest.current=payload;
 const turn=record?.turns?.at(-1),running=active.has(turn?.state),unknown=turn?.state==='RECOVERY_REQUIRED';
 const [proposalStatus,setProposalStatus]=useState(null),[designOnly,setDesignOnly]=useState(null);
 const proposalDraft=turn?.proposal&&draft?JSON.stringify(draft):null;
 const proposalKey=proposalDraft&&turn.id+' '+proposalDraft;
 const proposalCurrent=proposalKey&&proposalStatus?.key===proposalKey&&proposalStatus.current;
 useEffect(()=>{
  if(!proposalKey)return;
  let current=true;
  api(route('proposal_status'),{workspace:workspace.id,conversation,turn:turn.id,draft:JSON.parse(proposalDraft)})
   .then(value=>{if(current)setProposalStatus({key:proposalKey,current:value.current})})
   .catch(()=>{if(current)setProposalStatus({key:proposalKey,current:false})});
  return()=>{current=false};
 },[proposalKey,workspace.id,conversation,api]);
 const originalAccount=turn&&choicesFor(settings,turn.config||{provider:turn.provider}).catalog;
 const loginMissing=originalAccount?.authenticated===false;
 const refreshModelsStatus=()=>{
  api(route('settings',{workspace:workspace.id})).then(value=>{if(alive.current)setSettings(value)}).catch(()=>{});
 };
 useEffect(()=>{
  if(hidden)return;
  const check=()=>refreshModelsStatus();
  addEventListener('focus',check);
  const interval=setInterval(check,15000);
  return()=>{removeEventListener('focus',check);clearInterval(interval)};
 },[hidden,workspace.id,api]);
 const matching=preview&&(preview.payload.mode==='probe'?
  ['provider','native_profile','model','effort'].every(key=>preview.payload[key]===selection[key]):
  eq(preview.payload,payload));
 const intentStore=pendingKey(storeKey,conversation);
 function setText(value,id=conversation){
  if(value)sessionStorage.setItem(draftKey(storeKey,id),value);
  else sessionStorage.removeItem(draftKey(storeKey,id));
  setDraftText(value);
 }
 useEffect(()=>{
  alive.current=true;closing.current?.focus();
  api(route('settings',{workspace:workspace.id})).then(result=>{
   if(!alive.current)return;
   setSettings(result);
   if(result.preferences){setSelection(result.preferences);setLegacy(false)}
   else if(!providers.length&&!run&&!loadPending(pendingKey(storeKey,conversation))&&!nativeEntry)setView('settings');
  },failure=>{if(alive.current)setError(failure.message)});
  return()=>{alive.current=false;sequence.current++};
 },[]);
 useEffect(()=>{
  localStorage.setItem(storeKey,conversation);
  const stored=loadPending(intentStore);
  sequence.current++;setRecord(null);pendingRef.current=stored;setPending(stored);loadFlight.current=null;
  setDraftText(sessionStorage.getItem(draftKey(storeKey,conversation))||'');
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
 useEffect(()=>{if(settingsEntry)setView('settings')},[settingsEntry]);
 useEffect(()=>{if(nativeEntry){setLegacy(false);setMode('native')}},[nativeEntry]);
 useEffect(()=>{
  if(!running||view==='history')return;
  const timer=setInterval(()=>read(conversation).catch(failure=>alive.current&&setError(failure.message)),1100);
  return()=>clearInterval(timer);
 },[running,view,conversation]);
 useEffect(()=>{
  if(!draft&&attachDraft){setAttachDraft(false);if(mode==='proposal')setMode('chat')}
 },[Boolean(draft)]);
 function read(id=conversation){
  if(loadFlight.current)return loadFlight.current;
  const ticket=sequence.current;
  const request=api(route('conversation',{workspace:workspace.id,conversation:id})).then(value=>{
   if(alive.current&&ticket===sequence.current){
    setRecord(value);
    if(historySelection.current===id){
     historySelection.current=null;restoreLegacy(value.turns.at(-1));
    }
    if(pendingRef.current&&value.turns.some(item=>item.intent===pendingRef.current.intent)){
     const request=pendingRef.current;
     clearPending(pendingKey(storeKey,id));pendingRef.current=null;setPending(null);setPreview(null);
     if(request.mode!=='probe'&&sessionStorage.getItem(draftKey(storeKey,id))===request.text)setText('',id);
    }
   }
   return value;
  }).finally(()=>{if(loadFlight.current===request)loadFlight.current=null});
  loadFlight.current=request;
  return request;
 }
 async function perform(fn){
  if(operation.current)return;
  operation.current=true;setBusy(true);setError('');setNotice('');
  try{await fn()}
  catch(failure){if(alive.current)setError(failure.message)}
  finally{operation.current=false;if(alive.current)setBusy(false)}
 }
 function restoreLegacy(item){
  if(item&&!item.config&&providers.includes(item.provider)){
   setLegacy(true);setLegacyProvider(item.provider);setMode('chat');setAttachDraft(false);
  }
 }
 function switchTo(id,fromHistory=false){
  historySelection.current=fromHistory&&id!==conversation?id:null;
  if(fromHistory&&id===conversation)restoreLegacy(record?.turns.at(-1));
  setCorrection(null);setView('chat');setDraftText(sessionStorage.getItem(draftKey(storeKey,id))||'');setPreview(null);setPending(null);setError('');setNotice('');setConversation(id);
 }
 async function loadHistory(cursor=null){
  const result=await api(route('history',{workspace:workspace.id,...(cursor?{before:cursor}:{})}));
  if(alive.current){setHistory(rows=>cursor?[...rows,...result.conversations]:result.conversations);setBefore(result.before)}
 }
 async function showPreview(test=false){
  const current=structuredClone(test?{workspace:workspace.id,conversation,...selection,identity:null,
   mode:'probe',text:probeText,draft:null,files:[],run:null}:payload);
  let result;
  try{result=await api(route('preview'),current)}
  catch(failure){if(!legacy)refreshModelsStatus();throw failure}
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
  pendingRequests.set(intentStore,submitted);sessionStorage.setItem(intentStore,JSON.stringify({intent:submitted.intent,text:submitted.text,mode:submitted.mode}));
  pendingRef.current=submitted;setPending(submitted);
  try{
   await api(route('send'),submitted);
   if(alive.current){setCorrection(null);setPreview(null);await read()}
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
   setNotice('尚未查到原请求的回执。可以继续查询，或保留原请求后另开对话。');
  }
 }
 const valid=(legacy?!!legacyProvider&&(legacyProvider!=='codex'||(legacyModel&&legacyEffort)):canSend)
  &&text.trim()&&(!attachDraft||draft)&&(mode!=='proposal'||attachDraft)&&(mode!=='diagnosis'||run);
 return <aside className="project-panel v2-assistant" aria-label="AI 助手" hidden={hidden} onKeyDown={event=>{
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
    busy={busy||running||!!pending} blocked={unknown} onRefresh={()=>perform(refreshModels)} onSave={()=>perform(saveConfig)}
    onTest={()=>perform(()=>showPreview(true))}/>
  </div>:view==='history'?<section className="assistant-history" aria-label="助手会话列表">
   {!history.length&&<PanelEmpty icon={History} title="还没有助手对话"><p>切回对话即可开始提问或准备画布提案。</p></PanelEmpty>}
   {history.map(row=><button key={row.id} onClick={()=>switchTo(row.id,true)}><span>{row.title}</span>
    <small>{new Date(row.updated_at*1000).toLocaleString()}</small></button>)}
   {before&&<button disabled={busy} onClick={()=>perform(()=>loadHistory(before))}>更早的助手对话</button>}
  </section>:<>
   <div className="v2-assistant-scroll">
    <Diagnosis value={diagnosis}/>
    {diagnosis?.execution==='native-cli'&&onEditRunDraft&&<button type="button" onClick={onEditRunDraft}>打开草稿以提出修改</button>}
    {diagnosis&&diagnosis.execution!=='native-cli'&&<AssistantRepair key={diagnosis.run} api={api} workspace={workspace.id} diagnosis={diagnosis}
     agents={settings?.identities||[]} files={files} runs={runs} perform={perform} busy={busy} onRun={onOpenRun}/>}
    <section role="log" aria-label="助手对话消息">
     {!record?.turns?.length&&!matching&&<PanelEmpty icon={Sparkles} title="与助手一起准备工作流"
      actions={(providers.length>0||canSend)&&<button disabled={busy||running||unknown||!!pending}
       onClick={()=>setText(mode==='team'?'创建 Developer、Tester 和 Reviewer 团队。测试失败返回 Developer，审核通过后结束。':
        mode==='proposal'?'检查当前草稿的节点与交接，并建议一项可以审阅的改动。':
        mode==='diagnosis'||mode==='native'&&run?'为什么这个工作流执行失败了？请根据运行证据定位问题。':
        mode==='native'?'建立科研协作工作流：一位 Agent 调研并整理来源，另一位 Agent 汇总发现并指出不确定性。':
        '这个项目有哪些 Agent 和工作流？请结合当前草稿说明运行逻辑。')}>使用示例问题</button>}>
      <p>{providers.length||canSend?'提出问题或准备画布提案。发送前可以核对附件和内容。':
       '可先阅读运行诊断。发送消息前，请在助手模型设置中刷新目录并选择原生 CLI。'}</p>
     </PanelEmpty>}
     {record?.turns?.map(item=><article className="v2-assistant-turn" key={item.id}>
      <p className="assistant-user">{item.text}</p>
      <small>{item.provider} · {labels[item.state]||item.state}</small>
      {item.reply&&!item.proposal&&(['proposal','team'].includes(item.mode)||item.mode==='native'&&item.reply.startsWith('{')?<details><summary>查看原生提案回复</summary><pre>{item.reply}</pre></details>:
       <Markdown skipHtml remarkPlugins={[remarkGfm]}>{item.reply}</Markdown>)}
      {item.receipt&&<details><summary>Provider 回执</summary><dl>
       <dt>请求模型 / 强度</dt><dd>{item.receipt.requested_model||'原生默认'} / {item.receipt.requested_effort||'原生默认'}</dd>
       <dt>实际模型 / 强度</dt><dd>{item.receipt.resolved_model||'未报告'} / {item.receipt.resolved_effort||'未报告'}</dd>
       <dt>原生会话</dt><dd><code>{item.receipt.session_id}</code></dd>
      </dl></details>}
      {item.reply_truncated&&<p className="field-note">回复显示已按字节预算截取。</p>}
      {item===turn&&loginMissing&&['NOT_SENT','FAILED','RECOVERY_REQUIRED'].includes(item.state)&&
       <p>{providerNames[item.provider]} · {originalAccount.stale?'上次目录查询':'当前目录状态'}：CLI 未报告登录。请在原生客户端核对账号。</p>}
      {item.reason&&<p role="status">{item.reason}</p>}
      {item.execution_failure?.reason&&item.execution_failure.reason!==item.reason&&<p>{item.execution_failure.reason}</p>}
      {item.execution_failure?.action&&<p>{item.execution_failure.action}</p>}
      <details><summary>本次请求记录</summary><p>请求 ID <code>{item.intent}</code></p>
       <p>发送状态 {labels[item.state]||item.state}</p>
       {item.execution_failure?.process?.exit!=null&&<p>进程退出码 {item.execution_failure.process.exit}</p>}
      </details>
      {item.prompt&&<details><summary>{item.state==='COMPLETED'?'核验回执对应的可见输入':'本次确认的可见输入'}</summary><pre>{item.prompt}</pre>
       <small>SHA-256 <code>{item.prompt_sha256}</code></small></details>}
     </article>)}
    </section>
    {turn?.team&&<AssistantTeam key={turn.id} api={api} workspace={workspace.id} conversation={conversation}
     turn={turn} draft={draft} busy={busy} perform={perform} onSaved={onSaveTeam} onOpenDraft={onOpenDraft}/>}
    {turn?.proposal_error&&<section className="assistant-proposal"><p role="alert" className="error-text">{turn.proposal_error}</p>
     {turn.mode==='native'&&draft&&<button disabled={busy||running||!!text} onClick={()=>{
      setMode('native');setCorrection(turn.id);
      setText('请按当前画布核对并纠正上一条提案。原请求：'+turn.text.slice(0,4500)+
       '\n校验问题：'+turn.proposal_error.slice(0,700)+'\n只修正被指出的字段和依赖项，保留其余仍有效的操作，并返回完整 JSON。');
     }}>准备纠正请求</button>}
    </section>}
    {turn?.native_saved&&<section className="assistant-proposal">
     <p>提案已保存为工作流。</p>
     <button type="button" onClick={()=>onOpenDraft(turn.native_saved)}>打开已保存工作流</button>
    </section>}
    {turn?.proposal&&<section className="assistant-proposal" aria-label="画布提案">
     <h3>画布提案</h3><p>{turn.proposal.summary}</p>
     {turn.proposal.check?<NativeProposal value={turn.proposal}/>:<ProposalDiff before={turn.proposal.before} after={turn.proposal.after}/>}
     <p className="field-note">{workspace.name} · {turn.context?.draft?.name||'当前草稿'} ·
      {turn.context?.draft?.revision?' 版本 '+turn.context.draft.revision.slice(0,8):' 未保存草稿 '+turn.context?.draft?.sha256?.slice(0,8)}</p>
     {!proposalCurrent&&<p role="status">{proposalKey&&proposalStatus?.key!==proposalKey?'正在核对提案版本…':
      '提案对应的草稿已变化或无法核对。请基于当前草稿重新生成。'}</p>}
     {!proposalCurrent&&proposalKey&&proposalStatus?.key===proposalKey&&
      <button disabled={busy||running||!!text} onClick={()=>setText(turn.text)}>将原请求填入发送框</button>}
     {turn.proposal.check_regression&&<label><input type="checkbox" checked={designOnly===turn.id}
      onChange={event=>setDesignOnly(event.target.checked?turn.id:null)}/>
      确认此变更后工作流暂时仅可作为设计草稿保存</label>}
     <button className="primary" disabled={!proposalCurrent||busy||turn.proposal.check_regression&&designOnly!==turn.id}
      onClick={()=>perform(async()=>{
      const sha=turn.proposal.sha256;
      await onApply({workspace:workspace.id,conversation,turn:turn.id,draft,...(sha?{preview_sha256:sha}:{}),
       ...(turn.proposal.check_regression?{accept_design_only:true}:{})});
      if(sha)await read();
      if(alive.current)setNotice(sha?'已应用并保存。可以从画布撤销，再保存恢复后的版本。':'已应用到当前草稿，可以撤销。')
     })}>{turn.proposal.sha256?'应用并保存草稿':'应用到画布草稿'}</button>
     <details><summary>展开变更前后内容</summary><pre>{JSON.stringify({before:turn.proposal.before,after:turn.proposal.after},null,2)}</pre></details>
    </section>}
    {matching&&<section className="assistant-preview" aria-label="本次发送预览">
     <h3>{preview.payload.mode==='probe'?'真实推理测试':'发送预览'}</h3>
     <dl className="assistant-send-summary">
      <dt>我的问题</dt><dd>{preview.payload.text}</dd>
      <dt>接收方</dt><dd>{providerNames[preview.payload.provider]} ·
       {' '+(preview.context.config?.requested_model||'原生默认模型')}
       {preview.payload.mode==='probe'&&' · 测试可能计费'}</dd>
      {preview.context.correction&&<>
       <dt>原提案纠正</dt><dd>已锁定 {preview.context.correction.retained_count} 条可保留操作。
        {preview.context.correction.rejected.length>0&&<ul>{preview.context.correction.rejected.map(item=>
         <li key={item.index}>第 {item.index} 条 · {item.reason}</li>)}</ul>}
       </dd>
      </>}
      <dt>附带内容</dt><dd>{preview.context.draft?'当前草稿 · ':''}
       {preview.context.run?'运行证据 · ':''}{preview.context.files.length} 个文件
       {preview.context.previous_turns>0&&' · '+preview.context.previous_turns+' 轮历史'}
       {preview.context.run?.failure_facts?.map(fact=><p key={fact.event_seq}>{fact.node||'运行级'} ·
        {fact.http_status?' HTTP '+fact.http_status:' '+fact.code} · {fact.source} #{fact.event_seq}</p>)}
       {!!preview.context.run?.omitted_nodes&&<p>省略 {preview.context.run.omitted_nodes} 个节点详情。</p>}
       {!!preview.context.run?.failure_facts_omitted&&<p>省略 {preview.context.run.failure_facts_omitted} 条结构化错误。</p>}
       {preview.context.run?.evidence_insufficient&&<p>本次冻结运行证据不足，发送内容会注明仍需核验。</p>}
       {preview.context.files.length>0&&<ul>{preview.context.files.map(file=><li key={file.path}>{file.path}</li>)}</ul>}</dd>
     </dl>
     <button className="primary" disabled={busy||!!pending} onClick={()=>perform(send)}>确认发送</button>
     <details><summary>本次发送的可见内容</summary><pre>{preview.prompt}</pre></details>
     <button disabled={busy} onClick={()=>setPreview(null)}>关闭发送预览</button>
    </section>}
   </div>
   <section className="assistant-composer" aria-label="助手发送">
    {pending&&<div>
     <p role="status">正在核对原请求 · <code>{pending.intent}</code></p>
     {pending.text&&<details><summary>查看原始输入</summary><p>{pending.text}</p></details>}
     <button type="button" disabled={busy} onClick={()=>perform(verifyPending)}>核对本次发送</button>
    </div>}
    {unknown&&<div>
     <p className="field-note">请求 <code>{turn.intent}</code> 待核验。{loginMissing?
      '核对登录后刷新模型目录，再查询此请求。仍无回执时可另开对话。':
      '可检查账号、查询同一请求，或另开对话。'}</p>
     <button type="button" disabled={busy} onClick={()=>perform(async()=>{
      const result=await read();
      const receipt=result?.turns?.find(item=>item.intent===turn.intent);
      if(receipt)setNotice('请求 '+receipt.intent+' · '+(labels[receipt.state]||receipt.state));
     })}>查询发送回执</button>
     <button type="button" disabled={busy} onClick={()=>setView('settings')}>检查账号与模型</button>
    </div>}
    {turn?.retry_available&&!running&&!pending&&<button type="button" disabled={busy}
     onClick={()=>{
      setText(turn.text);setPreview(null);
      if(turn.mode!=='probe')setMode(turn.mode);
      setAttachDraft(Boolean(turn.context.draft));
      onFiles((turn.context.files||[]).map(file=>({path:file.path,start:file.start_line})));
      onRun(turn.context.run?.id||null);
      setNotice('已恢复原请求和附件选择。核对账号与当前内容后预览并确认。');
     }}>恢复原请求以重试</button>}
    <div className="assistant-selects">
     {legacy?<label>Provider<select aria-label="助手 Provider" value={legacyProvider} disabled={busy||running} onChange={event=>setLegacyProvider(event.target.value)}>
      {providers.map(key=><option key={key} value={key}>{providerNames[key]}</option>)}
     </select></label>:<div><small>当前模型</small><br/>
      <button disabled={busy} onClick={()=>setView('settings')}>{providerNames[selection.provider]} · {chosen?.id||'原生默认'}</button>
      {!configuredCatalog?.refreshed_at&&<small> · 待配置</small>}</div>}
     {(legacy||mode!=='native')&&<label>工作方式<select aria-label="助手工作方式" value={mode} disabled={busy||running} onChange={event=>{
      setMode(event.target.value);if(event.target.value==='proposal')setAttachDraft(true)
     }}><option value="native" disabled={legacy}>提问或修改工作流</option><option value="chat">对话与项目理解</option><option value="team" disabled={legacy}>创建或修改 Agent 团队</option>
      <option value="proposal" disabled={!draft}>画布提案</option><option value="diagnosis">运行诊断与授权修复</option></select></label>}
     {!legacy&&mode==='native'&&<details className="assistant-advanced-modes"><summary>兼容对话方式</summary>
      <button type="button" onClick={()=>setMode('chat')}>打开高级对话选项</button>
      {providers.length>0&&<button type="button" onClick={()=>{setLegacy(true);setMode('chat')}}>旧原生会话发送</button>}
     </details>}
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
     <summary>本次上下文（{files.length} 个文件{run?'、1 次运行':''}{currentDraft?'、当前草稿':''}）</summary>
     {mode==='native'?<p className="field-note">{draft?'使用当前画布。修改先提供预览。':'正在查看运行；打开草稿后可请求修改。'}</p>:
      <label className="assistant-checkbox"><input type="checkbox" disabled={!draft||mode==='proposal'||busy||running}
       checked={attachDraft} onChange={event=>setAttachDraft(event.target.checked)}/>附加当前草稿</label>}
     {!legacy&&mode!=='native'&&<label className="assistant-checkbox"><input type="checkbox" disabled={busy||running}
      checked={inventory} onChange={event=>setInventory(event.target.checked)}/>附加工作区 Agent 与工作流概览</label>}
     <label className="assistant-run-choice">运行证据<select aria-label="附加运行证据" value={run||''} disabled={busy||running} onChange={event=>onRun(event.target.value||null)}>
      <option value="">不附加</option>{runs.map(row=><option key={row.id} value={row.id}>{row.name} · {new Date(row.created_at*1000).toLocaleTimeString()}</option>)}
     </select></label>
     <AssistantSourcePicker api={api} workspace={workspace.id} files={files} onFiles={onFiles}
      busy={busy||running} perform={perform}/>
    </details>
    {!legacy&&settings&&(!configuredCatalog||configuredCatalog.stale||!configuredCatalog.modes?.Ask?.enabled)&&
     <div className="assistant-model-readiness" role="status">
      <p>{!configuredCatalog||configuredCatalog.stale?'当前模型目录需要刷新。':'当前 CLI 未核验无工具调用能力。'}</p>
      <button type="button" disabled={busy||running} onClick={()=>perform(refreshModels)}>就地刷新模型目录</button>
     </div>}
    {!canSend&&!legacy&&<button type="button" onClick={()=>setView('settings')}>配置助手模型</button>}
    {correction&&mode==='native'&&<p role="status">本次预览将核对原提案及草稿版本。
     <button type="button" disabled={busy} onClick={()=>setCorrection(null)}>取消定向纠正</button></p>}
    <textarea aria-label="助手消息" value={text} rows={3} maxLength={8000} disabled={busy||running||unknown||!!pending}
     onChange={event=>setText(event.target.value)} placeholder="输入问题或画布修改要求"/>
    <button className="primary assistant-preview-action" disabled={!valid||busy||running||unknown||!!pending} onClick={()=>perform(()=>showPreview())}>预览本次发送</button>
   </section>
  </>}
  {error&&<p className="error-text assistant-feedback" role="alert">{error}</p>}
  {notice&&<p className="assistant-feedback" role="status">{notice}</p>}
 </aside>;
}
