import React,{useEffect,useRef,useState} from 'react';
import {StatusBadge} from '../../studio/workbench_ui/RuntimeOverview.jsx';
import {roundText,stateDuration} from '../../studio/workbench_ui/runtime_projection.mjs';

const terminal=new Set(['COMPLETED','FAILED','CANCELLED','SKIPPED']);
const readable=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
const date=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():'未记录';
const endpoint=(route,scope)=>'run-inspector/'+route+'?'+new URLSearchParams(scope);
const payloadNames={output:'节点输出',actual_request:'实际可见请求',request_sections:'请求分节',
 inputs:'持久化输入',native_tool_activity:'原生工具日志',decision:'判定证据',loop_results:'循环轮次'};
const kinds={'run-authorized':'授权本次运行','worker-bound':'绑定运行器','worker-entered':'运行器进入',
 'run-started':'开始调度','node-running':'原生 Agent 已启动','native-input-sent':'已写入原生 CLI',
 'handoff-ready':'固定上游交接','handoff-received':'下游已接收','handoff-sent':'交接随请求写入',
 'node-ready':'节点已就绪','node-pending':'登记节点','node-starting':'开始处理节点',
 'node-completed':'节点完成','node-skipped':'跳过节点','node-failed':'节点失败',
 'output-contract-failed':'输出字段或产物合同失败','handoff-contract-failed':'交接合同失败',
 'node-recovery-required':'节点待核验','merge-completed':'汇合完成','condition-decided':'条件判定',
 'route-selected':'选择路由','validation-decided':'验证结果','human-confirmation-required':'等待人工审批',
 'human-decision':'记录人工决定','human-approved':'人工批准','human-rejected':'人工拒绝',
 'human-timeout':'审批超时','loop-round-started':'开始新一轮','loop-round-completed':'本轮完成',
 'loop-completed':'循环结束','loop-limit-reached':'达到研发循环上限',
 'development-verified':'记录实际测试结果','tool-verified':'记录工具实际校验结果','tool-output-written':'记录工作区文件写入','delivery-gate-decided':'记录独立交付 Gate','cancel-requested':'请求取消','run-finished':'运行结束',
 'native-tool-activity':'记录原生工具活动','event-oversized':'记录超过安全读取上限'};

function download(value){
 const url=URL.createObjectURL(new Blob([JSON.stringify(value)],{type:'application/json'}));
 try{const anchor=document.createElement('a');anchor.href=url;anchor.download='devflower-run-detail.json';anchor.click()}
 finally{setTimeout(()=>URL.revokeObjectURL(url),0)}
}

function ToolResult({api,scope,activation}){
 const [value,setValue]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const ref=activation.output?.result?.tool_artifact;
 const file=activation.tool_output_intent;
 if(!ref&&!file)return null;
 async function read(){
  setBusy(true);setError('');
  try{
   const actual=await api('tool-artifact?'+new URLSearchParams({...scope,activation:activation.id}));
   if(actual.sha256!==ref.sha256||actual.activation!==activation.id)throw Error('工具产物与原激活不一致。');
   setValue(actual);
  }catch(failure){setError(failure.message)}
  finally{setBusy(false)}
 }
 return <section className="tool-artifact" aria-label="校验工具产物">
  {file&&<p className="field-note">{activation.tool_output_verified?'已核验写入':activation.state==='FAILED'?'写入失败':'待核对的输出'} <code>{file.path}</code> · SHA-256 <code>{file.sha256}</code></p>}
  {ref&&(ref.available?<><button disabled={busy} onClick={read}>{busy?'正在读取…':'查看工具 JSON 产物'}</button>
   <small>SHA-256 <code>{ref.sha256}</code></small></>:<p className="field-note">{ref.reason}</p>)}
  {error&&<p role="alert" className="error-text">{error}</p>}
  {value&&<pre aria-label="工具实际 JSON 结果">{readable(value.value)}</pre>}
 </section>;
}

export function PayloadPages({api,workspace,run,activation,field,edge}){
 const [page,setPage]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const epoch=useRef(0);
 useEffect(()=>()=>{epoch.current++},[]);
 async function read(offset=0,sha){
  const ticket=++epoch.current;setBusy(true);setError('');
  try{
   const result=await api(endpoint('payload',{workspace,run,activation,field,offset,...(sha?{sha256:sha}:{}),...(edge?{edge}:{})}));
   if(epoch.current===ticket)setPage(result);
  }catch(failure){if(epoch.current===ticket)setError(failure.message)}
  finally{if(epoch.current===ticket)setBusy(false)}
 }
 const name=payloadNames[field];
 return <section className="paged-evidence">
  {!page?<button disabled={busy} onClick={()=>read()}>分段读取{name}</button>:<>
   <p className="field-note">显示 {page.offset+1}–{page.offset+[...page.text].length} / {page.chars} 字符</p>
   <pre aria-label={name}>{page.text}</pre>
   <div className="run-inspector-actions">
    <button disabled={busy||page.previous_offset===null} onClick={()=>read(page.previous_offset,page.sha256)}>上一段</button>
    <button disabled={busy||page.next_offset===null} onClick={()=>read(page.next_offset,page.sha256)}>下一段</button>
   </div>
   {field==='output'&&<p className="field-note">这是已存输出。仅 Agent 原生回执可证明 Provider 终结。</p>}
   <details><summary>公开视图 SHA-256</summary><code>{page.sha256}</code></details>
  </>}
  {error&&<p role="alert" className="error-text">{error} <button disabled={busy} onClick={()=>read()}>从头读取</button></p>}
 </section>;
}

