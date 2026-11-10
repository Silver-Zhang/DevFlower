const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const out=process.argv[2];
const endpoint='/api/v2/provider-catalog/copilot';
const pattern='**'+endpoint;
const rows=[],requests=[],deliveries=[],errors=[];
let browser,source,empty,page;
const states=complete=>fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({
 complete,cases:rows,requests,deliveries,errors,
},null,2));
const panel=page=>page.getByLabel('Provider 模型目录',{exact:true});
const opener=page=>page.getByRole('button',{name:'Provider 模型目录',exact:true});
const close=page=>page.getByRole('button',{name:'关闭 Provider 模型目录',exact:true});
const refresh=page=>page.getByRole('button',{name:'刷新 GitHub Copilot 目录',exact:true});
const row=(page,id)=>panel(page).getByText(id,{exact:true});
const isReply=(reply,method)=>new URL(reply.url()).pathname===endpoint&&reply.request().method()===method;
const settled=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

function attach(page,label,origin) {
 page.on('request',request=>{
  const url=new URL(request.url());
  if(url.origin!==new URL(origin).origin)requests.push({label,path:'external',method:request.method()});
  else if(url.pathname.startsWith('/api/'))requests.push({label,path:url.pathname,method:request.method()});
 });
 page.on('pageerror',error=>errors.push(error.message));
}
async function watch(page,name,test) {
 try {
  await test();
  await page.screenshot({path:path.join(out,name+'.png')});
  rows.push({name,status:'PASS'});
  console.log('PASS '+name);
 } catch(error) {
  rows.push({name,status:'FAIL',error:error.stack});
  await page.screenshot({path:path.join(out,name+'-FAIL.png')}).catch(()=>{});
  console.log('FAIL '+name+' '+error.message);
  throw error;
 } finally {states(false);}
}
async function show(page,{key=false}={}) {
 const reply=page.waitForResponse(response=>isReply(response,'GET'));
 if(key) {
  await opener(page).focus();
  await page.keyboard.press('Enter');
 } else await opener(page).click();
 assert.equal((await reply).status(),200);
 await panel(page).waitFor();
 await settled(page);
}
async function update(page) {
 const reply=page.waitForResponse(response=>isReply(response,'POST'));
 await refresh(page).click();
 const response=await reply;
 await settled(page);
 return response;
}
async function holdPost(page,label) {
 let release,arrive,rejectCapture,claimed=false;
 const gate=new Promise(resolve=>{release=resolve});
 const entered=new Promise((resolve,reject)=>{arrive=resolve;rejectCapture=reject});
 const handler=async route=>{
  if(claimed || route.request().method()!=='POST')return route.continue();
  claimed=true;
  try {
   const response=await route.fetch({timeout:8000});
   const origin=response.status();
   deliveries.push({label,origin});
   arrive(origin);
   await gate;
   await route.fulfill({response});
  } catch(error) {
   rejectCapture(error);
   await route.abort().catch(()=>{});
  }
 };
 await page.route(pattern,handler);
 let timer;
 try {
  await refresh(page).click();
  const origin=await Promise.race([entered,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('missing actual reply '+label)),8000)})]);
  return {
   origin,
   async deliver() {
    const observed=page.waitForResponse(reply=>isReply(reply,'POST'));
    release();
    assert.equal((await observed).status(),origin);
    await settled(page);
   },
   async stop() {release();await page.unroute(pattern,handler);},
  };
 } catch(error) {
  release();
  await page.unroute(pattern,handler);
  throw error;
 } finally {clearTimeout(timer);}
}
async function workspace(page,name) {
 await page.locator('.workspace-link').filter({hasText:new RegExp('^'+name+'$')}).click();
 await page.getByRole('banner').getByRole('button',{name,exact:true}).waitFor();
}
async function draft(page) {
 await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:'合成工作流',exact:true}).click();
 await page.locator('.agent-card strong').getByText('Agent Fixture A',{exact:true}).waitFor();
}
async function storage(page) {
 return page.evaluate(()=>[Object.entries(localStorage).sort(),Object.entries(sessionStorage).sort()]);
}

