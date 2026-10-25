// Real HTTP + ordinary Chromium. Approval records are SYNTHETIC test fixtures.
const assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.argv[2],access=JSON.parse(fs.readFileSync(out+'/access.json'));
const results=[];let browser;
async function test(name,fn){
 const context=await browser.newContext(),page=await context.newPage();page.setDefaultTimeout(6000);
 const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 try{await fn(page,context);assert.deepEqual(errors,[]);results.push({name,passed:true})}
 catch(e){results.push({name,passed:false,error:String(e)});await page.screenshot({path:out+'/'+name+'-failure.png'})}
 finally{await page.unrouteAll({behavior:'ignoreErrors'});await context.close()}
}
async function open(page,key){const x=access[key];await page.goto(x.url+'/#'+x.token);await page.locator('.brand').waitFor()}
function headers(key){return {Authorization:'Bearer '+access[key].token}}
(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 await test('stable-brand-folder-and-http-use-verified-release',async page=>{
  await open(page,'stable');assert.equal(await page.locator('.brand span').innerText(),'STABLE');
  assert.equal(await page.locator('.workspace-environment').innerText(),'Stable');
  const r=await page.request.get(access.stable.url+'/api/status',{headers:headers('stable')});assert.equal(r.status(),200);
  const status=await r.json();assert.equal(status.label,'Stable · Agent 工作台');assert.equal(status.release.candidate_sha256,access.candidate);
  await page.getByRole('button',{name:'打开文件夹',exact:true}).click();
  assert((await page.locator('.folder-dialog header small').innerText()).includes('Stable'));
  await page.reload();await page.locator('.brand').waitFor();assert.equal(await page.locator('.brand span').innerText(),'STABLE');
  await page.screenshot({path:out+'/synthetic-stable.png'});
 });
 await test('saved-graphs-workspaces-and-labels-cannot-promote-development',async(page,context)=>{
  await context.addInitScript(()=>localStorage.setItem('devflower-workbench-view',JSON.stringify({workspace:'fixture-workspace',
   stable_approved:true,release:{channel:'Stable'},label:'Stable',graph:{version:3,name:'Stable',stable_approved:true,
    nodes:[],edges:[],budget:{calls:1,seconds:60}}})));
  await open(page,'development');assert.equal(await page.locator('.brand span').innerText(),'DEVELOPMENT');
  assert.equal(await page.locator('.workspace-environment').innerText(),'Development');
  const r=await page.request.get(access.development.url+'/api/status',{headers:headers('development')});assert.equal((await r.json()).stable_approved,false);
  for(const path of ['/api/promote','/api/approve-release']){
   const response=await page.request.post(access.development.url+path,{headers:headers('development'),data:{stable_approved:true}});assert.equal(response.status(),400);
  }
 });
 await test('bare-status-flags-or-mismatched-candidate-do-not-brand-stable',async page=>{
  for(const release of [undefined,{channel:'Stable',candidate_sha256:'wrong-candidate'}]){
   await page.route('**/api/status',async route=>{const r=await route.fetch();const status=await r.json();
    await route.fulfill({response:r,json:{...status,stable_approved:true,label:'Stable',release}})});
   await open(page,'development');assert.equal(await page.locator('.brand span').innerText(),'DEVELOPMENT');
   await page.unroute('**/api/status');
  }
 });
 await test('approval-tamper-refuses-queries-starts-and-refresh',async page=>{
  await open(page,'stable');await page.request.post(access.tamper);
  for(const path of ['/api/status','/api/folders'])assert.equal((await page.request.get(access.stable.url+path,{headers:headers('stable')})).status(),400);
  for(const path of ['/api/start','/api/chat/start'])assert.equal((await page.request.post(access.stable.url+path,{headers:headers('stable'),data:{id:'fixture',sha256:'a'.repeat(64),answer:'SYNTHETIC'}})).status(),400);
  await page.reload();await page.locator('.splash').waitFor();assert.equal(await page.locator('.brand').count(),0);
  await page.screenshot({path:out+'/tampered-refresh.png'});
 });
 await browser.close();const summary={tests:results.length,failures:results.filter(r=>!r.passed).length,errors:0,skipped:0,results,
  evidence_type:'ordinary Chromium and HTTP; synthetic approvals, no model/release acceptance'};
 fs.writeFileSync(out+'/results.json',JSON.stringify(summary,null,2));console.log(JSON.stringify(summary));process.exitCode=summary.failures?1:0;
})().catch(e=>{console.error(e);process.exitCode=1;browser?.close()});