function EventFeed({api,workspace,run,activation,live}){
 const [entries,setEntries]=useState([]),[next,setNext]=useState(0),[more,setMore]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[older,setOlder]=useState(false);
 const sequence=useRef(0),flight=useRef(false);
 async function load(after){
  if(flight.current)return;
  const current=++sequence.current;
  flight.current=true;setBusy(true);
  try{
   const response=await api(endpoint('events',{workspace,run,after,...(activation?{activation}:{})}));
   if(sequence.current!==current)return;
   setEntries(previous=>{
    const values=after?[...previous,...response.events]:response.events;
    return values.slice(-240);
   });
   setOlder(previous=>after?previous||after>0&&response.events.length>0&&entries.length+response.events.length>240:false);
   setNext(response.after);setMore(response.more);setError('');
  }catch(failure){if(sequence.current===current)setError(failure.message)}
  finally{if(sequence.current===current){flight.current=false;setBusy(false)}}
 }
 useEffect(()=>{load(0);return()=>{sequence.current++;flight.current=false}},[]);
 useEffect(()=>{
  if(!live||busy||more||error)return;
  const timer=setTimeout(()=>load(next),1200);
  return ()=>clearTimeout(timer);
 },[live,busy,more,next,error]);
 return <section aria-label="运行事件" className="run-events">
  {error&&<p role="alert" className="error-text">{error}</p>}
  {!entries.length&&!busy&&<p className="field-note">当前已读区间内没有{activation?'此激活的':''}事件。</p>}
  {older&&<p className="field-note">页面保留最近读到的 240 条事件。可从头重新读取。</p>}
  <ol>{entries.map(entry=><li key={entry.seq}>
   <div><strong>{kinds[entry.kind]||entry.kind||'未命名事件'}</strong><small>#{entry.seq} {date(entry.at)}</small></div>
   <p>{entry.scope==='activation'?`${entry.node} · 第 ${roundText(entry)} 轮`:entry.scope==='run'?'运行级事件':'历史关联未核验'}</p>
   {(entry.reason||entry.action)&&<p>{entry.reason||entry.action}</p>}
   <details><summary>事件证据</summary><pre>{readable(entry)}</pre></details>
  </li>)}</ol>
  <div className="run-inspector-actions">
   <button disabled={busy} onClick={()=>load(more?next:0)}>{busy?'正在读取…':more?'读取下一批事件':'从头读取事件'}</button>
   {error&&next>0&&<button disabled={busy} onClick={()=>load(next)}>重试下一批</button>}
  </div>
 </section>;
}

