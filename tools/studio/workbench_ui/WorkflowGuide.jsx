import React,{useState} from 'react';
import { X, ListChecks, Sparkles } from 'lucide-react';
import './workflow-guide.css';

export function WorkflowGuide({open,onClose,onFolder,onTemplates,onNodes,onTask,onCheck,onHistory,workspace,graph,task}) {
 const [expanded,setExpanded]=useState(false);
 if(!open)return null;
 const step=!workspace?['打开工作区',onFolder]:!graph?.nodes?.length?['选择模板或添加节点',onTemplates]:!task?.trim()?['填写本次任务',onTask]:['检查工作流',onCheck];
 return <section className="workflow-guide" aria-label="工作流使用指南">
  <header><strong><ListChecks size={14}/>使用指南</strong><button onClick={step[1]}>下一步：{step[0]}</button><button aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?'收起步骤':'查看步骤'}</button><button aria-label="关闭使用指南" onClick={onClose}><X size={15}/></button></header>
  {expanded&&<ol><li><button onClick={onFolder} aria-label="向导：打开工作区">打开工作区</button>，选择任务所在工作区。</li><li><button onClick={onTemplates} aria-label="向导：选择示例模板">选择示例模板</button>，或从空白画布开始。</li><li><button onClick={onNodes} aria-label="向导：添加节点">添加节点</button>，点击节点配置模型与提示词，拖动连接点连线。节点太小时，点击画布下方“专注画布”展开工作区域。</li><li><button onClick={onTask} aria-label="向导：填写本次任务">填写本次任务</button>，说明希望完成什么。</li><li><button onClick={onCheck} aria-label="向导：检查工作流">检查工作流</button>，修复错误，再点击顶部“开始运行”。</li><li>查看底部活动和结果；<button onClick={onHistory} aria-label="向导：整轮对话">整轮对话</button>保留历史。需要新任务时使用“下一任务”；模板可收藏。</li></ol>}
 </section>;
}
export function WorkflowIssues({result,stale,onLocate,onAssistant,onClose}) {
 const [expanded,setExpanded]=useState(false);
 if(!result)return null;
 const errors=result.error_count ?? result.issues.filter(i=>i.severity==='error').length;
 const warnings=result.warning_count ?? (result.issues.length-errors);
 if(stale)return <section className="workflow-issues stale" aria-label="工作流检查结果" role="region"><header><strong><ListChecks size={15}/>上次检查（已过期）</strong><button onClick={onClose} aria-label="关闭检查结果"><X size={15}/></button></header><p role="status">草稿或任务已变化，请重新检查。</p><button disabled onClick={onAssistant}><Sparkles size={14}/>发给 DevFlower 助手</button></section>;
 return <section className="workflow-issues" aria-label="工作流检查结果" role="region"><header><strong><ListChecks size={15}/>检查结果 · {errors} 个错误 · {warnings} 条提醒</strong><button onClick={onClose} aria-label="关闭检查结果"><X size={15}/></button></header>
 {result.truncated&&<p role="status">共发现 {result.total} 条，显示 {result.issues.length} 条，遗漏 {result.omitted_count} 条。请分批修复已列问题后重新检查。</p>}
 {stale&&<p role="status">草稿或任务已变化，请重新检查。</p>}
 <ul>{(expanded?result.issues:result.issues.slice(0,1)).map((issue,index)=><li key={index} className={issue.severity}><span>{issue.severity==='error'?'错误':'提醒'}</span><button onClick={()=>onLocate(issue)}>{issue.message}</button></li>)}</ul>
 {result.issues.length>1&&<button aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?'收起问题':result.truncated?'查看已列问题':'查看全部问题'}</button>}
 <button disabled={Boolean(stale)} onClick={onAssistant}><Sparkles size={14}/>发给 DevFlower 助手</button></section>;
}
