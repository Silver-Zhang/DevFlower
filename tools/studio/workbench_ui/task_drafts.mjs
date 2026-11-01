// Browser-local data, independent of native sessions and run authorization.
// Each preserved task is a separate record; no tab writes another tab's map.
const DATABASE='devflower-task-drafts-v1', EVENT='devflower-task-drafts-changed';
let opening;
function transact(db,names,mode,work,{signal}={}){return new Promise((resolve,reject)=>{
 if(signal?.aborted){reject(new DOMException("操作已取消","AbortError"));return}
 let result;let transaction;
 try{transaction=db.transaction(names,mode,mode==='readwrite'?{durability:'strict'}:undefined);work(transaction,value=>result=value)}catch(error){try{transaction?.abort()}catch{}reject(error);return}
 const cancel=()=>{try{transaction.abort()}catch{}};signal?.addEventListener('abort',cancel,{once:true});const detach=()=>signal?.removeEventListener('abort',cancel);
 transaction.oncomplete=()=>{detach();resolve(result)};
 transaction.onabort=()=>{detach();reject(signal?.aborted?new DOMException('操作已取消','AbortError'):(transaction.error||Error('任务草稿事务未提交')))};
 transaction.onerror=()=>{}; // The abort, not request success, decides the result.
});}
function legacyObject(key){try{const value=JSON.parse(localStorage.getItem(key)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{}}catch{return {}}}
async function legacyId(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));return 'legacy-'+Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('')}
async function migrate(db){
 const records=[];
 for(const [workspace,entries] of Object.entries(legacyObject('devflower-retained-task-drafts'))){
  if(!Array.isArray(entries))continue;
  for(const [index,item] of entries.entries()){if(!item||typeof item.task!=='string'||!item.task.trim())continue;const value={workspace,task:item.task,workflow:typeof item.workflow==='string'?item.workflow:'旧版工作流',at:Number.isFinite(item.at)?item.at:0};records.push({...value,id:await legacyId({...value,legacy_id:typeof item.id==='string'?item.id:null,legacy_index:index}),origin:'legacy'})}
 }
 for(const [workspace,text] of Object.entries(legacyObject('devflower-workspace-task-drafts'))){
  if(typeof text!=='string'||!text.trim())continue;const value={workspace,task:text,workflow:'旧版工作区任务',at:0};records.push({...value,id:await legacyId(value),origin:'legacy'})
 }
 for(const [key,text] of Object.entries(legacyObject('devflower-message-drafts'))){const split=key.indexOf(':');if(split<1||typeof text!=='string'||!text.trim())continue;const scope=draftScope('message',key.slice(0,split),key.slice(split+1)),value={workspace:scope,task:JSON.stringify(text),workflow:'旧版消息草稿',at:0};records.push({...value,id:await legacyId(value),origin:'legacy'})}
 for(let index=0;index<localStorage.length;index++){const key=localStorage.key(index),match=/^devflower-agent:([^:]+):drafts$/.exec(key||'');if(!match)continue;const diagnostics=legacyObject('devflower-agent:'+match[1]+':diagnostic-drafts');for(const [topic,text] of Object.entries(legacyObject(key))){if(typeof text!=='string'||!text.trim())continue;const value={workspace:draftScope('assistant',match[1],topic),task:JSON.stringify({text,check:diagnostics[topic]??null}),workflow:'旧版助手草稿',at:0};records.push({...value,id:await legacyId(value),origin:'legacy'})}}
 // Legacy storage remains untouched; concurrent migrations have identical keys
 // and identical values. They cannot replace any new append or pending record.
 if(records.length)await transact(db,['retained'],'readwrite',tx=>{for(const row of records)tx.objectStore('retained').put(row)});
}
async function database(){
 if(!opening){opening=new Promise((resolve,reject)=>{
  if(!globalThis.indexedDB){reject(Error('当前浏览器不能保存任务草稿'));return}
  let cancelled=false;const request=indexedDB.open(DATABASE,1);
  request.onupgradeneeded=()=>{const db=request.result;for(const name of ['retained','pending']){const store=db.createObjectStore(name,{keyPath:['workspace','id']});store.createIndex('workspace','workspace')}};
  request.onblocked=()=>{cancelled=true;reject(Error('任务草稿数据库正被其他旧页面占用，请先保留当前文字'))};
  request.onerror=()=>reject(request.error||Error('无法打开任务草稿数据库'));
  request.onsuccess=()=>{const db=request.result;if(cancelled){db.close();return}db.onversionchange=()=>{db.close();opening=undefined};migrate(db).then(()=>resolve(db),error=>{db.close();reject(error)})};
 }).catch(error=>{opening=undefined;throw error})}
 return opening;
}
function notify(workspace){
 try{globalThis.dispatchEvent?.(new CustomEvent(EVENT,{detail:{workspace}}))}catch{}
 try{if(globalThis.BroadcastChannel){const channel=new BroadcastChannel(EVENT);channel.postMessage({workspace});channel.close()}}catch{}
}
function workspaceKey(workspace){if(typeof workspace!=='string'||!workspace.trim())throw Error('任务草稿需要明确工作区')}
export async function appendRetainedTask(workspace,task,workflow){
 workspaceKey(workspace);if(typeof task!=='string'||!task.trim())throw Error('没有可保留的任务文字');
 const db=await database();const row={workspace,id:crypto.randomUUID(),task,workflow:typeof workflow==='string'?workflow:'工作流',at:Date.now(),origin:'retained'};
 await transact(db,['retained'],'readwrite',tx=>tx.objectStore('retained').add(row));notify(workspace);return row;
}
export async function saveWorkspaceTask(workspace,client,task,version,options={}){
 workspaceKey(workspace);if(typeof client!=='string'||!client||typeof task!=='string'||!Number.isSafeInteger(version)||version<1)throw Error('任务草稿身份无效');
 const db=await database();
 await transact(db,['pending'],'readwrite',tx=>{
  const store=tx.objectStore('pending'),request=store.get([workspace,client]);
  request.onsuccess=()=>{if(!request.result||request.result.version<version)store.put({workspace,id:client,task,workflow:'自动保存的未发送任务',at:Date.now(),origin:'pending',version})};
 },options);notify(workspace);
}
async function rows(workspace){
 workspaceKey(workspace);const db=await database();
 return transact(db,['retained','pending'],'readonly',(tx,result)=>{
  const value={retained:[],pending:[]};for(const name of ['retained','pending']){const request=tx.objectStore(name).index('workspace').getAll(workspace);request.onsuccess=()=>{value[name]=request.result;result(value)}}
 });
}
export async function loadWorkspaceTask(workspace,client){
 const value=await rows(workspace);const own=value.pending.find(row=>row.id===client);
 if(own)return own.task;
 const latest=value.pending.sort((a,b)=>b.at-a.at)[0];
 if(latest)return latest.task;
 return value.retained.filter(row=>row.origin==='legacy').sort((a,b)=>b.at-a.at)[0]?.task;
}
export async function listTaskHistory(workspace,client){
 const value=await rows(workspace);
 return [...value.retained,...value.pending.filter(row=>row.id!==client&&row.task.trim())].sort((a,b)=>b.at-a.at||a.id.localeCompare(b.id));
}
export function subscribeTaskHistory(workspace,refresh){
 let timer;const changed=event=>{if(event?.detail?.workspace&&event.detail.workspace!==workspace)return;clearTimeout(timer);timer=setTimeout(refresh,30)};
 const channel=globalThis.BroadcastChannel?new BroadcastChannel(EVENT):null;
 if(channel)channel.onmessage=event=>changed({detail:event.data});
 globalThis.addEventListener(EVENT,changed);globalThis.addEventListener('focus',changed);
 return()=>{clearTimeout(timer);channel?.close();globalThis.removeEventListener(EVENT,changed);globalThis.removeEventListener('focus',changed)};
}

