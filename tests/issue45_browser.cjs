const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.argv[2],access=JSON.parse(fs.readFileSync(path.join(out,'access.json')));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(String(e)));
 try{
  const openWorkspace=async()=>{await page.locator('.workspace-menu summary').click();await page.fill('#workspace-path',access.source);await page.click('#open-workspace');await page.waitForFunction(p=>document.querySelector('#workspace-info').textContent.includes(p),access.source);await page.locator('.workspace-menu summary').click()};
  await page.goto(access.url+'#token='+access.token);await page.waitForFunction(()=>document.querySelector('#studio').dataset.ready==='true');await openWorkspace();
  await page.locator('#template-library summary').click();await page.selectOption('#template','A');await page.click('[data-tab="workflow"]');const template=page.waitForResponse(r=>r.url().includes('/api/template?'));await page.click('#new-draft');await template;
  await page.locator('#canvas [data-node="human-1"] .node-body').dblclick();await page.fill('#task-title','SYNTHETIC TITLE');await page.fill('#task-body','SYNTHETIC GOAL');await page.click('#task-next');await page.fill('#task-scope','README.md');await page.fill('#task-acceptance','SYNTHETIC ACCEPTANCE');await page.keyboard.press('Escape');await page.click('#save');await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#studio').dataset.ready==='true');await openWorkspace();await page.waitForFunction(()=>document.querySelector('#drafts').options.length>1);await page.selectOption('#drafts',{index:1});await page.locator('#canvas [data-node="human-1"] .node-body').dblclick();
  assert.equal(await page.inputValue('#task-title'),'SYNTHETIC TITLE');assert.equal(await page.inputValue('#task-body'),'SYNTHETIC GOAL');await page.click('#task-next');assert.equal(await page.inputValue('#task-scope'),'README.md');assert.equal(await page.inputValue('#task-acceptance'),'SYNTHETIC ACCEPTANCE');assert.deepEqual(errors,[]);await page.screenshot({path:path.join(out,'task-card-restored.png')});console.log('PASS synthetic task card save and reopen');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
