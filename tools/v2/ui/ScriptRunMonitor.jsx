import React,{useEffect,useRef,useState} from 'react';
import {downloadPython} from './PythonPanel.jsx';

function EvidenceText({label,value}) {
 if(!value)return null;
 return <section><h3>{label}</h3><pre aria-label={label}>{value.text}</pre>
  {value.truncated&&<p className="field-note">此处显示部分内容，完整文件在本机证据目录。</p>}
 </section>;
}

const loopStates={NOT_STARTED:'未开始',STARTING:'准备调用',RUNNING:'运行中',VERIFYING:'核验中',
 COMPLETED:'已完成',PASSED:'通过',FAILED:'失败',INVALID:'候选已变化',SKIPPED:'未执行',CANCELLED:'已取消',UNKNOWN:'待核验'};
const loopDecisions={LOOP_CONTINUED:'继续返工',LOOP_ACCEPTED:'接受',LOOP_LIMIT_REACHED:'达到迭代上限'};
function roundActors(round){
 const actors=Object.entries(round.agents);
 if(round.validation)actors.splice(1,0,[round.validation.node,round.validation]);
 return actors;
}

function durationBetween(start,end){
 const seconds=Math.floor((end-start)/1000);
 if(!Number.isFinite(seconds)||seconds<0)return '';
 if(seconds<60)return seconds+' 秒';
 const minutes=Math.floor(seconds/60);
 return (minutes>=60?Math.floor(minutes/60)+' 小时 ':'')+minutes%60+' 分 '+seconds%60+' 秒';
}

function readHandoff(evidence,hasInput){
 const prompt=evidence?.prompt?.text;
 const marker='\n\nUpstream department delivery (data)\n';
 const raw=evidence?.upstream?.text||(hasInput&&prompt?.includes(marker)?prompt.split(marker).at(-1).split('\n')[0]:null);
 if(!raw)return null;
 try{
  const delivery=JSON.parse(raw);
  return typeof delivery?.node==='string'&&typeof delivery.answer==='string'?delivery:null;
 }catch{return null}
}

