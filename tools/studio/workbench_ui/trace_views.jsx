import React,{useState} from 'react';
import Markdown from './DocumentLinks.jsx';
import remarkGfm from 'remark-gfm';
import {ReadableContent} from './ConversationView.jsx';
import {handoffDeliveries} from './handoff_deliveries.mjs';

const names = {answer:'最终回答', summary:'摘要', result:'结构化结果', artifacts:'产物引用', evidence:'验证与审核证据',
  issues:'未解决问题', next:'下一步', sources:'来源', accepted:'满足条件', reference:'文件引用', accessible:'可访问',
  source_round:'来源轮次',target_round:'目标轮次',source_name:'来源节点名称',target_name:'目标节点名称',received_at:'实际接收时间',target_node:'目标节点',source:'来源节点', content:'实际内容', sha256:'内容身份', node:'节点', iteration:'轮次', iteration_path:'完整轮次路径'};

export function DataValue({value, depth=0}) {
  if (value === null || value === undefined) return <span className="hint">无</span>;
  if (typeof value === 'string') return <Markdown remarkPlugins={[remarkGfm]} skipHtml>{value}</Markdown>;
  if (typeof value === 'boolean') return <span>{value?'是':'否'}</span>;
  if (typeof value !== 'object') return <span>{String(value)}</span>;
  if (value.truncated) return <div className="hint">交接预算已截断：原 {value.original_chars} 字符，预算 {value.budget}<pre>{value.text}</pre></div>;
  const entries=Object.entries(value).filter(([key])=>!['receipt','authorization','credentials'].includes(key));
  const identity=([key,v])=>['run','activation','sha256','session_id','raw_report_sha256','workflow_id','target_activation','content_sha256','source_content_sha256'].includes(key)&&typeof v==='string'&&/^[a-f\d-]{24,}$/i.test(v);
  const identifiers=entries.filter(identity);
  const body = Array.isArray(value) ? <ol className="data-array">{value.map((v,i)=><li key={i}><DataValue value={v} depth={depth+1}/></li>)}</ol> :
    <><dl className="data-object">{entries.filter(e=>!identity(e)).map(([key,v])=><React.Fragment key={key}><dt>{names[key]||key}</dt><dd><DataValue value={v} depth={depth+1}/></dd></React.Fragment>)}</dl>{identifiers.length>0&&<details className="identity-evidence"><summary>原始数据绑定身份</summary><dl className="handoff-facts">{identifiers.map(([key,v])=><React.Fragment key={key}><dt>{key}</dt><dd>{v}</dd></React.Fragment>)}</dl></details>}</>;
  return depth>2 ? <details><summary>展开{Array.isArray(value)?value.length+'项数据':'字段内容'}</summary>{body}</details> : body;
}

export function VisibleRequest({activation, defaultOpen=false}) {
  const [section,setSection]=useState('request');
  if (!activation) return null;
  const receipt=activation.receipt;
  const sent=Boolean(activation.input_sent_sha256);
  return <details className="actual-request" data-testid="actual-provider-request" open={defaultOpen||undefined}><summary>本次实际 Provider 请求 · {activation.input_sent_sha256?'已完整发送':'尚无完整发送回执'}</summary>
    <p className="hint">以下来自该运行的持久记录，不是草稿模板。实际物理后端与生效 effort：unknown。可见内容已脱敏；初始化{!activation.actual_request?'发送状态 unknown':activation.initialization_included?(sent?'本次已包含并发送':'已组装，发送待确认'):'本次未重复发送'}。</p>
    <nav className="request-tabs" aria-label="本轮请求分区">{[['request','A–D 请求组成'],['complete','E 完整请求'],['receipt','F 原生回执']].map(([key,label])=><button key={key} className={section===key?'active':''} onClick={()=>setSection(key)}>{label}</button>)}</nav>
    <div hidden={section!=='request'}><section><h4>A. 初始化提示词</h4><p>{activation.initialization_included?(sent?'本回合包含，已写入原生工具输入':'已组装，发送待确认'):'本回合未包含；此前初始化状态以原生记录为准'}</p></section>
    {(activation.request_sections||[]).map((part,i)=>{let data;try{if(part.title.startsWith('上游输入'))data=JSON.parse(part.text)}catch{}return <section key={i}><h4>{part.title.startsWith('每轮')?'B. ':part.title.startsWith('上游')?'C. ':part.title.startsWith('输出')?'D. ':''}{part.title}</h4>{data?<DataValue value={data}/>:<Markdown skipHtml>{part.text}</Markdown>}</section>})}
    {!activation.request_sections?.length&&<p className="hint">此历史回合没有保存分块证据。请展开完整可见请求核对，不能由当前模板补造。</p>}
    </div><section hidden={section!=='complete'}><h4>E. 最终组装的可见请求</h4><pre>{activation.actual_request||'尚无实际请求；不能使用模板补造'}</pre><p className="hint">这是运行器保存的脱敏请求。原生接口未提供服务端完整请求镜像，服务端逐字段接收状态 unknown。</p></section>
    <section hidden={section!=='receipt'} className="provider-receipt"><h4>Provider 实际回执</h4><dl className="handoff-facts"><dt>输入发送</dt><dd>{sent?'运行器确认完整写入原生工具输入':'尚无完整发送回执'}</dd><dt>请求配置</dt><dd>{receipt?.requested_model||activation.model||'unknown'} / {receipt?.requested_effort||activation.effort||'unknown'}</dd><dt>CLI 回报</dt><dd>{receipt?.resolved_model||'unknown'} · effort {receipt?.resolved_effort||'unknown'}</dd><dt>实际后端</dt><dd>unknown（未提供可独立核验的信息）</dd><dt>Provider 接收</dt><dd>{receipt?'原生回合有完成回执；内部逐字段接收 unknown':'unknown（尚无原生完成回执）'}</dd></dl></section>
    <details><summary>完整可见请求与身份</summary><pre>{activation.actual_request||'尚未产生真实请求'}</pre><dl className="handoff-facts"><dt>运行</dt><dd>{activation.run}</dd><dt>Activation</dt><dd>{activation.id}</dd><dt>完整轮次</dt><dd>{(activation.iteration_path?.length?activation.iteration_path:[activation.iteration||1]).join(' / ')}</dd><dt>实际发送SHA</dt><dd>{activation.input_sent_sha256||'尚无回执'}</dd><dt>原生会话</dt><dd>{receipt?.session_id||activation.observed_session||'unknown'}</dd></dl></details>
  </details>;
}

