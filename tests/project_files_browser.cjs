const fs=require('node:fs');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const out=process.argv[2],cfg=JSON.parse(fs.readFileSync(out+'/access.json'));
const results=[];
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:1480,height:980}}),page=await context.newPage();
 let starts=0;page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|chat\/start|terminal\/start|canvas-assistant\/messages)$/.test(new URL(r.url()).pathname))starts++});
 async function test(name,fn){try{await fn();results.push({name,passed:true})}catch(e){results.push({name,passed:false,error:String(e)});await page.screenshot({path:out+'/failure.png'});throw e}}
 async function open(path){await page.getByRole('navigation',{name:'工作区导航'}).getByRole('button',{name:'打开工作区',exact:true}).click();await page.getByLabel('目录路径',{exact:true}).fill(path);await page.getByRole('button',{name:'前往',exact:true}).click();await page.getByRole('button',{name:'选择此文件夹',exact:true}).click();await page.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor({state:'hidden'});}
 try{
  await page.goto(cfg.url+'/#'+cfg.token);await page.getByRole('navigation',{name:'工作区导航'}).getByRole('button',{name:'打开工作区',exact:true}).waitFor();
  await test('open-loads-project-design-without-host-cache',async()=>{await open(cfg.path);await page.getByRole('button',{name:'切换至工作流 已保存的项目工作流',exact:true}).click();await page.getByLabel('工作流名称',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('[aria-label="工作流名称"]').value==='已保存的项目工作流');assert((await page.locator('.notice').innerText()).includes('.devflower'));});
  await test('save-to-project-and-reload',async()=>{await page.getByLabel('工作流名称',{exact:true}).fill('浏览器保存的新版本');await page.getByRole('button',{name:'保存',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.notice')?.textContent.includes('当前改动已保存'));await page.reload();await page.getByLabel('工作流名称',{exact:true}).waitFor();assert.equal(await page.getByLabel('工作流名称',{exact:true}).inputValue(),'浏览器保存的新版本');});
  await test('saved-session-is-readable-without-native-home',async()=>{await page.getByRole('button',{name:'portable-browser',exact:false}).first().click();await page.getByText('项目保存的阅读副本 · 不恢复进程',{exact:true}).waitFor();await page.waitForFunction(()=>document.querySelector('.messages')?.textContent.includes('final answer'));assert((await page.locator('.messages').innerText()).includes('final answer'));assert(await page.getByRole('button',{name:'原生终端',exact:true}).isDisabled());});
  await test('project-run-group-chat-does-not-recover-a-process',async()=>{await page.getByRole('button',{name:'整轮对话',exact:true}).click();await page.getByRole('log',{name:'任务群聊记录'}).waitFor();await page.waitForFunction(()=>document.querySelector('.run-journal-messages')?.textContent.includes('final answer'));assert(await page.getByRole('button',{name:'用此流程开始下一任务',exact:true}).isDisabled());});
  await test('switching-project-isolates-design-and-conversations',async()=>{await open(cfg.other);assert.equal(await page.getByLabel('工作流名称',{exact:true}).inputValue(),'新工作流');assert(!(await page.locator('.session-list').innerText()).includes('portable-browser'));});
  await test('returning-project-restores-local-design',async()=>{await open(cfg.path);await page.getByRole('button',{name:'切换至工作流 浏览器保存的新版本',exact:true}).click();assert.equal(await page.getByLabel('工作流名称',{exact:true}).inputValue(),'浏览器保存的新版本');await page.screenshot({path:out+'/project-reopened.png'});});
  await test('project-favorite-template-is-still-available',async()=>{await page.getByRole('button',{name:'示例',exact:true}).click();await page.getByRole('button',{name:'项目收藏模板',exact:false}).first().waitFor();await page.getByRole('button',{name:'关闭示例库',exact:true}).click();});
  await test('project-assistant-history-is-readable-without-active-turn',async()=>{await page.getByRole('button',{name:'AI 配置',exact:true}).click();await page.getByRole('button',{name:'助手会话历史',exact:true}).click();await page.getByRole('button',{name:'已保存的助手问答',exact:false}).click();await page.getByText('保存在项目自己的隐藏目录内。',{exact:true}).waitFor();assert(await page.getByLabel('配置需求',{exact:true}).isDisabled());});
  await test('reading-and-reopening-launches-no-agent',async()=>assert.equal(starts,0));
 }finally{await browser.close();fs.writeFileSync(out+'/results.json',JSON.stringify({tests:results,failures:results.filter(r=>!r.passed).length,skipped:0,results},null,2));}
})().catch(e=>{console.error(e);process.exitCode=1});
