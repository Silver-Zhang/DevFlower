// A diagnostic is usable only with the exact checked document and task.
export function inspectionCurrent(check,workspace,graph,task){
 return Boolean(check&&!check.stale&&check.workspace===workspace&&check.doc===JSON.stringify(graph)&&check.task===task);
}
export function inspectionSummary(result){
 const issues=result?.issues||[];
 const summary=issues.map(i=>(i.severity==='error'?'错误':'提醒')+'：'+i.message).join('\n');
 return '请帮我分析工作流检查结果并提出修复建议：\n'+summary.slice(0,5000)+(summary.length>5000?'\n其余问题详见检查身份。':'');
}