function handoffRound(path, activation) {
  const values=Array.isArray(path)?path:activation?.iteration_path;
  if(values?.length)return values.every(n=>Number.isInteger(n)&&n>0)?values.join(' / '):'unknown';
  // Top-level activations have an empty loop path, not an unknown round.
  const round=activation?.round??(Number.isInteger(activation?.iteration)?activation.iteration||1:undefined);
  return Number.isInteger(round)&&round>0?String(round):'unknown';
}

export function HandoffTrace({edge, prefix, events, snapshot}) {
  const matches = handoffDeliveries(events,snapshot?.run.id).filter(e=>e.edge===edge.id && e.source===prefix+edge.source && e.node===prefix+edge.target);
  if(!matches.length)return <p className="hint">尚无本运行、本连线的实际交接记录。配置预览不代表已经发送。</p>;
  return <div data-testid="handoff-trace">{matches.map(event=>{
    const source=snapshot.nodes.find(a=>a.id===event.activation);
    // A shared node/round/input does not prove which activation received it.
    const target=event.target_activation?snapshot.nodes.find(a=>a.id===event.target_activation&&a.node===event.node):undefined;
    const state=event.kind==='handoff-sent'?'已完整发送到原生工具输入':event.kind==='handoff-received'?'确定性节点已接收':'内容已固定，等待发送';
    return <details key={event.seq} open><summary>第 {(event.iteration_path?.length?event.iteration_path:[event.iteration||1]).join(' / ')} 轮 · {state}</summary>
      <p className="handoff-route"><b>{source?.name||event.source_name||event.source}</b> → <b>{target?.name||event.target_name||event.node}</b></p><small>{edge.transfer==='field'?edge.field:names[edge.transfer||'result']||edge.transfer} · 第 {handoffRound(event.source_round,source)} 轮 → 第 {handoffRound(event.target_round,target)} 轮</small>
      <div className="handoff-readable"><ReadableContent value={event.content}/></div><details><summary>高级：字段、时间与运行身份</summary><DataValue value={event.content}/><dl className="handoff-facts"><dt>接收状态</dt><dd>{state}</dd><dt>来源轮次</dt><dd>{handoffRound(event.source_round,source)}</dd><dt>目标轮次</dt><dd>{handoffRound(event.target_round,target)}</dd><dt>内容 SHA-256</dt><dd>{event.content_sha256||'历史记录未提供'}</dd><dt>首次接收时间</dt><dd>{event.received_at?new Date(event.received_at*1000).toLocaleString('zh-CN'):'unknown'}</dd><dt>记录时间</dt><dd>{event.at?new Date(event.at*1000).toLocaleString('zh-CN'):'unknown'}</dd><dt>运行</dt><dd>{event.run}</dd><dt>来源Activation</dt><dd>{event.activation}</dd><dt>目标Activation</dt><dd>{target?.id||'尚无对应激活'}</dd><dt>调用前源码</dt><dd>{source?.source_at_request?.sha256||'此确定性来源没有模型源码回执'}</dd><dt>工作台候选</dt><dd>{snapshot.run.candidate_sha256||'历史记录未提供'}</dd></dl></details>
    </details>;
  })}</div>;
}