export function ScriptRunMonitor({api,snapshot,trusted,selected,stateLabel,onExplain,eventIndex}) {
 const {run,script,state}=snapshot;
 const [choice,setChoice]=useState(selected||'');
 const [evidence,setEvidence]=useState(null);
 const [error,setError]=useState('');
 const evidencePicker=useRef(null);
 const {events,started,forNode}=eventIndex;
 const loop=script.loop;
 const [iteration,selectedNode]=choice.includes('/')?choice.split('/'):[null,choice];
 const round=loop?.iterations.find(item=>String(item.iteration)===iteration);
 const outer=script.document.nodes.filter(item=>Object.hasOwn(script.agents,item.id)||item.config?.tool==='command');
 const tool=Boolean(selectedNode)&&(iteration?round?.validation?.node===selectedNode:
  outer.some(item=>item.id===selectedNode&&item.config?.tool==='command'));
 const agent=(iteration?script.loop_agents:script.agents)?.[selectedNode];
 const node=(tool||agent)?selectedNode:'';
 const completed=(iteration?round?.completed_nodes:script.completed_nodes)||[];
 const nodeEvents=id=>forNode(id,iteration);
 const facts=nodeEvents(node);
 const command=facts.find(event=>event.kind==='CALL_INTENT');
 const exit=facts.find(event=>event.kind==='PROCESS_EXITED');
 const reason=facts.findLast(event=>event.reason)?.reason;
 const reviews=events.filter(event=>event.kind==='NODE_COMPLETED'&&!event.iteration&&event.node!==loop?.node&&
  event.verdict&&completed.includes(event.node));
 const router=script.router;
 const decision=script.condition||router;
 const skippedPorts=Object.keys(decision?.branches||{}).filter(port=>port!==decision.selected_port);
 const skipped=skippedPorts.flatMap(port=>decision.branches[port]);
 const body=script.document.nodes.find(item=>item.id===loop?.node)?.config.body;
 const nameOf=id=>script.document.nodes.find(item=>item.id===id)?.name||id;
 const roundName=id=>body?.nodes.find(item=>item.id===id)?.name||id;
 const eventName=event=>event.iteration&&event.node?'第 '+event.iteration+' 轮 · '+roundName(event.node):
  nameOf(event.node||event.loop_node);
 const terminal=events.findLast(event=>['RUN_COMPLETED','RUN_STOPPED'].includes(event.kind));
 const lastProcess=events.findLast(event=>['PROCESS_STARTED','PROCESS_EXITED','CANCELLED','UNKNOWN','NOT_SENT'].includes(event.kind));
 const live=trusted&&script.active&&['RUNNING','CANCEL_REQUESTED'].includes(state)&&!terminal;
 const current=live&&lastProcess?.kind==='PROCESS_STARTED'?eventName(lastProcess):'';
 const failed=trusted&&state==='STOPPED'&&events.findLast(event=>[
  'CLI_FAILED','OUTPUT_CONTRACT_FAILED','NOT_SENT','UNKNOWN','VALIDATION_INVALID','CONDITION_REJECTED','ROUTER_REJECTED','LOOP_LIMIT_REACHED',
 ].includes(event.kind));
 const startedAt=Date.parse(events[0]?.at);
 const spent=trusted&&!['RECOVERY_REQUIRED','UNKNOWN'].includes(state)?
  durationBetween(startedAt,live?Date.now():Date.parse((terminal||events.at(-1))?.at)):'';
 function outerState(id){
  const facts=nodeEvents(id);
  const exited=facts.find(event=>event.kind==='PROCESS_EXITED');
  if(skipped.includes(id))return '未执行';
  if(completed.includes(id))return exited&&exited.returncode!==0?'失败':'成功';
  if(facts.some(event=>event.kind==='CANCELLED'))return '已取消';
  if(facts.some(event=>event.kind==='NOT_SENT'))return '启动失败';
  if(facts.some(event=>['CLI_FAILED','OUTPUT_CONTRACT_FAILED','UNKNOWN'].includes(event.kind)))return '未完成';
  if(id===loop?.node&&loop.iterations.length)return live?'运行中':state==='CANCELLED'?'已取消':'未完成';
  return facts.length?(live?'运行中':'未完成'):'未启动';
 }
 const rules=script.document.nodes.find(item=>item.id===router?.node)?.config.rules||[];
 const labelPort=port=>router?(port==='fallback'?'默认分支（fallback）':(rules.find(rule=>rule.id===port)?.label||port)+'（'+port+'）'):
  port==='true'?'满足（true）':'不满足（false）';
 const pathLabel=port=>decision.branches[port].map(nameOf).join(' → ');
 const path=new URLSearchParams({workspace:run.workspace,id:run.id,...(node?{node}:{}),
  ...(node&&round?{iteration}:{} )}).toString();
 useEffect(()=>{
  if(!trusted)return;
  let current=true;
  api('script-run/evidence?'+path).then(value=>{
   if(current){setEvidence({path,value});setError('')}
  }).catch(failure=>{if(current)setError(failure.message)});
  return()=>{current=false};
 },[path,state,events.length,trusted]);
 const shown=evidence?.path===path?evidence.value:null;
 const incoming=(graph,id)=>graph?.edges.some(edge=>edge.target===id&&edge.transfer?.mode!=='none'&&
  graph.nodes.find(item=>item.id===edge.source)?.kind!=='start');
 const handoff=trusted&&readHandoff(shown,iteration?(incoming(body,node)||incoming(script.document,loop.node)):
  incoming(script.document,node));
 function inspectChoice(value){
  setChoice(value);
  evidencePicker.current?.focus();
 }
 async function download(){
  setError('');
  try{await downloadPython(await api('script-run/source?'+new URLSearchParams({workspace:run.workspace,id:run.id})))}
  catch(failure){setError(failure.message)}
 }
 return <div className="inspector-content run-evidence script-evidence" aria-label="原生脚本运行证据">
  <section className="script-overview" aria-label="运行概览">
   <div className="script-metrics">
    <span className="workbench-state" data-state={trusted?state:'UNKNOWN'}>{stateLabel(trusted?state:'UNKNOWN')}</span>
    {spent&&<span>{terminal?'总耗时':'已用时'} <strong>{spent}</strong></span>}
    {current&&<span>正在执行 <strong>{current}</strong></span>}
   </div>
   {trusted&&script.reason&&<p className="error-text">{failed&&(failed.node||failed.loop_node)?eventName(failed)+' · ':''}{script.reason}</p>}
   {!trusted&&<p className="field-note">下方是上次读取的证据。</p>}
  </section>
  {error&&<p className="error-text" role="alert">{error}</p>}
  <label className="field"><span>查看证据</span><select ref={evidencePicker} value={node?choice:''} onChange={event=>setChoice(event.target.value)}>
   <option value="">最终结果</option>
   {outer.map(item=><option key={item.id} value={item.id}>
    {item.name}{skipped.includes(item.id)?'（分支未选中）':''}
   </option>)}
   {loop?.iterations.flatMap(item=>roundActors(item).map(([id])=>{
    const key=item.iteration+'/'+id;
    return <option key={key} value={key}>第 {item.iteration} 轮 · {roundName(id)}</option>;
   }))}
  </select></label>
  {node?<div className="script-evidence-detail" key={choice}>
   <p>{tool?(iteration?'验证命令':nameOf(node)):agent.provider==='codex'?'Codex CLI':'Claude Code'}
    {!iteration&&(tool||loop)&&' · '+outerState(node)}
    {exit?' · 退出码 '+exit.returncode:''}</p>
   {reason&&<p className="error-text">{reason}</p>}
   {!tool&&<EvidenceText label={completed.includes(node)?'最终回答':'已保存的回答文本'} value={shown?.answer}/>}
   {handoff&&<p className="field-note">上游交接 · {handoff.name||handoff.node}
    {Number.isInteger(handoff.structured?.result?.returncode)?' · 退出码 '+handoff.structured.result.returncode:''}
    {handoff.candidate?.candidate_sha256&&' · 含候选清单'}
   </p>}
   {completed.includes(node)&&(shown?.structured||shown?.candidate)&&<details><summary>结构化交付与候选文件</summary>
    <EvidenceText label="结构化结果" value={shown?.structured}/>
    <EvidenceText label="候选清单" value={shown?.candidate}/>
   </details>}
   {(shown?.prompt||shown?.upstream)&&<details><summary>请求与上游交接</summary>
    <EvidenceText label="命令标准输入（上游交接）" value={shown?.upstream}/>
    <EvidenceText label="实际请求（含上游交接）" value={shown?.prompt}/>
   </details>}
   <details><summary>调用与错误记录</summary>
    {tool&&command&&<pre aria-label="实际命令与工作目录">{JSON.stringify({argv:command.argv,cwd:command.cwd},null,2)}</pre>}
    {!tool&&command&&<pre>{JSON.stringify({argv:command.argv,cwd:command.cwd},null,2)}</pre>}
    {facts.length>0&&<ol>{facts.map((event,index)=><li key={index}>
     {event.at} · {event.kind}{event.reason?' · '+event.reason:''}
    </li>)}</ol>}
    <EvidenceText label={tool?(iteration?'验证 stdout':'命令 stdout'):'原生 CLI stdout'} value={shown?.stdout}/>
    <EvidenceText label={tool?(iteration?'验证 stderr':'命令 stderr'):'原生 CLI stderr'} value={shown?.stderr}/>
   </details>
   {!command&&<p>{skipped.includes(node)?(tool?'此命令所在分支未选中。':'此 Agent 所在分支未选中。'):
    tool?'尚无命令调用记录。':'尚无 CLI 调用记录。'}</p>}
  </div>:<div className="script-evidence-detail">
   {trusted&&state==='COMPLETED'?<EvidenceText label="工作流最终回答" value={shown?.result}/>:
    <>
     <p>尚无已核验的最终结果。</p>
     {trusted&&script.historical_completion&&<details><summary>查看已核验的历史结果</summary>
      <EvidenceText label="冻结最终结果" value={shown?.result}/>
     </details>}
    </>}
   {trusted&&onExplain&&<button type="button" onClick={onExplain}>让 AI 解释这次结果</button>}
   {!loop&&!decision&&started.length>0&&<section aria-label="实际执行顺序">
    <h3>实际执行顺序</h3><ol>{started.map((event,index)=><li key={index}>
     <button type="button" onClick={()=>inspectChoice(event.node)}>{nameOf(event.node)}</button>{' · '+outerState(event.node)}
    </li>)}</ol>
   </section>}
   {script.steps?.length>1&&loop&&<section aria-label="外层执行顺序">
    <h3>外层执行顺序</h3><ol>{script.steps.map(id=><li key={id}>
     {id===loop.node?nameOf(id):<button type="button" onClick={()=>inspectChoice(id)}>{nameOf(id)}</button>}
     {' · '+outerState(id)}
    </li>)}</ol>
   </section>}
   {loop&&<section aria-label="循环轮次">
    <h3>{nameOf(loop.node)} · 最多 {loop.max_iterations} 轮</h3>
    {loop.iterations.map(item=><section key={item.iteration} aria-label={'第 '+item.iteration+' 轮'}>
     <h4>第 {item.iteration} 轮{item.historical?' · 历史候选':''}
      {item.disposition?' · '+loopDecisions[item.disposition]:''}</h4>
     <ul>{roundActors(item).map(([id,actor])=><li key={id}>
      <button type="button" onClick={()=>inspectChoice(item.iteration+'/'+id)}>{roundName(id)}</button>
      {' · '+loopStates[actor.state]}
      {actor===item.validation&&Number.isInteger(actor.returncode)&&' · 退出码 '+actor.returncode}
     </li>)}</ul>
     {item.candidate_sha256&&<p>候选摘要 <code>{item.candidate_sha256}</code></p>}
     {item.review&&<div>
      <p>审核 <code>{item.review.verdict}</code> · {item.review.summary}</p>
      {item.review.issues.length>0&&<ul>{item.review.issues.map((issue,index)=><li key={index}>
       <code>{issue.file}</code> · {issue.severity} · {issue.description} · {issue.suggestion}
      </li>)}</ul>}
     </div>}
    </section>)}
    {script.candidate_current===false&&<p>最终候选已变化，原审核保留为历史记录。</p>}
   </section>}
   {decision&&<section aria-label={router?'路由分支':'条件分支'}>
    <h3>{nameOf(decision.node)}</h3>
    {router?<><p>上游 {nameOf(router.source_node)} · <button type="button"
     onClick={()=>inspectChoice(router.source_node)}>查看上游完整交付</button></p>
     <EvidenceText label="上游参与路由的结构化输入" value={{text:router.input_json}}/>
     <p>{router.matched_rule?'命中规则 '+labelPort(router.matched_rule):'未命中规则，使用 fallback'}</p>
     <p>选中端口 <code>{router.selected_port}</code></p>
    </>:<>
     <p>判断结果 <code>{decision.input_verdict}</code> · 选中 {labelPort(decision.selected_port)}</p>
     <p>候选摘要 <code>{decision.candidate_sha256}</code></p>
     {script.candidate_current===false&&<p>当前候选已变化。分支记录对应判断时的候选版本。</p>}
    </>}
    <p>选中路径 {pathLabel(decision.selected_port)}</p>
    {skippedPorts.map(port=><p key={port}>未选中 {labelPort(port)} · {pathLabel(port)}</p>)}
    <p>实际启动 {started.map(event=>nameOf(event.node)).join(' → ')}</p>
   </section>}
   {reviews.map(review=><p key={review.node}>
    {nameOf(review.node)} · {review.verdict==='accept'?'接受':'需要返工'}
    {state!=='COMPLETED'||script.candidate_current===false?'（历史审核记录）':''} · <code>{review.candidate_sha256}</code>
   </p>)}
   {events.length>0&&<details><summary>运行日志</summary><ol>{events.map((event,index)=><li key={index}>
    {event.at} · {event.kind}{event.node?' · '+event.node:''}
    {event.iteration?' · 第 '+event.iteration+' 轮':''}
    {typeof event.returncode==='number'?' · 退出码 '+event.returncode:''}
   </li>)}</ol></details>}
  </div>}
  <details className="script-artifact"><summary>脚本与配置版本</summary><pre>{JSON.stringify({
   filename:script.artifact.filename,sha256:script.artifact.sha256,draft_revision:run.draft_revision,agents:script.agents,
   ...(loop?{loop_agents:script.loop_agents}:{}),evidence_directory:script.directory
  },null,2)}</pre></details>
  <p><button disabled={!trusted} onClick={download}>下载本次运行脚本</button></p>
 </div>;
}
