const {openWorkflowActions}=require('./v2_workflow_actions.cjs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const out = process.argv[2];
const fixture = JSON.parse(process.env.V2_SUMMARY_FIXTURE);
const filename = 'devflower-run-summary.json';
const format = 'devflower.v2.run-summary';
const urlPattern = '**/api/v2/run-summary?*';
const endpoints = {summary:'/api/v2/run-summary', list:'/api/v2/runs', run:'/api/v2/run', status:'/api/v2/status'};
const archiveError = '运行摘要文件格式或校验无效。';
const changedError = '当前运行记录已变化，请重新读取后再导出。';
const cases = [], requests = [], deliveries = [], deliveryOrder = [], actorChanges = [], downloadFiles = [], errors = [];
let browser, source, offline, page, offlinePage, downloaded;
const database = process.env.V2_SUMMARY_DB;
const dbBytes = () => fs.readFileSync(database);
const storeResults = complete => fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({
 complete,cases,requests,deliveries,deliveryOrder,actorChanges,downloadFiles,errors,
},null,2));

const compact = value => Buffer.from(JSON.stringify(value));
const sha = value => createHash('sha256').update(value).digest('hex');
function archive(snapshot) {
 return Buffer.concat([compact({authority:false,format,snapshot,snapshot_sha256:sha(compact(snapshot)),version:1}),Buffer.from('\n')]);
}
function named(page, name) { return page.getByRole('button',{name,exact:true}); }
function details(page) { return page.getByLabel('运行状态',{exact:true}); }
function onEndpoint(response, endpoint) { return new URL(response.url()).pathname === endpoint; }
async function settled(page) {
 await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function watch(page, label, fn) {
 try {
  await fn();
  await page.screenshot({path:path.join(out,label+'.png')});
  cases.push({name:label,status:'PASS'});
  console.log('PASS '+label);
 } catch (error) {
  cases.push({name:label,status:'FAIL',error:error.stack});
  await page.screenshot({path:path.join(out,label+'-FAIL.png')}).catch(()=>{});
  console.log('FAIL '+label+' '+error.message);
  throw error;
 } finally {
  storeResults(false);
 }
}
function attach(page, actor) {
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const url = new URL(request.url());
  if(url.pathname.startsWith('/api/')) requests.push({actor,path:url.pathname,method:request.method()});
  else if(url.origin !== new URL(actor==='source'?process.env.V2_URL:process.env.V2_OFFLINE_URL).origin)
   requests.push({actor,path:'external',method:request.method()});
 });
 page.on('download',download=>downloadFiles.push({actor,name:download.suggestedFilename()}));
}
function stored(page) {
 return page.evaluate(()=>[Object.entries(localStorage).sort(),Object.entries(sessionStorage).sort()]);
}
function recentWorkspace(name) {
 return page.locator('.workspace-link').filter({hasText:new RegExp('^'+name+'$')});
}
async function visitWorkspace(name) {
 const response = page.waitForResponse(reply=>onEndpoint(reply,endpoints.list));
 await recentWorkspace(name).click();
 assert.equal((await response).status(),200);
 await settled(page);
 assert.equal(await page.locator('.app-header button').textContent(),name);
}
async function showRunExport() {
 const menu=page.locator('.run-export-menu');
 if(!await menu.evaluate(element=>element.open))await menu.locator('summary').click();
}
async function chooseRun(name) {
 const response = page.waitForResponse(reply=>onEndpoint(reply,endpoints.list));
 await page.getByLabel('运行历史',{exact:true}).getByRole('button',{name:new RegExp(name)}).click();
 await showRunExport();
 await named(page,'导出运行摘要').waitFor();
 assert.equal((await response).status(),200);
 await settled(page);
}
async function reread() {
 const response = page.waitForResponse(reply=>onEndpoint(reply,endpoints.list));
 await named(page,'重新读取').click();
 assert.equal((await response).status(),200);
 await settled(page);
}
function replaceState(state) {
 execFileSync('python3',['-B','-c',`
import json, pathlib, sqlite3, sys
database, run_id, state = sys.argv[1:]
with sqlite3.connect(pathlib.Path(database).as_uri()+'?mode=rw', uri=True) as db:
    row = json.loads(db.execute("SELECT data FROM records WHERE kind='run' AND id=?", (run_id,)).fetchone()[0])
    row['state'] = state
    raw = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    db.execute("UPDATE records SET data=? WHERE kind='run' AND id=?", (raw, run_id))
`,database,fixture.runs[0],state],{timeout:8000});
 actorChanges.push({actor:'fixture',state});
}
async function hold(label,{before=false,pattern=urlPattern,endpoint=endpoints.summary,
 start=()=>named(page,'导出运行摘要').click()}={}) {
 let release, arrive, rejectArrival, claimed=false;
 const gate = new Promise(resolve=>{release=resolve});
 const reached = new Promise((resolve,reject)=>{arrive=resolve;rejectArrival=reject});
 const handler = async route=>{
  if(claimed) return route.continue();
  claimed=true;
  try {
   if(before) { arrive();await gate; }
   const actual=await route.fetch({timeout:8000});
   deliveries.push({label,origin:actual.status()});
   if(!before) { arrive();await gate; }
   await route.fulfill({response:actual});
  } catch(error) {
   rejectArrival(error);
   await route.abort().catch(()=>{});
  }
 };
 await page.route(pattern,handler);
 let timer;
 try {
  await start();
  await Promise.race([reached,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Missing held request '+label)),8000)})]);
  return {
   async deliver() {
    const response=page.waitForResponse(reply=>onEndpoint(reply,endpoint));
    release();
    const reply=await response;
    assert.equal(await reply.finished(),null);
    deliveryOrder.push(label);
    return reply;
   },
   async close() {release();await page.unroute(pattern,handler);},
  };
 } catch(error) {
  release();
  await page.unroute(pattern,handler);
  throw error;
 } finally {clearTimeout(timer);}
}
async function noDownload(action) {
 const next = page.waitForEvent('download',{timeout:600}).then(
  () => {throw Error('Unexpected download');},
  error => {if(error.name!=='TimeoutError') throw error;});
 await action();
 await next;
}
async function pick(page, buffer, name=filename) {
 const selected = page.waitForEvent('filechooser');
 const navigation = named(page,'展开导航栏');
 if (page.viewportSize().width <= 760 && await navigation.isVisible()) {
  await navigation.focus();
  await page.keyboard.press('Enter');
 }
 const button = named(page,'查看运行摘要文件');
 await button.focus();
 await page.keyboard.press('Enter');
 const chooser = await selected;
 await chooser.setFiles({name,mimeType:'application/json',buffer});
}
async function closePreview(page) {
 const dialog=page.getByRole('dialog',{name:'运行摘要文件',exact:true});
 await page.keyboard.press('Escape');
 await dialog.waitFor({state:'hidden'});
 await settled(page);
 assert.equal(await named(page,'查看运行摘要文件').evaluate(element=>document.activeElement===element),true);
}
async function readOnlyArchive(page, sourceText) {
 const dialog=page.getByRole('dialog',{name:'运行摘要文件',exact:true});
 await dialog.waitFor();
 assert.match(await dialog.innerText(),/只读档案 · 来源状态由导出方记录/);
 assert.match(await dialog.innerText(),/当前状态未核验/);
 assert.equal(await dialog.getByText(sourceText,{exact:true}).count(),1);
 assert.equal(await dialog.locator('a,img,video,iframe,form,input').count(),0);
 assert.deepEqual(await dialog.getByRole('button').allTextContents(),['关闭']);
 assert.equal(await named(page,'关闭').evaluate(element=>document.activeElement===element),true);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 return dialog;
}

(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 source=await browser.newContext({viewport:{width:900,height:820},acceptDownloads:true});
 await source.tracing.start({screenshots:true,snapshots:true});
 page=await source.newPage();
 attach(page,'source');
 await page.goto(process.env.V2_URL);
 await page.locator('.workspace-link').filter({hasText:'A'}).waitFor();
 await visitWorkspace('A');
 await chooseRun('第一合成运行');

 await watch(page,'actual-download-is-one-read-and-canonical',async()=>{
  const prior=requests.length, before=dbBytes(), storage=await stored(page);
  const delivery=page.waitForResponse(reply=>onEndpoint(reply,endpoints.summary));
  const download=page.waitForEvent('download');
  await named(page,'导出运行摘要').focus();
  await page.keyboard.press('Enter');
  const response=await delivery;
  const file=await download;
  assert.equal(response.status(),200);
  assert.equal(file.suggestedFilename(),filename);
  await file.saveAs(path.join(out,filename));
  downloaded=fs.readFileSync(path.join(out,filename));
  const independent={
   agent_count:1,created_at_ms:1700000000125,edge_count:0,
   source_fingerprint:sha(Buffer.from([format,fixture.runs[0],fixture.definitions[0]].join('\0'),'ascii')),
   source_recorded_state:'FAILED',
  };
  assert.deepEqual(downloaded,await response.body());
  assert.deepEqual(downloaded,archive(independent));
  assert(!downloaded.toString().includes('PRIVATE_SUMMARY_FIXTURE'));
  assert(!downloaded.toString().includes(fixture.runs[0]));
  assert(!downloaded.toString().includes(fixture.definitions[0]));
  assert.deepEqual(requests.slice(prior),[{actor:'source',path:endpoints.summary,method:'GET'}]);
  assert.deepEqual(dbBytes(),before);
  assert.deepEqual(await stored(page),storage);
 });

 await watch(page,'changed-record-before-observation-or-newer-detail-refuses',async()=>{
  const earlier=await hold('before source observation',{before:true});
  try {
   replaceState('CANCELLED');
   await noDownload(async()=>{assert.equal((await earlier.deliver()).status(),200);});
   await page.getByText(changedError,{exact:true}).waitFor();
  } finally {replaceState('FAILED');await earlier.close();}
  await reread();
  const later=await hold('after source observation');
  try {
   replaceState('CANCELLED');
   await reread();
   assert.match(await details(page).innerText(),/本机调度已取消/);
   await noDownload(async()=>{assert.equal((await later.deliver()).status(),200);});
   await page.getByText(changedError,{exact:true}).waitFor();
  } finally {replaceState('FAILED');await later.close();}
  await reread();
 });

 await watch(page,'late-selected-run-reply-is-ignored',async()=>{
  const action=await hold('same-workspace reselection');
  try {
   await chooseRun('第二合成运行');
   await noDownload(()=>action.deliver());
   assert.equal(await page.getByText(changedError,{exact:true}).count(),0);
  } finally {await action.close();}
  await chooseRun('第一合成运行');
 });

 await watch(page,'late-workspace-aba-and-close-reopen-are-ignored',async()=>{
  for(const [label,endpoint,pattern,start] of [
   ['workspace','/api/v2/workspaces/open','**/api/v2/workspaces/open',()=>recentWorkspace('B').click()],
   ['draft','/api/v2/draft','**/api/v2/draft?*',()=>page.getByLabel('工作流列表',{exact:true})
     .getByRole('button',{name:'第一合成运行',exact:true}).click()],
  ]){
   const archiveReply=await hold('archive while '+label+' navigation pending');
   try {
    const navigation=await hold('pending '+label+' navigation',{endpoint,pattern,start});
    try {
     assert.equal(await page.locator('.app-header button').textContent(),'A');
     assert.equal(await recentWorkspace('B').isDisabled(),true);
     await noDownload(async()=>assert.equal((await archiveReply.deliver()).status(),200));
     assert.equal(await page.getByText(changedError,{exact:true}).count(),0);
     assert.equal(await named(page,'导出运行摘要').isDisabled(),true);
     const nextList=label==='workspace'?page.waitForResponse(reply=>onEndpoint(reply,endpoints.list)):null;
     assert.equal((await navigation.deliver()).status(),200);
     if(nextList) {
      assert.equal((await nextList).status(),200);
      await visitWorkspace('A');
     } else {
      await named(page,'导出运行摘要').waitFor({state:'hidden'});
     }
     await chooseRun('第一合成运行');
    } finally {await navigation.close();}
   } finally {await archiveReply.close();}
  }
  const action=await hold('workspace A B A');
  try {
   await visitWorkspace('B');
   await visitWorkspace('A');
   await chooseRun('第一合成运行');
   await noDownload(()=>action.deliver());
   assert.equal(await page.getByText(changedError,{exact:true}).count(),0);
  } finally {await action.close();}
  const reopened=await hold('close and reopen');
  try {
   await named(page,'关闭工作空间').click();
   await page.getByRole('banner').getByRole('button',{name:'打开工作空间',exact:true}).waitFor();
   await visitWorkspace('A');
   await chooseRun('第一合成运行');
   await noDownload(()=>reopened.deliver());
   assert.equal(await page.getByText(changedError,{exact:true}).count(),0);
  } finally {await reopened.close();}
 });

 await watch(page,'only-newest-export-action-downloads',async()=>{
  const action=await hold('first of two export clicks');
  try {
   const latest=page.waitForEvent('download');
   await named(page,'导出运行摘要').click();
   const newDownload=await latest;
   assert.deepEqual(fs.readFileSync(await newDownload.path()),downloaded);
   await noDownload(()=>action.deliver());
   assert.equal(await page.getByText(changedError,{exact:true}).count(),0);
  } finally {await action.close();}
 });

 await named(page,'编辑草稿').click();
 const beforeWrongImport=requests.length;
 const wrongImport=page.waitForEvent('filechooser');
 await openWorkflowActions(page);
 await named(page,'导入工作流').click();
 await (await wrongImport).setFiles({name:filename,mimeType:'application/json',buffer:downloaded});
 await page.getByText('工作流文件格式或编码无效。',{exact:true}).waitFor();
 assert.deepEqual(requests.slice(beforeWrongImport),[]);

 offline=await browser.newContext({viewport:{width:600,height:760}});
 await offline.tracing.start({screenshots:true,snapshots:true});
 offlinePage=await offline.newPage();
 attach(offlinePage,'offline');
 const ready=offlinePage.waitForResponse(reply=>onEndpoint(reply,endpoints.status));
 await offlinePage.goto(process.env.V2_OFFLINE_URL);
 assert.equal((await ready).status(),200);
 await offlinePage.getByRole('banner').getByRole('button',{name:'打开工作空间',exact:true}).waitFor();
 await settled(offlinePage);
 await offline.setOffline(true);
 const offlineFrom=requests.length, initialStorage=await stored(offlinePage);

 await watch(offlinePage,'offline-clean-state-keyboard-600-and-close',async()=>{
  assert.equal(await offlinePage.getByRole('button',{name:'导出运行摘要',exact:true}).count(),0);
  await pick(offlinePage,downloaded);
  const dialog=await readOnlyArchive(offlinePage,'来源记录失败');
  assert.match(await dialog.innerText(),/2023-11-14T22:13:20\.125Z/);
  assert.equal(await offlinePage.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').count(),0);
  await offlinePage.screenshot({path:path.join(out,'offline-visible-600.png')});
  await closePreview(offlinePage);
  assert.deepEqual(await stored(offlinePage),initialStorage);
 });

 await watch(offlinePage,'strict-invalid-archives-and-draft-format-rejected-offline',async()=>{
  const value=JSON.parse(downloaded), text=downloaded.toString();
  const draft=Buffer.from(JSON.stringify({format:'devflower.v2.workflow-draft',version:1,document:{},document_sha256:sha(Buffer.from('{}'))})+'\n');
  const bad=[
   Buffer.from(text.replace('"authority":false','"authority":false,"authority":false')),
   Buffer.from(text.replace('"authority":false','"authority":false,"extra":"<img src=x>"')),
   Buffer.from(text.replace('"authority":false','"authority":true')),
   Buffer.from(text.replace('"version":1','"version":2')),
   Buffer.from(text.replace('"agent_count":1','"agent_count":1.0')),
   Buffer.from(text.replace(value.snapshot_sha256,'0'.repeat(64))),
   Buffer.from(text.replace('FAILED','UNKNOWN')),
   Buffer.from(text.replace('FAILED','\\ud800')),
   Buffer.from(text.replace('FAILED','\\u0000')),
   Buffer.from(text.replace('FAILED','\\u0046AILED')),
   Buffer.from(text.replace('"edge_count":0','"edge_count":NaN')),
   Buffer.concat([Buffer.from([0xef,0xbb,0xbf]),downloaded]),
   Buffer.concat([Buffer.from([0xff]),downloaded]),
   Buffer.concat([downloaded,Buffer.from('\n')]),
   Buffer.alloc(8193,0x20), Buffer.alloc(8192,0x20), Buffer.alloc(0), draft,
  ];
  for(const raw of bad){
   await pick(offlinePage,raw);
   const dialog=offlinePage.getByRole('dialog',{name:'运行摘要文件',exact:true});
   await dialog.getByRole('alert').waitFor();
   assert.equal(await dialog.getByRole('alert').textContent(),archiveError);
   assert.equal(await dialog.locator('img').count(),0);
   await closePreview(offlinePage);
  }
 });

 await watch(offlinePage,'offline-all-state-labels-time-boundaries-and-chooser-cancel-900',async()=>{
  await offlinePage.setViewportSize({width:900,height:820});
  const states=[
   ['COMPLETED','来源记录已完成',253402300799999,'9999-12-31T23:59:59.999Z'],
   ['FAILED','来源记录失败',0,'1970-01-01T00:00:00.000Z'],
   ['CANCELLED','来源记录的本机调度已取消',null,'未知'],
   ['RECOVERY_REQUIRED','结果未知',null,'未知'],
  ];
  for(const [state,label,at,clock] of states){
   const raw=archive({agent_count:64,created_at_ms:at,edge_count:128,source_fingerprint:'0'.repeat(64),source_recorded_state:state});
   await pick(offlinePage,raw);
   const dialog=await readOnlyArchive(offlinePage,label);
   assert.equal(await dialog.getByText(clock,{exact:true}).count(),1);
   if(state==='RECOVERY_REQUIRED')await offlinePage.screenshot({path:path.join(out,'offline-visible-900.png')});
   await closePreview(offlinePage);
  }
  const ready=offlinePage.waitForEvent('filechooser');
  await named(offlinePage,'查看运行摘要文件').click();
  await (await ready).setFiles([]);
  assert.equal(await offlinePage.getByRole('dialog',{name:'运行摘要文件',exact:true}).count(),0);
  assert.deepEqual(await stored(offlinePage),initialStorage);
  assert.deepEqual(requests.slice(offlineFrom),[]);
  await offline.setOffline(false);
  await offlinePage.reload();
  assert.equal(await offlinePage.getByRole('dialog',{name:'运行摘要文件',exact:true}).count(),0);
  assert.equal(await offlinePage.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').count(),0);
 });

 const unexpected=requests.filter(item=>item.path==='external'||(item.method!=='GET'&&item.path!=='/api/v2/workspaces/open'));
 assert.deepEqual(unexpected,[]);
 assert.equal(requests.filter(item=>item.path===endpoints.summary).length,10);
 assert.equal(downloadFiles.length,2);
 assert.deepEqual(errors,[]);
 storeResults(true);
})().catch(error=>{
 console.error(error.stack);
 process.exitCode=1;
}).finally(async()=>{
 if(source)await source.tracing.stop({path:path.join(out,'source-trace.zip')}).catch(()=>{});
 if(offline)await offline.tracing.stop({path:path.join(out,'offline-trace.zip')}).catch(()=>{});
 await browser?.close();
});
