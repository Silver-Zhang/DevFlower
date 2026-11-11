import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider,Background,Controls,MarkerType,MiniMap,SelectionMode,useReactFlow,useStore,useUpdateNodeInternals} from '@xyflow/react';
import {FolderOpen,Plus,Save,X,Play,Check,Workflow,ArrowRight,Trash2,PanelRightClose} from 'lucide-react';
import '@xyflow/react/dist/style.css';
import '../../studio/static/theme.css';
import './style.css';
import {ProjectFiles} from './ProjectFiles.jsx';
import {ProviderCatalog} from './ProviderCatalog.jsx';
import {NativeSessions} from './NativeSessions.jsx';
import {NativePty} from './NativePty.jsx';
import {AgentManager} from './AgentManager.jsx';
import {downloadDraftArchive, makeDraftArchive, readDraftArchive} from './draftTransfer.js';
import {downloadRunSummary, openRunSummary, readRunSummary, RunSummaryFile, selectedRunMetadata} from './RunSummary.jsx';

import {arrangeSelection, cloneDocument as copy, removeSelection} from '../../studio/workbench_ui/canvas_editing.mjs';
import {CanvasContextMenu, ConnectedNodePicker} from '../../studio/workbench_ui/CanvasMenus.jsx';
import {EditorToolbar} from '../../studio/workbench_ui/EditorToolbar.jsx';
import {applySelection, canvasFragment, coordinate, pasteCanvasFragment} from './canvasEditing.mjs';

import {HandoffEdge} from '../../studio/workbench_ui/HandoffEdge.jsx';
import {ContractEditor} from '../../studio/workbench_ui/RouterEditor.jsx';
import {GitWorkspace} from './GitWorkspace.jsx';
import {Assistant} from './Assistant.jsx';
import {PayloadPages,RunInspector} from './RunInspector.jsx';
import {CanvasNodeCard, TypedNodeConfig, nodeKinds} from './CanvasNodes.jsx';
import {EdgeDraftConfig, NodeConnections} from './CanvasLinks.jsx';
import {connectionIssue, defaultConfig, defaultEdgeMode, defaultTransfer, nodeKind, nodeType, ports, runtimeBlock, transferModes} from './canvasModel.mjs';
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
const nodeTypes={canvas:CanvasNodeCard},edgeTypes={handoff:HandoffEdge};
const ended=state=>['COMPLETED','FAILED','CANCELLED'].includes(state);
const stateLabel=state=>({QUEUED:'等待启动',UNACTIVATED:'尚无激活',PENDING:'等待调度',READY:'可运行',WAITING:'等待上游',WAITING_CONFIRMATION:'等待人工审批',PAUSED:'已暂停',STARTING:'准备调用',RUNNING:'运行中',CANCEL_REQUESTED:'取消已请求',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'本机调度已取消',SKIPPED:'未执行',RECOVERY_REQUIRED:'待核验',UNKNOWN:'状态未确认'}[state]||state);
const outcomeLabel=value=>({completed:'终结成功已核验',failed:'终结失败已核验',not_sent:'没有请求发送证据',completed_partial:'部分节点已完成，其余未发送',pending:'正在等待终结证据',unknown:'结果未知，不能据本机停止推断 Provider 未执行'}[value]||'尚未核验');
const readable=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
function ControlEvidence({activation,trusted}){
 const state=trusted?(activation?.state||'UNACTIVATED'):'UNKNOWN';
 return <div className="run-evidence">
  <div className="session-note">冻结运行 · {stateLabel(state)}</div>
  {activation?.reason&&<p className="error-text">{activation.reason}</p>}
  {activation?.decision&&<details><summary>分支判定 · {activation.selected_port}</summary><pre>{readable(activation.decision)}</pre></details>}
  {activation?.loop_results&&<details open><summary>循环轮次 · {activation.loop_results.length}{activation.loop_exit?' · '+(activation.loop_exit==='limit'?'达到上限':'退出条件命中'):''}</summary>
   <pre>{readable(activation.loop_results)}</pre></details>}
  <h3>持久化输出</h3><pre aria-label="节点输出">{activation?.output?readable(activation.output):activation?.paged_fields?.output?'输出可分段读取':'尚无输出'}</pre>
  <details><summary>输入来源与激活身份</summary><pre>{readable(activation?{activation:activation.id,
   inputs:activation.inputs||[],iteration_path:activation.iteration_path}:null)}</pre></details>
 </div>;
}
function RunEvidence({activation,node,trusted,onTransition}){const a=activation;return <div className="inspector-content run-evidence">
 <div className="session-note">冻结运行 · {stateLabel(trusted?(a?.state||'PENDING'):'UNKNOWN')} · 只读{!trusted?' · 以下保留上次读取记录':''}</div>
 <h3>{a?.receipt_verified?'最终回答':'已存文本（终结尚未确认）'}</h3><pre aria-label="Agent 最终回答">{a?.output?.answer??'尚无已验证的最终回答'}</pre>
 {onTransition&&<Button onClick={onTransition}>转入 Agent</Button>}
 {a?.reason&&<p className="error-text">{a.reason}</p>}
 {a?.data_contract&&a.output&&<details><summary>结构化输出与产物引用</summary>
  <pre>{readable({result:a.output.result,artifacts:a.output.artifacts})}</pre></details>}
 <h3>本次实际可见请求</h3><p className="field-note">包括 DevFlower 加入的格式要求与实际上游内容；这是本机发送文本，不代表服务端全部上下文。</p><pre aria-label="实际可见请求">{a?.actual_request??'尚无持久化请求'}</pre>
 {a?.execution_limits&&<section aria-label="Agent 执行预算">
  <h3>执行预算</h3>
  <p>配置超时 {a.execution_limits.timeout_seconds??'未记录'} 秒
   {a.effective_timeout_seconds!=null?' · 本次最多 '+Math.floor(a.effective_timeout_seconds)+' 秒':''}
   {a.execution_limits.native_budget_usd?' · Claude 上限 $'+a.execution_limits.native_budget_usd:''}</p>
  {a.receipt?.usage&&<p>{a.receipt.usage.total_cost_usd==='unknown'?'CLI 未报告费用':
   a.receipt.usage.total_cost_usd!=null?'CLI 报告 $'+a.receipt.usage.total_cost_usd:'CLI 未报告费用'}
   {a.receipt.usage.semantics==='session-cumulative-do-not-sum'?'（会话累计）':''}</p>}
 </section>}
 {a?.execution_failure&&<section aria-label="执行诊断"><h3>{a.execution_failure.reason}</h3>
  <p>{a.execution_failure.effect==='not-sent'?'本次未发送。':a.execution_failure.effect==='terminal-failed'?
   '原生终结失败已核验。':'Provider 结果待核验。'} {a.execution_failure.action}</p></section>}
 <h3>发送与回执</h3><p>{a?.input_sent_sha256?'请求字节已写入自有 CLI 进程':'尚无请求写入证据'}；{a?.receipt_verified?'原生终结回执已关联核验':a?.terminal_failure_verified?'原生终结失败已关联核验':'终结结果未确认'}。</p>
 <p className="field-note">请求：{a?.provider||node.provider||'unknown'} · {a?.model||node.model||'unknown'} / {a?.effort||node.effort||'unknown'}。实际模型：{a?.receipt?.resolved_model||'unknown'}；实际强度：{a?.receipt?.resolved_effort||'unknown'}。未报告的字段保持 unknown。</p>
 <details><summary>初始化、每轮与输出要求</summary><h3>初始化</h3><pre>{node.initial_prompt||'（空）'}</pre><h3>每轮</h3><pre>{node.round_prompt||'（空）'}</pre><h3>输出与交接</h3><pre>{node.output_prompt||'（空）'}</pre><h3>实际输入分节</h3><pre>{a?.request_sections?readable(a.request_sections):'尚无记录'}</pre></details>
 <details><summary>身份、请求哈希与 Provider 回执</summary><pre aria-label="Provider 回执">{readable(a?{activation:a.id,session_binding:a.session_binding_id,native_session:a.observed_session||'unknown',native_turn:a.native_turn_id||'unknown',request:a.request_id,request_sha256:a.actual_request_sha256,input_sent_sha256:a.input_sent_sha256,input_sent_bytes:a.input_sent_bytes,input_sent_operation:a.input_sent_operation_id,receipt:a.receipt||a.failure_receipt||'unknown'}:{state:'PENDING'})}</pre></details>
 </div>}
