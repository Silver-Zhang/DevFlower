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
  await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await page.getByLabel('Provider',{exact:true}).selectOption('codex');await page.getByRole('button',{name:'刷新模型目录',exact:true}).click();await page.getByLabel('请求模型',{exact:true}).selectOption('fixture-model');await page.getByLabel('请求强度',{exact:true}).selectOption('low');await page.getByLabel('每轮提示词',{exact:true}).fill('synthetic request');await save();await page.getByLabel('关闭检查器',{exact:true}).click();await (await launchButton()).click();await complete();assert.equal(await page.getByLabel('工作流名称',{exact:true}).isDisabled(),true);await node('Agent A');assert.equal(await page.getByLabel('Agent 最终回答',{exact:true}).innerText(),'synthetic visible answer');assert((await page.getByLabel('实际可见请求',{exact:true}).innerText()).includes('synthetic request'));assert.equal(await page.getByLabel('每轮提示词',{exact:true}).count(),0);const writes=requests.filter(r=>r.path.endsWith('/drafts/save')).length;await page.keyboard.press('Control+s');await page.keyboard.press('Control+z');await sleep(200);assert.equal(requests.filter(r=>r.path.endsWith('/drafts/save')).length,writes,'Run shortcuts never save or undo hidden draft');
 });
 await test('run-and-draft-separate-reopen-does-not-send',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');await page.getByLabel('每轮提示词',{exact:true}).fill('new draft only');const launches=requests.filter(r=>r.path.endsWith('/launch')).length;await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();await complete();await node('Agent A');assert(!(await page.getByLabel('实际可见请求',{exact:true}).innerText()).includes('new draft only'));await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');assert.equal(await page.getByLabel('每轮提示词',{exact:true}).inputValue(),'new draft only');assert.equal(await page.locator('.save-state').innerText(),'未保存');await save();await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();await complete();await page.reload();await complete();assert.equal(requests.filter(r=>r.path.endsWith('/launch')).length,launches);await page.getByRole('button',{name:'编辑草稿',exact:true}).click();
 });
 await test('lost-launch-response-refresh-and-switch-reuses-intent',async()=>{
  let sent,committed;const lose=async route=>{sent=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);committed=await response.json();await route.abort('failed')};await page.route('**/api/v2/launch',lose);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('启动结果未确认'));assert.equal(await page.getByRole('button',{name:'核验本次启动',exact:true}).count(),1);await page.screenshot({path:path.join(out,'launch-committed-response-lost.png')});await page.unroute('**/api/v2/launch',lose);
  await page.getByLabel('新建工作流',{exact:true}).click();await page.getByLabel('工作流名称',{exact:true}).fill('other draft after unknown');await save();await page.reload();await until(async()=>await page.getByLabel('工作流名称',{exact:true}).inputValue()==='other draft after unknown');
  let retried;const retry=async route=>{retried=route.request().postDataJSON();const response=await route.fetch();await route.fulfill({response})};await page.route('**/api/v2/launch',retry);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();assert.deepEqual(retried,sent);assert.equal((await api('runs?workspace='+sent.workspace)).length,2);await page.getByLabel('运行状态',{exact:true}).getByText('运行身份与冻结版本',{exact:true}).click();assert((await page.getByLabel('运行状态',{exact:true}).innerText()).includes(committed.run.id));await page.unroute('**/api/v2/launch',retry);await page.getByRole('button',{name:'编辑草稿',exact:true}).click();assert.equal(await page.getByLabel('工作流名称',{exact:true}).inputValue(),'other draft after unknown');
 });
 await test('launch-before-send-uncertain-never-auto-retries',async()=>{
  await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').last().click();await complete();await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await page.locator('nav[aria-label="工作流列表"] .workflow-link').filter({hasText:'新工作流'}).click();await until(async()=>await page.locator('.agent-card').count()===1);let calls=0;const block=route=>{calls++;return route.abort('failed')};await page.route('**/api/v2/launch',block);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('启动结果未确认'));await sleep(1200);assert.equal(calls,1);await page.unroute('**/api/v2/launch',block);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();
 });
 await test('http-refusal-after-reservation-keeps-original-intent',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();let sent;const refuse=async route=>{sent=route.request().postDataJSON();const response=await route.fetch();assert.equal(response.status(),200);await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'synthetic refusal after durable reservation'})})};await page.route('**/api/v2/launch',refuse);await (await launchButton()).click();await until(async()=>(await page.getByRole('alert').innerText()).includes('HTTP 409'));assert.equal(await page.getByRole('button',{name:'核验本次启动',exact:true}).count(),1);await page.unroute('**/api/v2/launch',refuse);let retry;const observe=r=>{if(r.url().endsWith('/api/v2/launch'))retry=r.postDataJSON()};page.on('request',observe);await page.getByRole('button',{name:'核验本次启动',exact:true}).click();await complete();assert.deepEqual(retry,sent);page.off('request',observe);
 });
 await test('one-inflight-run-read-and-terminal-never-regresses',async()=>{
  let release,entered=0;const held=new Promise(r=>release=r);const delay=async route=>{entered++;const response=await route.fetch();await held;await route.fulfill({response})};await page.route('**/api/v2/run?*',delay);await page.getByLabel('运行历史',{exact:true}).locator('button.workflow-link').first().click();await until(()=>entered===1);await sleep(1200);await page.getByRole('button',{name:'重新读取',exact:true}).click();assert.equal(entered,1,'Polling/manual read share one in-flight request');release();await complete();await sleep(1100);assert.equal(entered,1,'Completed run stops automatic polling');await page.unroute('**/api/v2/run?*',delay);
 });
 await test('actual-truncated-handoff-and-downstream-request',async()=>{
  await page.getByRole('button',{name:'编辑草稿',exact:true}).click();await node('Agent A');await page.getByLabel('Agent 名称',{exact:true}).fill('Long source');await page.getByLabel('关闭检查器',{exact:true}).click();await page.getByRole('button',{name:'添加 Agent',exact:true}).click();await page.getByLabel('Provider',{exact:true}).selectOption('codex');await page.getByLabel('请求模型',{exact:true}).selectOption('fixture-model');await page.getByLabel('请求强度',{exact:true}).selectOption('low');await page.getByLabel('每轮提示词',{exact:true}).fill('use actual upstream');await node('Long source');await page.getByLabel('下游 Agent',{exact:true}).selectOption({label:'Agent B'});await page.getByRole('button',{name:'连接 Agent',exact:true}).click();await page.getByLabel('关闭检查器',{exact:true}).click();await (await launchButton()).click();await complete();await page.locator('.react-flow__edge').click();const content=await page.getByLabel('实际交接内容',{exact:true}).innerText();const input=JSON.parse(content);assert.equal(input.truncated,true);assert.equal(input.budget,16000);assert.equal(input.text.length,16000);await node('Agent B');const request=await page.getByLabel('实际可见请求',{exact:true}).innerText();assert(request.includes('"truncated": true'));assert(request.includes(JSON.stringify(input.text).slice(1,-1)),'Actual visible request contains the exact encoded transferred text');await page.screenshot({path:path.join(out,'downstream-actual-request.png')});for(const viewport of [{width:1440,height:900},{width:1280,height:800},{width:989,height:535}]){await page.setViewportSize(viewport);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.getByRole('button',{name:'编辑草稿',exact:true}).click({trial:true});await page.screenshot({path:path.join(out,'run-layout-'+viewport.width+'.png')})}
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
 assert.equal(errors.length,0);await context.tracing.stop({path:path.join(out,'trace.zip')});await browser.close();const result={category:'SYNTHETIC execution; real HTTP/SQLite/Chromium',complete:true,tests:rows.length,failures:rows.filter(r=>!r.passed).length,rows,errors,requests};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({tests:result.tests,failures:result.failures}));process.exitCode=result.failures?1:0;
})().catch(async e=>{fs.writeFileSync(path.join(out,'fatal.txt'),e.stack);await context?.tracing.stop({path:path.join(out,'trace-failure.zip')}).catch(()=>{});await browser?.close();console.error(e.message);process.exitCode=1});
