import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider,Background,Controls,Handle,Position,MarkerType,useStore,useUpdateNodeInternals} from '@xyflow/react';
import {FolderOpen,Plus,Save,Undo2,Redo2,X,Play,Check,Workflow,ArrowRight,Settings2,Trash2,PanelRightClose} from 'lucide-react';
import '@xyflow/react/dist/style.css';
import '../../studio/static/theme.css';
import './style.css';
import {ProjectFiles} from './ProjectFiles.jsx';
import {ProviderCatalog} from './ProviderCatalog.jsx';
import {NativeSessions} from './NativeSessions.jsx';
import {downloadDraftArchive, makeDraftArchive, readDraftArchive} from './draftTransfer.js';
import {downloadRunSummary, openRunSummary, readRunSummary, RunSummaryFile, selectedRunMetadata} from './RunSummary.jsx';

const copy=value=>JSON.parse(JSON.stringify(value));
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const uid=prefix=>prefix+crypto.randomUUID().replaceAll('-','');
const blank=()=>({version:1,name:'新工作流',nodes:[],edges:[],viewport:{x:0,y:0,zoom:1}});
const TOKEN_KEY='devflower-v2-capability';
let token=location.hash.slice(1)||sessionStorage.getItem(TOKEN_KEY)||'';
if(location.hash){sessionStorage.setItem(TOKEN_KEY,token);history.replaceState(null,'',location.pathname)}
// A restarted service issues a new capability. An explicit entry in this same
// tab can otherwise be a fragment-only navigation without module re-evaluation.
addEventListener('hashchange',()=>{if(location.hash){token=location.hash.slice(1);sessionStorage.setItem(TOKEN_KEY,token);history.replaceState(null,'',location.pathname);location.reload()}});
const conflictHelp='当前修改仍保留。请保留本页编辑，在复制的标签页中重新打开工作流核对已保存版本，再决定是否重新加载。';
class RequestError extends Error{constructor(message,{status=null,uncertain=false}={}){super(message);this.status=status;this.uncertain=uncertain}}
function connectionError(path){return new RequestError(path==='cancel'?
 '取消结果未确认。原运行身份与请求记录仍保留；请恢复本机服务连接后点击“重新读取”核验，不会新建运行或自动重发。':path==='launch'?
 '启动结果未确认。请保留本次启动身份，检查服务连接后点击“核验本次启动”；不会自动重发或创建第二次运行。':path==='drafts/save'?
 '保存结果未确认，当前修改仍保留。请检查本机服务连接后点击保存重试；重试会先核验已保存版本。若出现版本冲突，请保留本页编辑，再核对已保存版本。':
 '未能完整读取本机服务响应，当前编辑仍保留。请检查服务是否运行以及连接状态，再重试当前操作。',{uncertain:true})}
async function api(path,data,{raw=false}={}){
 let response;try{response=await fetch('/api/v2/'+path,{headers:{Authorization:'Bearer '+token,...(data?{'Content-Type':'application/json'}:{})},...(data?{method:'POST',body:raw?data:JSON.stringify(data)}:{})})}catch{throw connectionError(path)}
 let result;try{result=await response.json()}catch{if(response.ok)throw connectionError(path);throw new RequestError('本机服务返回 HTTP '+response.status+'，错误响应未完整读取。请检查服务连接后重试。',{status:response.status})}
 if(!response.ok)throw new RequestError((result.error||'本机服务暂时不可用')+'（HTTP '+response.status+'）'+(response.status===409&&path==='drafts/save'?'。'+conflictHelp:''),{status:response.status});return result;
}
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
const samePayload=(a,b)=>JSON.stringify(ordered(a))===JSON.stringify(ordered(b));
function Button({icon:Icon,children,...props}){return <button {...props}>{Icon&&<Icon size={16}/>}<span>{children}</span></button>}
function Field({label,children,note}){return <label className="field"><span>{label}</span>{React.cloneElement(children,{'aria-label':label,...children.props})}{note&&<small>{note}</small>}</label>}
function AgentCard({data,selected}){const n=data.node;return <article className={'agent-card'+(selected?' selected':'')}>
 <Handle type="target" position={Position.Left} aria-label={n.name+' 输入连接点'}/>
 <header><span className="agent-icon"><Workflow size={18}/></span><div><small>Agent</small><strong>{n.name||'未命名 Agent'}</strong></div><Settings2 size={15}/></header>
 <p>{n.model||'选择 Provider 与模型'}</p><footer><span>{data.activation?stateLabel(data.activation.state):n.provider==='codex'?'Codex':'待配置'}</span><span>{data.activation?.input_sent_sha256?'已写入 CLI':n.effort||'未配置强度'} · 独立会话</span></footer>
 <Handle type="source" position={Position.Right} aria-label={n.name+' 输出连接点'}/>
 </article>}
const nodeTypes={agent:AgentCard};
const ended=state=>['COMPLETED','FAILED','CANCELLED'].includes(state);
const stateLabel=state=>({QUEUED:'等待启动',PENDING:'等待上游',STARTING:'准备调用',RUNNING:'运行中',CANCEL_REQUESTED:'取消已请求',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'本机调度已取消',SKIPPED:'未执行',RECOVERY_REQUIRED:'待核验',UNKNOWN:'状态未确认'}[state]||state);
const outcomeLabel=value=>({completed:'终结成功已核验',failed:'终结失败已核验',not_sent:'没有请求发送证据',completed_partial:'部分节点已完成，其余未发送',pending:'正在等待终结证据',unknown:'结果未知，不能据本机停止推断 Provider 未执行'}[value]||'尚未核验');
const readable=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
function RunEvidence({activation,node,trusted}){const a=activation;return <div className="inspector-content run-evidence">
 <div className="session-note">冻结运行 · {stateLabel(trusted?(a?.state||'PENDING'):'UNKNOWN')} · 只读{!trusted?' · 以下保留上次读取记录':''}</div>
 <h3>{a?.receipt_verified?'最终回答':'已存文本（终结尚未确认）'}</h3><pre aria-label="Agent 最终回答">{a?.output?.answer??'尚无已验证的最终回答'}</pre>
 {a?.reason&&<p className="error-text">{a.reason}</p>}
 <h3>本次实际可见请求</h3><p className="field-note">包括 DevFlower 加入的格式要求与实际上游内容；这是本机发送文本，不代表服务端全部上下文。</p><pre aria-label="实际可见请求">{a?.actual_request??'尚无持久化请求'}</pre>
 <h3>发送与回执</h3><p>{a?.input_sent_sha256?'请求字节已写入自有 CLI 进程':'尚无请求写入证据'}；{a?.receipt_verified?'原生终结回执已关联核验':a?.terminal_failure_verified?'原生终结失败已关联核验':'终结结果未确认'}。</p>
 <p className="field-note">请求：{node.model} / {node.effort}。实际模型：{a?.receipt?.resolved_model||'unknown'}；实际强度：{a?.receipt?.resolved_effort||'unknown'}。未报告的字段保持 unknown。</p>
 <details><summary>初始化、每轮与输出要求</summary><h3>初始化</h3><pre>{node.initial_prompt||'（空）'}</pre><h3>每轮</h3><pre>{node.round_prompt||'（空）'}</pre><h3>输出与交接</h3><pre>{node.output_prompt||'（空）'}</pre><h3>实际输入分节</h3><pre>{a?.request_sections?readable(a.request_sections):'尚无记录'}</pre></details>
 <details><summary>身份、请求哈希与 Provider 回执</summary><pre aria-label="Provider 回执">{readable(a?{activation:a.id,session_binding:a.session_binding_id,native_session:a.observed_session||'unknown',native_turn:a.native_turn_id||'unknown',request:a.request_id,request_sha256:a.actual_request_sha256,input_sent_sha256:a.input_sent_sha256,input_sent_bytes:a.input_sent_bytes,input_sent_operation:a.input_sent_operation_id,receipt:a.receipt||a.failure_receipt||'unknown'}:{state:'PENDING'})}</pre></details>
 </div>}