// Namespaced JSON records use the same committed, per-client transactions.
export function draftScope(kind,workspace,topic){workspaceKey(workspace);if(!['message','assistant'].includes(kind)||typeof topic!=='string'||!topic)throw Error('草稿身份无效');return JSON.stringify([kind,workspace,topic])}
export const saveScopedDraft=(kind,workspace,topic,client,value,version,options)=>saveWorkspaceTask(draftScope(kind,workspace,topic),client,JSON.stringify(value),version,options);
export async function loadScopedDraft(kind,workspace,topic,client){const value=await loadWorkspaceTask(draftScope(kind,workspace,topic),client);if(value===undefined)return undefined;try{return JSON.parse(value)}catch{throw Error('本机草稿记录不可读，原件未修改')}}
export async function listScopedDrafts(kind,workspace,topic,client){const values=await listTaskHistory(draftScope(kind,workspace,topic),client);return values.map(row=>{try{return {...row,value:JSON.parse(row.task)}}catch{return null}}).filter(Boolean)}
export async function clearScopedDraftIf(kind,workspace,topic,client,expectedValue,expectedVersion,clearedValue,nextVersion,options={}){
 const scope=draftScope(kind,workspace,topic),db=await database();const expected=JSON.stringify(expectedValue);
 const cleared=await transact(db,['pending'],'readwrite',(tx,result)=>{const store=tx.objectStore('pending'),request=store.get([scope,client]);request.onsuccess=()=>{const old=request.result;if(old&&(old.version>expectedVersion||old.version===expectedVersion&&old.task!==expected)){result(false);return}store.put({workspace:scope,id:client,task:JSON.stringify(clearedValue),workflow:'自动保存的草稿',at:Date.now(),origin:'pending',version:nextVersion});result(true)}},options);
 if(cleared)notify(scope);return cleared;
}
export async function listDraftTopics(kind,workspace){
 const db=await database(),prefix=JSON.stringify([kind,workspace]).slice(0,-1)+',';
 return transact(db,['retained','pending'],'readonly',(tx,result)=>{const values=[];for(const name of ['retained','pending']){const request=tx.objectStore(name).index('workspace').getAll(IDBKeyRange.bound(prefix,prefix+'\uffff'));request.onsuccess=()=>{for(const row of request.result){try{const [tag,w,topic]=JSON.parse(row.workspace);if(tag===kind&&w===workspace)values.push({...row,topic,value:JSON.parse(row.task)})}catch{}}result(values)}}});
}
