import React from 'react';
import {Activity,ArrowRight,Check,Clock3,AlertCircle} from 'lucide-react';
import {statusInfo,roundText,duration,stateDuration} from './runtime_projection.mjs';
import {usageSummary} from './usage_summary.mjs';

export function StatusBadge({state}){
 const [label,tone,symbol]=statusInfo(state||'DRAFT');
 return <span className={'badge '+(state||'draft').toLowerCase()+' tone-'+tone}><span aria-hidden="true">{symbol}</span>{label}</span>;
}
export function RuntimeOverview({projection:p,snapshot,viewMode,onSelect,onRun,onReconnect}){
 const usage=usageSummary(snapshot?.nodes||[]);
 if(viewMode!=='run')return null;
 return <section className={'runtime-overview tone-'+statusInfo(p.state)[1]} aria-label="运行总览" aria-live="polite" data-testid="runtime-overview">
  <div className="overview-head"><div className="overview-title"><Activity size={18}/><strong>本次运行</strong><StatusBadge state={p.state}/><span className="frozen-label">冻结快照 · 只读</span></div><span className="elapsed"><Clock3 size={13}/>{p.trusted?stateDuration(snapshot.run.started_at||snapshot.run.created_at,snapshot.run.finished_at,p.state):'耗时待确认'}</span></div>
  {p.trusted&&<details className="run-usage" aria-label="实际调用与用量"><summary>调用与用量</summary><div><span>任务模型调用 {snapshot.run.actual_calls||0} 次 · 运行中连接检查 {snapshot.run.connection_checks||0} 次</span><span>已报告任务 Token：{usage.reported?`输入 ${usage.input} / 输出 ${usage.output}${usage.complete?'':'（部分统计）'}`:'Provider 未提供统计'}</span><span>Provider 报告费用（非核验账单）：{usage.costReported?(usage.cost===null?'已报告金额无法安全汇总（部分报告）':`USD ${usage.cost.toLocaleString('en-US',{maximumSignificantDigits:12})}${usage.costComplete?'':'（部分报告）'}`):'Provider 未报告费用'}</span></div></details>}
  <div className="overview-content"><div className="overview-current"><small>当前节点</small><strong>{!p.trusted?'正在重新核验状态':p.active.length?p.active.map(x=>x.node.name).join('、'):p.state==='COMPLETED'?'流程已结束':p.state==='PAUSED'?'等待继续调度':p.state==='FAILED'?'执行已停止，请查看原因':p.waiting.length?'等待输入或确认':p.state==='CANCELLED'?'本次任务已取消':'正在等待调度'}</strong><span>{p.active.map(x=>`第 ${roundText(x.activation)} 轮 · ${duration(x.activation.started_at)}`).join('；')}</span></div><div className="overview-progress"><small>已结束 / 已执行节点</small><strong>{p.trusted?`${p.settled} / ${p.activated}`:'— / —'}</strong><span>{p.trusted?<><Check size={12}/>基于真实运行记录</>:'旧状态已隐藏'}</span>{p.trusted&&p.latest.some(x=>x.activation?.state==='SKIPPED')&&<small>{p.latest.filter(x=>x.activation?.state==='SKIPPED').length} 个节点已跳过</small>}{p.trusted&&p.unactivated>0&&<small>{p.unactivated} 个设计节点未激活{p.inheritedSkipped>0?`（其中 ${p.inheritedSkipped} 个随所属循环跳过）`:''}</small>}</div></div>
  {(p.reason||p.waiting.length>0)&&<div className="overview-attention">{p.reason&&<p><AlertCircle size={14}/>{p.reason}{!p.trusted&&<button onClick={onReconnect}>重新读取</button>}</p>}{p.waiting.map(({node,activation:a})=><button key={node.path} onClick={()=>onSelect(node.path)}>{a.state==='WAITING_CONFIRMATION'?`确认「${node.name}」· 等待人工确认`:`${node.name} · 已收到 ${a.received||0}/${a.expected||0} 个输入；等待 ${(a.waiting||[]).map(id=>p.latest.find(x=>x.node.path===id)?.node.name||id).join('、')}`}</button>)}</div>}
 </section>;
}
