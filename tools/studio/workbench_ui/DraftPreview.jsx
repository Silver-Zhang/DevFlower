import React from 'react';

// This is intentionally not a Runtime receipt. Unknown upstream values stay unknown.
export function DraftPreview({node,graph,task}){
 const incoming=(graph?.edges||[]).filter(e=>e.target===node.id),outgoing=(graph?.edges||[]).filter(e=>e.source===node.id);
 const inputs=incoming.map(e=>{const source=graph.nodes.find(n=>n.id===e.source);return {source:source?.name||e.source,field:e.transfer==='field'?e.field:e.transfer||'result',value:source?.kind==='start'?{answer:task,result:{task}}:'unknown：上游尚未执行'}});
 const prompt=[['工作方式',node.mode||'Ask'],['初始化提示词（新运行计划发送，不是已发送证明）',node.initial_prompt||'（未填写）'],...(node.task_context===true?[['本次任务目标（草稿预览，尚未发送）',task||'（尚未填写）']]:[]),['每轮提示词',node.round_prompt||'（未填写）'],['上游输入（草稿预览，不授予权限）',JSON.stringify(inputs,null,2)],['输出与交接要求',node.handoff_prompt||'（未填写）']].map(([title,text])=>'## '+title+'\n'+text).join('\n\n');
 const routes=edges=>edges.map(e=>{const other=graph.nodes.find(n=>n.id===(e.target===node.id?e.source:e.target));return <li key={e.id}><strong>{other?.name}</strong> · {e.transfer==='field'?e.field:e.transfer||'结构化结果'}{e.route?' · 分支 '+e.route:''}</li>});
 return <section className="draft-preview" aria-label="草稿预览"><p className="hint">设计预览 · 尚未执行，不包含实际运行、Provider 回执或接收证明。</p><details><summary>输入/输出预览（草稿）</summary><h4>本节点输入</h4>{incoming.length?<ul>{routes(incoming)}</ul>:<p>无上游连接</p>}<h4>本节点输出与交接</h4>{outgoing.length?<ul>{routes(outgoing)}</ul>:<p>无下游连接</p>}<p>输入合同：{(node.input_contract||[]).map(r=>r.field+' / '+r.type+(r.required?' / 必需':'')).join('；')||'无附加字段限制，仅允许公开交接数据'}</p><p>输出合同：{(node.output_contract||[]).map(r=>r.field+' / '+r.type+(r.required?' / 必需':'')).join('；')||'运行器基础输出合同；实际输出 unknown'}</p></details>{node.kind==='agent'&&<details><summary>本轮 Prompt 预览（草稿模板）</summary><pre>{prompt}</pre><p className="hint">仅预览当前编辑值。运行器权限、输出 Schema、源码身份和原生工具的附加字段，以运行后的 E 完整请求为准；不能用此预览证明模型已收到输入。</p></details>}</section>
}
