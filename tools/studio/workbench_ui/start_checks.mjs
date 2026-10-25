// A real user-confirmed action owns these exact frozen checks. No background
// model calls, retries, guessed models, or bypass of the server's launch gate.
function retryRequired(check,status){
 const error=Error('此配置的连接检查已失败或撤销。请核对配置，明确重试一次只读连接检查（可能计费）；任务尚未发送。');
 if(status.status==='failed'&&typeof status.verification_id==='string'&&status.verification_id){
  error.retry={key:check.key,verificationId:status.verification_id};
 }
 return error;
}
export async function checkConnections(api,permit,answer,onProgress=()=>{},isCurrent=()=>true,retry=null){
 if(typeof answer!=='string'||!answer.trim())throw Error('请先确认本次操作范围');
 const checks=permit.data?.admission_checks||[],seen=new Set();
 if(retry&&(!checks.some(c=>c.key===retry.key)||!retry.verificationId))throw Error('重试配置与本次操作不一致');
 for(const check of checks){
  if(seen.has(check.key))continue;seen.add(check.key);
  if(!isCurrent())throw Error('操作已取消，未启动任务');
  onProgress(`正在连接 ${check.agent||'Agent'}…`);
  const status=await api('/api/model-admission/status',check.selection);
  if(!isCurrent())throw Error('操作已取消，未启动任务');
  const manual=retry?.key===check.key;
  if(manual){
   if(status.status!=='failed'||status.verification_id!==retry.verificationId)throw Error('连接状态已变化，请重新核对；未重试');
  }else{
   if(status.execution_allowed)continue;
   if(status.status==='failed')throw retryRequired(check,status);
   if(status.status==='unknown')throw Error('连接检查结果未确认，请查询现状；不会自动重试');
  }
  const prepared=await api('/api/model-admission/prepare',check.selection);
  if(!isCurrent())throw Error('操作已取消，未启动任务');
  // This is the same actual confirmation, never a fabricated user answer.
  // Only the separately labelled retry button supplies this exact failed key.
  let verified;
  try{verified=await api('/api/model-admission/verify',{id:prepared.id,sha256:prepared.sha256,answer,reuse_verified:!manual})}
  catch(error){
   // A lost response is unknown. A definite rejection may offer a NEW user
   // action after a read-only status query; it never performs that retry.
   if(isCurrent()&&[400,409].includes(error.responseStatus)){
    let current;try{current=await api('/api/model-admission/status',check.selection)}catch{}
    if(isCurrent()&&current?.status==='failed'){
     const next=retryRequired(check,current);next.message=error.message+' '+next.message;throw next;
    }
   }
   throw error;
  }
  if(!verified.execution_allowed)throw Error('连接检查未通过，未发送业务请求');
 }
 if(!isCurrent())throw Error('操作已取消，未启动任务');
 onProgress('连接已就绪，正在启动…');
}
