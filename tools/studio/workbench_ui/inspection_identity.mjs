// A diagnostic is usable only with the exact checked document and task.
export function inspectionCurrent(check,workspace,graph,task){
 return Boolean(check&&!check.stale&&check.workspace===workspace&&check.doc===JSON.stringify(graph)&&check.task===task);
}
export function inspectionSummary(result){
 const issues=result?.issues||[];
 const summary=issues.map(i=>(i.severity==='error'?'错误':'提醒')+'：'+i.message).join('\n');
 const counts=result?.total != null ? `共 ${result.total} 条：${result.error_count} 个错误，${result.warning_count} 条提醒；返回 ${issues.length} 条，遗漏 ${result.omitted_count} 条。` : '';
 const guidance=result?.truncated ? '检查结果已截断，只能处理已列问题；请分批修复并重新检查。' : '';
 return '请帮我分析工作流检查结果并提出修复建议：\n'+counts+'\n'+guidance+'\n'+summary.slice(0,5000)+(summary.length>5000?'\n消息摘要未列完，完整返回列表详见检查身份。':'');
}