(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 empty=await browser.newContext({viewport:{width:600,height:760}});
 source=await browser.newContext({viewport:{width:900,height:820}});
 await empty.tracing.start({screenshots:true,snapshots:true});
 await source.tracing.start({screenshots:true,snapshots:true});
 const noConfig=await empty.newPage();
 attach(noConfig,'empty',process.env.V2_EMPTY_URL);
 await noConfig.goto(process.env.V2_EMPTY_URL);
 await noConfig.getByRole('banner').getByRole('button',{name:'打开工作空间',exact:true}).waitFor();
 await settled(noConfig);

 await watch(noConfig,'unconfigured-empty-workbench-keyboard-600',async()=>{
  const before=requests.length, saved=await storage(noConfig);
  assert.equal(requests.filter(item=>item.path===endpoint).length,0);
  await show(noConfig,{key:true});
  await panel(noConfig).getByText('尚未配置 GitHub Copilot 模型目录',{exact:true}).waitFor();
  assert.equal(await refresh(noConfig).isDisabled(),true);
  assert.equal(await close(noConfig).evaluate(el=>el===document.activeElement),true);
  assert.equal(await noConfig.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await noConfig.screenshot({path:path.join(out,'unconfigured-open-600.png')});
  await noConfig.keyboard.press('Escape');
  await panel(noConfig).waitFor({state:'hidden'});
  await settled(noConfig);
  assert.equal(await opener(noConfig).evaluate(el=>el===document.activeElement),true);
  assert.deepEqual(requests.slice(before),[{label:'empty',path:endpoint,method:'GET'}]);
  assert.deepEqual(await storage(noConfig),saved);
 });

 page=await source.newPage();
 attach(page,'source',process.env.V2_URL);
 await page.goto(process.env.V2_URL);
 await page.locator('.workspace-link').filter({hasText:'A'}).waitFor();

 await watch(page,'manual-refresh-projects-only-directory-and-literal-text',async()=>{
  const saved=await storage(page), before=requests.length;
  await show(page);
  await panel(page).getByText('尚未查询模型目录，请手动刷新',{exact:true}).waitFor();
  assert.deepEqual(requests.slice(before),[{label:'source',path:endpoint,method:'GET'}]);
  assert.equal((await update(page)).status(),200);
  await row(page,'fixture-1').waitFor();
  assert.equal(await panel(page).getByText('<b>literal model</b>',{exact:true}).count(),1);
  assert.equal(await panel(page).locator('b,a,img,iframe').count(),0);
  assert.match(await panel(page).innerText(),/仅目录查询[\s\S]*Copilot CLI · models.list[\s\S]*推理强度[\s\S]*未知/);
  assert.deepEqual(requests.slice(before),[{label:'source',path:endpoint,method:'GET'},{label:'source',path:endpoint,method:'POST'}]);
  assert.deepEqual(await storage(page),saved);
 });

 await watch(page,'close-and-reopen-do-not-publish-late-failure',async()=>{
  const old=await holdPost(page,'old timeout');
  try {
   assert.equal(old.origin,503);
   assert.equal(await refresh(page).isDisabled(),true);
   await close(page).click();
   await panel(page).waitFor({state:'hidden'});
   await show(page);
   await panel(page).getByText('Copilot 模型目录查询超时，可手动重试',{exact:true}).waitFor();
   assert.equal((await update(page)).status(),200);
   await row(page,'fixture-3').waitFor();
   await old.deliver();
   assert.equal(await row(page,'fixture-3').count(),1);
   assert.equal(await panel(page).getByText('Copilot 模型目录查询超时，可手动重试',{exact:true}).count(),0);
  } finally {await old.stop();}
 });

 await watch(page,'workspace-aba-and-obsolete-success-remain-isolated',async()=>{
  await close(page).click();
  await workspace(page,'A');
  await show(page);
  const old=await holdPost(page,'old workspace success');
  try {
   assert.equal(old.origin,200);
   await workspace(page,'B');
   await panel(page).waitFor({state:'hidden'});
   await workspace(page,'A');
   await show(page);
   assert.equal((await update(page)).status(),200);
   await row(page,'fixture-5').waitFor();
   await old.deliver();
   assert.equal(await row(page,'fixture-5').count(),1);
   assert.equal(await row(page,'fixture-4').count(),0);
  } finally {await old.stop();}
 });

 await watch(page,'agent-selection-creates-a-new-view-and-ticket',async()=>{
  await close(page).click();
  await draft(page);
  await page.locator('.agent-card strong').getByText('Agent Fixture A',{exact:true}).click();
  await show(page);
  const old=await holdPost(page,'old agent selection');
  try {
   assert.equal(old.origin,200);
   const reread=page.waitForResponse(reply=>isReply(reply,'GET'));
   await page.locator('.agent-card strong').getByText('Agent Fixture B',{exact:true}).click();
   assert.equal((await reread).status(),200);
   await settled(page);
   assert.equal((await update(page)).status(),200);
   await row(page,'fixture-7').waitFor();
   await old.deliver();
   assert.equal(await row(page,'fixture-7').count(),1);
   assert.equal(await row(page,'fixture-6').count(),0);
  } finally {await old.stop();}
 });

 await watch(page,'reopening-reads-cache-and-keeps-existing-codex-picker',async()=>{
  await close(page).click();
  await page.locator('.agent-card strong').getByText('Agent Fixture A',{exact:true}).click();
  const provider=page.getByLabel('Provider',{exact:true});
  assert.equal(await provider.inputValue(),'codex');
  assert.deepEqual(await provider.locator('option').allTextContents(),['待配置','Codex CLI']);
  assert.equal(await page.getByLabel('请求模型',{exact:true}).inputValue(),'old-codex-model');
  const before=requests.length;
  await page.getByRole('button',{name:'刷新模型目录',exact:true}).click();
  await page.getByText('已读取真实模型目录；目录和登录状态不代表推理已成功。',{exact:true}).waitFor();
  await show(page);
  await row(page,'fixture-7').waitFor();
  assert.deepEqual(requests.slice(before),[
   {label:'source',path:'/api/v2/providers/discover',method:'POST'},{label:'source',path:endpoint,method:'GET'},
  ]);
 });

 await watch(page,'configured-narrow-view-keyboard-and-no-durable-actions',async()=>{
  await page.setViewportSize({width:600,height:780});
  await close(page).click();
  await show(page,{key:true});
  await row(page,'fixture-7').waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await close(page).evaluate(el=>el===document.activeElement),true);
  await page.screenshot({path:path.join(out,'configured-open-600.png')});
  await page.keyboard.press('Escape');
  await panel(page).waitFor({state:'hidden'});
  await settled(page);
  assert.equal(await opener(page).evaluate(el=>el===document.activeElement),true);
  const forbidden=requests.filter(item=>item.path==='external'||(item.method==='POST'&&
   ![endpoint,'/api/v2/providers/discover','/api/v2/workspaces/open'].includes(item.path)));
  assert.deepEqual(forbidden,[]);
  assert.equal(requests.filter(item=>item.path===endpoint&&item.method==='POST').length,7);
  assert.deepEqual(errors,[]);
 });
 states(true);
})().catch(error=>{
 console.error(error.stack);
 process.exitCode=1;
}).finally(async()=>{
 if(source)await source.tracing.stop({path:path.join(out,'configured-trace.zip')}).catch(()=>{});
 if(empty)await empty.tracing.stop({path:path.join(out,'unconfigured-trace.zip')}).catch(()=>{});
 await browser?.close();
});