export function RunInspector({api,snapshot,trusted,selection,onSelect,wide=false,children}){
 const uncertain=['FAILED','RECOVERY_REQUIRED','CANCELLED'].includes(snapshot?.state);
 const initialTab=wide&&(!snapshot||trusted&&!uncertain&&snapshot.state!=='COMPLETED')?'events':'detail';
 const [selectedTab,setTab]=useState(wide&&!snapshot?null:initialTab);
 const tab=selectedTab??initialTab;
 const [chosen,setChosen]=useState(selection?.activation||''),[exportError,setExportError]=useState('');
 useEffect(()=>{if(snapshot)setTab(current=>current??initialTab)},[Boolean(snapshot),initialTab]);
 const [exporting,setExporting]=useState(false);
 const mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const run=snapshot?.run,nodes=snapshot?.nodes||[];
 const eligible=selection?.node?nodes.filter(row=>row.node===selection.node||row.node.startsWith(selection.node+'/')):nodes;
 const exact=eligible.find(row=>row.id===chosen);
 const activation=exact||nodes.find(row=>row.node===selection?.node);
 const scope=run&&{workspace:run.workspace,run:run.id};
 const live=trusted&&run&&snapshot.worker_alive&&!terminal.has(snapshot.state);
 const errors=nodes.filter(row=>['FAILED','RECOVERY_REQUIRED','CANCELLED'].includes(row.state));
 const errorActivations=errors.map(row=><button key={row.id} onClick={()=>{
  if(wide){setChosen(row.id);setTab('detail')}else onSelect(row.node)
 }}>{row.name||row.node} · 第 {roundText(row)} 轮 · {row.reason||'查看已记录错误'}</button>);
 const detail=tab==='detail'&&children?.(activation);
 async function exportRun(){
  if(!scope||exporting)return;
  setExporting(true);setExportError('');
  try{
   const result=await api(endpoint('export',scope));
   if(mounted.current)download(result);
  }catch(failure){if(mounted.current)setExportError(failure.message)}
  finally{if(mounted.current)setExporting(false)}
 }
 return <div className="inspector-content run-inspector-content">
  {(!wide||activation)&&<header className="run-inspector-head">
   <StatusBadge state={trusted?(activation?.state||snapshot?.state):'UNKNOWN'}/>
   <span>{activation?`第 ${roundText(activation)} 轮 · ${stateDuration(activation.started_at,activation.finished_at,activation.state)}`:
    run?stateDuration(run.started_at,run.finished_at,snapshot.state):'读取原运行'}</span>
  </header>}
  {wide&&trusted&&(errors.length>0||uncertain&&run?.reason)&&
   <section className="run-evidence-issues" aria-label="失败与待核验激活">
    {uncertain&&run?.reason&&<p className="error-text">{run.reason}</p>}
    {errorActivations}
   </section>}
  {activation?.native_profile_id&&<p className="field-note">独立原生 Profile {activation.native_profile_id}</p>}
  <div className="run-inspector-toolbar">
  {eligible.length>0&&!selection?.edge&&<label className="field"><span>激活与轮次</span><select aria-label="激活与轮次"
   value={activation?.id||''} onChange={event=>{setChosen(event.target.value);if(!wide)setTab('detail')}}>
   <option value="">{selection?.node?'当前画布节点':'整次运行'}</option>
   {eligible.map(row=><option key={row.id} value={row.id}>{row.name||row.node} · 第 {roundText(row)} 轮</option>)}
  </select></label>}
  <nav className="run-inspector-actions" aria-label="检查器视图">
   {Object.entries({detail:'详情',events:'事件',logs:'日志'}).map(([key,label])=><button key={key} aria-pressed={tab===key}
    onClick={()=>setTab(key)}>{label}</button>)}
   <button disabled={!scope||exporting} onClick={exportRun}>{exporting?'正在导出…':'导出运行详情'}</button>
  </nav>
  </div>
  {tab==='detail'&&trusted&&activation?.kind==='tool'&&<ToolResult key={activation.id} api={api} scope={scope} activation={activation}/>}
  {exportError&&<p role="alert" className="error-text">{exportError}</p>}
  {tab==='detail'&&(detail||<div className="run-inspector-overview">
   <dl><dt>运行</dt><dd>{run?.id||'读取中'}</dd><dt>登记</dt><dd>{date(run?.created_at)}</dd><dt>开始</dt><dd>{date(run?.started_at)}</dd>
    <dt>结束</dt><dd>{date(run?.finished_at)}</dd><dt>持久激活</dt><dd>{nodes.length}</dd></dl>
   {run?.execution_budget&&<section aria-label="运行预算"><h3>运行预算</h3>
    <dl><dt>已登记调用</dt><dd>{run.execution_budget.admitted_calls} / {run.execution_budget.calls}</dd>
     <dt>已启动原生进程</dt><dd>{run.execution_budget.native_process_starts}</dd>
     <dt>运行时间上限</dt><dd>{run.execution_budget.seconds} 秒</dd>
     <dt>截止时间</dt><dd>{date(run.execution_budget.deadline_at)}</dd></dl></section>}
   {!wide&&run?.reason&&<p className="error-text">{run.reason}</p>}
   {run?.resume_available&&<p className="field-note">运行状态区提供原检查点的恢复入口。</p>}
   {!wide&&errorActivations}
  </div>)}
  {tab==='events'&&scope&&<EventFeed key={run.id+':'+(activation?.id||'all')} api={api} {...scope} activation={activation?.id} live={live}/>}
  {tab==='logs'&&<div className="run-inspector-logs">
   {activation?<><p className="field-note">公开日志展示 CLI 已记录的诊断和最近保留的 40 条工具活动。</p>
    {activation.visible_error&&<pre aria-label="节点公开诊断">{activation.visible_error}</pre>}
    {activation.reason&&<p>{activation.reason}</p>}
    {activation.native_tool_activity?.length>0&&<pre aria-label="原生工具日志">{readable(activation.native_tool_activity)}</pre>}
    {scope&&activation.paged_fields?.native_tool_activity&&<PayloadPages api={api} {...scope} activation={activation.id} field="native_tool_activity"/>}
    {!activation.native_tool_activity?.length&&!activation.paged_fields?.native_tool_activity&&!activation.visible_error&&!activation.reason&&
      <p>此激活尚无公开工具活动或错误记录。</p>}
   </>:<p>选择具体激活以查看已记录日志。</p>}
  </div>}
 </div>;
}