function PlanApproval({value,busy,error,onClose,onConfirm}){
 const dialog=useRef(null);
 const [verified,setVerified]=useState(null);
 useEffect(()=>{dialog.current.showModal()},[]);
 useEffect(()=>{
  let active=true;
  async function verify(){
   try{
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value.text));
    const hex=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(active)setVerified(hex===value.sha);
   }catch{if(active)setVerified(false)}
  }
  verify();
  return ()=>{active=false};
 },[value.text,value.sha]);
 return <dialog ref={dialog} className="dialog plan-approval" aria-label="确认执行原生 Plan"
  onCancel={event=>{event.preventDefault();if(!busy)onClose()}}
  onKeyDown={event=>{if(event.key==='Escape')event.stopPropagation()}}>
  <h2>执行已完成的 Codex Plan</h2>
  <p>继续使用原生会话 {value.session}。本次授权允许 Agent 读写以下工作目录。</p>
  <p className="workspace-path">{value.cwd}</p>
  <section aria-label="本次执行的冻结计划">
   <h3>本次执行的冻结计划</h3>
   <pre tabIndex={0} aria-label="已核验计划正文">{value.text}</pre>
   <p className="field-note">计划 SHA-256 <code>{value.sha}</code></p>
  </section>
  {verified===false&&<p role="alert" className="error-text">计划正文未通过哈希核验。请重新打开运行记录。</p>}
  {error&&<p role="alert" className="error-text">{error}</p>}
  <footer><Button autoFocus disabled={busy} onClick={onClose}>返回计划</Button>
   <Button className="primary" disabled={busy||verified!==true} onClick={onConfirm}>{value.pending?'核验上次启动':'确认读写并启动 Agent'}</Button></footer>
 </dialog>;
}
// Same controlled-measurement boundary as the existing workbench: geometry is
// live view state, never part of the saved draft or undo history.
function RepairHandles(){const update=useUpdateNodeInternals();const missing=useStore(s=>JSON.stringify([...s.nodeLookup.values()].filter(n=>!n.hidden&&n.measured.width>0&&n.measured.height>0&&!n.internals.handleBounds).map(n=>n.id)));useEffect(()=>{const ids=JSON.parse(missing);if(ids.length)update(ids)},[missing,update]);return null}
function App(){
 const [status,setStatus]=useState(null),[workspace,setWorkspace]=useState(null),[drafts,setDrafts]=useState([]);
 const [doc,setDoc]=useState(blank),[saved,setSaved]=useState(null),[selected,setSelectedNode]=useState(null),[edge,setEdge]=useState(null);
 const [runInspectorOpen,setRunInspectorOpen]=useState(false);
 const [nodeSelection,setNodeSelection]=useState([]),[edgeSelection,setEdgeSelection]=useState([]);
 const [canvasMode,setCanvasMode]=useState('select'),[canvasFocused,setCanvasFocused]=useState(false),[minimap,setMinimap]=useState(false);
 const [canvasMenu,setCanvasMenu]=useState(null),[nodePicker,setNodePicker]=useState(null),[arrangeOpen,setArrangeOpen]=useState(false);
 const menuRef=useRef(null),canvasRef=useRef(null),clipboard=useRef(null);
 const flow=useReactFlow();
 const [past,setPast]=useState([]),[future,setFuture]=useState([]),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [folder,setFolder]=useState(null),[folderPath,setFolderPath]=useState(''),[folderError,setFolderError]=useState('');
 const [pending,setPending]=useState(null),[checks,setChecks]=useState(null);
 const [agentVersions,setAgentVersions]=useState(null),[agentRefresh,setAgentRefresh]=useState(0);
 const [sideView,setSidePanel]=useState('');
 const [runTerminal,setRunTerminal]=useState(null),[linkedActivation,setLinkedActivation]=useState(null);
 const [assistantFiles,setAssistantFiles]=useState([]),[assistantRun,setAssistantRun]=useState(null);
 const [taskFiles,setTaskFiles]=useState([]),[taskText,setTaskText]=useState('');
 const assistantOpener=useRef(null),gitOpener=useRef(null);
 const [agentPanel,setAgentPanel]=useState(null);
 const [planApproval,setPlanApproval]=useState(null),[planBusy,setPlanBusy]=useState(false),[planError,setPlanError]=useState('');
 const planBusyRef=useRef(false);
 const agentOpener=useRef(null);
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
 const viewModeRef=useRef(viewMode);viewModeRef.current=viewMode;
 const dirty=(!saved&&transferred)||!same(doc,saved?.graph||blank());
 const dirtyRef=useRef(dirty);dirtyRef.current=dirty;
 useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('devflower-v2-theme',theme)},[theme]);
 useEffect(()=>{const warn=e=>{if(workspaceRef.current&&dirtyRef.current){e.preventDefault();e.returnValue=''}};addEventListener('beforeunload',warn);return()=>removeEventListener('beforeunload',warn)},[]);
 function invalidateProviderView(){setProviderGeneration(++providerEpoch.current)}
 function setSideView(view){
  if(providerOpen||view==='providers')invalidateProviderView();
  setSidePanel(view);
 }
 function setSelected(id){invalidateProviderView();setSelectedNode(id);setNodeSelection(id?[id]:[])}
 function closeProviderCatalog(){
  setSideView('');
  const epoch=providerEpoch.current;
  requestAnimationFrame(()=>{if(providerEpoch.current===epoch)providerOpener.current?.focus()});
 }
 function pendingLaunch(w=workspaceRef.current){if(!w||!environmentRef.current)return null;const value=JSON.parse(localStorage.getItem('devflower-v2-launch:'+environmentRef.current+':'+w.id)||'null');if(value&&(value.workspace!==w.id||typeof value.intent_key!=='string'))throw Error('本地启动身份无效，请保留本页并核对运行历史；未创建新运行');return value}
 function keepLaunch(value,w=workspaceRef.current){const key='devflower-v2-launch:'+environmentRef.current+':'+w.id;if(value)localStorage.setItem(key,JSON.stringify(value));else localStorage.removeItem(key);launchRef.current=value;setLaunchAttempt(value)}
 function install(next,row=null,fromArchive=false){setTaskFiles([]);setTaskText('');cancelTransfer();clearRunSummary();summarySource.current=null;setTransferred(fromArchive);generation.current++;saveAttempt.current=null;const attempt=pendingLaunch();launchRef.current=attempt;setLaunchAttempt(attempt);runRef.current=null;runEpoch.current++;setActiveRun(null);setRunSnapshot(null);setRunError('');setViewMode('draft');docRef.current=copy(next);savedRef.current=row;setDoc(copy(next));setSaved(row);setSelected(null);setEdge(null);setPast([]);setFuture([]);setMeasurements(new Map());setChecks(null);setError('');resetCanvasSelection()}
 function edit(next,history=true){if(same(next,docRef.current))return;cancelTransfer();if(history){const previous=copy(docRef.current);setPast(p=>[...p.slice(-49),previous]);setFuture([])}docRef.current=next;setDoc(next);setChecks(null)}
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
 function showRun(id,w=workspaceRef.current,environment){setRunInspectorOpen(false);setLinkedActivation(null);cancelTransfer();clearRunSummary();summarySource.current=null;const value={id,workspace:w.id,epoch:++runEpoch.current};runRef.current=value;setActiveRun(value);setRunSnapshot(null);setRunTrusted(false);setRunError('');setViewMode('run');resetCanvasSelection();setCanvasFocused(false);setChecks(null);setSelected(null);setEdge(null);setMeasurements(new Map());rememberRun(value,'run',environment)}
 function editDraft(){setRunInspectorOpen(false);clearRunSummary();setViewMode('draft');resetCanvasSelection();setSelected(null);setEdge(null);setMeasurements(new Map());rememberRun(activeRun,'draft')}
 async function rereadRun(){const value=runRef.current;if(!value)return;if(runRead.current?.epoch===value.epoch)return;const ticket={epoch:value.epoch};runRead.current=ticket;
  try{const response=await api('run?workspace='+value.workspace+'&id='+value.id);if(runRead.current!==ticket||runRef.current?.epoch!==value.epoch||workspaceRef.current?.id!==value.workspace)return;summarySource.current=response;setRunSnapshot(response);setRuns(previous=>previous.map(row=>row.id===value.id?{...row,state:response.state,reason:response.run.reason}:row));setRunTrusted(true);setRunError('');const done=ended(response.state)&&ended(response.run.persisted_state??response.state);if(done)refreshRuns();return done}catch(e){if(runRead.current===ticket&&runRef.current?.epoch===value.epoch){summarySource.current=null;setRunTrusted(false);setRunError('运行状态未确认。'+e.message+' 重新读取只查询原运行，不启动或重发。')}}finally{if(runRead.current===ticket)runRead.current=null}}
 useEffect(()=>{if(!activeRun)return;let closed=false,timer;const read=async()=>{const finished=await rereadRun();if(!closed&&!finished)timer=setTimeout(read,900)};read();return()=>{closed=true;clearTimeout(timer)}},[activeRun?.id,activeRun?.epoch]);
 async function cancelRun(){const value=runRef.current;if(!value||cancelBusy.current)return;cancelBusy.current=value;setCancelling(true);setError('');try{
  const result=await api('cancel',{workspace:value.workspace,run_id:value.id});
  if(runRef.current?.epoch===value.epoch&&workspaceRef.current?.id===value.workspace){runRead.current=null;summarySource.current=result;setRunSnapshot(result);setRunTrusted(true);setRunError('');refreshRuns();setNotice('已记录本次取消请求。仅停止本任务自有工作；Provider 结果依实际回执显示。')}
 }finally{if(cancelBusy.current===value){cancelBusy.current=null;setCancelling(false)}}}
 async function controlRun(action,extra={}){
  const value=runRef.current;
  if(!value||cancelBusy.current)return;
  cancelBusy.current=value;setCancelling(true);setError('');
  try{
   const result=await api(action,{workspace:value.workspace,run_id:value.id,...extra});
   if(runRef.current?.epoch===value.epoch&&workspaceRef.current?.id===value.workspace){
    runRead.current=null;summarySource.current=result;setRunSnapshot(result);setRunTrusted(true);setRunError('');
    refreshRuns();
   }
  }finally{if(cancelBusy.current===value){cancelBusy.current=null;setCancelling(false)}}
 }
 async function start(){if(launchBusy.current)return;const w=workspaceRef.current;if(!w)throw Error('请先打开工作空间');const epoch=workspaceEpoch.current;let attempt=launchRef.current;cancelTransfer();launchBusy.current=true;setLaunching(true);setError('');
  try{if(!attempt){const unchanged=await save();if(!unchanged)throw Error('保存期间草稿已变化，未启动；请先保存当前修改');const row=savedRef.current;attempt={workspace:w.id,draft_id:row.id,revision:row.revision,intent_key:crypto.randomUUID().replaceAll('-',''),task:taskText};
    if(taskFiles.length)attempt.task_files=taskFiles.map(({path,sha256})=>({path,sha256}));
    keepLaunch(attempt,w)}
   const result=await api('launch',attempt);if(workspaceRef.current?.id!==w.id)return;keepLaunch(null,w);setTaskFiles([]);setTaskText('');showRun(result.run.id,w);summarySource.current=result;setRunSnapshot(result);setRunTrusted(true);refreshRuns(w);setNotice('本次运行使用冻结版本。编辑草稿不会改变已启动运行。');
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
  clipboard.current=null;setCanvasFocused(false);setAgentVersions(null);
  workspaceEpoch.current++;
  cancelBusy.current=null;setCancelling(false);
  setSideView('');
  setAgentPanel(null);setAssistantFiles([]);setAssistantRun(null);setRunTerminal(null);setLinkedActivation(null);
  workspaceRef.current=w;setWorkspace(w);setDrafts(rows);setRuns([]);
  install(row?.graph||blank(),row);
 }
 function closeWorkspace(){
  clipboard.current=null;setCanvasFocused(false);setAgentVersions(null);
  selectWorkspace(null,[]);
  remember(null);rememberRun(null);
  setNotice('已关闭工作空间');
  requestAnimationFrame(()=>workspacePicker.current?.focus());
 }
 function attachTaskFile({path,sha256,bytes}){
  if(launchBusy.current||launchRef.current||viewModeRef.current!=='draft')return;
  if(taskFiles.length>=3&&!taskFiles.some(item=>item.path===path)){
   setError('本次运行最多附加 3 个项目文件。');return;
  }
  setTaskFiles(files=>[...files.filter(item=>item.path!==path),{path,sha256,bytes}]);
  setNotice('已选择 '+path+'。启动时会核验文件摘要。');
 }
 function closeGit(){
  setSideView('');
  requestAnimationFrame(()=>gitOpener.current?.focus());
 }
 function openAssistant({file,run}={}){
  if(file)setAssistantFiles(list=>list.includes(file)?list:[...list,file].slice(-3));
  if(run)setAssistantRun(run);
  setSideView('assistant');
 }
 function closeAssistant(){
  setSideView('');
  requestAnimationFrame(()=>assistantOpener.current?.focus());
 }
 async function applyAssistant(data){
  const w=workspaceRef.current,epoch=workspaceEpoch.current,gen=generation.current;
  const sameDraft=()=>w&&data.workspace===w.id&&inWorkspace(w,epoch)&&generation.current===gen&&viewModeRef.current==='draft'&&
   same(data.draft.document,docRef.current)&&data.draft.id===(savedRef.current?.id??null)&&
   data.draft.revision===(savedRef.current?.revision??null)&&!saving.current&&!launchBusy.current;
  if(!sameDraft())throw Error('当前草稿已变化，提案未应用。');
  const result=await api('assistant/apply',data);
  if(!sameDraft())throw Error('验证期间草稿已变化，提案未应用。');
  edit(result.document);
 }
 function closeFiles(){
  setSideView('');
  requestAnimationFrame(()=>filesOpener.current?.focus());
 }
 async function openRunTerminal(activation){
  const current=runRef.current,w=workspaceRef.current;
  if(!current||!w||activation.run!==current.id)return;
  const target=await api('native-pty/run-target?'+new URLSearchParams({workspace:w.id,run:current.id,activation:activation.id}));
  if(runRef.current?.epoch!==current.epoch||workspaceRef.current?.id!==w.id)return;
  if(!target.available)throw Error(target.reason);
  setRunTerminal(target);setSideView('run-pty');
 }
 function closeNative(){
  setSideView('');
  requestAnimationFrame(()=>nativeOpener.current?.focus());
 }
 function openAgents(node=null){
  setAgentPanel({node});
  setSideView('agents');
 }
 function closeAgents(){
  setSideView('');
  requestAnimationFrame(()=>agentOpener.current?.focus());
 }
 async function openPlanApproval(activation){
  const w=workspaceRef.current,run=runRef.current;
  const config=runSnapshot?.definition?.agent_configs?.[activation.node]?.config;
  const plan=activation.receipt?.native_plan;
  if(!w||!run||!config||!activation.receipt_verified||!plan?.finalized||!(/^[0-9a-f]{64}$/).test(plan.sha256))return;
  let text=activation.output?.answer;
  if(!text&&activation.paged_fields?.output){
   let offset=0,sha,payload='';
   while(offset!==null){
    const query=new URLSearchParams({workspace:w.id,run:run.id,activation:activation.id,field:'output',offset,
     ...(sha?{sha256:sha}:{})});
    const page=await api('run-inspector/payload?'+query);
    if(runRef.current!==run||workspaceRef.current?.id!==w.id)return;
    if(page.bytes>8_000_000||page.next_offset!==null&&page.next_offset<=offset)throw Error('计划记录超出安全读取范围。');
    payload+=page.text;sha=page.sha256;offset=page.next_offset;
   }
   text=JSON.parse(payload).answer;
  }
  if(typeof text!=='string'||!text)return;
  const key='df-plan-agent:'+w.id+':'+run.id+':'+activation.node;
  let pending=null;
  try{pending=JSON.parse(sessionStorage.getItem(key))}catch{}
  setPlanError('');
  setPlanApproval({workspace:w.id,run:run.id,node:activation.node,session:activation.receipt.session_id,
   cwd:config.cwd,text,sha:plan.sha256,key,pending});
 }
 async function confirmPlan(){
  if(!planApproval||planBusyRef.current)return;
  const value=planApproval,w=workspaceRef.current;
  const data=value.pending||{workspace:value.workspace,run:value.run,node:value.node,
   authorization:'confirm-workspace-write',intent_key:crypto.randomUUID()};
  planBusyRef.current=true;setPlanBusy(true);setPlanError('');
  try{
   sessionStorage.setItem(value.key,JSON.stringify(data));
   setPlanApproval({...value,pending:data});
   const result=await api('agents/plan-transition',data);
   sessionStorage.removeItem(value.key);
   if(workspaceRef.current?.id!==value.workspace)return;
   setPlanApproval(null);showRun(result.run.id,w);refreshRuns(w);
  }catch(failure){
   if(!failure.uncertain&&failure.status&&failure.status<500){
    sessionStorage.removeItem(value.key);
    if(workspaceRef.current?.id===value.workspace)setPlanApproval({...value,pending:null});
   }
   if(workspaceRef.current?.id===value.workspace)setPlanError(failure.message);
  }finally{planBusyRef.current=false;setPlanBusy(false)}
 }
 function bindAgent(record){
  if(!editable)return;
  const previous=docRef.current.nodes.find(node=>node.id===agentPanel?.node);
  if(agentPanel?.node&&(!previous||nodeKind(previous)!=='agent')){setError('原节点已变化，请重新选择画布节点。');return}
  const id=previous?.id||uid('n');
  change(d=>{
   const node={id,name:record.config.name,agent_id:record.id,agent_revision:record.revision,
    position:previous?.position||{x:100+d.nodes.length*300,y:160},...(!previous||previous.kind?{kind:'agent'}:{})};
   if(previous?.output_contract?.length)node.output_contract=previous.output_contract;
   if(previous?.artifact_paths?.length)node.artifact_paths=previous.artifact_paths;
   if(previous)d.nodes=d.nodes.map(item=>item.id===id?node:item);else d.nodes.push(node);
  });
  setSelected(id);setEdge(null);setEdgeSelection([]);setAgentRefresh(value=>value+1);closeAgents();
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
 function resetCanvasSelection(){setNodeSelection([]);setEdgeSelection([]);setCanvasMenu(null);setNodePicker(null);setArrangeOpen(false);dragBase.current=null}
 function clearSelection(){setSelected(null);setEdge(null);setEdgeSelection([]);setCanvasMenu(null);setArrangeOpen(false)}
 function closeInspector(){setRunInspectorOpen(false);clearSelection();setTimeout(()=>canvasRef.current?.focus(),0)}
 useEffect(()=>{
  if(!canvasMenu&&!nodePicker&&!arrangeOpen)return;
  function outside(event){
   if(!event.target.closest('.canvas-context-menu,.connected-node-picker,.arrange-menu,[data-node-library]')){
    setCanvasMenu(null);setNodePicker(null);setArrangeOpen(false);
   }
  }
  addEventListener('pointerdown',outside);
  return()=>removeEventListener('pointerdown',outside);
 },[canvasMenu,nodePicker,arrangeOpen]);
 function recordDrag(){
  const before=dragBase.current;
  dragBase.current=null;
  if(before&&!same(before,docRef.current)){setPast(p=>[...p.slice(-49),before]);setFuture([])}
 }
 function changeNodes(changes){
  const dimensions=changes.filter(c=>c.type==='dimensions'&&c.dimensions);
  if(dimensions.length)setMeasurements(previous=>{
   const next=new Map(previous);
   for(const c of dimensions)if(c.dimensions.width>0&&c.dimensions.height>0)next.set(c.id,c.dimensions);
   return next;
  });
  const selects=changes.filter(c=>c.type==='select');
  if(selects.length)setNodeSelection(current=>applySelection(current,selects));
  const positions=changes.filter(c=>c.type==='position'&&c.position);
  if(!editable||!positions.length)return;
  const next=copy(docRef.current);
  for(const p of positions){
   const node=next.nodes.find(n=>n.id===p.id);
   if(node)node.position={x:coordinate(p.position.x),y:coordinate(p.position.y)};
  }
  edit(next,!dragBase.current&&!positions.some(p=>p.dragging));
 }
 function changeEdges(changes){
  const selects=changes.filter(c=>c.type==='select');
  if(selects.length)setEdgeSelection(current=>applySelection(current,selects));
 }
 function inspectNode(event,node){
  setCanvasMenu(null);setEdge(null);
  if(event.shiftKey||event.ctrlKey||event.metaKey){invalidateProviderView();setSelectedNode(null);return}
  setSelected(node.id);setEdgeSelection([]);
 }
 function inspectEdge(event,value){
  setCanvasMenu(null);setSelected(null);
  if(event.shiftKey||event.ctrlKey||event.metaKey){setEdge(null);return}
  setEdge(value.id);setEdgeSelection([value.id]);
 }
 function contextMenu(event,node){
  if(!editable)return;
  event.preventDefault();
  let ids=nodeSelection;
  if(node&&!ids.includes(node.id)){ids=[node.id];setSelected(node.id);setEdge(null);setEdgeSelection([])}
  setNodePicker(null);
  setCanvasMenu({isBlockMenuOpen:Boolean(node),isPaneMenuOpen:!node,menuRef,
   position:{x:event.clientX,y:event.clientY},ids});
 }
 function openPicker(screen,atPointer=true){
  if(!editable)return;
  setCanvasMenu(null);
  setNodePicker({screen,position:atPointer?flow.screenToFlowPosition(screen):null});
 }
 function fitCanvas(){setCanvasMenu(null);flow.fitView({padding:.18,duration:0,maxZoom:1.25})}
 function canvasCommand(action,ids=nodeSelection){
  if(!editable)return;
  setCanvasMenu(null);
  if(action==='delete'){
   if(ids.length||edgeSelection.length){edit(removeSelection(docRef.current,ids,edgeSelection));closeInspector()}
   return;
  }
  if(['copy','duplicate','paste'].includes(action)){
   const w=workspaceRef.current;
   const fragment=action==='paste'?clipboard.current?.workspace===w?.id&&clipboard.current.fragment:canvasFragment(docRef.current,ids);
   if(!fragment?.nodes.length)return;
   if(action==='copy'){
    clipboard.current={workspace:w.id,fragment};
    setNotice('已复制 '+fragment.nodes.length+' 个节点');
    return;
   }
   try{
    const result=pasteCanvasFragment(docRef.current,fragment,uid);
    edit(result.graph);
    setSelected(result.selected.length===1?result.selected[0]:null);setNodeSelection(result.selected);
    setEdge(null);setEdgeSelection([]);
    if(fragment.nodes.some(node=>node.agent_id))setNotice('已粘贴节点副本；请为副本重新选择 Agent 配置。');
   }catch(failure){setError(failure.message)}
   return;
  }
  const next=arrangeSelection(docRef.current,ids,action,Object.fromEntries(measurements));
  for(const node of next.nodes)if(ids.includes(node.id))node.position={x:coordinate(node.position.x),y:coordinate(node.position.y)};
  edit(next);
 }

 useEffect(()=>{
  function key(event){
   if(event.defaultPrevented)return;
   if(event.key==='Escape'){
    if(canvasMenu||nodePicker||arrangeOpen){setCanvasMenu(null);setNodePicker(null);setArrangeOpen(false);canvasRef.current?.focus();return}
    if(pendingRef.current){closePending();return}
    if(folder){hideFolder();return}
    if(providerOpen){closeProviderCatalog();return}
    if(filesOpen){closeFiles();return}
    if(nativeOpen){closeNative();return}
    if(sideView==='assistant'){closeAssistant();return}
    if(sideView==='git'){closeGit();return}
    if(sideView==='agents'){closeAgents();return}
    closeInspector();
   }
   const letter=event.key.toLowerCase(),modifier=event.ctrlKey||event.metaKey;
   if(modifier&&letter==='s'){
    event.preventDefault();
    if(workspaceRef.current&&viewMode==='draft'&&!launchBusy.current)guard(save)();
    return;
   }
   if(!editable||folder||pending||planApproval||sideView||event.target.closest?.('input,textarea,select,[contenteditable]'))return;
   if(modifier&&['z','y'].includes(letter)){event.preventDefault();letter==='y'||event.shiftKey?redo():undo();return}
   if(!canvasRef.current?.contains(event.target)&&event.target!==document.body)return;
   if(modifier&&['c','v','d','a'].includes(letter)){
    if(letter==='c'&&window.getSelection()?.toString())return;
    event.preventDefault();
    if(letter==='a'){setSelected(null);setEdge(null);setEdgeSelection([]);setNodeSelection(docRef.current.nodes.map(node=>node.id))}
    else canvasCommand({c:'copy',v:'paste',d:'duplicate'}[letter]);
   }
   if(['Delete','Backspace'].includes(event.key)&&(nodeSelection.length||edgeSelection.length)){event.preventDefault();canvasCommand('delete')}
  }
  addEventListener('keydown',key);
  return()=>removeEventListener('keydown',key);
 });
 function restoreHistory(value){
  cancelTransfer();resetCanvasSelection();setSelected(null);setEdge(null);
  const next=copy(value);
  if(docRef.current.viewport)next.viewport=docRef.current.viewport;else delete next.viewport;
  docRef.current=next;setDoc(next);setChecks(null);
 }
 function undo(){if(!past.length||!editable)return;const current=copy(docRef.current);setFuture(f=>[current,...f]);setPast(p=>p.slice(0,-1));restoreHistory(past.at(-1))}
 function redo(){if(!future.length||!editable)return;const current=copy(docRef.current);setPast(p=>[...p,current]);setFuture(f=>f.slice(1));restoreHistory(future[0])}
 function addNode(kind='agent',position,legacy=false){
  if(!editable||docRef.current.nodes.length>=64)return;
  const id=uid('n');
  const index=docRef.current.nodes.length;
  const point=position&&Number.isFinite(position.x)&&Number.isFinite(position.y)?
   {x:coordinate(position.x),y:coordinate(position.y)}:legacy?{x:100+index*300,y:160}:
   {x:100+(index%4)*300,y:100+Math.floor(index/4)*220};
  change(d=>{
   const common={id,name:kind==='agent'?'Agent '+String.fromCharCode(65+d.nodes.length%26):nodeKinds[kind][0],position:point};
   if(kind!=='agent')d.nodes.push({...common,kind,config_version:1,config:defaultConfig(kind)});
   else if(legacy)d.nodes.push({...common,provider:'',model:'',effort:'',initial_prompt:'',round_prompt:'',output_prompt:'',
    permission:'read',timeout:300});
   else d.nodes.push({...common,kind});
  });
  setNodePicker(null);setSelected(id);setEdge(null);setEdgeSelection([]);
 }
 function connect(candidate){
  if(!editable)return;
  const current=docRef.current,issue=connectionIssue(current,candidate);
  if(issue){setError(issue);return}
  if(current.edges.length>=128){setError('草稿最多保存 128 条连线。');return}
  const {source,target,source_port,target_port}=candidate;
  const legacy=source_port==='out'&&target_port==='in'&&current.nodes
   .filter(node=>node.id===source||node.id===target).every(node=>nodeKind(node)==='agent');
  change(d=>d.edges.push(legacy?{id:uid('e'),source,target}:{id:uid('e'),...candidate}));
  setError('');setNotice('已添加草稿连线。');
 }
 function rfConnection(value){
  return {source:value.source,target:value.target,source_port:value.sourceHandle??'out',target_port:value.targetHandle??'in'};
 }
 function explainConnection(event,state){
  if(!editable||state.isValid||!state.fromHandle)return;
  const node=event.target?.closest?.('.react-flow__handle');
  const handle=state.toHandle||(node?{nodeId:node.dataset.nodeid,id:node.dataset.handleid,type:node.classList.contains('source')?'source':'target'}:null);
  if(!handle)return;
  if(handle.type===state.fromHandle.type){setError('请将输出端口连接到输入端口。');return}
  const [out,input]=state.fromHandle.type==='source'?[state.fromHandle,handle]:[handle,state.fromHandle];
  const issue=connectionIssue(docRef.current,{source:out.nodeId,target:input.nodeId,source_port:out.id,target_port:input.id});
  if(issue)setError(issue);
 }
 function updateEdge(id,patch){
  if(!editable)return;
  const previous=docRef.current.edges.find(item=>item.id===id);
  if(!previous)return;
  const next={...previous,...patch},issue=connectionIssue(docRef.current,next,id);
  if(issue){setError(issue);return}
  const from=docRef.current.nodes.find(node=>node.id===next.source),to=docRef.current.nodes.find(node=>node.id===next.target);
  if(next.transfer?.mode===defaultEdgeMode(from,to)&&next.transfer.max_chars===defaultTransfer.max_chars)delete next.transfer;
  change(d=>{d.edges=d.edges.map(item=>item.id===id?next:item)});setError('');
 }
 function referenceIssue(node){
  if(!node.agent_id||agentVersions?.workspace!==workspace?.id)return '';
  if(agentVersions.failed)return '无法核对 Agent 配置版本。';
  return agentVersions.revisions.get(node.agent_id)===node.agent_revision?'':'此 Agent 配置版本在当前工作空间不可用，请重新选择。';
 }
 useEffect(()=>{
  if(viewMode!=='draft'||!workspace||!doc.nodes.some(node=>node.agent_id))return;
  let alive=true;
  const id=workspace.id;
  setAgentVersions(null);
  api('agents?workspace='+id).then(result=>{
   if(alive)setAgentVersions({workspace:id,revisions:new Map(result.agents.map(row=>[row.id,row.revision]))});
  }).catch(()=>{if(alive)setAgentVersions({workspace:id,failed:true})});
  return()=>{alive=false};
 },[workspace?.id,viewMode,agentRefresh,selected,doc.nodes.some(node=>node.agent_id)]);
 const readOnly=viewMode==='run',editable=Boolean(workspace&&!readOnly&&!busy&&!launching),shown=readOnly?(runSnapshot?.definition?.draft_document||blank()):doc;
 const canExportRunSummary=readOnly&&runTrusted&&selectedRunMetadata(runSnapshot,workspace?.id,activeRun?.id);
 const activation=id=>runSnapshot?.nodes.find(a=>a.node===id),n=shown.nodes.find(n=>n.id===selected),e=shown.edges.find(e=>e.id===edge);
 const edgeTarget=e&&activation(e.target);
 const actualInputs=e?(edgeTarget?.inputs||[]).filter(i=>i.edge===e.id&&i.source===e.source&&i.target_activation===edgeTarget.id):[];
 const completedPlans=runTrusted&&runSnapshot?.run?.persisted_state==='COMPLETED'?
  runSnapshot.nodes.filter(a=>a.receipt_verified&&a.receipt?.native_plan?.finalized):[];
 const launchBlock=doc.nodes.map(runtimeBlock).find(Boolean);
 const approval=runSnapshot?.nodes.find(row=>row.state==='WAITING_CONFIRMATION'&&row.id===runSnapshot.run?.waiting_approval);
 const typedActive=runTrusted&&runSnapshot?.run?.designer_runtime===1&&['RUNNING','WAITING_CONFIRMATION','PAUSED'].includes(runSnapshot.state);
 const inlineAgent=doc.nodes.some(node=>nodeKind(node)==='agent'&&!node.agent_id);
 const rfNodes=shown.nodes.map(node=>{
  const value=readOnly?{...(activation(node.id)||{state:'UNACTIVATED'}),...(!runTrusted?{state:'UNKNOWN'}:{})}:null;
  return {id:node.id,type:'canvas',position:node.position,selected:nodeSelection.includes(node.id),
   draggable:editable&&canvasMode==='select',connectable:editable,
   data:{node,activation:value,stateLabel:value&&stateLabel(value.state),referenceIssue:!readOnly&&referenceIssue(node)},
   ...(measurements.has(node.id)?{measured:measurements.get(node.id)}:{})};
 });
 const shownNodes=new Map(shown.nodes.map(node=>[node.id,node]));
 const rfEdges=shown.edges.map(item=>{
  const {source,target}=item,sourceHandle=item.source_port??'out',targetHandle=item.target_port??'in';
  const output=ports(shownNodes.get(source),'outputs').find(([id])=>id===sourceHandle)?.[1]||sourceHandle;
  const input=ports(shownNodes.get(target),'inputs').find(([id])=>id===targetHandle)?.[1]||targetHandle;
  return {id:item.id,type:'handoff',source,target,sourceHandle,targetHandle,selected:edgeSelection.includes(item.id),
   markerEnd:{type:MarkerType.ArrowClosed},label:item.transfer?transferModes[item.transfer.mode]:edgeSelection.includes(item.id)?output+' → '+input:undefined,
   ariaLabel:shownNodes.get(source).name+' '+output+' → '+shownNodes.get(target).name+' '+input,
   data:{hitArea:true,issues:checks?.issues.filter(issue=>issue.edge_id===item.id)},style:{strokeWidth:2}};
 });
 const panelEpoch=workspaceEpoch.current;
 const models=catalog?.models||[],model=models.find(m=>m.id===n?.model),modelKnown=model&&(model.efforts||[]).includes(n?.effort)&&n?.provider==='codex';
 return <div className={'v2-app'+(canvasFocused?' canvas-focused':'')}>
  <header className="app-header"><a className="brand" href="/" onClick={e=>e.preventDefault()}><Workflow size={22}/> DevFlower <span>V2</span></a><span className="channel">Development</span><div className="header-spacer"/><label className="theme">外观<select aria-label="外观主题" value={theme} onChange={e=>setTheme(e.target.value)}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label><Button ref={workspacePicker} icon={FolderOpen} disabled={!status||!!busy} onClick={showFolder}>{workspace?workspace.name:'打开工作空间'}</Button></header>
  <aside className="navigation"><div className="section-heading"><span>工作流</span><button aria-label="新建工作流" disabled={!workspace||!!busy} onClick={()=>navigate(()=>{install(blank());remember(workspace,null)},'新建工作流')}><Plus size={17}/></button></div>
   <div className="workspace-line" title={workspace?.path}>{workspace?.path||'先打开一个工作空间'}</div>
   {workspace&&<div className="workspace-actions">
    <Button ref={filesOpener} aria-expanded={filesOpen} onClick={()=>filesOpen?closeFiles():setSideView('files')}>文件</Button>
    <Button ref={nativeOpener} aria-expanded={nativeOpen} onClick={()=>nativeOpen?closeNative():setSideView('native')}>原生会话</Button>
    <Button ref={gitOpener} aria-expanded={sideView==='git'} onClick={()=>sideView==='git'?closeGit():setSideView('git')}>Git 平台</Button>
    <Button ref={assistantOpener} aria-expanded={sideView==='assistant'} onClick={()=>sideView==='assistant'?closeAssistant():openAssistant()}>AI 助手</Button>
    <Button ref={agentOpener} aria-expanded={sideView==='agents'} onClick={()=>sideView==='agents'?closeAgents():openAgents()}>Agent 管理</Button>
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
  <main className="editor"><div className="document-bar"><div><input aria-label="工作流名称" disabled={!workspace||readOnly} value={shown.name} onChange={e=>change(d=>d.name=e.target.value)}/><span className={'save-state '+(!readOnly&&dirty?'dirty':'')} aria-live="polite">{readOnly?'冻结版本 · 只读':workspace?(dirty?'未保存':saved?'已保存':'尚未保存'):'空白工作台'}</span></div><div className="document-actions">{readOnly?<Button onClick={editDraft}>编辑草稿</Button>:<><Button icon={Save} disabled={!workspace||!!busy||launching} onClick={guard(save)}>{busy==='save'?'保存中…':'保存'}</Button><Button icon={Check} disabled={!workspace||!!busy||launching} onClick={guard(async()=>{const w=workspaceRef.current,gen=generation.current,snapshot=copy(docRef.current),r=await api('check',{workspace:w.id,document:snapshot});if(gen===generation.current&&w.id===workspaceRef.current?.id&&same(snapshot,docRef.current))setChecks(r)})}>检查</Button><Button className="primary" icon={Play} disabled={!workspace||!!busy||launching||(!launchAttempt&&(launchBlock||!doc.nodes.length||!status?.provider_available&&inlineAgent))} title={launchBlock&&!launchAttempt?launchBlock:status?.provider_available?'明确启动将调用所选模型；不自动重试':catalog?.error||'执行不可用，编辑和保存仍可使用'} onClick={guard(start)}>{launching?'核验启动中…':launchAttempt?'核验本次启动':dirty?'保存并启动':'启动'}</Button></>}</div></div>
   {workspace&&!readOnly&&<div className="draft-transfer">
    <Button disabled={!!busy||launching} onClick={chooseArchive}>导入工作流</Button>
    <Button disabled={!saved||dirty||!!busy||launching} onClick={exportDraft}>导出已保存工作流</Button>
    <input ref={transferInput} hidden type="file" accept=".json,application/json" aria-label="选择工作流文件"
     onChange={importArchive} onCancel={cancelArchiveChoice}/>
    {(!saved||dirty)&&<small className="muted">请先保存当前工作流，再导出已保存版本。</small>}
    <details className="task-context" aria-label="本次运行上下文">
     <summary>本次运行上下文{taskFiles.length>0?' · '+taskFiles.length+' 个文件':''}</summary>
     <Field label="本次任务"><textarea rows={2} maxLength={20000} value={taskText} disabled={launching||Boolean(launchAttempt)}
      onChange={event=>setTaskText(event.target.value)}/></Field>
     <p className="field-note">所选文件文本会发送给本次运行的 Agent。文件权限由各 Agent 配置控制。</p>
     {taskFiles.map(file=><div key={file.path} className="task-file-row"><span>{file.path} · {file.bytes} B</span>
      <button aria-label={'移除任务文件 '+file.path} disabled={launching||Boolean(launchAttempt)}
       onClick={()=>setTaskFiles(files=>files.filter(item=>item.path!==file.path))}>移除</button></div>)}
     <Button disabled={launching||Boolean(launchAttempt)} onClick={()=>setSideView('files')}>选择任务文件</Button>
    </details>
   </div>}
   <div className="canvas-toolbar"><span><span className="stage-dot"/>{readOnly?'冻结运行':'编辑草稿'}</span><small>{readOnly?'原草稿保留；此视图只查询本次运行':'添加节点并连接数据端口'}</small><div className="header-spacer"/>{readOnly?<>{canExportRunSummary&&<Button disabled={!!busy||!!pending} onClick={exportRunSummary}>导出运行摘要</Button>}<Button onClick={guard(rereadRun)}>重新读取</Button></>:<><Button data-node-library icon={Plus} disabled={!editable} onClick={event=>{const bounds=event.currentTarget.getBoundingClientRect();openPicker({x:bounds.left,y:bounds.bottom+6},false)}}>添加节点</Button><Button title="兼容旧草稿的内联 Agent；新配置请从节点库添加。" disabled={!editable} onClick={()=>addNode('agent',null,true)}>添加 Agent</Button></>}</div>
   {!readOnly&&launchBlock&&<p className="phase5-note">{launchBlock} 当前图仍可编辑、保存和导出。</p>}
   {!readOnly&&launchAttempt&&<div className="launch-warning" role="status">上次启动结果待核验，身份已保留。即使切换草稿或刷新页面，“核验本次启动”也只核验原启动，不提交当前新编辑。</div>}
   {readOnly&&<section className="run-summary" aria-label="运行状态"><strong>{runTrusted?stateLabel(runSnapshot?.state||'UNKNOWN'):'状态未确认'}</strong><span>实际调用 {runSnapshot?.run?.actual_calls??'unknown'} 次{!runTrusted?'（上次读取）':''}</span><Button disabled={!runSnapshot||cancelling||(runSnapshot?.run?.cancel_requested&&runSnapshot?.worker_observation!=='dead')||['COMPLETED','FAILED','CANCELLED'].includes(runSnapshot?.state)} onClick={guard(cancelRun)}>{cancelling?'正在核验取消…':runSnapshot?.run?.cancel_requested?(runSnapshot?.worker_observation==='dead'?'停止残留自有进程':'取消已请求'):'取消本次运行'}</Button>{runTrusted&&runSnapshot?.run?.resume_available&&<Button disabled={cancelling} onClick={guard(()=>controlRun('resume'))}>恢复运行</Button>}
   <Button onClick={()=>{clearSelection();setRunInspectorOpen(true)}}>运行检查器</Button>
   <Button disabled={!activeRun?.id} onClick={()=>openAssistant({run:activeRun.id})}>诊断本次运行</Button>
   {typedActive&&<Button disabled={cancelling} onClick={guard(()=>controlRun('pause',{paused:!runSnapshot.run.paused}))}>{runSnapshot.run.paused?'继续运行':'暂停运行'}</Button>}
   {completedPlans.length===1&&!n&&<Button onClick={guard(()=>openPlanApproval(completedPlans[0]))}>转入 Agent</Button>}<p>{runTrusted?'Provider：':'上次读取 Provider：'}{outcomeLabel(runSnapshot?.provider_outcome)}。</p>{runSnapshot?.run?.reason&&<p>{runSnapshot.run.reason}</p>}
   {!!runSnapshot?.run?.partial_failures?.length&&<details><summary>{runSnapshot.run.partial_failures.length} 个分支失败，保留成功输出</summary>
    {runSnapshot.nodes.filter(row=>runSnapshot.run.partial_failures.includes(row.id)).map(row=><p key={row.id}>{row.name} · {row.reason}</p>)}</details>}
   {approval&&<section className="approval-wait" aria-label="待人工审批"><h3>{approval.name}</h3><p>{approval.question}</p>
    <p className="field-note">截止 {new Date(approval.approval_deadline*1000).toLocaleTimeString()}。等待期间不会启动后续节点。</p>
    <details><summary>待审批输入</summary><pre>{readable(approval.inputs)}</pre></details>
    {approval.approval_decision?<p role="status">已记录决定，正在核验。</p>:<div className="approval-actions">
     <Button className="primary" disabled={!typedActive||cancelling} onClick={guard(()=>controlRun('approval',{activation:approval.id,decision:'approve'}))}>批准</Button>
     <Button disabled={!typedActive||cancelling} onClick={guard(()=>controlRun('approval',{activation:approval.id,decision:'reject'}))}>拒绝</Button>
    </div>}
   </section>}
   <details><summary>运行身份与冻结版本</summary><pre>{readable({run:activeRun?.id,definition:runSnapshot?.definition?.id,draft:runSnapshot?.run?.draft_id,draft_revision:runSnapshot?.run?.draft_revision,worker:runSnapshot?.worker_observation,local_status:runSnapshot?.run?.local_status})}</pre></details></section>}
   {readOnly&&summaryError&&<div className="message error" role="alert">{summaryError}</div>}
   {readOnly&&runError&&<div className="message error" role="alert"><span>{runError}</span></div>}
   {error&&<div className="message error" role="alert"><span>{error}</span><button aria-label="关闭错误" onClick={()=>setError('')}><X size={16}/></button></div>}
   {notice&&<div className="notice" role="status">{notice}</div>}
   <div ref={canvasRef} className="canvas-region" aria-label="工作流画布" tabIndex={0}>
    <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodeClick={inspectNode} onEdgeClick={inspectEdge}
     onPaneClick={closeInspector} onPaneContextMenu={event=>contextMenu(event)} onNodeContextMenu={contextMenu}
     onConnect={value=>connect(rfConnection(value))} onConnectEnd={explainConnection} onClickConnectEnd={explainConnection}
     isValidConnection={value=>editable&&!connectionIssue(docRef.current,rfConnection(value))}
     nodesDraggable={editable&&canvasMode==='select'} nodesConnectable={editable}
     onNodesChange={changeNodes} onEdgesChange={changeEdges}
     onNodeDragStart={()=>{if(editable&&!dragBase.current)dragBase.current=copy(docRef.current)}}
     onNodeDragStop={recordDrag} onSelectionDragStart={()=>{if(editable&&!dragBase.current)dragBase.current=copy(docRef.current)}}
     onSelectionDragStop={recordDrag} onSelectionStart={()=>{setSelectedNode(null);setEdge(null)}}
     selectionOnDrag={editable&&canvasMode==='select'} selectionMode={SelectionMode.Partial}
     multiSelectionKeyCode={['Shift','Control','Meta']} panOnDrag={canvasMode==='pan'||readOnly?true:[1,2]}
     onMoveEnd={(_,viewport)=>{if(!editable)return;const next=copy(docRef.current);next.viewport={...viewport,x:coordinate(viewport.x),y:coordinate(viewport.y)};edit(next,false)}}
     defaultViewport={shown.viewport} key={readOnly?'run:'+activeRun?.id+':'+Boolean(runSnapshot):'draft:'+generation.current}
     minZoom={.2} maxZoom={2} deleteKeyCode={null} colorMode={theme} proOptions={{hideAttribution:true}}>
     <RepairHandles/><Background gap={24} size={1}/>{readOnly&&<Controls showInteractive={false}/>}
     {minimap&&<MiniMap pannable zoomable/>}
    </ReactFlow>
    {!readOnly&&workspace&&<EditorToolbar focused={canvasFocused} onFocus={()=>setCanvasFocused(value=>!value)}
     mode={canvasMode} setMode={setCanvasMode} count={nodeSelection.length} disabled={!editable}
     undo={undo} redo={redo} canUndo={Boolean(past.length)} canRedo={Boolean(future.length)} command={canvasCommand}
     minimap={minimap} setMinimap={setMinimap} onFit={fitCanvas} zoomIn={()=>flow.zoomIn()} zoomOut={()=>flow.zoomOut()}
     menuOpen={arrangeOpen} setMenuOpen={setArrangeOpen}/>}
    {canvasMenu&&<CanvasContextMenu menu={canvasMenu} onAction={action=>canvasCommand(action,canvasMenu.ids)}
     onAdd={()=>openPicker(canvasMenu.position)} onFit={fitCanvas}/>}
    {nodePicker&&<ConnectedNodePicker pending={nodePicker} kinds={nodeKinds} canChoose={()=>true}
     onChoose={kind=>addNode(kind,nodePicker.position)} onClose={()=>setNodePicker(null)}/>}
    {!shown.nodes.length&&<div className="canvas-empty"><span className="empty-icon"><Workflow size={30}/></span><h1>{readOnly?'正在读取原运行':workspace?'从第一个 Agent 开始':'把想法连接成工作流'}</h1><p>{readOnly?'仅查询持久化结果，不启动或重发请求。':workspace?'从节点库选择类型，或先添加一个 Agent。':'打开一个工作空间，创建并保存你的 Agent 工作流。'}</p>{!readOnly&&<><Button className="primary" icon={workspace?Plus:FolderOpen} disabled={!status||!!busy} onClick={workspace?()=>addNode():showFolder}>{workspace?'添加第一个 Agent':'打开工作空间'}</Button><small>空白或未配置完成的工作流也可以保存</small></>}</div>}
   </div>
   {checks&&<section className="checks" aria-label="工作流检查结果"><header><strong>配置检查</strong><button aria-label="关闭检查结果" onClick={()=>setChecks(null)}><X size={16}/></button></header><p>{checks.message}</p>{checks.issues.length?<ul>{checks.issues.map((i,k)=><li key={k}><button onClick={()=>{if(i.node_id){setSelected(i.node_id.split('/')[0]);setEdge(null);setEdgeSelection([])}else if(i.edge_id){setSelected(null);setEdge(i.edge_id);setEdgeSelection([i.edge_id])}}}>{i.message}</button></li>)}</ul>:<p>工作流配置已填写，尚未验证 Provider 可用性。</p>}</section>}
   <footer className="editor-footer"><span>{shown.nodes.length} 个节点 · {shown.edges.length} 条连线</span><span>{readOnly?'独立新会话 · 实际执行记录':'保存不调用模型 · 明确启动才执行'}</span></footer>
  </main>
  {readOnly&&(n||e||runInspectorOpen)&&<aside className="inspector run-inspector" aria-label={n?(nodeKind(n)==='agent'?'Agent 配置':'节点配置'):e?'连线配置':'运行检查器'}>
   <header><div><small>运行检查器</small><h2>{n?.name||(e?'交接记录':'运行详情')}</h2></div>
    <button aria-label="关闭检查器" onClick={closeInspector}><PanelRightClose size={18}/></button></header>
   <RunInspector key={activeRun?.id+':'+(n?.id||e?.id||'run')} api={api} snapshot={runSnapshot} trusted={runTrusted}
    selection={n?{node:n.id,activation:linkedActivation}:e?{edge:e.id,node:e.target}:null} onSelect={path=>{setEdge(null);setSelected(path.split('/')[0])}}>
    {current=>{
     if(!n&&!e&&!current)return null;
     const scoped={api,workspace:runSnapshot?.run?.workspace,run:runSnapshot?.run?.id,activation:current?.id};
     if(e)return <div className="run-evidence">
      <p>{shown.nodes.find(node=>node.id===e.source)?.name} <ArrowRight size={15}/> {shown.nodes.find(node=>node.id===e.target)?.name}</p>
      <h3>{e.transfer?.mode==='fields'?'交接选定字段':e.transfer?.mode==='artifacts'?'交接产物引用':'交接最终回答'}</h3>
      <p className="field-note">此处显示下游激活实际记录的交接。</p>
      {actualInputs.map((input,index)=><section key={input.handoff_id||index}>
       <pre aria-label="实际交接内容">{readable(input.content)}</pre>
       <p className="field-note">{current?.input_sent_sha256?'下游请求已有字节写入证据。':current?.kind==='agent'?'尚无下游请求写入证据。':'确定性节点的接收状态见事件。'}</p>
       <details><summary>交接身份与哈希</summary><pre>{readable(input)}</pre></details>
      </section>)}
      {!actualInputs.length&&(current?.paged_fields?.inputs?<PayloadPages {...scoped} field="inputs" edge={e.id}/>:
       <p>尚无已持久化的下游输入。</p>)}
     </div>;
     const agent=current?current.kind==='agent':nodeKind(n)==='agent';
     const spec=current?.node.split('/').reduce((parent,id)=>parent?.body?.nodes.find(node=>node.id===id),
       {body:runSnapshot?.definition?.graph})||n;
     return <>
      {agent?<RunEvidence activation={current} node={{...n,...spec}} trusted={runTrusted}
       onTransition={completedPlans.some(row=>row.id===current?.id)&&current.node===n?.id?guard(()=>openPlanApproval(current)):null}/>:
       <ControlEvidence activation={current} trusted={runTrusted}/>}
      {current?.started_at&&<details><summary>激活时间</summary><pre>{readable({started_at:current.started_at,finished_at:current.finished_at})}</pre></details>}
      {current?.receipt_verified&&current.provider==='codex'&&status?.native_codex_terminal&&
       <Button onClick={guard(()=>openRunTerminal(current))}>查看此激活的原生终端</Button>}
      {current?.task_files?.length>0&&<details><summary>本次所附任务文件</summary><pre>{readable(current.task_files)}</pre></details>}
      {current?.data_contract&&<p className="field-note" aria-label="输出合同结果">
       {current.data_contract.state==='PASSED'?'输出合同通过':'输出合同未通过'}{current.receipt_verified?' · 原生回执已核验':''}</p>}
      {agent&&current?.inputs?.length>0&&<details><summary>持久化输入</summary><pre>{readable(current.inputs)}</pre></details>}
      {Object.keys(current?.paged_fields||{}).filter(field=>field!=='native_tool_activity').map(field=>
       <PayloadPages key={current.id+field} {...scoped} field={field}/>)}
     </>;
    }}
   </RunInspector>
  </aside>}
  {!readOnly&&(n||e)&&<aside className="inspector" aria-label={n?(nodeKind(n)==='agent'?'Agent 配置':'节点配置'):'连线配置'}><header><div><small>{n?nodeType(n).english:'连接'}</small><h2>{n?.name||'Agent 交接'}</h2></div><button aria-label="关闭检查器" onClick={closeInspector}><PanelRightClose size={18}/></button></header>
   {n?<div className="inspector-content" key={n.id}><Field label={nodeKind(n)==='agent'?'Agent 名称':'节点名称'}><input maxLength={160} value={n.name} onChange={e=>updateNode(n.id,{name:e.target.value})}/></Field>
    {nodeKind(n)!=='agent'?<TypedNodeConfig node={n} onChange={patch=>updateNode(n.id,{config:{...n.config,...patch}})}
     bodyChoices={drafts.filter(row=>row.id!==saved?.id)} siblingNodes={doc.nodes} connected={new Set(doc.edges.filter(item=>item.source===n.id).map(item=>item.source_port??'out'))} Field={Field}/>:
    n.agent_id?<div className="session-note"><p>此节点引用已保存的 Agent 配置版本。</p>
     <p className={referenceIssue(n)?'error-text':'field-note'}>{referenceIssue(n)||(!agentVersions?'正在核对工作空间内的版本。':'已核对绑定版本。')}</p>
     <Button onClick={()=>openAgents(n.id)}>选择 Agent 配置版本</Button></div>:n.kind==='agent'?<>
    <div className="session-note">从 Agent 管理绑定已保存的配置。节点会保留当时选择的版本。</div>
    <Button onClick={()=>openAgents(n.id)}>选择 Agent 配置版本</Button>
    </>:<>
    <div className="session-note">兼容旧草稿 · 每个节点使用独立新会话和只读提示词模式。</div>
    <Button onClick={()=>openAgents(n.id)}>使用已保存 Agent</Button>
    <Field label="Provider"><select value={n.provider||''} onChange={e=>updateNode(n.id,{provider:e.target.value})}><option value="">待配置</option><option value="codex">Codex CLI</option></select></Field>
    <Button disabled={catalogBusy||!status?.provider_available} onClick={guard(discover)}>{catalogBusy?'正在读取目录…':'刷新模型目录'}</Button>
    <div className="config-pair"><Field label="请求模型">{models.length?<select value={n.model||''} onChange={e=>updateNode(n.id,{model:e.target.value})}><option value="">选择真实模型</option>{n.model&&!model&&<option value={n.model}>{n.model}（目录中不可用）</option>}{models.map(m=><option key={m.id} value={m.id}>{m.name||m.id}</option>)}</select>:<input value={n.model||''} placeholder="待发现；可保存未验证值" onChange={e=>updateNode(n.id,{model:e.target.value})}/>}</Field><Field label="请求强度">{model?<select value={n.effort||''} onChange={e=>updateNode(n.id,{effort:e.target.value})}><option value="">请选择</option>{n.effort&&!(model.efforts||[]).includes(n.effort)&&<option value={n.effort}>{n.effort}（不可用）</option>}{(model.efforts||[]).map(x=><option key={x} value={x} disabled={x==='ultra'}>{x}{x==='ultra'?'（本期不开放）':''}</option>)}</select>:<input value={n.effort||''} placeholder="例如 low" onChange={e=>updateNode(n.id,{effort:e.target.value})}/>}</Field></div>
    <p className="field-note" aria-label="模型目录状态">{catalog?.source||'尚无真实发现目录'}{catalog?.at?' · '+new Date(catalog.at*1000).toLocaleString():''}。{catalog?.stale?'目录过期或不可用，请刷新。':''}{catalog?.error||''} {modelKnown?'所选模型与强度在目录中；启动时仍将核验。':'当前配置尚未被目录确认，原值可继续保存。'} 登录状态与目录不代表推理成功。</p>
    <Field label="初始化提示词" note="每次新运行初始化此 Agent。"><textarea rows={3} value={n.initial_prompt||''} onChange={e=>updateNode(n.id,{initial_prompt:e.target.value})}/></Field><Field label="每轮提示词"><textarea rows={4} value={n.round_prompt||''} onChange={e=>updateNode(n.id,{round_prompt:e.target.value})}/></Field><Field label="输出与交接要求"><textarea rows={3} value={n.output_prompt||''} onChange={e=>updateNode(n.id,{output_prompt:e.target.value})}/></Field>
    <details><summary>权限与超时</summary><p className="field-note">Ask 只读、禁用文件/命令、Skills、子 Agent、浏览器与 MCP。CLI 可能保留询问用户工具；若出现询问阻断，将如实失败或待核验，不自动授权。</p><Field label="超时（秒）"><input type="number" min={10} max={3600} value={n.timeout??300} onChange={e=>updateNode(n.id,{timeout:Number(e.target.value)})}/></Field></details></>}
    {nodeKind(n)==='agent'&&<>
     <ContractEditor label="输出" rows={n.output_contract} maxRows={32} maxFieldLength={200}
      onChange={rows=>updateNode(n.id,{output_contract:rows})}/>
     <details className="contract-editor"><summary>可报告的产物路径 · {n.artifact_paths?.length||0} 项</summary>
      <p className="field-note">填写当前工作区内的相对路径。实际回复报告这些文件时，运行器记录文件摘要。</p>
      {(n.artifact_paths||[]).map((path,index)=><div className="task-file-row" key={index}>
       <input aria-label={'产物路径 '+(index+1)} value={path} placeholder="例如 results/report.json" maxLength={4096}
        onChange={event=>updateNode(n.id,{artifact_paths:n.artifact_paths.map((item,i)=>i===index?event.target.value:item)})}/>
       <button aria-label={'移除产物路径 '+(index+1)}
        onClick={()=>updateNode(n.id,{artifact_paths:n.artifact_paths.filter((_,i)=>i!==index)})}>移除</button>
      </div>)}
      <Button disabled={(n.artifact_paths?.length||0)>=8}
       onClick={()=>updateNode(n.id,{artifact_paths:[...(n.artifact_paths||[]),'']})}>添加产物路径</Button>
     </details>
    </>}
    <NodeConnections node={n} graph={doc} onConnect={connect} Field={Field}/>
    {nodeKind(n)==='agent'&&<div className="runtime-placeholder">实际请求、Provider 回执和结果将在真实执行后显示。</div>}
    <Button className="danger" icon={Trash2} onClick={()=>{edit(removeSelection(docRef.current,[n.id]));closeInspector()}}>
     {nodeKind(n)==='agent'?'删除 Agent':'删除节点'}</Button>
   </div>:<div className="inspector-content run-evidence"><p>{shown.nodes.find(n=>n.id===e.source)?.name} <ArrowRight size={15}/> {shown.nodes.find(n=>n.id===e.target)?.name}</p><h3>连线交接配置</h3><EdgeDraftConfig edge={e} source={shownNodes.get(e.source)} target={shownNodes.get(e.target)} onUpdate={patch=>updateEdge(e.id,patch)} Field={Field}/><Button className="danger" icon={Trash2} onClick={()=>{edit(removeSelection(docRef.current,[],[e.id]));closeInspector()}}>删除连线</Button></div>}
  </aside>}
  {providerOpen&&<ProviderCatalog key={providerGeneration} token={token}
   isCurrent={()=>providerEpoch.current===providerGeneration} onClose={closeProviderCatalog}/>}
  {workspace&&filesOpen&&<ProjectFiles key={workspace.id} api={api} workspace={workspace} onClose={closeFiles} onAttach={file=>openAssistant({file})}
   onTaskAttach={!readOnly&&!launching&&!launchAttempt?attachTaskFile:undefined}/>}
  {workspace&&sideView==='git'&&<GitWorkspace key={workspace.id+':'+panelEpoch} api={api} workspace={workspace} onClose={closeGit}/>}
  {workspace&&sideView==='assistant'&&<Assistant key={workspace.id+':'+panelEpoch} api={api} workspace={workspace}
   storageKey={environmentRef.current} providers={status?.native_session_writes||[]} catalog={catalog} onDiscover={discover}
   draft={readOnly?null:{id:saved?.id??null,revision:saved?.revision??null,document:doc}}
   files={assistantFiles} onFiles={setAssistantFiles} run={assistantRun} onRun={setAssistantRun} runs={runs}
   onApply={applyAssistant} onClose={closeAssistant}/>}
  {workspace&&nativeOpen&&<NativeSessions key={workspace.id+':'+panelEpoch} api={api} workspace={workspace}
    enabled={Boolean(status?.native_history_available)} writeProviders={status?.native_session_writes}
    workflowLinks={status?.native_workflow_links} workflowTrash={status?.native_workflow_trash}
    terminalAvailable={status?.native_codex_terminal} titleAvailable={status?.native_codex_title}
    catalog={catalog} refreshCatalog={discover}
    onWorkflow={link=>{showRun(link.run,workspace);setLinkedActivation(link.activation);setSelected(link.node.split('/')[0]);closeNative()}}
    isCurrent={()=>inWorkspace(workspace,panelEpoch)} onClose={closeNative}/>}
  {workspace&&sideView==='run-pty'&&runTerminal&&<aside className="project-panel" aria-label="运行关联原生终端">
   <NativePty key={runTerminal.activation} api={api} workspace={workspace} scope={runTerminal} catalog={catalog}
    refreshCatalog={discover} onClose={()=>{setRunTerminal(null);setSideView('')}}/>
  </aside>}
  {workspace&&agentPanel&&<AgentManager key={workspace.id} api={api} workspace={workspace} visible={sideView==='agents'}
   writeProviders={status?.native_session_writes} selectedNode={agentPanel.node} onBind={bindAgent} onClose={closeAgents} onChanged={()=>setAgentRefresh(value=>value+1)}
   onRun={id=>{showRun(id,workspace);refreshRuns(workspace);closeAgents();}}/>}
  {summaryFile&&<RunSummaryFile snapshot={summaryFile.snapshot} onClose={closeRunSummary}/>}
  {planApproval&&planApproval.workspace===workspace?.id&&<PlanApproval value={planApproval} busy={planBusy}
   error={planError} onClose={()=>setPlanApproval(null)} onConfirm={confirmPlan}/>}
  {folder&&<div className="scrim"><section className="dialog folder-dialog" role="dialog" aria-modal="true" aria-label="打开工作空间"><header><h2>打开工作空间</h2><button aria-label="关闭文件夹选择" onClick={hideFolder}><X size={18}/></button></header><p>选择本机允许范围内的文件夹。打开和保存不会启动 Agent。</p><div className="folder-address"><input autoFocus aria-label="文件夹路径" disabled={Boolean(folder.loading)} value={folderPath} onChange={e=>{folderEpoch.current++;setFolderPath(e.target.value);setFolder(f=>f?{...f,loading:false}:f)}} onKeyDown={e=>{if(e.key==='Enter')browse(folderPath)}}/><Button onClick={()=>browse(folderPath)}>前往</Button></div>{folderError&&<p role="alert" className="error-text">{folderError}</p>}<div className="folder-roots">{folder.roots.map(root=><button key={root} onClick={()=>browse(root)}>{root}</button>)}</div><div className="folder-list">{folder.parent&&<button onClick={()=>browse(folder.parent)}>← 返回上级</button>}{folder.entries.map(entry=><button key={entry.path} onClick={()=>browse(entry.path)}><FolderOpen size={16}/>{entry.name}</button>)}{!folder.entries.length&&<p className="muted">此目录没有可浏览的子文件夹</p>}</div><footer><Button onClick={hideFolder}>取消</Button><Button className="primary" disabled={!folder.path||folder.loading||folderPath!==folder.path||!!busy} onClick={()=>navigate(()=>openWorkspacePath(folder.path),'切换工作空间')}>打开此文件夹</Button></footer></section></div>}
  {pending&&<div className="scrim confirm-scrim"><section className="dialog" role="dialog" aria-modal="true" aria-label="未保存的草稿"><h2>保存后再{pending.title}？</h2><p>当前工作流有未保存的修改。空白和不完整草稿也可以保存。</p>{error&&<p role="alert" className="error-text">{error}</p>}<footer><Button autoFocus onClick={closePending}>{pending.title==='关闭工作空间'?'取消关闭':'取消切换'}</Button><Button disabled={!!busy} onClick={guard(async()=>{const operation=pendingRef.current;if(!operation)return;closePending(operation.transfer);await operation.action()})}>放弃修改</Button><Button className="primary" disabled={!!busy} onClick={guard(saveAndContinue)}>保存并继续</Button></footer></section></div>}
 </div>
}
createRoot(document.getElementById('root')).render(<ReactFlowProvider><App/></ReactFlowProvider>);
