const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.argv[2],access=JSON.parse(fs.readFileSync(path.join(out,'access.json')));
const results=[];let browser;
async function test(name,fn){
 const context=await browser.newContext({viewport:{width:1600,height:1000}}),p=await context.newPage(),errors=[];
 p.setDefaultTimeout(6000);p.on('pageerror',e=>errors.push(String(e)));
 try{
  await p.goto(access.url+'#token='+access.token);await p.waitForFunction(()=>document.querySelector('#studio').dataset.ready==='true');
  await fn(p,context);assert.deepEqual(errors,[]);results.push({name,status:'PASS'});
 }catch(e){results.push({name,status:'FAIL',error:String(e)});}
 finally{await p.screenshot({path:path.join(out,`case-${results.length}.png`)}).catch(()=>{});await context.close();}
 console.log(JSON.stringify(results.at(-1)));
}
async function preset(p,kind){
 await p.locator('#template-library summary').click();await p.selectOption('#template',kind);
 await p.click('#new-draft');await p.waitForFunction(k=>document.querySelector('#workflow-name').value.startsWith(k+' 起始模板'),kind);
}
async function openWorkspace(p){
 await p.locator('.workspace-menu summary').click();await p.fill('#workspace-path',access.source);
 const opened=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/workspace'&&r.request().method()==='POST');
 const listed=p.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/status'&&u.searchParams.get('workspace')===access.workspace});
 await p.click('#open-workspace');assert.equal((await opened).status(),200);assert.equal((await listed).status(),200);
 await p.waitForFunction(source=>document.querySelector('#workspace-info').textContent.includes(source),access.source);
 await p.locator('.workspace-menu summary').click();
}
async function compile(p){
 await p.keyboard.press('Escape');await p.click('#flow-options');
 const response=p.waitForResponse(r=>r.url().endsWith('/api/compile'));await p.click('#compile');return await response;
}
async function remove(p,id){
 await p.locator(`#canvas [data-node="${id}"] .node-body`).click({button:'right'});
 await p.getByRole('menuitem',{name:'删除节点',exact:true}).click();
}
async function connect(p,source,target){
 await p.keyboard.press('Escape');await p.click('#flow-options');
 if(!(await p.locator('.connections-panel').evaluate(e=>e.open)))await p.locator('.connections-panel summary').click();
 await p.selectOption('#edge-source',source);await p.selectOption('#edge-target',target);await p.selectOption('#edge-on','next');await p.click('#connect');await p.keyboard.press('Escape');
}
(async()=>{
 browser=await chromium.launch({executablePath:process.env.CHROMIUM,headless:true,args:['--no-sandbox']});
 await test('immediate blank entry without ABC chooser or authority',async p=>{
  assert.equal(await p.locator('#canvas .node').count(),0);
  assert.equal(await p.locator('#template-library').evaluate(e=>e.open),false);
  await openWorkspace(p);
  await p.getByRole('button',{name:'创建任务入口',exact:true}).click();
  await p.waitForFunction(()=>document.querySelectorAll('#canvas .node').length===1);
  await p.keyboard.press('Escape');const request=p.waitForRequest(r=>r.url().endsWith('/api/draft'));await p.click('#save');
  const doc=(await request).postDataJSON().document;assert.equal(doc.version,2);assert.equal(doc.template,'blank');
  const r=await compile(p);assert.equal(r.status(),400); // partial draft is savable, not executable
 });
 await test('new blank does not reuse selected preset',async p=>{
  await preset(p,'C');assert.equal(await p.locator('#canvas .human_gate').count(),4);
  await p.click('#new-blank');await p.waitForFunction(()=>document.querySelectorAll('#canvas .node').length===0);
  assert.equal(await p.locator('#template-library').evaluate(e=>e.open),false);
 });
 await test('C nodes can be removed and reconnected without changing template',async p=>{
  await preset(p,'C');await p.click('#fit');
  for(const id of ['human-2','human-3','human-4'])await remove(p,id);
  await connect(p,'human-1','planner');await connect(p,'reviewer','delivery');
  const r=await compile(p);assert.equal(r.status(),200);const plan=await r.json();
  assert.equal(plan.document.template,'C');assert.equal(plan.document.version,2);
  assert.deepEqual(plan.initial_gates,['human-1']);assert.deepEqual(plan.result_gates,[]);
 });
 await test('A template can add a genuine result-confirmation node',async p=>{
  await preset(p,'A');await p.locator('#canvas [data-node="reviewer"]').hover();
  await p.locator('#canvas [data-node="reviewer"] .node-add').click();await p.locator('#palette [data-kind="human_gate"]').click();
  await p.waitForFunction(()=>document.querySelectorAll('#canvas .node.human_gate').length===2);
  const r=await compile(p);assert.equal(r.status(),200);const plan=await r.json();
  assert.equal(plan.document.template,'A');assert.equal(plan.result_gates.length,1);
 });
 await test('legacy edit migrates a copy and preserves immutable prior revision',async(p,context)=>{
  await openWorkspace(p);
  await p.waitForFunction(id=>Array.from(document.querySelector('#drafts').options).some(o=>o.value===id),access.legacy.id);
  await p.selectOption('#drafts',access.legacy.id);
  assert((await p.textContent('#save-state')).includes('旧草稿'));
  const request=p.waitForRequest(r=>r.url().endsWith('/api/draft'));await p.click('#save');
  const body=(await request).postDataJSON();assert.equal(body.document.version,2);assert.equal(body.id,access.legacy.id);assert.equal(body.expected,access.legacy.revision);
  await p.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
  const response=await context.request.get(access.url+'api/status?workspace='+encodeURIComponent(access.workspace),{headers:{Authorization:'Bearer '+access.token}});assert.equal(response.status(),200);
  const status=await response.json(),value=status.drafts.find(x=>x.id===access.legacy.id);assert(value,'The migrated draft must be listed in its fixture workspace');assert.notEqual(value.revision,access.legacy.revision);
 });
 await test('blank and preset workflows retain mandatory review/verification gates',async p=>{
  await preset(p,'A');await remove(p,'reviewer');const r=await compile(p);assert.equal(r.status(),400);
  assert(!(await p.textContent('#graph-status')).includes('顺序校验通过'));
 });
})().catch(e=>{results.push({name:'harness',status:'FAIL',error:String(e)});}).finally(async()=>{
 fs.writeFileSync(path.join(out,'template-results.json'),JSON.stringify({real_http:true,offline_bridge:false,models:false,
  tests:results.length,failures:results.filter(x=>x.status==='FAIL').length,skipped:0,results},null,2));
 if(browser)await browser.close();process.exitCode=results.some(x=>x.status==='FAIL')?1:0;
});
