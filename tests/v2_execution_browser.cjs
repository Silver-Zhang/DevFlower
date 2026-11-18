const {openWorkflowActions}=require('./v2_workflow_actions.cjs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const out=process.argv[2],rows=[],errors=[],requests=[];let browser,context,page;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<120;i++){if(await fn())return;await sleep(100)}throw Error('condition not met')}
async function test(name,fn){try{await fn();await page.screenshot({path:path.join(out,name+'.png')});rows.push({name,passed:true});console.log('PASS '+name)}catch(e){rows.push({name,passed:false,error:e.stack});await page.screenshot({path:path.join(out,name+'-FAIL.png')}).catch(()=>{});console.log('FAIL '+name+' '+e.message)}finally{fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({category:'SYNTHETIC execution; real HTTP/SQLite/Chromium',complete:false,rows,errors,requests},null,2))}}
async function node(name){await page.locator('.agent-card strong').getByText(name,{exact:true}).click()}
async function save(){await page.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>await page.locator('.save-state').textContent()==='已保存')}
async function launchButton(){return page.getByRole('button',{name:/^(启动|保存并启动|核验本次启动)$/})}
async function complete(){await until(async()=>await page.getByLabel('运行状态',{exact:true}).innerText().then(t=>t.includes('已完成')))}
async function api(route,data){return page.evaluate(async({route,data})=>{const result=await fetch('/api/v2/'+route,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+sessionStorage.getItem('devflower-v2-capability'),...(data?{'Content-Type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});return result.json()},{route,data})}
(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});context=await browser.newContext({viewport:{width:1440,height:900}});page=await context.newPage();page.setDefaultTimeout(10000);page.on('dialog',d=>d.accept());page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push({path:new URL(r.url()).pathname,method:r.method()})});await context.tracing.start({screenshots:true,snapshots:true});await page.goto(process.env.V2_URL);
 await test('metadata-select-and-single-frozen-run',async()=>{
  await page.locator('.app-header button').click();await page.getByLabel('文件夹路径',{exact:true}).fill(path.join(process.env.V2_WORKSPACES,'A'));await page.getByRole('button',{name:'前往',exact:true}).click();await until(async()=>await page.getByRole('button',{name:'打开此文件夹',exact:true}).isEnabled());await page.getByRole('button',{name:'打开此文件夹',exact:true}).click();await until(async()=>await page.locator('.app-header button').textContent()==='A');
  await openWorkflowActions(page);await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await page.getByLabel('Provider',{exact:true}).selectOption('codex');await page.getByRole('button',{name:'刷新模型目录',exact:true}).click();await page.getByLabel('请求模型',{exact:true}).selectOption('fixture-model');await page.getByLabel('请求强度',{exact:true}).selectOption('low');await page.getByLabel('每轮提示词',{exact:true}).fill('synthetic request');await save();await page.getByLabel('关闭检查器',{exact:true}).click();await (await launchButton()).click();await complete();assert.equal(await page.getByLabel('工作流名称',{exact:true}).isDisabled(),true);await node('Agent A');assert.equal(await page.getByLabel('Agent 最终回答',{exact:true}).innerText(),'synthetic visible answer');assert((await page.getByLabel('实际可见请求',{exact:true}).innerText()).includes('synthetic request'));assert.equal(await page.getByLabel('每轮提示词',{exact:true}).count(),0);const writes=requests.filter(r=>r.path.endsWith('/drafts/save')).length;await page.keyboard.press('Control+s');await page.keyboard.press('Control+z');await sleep(200);assert.equal(requests.filter(r=>r.path.endsWith('/drafts/save')).length,writes,'Run shortcuts never save or undo hidden draft');
 });
 await test('run-and-draft-separate-reopen-does-not-send',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');await page.getByLabel('每轮提示词',{exact:true}).fill('new draft only');const launches=requests.filter(r=>r.path.endsWith('/launch')).length;await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();await complete();await node('Agent A');assert(!(await page.getByLabel('实际可见请求',{exact:true}).innerText()).includes('new draft only'));await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');assert.equal(await page.getByLabel('每轮提示词',{exact:true}).inputValue(),'new draft only');assert.equal(await page.locator('.save-state').innerText(),'未保存');await save();await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();await complete();await page.reload();await complete();assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,launches);await page.getByRole('button',{name:'编辑草稿',exact:true}).click();
 });
 await test('lost-launch-response-refresh-and-switch-reuses-intent',async()=>{
  let sent,committed;const lose=async route=>{sent=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);committed=await response.json();await route.abort('failed')};await page.route('**/api/v2/launch',lose);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('启动结果未确认'));assert.equal(await page.getByRole('button',{name:'核验本次启动',exact:true}).count(),1);await page.screenshot({path:path.join(out,'launch-committed-response-lost.png')});await page.unroute('**/api/v2/launch',lose);
  await page.getByLabel('新建工作流',{exact:true}).click();await page.getByLabel('工作流名称',{exact:true}).fill('other draft after unknown');await save();await page.reload();await until(async()=>await page.getByLabel('工作流名称',{exact:true}).inputValue()==='other draft after unknown');
  let retried;const retry=async route=>{retried=route.request().postDataJSON();const response=await route.fetch();await route.fulfill({response})};await page.route('**/api/v2/launch',retry);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();assert.deepEqual(retried,sent);assert.equal((await api('runs?workspace='+sent.workspace)).length,2);await page.getByLabel('运行状态',{exact:true}).getByText('运行身份与冻结版本',{exact:true}).click();assert((await page.getByLabel('运行状态',{exact:true}).innerText()).includes(committed.run.id));await page.unroute('**/api/v2/launch',retry);await page.getByRole('button',{name:'工作流',exact:true}).click();assert.equal(await page.getByLabel('工作流名称',{exact:true}).inputValue(),'other draft after unknown');
 });
 await test('launch-before-send-uncertain-never-auto-retries',async()=>{
  await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').last().click();await complete();await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await page.locator('nav[aria-label="工作流列表"] .workflow-link').filter({hasText:'新工作流'}).click();await until(async()=>await page.locator('.agent-card').count()===1);let calls=0;const block=route=>{calls++;return route.abort('failed')};await page.route('**/api/v2/launch',block);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('启动结果未确认'));await sleep(1200);assert.equal(calls,1);await page.unroute('**/api/v2/launch',block);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();
 });
 await test('http-refusal-after-reservation-keeps-original-intent',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();let sent;const refuse=async route=>{sent=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'synthetic refusal after durable reservation'})})};await page.route('**/api/v2/launch',refuse);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('HTTP 409'));assert.equal(await page.getByRole('button',{name:'核验本次启动',exact:true}).count(),1);await page.unroute('**/api/v2/launch',refuse);let retry;const observe=r=>{if(r.url().endsWith('/api/v2/launch'))retry=r.postDataJSON()};page.on('request',observe);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();assert.deepEqual(retry,sent);page.off('request',observe);
 });
 await test('one-inflight-run-read-and-terminal-never-regresses',async()=>{
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const latest=(await api('runs?workspace='+workspace)).sort((a,b)=>b.created_at-a.created_at)[0].id;
  // The receipt projection can become COMPLETED before the worker commits the run state.
  await until(async()=>{
   const snapshot=await api('run?workspace='+workspace+'&id='+latest);
   return snapshot.state==='COMPLETED'&&snapshot.run.persisted_state==='COMPLETED';
  });
  let release,entered=0;const held=new Promise(r=>release=r);
  const delay=async route=>{
   entered++;
   const response=await route.fetch();
   const snapshot=await response.json();
   assert.equal(snapshot.run.id,latest);
   assert.equal(snapshot.run.persisted_state,'COMPLETED');
   await held;
   await route.fulfill({response});
  };
  await page.route('**/api/v2/run?*',delay);
  await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();
  await until(()=>entered===1);await sleep(1200);
  await page.getByRole('button',{name:'重新读取',exact:true}).click();
  assert.equal(entered,1,'Polling/manual read share one in-flight request');
  release();await complete();await sleep(1100);
  assert.equal(entered,1,'Durably completed run stops automatic polling');
  await page.unroute('**/api/v2/run?*',delay);
 });
 await test('durable-manual-read-stops-an-already-scheduled-poll',async()=>{
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const latest=(await api('runs?workspace='+workspace)).sort((a,b)=>b.created_at-a.created_at)[0].id;
  const history=page.getByLabel('运行历史',{exact:true});
  await history.locator('button.workflow-link').last().click();
  await complete();
  await page.evaluate(()=>{
   const original=window.setTimeout.bind(window);
   const clock={scheduled:null,restore:()=>{window.setTimeout=original;clearTimeout(clock.scheduled?.id)}};
   window.fixtureRunPoll=clock;
   window.setTimeout=(callback,delay,...args)=>{
    if(delay!==900||clock.scheduled)return original(callback,delay,...args);
    const id=original(()=>{},30_000);
    clock.scheduled={id,run:()=>callback(...args)};
    return id;
   };
  });
  let reads=0;
  const pattern='**/api/v2/run?*';
  const project=async route=>{
   if(new URL(route.request().url()).searchParams.get('id')!==latest)return route.continue();
   const response=await route.fetch();
   const actual=await response.json();
   assert.equal(actual.run.persisted_state,'COMPLETED');
   if(++reads===1){
    await route.fulfill({response,json:{...actual,state:'RUNNING',run:{...actual.run,persisted_state:'RUNNING'}}});
   }else await route.fulfill({response});
  };
  await page.route(pattern,project);
  try{
   await history.locator('button.workflow-link').first().click();
   await page.getByLabel('运行状态',{exact:true}).getByText('运行中',{exact:true}).waitFor();
   await page.waitForFunction(()=>Boolean(window.fixtureRunPoll?.scheduled));
   const reread=page.getByRole('button',{name:'重新读取',exact:true});
   await reread.click();
   await complete();
   assert.equal(reads,2);
   await page.evaluate(()=>{const clock=window.fixtureRunPoll;clock.restore();clock.scheduled.run()});
   await sleep(100);
   assert.equal(reads,2,'An already scheduled automatic callback skips a durably completed identity');
   const explicit=page.waitForResponse(reply=>new URL(reply.url()).searchParams.get('id')===latest);
   await reread.click();
   assert.equal((await explicit).status(),200);
   assert.equal(reads,3,'The operator can still reread a durably completed run');
  }finally{
   await page.unroute(pattern,project);
   await page.evaluate(()=>{window.fixtureRunPoll?.restore();delete window.fixtureRunPoll});
  }
 });
 await test('actual-truncated-handoff-and-downstream-request',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');await page.getByLabel('Agent 名称',{exact:true}).fill('Long source');await page.getByLabel('关闭检查器',{exact:true}).click();await openWorkflowActions(page);await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await page.getByLabel('Provider',{exact:true}).selectOption('codex');await page.getByLabel('请求模型',{exact:true}).selectOption('fixture-model');await page.getByLabel('请求强度',{exact:true}).selectOption('low');await page.getByLabel('每轮提示词',{exact:true}).fill('use actual upstream');await node('Long source');await page.getByLabel('下游 Agent',{exact:true}).selectOption({label:'Agent B'});await page.getByRole('button',{name:'连接 Agent',exact:true}).click();await page.getByLabel('关闭检查器',{exact:true}).click();await (await launchButton()).click();await complete();await page.locator('.react-flow__edge').click();const content=await page.getByLabel('实际交接内容',{exact:true}).innerText();const input=JSON.parse(content);assert.equal(input.truncated,true);assert.equal(input.budget,16000);assert.equal(input.text.length,16000);await node('Agent B');const request=await page.getByLabel('实际可见请求',{exact:true}).innerText();assert(request.includes('"truncated": true'));assert(request.includes(JSON.stringify(input.text).slice(1,-1)),'Actual visible request contains the exact encoded transferred text');await page.screenshot({path:path.join(out,'downstream-actual-request.png')});for(const viewport of [{width:1440,height:900},{width:1280,height:800},{width:989,height:535}]){await page.setViewportSize(viewport);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.getByRole('button',{name:'编辑草稿',exact:true}).click({trial:true});await page.screenshot({path:path.join(out,'run-layout-'+viewport.width+'.png')})}
 });
 await test('latest-run-list-response-wins-within-and-across-workspaces',async()=>{
  const {execFileSync}=require('node:child_process');
  const requestStart=requests.length,history=page.getByLabel('运行历史',{exact:true});
  const refresh=history.getByRole('button',{name:'刷新运行历史',exact:true});
  const header=page.locator('.app-header button');
  const entries=()=>history.locator('button.workflow-link > span').evaluateAll(nodes=>nodes.map(node=>node.firstChild.textContent));
  const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const responseScope=(response,scope)=>{
   const url=new URL(response.url());
   return url.pathname==='/api/v2/runs'&&url.searchParams.get('workspace')===scope;
  };
  const observed={fixtureActor:[],reads:[],delivery:[],realModelCalls:0};
  async function capture(response,label){
   const value=await response.json();
   const result={label,status:response.status(),names:Array.isArray(value)?value.map(row=>row.name):null};
   observed.reads.push(result);
   return result;
  }
  async function latest(scope,label,action=()=>refresh.click()){
   const response=page.waitForResponse(reply=>responseScope(reply,scope));
   await action();
   const result=await capture(await response,label);
   await settled();
   observed.delivery.push(label);
   return result;
  }
  async function hold(scope,label,action){
   let release,arrived,rejectCapture,claimed=false;
   const gate=new Promise(resolve=>{release=resolve});
   const captured=new Promise((resolve,reject)=>{arrived=resolve;rejectCapture=reject});
   const pattern='**/api/v2/runs?workspace='+scope;
   const handler=async route=>{
    if(claimed)return route.continue();
    claimed=true;
    try{
     const response=await route.fetch({timeout:8000});
     arrived(await capture(response,label));
     await gate;
     await route.fulfill({response});
    }catch(e){rejectCapture(e);await route.abort().catch(()=>{})}
   };
   await page.route(pattern,handler);
   let timer;
   try{
    await action();
    const earlier=await Promise.race([captured,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('No actual held response: '+label)),8000)})]);
    return {earlier,deliver:async()=>{
     const delivery=page.waitForResponse(reply=>responseScope(reply,scope));
     release();
     assert.equal((await delivery).status(),earlier.status);
     await settled();
     observed.delivery.push(label);
    },close:async()=>{release();await page.unroute(pattern,handler)}};
   }catch(e){release();await page.unroute(pattern,handler);throw e}
   finally{clearTimeout(timer)}
  }
  function seed(scope,name){
   // The fixture actor writes only a synthetic terminal definition and a run
   // with no activations. No HTTP launch, worker, CLI, or Provider is involved.
   execFileSync('python3',['-B','-c',`
from hashlib import sha256
import json, pathlib, sqlite3, sys, time, uuid

database, workspace, name = sys.argv[1:]
database = pathlib.Path(database)
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
graph = dict(version=1, name=name, nodes=[], edges=[])
definition = dict(draft_document=graph, graph=dict(nodes=[], edges=[]))
definition['id'] = sha256(canonical(definition).encode()).hexdigest()
run_id = uuid.uuid4().hex
run = dict(id=run_id, workspace=workspace, definition_id=definition['id'],
           draft_id=uuid.uuid4().hex, draft_revision='0'*64,
           launch_intent=uuid.uuid4().hex, created_at=time.time(),
           state='CANCELLED', cancel_requested=True, actual_calls=0,
           reason='synthetic test actor; never sent')
with sqlite3.connect(database.as_uri()+'?mode=rw', uri=True) as db:
    assert db.execute("SELECT 1 FROM records WHERE kind='workspace' AND id=?", (workspace,)).fetchone()
    db.executemany('INSERT INTO records VALUES(?,?,?)', [
        ('definition', definition['id'], canonical(definition)), ('run', run_id, canonical(run))])
`,path.join(out,'state','v2.sqlite'),scope,name],{timeout:8000});
   observed.fixtureActor.push(name);
  }
  async function temporarilyUnavailable(name,action){
   const original=path.join(process.env.V2_WORKSPACES,name),temporary=original+'-test-rename';
   fs.renameSync(original,temporary);
   try{return await action()}finally{fs.renameSync(temporary,original)}
  }
  const recent=name=>page.locator('.workspace-link').filter({hasText:new RegExp('^'+name+'$')});
  const names=['order fixture first','order fixture second','scope return fixture'];
  let pendingKey,pending;
  try{
   await header.click();
   await page.getByLabel('文件夹路径',{exact:true}).fill(path.join(process.env.V2_WORKSPACES,'B'));
   await page.getByRole('button',{name:'前往',exact:true}).click();
   const open=page.getByRole('button',{name:'打开此文件夹',exact:true});
   await until(()=>open.isEnabled());
   const opening=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/v2/runs');
   await open.click();
   await opening;
   await until(async()=>await header.textContent()==='B');
   await settled();
   const status=await api('status');
   const scope=name=>status.workspaces.find(row=>row.name===name).id;
   const a=scope('A'),b=scope('B');
   seed(b,names[0]);
   const old=await hold(b,'older 200',()=>refresh.click());
   try{
    assert.deepEqual(old.earlier,{label:'older 200',status:200,names:[names[0]]});
    seed(b,names[1]);
    const fresh=await latest(b,'newer 200');
    assert.equal(fresh.status,200);
    assert.deepEqual([...fresh.names].sort(),names.slice(0,2));
    await until(async()=>JSON.stringify(await entries())===JSON.stringify([names[1],names[0]]));
    await old.deliver();
    assert.deepEqual(await entries(),[names[1],names[0]]);
   }finally{await old.close()}

   const aDraft=(await api('drafts?workspace='+a))[0];
   assert(aDraft);
   pendingKey='devflower-v2-launch:'+status.environment.id+':'+a;
   pending=JSON.stringify({workspace:a,draft_id:aDraft.id,revision:aDraft.revision,
    intent_key:'d'.repeat(32),task:''});
   await page.evaluate(([key,value])=>localStorage.setItem(key,value),[pendingKey,pending]);
   const reopened=await latest(a,'open A',()=>recent('A').click());
   assert.equal(reopened.status,200);
   await until(async()=>await header.textContent()==='A'&&(await entries()).length===reopened.names.length);
   const detailList=page.waitForResponse(reply=>responseScope(reply,a));
   await history.locator('button.workflow-link').first().click();
   await complete();
   await detailList;
   await settled();
   const frozen=page.getByLabel('运行状态',{exact:true});
   await frozen.getByText('运行身份与冻结版本',{exact:true}).click();
   const frozenIdentity=await frozen.locator('pre').innerText();
   const originalNames=await entries();

   const failed=await temporarilyUnavailable('A',()=>hold(a,'older 400',()=>refresh.click()));
   try{
    assert.equal(failed.earlier.status,400);
    const fresh=await latest(a,'newer 200 after older 400');
    assert.equal(fresh.status,200);
    await failed.deliver();
    assert.equal(await page.getByRole('alert').count(),0);
    assert.deepEqual(await entries(),originalNames);
    assert.equal(await frozen.locator('pre').innerText(),frozenIdentity);
   }finally{await failed.close()}

   const staleSuccess=await hold(a,'200 superseded by latest 400',()=>refresh.click());
   try{
    const failure=await temporarilyUnavailable('A',()=>latest(a,'latest 400'));
    assert.equal(failure.status,400);
    await until(async()=>(await page.getByRole('alert').allTextContents()).some(text=>text.includes('HTTP 400')));
    await staleSuccess.deliver();
    assert.match(await page.getByRole('alert').innerText(),/HTTP 400/);
    assert.deepEqual(await entries(),originalNames);
    assert.equal(await frozen.locator('pre').innerText(),frozenIdentity);
   }finally{await staleSuccess.close()}

   const previousScope=await hold(a,'A before B and A',()=>refresh.click());
   try{
    assert.equal(previousScope.earlier.status,200);
    seed(a,names[2]);
    assert.equal((await latest(b,'switch to B',()=>recent('B').click())).status,200);
    await until(async()=>await header.textContent()==='B'&&(await entries()).length===2);
    assert.deepEqual(await entries(),[names[1],names[0]]);
    assert.equal((await latest(a,'return to A',()=>recent('A').click())).status,200);
    await until(async()=>await header.textContent()==='A'&&(await entries()).includes(names[2]));
    await previousScope.deliver();
    assert.deepEqual(await entries(),[names[2],...originalNames]);
    assert.equal(await page.getByRole('alert').count(),0);
   }finally{await previousScope.close()}
   assert.equal((await latest(a,'reload A',()=>page.reload())).status,200);
   await until(async()=>await header.textContent()==='A'&&(await entries()).includes(names[2]));
   assert.deepEqual(await entries(),[names[2],...originalNames]);
   assert.equal(await page.getByRole('button',{name:'核验本次启动',exact:true}).count(),1);
   assert.equal(await page.evaluate(key=>localStorage.getItem(key),pendingKey),pending);
   observed.pendingIntentUnchanged=true;
   observed.selectedFrozenIdentityUnchanged=true;
   observed.finalScope='A';
   observed.finalNames=await entries();
  }finally{
   observed.browserRequests=requests.slice(requestStart);
   fs.writeFileSync(path.join(out,'run-list-order.json'),JSON.stringify(observed,null,2));
  }
  assert.deepEqual(observed.browserRequests.filter(request=>request.method!=='GET'),
   Array(4).fill({path:'/api/v2/workspaces/open',method:'POST'}));
 });
 await test('recent-workspace-refuses-a-replaced-directory-by-registered-identity',async()=>{
  const header=page.locator('.app-header button'),name=page.getByLabel('工作流名称',{exact:true});
  const drafts=page.getByLabel('工作流列表',{exact:true}),history=page.getByLabel('运行历史',{exact:true});
  const recent=()=>page.locator('.workspace-link').filter({hasText:/^B$/});
  const source=await api('status'),a=source.workspaces.find(row=>row.name==='A'),b=source.workspaces.find(row=>row.name==='B');
  assert.equal(await header.textContent(),'A');
  const oldDraft=await api('drafts/save',{workspace:b.id,id:null,expected:null,
   document:{version:1,name:'原 B 草稿',nodes:[],edges:[]}});
  const oldRuns=await api('runs?workspace='+b.id);
  assert(oldDraft.id&&oldRuns.length>0);
  const aDrafts=await drafts.locator('button').allTextContents(),aRuns=await history.locator('button.workflow-link').allTextContents();
  assert(aDrafts.length>0&&aRuns.length>0);
  const location=b.path,moved=path.join(path.dirname(location),'B-original-fixture');
  const beforeInode=fs.statSync(location).ino;
  fs.renameSync(location,moved);fs.mkdirSync(location);
  assert.notEqual(fs.statSync(location).ino,beforeInode);
  const observed={parentFolderReplaced:true,oldWorkspace:b.id,oldDraft:oldDraft.id,oldRunIds:oldRuns.map(row=>row.id),realModelCalls:0};
  try{
   await name.fill('A 保留的未保存修改');
   const writes=requests.length;
   const read=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/v2/workspaces/open');
   await recent().click();
   const pending=page.getByRole('dialog',{name:'未保存的草稿',exact:true});
   await pending.getByRole('button',{name:'放弃修改',exact:true}).click();
   const reply=await read,response=await reply.json();
   await until(()=>header.isEnabled());
   observed.recent={status:reply.status(),method:reply.request().method(),path:new URL(reply.url()).pathname,
    body:reply.request().postDataJSON(),response,header:await header.textContent(),name:await name.inputValue(),
    writes:requests.slice(writes).filter(row=>row.method!=='GET')};
   const after=await api('status');
   observed.registeredAfterRecent=after.workspaces.map(row=>row.id);
   assert.equal(observed.recent.status,409);
   assert.equal(observed.recent.method,'POST');
   assert.equal(observed.recent.path,'/api/v2/workspaces/open');
   assert.deepEqual(observed.recent.body,{path:location,expected_workspace:b.id});
   assert.deepEqual(observed.recent.writes,[{path:'/api/v2/workspaces/open',method:'POST'}]);
   assert.deepEqual(observed.registeredAfterRecent.sort(),source.workspaces.map(row=>row.id).sort());
   assert.equal(observed.recent.header,'A');
   assert.equal(observed.recent.name,'A 保留的未保存修改');
   assert.deepEqual(await drafts.locator('button').allTextContents(),aDrafts);
   assert.deepEqual(await history.locator('button.workflow-link').allTextContents(),aRuns);
   await page.getByRole('alert').filter({hasText:'工作空间文件夹身份已变化'}).waitFor();
   observed.invalidIds=[];
   for(const [id,code] of [[a.id,409],['f'.repeat(64),400],[null,400]]){
    const invalid=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/v2/workspaces/open');
    const result=await api('workspaces/open',{path:location,expected_workspace:id});
    assert.equal((await invalid).status(),code);
    assert.equal(typeof result.error,'string');
    observed.invalidIds.push(code);
   }
   assert.deepEqual((await api('status')).workspaces.map(row=>row.id).sort(),source.workspaces.map(row=>row.id).sort());
   const opened=page.waitForResponse(reply=>new URL(reply.url()).pathname==='/api/v2/workspaces/open');
   await header.click();
   const picker=page.getByRole('dialog',{name:'打开工作空间',exact:true});
   await picker.getByLabel('文件夹路径',{exact:true}).fill(location);
   await picker.getByRole('button',{name:'前往',exact:true}).click();
   await picker.getByRole('button',{name:'打开此文件夹',exact:true}).click();
   await pending.getByRole('button',{name:'放弃修改',exact:true}).click();
   const openedReply=await opened,replacement=await openedReply.json();
   assert.equal(openedReply.status(),200);
   assert.equal(openedReply.request().method(),'POST');
   assert.deepEqual(openedReply.request().postDataJSON(),{path:location});
   assert.notEqual(replacement.id,b.id);assert.notEqual(replacement.id,a.id);
   await until(async()=>await header.textContent()==='B'&&await header.isEnabled());
   assert.equal(await name.inputValue(),'新工作流');
   assert.equal(await drafts.locator('button').count(),0);
   assert.equal(await history.locator('button.workflow-link').count(),0);
   assert.deepEqual(await api('drafts?workspace='+replacement.id),[]);
   assert.deepEqual(await api('runs?workspace='+replacement.id),[]);
   observed.explicit={status:openedReply.status(),replacement:replacement.id};

   fs.rmdirSync(location);fs.renameSync(moved,location);
   const reopened=page.waitForResponse(reply=>{
    const url=new URL(reply.url());
    return url.pathname==='/api/v2/drafts'&&url.searchParams.get('workspace')===b.id;
   });
   await recent().last().click();
   assert.equal((await reopened).status(),200);
   await drafts.getByRole('button',{name:'原 B 草稿',exact:true}).waitFor();
   await until(async()=>await history.locator('button.workflow-link').count()===oldRuns.length);
   assert.deepEqual((await api('runs?workspace='+b.id)).map(row=>row.id),observed.oldRunIds);
   observed.restoredOldIdentity=true;
  }finally{
   if(fs.existsSync(moved)){fs.rmdirSync(location);fs.renameSync(moved,location)}
   fs.writeFileSync(path.join(out,'recent-workspace-identity.json'),JSON.stringify(observed,null,2));
  }
 });
 await test('run-edit-opens-its-saved-draft-and-preserves-editor-and-run-identities',async()=>{
  const {id:workspace}=await api('workspaces/open',{path:path.join(process.env.V2_WORKSPACES,'B')});
  assert(workspace);
  const name=page.getByLabel('工作流名称',{exact:true});
  const drafts=page.getByLabel('工作流列表',{exact:true}),history=page.getByLabel('运行历史',{exact:true});
  const editSource=page.getByRole('button',{name:'编辑草稿',exact:true});
  const draftButton=label=>drafts.getByRole('button',{name:label,exact:true});
  const runButton=label=>history.locator('button.workflow-link').filter({hasText:label});
  const pending=page.getByRole('dialog',{name:'未保存的草稿',exact:true});
  const draft=label=>({version:1,name:label,nodes:[{id:'Agent',name:'Draft Agent',position:{x:80,y:100},
   provider:'codex',model:'fixture-model',effort:'low',round_prompt:'Check draft identity'}],edges:[]});
  const saveDraft=(document,row)=>api('drafts/save',{workspace,document,id:row?.id??null,expected:row?.revision??null});
  const a=await saveDraft(draft('运行来源 Alpha')),b=await saveDraft(draft('运行来源 Beta'));
  assert(a.id&&b.id,JSON.stringify({a,b}));
  const launch=row=>api('launch',{workspace,draft_id:row.id,revision:row.revision,
   intent_key:'fixture-run-source-'+row.id,task:''});
  const ar=await launch(a),br=await launch(b);
  assert(ar.run?.id&&br.run?.id);
  const latest=await saveDraft(draft('Alpha 当前版本'),a);
  assert.equal(latest.id,a.id);
  await history.getByRole('button',{name:'刷新运行历史',exact:true}).click();
  await runButton('运行来源 Beta').click();
  await editSource.click();
  await until(async()=>await name.inputValue()==='运行来源 Beta'&&await name.isEnabled());
  await name.fill('Beta 保存后修改');
  await runButton('运行来源 Alpha').click();
  await complete();
  assert.equal(await name.inputValue(),'运行来源 Alpha');
  await editSource.click();
  await pending.getByRole('button',{name:'取消切换',exact:true}).click();
  assert.equal(await name.inputValue(),'运行来源 Alpha');
  assert(await name.isDisabled());
  await editSource.click();
  await pending.getByRole('button',{name:'保存并继续',exact:true}).click();
  await until(async()=>await name.inputValue()==='Alpha 当前版本'&&await name.isEnabled());
  assert.equal((await api('draft?workspace='+workspace+'&id='+b.id)).graph.name,'Beta 保存后修改');
  const frozen=await api('run?workspace='+workspace+'&id='+ar.run.id);
  assert.equal(frozen.definition.draft_document.name,'运行来源 Alpha');
  assert.equal(frozen.run.draft_revision,a.revision);

  await draftButton('Beta 保存后修改').click();
  await until(async()=>await name.inputValue()==='Beta 保存后修改'&&await name.isEnabled());
  await name.fill('Beta 放弃的修改');
  await runButton('运行来源 Alpha').click();
  await editSource.click();
  await pending.getByRole('button',{name:'放弃修改',exact:true}).click();
  await until(async()=>await name.inputValue()==='Alpha 当前版本'&&await name.isEnabled());
  assert.equal((await api('draft?workspace='+workspace+'&id='+b.id)).graph.name,'Beta 保存后修改');

  let resume,held;
  const blocked=new Promise(resolve=>{held=resolve}),release=new Promise(resolve=>{resume=resolve});
  const exactRead=url=>url.pathname==='/api/v2/draft'&&url.searchParams.get('id')===a.id;
  await draftButton('Beta 保存后修改').click();
  await until(async()=>await name.inputValue()==='Beta 保存后修改'&&await name.isEnabled());
  await runButton('运行来源 Alpha').click();
  await complete();
  await page.route(exactRead,async route=>{held();await release;await route.continue()});
  try{
   await editSource.click();
   await blocked;
   await runButton('运行来源 Beta').click();
   await complete();
   const response=page.waitForResponse(r=>exactRead(new URL(r.url())));
   resume();
   assert.equal((await response).status(),200);
   await until(()=>editSource.isEnabled());
   assert.equal(await name.inputValue(),'运行来源 Beta');
   assert(await name.isDisabled());
  }finally{resume();await page.unroute(exactRead)}

  // The fixture represents historical data whose editable draft disappeared.
  const {execFileSync}=require('node:child_process');
  execFileSync('python3',['-B','-c',`import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    removed=db.execute("DELETE FROM records WHERE kind='draft' AND id=?", (sys.argv[2],))
    assert removed.rowcount == 1`,path.join(out,'state','v2.sqlite'),a.id]);
  await runButton('运行来源 Alpha').click();
  const fallback=page.getByRole('button',{name:'返回当前草稿',exact:true});
  await fallback.waitFor();
  assert.equal(await editSource.count(),0);
  assert.equal(await name.inputValue(),'运行来源 Alpha');
  await fallback.click();
  assert.equal(await name.inputValue(),'Beta 保存后修改');
 });
 // Use a fresh browser profile for native execution. The managed fault cases
 // deliberately leave durable launch intents in the previous profile.
 await context.tracing.stop({path:path.join(out,'managed-trace.zip')});
 await context.close();
 context=await browser.newContext({viewport:{width:1440,height:900}});
 page=await context.newPage();page.setDefaultTimeout(10000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/'))requests.push({path:url.pathname,method:request.method()})});
 await context.tracing.start({screenshots:true,snapshots:true});
 await page.goto(process.env.V2_URL);
 await test('native-condition-compile-launch-and-distinguish-selected-branches',async()=>{
  const button=name=>page.getByRole('button',{name,exact:true});
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const nodes=[];
  for(const [name,provider,cwd] of [['Developer','codex','.'],['Reviewer','claude','.'],
    ['Success','codex','success'],['Correction','claude','correction']]){
   const agent=await api('agents/save',{workspace,id:null,expected:null,session_revision:null,
    config:{execution:'native-cli',name,provider,cwd:path.join(process.env.V2_WORKSPACES,'A',cwd),
     role:name,round_prompt:'Fixture',output_prompt:'Deliver'}});
   assert(agent.revision,JSON.stringify(agent));
   nodes.push({id:name,name,kind:'agent',agent_id:agent.id,agent_revision:agent.revision,position:{x:200+nodes.length*250,y:170}});
  }
  nodes[0].native_output={version:1,kind:'result'};nodes[0].artifact_paths=['a.py','b.py'];
  nodes[1].native_output={version:1,kind:'review'};
  for(const [id,kind,x,y,config] of [['Start','start',0,170,{}],['Gate','condition',700,0,{}],
    ['Yes','output',1200,0,{}],['No','output',1200,300,{}]]){
   nodes.push({id,name:id,kind,config_version:1,position:{x,y},config});
  }
  const edges=[['Start','Developer'],['Developer','Reviewer'],['Reviewer','Gate'],
   ['Success','Yes'],['Correction','No']].map(([source,target],i)=>
   ({id:'edge'+i,source,target,source_port:'out',target_port:'in'}));
  const name='P2-A 条件夹具';
  const draft=await api('drafts/save',{workspace,id:null,expected:null,document:{version:1,name,nodes,edges}});
  assert(draft.revision,JSON.stringify(draft));
  await page.reload();
  await page.locator('.workspace-link').filter({hasText:'A'}).click();
  await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name,exact:true}).click();
  await page.getByLabel('适应画布',{exact:true}).click();await node('Gate');
  await button('使用候选审核条件').click();
  assert.equal(await page.getByLabel('判定字段',{exact:true}).inputValue(),'result.verdict');
  for(const [port,target] of [['true','Success'],['false','Correction']]){
   await page.getByLabel('源输出端口',{exact:true}).selectOption(port);
   await page.getByLabel('下游节点',{exact:true}).selectOption(target);
   await button('连接节点').click();
  }
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  await page.reload();await button('检查').click();
  await page.getByText('原生脚本编译通过。检查未启动 CLI。',{exact:true}).waitFor();
  await page.getByLabel('关闭检查结果',{exact:true}).click();
  await button('预览 Python').click();
  const preview=page.getByRole('dialog',{name:'Python 脚本预览'});
  const script=await preview.locator('pre').textContent();
  assert.match(script,/if evaluate_condition\(context, WORKFLOW\['condition'\]/);
  await preview.getByRole('button',{name:'关闭预览'}).click();
  for(const [selected,skipped,verdict,task] of [['Success','Correction','accept',''],
    ['Correction','Success','needs_correction','P2_FIXTURE_CORRECTION']]){
   if(task){
    await button('编辑草稿').click();
    const context=page.getByLabel('本次运行上下文',{exact:true}).locator('summary');
    await context.click();
    await page.getByLabel('本次任务',{exact:true}).fill(task);
    await context.click();
   }
   const [response]=await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/api/v2/launch')), button('启动').click()]);
   const {run}=await response.json();
   assert.equal(response.status(),200);assert.equal(run.execution,'native-cli');await complete();
   const monitor=page.getByLabel('原生脚本运行证据',{exact:true});
   if(!await monitor.isVisible())await button('运行检查器').click();
   const branch=monitor.getByRole('region',{name:'条件分支'});
   await branch.getByText(verdict,{exact:true}).waitFor();
   assert.match(await branch.getByText(/^未选中 /).textContent(),new RegExp(skipped));
   assert.equal(await branch.getByText(/^实际启动 /).textContent(),'实际启动 Developer → Reviewer → '+selected);
   assert.equal(await monitor.getByLabel('工作流最终回答',{exact:true}).textContent(),selected.toUpperCase()+'_FINISHED');
   await monitor.getByLabel('查看证据').selectOption({label:skipped+'（分支未选中）'});
   await monitor.getByText('此 Agent 所在分支未选中。',{exact:true}).waitFor();
   assert.equal(await monitor.getByLabel('实际请求（含上游交接）',{exact:true}).count(),0);
   const [download]=await Promise.all([page.waitForEvent('download'),button('下载本次运行脚本').click()]);
   const filename=path.join(out,run.id+'.py');await download.saveAs(filename);
   assert.equal(fs.readFileSync(filename,'utf8'),script);
  }
 });
 await test('native-router-reorders-rules-keeps-ports-and-shows-evidence',async()=>{
  const button=name=>page.getByRole('button',{name,exact:true});
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const nodes=[];
  for(const name of ['Classifier','Research','Development','General']){
   const agent=await api('agents/save',{workspace,id:null,expected:null,session_revision:null,
    config:{execution:'native-cli',name,provider:'codex',cwd:path.join(process.env.V2_WORKSPACES,'A'),
     role:name,round_prompt:'Fixture',output_prompt:'Deliver'}});
   assert(agent.revision,JSON.stringify(agent));
   const i=nodes.length;
   nodes.push({id:name,name,kind:'agent',agent_id:agent.id,agent_revision:agent.revision,
    position:{x:i?780:245,y:i?200*(i-1):170}});
  }
  nodes[0].native_output={version:1,kind:'result'};
  nodes[0].output_contract=[{field:'result.data.category',type:'string',required:true}];
  for(const [id,kind,x,y,config] of [['Start','start',0,170,{}],['Router','router',520,170,{}],
    ['ResearchOut','output',1040,0,{}],['DevelopmentOut','output',1040,200,{}],['GeneralOut','output',1040,400,{}]]){
   nodes.push({id,name:id,kind,config_version:1,position:{x,y},config});
  }
  const edges=[['Start','Classifier'],['Classifier','Router'],['Research','ResearchOut'],
   ['Development','DevelopmentOut'],['General','GeneralOut']].map(([source,target],i)=>({id:'edge'+i,source,target}));
  const name='P2-B 路由夹具';
  const seed=await api('drafts/save',{workspace,id:null,expected:null,document:{version:1,name,nodes,edges}});
  assert(seed.revision,JSON.stringify(seed));
  await page.reload();
  await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name,exact:true}).click();
  await page.getByLabel('适应画布',{exact:true}).click();await node('Router');
  const inspector=page.getByLabel('节点配置',{exact:true});
  for(const [index,label] of ['科研任务','开发任务'].entries()){
   await button('添加分支规则').click();
   await inspector.getByLabel('分支名称 '+(index+1),{exact:true}).fill(label);
   await inspector.getByLabel('路由字段 '+(index+1),{exact:true}).fill('result.data.category');
   await inspector.getByRole('group',{name:'规则 '+(index+1),exact:true}).getByLabel('比较值',{exact:true}).fill('research');
  }
  async function connect(portLabel,target){
   await inspector.getByLabel('源输出端口',{exact:true}).selectOption({label:portLabel});
   await inspector.getByLabel('下游节点',{exact:true}).selectOption(target);
   await button('连接节点').click();
  }
  await connect('科研任务','Research');
  await connect('开发任务','Development');
  await button('检查').click();
  await page.getByText(/Missing: fallback/).waitFor();
  await page.getByLabel('关闭检查结果',{exact:true}).click();
  await connect('默认分支','General');
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  const query='draft?'+new URLSearchParams({workspace,id:seed.id});
  const before=(await api(query)).graph;
  const rules=before.nodes.find(current=>current.id==='Router').config.rules;
  await page.reload();
  await page.getByLabel('适应画布',{exact:true}).click();await node('Router');
  await button('上移规则 2').click();
  assert.equal(await inspector.getByLabel('分支名称 1',{exact:true}).inputValue(),'开发任务');
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  await page.reload();
  const after=(await api(query)).graph;
  assert.deepEqual(after.nodes.find(current=>current.id==='Router').config.rules,[rules[1],rules[0]]);
  assert.deepEqual(after.edges,before.edges);
  await button('检查').click();
  await page.getByText('原生脚本编译通过。检查未启动 CLI。',{exact:true}).waitFor();
  await page.getByLabel('关闭检查结果',{exact:true}).click();
  await button('预览 Python').click();
  const preview=page.getByRole('dialog',{name:'Python 脚本预览'});
  const source=await preview.locator('pre').textContent();
  assert.match(source,/port = evaluate_router/);
  await preview.getByRole('button',{name:'关闭预览'}).click();
  const [exported]=await Promise.all([page.waitForEvent('download'),button('导出 Python（原生 CLI）').click()]);
  const exportedPath=path.join(out,'router-exported.py');
  await exported.saveAs(exportedPath);
  assert.equal(fs.readFileSync(exportedPath,'utf8'),source);
  for(const [category,expectedPort,selected] of [['research',rules[1].id,'Development'],['other','fallback','General']]){
   if(expectedPort==='fallback'){
    await button('编辑草稿').click();
    const context=page.getByLabel('本次运行上下文',{exact:true}).locator('summary');
    await context.click();
    await page.getByLabel('本次任务',{exact:true}).fill('P2_ROUTER_FALLBACK');
    await context.click();
   }
   const [response]=await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/api/v2/launch')),button('启动').click()]);
   const {run}=await response.json();
   assert.equal(response.status(),200);assert.equal(run.execution,'native-cli');await complete();
   const monitor=page.getByLabel('原生脚本运行证据',{exact:true});
   if(!await monitor.isVisible())await button('运行检查器').click();
   const branch=monitor.getByRole('region',{name:'路由分支'});
   assert.equal(await branch.getByText(/^选中端口 /).textContent(),'选中端口 '+expectedPort);
   await branch.getByText(expectedPort==='fallback'?/未命中规则，使用 fallback/:/命中规则 开发任务/).waitFor();
   assert.deepEqual(JSON.parse(await branch.getByLabel('上游参与路由的结构化输入').textContent()),{'result.data.category':category});
   assert.equal(await branch.getByText(/^实际启动 /).textContent(),'实际启动 Classifier → '+selected);
   assert.equal(await branch.getByText(/^未选中 /).count(),2);
   assert.match(await branch.getByText(/^未选中 科研任务/).textContent(),/Research/);
   assert.equal(await monitor.getByLabel('工作流最终回答',{exact:true}).textContent(),selected.toUpperCase()+'_FINISHED');
   await button('查看上游完整交付').click();
   await monitor.getByText('结构化交付与候选文件',{exact:true}).click();
   assert.equal(JSON.parse(await monitor.getByLabel('结构化结果',{exact:true}).textContent()).result.data.category,category);
   const [download]=await Promise.all([page.waitForEvent('download'),button('下载本次运行脚本').click()]);
   const file=path.join(out,run.id+'.py');await download.saveAs(file);
   assert.equal(fs.readFileSync(file,'utf8'),source);
  }
 });
 for(const scenario of ['plain','validation','composed'])await test({
  plain:'native-loop-freezes-saved-body-and-shows-round-evidence',
  validation:'native-loop-command-edit-retry-and-evidence',
  composed:'native-loop-composed-handoffs-survive-reload',
 }[scenario],async()=>{
  const withTool=scenario==='validation',composed=scenario==='composed';
  const button=name=>page.getByRole('button',{name,exact:true});
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const project=path.join(process.env.V2_WORKSPACES,'A');
  const nodes=[];
  for(const [name,provider] of [['Developer','codex'],['Reviewer','claude']]){
   const agent=await api('agents/save',{workspace,id:null,expected:null,session_revision:null,
    config:{execution:'native-cli',name,provider,cwd:project,role:name,round_prompt:'Fixture',output_prompt:'Deliver'}});
   assert(agent.revision,JSON.stringify(agent));
   nodes.push({id:name,name,kind:'agent',agent_id:agent.id,agent_revision:agent.revision,
    position:{x:240+nodes.length*240,y:130},native_output:{version:1,kind:name==='Developer'?'result':'review'}});
  }
  nodes[0].artifact_paths=['a.py','b.py'];
  const typed=(id,kind,x)=>({id,name:id,kind,position:{x,y:130},config_version:1,config:{}});
  const edges=ids=>ids.slice(1).map((target,i)=>({id:'edge'+i,source:ids[i],target}));
  let bodyGraph={version:1,name:withTool?'P3.1 验证子流程':'P3 冻结子流程',
   nodes:[typed('BodyStart','start',0),...nodes,typed('BodyEnd','output',720)],
   edges:edges(['BodyStart','Developer',...(withTool?['Validation']:[]),'Reviewer','BodyEnd'])};
  if(withTool){
   bodyGraph.nodes.push(typed('Validation','tool',430));
   bodyGraph.nodes.find(item=>item.id==='Reviewer').position.x=660;
   bodyGraph.nodes.find(item=>item.id==='BodyEnd').position.x=890;
  }
  let body=await api('drafts/save',{workspace,id:null,expected:null,document:bodyGraph});
  assert(body.revision,JSON.stringify(body));
  const argv=['python3','-B','verify.py','literal ; argument'];
  if(withTool){
   const qa=path.join(project,'qa');fs.mkdirSync(qa);
   fs.writeFileSync(path.join(qa,'verify.py'),[
    'from pathlib import Path','import sys',"assert sys.argv[1] == 'literal ; argument'",
    "iteration = Path('../a.py').read_text().split()[1]",
    "print('test stdout ' + iteration)","print('test stderr ' + iteration,file=sys.stderr)","sys.exit(iteration == '1')"
   ].join('\n'));
   await page.reload();
   await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:bodyGraph.name,exact:true}).click();
   await page.getByLabel('适应画布',{exact:true}).click();await node('Validation');
   await page.getByLabel('工具类型',{exact:true}).selectOption('command');
   const args=page.getByLabel('命令参数（JSON）',{exact:true});
   await args.fill('["broken"');
   await page.getByRole('alert').filter({hasText:'请输入有效的 JSON 字符串数组'}).waitFor();
   await args.fill(JSON.stringify(argv));
   await page.getByLabel('命令工作目录',{exact:true}).fill('qa');
   await page.getByLabel('命令超时（秒）',{exact:true}).fill('10');
   await page.getByLabel('关闭检查器',{exact:true}).click();await save();
   await page.reload();await page.getByLabel('适应画布',{exact:true}).click();await node('Validation');
   assert.deepEqual(JSON.parse(await args.inputValue()),argv);
   assert.equal(await page.getByLabel('命令工作目录',{exact:true}).inputValue(),'qa');
   assert.equal(await page.getByLabel('命令超时（秒）',{exact:true}).inputValue(),'10');
   await page.getByLabel('关闭检查器',{exact:true}).click();
   body=await api('draft?'+new URLSearchParams({workspace,id:body.id}));
   bodyGraph=body.graph;
  }
  const name=composed?'P4-B 组合循环夹具':withTool?'P3.1 验证循环夹具':'P3 原生循环夹具';
  let outerNodes=[typed('Start','start',0),typed('Loop','loop',260),typed('End','output',520)];
  if(composed){
   const outerAgent=async(id,name,x)=>{
    const row=await api('agents/save',{workspace,id:null,expected:null,session_revision:null,
     config:{execution:'native-cli',name,provider:'codex',cwd:project,role:name,round_prompt:'Fixture',output_prompt:'Deliver'}});
    assert(row.revision,JSON.stringify(row));
    return {id,name,kind:'agent',position:{x,y:130},agent_id:row.id,agent_revision:row.revision};
   };
   const planner=await outerAgent('Developer','Planner',220);
   planner.native_output={version:1,kind:'result'};planner.artifact_paths=['plan.txt'];
   const pack=typed('Package','tool',670);
   pack.config={tool:'command',cwd:'.',argv:['python3','-c',[
    'import json, sys','from pathlib import Path','d = json.load(sys.stdin)',
    "assert d['accepted_iteration'] == 2 and d['structured']['result']['verdict'] == 'accept'",
    "assert d['candidate']['candidate_sha256'] == Path('previous-digest.txt').read_text()",
    "print('consumed ' + d['candidate']['candidate_sha256'])",
   ].join('\n')]};
   outerNodes=[outerNodes[0],planner,typed('Loop','loop',440),pack,
    await outerAgent('Publisher','Publisher',900),typed('End','output',1120)];
  }
  const outer=await api('drafts/save',{workspace,id:null,expected:null,document:{version:1,name,
   nodes:outerNodes,edges:edges(outerNodes.map(item=>item.id))}});
  assert(outer.revision,JSON.stringify(outer));
  await page.reload();
  await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name,exact:true}).click();
  await page.getByLabel('适应画布',{exact:true}).click();await node('Loop');
  await page.getByLabel('循环子流程',{exact:true}).selectOption(body.id);
  await button('使用候选审核条件').click();
  await page.getByLabel('最多迭代次数',{exact:true}).fill('2');
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  const request='draft?'+new URLSearchParams({workspace,id:outer.id});
  const frozen=(await api(request)).graph.nodes.find(current=>current.id==='Loop').config;
  assert.equal(frozen.max_iterations,2);
  assert.deepEqual(frozen.body,bodyGraph);
  const changed=await api('drafts/save',{workspace,id:body.id,expected:body.revision,
   document:{...bodyGraph,name:'P3 已修改的源子流程'}});
  assert(changed.revision&&changed.revision!==body.revision);
  await page.reload();
  assert.deepEqual((await api(request)).graph.nodes.find(current=>current.id==='Loop').config,frozen);
  await button('检查').click();
  await page.getByText('原生脚本编译通过。检查未启动 CLI。',{exact:true}).waitFor();
  await page.getByLabel('关闭检查结果',{exact:true}).click();
  await button('预览 Python').click();
  const preview=page.getByRole('dialog',{name:'Python 脚本预览'});
  const source=await preview.locator('pre').textContent();
  assert(source.includes("results['Loop'] = run_loop(context, WORKFLOW['loop'], task, "+
   (composed?"results['Developer']":"None")+", None)"));
  await preview.getByRole('button',{name:'关闭预览'}).click();
  const [exported]=await Promise.all([page.waitForEvent('download'),button('导出 Python（原生 CLI）').click()]);
  const exportedPath=path.join(out,composed?'loop-composed-exported.py':'loop-exported.py');await exported.saveAs(exportedPath);
  assert.equal(fs.readFileSync(exportedPath,'utf8'),source);
  const task=page.getByLabel('本次运行上下文',{exact:true}).locator('summary');
  await task.click();await page.getByLabel('本次任务',{exact:true}).fill('P3_LOOP');await task.click();
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/v2/launch')),button('启动').click()]);
  const {run}=await response.json();
  assert.equal(response.status(),200);assert.equal(run.execution,'native-cli');await complete();
  if(composed){await page.reload();await complete()}
  const monitor=page.getByLabel('原生脚本运行证据',{exact:true});
  if(!await monitor.isVisible())await button('运行检查器').click();
  const rounds=monitor.getByRole('region',{name:'循环轮次'});
  const first=rounds.getByRole('region',{name:'第 1 轮',exact:true});
  const second=rounds.getByRole('region',{name:'第 2 轮',exact:true});
  await first.getByText(/历史候选/).waitFor();
  if(withTool){
   await first.getByText(/失败 · 退出码 1/).waitFor();
   await first.getByText(/未执行/).waitFor();
   assert.equal(await first.getByText('needs_correction',{exact:true}).count(),0);
   await second.getByText(/通过 · 退出码 0/).waitFor();
   await first.getByRole('button',{name:'Validation',exact:true}).click();
   const invoked=JSON.parse(await monitor.getByLabel('实际命令与工作目录',{exact:true}).textContent());
   assert.deepEqual(invoked,{argv,cwd:path.join(project,'qa')});
   assert.match(await monitor.getByLabel('验证 stdout',{exact:true}).textContent(),/test stdout 1/);
   assert.match(await monitor.getByLabel('验证 stderr',{exact:true}).textContent(),/test stderr 1/);
   await monitor.getByLabel('查看证据').selectOption('');
  }else{
   await first.getByText('needs_correction',{exact:true}).waitFor();
   await first.getByText(/Improve 1/).waitFor();
  }
  await second.getByText('accept',{exact:true}).waitFor();
  assert.notEqual(await first.getByText(/^候选摘要 /).textContent(),await second.getByText(/^候选摘要 /).textContent());
  const finalAnswer=await monitor.getByLabel('工作流最终回答',{exact:true}).textContent();
  if(composed){
   assert.equal(finalAnswer,'PUBLISHED');
   const chain=monitor.getByRole('region',{name:'外层执行顺序',exact:true});
   assert.deepEqual(await chain.locator('li').allTextContents(),['Planner · 成功','Loop · 成功','Package · 成功','Publisher · 成功']);
   const choice=monitor.getByLabel('查看证据');
   await choice.selectOption({label:'Planner'});
   assert.match(await monitor.getByLabel('最终回答',{exact:true}).textContent(),/Plan from upstream/);
   await choice.selectOption({label:'Package'});
   const delivery=JSON.parse(await monitor.getByLabel('命令标准输入（上游交接）',{exact:true}).textContent());
   assert.equal(delivery.structured.result.verdict,'accept');
   assert.equal(delivery.accepted_iteration,2);
   assert.match(await monitor.getByLabel('命令 stdout',{exact:true}).textContent(),new RegExp(delivery.candidate.candidate_sha256));
   await choice.selectOption({label:'Publisher'});
   assert.match(await monitor.getByLabel('实际请求（含上游交接）',{exact:true}).textContent(),/consumed /);
   await choice.selectOption('');
   assert.match(await second.getByText(/^候选摘要 /).textContent(),new RegExp(delivery.candidate.candidate_sha256));
  }else{
   assert.equal(JSON.parse(finalAnswer).result.verdict,'accept');
  }
  await second.getByRole('button',{name:'Developer',exact:true}).click();
  const actualRequest=monitor.getByLabel('实际请求（含上游交接）',{exact:true});
  await actualRequest.waitFor();
  assert.match(await actualRequest.textContent(),withTool?/Validation failed \(exit 1\)/:/Improve 1/);
  await monitor.getByLabel('查看证据').selectOption('1/Developer');
  await actualRequest.waitFor();
  await until(async()=>!(await actualRequest.textContent()).includes('Previous iteration'));
  if(composed)assert.match(await actualRequest.textContent(),/Plan from upstream/);
  const evidencePath=new URLSearchParams({workspace,id:run.id,node:'Developer',iteration:'3'});
  assert.match((await api('script-run/evidence?'+evidencePath)).error,/有效轮次/);
  const [download]=await Promise.all([page.waitForEvent('download'),button('下载本次运行脚本').click()]);
  const file=path.join(out,run.id+'.py');await download.saveAs(file);
  assert.equal(fs.readFileSync(file,'utf8'),source);
  fs.writeFileSync(path.join(project,'a.py'),'changed after Loop completion');
  await monitor.getByLabel('查看证据').selectOption('');
  await button('重新读取').click();
  await page.getByLabel('运行状态',{exact:true}).getByText('待核验',{exact:true}).waitFor();
  await rounds.getByText(/最终候选已变化/).waitFor();
  assert.equal(await monitor.getByLabel('工作流最终回答',{exact:true}).count(),0);
 });
 await test('ordinary-command-add-connect-reopen-and-failed-result-handoff',async()=>{
  const button=name=>page.getByRole('button',{name,exact:true});
  const workspace=(await api('status')).workspaces.find(row=>row.name==='A').id;
  const project=path.join(process.env.V2_WORKSPACES,'A');
  const nodes=[];
  for(const [name,provider] of [['Developer','codex'],['Reviewer','claude']]){
   const agent=await api('agents/save',{workspace,id:null,expected:null,session_revision:null,
    config:{execution:'native-cli',name,provider,cwd:project,role:name,round_prompt:'Fixture',output_prompt:'Deliver'}});
   assert(agent.revision,JSON.stringify(agent));
   nodes.push({id:name,name,kind:'agent',agent_id:agent.id,agent_revision:agent.revision,
    position:{x:220+500*nodes.length,y:180},native_output:{version:1,kind:name==='Developer'?'result':'review'}});
  }
  nodes[0].artifact_paths=['a.py','b.py'];
  for(const [id,kind,x] of [['Start','start',0],['End','output',960]])
   nodes.push({id,name:id,kind,position:{x,y:180},config_version:1,config:{}});
  const name='P4-A 命令夹具';
  const draft=await api('drafts/save',{workspace,id:null,expected:null,document:{version:1,name,nodes,
   edges:[{id:'entry',source:'Start',target:'Developer'},{id:'exit',source:'Reviewer',target:'End'}]}});
  assert(draft.revision,JSON.stringify(draft));
  const qa=path.join(project,'qa');fs.mkdirSync(qa,{recursive:true});
  fs.writeFileSync(path.join(qa,'p4_verify.py'),[
   'import sys',"assert sys.argv[1]=='literal ; argument'","print('command stdout')",
   "print('command stderr',file=sys.stderr)","sys.exit(7)"
  ].join('\n'));
  const argv=['python3','-B','p4_verify.py','literal ; argument'];
  await page.reload();
  await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name,exact:true}).click();
  await button('添加节点').click();
  const picker=page.getByRole('dialog',{name:'在画布添加节点',exact:true});
  await picker.getByLabel('搜索节点类型').fill('Tool');
  await picker.getByLabel('搜索节点类型').press('Enter');
  await page.getByLabel('节点名称',{exact:true}).fill('P4 Verify');
  await page.getByLabel('工具类型',{exact:true}).selectOption('command');
  await page.getByLabel('命令参数（JSON）',{exact:true}).fill(JSON.stringify(argv));
  await page.getByLabel('命令工作目录',{exact:true}).fill('qa');
  await page.getByLabel('命令超时（秒）',{exact:true}).fill('10');
  await page.getByLabel('下游节点',{exact:true}).selectOption({label:'Reviewer'});
  await button('连接节点').click();
  await page.getByLabel('关闭检查器',{exact:true}).click();
  await page.getByLabel('适应画布',{exact:true}).click();await node('Developer');
  await page.getByLabel('下游 Agent',{exact:true}).selectOption({label:'P4 Verify'});
  await button('连接 Agent').click();
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  await page.reload();await page.getByLabel('适应画布',{exact:true}).click();await node('P4 Verify');
  assert.deepEqual(JSON.parse(await page.getByLabel('命令参数（JSON）',{exact:true}).inputValue()),argv);
  assert.equal(await page.getByLabel('命令工作目录',{exact:true}).inputValue(),'qa');
  assert.equal(await page.getByLabel('命令超时（秒）',{exact:true}).inputValue(),'10');
  await page.getByLabel('关闭检查器',{exact:true}).click();await save();
  await button('检查').click();
  await page.getByText('原生脚本编译通过。检查未启动 CLI。',{exact:true}).waitFor();
  await page.getByLabel('关闭检查结果',{exact:true}).click();
  await button('预览 Python').click();
  const preview=page.getByRole('dialog',{name:'Python 脚本预览'});
  const source=await preview.locator('pre').textContent();
  assert.match(source,/'p4_verify\.py',\s*'literal ; argument'/);
  await preview.getByRole('button',{name:'关闭预览'}).click();
  const [exported]=await Promise.all([page.waitForEvent('download'),button('导出 Python（原生 CLI）').click()]);
  const exportedPath=path.join(out,'command-exported.py');await exported.saveAs(exportedPath);
  assert.equal(fs.readFileSync(exportedPath,'utf8'),source);
  const task=page.getByLabel('本次运行上下文',{exact:true}).locator('summary');
  await task.click();await page.getByLabel('本次任务',{exact:true}).fill('P4_TOOL');await task.click();
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/v2/launch')),button('启动').click()]);
  const {run}=await response.json();
  assert.equal(response.status(),200);assert.equal(run.execution,'native-cli');await complete();
  const monitor=page.getByLabel('原生脚本运行证据',{exact:true});
  if(!await monitor.isVisible())await button('运行检查器').click();
  const select=monitor.getByLabel('查看证据');
  await select.selectOption({label:'P4 Verify'});
  await monitor.getByText(/P4 Verify · 失败 · 退出码 7/).waitFor();
  assert.deepEqual(JSON.parse(await monitor.getByLabel('实际命令与工作目录',{exact:true}).textContent()),{argv,cwd:qa});
  const delivery=JSON.parse(await monitor.getByLabel('结构化结果',{exact:true}).textContent());
  assert.equal(delivery.result.success,false);
  assert.equal((await monitor.getByLabel('命令 stdout',{exact:true}).textContent()).trim(),'command stdout');
  assert.equal((await monitor.getByLabel('命令 stderr',{exact:true}).textContent()).trim(),'command stderr');
  await select.selectOption({label:'Reviewer'});
  const request=await monitor.getByLabel('实际请求（含上游交接）',{exact:true}).textContent();
  assert.deepEqual(JSON.parse(request.split('Upstream department delivery (data)\n')[1]).structured,delivery);
  const [download]=await Promise.all([page.waitForEvent('download'),button('下载本次运行脚本').click()]);
  const file=path.join(out,run.id+'.py');await download.saveAs(file);
  assert.equal(fs.readFileSync(file,'utf8'),source);
 });
 assert.equal(errors.length,0);await context.tracing.stop({path:path.join(out,'trace.zip')});await browser.close();const result={category:'SYNTHETIC execution; real HTTP/SQLite/Chromium',complete:true,tests:rows.length,failures:rows.filter(r=>!r.passed).length,rows,errors,requests};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({tests:result.tests,failures:result.failures}));process.exitCode=result.failures?1:0;
})().catch(async e=>{fs.writeFileSync(path.join(out,'fatal.txt'),e.stack);await context?.tracing.stop({path:path.join(out,'trace-failure.zip')}).catch(()=>{});await browser?.close();console.error(e.message);process.exitCode=1});
