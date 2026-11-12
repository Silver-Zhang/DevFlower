const {chromium}=require(process.env.PLAYWRIGHT_MODULE),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),readline=require('node:readline');
const out=process.argv[2],rows=[],errors=[],requests=[],replies=[];let browser,context,page,workspace,run,owner,agentID,runName;
readline.createInterface({input:process.stdin}).on('line',line=>replies.shift()?.(JSON.parse(line)));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function control(command){const promise=new Promise(resolve=>replies.push(resolve));process.stdout.write(JSON.stringify(command)+'\n');return promise}
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await sleep(100)}throw Error('observable condition not met')}
function attach(p){p.setDefaultTimeout(10000);p.on('dialog',d=>d.accept());p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push({method:r.method(),path:new URL(r.url()).pathname})})}
async function api(route){return page.evaluate(async route=>{const r=await fetch('/api/v2/'+route,{headers:{Authorization:'Bearer '+sessionStorage.getItem('devflower-v2-capability')}});if(!r.ok)throw Error('HTTP '+r.status);return r.json()},route)}
async function snapshot(){return api('run?workspace='+workspace+'&id='+run)}
async function barrier(stage='input-read'){await until(()=>{const p=path.join(out,'barriers',run+'-'+agentID+'.json');return fs.existsSync(p)&&JSON.parse(fs.readFileSync(p)).stage===stage})}
async function release(){const dir=path.join(out,'releases');fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,run+'-'+agentID),'')}
async function node(name){await page.locator('.agent-card strong').getByText(name,{exact:true}).click()}
async function config(name){await page.getByLabel('Agent 名称',{exact:true}).fill(name);await page.getByLabel('Provider',{exact:true}).selectOption('codex');await page.getByLabel('请求模型',{exact:true}).selectOption('fixture');await page.getByLabel('请求强度',{exact:true}).selectOption('low');await page.getByLabel('每轮提示词',{exact:true}).fill('synthetic controlled process only')}
async function newBlank(){const dirty=await page.locator('.save-state').innerText()==='未保存';await page.getByLabel('新建工作流',{exact:true}).click();if(dirty){await page.getByRole('dialog',{name:'未保存的草稿',exact:true}).waitFor();await page.getByRole('button',{name:'放弃修改',exact:true}).click()}await until(async()=>await page.getByLabel('工作流名称',{exact:true}).inputValue()==='新工作流'&&await page.locator('.agent-card').count()===0)}
async function prepare(name){await page.getByLabel('工作流名称',{exact:true}).fill(name);await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await config('Controlled A');await page.getByLabel('关闭检查器',{exact:true}).click()}
async function launch(){await page.getByRole('button',{name:'保存并启动',exact:true}).click();await page.getByLabel('运行状态',{exact:true}).waitFor();await until(async()=>{const status=await api('status');workspace=status.workspaces.find(w=>w.name==='A')?.id;const runs=workspace?await api('runs?workspace='+workspace):[];if(!runs.length)return false;run=runs.sort((a,b)=>b.created_at-a.created_at)[0].id;return true});const snap=await snapshot();owner=snap.run.worker;agentID=snap.definition.draft_document.nodes[0].id;runName=snap.definition.draft_document.name;await barrier()}
async function restart(mode='hold',samePort=false){const response=await control({control:'restart-service',mode,samePort});await page.goto(response.url);await until(async()=>await page.getByLabel('运行状态',{exact:true}).count()>0||await page.locator('.workspace-link').count()>0);if(!await page.getByLabel('运行状态',{exact:true}).count()){await page.locator('.workspace-link').filter({hasText:'A'}).click();await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').filter({hasText:runName}).click()}await page.getByLabel('运行状态',{exact:true}).waitFor()}
async function state(value){await until(async()=>{const text=await page.getByLabel('运行状态',{exact:true}).innerText();return text.startsWith(value)})}
async function proof(name,fn){try{await fn();await page.screenshot({path:path.join(out,name+'.png')});rows.push({name,passed:true});console.log('PASS '+name)}catch(e){rows.push({name,passed:false,error:e.stack});await page.screenshot({path:path.join(out,name+'-FAIL.png')}).catch(()=>{});console.log('FAIL '+name+' '+e.message);throw e}finally{fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({category:'SYNTHETIC Provider; actual service/worker/child and Chromium',complete:false,rows,errors,requests},null,2))}}
(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});context=await browser.newContext({viewport:{width:1440,height:900}});page=await context.newPage();attach(page);await context.tracing.start({screenshots:true,snapshots:true});await page.goto(process.env.V2_URL);
 await proof('live-view-close-refresh-and-tab-reopen-never-cancel',async()=>{let releaseInitial,initialSeen=false,initialDone=false;const heldInitial=new Promise(resolve=>releaseInitial=resolve);const holdInitial=async route=>{const response=await route.fetch();initialSeen=true;await heldInitial;await route.fulfill({response});initialDone=true};await page.route('**/api/v2/folders',holdInitial);await page.locator('.app-header button').click();await until(()=>initialSeen);assert(await page.getByLabel('文件夹路径',{exact:true}).isDisabled());assert.equal(await page.getByLabel('文件夹路径',{exact:true}).inputValue(),process.env.V2_WORKSPACES);releaseInitial();await until(()=>initialDone);await page.unroute('**/api/v2/folders',holdInitial);await page.locator('.folder-list button').filter({hasText:/^A$/}).click();await until(async()=>await page.getByLabel('文件夹路径',{exact:true}).inputValue()===path.join(process.env.V2_WORKSPACES,'A')&&await page.getByRole('button',{name:'打开此文件夹',exact:true}).isEnabled());await page.getByRole('button',{name:'打开此文件夹',exact:true}).click();await until(async()=>await page.locator('.app-header button').textContent()==='A');await prepare('M3 controlled chain');await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await config('Controlled B');await node('Controlled A');await page.getByLabel('下游 Agent',{exact:true}).selectOption({label:'Controlled B'});await page.getByRole('button',{name:'连接 Agent',exact:true}).click();await page.getByLabel('关闭检查器',{exact:true}).click();await launch();await node('Controlled A');await page.getByLabel('关闭检查器',{exact:true}).click();const before=requests.filter(r=>r.path.endsWith('/launch')).length;await page.reload();await state('运行中');await page.close();page=await context.newPage();attach(page);await page.goto(process.env.V2_URL);await state('运行中');const snap=await snapshot();assert.deepEqual(snap.run.worker,owner);assert(snap.worker_alive);assert.equal(snap.run.cancel_requested,false);assert.equal(snap.run.actual_calls,1);assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,before)});
 await proof('new-capability-declined-reload-keeps-dirty-draft-saveable',async()=>{await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Controlled A');await page.getByLabel('每轮提示词',{exact:true}).fill('M3 retained dirty edit after declined reload');const response=await control({control:'restart-service',mode:'hold',samePort:true});let dismissed=false;page.removeAllListeners('dialog');page.once('dialog',async d=>{await d.dismiss();dismissed=true});await page.goto(response.url);await until(()=>dismissed);page.on('dialog',d=>d.accept());assert.equal(await page.getByLabel('每轮提示词',{exact:true}).inputValue(),'M3 retained dirty edit after declined reload');assert.equal(await page.locator('.save-state').innerText(),'未保存');await page.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>await page.locator('.save-state').innerText()==='已保存');await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').filter({hasText:runName}).click();await state('运行中');assert.notEqual((await snapshot()).definition.draft_document.nodes[0].round_prompt,'M3 retained dirty edit after declined reload')});
 await proof('browser-network-disconnect-reconnect-keeps-run-and-send',async()=>{const offline=route=>route.abort('failed');await page.route('**/api/v2/run?*',offline);await page.getByRole('button',{name:'重新读取',exact:true}).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('运行状态未确认'));assert((await page.getByLabel('运行状态',{exact:true}).innerText()).startsWith('状态未确认'));assert((await page.locator('.agent-card').first().innerText()).includes('状态未确认'));assert((await page.getByLabel('运行历史',{exact:true}).locator('button.active').innerText()).includes('状态未确认'));await page.screenshot({path:path.join(out,'offline-state-unconfirmed.png')});await page.unroute('**/api/v2/run?*',offline);await page.getByRole('button',{name:'重新读取',exact:true}).click();await state('运行中');const snap=await snapshot();assert.equal(snap.run.actual_calls,1);assert.deepEqual(snap.run.worker,owner)});
 await proof('service-only-process-restart-keeps-live-worker',async()=>{const before=requests.filter(r=>r.path.endsWith('/launch')).length;await restart('hold',true);await state('运行中');const snap=await snapshot();assert(snap.worker_alive);assert.deepEqual(snap.run.worker,owner);assert.equal(snap.run.actual_calls,1);assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,before)});
 await proof('p1a-active-workspace-close-keeps-worker-and-launch-intent',async()=>{
  const history=()=>api('runs?workspace='+workspace);
  const before=(await history()).map(r=>r.id);
  const actions=()=>requests.filter(r=>r.path.endsWith('/launch')||r.path.endsWith('/cancel')).length;
  const actionCount=actions();
  const {environment}=await api('status');
  const key='devflower-v2-launch:'+environment.id+':'+workspace;
  const pending={workspace,intent_key:'synthetic-p1a-preserved-key'};
  await page.evaluate(([key,pending])=>localStorage.setItem(key,JSON.stringify(pending)),[key,pending]);

  await page.getByRole('button',{name:'文件',exact:true}).click();
  const panel=page.getByLabel('项目文件',{exact:true});
  await panel.getByRole('button',{name:'预览文件 readme.txt',exact:true}).click();
  await panel.getByText('read-only during a synthetic run',{exact:true}).waitFor();
  await panel.getByRole('button',{name:'关闭项目文件',exact:true}).click();
  await state('运行中');

  let release,seen=false,finished=false;
  const delayed=new Promise(resolve=>{release=resolve});
  const routePattern='**/api/v2/run?*';
  const hold=async route=>{
   if(seen)return route.continue();
   seen=true;
   const response=await route.fetch();
   const row=await response.json();
   await delayed;
   await route.fulfill({response,json:{...row,state:'FAILED',run:{...row.run,reason:'STALE-OLD-RUN'}}});
   finished=true;
  };
  await page.route(routePattern,hold);
  await until(()=>seen);
  await page.getByRole('button',{name:'关闭工作空间',exact:true}).click();
  await page.getByRole('heading',{name:'打开项目，开始工作',exact:true}).waitFor();
  assert.equal(await page.getByLabel('运行状态',{exact:true}).count(),0);
  assert.deepEqual((await history()).map(r=>r.id),before);
  assert.equal((await snapshot()).run.worker.pid,owner.pid);
  assert.deepEqual(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key),pending);
  assert.equal(await page.evaluate(id=>localStorage.getItem('devflower-v2-run-view:'+id),environment.id),null);

  await page.locator('.workspace-link').filter({hasText:'A'}).click();
  await page.getByRole('button',{name:'核验本次启动',exact:true}).waitFor();
  await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').filter({hasText:runName}).click();
  await state('运行中');
  release();await until(()=>finished);await sleep(150);
  await state('运行中');
  assert.equal(await page.getByText('STALE-OLD-RUN',{exact:true}).count(),0);
  assert.equal(actions(),actionCount);
  await page.unroute(routePattern,hold);
  await page.evaluate(key=>localStorage.removeItem(key),key);
 });
 await proof('cancel-response-loss-unknown-provider-and-no-downstream',async()=>{const lose=async route=>{const response=await route.fetch();assert.equal(response.status(),200);await route.abort('failed')};await page.route('**/api/v2/cancel',lose);await page.getByRole('button',{name:'取消本次运行',exact:true}).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('取消结果未确认'));await page.unroute('**/api/v2/cancel',lose);await page.getByRole('button',{name:'重新读取',exact:true}).click();await state('待核验');const snap=await snapshot();assert.equal(snap.provider_outcome,'unknown');assert.equal(snap.run.actual_calls,1);assert(snap.run.cancel_requested);assert(!snap.nodes.find(n=>n.name==='Controlled B').input_sent_sha256);assert(!snap.nodes.find(n=>n.name==='Controlled B').process,'Downstream native process never starts after cancelling A');assert(fs.existsSync('/proc/'+process.env.UNRELATED_PID+'/stat'),'Unrelated process survives');const before=requests.filter(r=>r.path.endsWith('/launch')).length;await restart();await state('待核验');assert.equal((await snapshot()).run.actual_calls,1);assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,before)});
 await proof('sent-worker-death-and-service-restart-stay-unknown',async()=>{await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await newBlank();await prepare('M3 worker death');await launch();await control({control:'kill-worker',run});await restart();await state('待核验');const snap=await snapshot();assert.equal(snap.provider_outcome,'unknown');assert.equal(snap.run.actual_calls,1);assert.equal(snap.nodes.filter(n=>n.receipt_verified).length,0)});
 await proof('completion-file-crash-window-reopens-verified-result',async()=>{await restart('completion-before-record');await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await newBlank();await prepare('M3 completion recovery');await launch();await release();await barrier('completion-before-record');await state('已完成');await node('Controlled A');assert.equal(await page.getByLabel('Agent 最终回答',{exact:true}).innerText(),'synthetic controlled completion');const before=requests.filter(r=>r.path.endsWith('/launch')).length;await restart();await state('已完成');assert.equal((await snapshot()).run.actual_calls,1);assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,before)});
 await proof('corrupt-completion-cannot-become-success',async()=>{await restart('corrupt-completion');await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await newBlank();await prepare('M3 corrupt completion');await launch();await release();await barrier('completion-before-record');await page.reload();await state('待核验');await restart();await state('待核验');assert.equal((await snapshot()).provider_outcome,'unknown');for(const viewport of [{width:1440,height:900},{width:1280,height:800},{width:989,height:535}]){await page.setViewportSize(viewport);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.getByRole('button',{name:'重新读取',exact:true}).click({trial:true});await page.screenshot({path:path.join(out,'recovery-layout-'+viewport.width+'.png')})}});
 assert.equal(errors.length,0);await context.tracing.stop({path:path.join(out,'trace.zip')});await browser.close();const result={category:'SYNTHETIC Provider; actual service/worker/child and Chromium',complete:true,tests:rows.length,failures:0,real_model_calls:0,rows,errors,requests};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({tests:rows.length,failures:0,real_model_calls:0}));process.exit(0);
})().catch(async e=>{fs.writeFileSync(path.join(out,'fatal.txt'),e.stack);await context?.tracing.stop({path:path.join(out,'trace-failure.zip')}).catch(()=>{});await browser?.close();console.error(e.message);process.exit(1)});