// Same controlled-measurement boundary as the existing workbench: geometry is
// live view state, never part of the saved draft or undo history.
function RepairHandles(){const update=useUpdateNodeInternals();const missing=useStore(s=>JSON.stringify([...s.nodeLookup.values()].filter(n=>!n.hidden&&n.measured.width>0&&n.measured.height>0&&!n.internals.handleBounds).map(n=>n.id)));useEffect(()=>{const ids=JSON.parse(missing);if(ids.length)update(ids)},[missing,update]);return null}
function canConnect(doc,source,target){
 if(!source||!target||source===target||doc.edges.some(e=>e.source===source||e.target===target))return false;
 let current=target;const seen=new Set();
 while(current){if(current===source||seen.has(current))return false;seen.add(current);current=doc.edges.find(e=>e.source===current)?.target}
 return true;
}

function App(){
 const [status,setStatus]=useState(null),[workspace,setWorkspace]=useState(null),[drafts,setDrafts]=useState([]);
 const [doc,setDoc]=useState(blank),[saved,setSaved]=useState(null),[selected,setSelectedNode]=useState(null),[edge,setEdge]=useState(null);
 const [past,setPast]=useState([]),[future,setFuture]=useState([]),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [folder,setFolder]=useState(null),[folderPath,setFolderPath]=useState(''),[folderError,setFolderError]=useState('');
 const [pending,setPending]=useState(null),[checks,setChecks]=useState(null),[target,setTarget]=useState('');
 const [sideView,setSidePanel]=useState('');
 const [providerGeneration,setProviderGeneration]=useState(0);
 const providerEpoch=useRef(0),providerOpener=useRef(null);
 const providerOpen=sideView==='providers';
 const [transferred,setTransferred]=useState(false);
 const transferTicket=useRef(null),fileChoice=useRef(null),transferInput=useRef(null);
 const [summaryFile,setSummaryFile]=useState(null),[summaryError,setSummaryError]=useState('');
 const summaryAction=useRef(null),summaryInput=useRef(null),summaryOpener=useRef(null),summarySource=useRef(null);
 const filesOpen=sideView==='files',nativeOpen=sideView==='native';
 const workspaceEpoch=useRef(0),workspacePicker=useRef(null),filesOpener=useRef(null),nativeOpener=useRef(null);
 const [theme,setTheme]=useState(()=>localStorage.getItem('devflower-v2-theme')||'system');
 const [measurements,setMeasurements]=useState(()=>new Map());
 const [catalog,setCatalog]=useState(null),[catalogBusy,setCatalogBusy]=useState(false),[runs,setRuns]=useState([]);
 const [viewMode,setViewMode]=useState('draft'),[activeRun,setActiveRun]=useState(null),[runSnapshot,setRunSnapshot]=useState(null),[runError,setRunError]=useState(''),[runTrusted,setRunTrusted]=useState(false);
 const [launching,setLaunching]=useState(false),[launchAttempt,setLaunchAttempt]=useState(null),[cancelling,setCancelling]=useState(false);
 const launchRef=useRef(null),launchBusy=useRef(false),runRef=useRef(null),runEpoch=useRef(0),runRead=useRef(null),runListRead=useRef(0),environmentRef=useRef(null),cancelBusy=useRef(null);
 const docRef=useRef(doc),savedRef=useRef(saved),workspaceRef=useRef(workspace),generation=useRef(0),saving=useRef(false),saveAttempt=useRef(null),dragBase=useRef(null),pendingRef=useRef(null),lastFocus=useRef(null),folderEpoch=useRef(0),folderOpen=useRef(false);
 const dirty=(!saved&&transferred)||!same(doc,saved?.graph||blank());
 const dirtyRef=useRef(dirty);dirtyRef.current=dirty;
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('devflower-v2-theme',theme)},[theme]);
 useEffect(()=>{const warn=e=>{if(workspaceRef.current&&dirtyRef.current){e.preventDefault();e.returnValue=''}};addEventListener('beforeunload',warn);return()=>removeEventListener('beforeunload',warn)},[]);
 function invalidateProviderView(){setProviderGeneration(++providerEpoch.current)}
 function setSideView(view){
  if(providerOpen||view==='providers')invalidateProviderView();
  setSidePanel(view);
 }
 function setSelected(id){invalidateProviderView();setSelectedNode(id)}
 function closeProviderCatalog(){
  setSideView('');
  const epoch=providerEpoch.current;
  requestAnimationFrame(()=>{if(providerEpoch.current===epoch)providerOpener.current?.focus()});
 }
 function pendingLaunch(w=workspaceRef.current){if(!w||!environmentRef.current)return null;const value=JSON.parse(localStorage.getItem('devflower-v2-launch:'+environmentRef.current+':'+w.id)||'null');if(value&&(value.workspace!==w.id||typeof value.intent_key!=='string'))throw Error('本地启动身份无效，请保留本页并核对运行历史；未创建新运行');return value}
 function keepLaunch(value,w=workspaceRef.current){const key='devflower-v2-launch:'+environmentRef.current+':'+w.id;if(value)localStorage.setItem(key,JSON.stringify(value));else localStorage.removeItem(key);launchRef.current=value;setLaunchAttempt(value)}
 function install(next,row=null,fromArchive=false){cancelTransfer();clearRunSummary();summarySource.current=null;setTransferred(fromArchive);generation.current++;saveAttempt.current=null;const attempt=pendingLaunch();launchRef.current=attempt;setLaunchAttempt(attempt);runRef.current=null;runEpoch.current++;setActiveRun(null);setRunSnapshot(null);setRunError('');setViewMode('draft');docRef.current=copy(next);savedRef.current=row;setDoc(copy(next));setSaved(row);setSelected(null);setEdge(null);setPast([]);setFuture([]);setMeasurements(new Map());setChecks(null);setError('');setTarget('')}
 function edit(next,history=true){if(same(next,docRef.current))return;cancelTransfer();if(history){setPast(p=>[...p.slice(-49),copy(docRef.current)]);setFuture([])}docRef.current=next;setDoc(next);setChecks(null)}
 function change(fn){const next=copy(docRef.current);fn(next);edit(next)}
 function updateNode(id,patch){change(d=>{const n=d.nodes.find(n=>n.id===id);if(n)Object.assign(n,patch)})}
 function remember(w,row){
  if(!environmentRef.current)return;
  const key='devflower-v2-last:'+environmentRef.current;
  if(w)localStorage.setItem(key,JSON.stringify({workspace:w.id,draft:row?.id||null}));
  else localStorage.removeItem(key);
 }
 function inWorkspace(w,epoch){return workspaceRef.current?.id===w.id&&workspaceEpoch.current===epoch}
 async function refreshDrafts(w=workspaceRef.current){
  if(!w)return;
  const epoch=workspaceEpoch.current,rows=await api('drafts?workspace='+w.id);
  if(inWorkspace(w,epoch))setDrafts(rows);
 }
 async function refreshRuns(w=workspaceRef.current,{showError=false,launchIntent=null}={}){
  if(!w||workspaceRef.current!==w)return;
  const epoch=workspaceEpoch.current,ticket=++runListRead.current;
  const current=()=>inWorkspace(w,epoch)&&runListRead.current===ticket;
  function sameIntent(){
   if(!launchIntent||launchRef.current!==launchIntent)return false;
   try{return samePayload(pendingLaunch(w),launchIntent)}catch{return false}
  }
  if(showError)setError('');
  try{
   const verifyIntent=sameIntent();
   const rows=await api('runs?workspace='+w.id);
   if(!current())return;
   if(!Array.isArray(rows))throw connectionError('runs');
   setRuns(rows);
   if(verifyIntent&&sameIntent()&&rows.every(row=>
      typeof row?.id==='string'&&/^[0-9a-f]{32}$/.test(row.id)&&
      typeof row.launch_intent==='string'&&/^[a-zA-Z0-9_-]{16,128}$/.test(row.launch_intent))&&
      !rows.some(row=>row.launch_intent===launchIntent.intent_key))keepLaunch(null,w);
  }catch(e){if(current()&&showError)setError(e.message||'操作失败，请重试')}
 }
 async function discover(){const epoch=workspaceEpoch.current;setCatalogBusy(true);try{const value=await api('providers/discover',{});setCatalog(value);if(value.error)throw Error(value.error);if(epoch===workspaceEpoch.current)setNotice('已读取真实模型目录；目录和登录状态不代表推理已成功。')}finally{setCatalogBusy(false)}}
 function rememberRun(value,mode='run',environment=environmentRef.current){
  if(!environment)return;
  const key='devflower-v2-run-view:'+environment;
  if(value)localStorage.setItem(key,JSON.stringify({...value,mode}));
  else localStorage.removeItem(key);
 }
 function showRun(id,w=workspaceRef.current,environment){cancelTransfer();clearRunSummary();summarySource.current=null;const value={id,workspace:w.id,epoch:++runEpoch.current};runRef.current=value;setActiveRun(value);setRunSnapshot(null);setRunTrusted(false);setRunError('');setViewMode('run');setChecks(null);setSelected(null);setEdge(null);setMeasurements(new Map());rememberRun(value,'run',environment)}
 function editDraft(){clearRunSummary();setViewMode('draft');setSelected(null);setEdge(null);setMeasurements(new Map());rememberRun(activeRun,'draft')}
 async function rereadRun(){const value=runRef.current;if(!value)return;if(runRead.current?.epoch===value.epoch)return;const ticket={epoch:value.epoch};runRead.current=ticket;
  try{const response=await api('run?workspace='+value.workspace+'&id='+value.id);if(runRead.current!==ticket||runRef.current?.epoch!==value.epoch||workspaceRef.current?.id!==value.workspace)return;summarySource.current=response;setRunSnapshot(response);setRuns(previous=>previous.map(row=>row.id===value.id?{...row,state:response.state,reason:response.run.reason}:row));setRunTrusted(true);setRunError('');if(ended(response.state))refreshRuns();return ended(response.state)}catch(e){if(runRead.current===ticket&&runRef.current?.epoch===value.epoch){summarySource.current=null;setRunTrusted(false);setRunError('运行状态未确认。'+e.message+' 重新读取只查询原运行，不启动或重发。')}}finally{if(runRead.current===ticket)runRead.current=null}}
 useEffect(()=>{if(!activeRun)return;let closed=false,timer;const read=async()=>{const finished=await rereadRun();if(!closed&&!finished)timer=setTimeout(read,900)};read();return()=>{closed=true;clearTimeout(timer)}},[activeRun?.id,activeRun?.epoch]);
 async function cancelRun(){const value=runRef.current;if(!value||cancelBusy.current)return;cancelBusy.current=value;setCancelling(true);setError('');try{
  const result=await api('cancel',{workspace:value.workspace,run_id:value.id});
  if(runRef.current?.epoch===value.epoch&&workspaceRef.current?.id===value.workspace){runRead.current=null;summarySource.current=result;setRunSnapshot(result);setRunTrusted(true);setRunError('');refreshRuns();setNotice('已记录本次取消请求。仅停止本任务自有工作；Provider 结果依实际回执显示。')}
 }finally{if(cancelBusy.current===value){cancelBusy.current=null;setCancelling(false)}}}
 async function start(){if(launchBusy.current)return;const w=workspaceRef.current;if(!w)throw Error('请先打开工作空间');const epoch=workspaceEpoch.current;let attempt=launchRef.current;cancelTransfer();launchBusy.current=true;setLaunching(true);setError('');
  try{if(!attempt){const unchanged=await save();if(!unchanged)throw Error('保存期间草稿已变化，未启动；请先保存当前修改');const row=savedRef.current;attempt={workspace:w.id,draft_id:row.id,revision:row.revision,intent_key:crypto.randomUUID().replaceAll('-',''),task:''};keepLaunch(attempt,w)}
   const result=await api('launch',attempt);if(workspaceRef.current?.id!==w.id)return;keepLaunch(null,w);showRun(result.run.id,w);summarySource.current=result;setRunSnapshot(result);setRunTrusted(true);refreshRuns(w);setNotice('本次运行使用冻结版本。编辑草稿不会改变已启动运行。');
  }catch(e){if(!e.uncertain&&e.status&&e.status<500&&inWorkspace(w,epoch)){
    // Even an explicit HTTP error can follow durable reservation (e.g. worker
    // publication refusal). Only a successful scoped list proving absence may
    // clear this identity; a failed read never authorizes a new launch key.
    await refreshRuns(w,{launchIntent:attempt});
   }throw e}finally{launchBusy.current=false;setLaunching(false)}
 }
 async function save(preservedTransfer=null){
  if(saving.current)throw Error('正在保存，请稍候');const w=workspaceRef.current;if(!w)throw Error('先打开工作空间再保存');
  if(transferTicket.current!==preservedTransfer)cancelTransfer();
  const gen=generation.current;let attempt=saveAttempt.current;saving.current=true;setBusy('save');setError('');setNotice('');
  const current=()=>{if(gen!==generation.current||workspaceRef.current?.id!==w.id)throw Error('保存响应属于先前的文档，当前编辑未替换；请从原工作空间核对')};
  try{
   if(attempt&&attempt.workspace===w.id&&attempt.generation===gen){
    // A successful workspace-scoped listing is evidence of existence/absence.
    // A failed GET (including HTTP 400) never authorizes another POST.
    const rows=await api('drafts?workspace='+w.id);current();setDrafts(rows);
    const remote=rows.find(row=>row.id===attempt.id);
    if(remote&&samePayload(remote.graph,attempt.document)){
     const verified={...remote,graph:copy(attempt.document)};savedRef.current=verified;setSaved(verified);saveAttempt.current=null;remember(w,verified);
     const unchanged=same(attempt.document,docRef.current);setNotice(unchanged?'已核验上次保存 · '+remote.graph.name:'已核验上次提交；当前后续修改尚未保存，请再次点击保存。');return unchanged;
    }
    if(remote?remote.revision!==attempt.expected:attempt.expected!==null)throw new RequestError('已保存版本与上次提交不同，不能覆盖。'+conflictHelp,{status:409});
   }else{
    const row=savedRef.current;attempt={workspace:w.id,generation:gen,id:row?.id||crypto.randomUUID().replaceAll('-',''),expected:row?.revision||null,document:copy(docRef.current)};
   }
   saveAttempt.current=attempt;let result;
   try{result=await api('drafts/save',{workspace:w.id,id:attempt.id,expected:attempt.expected,document:attempt.document})}
   catch(e){if(!e.uncertain&&e.status!==409&&saveAttempt.current===attempt)saveAttempt.current=null;throw e}
   current();savedRef.current=result;setSaved(result);saveAttempt.current=null;remember(w,result);setNotice('已保存 · '+result.graph.name);
   try{await refreshDrafts(w)}catch(e){throw new RequestError('本次保存已确认，但工作流列表未刷新。'+e.message,{status:e.status})}
   return same(attempt.document,docRef.current);
  }finally{saving.current=false;setBusy('')}
 }
 const guard=fn=>async(...args)=>{
  const epoch=workspaceEpoch.current;setError('');
  try{await fn(...args)}catch(e){if(epoch===workspaceEpoch.current)setError(e.message||'操作失败，请重试')}
 };
 function cancelTransfer(){transferTicket.current=null;fileChoice.current=null}
 useEffect(()=>{
  addEventListener('pagehide',cancelTransfer);
  return ()=>{removeEventListener('pagehide',cancelTransfer);cancelTransfer()};
 },[]);
 function clearRunSummary(){summaryAction.current=null;setSummaryFile(null);setSummaryError('')}
 useEffect(()=>{
  const clear=()=>{summaryAction.current=null};
  addEventListener('pagehide',clear);
  return ()=>{removeEventListener('pagehide',clear);clear()};
 },[]);
 function beginRunSummary(kind){
  clearRunSummary();
  const action={kind,workspace:workspaceRef.current?.id,epoch:workspaceEpoch.current,run:runRef.current};
  summaryAction.current=action;
  return action;
 }
 function currentRunSummary(action){
  return summaryAction.current===action&&workspaceRef.current?.id===action.workspace&&
   workspaceEpoch.current===action.epoch&&runRef.current===action.run;
 }
 async function exportRunSummary(){
  if(busy||pendingRef.current)return;
  const action=beginRunSummary('export');
  const metadata=()=>selectedRunMetadata(summarySource.current,action.workspace,action.run?.id);
  const source=metadata();
  if(!source)return;
  try{
   const bytes=await readRunSummary(action.workspace,source,token);
   if(!currentRunSummary(action))return;
   if(!same(metadata(),source))throw Error();
   downloadRunSummary(bytes);
  }catch{
   if(currentRunSummary(action))setSummaryError('当前运行记录已变化，请重新读取后再导出。');
  }finally{
   if(currentRunSummary(action))summaryAction.current=null;
  }
 }
 function chooseRunSummary(){
  beginRunSummary('choose');
  summaryInput.current.value='';
  summaryInput.current.click();
 }
 async function viewRunSummary(event){
  const choice=summaryAction.current,file=event.currentTarget.files?.[0];
  event.currentTarget.value='';
  if(choice?.kind!=='choose'||!currentRunSummary(choice))return;
  if(!file){clearRunSummary();return}
  const action=beginRunSummary('read');
  try{
   const snapshot=await openRunSummary(file);
   if(currentRunSummary(action))setSummaryFile({snapshot});
  }catch{
   if(currentRunSummary(action))setSummaryFile({snapshot:null});
  }finally{
   if(currentRunSummary(action))summaryAction.current=null;
  }
 }
 function closeRunSummary(){
  clearRunSummary();
  requestAnimationFrame(()=>summaryOpener.current?.focus());
 }
 function beginTransfer(){
  const currentWorkspace=workspaceRef.current;
  if(!currentWorkspace||busy||launchBusy.current||viewMode!=='draft')return null;
  const ticket={workspace:currentWorkspace,epoch:workspaceEpoch.current,generation:generation.current};
  transferTicket.current=ticket;fileChoice.current=null;
  return ticket;
 }
 function currentTransfer(ticket){
  return ticket&&transferTicket.current===ticket&&ticket.generation===generation.current&&inWorkspace(ticket.workspace,ticket.epoch);
 }
 async function exportDraft(){
  const selected=savedRef.current;
  if(!selected||dirtyRef.current)return;
  const ticket=beginTransfer();
  if(!ticket)return;
  setError('');
  try{
   const result=await api('draft?'+new URLSearchParams({workspace:ticket.workspace.id,id:selected.id}));
   if(!currentTransfer(ticket))return;
   if(result.workspace!==ticket.workspace.id||result.id!==selected.id||result.revision!==selected.revision)throw new Error();
   const bytes=await makeDraftArchive(result.graph);
   if(currentTransfer(ticket)&&!dirtyRef.current&&savedRef.current===selected)downloadDraftArchive(bytes);
  }catch{
   if(currentTransfer(ticket))setError('当前已保存版本已变化，请重新打开工作流。');
  }finally{
   if(currentTransfer(ticket))cancelTransfer();
  }
 }
 function chooseArchive(){
  const ticket=beginTransfer();
  if(!ticket)return;
  fileChoice.current=ticket;
  transferInput.current.value='';
  transferInput.current.click();
 }
 function cancelArchiveChoice(){
  if(fileChoice.current&&fileChoice.current===transferTicket.current)cancelTransfer();
 }
 async function importArchive(event){
  const ticket=fileChoice.current,file=event.currentTarget.files?.[0];
  event.currentTarget.value='';fileChoice.current=null;
  if(!currentTransfer(ticket))return;
  if(!file){cancelTransfer();return}
  setError('');
  try{
   const archive=await readDraftArchive(file);
   if(!currentTransfer(ticket))return;
   const proof=await api('drafts/validate?'+new URLSearchParams({workspace:ticket.workspace.id}),archive.bytes,{raw:true});
   if(!currentTransfer(ticket))return;
   if(proof.valid!==true||proof.workspace!==ticket.workspace.id||proof.document_sha256!==archive.documentSha){
    throw Error('当前工作空间暂不能核验，请重新打开后再试。');
   }
   navigate(()=>{
    if(!currentTransfer(ticket))return;
    install(archive.document,null,true);
    setNotice('已打开未保存的工作流，保存后将创建新副本。');
   },'导入工作流',ticket);
  }catch(e){
   if(currentTransfer(ticket)){cancelTransfer();setError(e.message||'工作流文件格式或编码无效。')}
  }
 }
 function navigate(action,title,transfer=null){
  if(busy||launchBusy.current)return;
  if(providerOpen)setSideView('');
  clearRunSummary();
  if(!transfer)cancelTransfer();
  if(workspaceRef.current&&dirtyRef.current){const value={action,title,transfer};pendingRef.current=value;setPending(value)}else guard(action)()
 }
 function closePending(preservedTransfer=null){
  const transfer=pendingRef.current?.transfer;
  if(transfer&&transfer!==preservedTransfer&&currentTransfer(transfer))cancelTransfer();
  pendingRef.current=null;setPending(null)
 }
 async function saveAndContinue(){
  const operation=pendingRef.current;if(!operation)return;
  if(operation.transfer&&!currentTransfer(operation.transfer)){closePending();return}
  const unchanged=await save(operation.transfer);
  if(pendingRef.current!==operation)return;
  if(!unchanged)throw Error('保存期间草稿已变化，请再次保存或取消切换');
  closePending(operation.transfer);await operation.action()
 }
 function selectWorkspace(w,rows,row=null){
  workspaceEpoch.current++;
  cancelBusy.current=null;setCancelling(false);
  setSideView('');
  workspaceRef.current=w;setWorkspace(w);setDrafts(rows);setRuns([]);
  install(row?.graph||blank(),row);
 }
 function closeWorkspace(){
  selectWorkspace(null,[]);
  remember(null);rememberRun(null);
  setNotice('已关闭工作空间');
  requestAnimationFrame(()=>workspacePicker.current?.focus());
 }
 function closeFiles(){
  setSideView('');
  requestAnimationFrame(()=>filesOpener.current?.focus());
 }
 function closeNative(){
  setSideView('');
  requestAnimationFrame(()=>nativeOpener.current?.focus());
 }
 async function openWorkspacePath(path,expectedWorkspace=null){
  const gen=generation.current,snapshot=copy(docRef.current);setBusy('open');
  try{
   const w=await api('workspaces/open',expectedWorkspace===null?{path}:{path,expected_workspace:expectedWorkspace});
   const rows=await api('drafts?workspace='+w.id);
   if(gen!==generation.current||!same(snapshot,docRef.current))throw Error('读取期间草稿已变化，未切换工作空间');
   selectWorkspace(w,rows);rememberRun({workspace:w.id},'draft');
   if(status?.execution_supported)refreshRuns(w,{showError:true});
   hideFolder();remember(w);setNotice('已打开 '+w.name+' · 保存不会启动 Agent');
   setStatus(s=>({...s,workspaces:[w,...(s?.workspaces||[]).filter(x=>x.id!==w.id)]}));
  }finally{setBusy('')}
 }
 async function openDraft(row){const w=workspaceRef.current,gen=generation.current,snapshot=copy(docRef.current);setBusy('open');try{const result=await api('draft?workspace='+w.id+'&id='+row.id);if(gen!==generation.current||w.id!==workspaceRef.current?.id||!same(snapshot,docRef.current))throw Error('读取期间草稿已变化，未替换编辑器');install(result.graph,result);rememberRun({workspace:w.id},'draft');remember(w,result)}finally{setBusy('')}}
 useEffect(()=>{
  setBusy('open');
  guard(async()=>{
   try{
    const s=await api('status');
    environmentRef.current=s.environment.id;setStatus(s);setCatalog(s.provider);
    let last,view;
    try{
     last=JSON.parse(localStorage.getItem('devflower-v2-last:'+s.environment.id));
     view=JSON.parse(localStorage.getItem('devflower-v2-run-view:'+s.environment.id));
    }catch{}
    const w=s.workspaces.find(w=>w.id===last?.workspace);
    if(!w)return;
    const rows=await api('drafts?workspace='+w.id);
    selectWorkspace(w,rows,rows.find(d=>d.id===last.draft));
    if(s.execution_supported)refreshRuns(w,{showError:true});
    if(view?.mode==='run'&&view.workspace===w.id&&view.id)showRun(view.id,w);
   }finally{setBusy('')}
  })();
 },[]);
 async function browse(path){const epoch=++folderEpoch.current;setFolderError('');setFolder(f=>f?{...f,loading:true}:f);try{const listing=await api('folders'+(path?'?path='+encodeURIComponent(path):''));if(folderOpen.current&&epoch===folderEpoch.current){setFolder(listing);setFolderPath(listing.path)}}catch(e){if(folderOpen.current&&epoch===folderEpoch.current){setFolderError(e.message);setFolder(f=>f?{...f,loading:false}:f)}}}
 function hideFolder(){folderOpen.current=false;folderEpoch.current++;setFolder(null);lastFocus.current?.focus()}
 function showFolder(){lastFocus.current=document.activeElement;folderOpen.current=true;setFolderPath(status?.roots?.[0]||'');setFolder({entries:[],roots:status?.roots||[],loading:true});browse()}
 useEffect(()=>{if(!folder&&!pending)return;const dialogs=document.querySelectorAll('[role="dialog"]'),dialog=dialogs[dialogs.length-1],previous=document.activeElement;if(!dialog)return;if(!dialog.contains(document.activeElement))dialog.querySelector('input,button,select,textarea')?.focus();const trap=e=>{if(e.key!=='Tab')return;const items=[...dialog.querySelectorAll('input,button,select,textarea,[tabindex="0"]')].filter(x=>!x.disabled&&x.getClientRects().length);const first=items[0],last=items.at(-1);if(!first)return;if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}};dialog.addEventListener('keydown',trap);return()=>{dialog.removeEventListener('keydown',trap);if(previous?.isConnected)previous.focus()}},[Boolean(folder),Boolean(pending)]);
 function closeInspector(){setSelected(null);setEdge(null);setTimeout(()=>document.querySelector('[aria-label="工作流画布"]')?.focus(),0)}
 useEffect(()=>{const key=e=>{if(e.key==='Escape'){if(pendingRef.current){closePending();return}if(folder){hideFolder();return}if(providerOpen){closeProviderCatalog();return}if(filesOpen){closeFiles();return}if(nativeOpen){closeNative();return}closeInspector()}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();if(workspaceRef.current&&viewMode==='draft'&&!launchBusy.current)guard(save)()}if(viewMode==='draft'&&!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)&&(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo()}};addEventListener('keydown',key);return()=>removeEventListener('keydown',key)},[folder,past,future,busy,viewMode,filesOpen,nativeOpen,providerOpen]);
 function undo(){if(!past.length||busy)return;cancelTransfer();const previous=past.at(-1);setFuture(f=>[copy(docRef.current),...f]);setPast(p=>p.slice(0,-1));docRef.current=previous;setDoc(previous);setChecks(null)}
 function redo(){if(!future.length||busy)return;cancelTransfer();const next=future[0];setPast(p=>[...p,copy(docRef.current)]);setFuture(f=>f.slice(1));docRef.current=next;setDoc(next);setChecks(null)}
 function addAgent(){const id=uid('n');change(d=>d.nodes.push({id,name:'Agent '+String.fromCharCode(65+d.nodes.length%26),position:{x:100+d.nodes.length*300,y:160},provider:'',model:'',effort:'',initial_prompt:'',round_prompt:'',output_prompt:'',permission:'read',timeout:300}));setSelected(id);setEdge(null)}
 function connect(source,destination){if(!canConnect(docRef.current,source,destination)){setError('本期仅支持无环顺序链；请先删除重复、分支或反向连线');return}change(d=>d.edges.push({id:uid('e'),source,target:destination}));setNotice('已连接两个 Agent · 运行时使用独立新会话')}
 const readOnly=viewMode==='run',shown=readOnly?(runSnapshot?.definition?.draft_document||blank()):doc;
 const canExportRunSummary=readOnly&&runTrusted&&selectedRunMetadata(runSnapshot,workspace?.id,activeRun?.id);
 const activation=id=>runSnapshot?.nodes.find(a=>a.node===id),n=shown.nodes.find(n=>n.id===selected),e=shown.edges.find(e=>e.id===edge);
 const actualInputs=e?(activation(e.target)?.inputs||[]).filter(i=>i.edge===e.id):[];
 const rfNodes=shown.nodes.map(n=>({id:n.id,type:'agent',position:n.position,selected:n.id===selected,draggable:!readOnly,connectable:!readOnly,data:{node:n,activation:readOnly?{...(activation(n.id)||{state:'PENDING'}),...(!runTrusted?{state:'UNKNOWN'}:{})}:null},...(measurements.has(n.id)?{measured:measurements.get(n.id)}:{})}));
 const rfEdges=shown.edges.map(e=>({...e,selected:e.id===edge,markerEnd:{type:MarkerType.ArrowClosed},label:'回答 → 输入',style:{strokeWidth:2}}));
 const panelEpoch=workspaceEpoch.current;
 const models=catalog?.models||[],model=models.find(m=>m.id===n?.model),modelKnown=model&&(model.efforts||[]).includes(n?.effort)&&n?.provider==='codex';
 return <div className="v2-app">
  <header className="app-header"><a className="brand" href="/" onClick={e=>e.preventDefault()}><Workflow size={22}/> DevFlower <span>V2</span></a><span className="channel">Development</span><div className="header-spacer"/><label className="theme">外观<select aria-label="外观主题" value={theme} onChange={e=>setTheme(e.target.value)}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label><Button ref={workspacePicker} icon={FolderOpen} disabled={!status||!!busy} onClick={showFolder}>{workspace?workspace.name:'打开工作空间'}</Button></header>
  <aside className="navigation"><div className="section-heading"><span>工作流</span><button aria-label="新建工作流" disabled={!workspace||!!busy} onClick={()=>navigate(()=>{install(blank());remember(workspace,null)},'新建工作流')}><Plus size={17}/></button></div>
   <div className="workspace-line" title={workspace?.path}>{workspace?.path||'先打开一个工作空间'}</div>
   {workspace&&<div className="workspace-actions">
    <Button ref={filesOpener} aria-expanded={filesOpen} onClick={()=>filesOpen?closeFiles():setSideView('files')}>文件</Button>
    <Button ref={nativeOpener} aria-expanded={nativeOpen} onClick={()=>nativeOpen?closeNative():setSideView('native')}>原生会话</Button>
    <Button disabled={!!busy||launching} onClick={()=>navigate(closeWorkspace,'关闭工作空间')}>关闭工作空间</Button>
   </div>}
   <Button ref={providerOpener} className="provider-catalog-open" aria-expanded={providerOpen}
    onClick={()=>providerOpen?closeProviderCatalog():setSideView('providers')}>Provider 模型目录</Button>
   <Button ref={summaryOpener} className="run-summary-open" onClick={chooseRunSummary}>查看运行摘要文件</Button>
   <input ref={summaryInput} hidden type="file" accept=".json,application/json" aria-label="选择运行摘要文件"
    onChange={viewRunSummary} onCancel={()=>{if(summaryAction.current?.kind==='choose')clearRunSummary()}}/>
   <nav aria-label="工作流列表">{drafts.map(row=><button key={row.id} className={'workflow-link'+(saved?.id===row.id?' active':'')} disabled={!!busy} onClick={()=>navigate(()=>openDraft(row),'打开其他工作流')}><Workflow size={16}/><span>{row.graph.name}</span></button>)}{workspace&&!drafts.length&&<p className="muted small empty-list">还没有已保存的工作流。<br/>从空白画布开始。</p>}</nav>
   <div className="section-heading spaces">最近工作空间</div>{status?.workspaces.map(w=><button key={w.id} className={'workspace-link'+(w.id===workspace?.id?' active':'')} title={w.path} disabled={!!busy} onClick={()=>{if(w.id!==workspace?.id)navigate(()=>openWorkspacePath(w.path,w.id),'切换工作空间')}}><FolderOpen size={15}/><span>{w.name}</span></button>)}
   <section className="run-history" aria-label="运行历史"><div className="section-heading"><span>运行历史</span><button disabled={!workspace||!status?.execution_supported} onClick={()=>refreshRuns(workspace,{showError:true})} aria-label="刷新运行历史">↻</button></div>{runs.slice().sort((a,b)=>b.created_at-a.created_at).map(row=><button key={row.id} className={'workflow-link'+(readOnly&&activeRun?.id===row.id?' active':'')} onClick={()=>showRun(row.id)}><span>{row.name}<small>{stateLabel(activeRun?.id===row.id?(runTrusted?(runSnapshot?.state||row.state):'UNKNOWN'):row.state)} · {new Date(row.created_at*1000).toLocaleTimeString()}</small></span></button>)}{!runs.length&&<p className="muted small empty-list">尚无运行记录</p>}</section>
  </aside>
  <main className="editor"><div className="document-bar"><div><input aria-label="工作流名称" disabled={!workspace||readOnly} value={shown.name} onChange={e=>change(d=>d.name=e.target.value)}/><span className={'save-state '+(!readOnly&&dirty?'dirty':'')} aria-live="polite">{readOnly?'冻结版本 · 只读':workspace?(dirty?'未保存':saved?'已保存':'尚未保存'):'空白工作台'}</span></div><div className="document-actions">{readOnly?<Button onClick={editDraft}>编辑草稿</Button>:<><Button icon={Save} disabled={!workspace||!!busy||launching} onClick={guard(save)}>{busy==='save'?'保存中…':'保存'}</Button><Button icon={Check} disabled={!workspace||!!busy||launching} onClick={guard(async()=>{const w=workspaceRef.current,gen=generation.current,snapshot=copy(docRef.current),r=await api('check',{workspace:w.id,document:snapshot});if(gen===generation.current&&w.id===workspaceRef.current?.id&&same(snapshot,docRef.current))setChecks(r)})}>检查</Button><Button className="primary" icon={Play} disabled={!workspace||!!busy||launching||(!launchAttempt&&(!doc.nodes.length||!status?.provider_available))} title={status?.provider_available?'明确启动将调用所选模型；不自动重试':catalog?.error||'执行不可用，编辑和保存仍可使用'} onClick={guard(start)}>{launching?'核验启动中…':launchAttempt?'核验本次启动':dirty?'保存并启动':'启动'}</Button></>}</div></div>
   {workspace&&!readOnly&&<div className="draft-transfer">
    <Button disabled={!!busy||launching} onClick={chooseArchive}>导入工作流</Button>
    <Button disabled={!saved||dirty||!!busy||launching} onClick={exportDraft}>导出已保存工作流</Button>
    <input ref={transferInput} hidden type="file" accept=".json,application/json" aria-label="选择工作流文件"
     onChange={importArchive} onCancel={cancelArchiveChoice}/>
    {(!saved||dirty)&&<small className="muted">请先保存当前工作流，再导出已保存版本。</small>}
   </div>}
   <div className="canvas-toolbar"><span><span className="stage-dot"/>{readOnly?'冻结运行':'编辑草稿'}</span><small>{readOnly?'原草稿保留；此视图只查询本次运行':'顺序连接 Agent，构建你的工作流'}</small><div className="header-spacer"/>{readOnly?<>{canExportRunSummary&&<Button disabled={!!busy||!!pending} onClick={exportRunSummary}>导出运行摘要</Button>}<Button onClick={guard(rereadRun)}>重新读取</Button></>:<><Button icon={Plus} disabled={!workspace||!!busy||launching} onClick={addAgent}>添加 Agent</Button><button aria-label="撤销" disabled={!past.length||!!busy} onClick={undo}><Undo2 size={16}/></button><button aria-label="重做" disabled={!future.length||!!busy} onClick={redo}><Redo2 size={16}/></button></>}</div>
   {!readOnly&&launchAttempt&&<div className="launch-warning" role="status">上次启动结果待核验，身份已保留。即使切换草稿或刷新页面，“核验本次启动”也只核验原启动，不提交当前新编辑。</div>}
   {readOnly&&<section className="run-summary" aria-label="运行状态"><strong>{runTrusted?stateLabel(runSnapshot?.state||'UNKNOWN'):'状态未确认'}</strong><span>实际调用 {runSnapshot?.run?.actual_calls??'unknown'} 次{!runTrusted?'（上次读取）':''}</span><Button disabled={!runSnapshot||cancelling||(runSnapshot?.run?.cancel_requested&&runSnapshot?.worker_observation!=='dead')||['COMPLETED','FAILED','CANCELLED'].includes(runSnapshot?.state)} onClick={guard(cancelRun)}>{cancelling?'正在核验取消…':runSnapshot?.run?.cancel_requested?(runSnapshot?.worker_observation==='dead'?'停止残留自有进程':'取消已请求'):'取消本次运行'}</Button><p>{runTrusted?'Provider：':'上次读取 Provider：'}{outcomeLabel(runSnapshot?.provider_outcome)}。</p>{runSnapshot?.run?.reason&&<p>{runSnapshot.run.reason}</p>}<details><summary>运行身份与冻结版本</summary><pre>{readable({run:activeRun?.id,definition:runSnapshot?.definition?.id,draft:runSnapshot?.run?.draft_id,draft_revision:runSnapshot?.run?.draft_revision,worker:runSnapshot?.worker_observation,local_status:runSnapshot?.run?.local_status})}</pre></details></section>}
   {readOnly&&summaryError&&<div className="message error" role="alert">{summaryError}</div>}
   {readOnly&&runError&&<div className="message error" role="alert"><span>{runError}</span></div>}
   {error&&<div className="message error" role="alert"><span>{error}</span><button aria-label="关闭错误" onClick={()=>setError('')}><X size={16}/></button></div>}
   {notice&&<div className="notice" role="status">{notice}</div>}
   <div className="canvas-region" aria-label="工作流画布" tabIndex={0}>
    <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} onNodeClick={(_,n)=>{setSelected(n.id);setEdge(null);setTarget('')}} onEdgeClick={(_,e)=>{setEdge(e.id);setSelected(null)}} onPaneClick={closeInspector}
     onConnect={c=>{if(!readOnly)connect(c.source,c.target)}} isValidConnection={c=>!readOnly&&canConnect(docRef.current,c.source,c.target)} nodesDraggable={!readOnly} nodesConnectable={!readOnly}
     onNodesChange={changes=>{const dimensions=changes.filter(c=>c.type==='dimensions'&&c.dimensions);if(dimensions.length)setMeasurements(previous=>{const next=new Map(previous);for(const c of dimensions)if(c.dimensions.width>0&&c.dimensions.height>0)next.set(c.id,c.dimensions);return next});const positions=changes.filter(c=>c.type==='position'&&c.position);if(!readOnly&&positions.length){const next=copy(docRef.current);for(const p of positions){const node=next.nodes.find(n=>n.id===p.id);if(node)node.position=p.position}edit(next,false)}}}
     onNodeDragStart={()=>{dragBase.current=copy(docRef.current)}} onNodeDragStop={()=>{const before=dragBase.current;if(before&&!same(before,docRef.current)){setPast(p=>[...p.slice(-49),before]);setFuture([])}dragBase.current=null}}
     onMoveEnd={(_,viewport)=>{if(readOnly)return;const next=copy(docRef.current);next.viewport=viewport;edit(next,false)}} defaultViewport={shown.viewport} key={readOnly?'run:'+activeRun?.id+':'+Boolean(runSnapshot):'draft:'+generation.current} minZoom={.2} maxZoom={2} deleteKeyCode={null} colorMode={theme} proOptions={{hideAttribution:true}}>
     <RepairHandles/><Background gap={24} size={1}/><Controls showInteractive={false}/>
    </ReactFlow>
    {!shown.nodes.length&&<div className="canvas-empty"><span className="empty-icon"><Workflow size={30}/></span><h1>{readOnly?'正在读取原运行':workspace?'从第一个 Agent 开始':'把想法连接成工作流'}</h1><p>{readOnly?'仅查询持久化结果，不启动或重发请求。':workspace?'添加两个独立 Agent，设置提示词，再连接 A → B。':'打开一个工作空间，创建并保存你的 Agent 工作流。'}</p>{!readOnly&&<><Button className="primary" icon={workspace?Plus:FolderOpen} disabled={!status||!!busy} onClick={workspace?addAgent:showFolder}>{workspace?'添加第一个 Agent':'打开工作空间'}</Button><small>空白或未配置完成的工作流也可以保存</small></>}</div>}
   </div>
   {checks&&<section className="checks" aria-label="工作流检查结果"><header><strong>配置检查</strong><button aria-label="关闭检查结果" onClick={()=>setChecks(null)}><X size={16}/></button></header><p>{checks.message}</p>{checks.issues.length?<ul>{checks.issues.map((i,k)=><li key={k}><button onClick={()=>{if(i.node_id){setSelected(i.node_id);setEdge(null)}}}>{i.message}</button></li>)}</ul>:<p>顺序链配置已填写，尚未验证 Provider 可用性。</p>}</section>}
   <footer className="editor-footer"><span>{shown.nodes.length} 个 Agent · {shown.edges.length} 条连线</span><span>{readOnly?'独立新会话 · 实际执行记录':'保存不调用模型 · 明确启动才执行'}</span></footer>
  </main>
  {(n||e)&&<aside className="inspector" aria-label={n?'Agent 配置':'连线配置'}><header><div><small>{n?'AGENT':'连接'}</small><h2>{n?.name||'Agent 交接'}</h2></div><button aria-label="关闭检查器" onClick={closeInspector}><PanelRightClose size={18}/></button></header>
   {n?readOnly?<RunEvidence activation={activation(n.id)} node={n} trusted={runTrusted}/>:<div className="inspector-content"><Field label="Agent 名称"><input value={n.name} onChange={e=>updateNode(n.id,{name:e.target.value})}/></Field><div className="session-note">每个节点使用独立新会话 · 只读提示词模式</div>
    <Field label="Provider"><select value={n.provider||''} onChange={e=>updateNode(n.id,{provider:e.target.value})}><option value="">待配置</option><option value="codex">Codex CLI</option></select></Field>
    <Button disabled={catalogBusy||!status?.provider_available} onClick={guard(discover)}>{catalogBusy?'正在读取目录…':'刷新模型目录'}</Button>
    <div className="config-pair"><Field label="请求模型">{models.length?<select value={n.model||''} onChange={e=>updateNode(n.id,{model:e.target.value})}><option value="">选择真实模型</option>{n.model&&!model&&<option value={n.model}>{n.model}（目录中不可用）</option>}{models.map(m=><option key={m.id} value={m.id}>{m.name||m.id}</option>)}</select>:<input value={n.model||''} placeholder="待发现；可保存未验证值" onChange={e=>updateNode(n.id,{model:e.target.value})}/>}</Field><Field label="请求强度">{model?<select value={n.effort||''} onChange={e=>updateNode(n.id,{effort:e.target.value})}><option value="">请选择</option>{n.effort&&!(model.efforts||[]).includes(n.effort)&&<option value={n.effort}>{n.effort}（不可用）</option>}{(model.efforts||[]).map(x=><option key={x} value={x} disabled={x==='ultra'}>{x}{x==='ultra'?'（本期不开放）':''}</option>)}</select>:<input value={n.effort||''} placeholder="例如 low" onChange={e=>updateNode(n.id,{effort:e.target.value})}/>}</Field></div>
    <p className="field-note" aria-label="模型目录状态">{catalog?.source||'尚无真实发现目录'}{catalog?.at?' · '+new Date(catalog.at*1000).toLocaleString():''}。{catalog?.stale?'目录过期或不可用，请刷新。':''}{catalog?.error||''} {modelKnown?'所选模型与强度在目录中；启动时仍将核验。':'当前配置尚未被目录确认，原值可继续保存。'} 登录状态与目录不代表推理成功。</p>
    <Field label="初始化提示词" note="每次新运行初始化此 Agent。"><textarea rows={3} value={n.initial_prompt||''} onChange={e=>updateNode(n.id,{initial_prompt:e.target.value})}/></Field><Field label="每轮提示词"><textarea rows={4} value={n.round_prompt||''} onChange={e=>updateNode(n.id,{round_prompt:e.target.value})}/></Field><Field label="输出与交接要求"><textarea rows={3} value={n.output_prompt||''} onChange={e=>updateNode(n.id,{output_prompt:e.target.value})}/></Field>
    <details><summary>权限与超时</summary><p className="field-note">Ask 只读、禁用文件/命令、Skills、子 Agent、浏览器与 MCP。CLI 可能保留询问用户工具；若出现询问阻断，将如实失败或待核验，不自动授权。</p><Field label="超时（秒）"><input type="number" min={10} max={3600} value={n.timeout??300} onChange={e=>updateNode(n.id,{timeout:Number(e.target.value)})}/></Field></details>
    <section className="connect-section"><h3>连接下游 Agent</h3><p>从节点右侧端口拖向下游左侧端口，或在这里选择。</p><select aria-label="下游 Agent" value={target} onChange={e=>setTarget(e.target.value)}><option value="">选择下游 Agent</option>{doc.nodes.filter(x=>x.id!==n.id).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select><Button icon={ArrowRight} disabled={!target} onClick={()=>connect(n.id,target)}>连接 Agent</Button></section>
    <div className="runtime-placeholder">实际请求、Provider 回执和结果将在真实执行后显示。</div><Button className="danger" icon={Trash2} onClick={()=>{change(d=>{d.nodes=d.nodes.filter(x=>x.id!==n.id);d.edges=d.edges.filter(x=>x.source!==n.id&&x.target!==n.id)});closeInspector()}}>删除 Agent</Button>
   </div>:<div className="inspector-content run-evidence"><p>{shown.nodes.find(n=>n.id===e.source)?.name} <ArrowRight size={15}/> {shown.nodes.find(n=>n.id===e.target)?.name}</p><h3>交接最终回答</h3>{readOnly?<><p className="field-note">下列内容来自下游 activation 实际持久化输入；可能按执行定义截断或包装，私有回执不跨边。</p>{actualInputs.length?actualInputs.map((input,k)=><section key={k}><pre aria-label="实际交接内容">{readable(input.content)}</pre><p className="field-note">{activation(e.target)?.input_sent_sha256?'下游请求已有字节写入证据；详见下游实际请求。':'尚无下游请求写入证据。'}</p><details><summary>交接身份与哈希</summary><pre>{readable(input)}</pre></details></section>):<p>尚无已持久化的下游输入。</p>}</>:<><p className="field-note">连接已保存在草稿。尚未运行，没有实际交接数据。</p><Button className="danger" icon={Trash2} onClick={()=>{change(d=>d.edges=d.edges.filter(x=>x.id!==e.id));closeInspector()}}>删除连线</Button></>}</div>}
  </aside>}
  {providerOpen&&<ProviderCatalog key={providerGeneration} token={token}
   isCurrent={()=>providerEpoch.current===providerGeneration} onClose={closeProviderCatalog}/>}
  {workspace&&filesOpen&&<ProjectFiles key={workspace.id} api={api} workspace={workspace} onClose={closeFiles}/>}
  {workspace&&nativeOpen&&<NativeSessions key={workspace.id+':'+panelEpoch} api={api} workspace={workspace}
    enabled={Boolean(status?.native_history_available)} writeProviders={status?.native_session_writes}
    workflowLinks={status?.native_workflow_links} workflowTrash={status?.native_workflow_trash} catalog={catalog} refreshCatalog={discover}
    onWorkflow={link=>{showRun(link.run,workspace);setSelected(link.node);closeNative()}}
    isCurrent={()=>inWorkspace(workspace,panelEpoch)} onClose={closeNative}/>}
  {summaryFile&&<RunSummaryFile snapshot={summaryFile.snapshot} onClose={closeRunSummary}/>}
  {folder&&<div className="scrim"><section className="dialog folder-dialog" role="dialog" aria-modal="true" aria-label="打开工作空间"><header><h2>打开工作空间</h2><button aria-label="关闭文件夹选择" onClick={hideFolder}><X size={18}/></button></header><p>选择本机允许范围内的文件夹。打开和保存不会启动 Agent。</p><div className="folder-address"><input autoFocus aria-label="文件夹路径" disabled={Boolean(folder.loading)} value={folderPath} onChange={e=>{folderEpoch.current++;setFolderPath(e.target.value);setFolder(f=>f?{...f,loading:false}:f)}} onKeyDown={e=>{if(e.key==='Enter')browse(folderPath)}}/><Button onClick={()=>browse(folderPath)}>前往</Button></div>{folderError&&<p role="alert" className="error-text">{folderError}</p>}<div className="folder-roots">{folder.roots.map(root=><button key={root} onClick={()=>browse(root)}>{root}</button>)}</div><div className="folder-list">{folder.parent&&<button onClick={()=>browse(folder.parent)}>← 返回上级</button>}{folder.entries.map(entry=><button key={entry.path} onClick={()=>browse(entry.path)}><FolderOpen size={16}/>{entry.name}</button>)}{!folder.entries.length&&<p className="muted">此目录没有可浏览的子文件夹</p>}</div><footer><Button onClick={hideFolder}>取消</Button><Button className="primary" disabled={!folder.path||folder.loading||folderPath!==folder.path||!!busy} onClick={()=>navigate(()=>openWorkspacePath(folder.path),'切换工作空间')}>打开此文件夹</Button></footer></section></div>}
  {pending&&<div className="scrim confirm-scrim"><section className="dialog" role="dialog" aria-modal="true" aria-label="未保存的草稿"><h2>保存后再{pending.title}？</h2><p>当前工作流有未保存的修改。空白和不完整草稿也可以保存。</p>{error&&<p role="alert" className="error-text">{error}</p>}<footer><Button autoFocus onClick={closePending}>{pending.title==='关闭工作空间'?'取消关闭':'取消切换'}</Button><Button disabled={!!busy} onClick={guard(async()=>{const operation=pendingRef.current;if(!operation)return;closePending(operation.transfer);await operation.action()})}>放弃修改</Button><Button className="primary" disabled={!!busy} onClick={guard(saveAndContinue)}>保存并继续</Button></footer></section></div>}
 </div>
}
createRoot(document.getElementById('root')).render(<ReactFlowProvider><App/></ReactFlowProvider>);
