/* Real HTTP/Chromium with delayed real responses. Snapshots are synthetic fixtures. */
const fs=require('fs'),assert=require('assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.argv[2],access=JSON.parse(fs.readFileSync(out+'/access.json'));
const results=[];let browser;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await delay(40)}throw Error('Condition did not become true');}
function writeResults(current=null,complete=false){fs.writeFileSync(out+'/workbench-results.json',JSON.stringify({evidence_type:'synthetic native data, ordinary Chromium/real HTTP',tests:results.length,failures:results.filter(r=>!r.passed).length,skipped:0,current_case:current,complete,results},null,2));}
async function test(name,fn){
 if(process.env.TEST_FILTER&&!name.includes(process.env.TEST_FILTER))return;
 const context=await browser.newContext({viewport:{width:952,height:756}}),page=await context.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(String(e)));const started=Date.now();let timer;
 writeResults(name);console.log(JSON.stringify({case:name,state:'started'}));
 try{
  await Promise.race([(async()=>{await page.addInitScript(()=>localStorage.setItem('devflower-terminal-auto-open','false'));await page.goto(access.url+'/#'+access.token);await page.getByRole('button',{name:'打开文件夹',exact:true}).waitFor();await fn(page);assert.deepEqual(errors,[])})(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Browser case exceeded 45 seconds: '+name)),45000)})]);
  results.push({name,passed:true,milliseconds:Date.now()-started});
 }catch(e){
  results.push({name,passed:false,error:e.stack||String(e),milliseconds:Date.now()-started});
  writeResults(name);
  try{await page.screenshot({path:out+'/'+name+'-failure.png',timeout:3000})}catch(shot){results.at(-1).screenshot_error=String(shot)}
 }finally{
  clearTimeout(timer);await page.unrouteAll({behavior:'ignoreErrors'});writeResults(name);
  let cleanup;try{await Promise.race([context.close(),new Promise((_,reject)=>{cleanup=setTimeout(()=>reject(Error('Browser context did not close: '+name)),5000)})])}catch(e){results.at(-1).passed=false;results.at(-1).cleanup_error=String(e);writeResults(name);throw e}finally{clearTimeout(cleanup)}
  writeResults();console.log(JSON.stringify({case:name,state:results.at(-1).passed?'passed':'failed',milliseconds:Date.now()-started}));
 }
}
async function openWorkspace(p,chooseExample=true){await p.getByRole('button',{name:'打开文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor();await p.getByRole('button',{name:'选择此文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor({state:'hidden'});await until(async()=>!(await p.getByRole('button',{name:'开始运行',exact:true}).isDisabled()));if(chooseExample)await example(p,0);}
async function settleCanvas(p){
 await until(async()=>p.locator('.react-flow__node').evaluateAll(es=>es.length>0&&es.every(e=>getComputedStyle(e).visibility==='visible')));
 await p.evaluate(()=>new Promise((resolve,reject)=>{let last,stable=0,frames=0;function tick(){const style=document.querySelector('.react-flow__viewport')?.getAttribute('style');stable=style===last?stable+1:0;last=style;if(stable>=3)return resolve();if(++frames>90)return reject(Error('Canvas viewport did not settle'));requestAnimationFrame(tick)}requestAnimationFrame(tick)}));
}
async function example(p,index,overview=true){await p.getByRole('button',{name:'示例',exact:true}).click();await p.locator('.example-library .built-in-template').nth(index).click();if(await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();if(overview){await p.locator('.react-flow__node').first().waitFor();await p.getByRole('button',{name:'适应画布',exact:true}).click();await settleCanvas(p);}}
async function displayedCanvasEdges(p,graph){
 const paths=p.locator('.react-flow__edge-path'),nodes=p.locator('.react-flow__node');
 await until(async()=>await paths.count()===graph.edges.length&&await nodes.count()===graph.nodes.length&&await nodes.evaluateAll(es=>es.every(e=>getComputedStyle(e).visibility==='visible')));
 // Compare path/handle coordinates from one frame, including during a fit.
 const geometry=await paths.evaluateAll((es,g)=>es.map(e=>{const length=e.getTotalLength(),matrix=e.getScreenCTM(),start=e.getPointAtLength(0).matrixTransform(matrix),end=e.getPointAtLength(length).matrixTransform(matrix),style=getComputedStyle(e),id=e.closest('[data-id]').dataset.id,edge=g.edges.find(x=>x.id===id),root=e.closest('.react-flow');const handles={};for(const [side,node,type] of [['start',edge.source,'source'],['end',edge.target,'target']]){const h=root.querySelector(`.react-flow__node[data-id="${CSS.escape(node)}"] .react-flow__handle.${type}`),r=h?.getBoundingClientRect();handles[side]=r?{x:r.x,y:r.y,width:r.width,height:r.height}:null}return {id,length,start:{x:start.x,y:start.y},end:{x:end.x,y:end.y},handles,visible:style.visibility==='visible'&&style.display!=='none'&&style.stroke!=='none'}}),graph);
 assert.equal(await p.locator('.react-flow__edge-interaction').count(),graph.edges.length,'Every displayed edge must remain interactive');
 for(const path of geometry){
  assert(path.visible&&Number.isFinite(path.length)&&path.length>1,'Edge must have visible finite geometry: '+path.id);
  const edge=graph.edges.find(e=>e.id===path.id);assert(edge,'Unexpected displayed edge: '+path.id);
  for(const [side,id,type] of [['start',edge.source,'source'],['end',edge.target,'target']]){
   const handle=path.handles[side];assert(handle,'Missing displayed handle: '+id);
   assert(Math.hypot(path[side].x-handle.x-handle.width/2,path[side].y-handle.y-handle.height/2)<12,'Edge endpoint must meet its displayed handle: '+path.id+' '+side);
  }
 }
}
async function nativeSessions(p){
 await openWorkspace(p);await p.getByLabel('会话分组方式',{exact:true}).selectOption('workflow');
 await p.locator('.session-group-title').first().waitFor();
 for(const label of ['dialog-B','dialog-A'])await p.locator('.session-main').filter({hasText:'Synthetic '+label}).click();
 await p.getByRole('button',{name:'原生终端',exact:true}).click();
 await until(async()=>await p.getByRole('button',{name:'打开同一原生会话',exact:true}).isEnabled());
 const mutations=[];
 // These races need real prepare replies, never a native fixture launched as a model.
 await p.route(/\/api\/terminal\/(start|input|resize|close)$/,route=>{mutations.push(route.request().url());return route.abort()});
 return {mutations,tab:label=>p.locator('.editor-tabs').getByRole('button',{name:'Synthetic '+label,exact:true})};
}
async function heldPrepare(p,handle,invalid=false){
 let release,reply,delivered=false;const held=new Promise(r=>release=r);
 const handler=async route=>{
  const data=route.request().postDataJSON();if(data.handle!==handle)return route.fallback();
  // For the error case, ask the real server to reject an unknown identity.
  const response=await route.fetch(invalid?{postData:JSON.stringify({...data,handle:'unknown-native-terminal-handle'})}:{});
  reply={status:response.status(),body:await response.json(),request:data};await held;
  await route.fulfill({response});delivered=true;
 };
 await p.route('**/api/terminal/prepare',handler);
 return {release,seen:async()=>{await until(()=>reply!==undefined);return reply},finish:async()=>{release();await until(()=>delivered)}};
}
(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
await test('canvas-theme-follows-choice-and-live-system-preference',async p=>{
 await openWorkspace(p);const selector=p.getByLabel('外观主题',{exact:true}),flow=p.locator('.react-flow');
 await selector.selectOption('light');await until(async()=>await flow.evaluate(e=>e.classList.contains('light')));assert.equal(await flow.evaluate(e=>e.classList.contains('dark')),false);
 await selector.selectOption('dark');await until(async()=>await flow.evaluate(e=>e.classList.contains('dark')));
 await p.emulateMedia({colorScheme:'light'});await selector.selectOption('system');await until(async()=>await flow.evaluate(e=>e.classList.contains('light')));
 await p.emulateMedia({colorScheme:'dark'});await until(async()=>await flow.evaluate(e=>e.classList.contains('dark')));
});
await test('guide-check-and-send-issues-preserves-assistant-draft',async p=>{
 await openWorkspace(p);const guide=p.getByLabel('工作流新手向导');assert(await guide.isVisible());await p.getByLabel('关闭新手向导').click();assert.equal(await guide.count(),0);await p.getByRole('button',{name:'新手向导',exact:true}).click();assert(await guide.isVisible());await p.getByLabel('关闭新手向导').click();
 await p.getByRole('button',{name:'AI 助手',exact:true}).click();const input=p.getByLabel('配置需求',{exact:true});await input.fill('保留我的草稿');
 await p.getByRole('button',{name:'检查工作流',exact:true}).click();const results=p.getByLabel('工作流检查结果',{exact:true});await results.waitFor();assert((await results.innerText()).includes('本次任务'));await results.getByRole('button',{name:'发给 DevFlower 助手'}).click();await until(async()=>(await input.inputValue()).includes('本次任务'));assert((await input.inputValue()).startsWith('保留我的草稿'));
});
await test('assistant-reopen-current-history-reads-again-without-replay',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');
 await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();
 let posts=0,reads=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))posts++;if(r.method()==='GET'&&r.url().includes('/api/canvas-assistant/conversation?'))reads++});
 await dock.getByLabel('配置需求',{exact:true}).fill('SYNTHETIC current history');await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));
 const previous=reads;await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.locator('.assistant-history-open').filter({hasText:'SYNTHETIC current history'}).click();
 await until(async()=>reads>previous&&(await dock.getByRole('log').innerText()).includes('SYNTHETIC current history'));assert.equal(await dock.getByText('正在读取会话…',{exact:true}).count(),0);assert.equal(posts,1);
 await p.reload();await p.getByRole('button',{name:'AI 助手',exact:true}).click();await until(async()=>(await p.getByTestId('assistant-dock').getByRole('log').innerText()).includes('SYNTHETIC current history'));assert.equal(posts,1);
});
await test('assistant-reopen-running-history-does-not-resend',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();
 let posts=0,reads=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))posts++;if(r.method()==='GET'&&r.url().includes('/api/canvas-assistant/conversation?'))reads++});
 await dock.getByLabel('配置需求',{exact:true}).fill('SYNTHETIC-STREAM-UI');await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>await dock.getByRole('button',{name:'停止本次生成',exact:true}).isVisible());
 const previous=reads;await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.locator('.assistant-history-open').filter({hasText:'SYNTHETIC-STREAM-UI'}).click();await until(async()=>reads>previous&&(await dock.getByRole('log').innerText()).includes('SYNTHETIC-STREAM-UI'));
 assert.equal(await dock.getByText('正在读取会话…',{exact:true}).count(),0);assert.equal(posts,1);
});
await test('assistant-late-history-read-cannot-replace-newer-selection',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();
 let posts=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))posts++});
 for(const text of ['SYNTHETIC history A','SYNTHETIC history B']){if(text.endsWith('B'))await dock.getByLabel('新建助手对话',{exact:true}).click();await dock.getByLabel('配置需求',{exact:true}).fill(text);await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));}
 await dock.getByLabel('助手会话历史',{exact:true}).click();let release,arrived,delivered;const hold=new Promise(r=>release=r),seen=new Promise(r=>arrived=r),done=new Promise(r=>delivered=r);let first=true;
 await p.route('**/api/canvas-assistant/conversation?*',async route=>{if(!first)return route.continue();first=false;const response=await route.fetch();arrived();await hold;await route.fulfill({response});delivered()});
 await dock.locator('.assistant-history-open').filter({hasText:'SYNTHETIC history A'}).click();await seen;await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.locator('.assistant-history-open').filter({hasText:'SYNTHETIC history B'}).click();
 await until(async()=>(await dock.getByRole('log').innerText()).includes('SYNTHETIC history B'));release();await done;await p.unroute('**/api/canvas-assistant/conversation?*');assert((await dock.getByRole('log').innerText()).includes('SYNTHETIC history B'));assert.equal(posts,2);
});
await test('inspection-late-response-is-not-attached-to-new-task',async p=>{
 await openWorkspace(p);await p.getByLabel('本次任务',{exact:true}).fill('旧检查任务');
 let release,arrived;const hold=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);
 await p.route('**/api/workflow/check',async route=>{const response=await route.fetch();arrived();await hold;await route.fulfill({response})});
 await p.getByRole('button',{name:'检查工作流',exact:true}).click();await seen;
 await p.getByLabel('本次任务',{exact:true}).fill('新检查任务');release();
 await until(async()=>(await p.locator('.alert').allTextContents()).some(v=>v.includes('检查期间草稿或任务已变化')));
 assert.equal(await p.getByLabel('工作流检查结果',{exact:true}).count(),0);
});
await test('inspection-truncated-real-http-displays-counts',async p=>{
 await openWorkspace(p);await p.getByLabel('本次任务',{exact:true}).fill('检查截断');
 // Exercise the real checker with a large synthetic graph, then render its actual response.
 await p.route('**/api/workflow/check',async route=>{
  const body=route.request().postDataJSON();body.graph={version:3,name:'repro',nodes:Array.from({length:64},(_,i)=>({id:`bad${i}`,name:`bad${i}`,kind:'agent'})),edges:[]};
  const response=await route.fetch({postData:JSON.stringify(body)});await route.fulfill({response});
 });
 await p.getByRole('button',{name:'检查工作流',exact:true}).click();const results=p.getByLabel('工作流检查结果',{exact:true});await results.waitFor();
 const text=await results.innerText();assert(text.includes('449 个错误'));assert(text.includes('2 条提醒'));assert(text.includes('遗漏 195 条'));assert(text.includes('分批修复'));
 assert.equal(await results.getByRole('button',{name:'查看全部问题'}).count(),0);await results.getByRole('button',{name:'查看已列问题'}).click();
});
await test('inspection-handoff-sends-versioned-issues-over-real-http',async p=>{
 await openWorkspace(p);await p.getByLabel('本次任务',{exact:true}).fill('检查身份测试');
 await p.getByRole('button',{name:'检查工作流',exact:true}).click();const results=p.getByLabel('工作流检查结果',{exact:true});await results.waitFor();
 await results.getByRole('button',{name:'发给 DevFlower 助手'}).click();const panel=p.getByTestId('assistant-dock');
 await panel.getByLabel('助手模型',{exact:true}).selectOption('auto');await panel.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await panel.getByRole('checkbox',{name:/允许发送.*对话/}).check();
 let sent; p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))sent=r.postDataJSON()});
 await panel.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>Boolean(sent));
 assert.equal(sent.inspection.workspace,access.workspace);assert.equal(sent.inspection.task,'检查身份测试');assert.deepEqual(sent.inspection.graph,sent.graph);assert(sent.inspection.issues.length>0);
 await until(async()=>(await panel.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));
 assert(await panel.getByText('检查身份与结构位置（高级）',{exact:true}).isVisible());
});
await test('workflow-check-locates-nested-prompt-and-expires-after-edit',async p=>{
 await openWorkspace(p);await example(p,2);await p.getByLabel('本次任务',{exact:true}).fill('Synthetic nested validation');
 await p.locator('.react-flow__node[data-id="cycle"]').getByRole('button',{name:'编辑循环内流程'}).click();await p.locator('.react-flow__node[data-id="proposal"]').click();await p.getByRole('button',{name:'提示词',exact:true}).click();await p.getByLabel('每轮提示词',{exact:true}).fill('');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();await p.getByRole('button',{name:'返回外层'}).click();
 await p.getByRole('button',{name:'检查工作流',exact:true}).click();const results=p.getByLabel('工作流检查结果',{exact:true});await results.waitFor();if(await results.getByRole('button',{name:'查看全部问题'}).count())await results.getByRole('button',{name:'查看全部问题'}).click();
 const problem=results.getByRole('button',{name:/提出或修订方案.*每轮提示词/});await problem.waitFor();await problem.click();assert((await p.locator('.loop-breadcrumb').innerText()).includes('cycle'));assert.equal(await p.locator('.react-flow__node[data-id="proposal"].selected').count(),1);assert.equal(await p.getByLabel('每轮提示词',{exact:true}).inputValue(),'');await until(async()=>await p.evaluate(()=>document.activeElement?.getAttribute('aria-label'))==='每轮提示词');
 const checked=await p.evaluate(()=>JSON.stringify(JSON.parse(localStorage.getItem('devflower-workbench-view')).graph));await p.getByLabel('每轮提示词',{exact:true}).fill('修复提示词');assert((await results.innerText()).includes('草稿或任务已变化'));assert(await results.getByRole('button',{name:'发给 DevFlower 助手'}).isDisabled());
 await p.getByRole('button',{name:'撤销',exact:true}).click();await until(async()=>await p.evaluate(()=>JSON.stringify(JSON.parse(localStorage.getItem('devflower-workbench-view')).graph))===checked);assert((await results.innerText()).includes('草稿或任务已变化'));await p.getByRole('button',{name:'检查工作流',exact:true}).click();await until(async()=>!(await results.innerText()).includes('草稿或任务已变化'));
});
await test('direct-click-workflow-start-no-probe-or-written-answer',async p=>{
 await openWorkspace(p);await p.getByLabel('本次任务',{exact:true}).fill('Synthetic direct browser task');
 const calls=[];p.on('request',r=>{if(/\/api\/(model-admission|start)/.test(r.url()))calls.push(r.url())});
 await p.route('**/api/run?**',route=>route.fulfill({json:{run:{id:'DIRECT-SYNTHETIC',task:'Synthetic direct browser task',workspace:access.workspace,graph:{name:'synthetic',nodes:[],edges:[]}},state:'QUEUED',nodes:[],events:[]}}));
 await p.route('**/api/start',async route=>{const body=route.request().postDataJSON();assert.deepEqual(body.answer,{action:'start'});return route.fulfill({json:{run:{id:'DIRECT-SYNTHETIC',task:'Synthetic direct browser task',workspace:access.workspace,graph:{name:'synthetic',nodes:[],edges:[]}},state:'QUEUED',nodes:[],events:[]}})});
 await p.getByRole('button',{name:'开始运行',exact:true}).click();await until(()=>Promise.resolve(calls.some(x=>x.endsWith('/api/start'))));
 assert.equal(calls.filter(x=>x.endsWith('/api/start')).length,1);assert(!calls.some(x=>x.includes('/api/model-admission/')));
 assert.equal(await p.getByLabel('本次授权确认意见').count(),0);
});
await test('directory-cancel-and-origin-auth',async p=>{assert(!p.url().includes('#'));assert(await p.locator('.brand').getByText('DEVELOPMENT',{exact:true}).isVisible(),'Development identity must remain visible at ordinary viewport widths');const denied=await p.request.get(access.url+'/api/status');assert.equal(denied.status(),403);await p.getByRole('button',{name:'打开文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor();await p.getByRole('button',{name:'取消',exact:true}).click();assert(await p.getByText('尚未打开工作区',{exact:false}).isVisible());await openWorkspace(p);assert((await p.locator('.workspace-name').innerText()).includes(access.path));await p.reload();await until(async()=>(await p.locator('.workspace-name').innerText()).includes(access.path));assert.equal((await p.context().cookies()).filter(c=>c.name==='devflower_workbench').length,0);});
await test('node-panel-prompts-drag-and-save-reopen',async p=>{await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByLabel('模块名称',{exact:true}).fill('浏览器配置节点');await p.getByLabel('工作方式',{exact:true}).selectOption('Plan');await p.getByRole('button',{name:'提示词',exact:true}).click();await p.getByLabel('本次运行初始化提示词',{exact:true}).fill('INIT-UI');await p.getByLabel('每轮提示词',{exact:true}).fill('ROUND-UI');await p.getByLabel('输出与交接要求',{exact:true}).fill('OUTPUT-UI');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();const node=p.locator('.react-flow__node[data-id="analyze"]'),before=await node.boundingBox();await p.mouse.move(before.x+40,before.y+20);await p.mouse.down();await p.mouse.move(before.x+90,before.y+60,{steps:8});await p.mouse.up();assert.equal(await p.locator('.inspector').count(),0);await p.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));await p.reload();await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'提示词',exact:true}).click();assert.equal(await p.getByLabel('本次运行初始化提示词',{exact:true}).inputValue(),'INIT-UI');assert.equal(await p.getByLabel('每轮提示词',{exact:true}).inputValue(),'ROUND-UI');await p.screenshot({path:out+'/configured-canvas.png'});});
await test('delayed-save-cannot-adopt-new-document',async p=>{await openWorkspace(p);await p.getByLabel('工作流名称',{exact:true}).fill('文档 A');let release,arrived;const held=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);let first=true;const sent=[];p.on('request',r=>{if(r.url().endsWith('/api/draft'))sent.push(r.postDataJSON());});await p.route('**/api/draft',async route=>{const response=await route.fetch();if(first){first=false;arrived();await held;}await route.fulfill({response});});await p.getByRole('button',{name:'保存',exact:true}).click();await seen;await example(p,1);await p.getByLabel('工作流名称',{exact:true}).fill('文档 B');release();await until(async()=>await p.getByRole('button',{name:'保存',exact:true}).isEnabled());await p.getByRole('button',{name:'保存',exact:true}).click();await until(()=>Promise.resolve(sent.length===2));await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));assert(sent[1].id==null,'New document must omit the old save identity');assert.equal(sent[1].graph.name,'文档 B');assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'文档 B');});
await test('reordered-run-and-network-failure-are-honest',async p=>{await openWorkspace(p);let release,arrived;const hold=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);let first=true;await p.route('**/api/run?**',async route=>{const response=await route.fetch();if(first&&route.request().url().includes('run=A')){first=false;arrived();await hold;}await route.fulfill({response});});await p.getByLabel('历史运行',{exact:true}).selectOption('A');await seen;await p.getByLabel('历史运行',{exact:true}).selectOption('B');await until(async()=>await p.locator('.react-flow__node[data-id="analyze"]').getByText('分析 B',{exact:true}).count()===1);release();await delay(180);assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'运行 B');assert.equal(await p.getByLabel('本次任务',{exact:true}).inputValue(),'任务 B');await p.unroute('**/api/run?**');await p.route('**/api/run?**',route=>route.abort());await until(async()=>(await p.locator('.activity-tabs').innerText()).includes('状态未确认'));assert(!(await p.locator('.activity-tabs').innerText()).includes('已完成'));});
await test('loop-permission-preview-includes-nested-agents',async p=>{
 await openWorkspace(p);await example(p,2);await p.getByLabel('本次任务',{exact:true}).fill('Synthetic nested scope test');
 let prepared=null,starts=0,probes=0;
 p.on('request',r=>{if(r.url().includes('/api/model-admission/'))probes++});
 await p.route('**/api/prepare',async route=>{const response=await route.fetch(),body=await response.json();assert.equal(response.status(),200,JSON.stringify(body));prepared=body.data;await route.fulfill({response,json:body})});
 await p.route('**/api/start',route=>{starts++;assert.deepEqual(route.request().postDataJSON().answer,{action:'start'});return route.fulfill({status:400,json:{error:'Synthetic start rejected; no model call'}})});
 await p.getByRole('button',{name:'开始运行',exact:true}).click();await until(()=>starts===1);assert(prepared);assert.equal(prepared.interaction,'click');assert.deepEqual(prepared.admission_checks,[]);
 assert.equal(prepared.graph.budget.calls,24);assert.equal(prepared.graph.budget.seconds,3600);
 const nodes=Object.entries(prepared.bindings);assert.equal(nodes.length,2);
 for(const [path,selected] of nodes){assert(path.startsWith('cycle/'));assert.equal(selected.binding.execution_mode,'Ask');assert.equal(selected.binding.role,'reviewer');assert.equal(selected.binding.effort,'high')}
 await delay(150);assert.equal(starts,1);assert.equal(probes,0);assert.equal(await p.getByLabel('本次授权确认意见').count(),0);
});
await test('frozen-budget-does-not-display-or-edit-draft',async p=>{await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();await p.getByLabel('运行调用预算',{exact:true}).fill('24');await p.getByLabel('运行时间预算（秒）',{exact:true}).fill('1200');await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();assert.equal(await p.getByLabel('运行调用预算',{exact:true}).inputValue(),'10');assert.equal(await p.getByLabel('运行时间预算（秒）',{exact:true}).inputValue(),'600');assert(await p.getByLabel('运行调用预算',{exact:true}).isDisabled());assert(await p.getByLabel('运行时间预算（秒）',{exact:true}).isDisabled());await p.getByLabel('历史运行',{exact:true}).selectOption('B');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 B');await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();assert.equal(await p.getByLabel('运行调用预算',{exact:true}).inputValue(),'12');await p.getByRole('button',{name:'编辑草稿',exact:true}).click();await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();assert.equal(await p.getByLabel('运行调用预算',{exact:true}).inputValue(),'24');assert.equal(await p.getByLabel('运行时间预算（秒）',{exact:true}).inputValue(),'1200');assert(await p.getByLabel('运行调用预算',{exact:true}).isEnabled());});
await test('slow-poll-eventually-displays-real-snapshot',async p=>{await openWorkspace(p);let responses=0;await p.route('**/api/run?**',async route=>{const response=await route.fetch();await delay(1400);responses++;await route.fulfill({response});});await p.getByLabel('历史运行',{exact:true}).selectOption('A');await delay(4600);const title=await p.getByLabel('工作流名称',{exact:true}).inputValue();assert(responses>=2,'Actual HTTP replies must have returned');assert.equal(title,'运行 A','Slow valid responses must eventually update the selected run');});
await test('hung-snapshot-becomes-unknown-without-restarting-run',async p=>{await openWorkspace(p);let release;const hold=new Promise(r=>release=r);let held=0;await p.route('**/api/run?**',async route=>{const response=await route.fetch();held++;await hold;await route.fulfill({response});});try{await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>held===1);await delay(3000);assert.equal(held,1,'An outstanding snapshot must not overlap before its deadline');await delay(2600);assert((await p.locator('.activity-tabs').innerText()).includes('状态未确认'));assert(held<=2,'Only a timed-out read may be retried');}finally{release();}});
await test('canvas-edges-survive-measurement-race-and-view-switches',async p=>{
 const graph=()=>p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph);
 await displayedCanvasEdges(p,await graph());await openWorkspace(p);await displayedCanvasEdges(p,await graph());
 // Deliver an actual native RO batch in an asynchronous save's DOM commit,
 // before pending passive effects. No fabricated sizes or dependency patches.
 await p.context().addInitScript(()=>{
  const schedule=window.canvasMeasurementSchedule={pending:null,armed:false,finished:false};
  const Native=ResizeObserver;
  window.ResizeObserver=class extends Native{
   constructor(callback){super((entries,observer)=>{if(!schedule.finished&&entries.some(e=>e.target.classList.contains('react-flow__node'))){schedule.pending={entries,observer,callback};return;}callback(entries,observer)});}
   disconnect(){if(schedule.pending?.observer===this)schedule.pending=null;return super.disconnect()}
  };
  new MutationObserver(()=>{if(schedule.pending&&schedule.armed&&document.querySelector('.notice')?.textContent.includes('当前改动已保存')){const batch=schedule.pending;schedule.pending=null;schedule.finished=true;batch.callback(batch.entries,batch.observer)}}).observe(document,{childList:true,subtree:true,characterData:true});
 });
 await p.reload();await until(async()=>await p.evaluate(()=>!!window.canvasMeasurementSchedule?.pending));
 const initial=await graph();let release,arrived;const held=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);
 await p.route('**/api/draft',async route=>{const response=await route.fetch();arrived();await held;await route.fulfill({response})});
 try{
  await p.getByRole('button',{name:'保存',exact:true}).click();await seen;await p.evaluate(()=>window.canvasMeasurementSchedule.armed=true);release();
  await until(async()=>await p.evaluate(()=>window.canvasMeasurementSchedule.finished));await displayedCanvasEdges(p,initial);
 }finally{release();await p.unroute('**/api/draft')}
 for(let i=0;i<3;i++){await p.getByLabel('本次任务',{exact:true}).fill('Edge geometry update '+i);await displayedCanvasEdges(p,initial)}
 await p.getByRole('checkbox',{name:'减少动态效果'}).check();await displayedCanvasEdges(p,initial);
 for(const id of ['A','B']){
  const response=await p.request.get(access.url+'/api/run?run='+id+'&after=0',{headers:{Authorization:'Bearer '+access.token}});assert.equal(response.status(),200);const frozen=(await response.json()).run.graph;
  await p.getByLabel('历史运行',{exact:true}).selectOption(id);await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()===frozen.name);await displayedCanvasEdges(p,frozen);
 }
 await p.getByRole('button',{name:'编辑草稿',exact:true}).click();await displayedCanvasEdges(p,initial);assert.deepEqual(await graph(),initial,'Measurements and run switches must preserve the canonical draft');
 await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByRole('button',{name:'工作流画布',exact:true}).click();await displayedCanvasEdges(p,initial);
 await example(p,2);const nested=await graph();await displayedCanvasEdges(p,nested);
 await p.getByRole('button',{name:'编辑循环内流程',exact:true}).click();await displayedCanvasEdges(p,nested.nodes.find(n=>n.kind==='loop').body);
 await p.getByRole('button',{name:'返回外层',exact:true}).click();await displayedCanvasEdges(p,nested);
 await example(p,0);await displayedCanvasEdges(p,await graph());
});
await test('canvas-nodes-stay-visible-during-unrelated-edits',async p=>{
 await openWorkspace(p);
 const nodes=p.locator('.react-flow__node');await until(async()=>await nodes.count()>0&&(await nodes.evaluateAll(es=>es.every(e=>getComputedStyle(e).visibility==='visible'))));
 await p.evaluate(()=>{const visible=[...document.querySelectorAll('.react-flow__node')];window.canvasVisibilityLoss=[];window.canvasVisibilityObserver=new MutationObserver(records=>{for(const m of records){if(m.attributeName==='style'&&(m.oldValue?.includes('visibility: hidden')||m.target.style.visibility==='hidden'))window.canvasVisibilityLoss.push(m.target.dataset.id);}});for(const e of visible)window.canvasVisibilityObserver.observe(e,{attributes:true,attributeFilter:['style'],attributeOldValue:true});});
 for(let i=0;i<5;i++){await p.getByLabel('本次任务',{exact:true}).fill('Ordinary task edit '+i);await delay(90);}
 assert.deepEqual(await p.evaluate(()=>window.canvasVisibilityLoss),[],'Visible canvas modules must retain their measured size during unrelated task editing');
 assert(await nodes.evaluateAll(es=>es.every(e=>getComputedStyle(e).visibility==='visible')),'Every module must remain available to click and drag');
 await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByLabel('模块名称',{exact:true}).waitFor();
 await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 const node=p.locator('.react-flow__node[data-id="analyze"]'),before=await node.boundingBox();
 await p.mouse.move(before.x+40,before.y+20);await p.mouse.down();await p.mouse.move(before.x+90,before.y+60,{steps:8});await p.mouse.up();
 await until(async()=>{const after=await node.boundingBox();return after&&Math.hypot(after.x-before.x,after.y-before.y)>10});
 assert.deepEqual(await p.evaluate(()=>window.canvasVisibilityLoss),[],'Clicking and dragging must also preserve measured dimensions');
 await p.evaluate(()=>window.canvasVisibilityObserver.disconnect());
});
await test('canvas-measurements-stay-out-of-drafts-and-isolate-views',async p=>{
 await openWorkspace(p);
 const visible=async()=>until(async()=>await p.locator('.react-flow__node').count()>0&&await p.locator('.react-flow__node').evaluateAll(es=>es.every(e=>getComputedStyle(e).visibility==='visible')));
 const graphOnly=async()=>p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph);
 const cleanGraph=g=>{for(const n of g.nodes){for(const key of ['measured','width','height','dimensions'])assert(!(key in n),'UI measurement leaked into canonical node: '+key);if(n.body)cleanGraph(n.body)}};
 await visible();let savedRequest;
 p.on('request',r=>{if(r.url().endsWith('/api/draft'))savedRequest=r.postDataJSON()});
 await p.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));await visible();
 const initial=await graphOnly();cleanGraph(initial);cleanGraph(savedRequest.graph);
 assert.equal(await p.locator('.dirty').innerText(),'已保存');
 for(const id of ['A','B']){
  await p.getByLabel('历史运行',{exact:true}).selectOption(id);await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 '+id);await visible();
  await p.getByRole('checkbox',{name:'减少动态效果'}).check();await p.getByRole('checkbox',{name:'减少动态效果'}).uncheck();await visible();
  assert.equal(await p.locator('.dirty').innerText(),'已保存');assert.deepEqual(await graphOnly(),initial);
  assert((await p.locator('.activity-tabs').innerText()).includes('已完成'),'UI measurement must not change runtime status');
 }
 await p.getByRole('button',{name:'编辑草稿',exact:true}).click();await visible();assert.deepEqual(await graphOnly(),initial);
 await example(p,2);await visible();const loop=p.locator('.react-flow__node').filter({has:p.getByRole('button',{name:'编辑循环内流程',exact:true})});
 await loop.getByRole('button',{name:'编辑循环内流程',exact:true}).click();await visible();
 const nested=await graphOnly();cleanGraph(nested);
 await p.getByLabel('本次任务',{exact:true}).fill('Nested canvas measurement check');await visible();assert.deepEqual(await graphOnly(),nested);
 await p.getByRole('button',{name:'返回外层',exact:true}).click();await visible();
 // A new document reuses the example IDs but must mount a fresh canvas.
 await example(p,0);await visible();await p.getByLabel('本次任务',{exact:true}).fill('Replacement document measurement check');await visible();cleanGraph(await graphOnly());
});
await test('structured-dialog-visible-reload-switch-and-no-duplicates',async p=>{
 await openWorkspace(p);
 const open=async label=>{await p.locator('.session-main').filter({hasText:'Synthetic '+label}).click();await until(async()=>await p.locator('.conversation-toolbar>strong').innerText()==='Synthetic '+label);};
 const answers=p.locator('.execution-result');
 const check=async label=>{await until(async()=>await answers.count()===2&&(await answers.first().innerText()).includes('SYNTHETIC '+label+' final answer 1'));assert((await answers.first().innerText()).includes('工作流回答'));assert(!(await answers.first().innerText()).includes(label+'-run'),'Internal run identity stays in advanced view');const text=await answers.allTextContents();assert(text[0].includes('结构化执行结果'));assert(text[0].includes('loop/analyze'));assert(text[0].includes('1 / 1'));assert(text[1].includes('1 / 2'));assert(text[0].includes(label+'-run'));assert(!text.join('').includes('HIDDEN_RESULT_REASONING'));assert.equal(await p.locator('.message.assistant').filter({hasText:'final answer'}).count(),0);};
 await open('dialog-A');await check('dialog-A');
 assert.equal(await p.locator('.request-group[open]').count(),0,'Advanced payloads stay folded');
 await p.getByRole('button',{name:'刷新历史',exact:true}).click();await check('dialog-A');
 await p.reload();await check('dialog-A');assert.equal(await p.locator('.conversation-toolbar>strong').innerText(),'Synthetic dialog-A');
 await open('dialog-B');await check('dialog-B');await open('dialog-A');await check('dialog-A');
 await p.getByRole('button',{name:'刷新历史',exact:true}).click();await check('dialog-A');
 await p.screenshot({path:out+'/structured-dialog.png'});
 await open('dialog-native');await until(async()=>await p.locator('.message.assistant').filter({hasText:'SYNTHETIC dialog-native final answer'}).count()===2);assert.equal(await answers.count(),0);
 await open('dialog-failed');await until(async()=>await p.locator('.turn-status').count()===2);assert.equal(await answers.count(),0);assert(!(await p.locator('.conversation-timeline').innerText()).includes('No response requested.'));await p.getByText('高级：原生消息记录',{exact:true}).click();assert.equal(await p.locator('.native-raw-message').filter({hasText:'No response requested.'}).count(),2);
});
await test('delayed-session-history-isolated-and-http-search-truthful',async p=>{
 await openWorkspace(p);let release,arrived;const hold=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);let first=true;
 await p.route('**/api/history?**',async route=>{const response=await route.fetch();if(first&&route.request().url().includes(access.sessions['dialog-A'].handle)){first=false;arrived();await hold;}await route.fulfill({response});});
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();await seen;
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-B'}).click();await until(async()=>(await p.locator('.execution-result').allTextContents()).join('').includes('SYNTHETIC dialog-B final answer 2'));release();await delay(150);
 const text=await p.locator('.execution-result').allTextContents();assert.equal(text.length,2);assert(!text.join('').includes('dialog-A'));
 const response=await p.request.get(access.url+'/api/history?'+new URLSearchParams({workspace:access.workspace,handle:access.sessions['dialog-B'].handle,query:'final answer 2'}),{headers:{Authorization:'Bearer '+access.token}});assert.equal(response.status(),200);const history=await response.json();assert.deepEqual(history.execution_results.map(r=>r.text),['SYNTHETIC dialog-B final answer 2']);assert.equal(history.messages.length,0);
});
await test('non-loop-results-use-first-human-round',async p=>{
 await openWorkspace(p);
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-plain'}).click();
 const rows=p.locator('.execution-result');await until(async()=>await rows.count()===1);
 const text=await rows.first().innerText();
 assert(/第\s+1\s+轮/.test(text),'Non-loop completed results must show human round 1, matching the actual-input group');
 assert(!/第\s+0\s+轮/.test(text),'Internal zero must not be shown as a human round');
 assert((await p.locator('.request-group').first().innerText()).includes('第 1 轮'));
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();
 await until(async()=>await rows.count()===2);
 assert(/第\s+1\s*\/\s*1\s+轮/.test(await rows.first().innerText()),'Nested loop path must retain its actual 1 / 1 round');
});
await test('product-editor-copy-delete-undo-and-multiselect',async p=>{
 await openWorkspace(p);const graph=()=>p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph);
 const initial=await graph();await p.locator('.react-flow__pane').click({position:{x:25,y:25}});
 await p.keyboard.press('Control+a');await until(async()=>!(await p.getByRole('button',{name:'复制选中节点',exact:true}).isDisabled()));
 await p.getByRole('button',{name:'复制选中节点',exact:true}).click();await p.getByRole('button',{name:'粘贴节点',exact:true}).click();
 await until(async()=>(await graph()).nodes.length===initial.nodes.length*2-1);
 const pasted=await graph();assert.equal(pasted.nodes.filter(n=>n.kind==='start').length,1);
 for(const n of pasted.nodes.filter(n=>!initial.nodes.some(x=>x.id===n.id)&&n.kind==='agent'))assert.equal(n.session,'new');
 await p.getByRole('button',{name:'删除选中节点',exact:true}).click();await until(async()=>(await graph()).nodes.length===initial.nodes.length);
 await p.getByRole('button',{name:'撤销',exact:true}).click();await until(async()=>(await graph()).nodes.length===pasted.nodes.length);
 await p.getByRole('button',{name:'重做',exact:true}).click();await until(async()=>(await graph()).nodes.length===initial.nodes.length);
 await p.getByRole('button',{name:'撤销',exact:true}).click();await p.keyboard.press('Control+a');await p.getByRole('button',{name:'对齐与分布',exact:true}).click();await p.getByRole('menuitem',{name:'顶部对齐',exact:true}).click();
 assert.equal(new Set((await graph()).nodes.map(n=>n.position.y)).size,1);
 await p.getByRole('button',{name:'缩略导航',exact:true}).click();assert.equal(await p.locator('.react-flow__minimap').count(),0);
 await p.getByRole('button',{name:'缩略导航',exact:true}).click();assert.equal(await p.locator('.react-flow__minimap').count(),1);
 await p.screenshot({path:out+'/product-editor.png'});
});
await test('product-edge-label-insertion-and-frozen-configuration',async p=>{
 await openWorkspace(p);const graph=()=>p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph);const initial=await graph();
 await p.locator('.react-flow__edge-interaction').first().click({force:true});await p.getByLabel('连线标签',{exact:true}).fill('显式交接');
 await p.getByLabel('传递内容',{exact:true}).selectOption('field');await p.getByLabel('字段',{exact:true}).fill('result.score');
 await p.getByLabel('条件',{exact:true}).selectOption('true');await p.getByLabel('上下文字符预算',{exact:true}).fill('2048');
 await until(async()=>{const e=(await graph()).edges[0];return e.label==='显式交接'&&e.transfer==='field'&&e.field==='result.score'&&e.when==='true'&&e.max_chars===2048});
 const beforeInsert=await graph(),original=beforeInsert.edges[0];
 await p.getByRole('button',{name:'在连线上插入 Agent',exact:true}).click();await until(async()=>(await graph()).nodes.length===initial.nodes.length+1);
 const inserted=await graph();assert.equal(inserted.edges.length,beforeInsert.edges.length+1);
 const addedNodes=inserted.nodes.filter(n=>!beforeInsert.nodes.some(old=>old.id===n.id));assert.equal(addedNodes.length,1);
 const agent=addedNodes[0];assert.equal(agent.kind,'agent');assert.equal(agent.session,'new');assert(!agent.context_from);assert(!agent.session_id);
 const addedEdges=inserted.edges.filter(e=>!beforeInsert.edges.some(old=>old.id===e.id));assert.equal(addedEdges.length,1);
 assert.deepEqual(addedEdges[0],{id:addedEdges[0].id,source:agent.id,target:original.target,transfer:'result',when:'always'});
 const expected=JSON.parse(JSON.stringify(beforeInsert));expected.nodes.push(agent);expected.edges.find(e=>e.id===original.id).target=agent.id;expected.edges.push(addedEdges[0]);
 assert.deepEqual(inserted,expected,'Insertion must preserve the rest of the graph, including the original edge label and handoff configuration');
 await p.getByRole('button',{name:'撤销',exact:true}).click();await until(async()=>(await graph()).nodes.length===beforeInsert.nodes.length);
 assert.deepEqual(await graph(),beforeInsert,'One Undo must restore the complete original graph without an isolated Agent');
 assert.equal(await p.locator(`.react-flow__node[data-id="${agent.id}"]`).count(),0);
 await p.getByRole('button',{name:'重做',exact:true}).click();await until(async()=>(await graph()).nodes.length===inserted.nodes.length);
 assert.deepEqual(await graph(),inserted,'One Redo must restore the same Agent identity, topology and handoff configuration');
 await p.locator(`.react-flow__node[data-id="${agent.id}"]`).waitFor();
 const saved=p.waitForResponse(r=>r.url()===access.url+'/api/draft'&&r.request().method()==='POST');
 await p.getByRole('button',{name:'保存',exact:true}).click();const response=await saved;assert(response.ok());
 assert.deepEqual(response.request().postDataJSON().graph,inserted,'The real HTTP save must contain the complete redone graph');
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');
 await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'基本',exact:true}).click();assert(await p.getByLabel('模块名称',{exact:true}).isDisabled());
 await p.getByRole('button',{name:'提示词',exact:true}).click();assert(await p.getByLabel('每轮提示词',{exact:true}).isDisabled());
});
await test('product-workflow-grouping-and-readable-public-dialog',async p=>{
 await openWorkspace(p);await p.getByLabel('会话分组方式',{exact:true}).selectOption('workflow');
 await until(async()=>(await p.locator('.session-group-title').allTextContents()).length>0);
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();await until(async()=>await p.locator('.execution-result').count()===2);
 assert.equal(await p.locator('.request-group[open]').count(),0);
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'打开同一原生会话',exact:true}).waitFor();
 let starts=0;p.on('request',r=>{if(r.url().endsWith('/api/terminal/start'))starts++});await p.reload();await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'打开同一原生会话',exact:true}).waitFor();assert.equal(starts,0,'Reload must never implicitly start a native writer');
});
for(const invalid of [false,true])await test('native-delayed-prepare-'+(invalid?'error':'success')+'-cannot-cross-session',async p=>{
 const {mutations,tab}=await nativeSessions(p),a=await heldPrepare(p,access.sessions['dialog-A'].handle,invalid),b=await heldPrepare(p,access.sessions['dialog-B'].handle);
 const open=p.getByRole('button',{name:'打开同一原生会话',exact:true}),prepares=[],starts=[];
 p.on('request',r=>{if(r.url().endsWith('/api/terminal/prepare'))prepares.push(r.postDataJSON());if(r.url().endsWith('/api/terminal/start'))starts.push(r.postDataJSON())});
 try{
  await open.click();const old=await a.seen();assert.equal(old.status,invalid?400:200);if(!invalid)assert.equal(old.body.data.handle,access.sessions['dialog-A'].handle);
  await tab('dialog-B').click();await until(async()=>await open.isEnabled());await open.click();const fresh=await b.seen();assert.equal(fresh.status,200);assert.equal(fresh.body.data.handle,access.sessions['dialog-B'].handle);
  await a.finish();await delay(120);assert.equal(starts.length,0,'Stale A prepare must not start a writer');assert.equal(await p.locator('.native-terminal [role=alert]').count(),0,'Stale A failure must not appear under B');assert(await open.isDisabled());
  await b.finish();await until(()=>starts.length===1);assert.equal(starts[0].id,fresh.body.id);assert.deepEqual(starts[0].answer,{action:'open-terminal'});await delay(150);assert.equal(starts.length,1);
  await tab('dialog-A').click();await tab('dialog-B').click();await delay(200);assert.equal(starts.length,1,'Switching sessions must not replay either request');assert.deepEqual(prepares.map(r=>r.handle),[access.sessions['dialog-A'].handle,access.sessions['dialog-B'].handle]);assert.equal(mutations.length,1);
 }finally{a.release();b.release()}
});
await test('native-unmount-discards-delayed-prepare-for-same-session',async p=>{
 const {mutations,tab}=await nativeSessions(p),a=await heldPrepare(p,access.sessions['dialog-A'].handle);
 const open=p.getByRole('button',{name:'打开同一原生会话',exact:true});let prepares=0;
 p.on('request',r=>{if(r.url().endsWith('/api/terminal/prepare'))prepares++});
 try{
  await open.click();assert.equal((await a.seen()).status,200);
  await p.getByRole('button',{name:'对话视图',exact:true}).click();assert.equal(await p.locator('.native-terminal').count(),0);
  await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await open.isEnabled());
  await a.finish();await delay(120);assert.equal(await p.locator('.terminal-confirm').count(),0);
  await tab('dialog-B').click();await until(async()=>await open.isEnabled());await tab('dialog-A').click();await until(async()=>await open.isEnabled());
  assert.equal(await p.locator('.terminal-confirm').count(),0);assert.equal(prepares,1);assert.deepEqual(mutations,[]);
 }finally{a.release()}
});
await test('recovery-entry-refuses-missing-completion-without-replay',async p=>{
 await openWorkspace(p);let starts=0,afterRejection=false,backgroundReads=0;p.on('request',r=>{if(r.url().endsWith('/api/start'))starts++;if(afterRejection&&r.url().includes('/api/sessions?'))backgroundReads++});
 // A subsequent real HTTP snapshot changes native lifecycle metadata. Its
 // background sidebar refresh must not erase an earlier user-action rejection.
 await p.route('**/api/run?**',async route=>{const response=await route.fetch(),v=await response.json();if(afterRejection&&v.run.id===access.lost_run)for(const n of v.nodes)n.state='FAILED';await route.fulfill({json:v})});
 await p.getByLabel('历史运行',{exact:true}).selectOption(access.lost_run);
 await p.getByRole('button',{name:'核验并恢复',exact:true}).waitFor();const rejected=p.waitForResponse(r=>r.url().endsWith('/api/control')&&r.request().method()==='POST');await p.getByRole('button',{name:'核验并恢复',exact:true}).click();const r=await rejected;assert.equal(r.status(),400);await until(async()=>await p.locator('.alert').count()>0&&(await p.locator('.alert').innerText()).includes('缺少可靠原生完成回执'));afterRejection=true;
 await until(()=>Promise.resolve(backgroundReads>0));await delay(500);assert((await p.locator('.alert').innerText()).includes('缺少可靠原生完成回执'));assert.equal(starts,0);assert((await p.locator('.activity-tabs').innerText()).includes('状态未确认'));
});
await test('existing-chat-keeps-native-profile-and-terminal-hides-second-composer',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-native'}).click();
 await p.getByLabel('此会话执行配置',{exact:true}).waitFor();assert((await p.getByLabel('此会话执行配置',{exact:true}).innerText()).includes('codex'));
 assert.equal(await p.getByLabel('Agent 工具',{exact:true}).count(),0);
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'打开同一原生会话',exact:true}).waitFor();assert.equal(await p.locator('.chat-composer').count(),0);
});
await test('session-refresh-coalesces-identical-pending-requests',async p=>{
 await openWorkspace(p);await p.locator('.session-main').first().waitFor();
 let requests=0,release;const hold=new Promise(r=>release=r),pending=[];
 const handler=route=>{const task=(async()=>{requests++;const response=await route.fetch();await hold;await route.fulfill({response})})();pending.push(task);return task};
 await p.route('**/api/sessions?**',handler);
 try{
  for(let i=0;i<5;i++)await p.getByRole('button',{name:'刷新会话',exact:true}).click();
  await delay(200);assert.equal(requests,1,'One pending metadata read must serve repeated refresh clicks, without filling HTTP connections');
 }finally{release();await Promise.all(pending);await p.unroute('**/api/sessions?**',handler)}
 await p.waitForLoadState('networkidle');
 let fresh=0;p.on('request',r=>{if(r.url().includes('/api/sessions?'))fresh++});
 await p.getByRole('button',{name:'刷新会话',exact:true}).click();await until(()=>Promise.resolve(fresh===1));
});
await test('native-live-output-does-not-poll-full-session-discovery',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();
 const handle=access.sessions['dialog-A'].handle,id='synthetic-existing-pty';let outputReads=0,discoveryReads=0;
 const row={id,workspace:access.workspace,handle,provider:'claude',state:'RUNNING',process_alive:true,input_sequence:0,session_id:access.sessions['dialog-A'].session_id};
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));
 await p.route('**/api/terminal/output?**',route=>{outputReads++;return route.fulfill({json:{...row,output:'',next:0,reset:false}})});
 p.on('request',r=>{if(r.url().includes('/api/sessions?'))discoveryReads++});
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'关闭此终端',exact:true}).waitFor();
 await delay(4400);assert(outputReads>=4,'Actual HTTP output polling must remain live');
 assert.equal(discoveryReads,0,'An unchanged live PTY must not repeatedly rescan all native histories and starve terminal input');
});

await test('ux-overview-truthful-merge-waits-and-round',async p=>{
 await openWorkspace(p);
 await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'){v.state='RUNNING';v.nodes=[{id:'a',run:'A',node:'analyze',name:'分析 A',state:'RUNNING',iteration_path:[2],started_at:Date.now()/1000-8},{id:'m',run:'A',node:'review',name:'汇合等待',state:'PENDING',received:1,expected:2,waiting:['analyze']}];}await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');const overview=p.getByTestId('runtime-overview');await until(async()=>(await overview.innerText()).includes('已收到 1/2'));
 const text=await overview.innerText();assert(text.includes('正在运行'));assert(text.includes('分析 A'));assert(text.includes('第 2 轮'));assert(text.includes('分析 A'));assert(text.includes('冻结快照'));assert(!(await p.locator('.node-card.running').innerText()).includes('undefined'));
 await p.screenshot({path:out+'/ux-merge-overview.png'});
});
await test('ux-network-loss-clears-all-stale-success-and-reconnects-without-start',async p=>{
 await openWorkspace(p);let offline=false,posts=0;
 p.on('request',r=>{if(r.requestMethod==='POST'||r.method()==='POST')if(/\/api\/(start|chat\/start)$/.test(r.url()))posts++});
 await p.route('**/api/run?**',async route=>offline?route.abort():route.fulfill({response:await route.fetch()}));
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('已完成'));
 offline=true;await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('状态待确认'));
 assert.equal(await p.locator('.node-card.completed').count(),0);assert.equal(await p.locator('.node-card.running').count(),0);assert.equal(await p.locator('.handoff-pulse').count(),0);assert((await p.getByTestId('runtime-overview').innerText()).includes('旧状态已隐藏'));
 await p.screenshot({path:out+'/ux-state-unknown.png'});offline=false;await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('流程已结束'));assert.equal(posts,0);
 await p.reload();await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('流程已结束'));assert.equal(await p.getByLabel('历史运行',{exact:true}).inputValue(),'A');assert.equal(posts,0);
});
await test('ux-late-A-response-cannot-overwrite-B-overview',async p=>{
 await openWorkspace(p);let release,seen;const held=new Promise(r=>release=r),arrived=new Promise(r=>seen=r);let once=true;
 await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'&&once){once=false;seen();await held;}await route.fulfill({response:r});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await arrived;await p.getByLabel('历史运行',{exact:true}).selectOption('B');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 B');release();await delay(150);assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'运行 B');assert(!(await p.locator('.node-card').allTextContents()).join('').includes('分析 A'));
});
await test('ux-handoff-pulse-is-new-event-bound-finite-and-reduced-motion',async p=>{
 await openWorkspace(p);let phase=0;
 await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'){v.events=phase?[{seq:phase,at:Date.now()/1000,run:'A',edge:'edge1',source:'analyze',node:'review',kind:'handoff-sent',activation:'A',content:{answer:'本轮真实事件形式的合成证据'},iteration:0,iteration_path:[]}]:[];}await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('已完成'));assert.equal(await p.locator('.handoff-pulse').count(),0);
 // Use the selected graph's real edge identity, not a guessed DOM connection.
 // The overview can commit before React Flow measures and attaches edges.
 // Keep the identity/pulse assertions, and wait for the real edge before sending its event.
 await p.locator('.react-flow__edge[data-id="edge1"]').waitFor({state:'attached'});
 const edges=await p.locator('.react-flow__edge').evaluateAll(es=>es.map(e=>e.dataset.id));assert(edges.includes('edge1'));phase=1;await p.locator('.handoff-pulse[data-event="1"]').waitFor({state:'attached'});
 const pulse=p.locator('.handoff-pulse');assert.equal(await pulse.evaluate(e=>getComputedStyle(e).stroke),'rgb(209, 255, 236)');assert.equal(await pulse.getAttribute('data-run'),'A');await delay(1800);assert.equal(await pulse.evaluate(e=>getComputedStyle(e).opacity),'0');
 await p.getByRole('checkbox',{name:'减少动态效果'}).check();assert.equal(await p.locator('.handoff-pulse').count(),0);
});
await test('ux-session-workflow-node-group-keeps-same-chat-and-filter',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();await until(async()=>await p.locator('.execution-result').count()===2);
 await p.getByLabel('会话分组方式',{exact:true}).selectOption('workflow');assert(await p.locator('.session-node-group .session-node-title[aria-expanded]').count()>0);assert.equal(await p.locator('.conversation-toolbar>strong').innerText(),'Synthetic dialog-A');
 await p.getByRole('button',{name:'正在运行',exact:true}).click();assert.equal(await p.locator('.session-main').count(),0);assert.equal(await p.locator('.conversation-toolbar>strong').innerText(),'Synthetic dialog-A');
 await p.getByRole('button',{name:'全部',exact:true}).click();await p.getByLabel('会话分组方式',{exact:true}).selectOption('provider');assert.equal(await p.locator('.conversation-toolbar>strong').innerText(),'Synthetic dialog-A');assert(await p.locator('.session-main').count()>0);
 await p.screenshot({path:out+'/ux-readable-conversation.png'});
});
await test('ux-preflight-locates-missing-node-without-native-call',async p=>{
 await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'提示词',exact:true}).click();await p.getByLabel('每轮提示词',{exact:true}).fill('');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();let prepare=0;p.on('request',r=>{if(r.url().endsWith('/api/prepare'))prepare++});
 await p.getByRole('button',{name:'开始运行',exact:true}).click();await p.locator('.alert').filter({hasText:'每轮提示词'}).waitFor();assert.equal(await p.getByLabel('每轮提示词',{exact:true}).inputValue(),'');assert((await p.locator('.inspector>header').innerText()).includes('分析任务'));assert.equal(prepare,0);assert(!(await p.locator('.alert').innerText()).includes('Traceback'));
});
await test('ux-failed-and-human-wait-have-explicit-noncolour-labels',async p=>{
 await openWorkspace(p);let state='FAILED';await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'){v.state=state==='FAILED'?'FAILED':'RUNNING';v.run.reason=state==='FAILED'?'缺少已批准执行配置，请返回草稿选择可用工具':'';v.nodes=[{id:'a',run:'A',node:'analyze',state,reason:v.run.reason,name:'分析 A'}];}await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('缺少已批准执行配置'));await until(async()=>await p.locator('.node-card.failed').isVisible()&&(await p.locator('.node-card.failed').innerText()).includes('!'));assert((await p.locator('.node-card.failed').innerText()).includes('!'));state='WAITING_CONFIRMATION';await until(async()=>(await p.getByTestId('runtime-overview').innerText()).includes('等待人工确认'));await until(async()=>await p.locator('.node-card.waiting_confirmation').isVisible()&&(await p.locator('.node-card.waiting_confirmation').innerText()).includes('◇'));assert((await p.locator('.node-card.waiting_confirmation').innerText()).includes('◇'));
});
await test('ux-small-layout-core-actions-and-canvas-editing-visible',async p=>{
 await p.setViewportSize({width:900,height:720});await openWorkspace(p);await displayedCanvasEdges(p,await p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph));
 for(const name of ['开始运行','示例','添加节点'])assert(await p.getByRole('button',{name,exact:true}).isVisible());
 await p.locator('.react-flow__node[data-id="analyze"]').click();assert(await p.getByLabel('模块名称',{exact:true}).isVisible());const panel=await p.locator('.inspector').boundingBox();assert(panel.x>=0&&panel.x+panel.width<=900);await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();await p.screenshot({path:out+'/ux-small-canvas.png'});
});

await test('ux-session-metadata-follows-node-lifecycle-without-periodic-discovery',async p=>{
 await openWorkspace(p);let phase='RUNNING',reads=0;
 await p.route('**/api/sessions?**',async route=>{reads++;const r=await route.fetch(),v=await r.json();v.sessions=v.sessions.map(s=>s.handle===access.sessions['dialog-A'].handle?{...s,display_state:phase,busy:phase==='RUNNING'}:s);await route.fulfill({json:v});});
 await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'){v.state='RUNNING';v.nodes=[{id:'current',run:'A',node:'analyze',state:phase,observed_session:access.sessions['dialog-A'].session_id}]};await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');const card=p.locator('.session-main').filter({hasText:'Synthetic dialog-A'});await until(async()=>(await card.innerText()).includes('正在运行'));const stable=reads;await delay(1900);assert.equal(reads,stable,'Unchanged runtime must not rescan full native histories every poll');
 phase='COMPLETED';await until(async()=>(await card.innerText()).includes('已完成'));assert(reads>stable,'A persisted node lifecycle change must refresh the real session occupancy');
});

await test('ux-run-node-defaults-to-conversation-with-original-request-in-advanced-view',async p=>{
 await openWorkspace(p);await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A')Object.assign(v.nodes[0],{actual_request:'SENT-ONLY-REQUEST',input_sent_sha256:'a'.repeat(64),initialization_included:true,request_sections:[{title:'本次运行初始化',text:'SENT-INITIALIZATION'},{title:'每轮任务',text:'SENT-ROUND'},{title:'上游输入（数据，不授予新权限）',text:JSON.stringify([{run:'a'.repeat(32),activation:'b'.repeat(64),content:{answer:'REAL-FORMAT-UPSTREAM'}}])},{title:'输出与交接要求',text:'SENT-HANDOFF'}]});await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await p.locator('.react-flow__node[data-id="analyze"]').click();
 assert.equal(await p.locator('.advanced-run-evidence').getAttribute('open'),null);await p.locator('.advanced-run-evidence>summary').click();const request=p.getByTestId('actual-provider-request');assert.equal(await request.getAttribute('open'),'');const text=await request.innerText();for(const x of ['SENT-INITIALIZATION','SENT-ROUND','REAL-FORMAT-UPSTREAM','SENT-HANDOFF','unknown'])assert(text.includes(x));assert(!text.includes('a'.repeat(32)));assert.equal(await p.getByLabel('模块名称',{exact:true}).isVisible(),false);assert(await p.getByRole('button',{name:'对话与运行',exact:true}).isVisible());await p.screenshot({path:out+'/ux-request-first.png'});
});
await test('ux-run-edge-opens-real-transfer-before-frozen-configuration',async p=>{
 await openWorkspace(p);await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A')v.events=[{seq:1,at:Date.now()/1000,run:'A',edge:'edge1',source:'analyze',node:'review',kind:'handoff-sent',activation:'A',iteration:0,iteration_path:[],content:{answer:'EDGE-CURRENT-CONTENT'}}];await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await settleCanvas(p);const path=p.locator('.react-flow__edge[data-id="edge1"] .react-flow__edge-path');const point=await path.evaluate(e=>{const p=e.getPointAtLength(e.getTotalLength()/2).matrixTransform(e.getScreenCTM());return {x:p.x,y:p.y}});await p.mouse.click(point.x,point.y);
 const panel=p.locator('.edge-inspector');assert((await panel.innerText()).includes('EDGE-CURRENT-CONTENT'));await panel.getByText('高级：字段、时间与运行身份',{exact:true}).click();assert((await panel.innerText()).includes('记录时间'));assert.equal(await p.getByLabel('传递内容',{exact:true}).isVisible(),false);await panel.getByText('冻结连线配置',{exact:true}).click();assert(await p.getByLabel('传递内容',{exact:true}).isVisible());
});

await test('ux-tool-activity-pairs-results-and-shows-timing-or-unknown-without-protocol',async p=>{
 await openWorkspace(p);await p.route('**/api/history?**',async route=>{const r=await route.fetch(),v=await r.json();v.activity=[{name:'exec',history_truncated:true,call_id:'alpha-native-id',arguments:'alpha-command',result:'alpha-result',association:'matched',started_at:10,finished_at:22,duration_seconds:12,events:[]},{name:'Read',arguments:'read-unknown-time',result:'read-result',association:'matched',duration_seconds:null,events:[]}];await route.fulfill({json:v});});
 await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();const tools=p.locator('.tool-activity');await tools.waitFor();assert.equal(await tools.getAttribute('open'),null);await tools.locator(':scope>summary').click();assert((await tools.innerText()).includes('更早活动已省略'));const entries=tools.locator('.tool-entry');assert((await entries.nth(0).innerText()).includes('12 秒'));assert((await entries.nth(1).innerText()).includes('unknown'));
 await entries.nth(0).locator(':scope>summary').click();const text=await entries.nth(0).innerText();assert(text.includes('参数摘要'));assert(text.includes('alpha-command'));assert(text.includes('alpha-result'));assert(!text.includes('alpha-native-id'));await p.screenshot({path:out+'/ux-tool-timing.png'});
});

await test('ux-first-entry-and-new-workflow-are-truly-blank-with-five-step-guide',async p=>{
 await openWorkspace(p,false);assert.equal(await p.locator('.react-flow__node').count(),0);assert.equal(await p.locator('.onboarding li').count(),5);assert(await p.getByRole('button',{name:'创建空白工作流',exact:true}).isVisible());
 await p.getByRole('button',{name:'创建空白工作流',exact:true}).click();await p.locator('.palette').waitFor();assert.equal(await p.locator('.react-flow__node').count(),0);await p.locator('.palette').getByRole('button',{name:'输入',exact:true}).click();await until(async()=>await p.locator('.react-flow__node').count()===1);
 await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();await p.getByRole('button',{name:'工作流',exact:true}).click();await p.getByRole('button',{name:'新建工作流',exact:true}).click();if(await p.getByRole('dialog',{name:'未保存的草稿'}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();await until(async()=>await p.locator('.react-flow__node').count()===0);await p.locator('.palette h3 button').click();await p.reload();await p.getByRole('button',{name:'打开文件夹',exact:true}).waitFor();assert.equal(await p.locator('.react-flow__node').count(),0,'Refresh restores the same empty editable draft, never silently installs a template');
});
await test('i16-node-edge-palette-escape-keeps-draft-and-never-controls-run',async p=>{
 await openWorkspace(p);const controls=[];p.on('request',r=>{if(/\/api\/(control|terminal\/close)$/.test(r.url()))controls.push(r.url())});
 await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByLabel('模块名称',{exact:true}).fill('保留未保存节点');await p.keyboard.press('Escape');assert.equal(await p.locator('.inspector').count(),0);await p.locator('.react-flow__node[data-id="analyze"]').click();assert.equal(await p.getByLabel('模块名称',{exact:true}).inputValue(),'保留未保存节点');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 await p.getByRole('button',{name:'添加节点',exact:true}).click();await p.locator('.palette').waitFor();await p.keyboard.press('Escape');assert.equal(await p.locator('.palette').count(),0);assert.equal(await p.getByRole('button',{name:'添加节点',exact:true}).evaluate(e=>document.activeElement===e),true);assert.equal(controls.length,0);
});
await test('i16-chat-tabs-draft-save-discard-cancel-and-reopen',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('消息',{exact:true}).fill('未发送内容不得丢失');await p.getByRole('button',{name:'关闭新会话标签',exact:true}).click();await p.getByRole('dialog',{name:'未保存的草稿'}).waitFor();await p.getByRole('button',{name:'取消关闭',exact:true}).click();assert.equal(await p.getByLabel('消息',{exact:true}).inputValue(),'未发送内容不得丢失');await p.keyboard.press('Escape');await p.getByRole('button',{name:'保存草稿并关闭',exact:true}).click();await p.getByRole('button',{name:'新建会话',exact:true}).click();assert.equal(await p.getByLabel('消息',{exact:true}).inputValue(),'未发送内容不得丢失');await p.getByRole('button',{name:'关闭新会话标签',exact:true}).click();await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();await p.getByRole('button',{name:'新建会话',exact:true}).click();assert.equal(await p.getByLabel('消息',{exact:true}).inputValue(),'');
});
await test('i16-model-source-refresh-and-provider-error-are-visible',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');await p.getByRole('button',{name:'刷新模型',exact:true}).waitFor();await until(async()=>await p.getByRole('button',{name:'刷新模型',exact:true}).isEnabled());await p.getByRole('button',{name:'刷新模型',exact:true}).click();await p.locator('.model-connection-details>summary').click();await until(async()=>(await p.locator('.catalog-meta').innerText()).includes('目录已获取'));assert((await p.locator('.catalog-meta').innerText()).includes('Codex app-server model/list'));assert(await p.getByLabel('推理强度',{exact:true}).isVisible());await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-claude');await p.locator('.model-connection-details>summary').click();await until(async()=>(await p.locator('.model-picker').innerText()).includes('未配置'));assert((await p.locator('.model-picker').innerText()).includes('不是完整模型列表'));
});
await test('i16-router-editor-and-close-have-no-native-side-effects',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'添加节点',exact:true}).click();await p.locator('.palette').getByRole('button',{name:'智能路由',exact:true}).click();await p.getByLabel('路由字段 1',{exact:true}).fill('result.task_type');await p.getByLabel('默认分支',{exact:true}).fill('default');await p.getByLabel('启用语义分类',{exact:true}).check();assert(await p.getByText('分类超时（秒）',{exact:true}).isVisible());await p.keyboard.press('Escape');assert.equal(await p.locator('.inspector').count(),0);assert.equal(await p.locator('.react-flow__node').count(),5);
});
await test('i16-terminal-view-close-detaches-without-stop',async p=>{
 const {mutations}=await nativeSessions(p);await p.keyboard.press('Escape');assert.equal(await p.locator('.native-terminal').count(),0);assert.equal(mutations.length,0);assert(await p.getByLabel('消息',{exact:true}).isVisible());
});
await test('i16-delayed-chat-prepare-cannot-open-in-another-tab',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('消息',{exact:true}).fill('PREVIEW-A');let release,arrived;const hold=new Promise(r=>release=r),seen=new Promise(r=>arrived=r);await p.route('**/api/chat/prepare',async route=>{const response=await route.fetch();arrived();await hold;await route.fulfill({response})});await p.getByRole('button',{name:'发送',exact:true}).click();await seen;await p.getByRole('button',{name:'工作流画布',exact:true}).click();release();await delay(180);assert.equal(await p.locator('.authorization').count(),0);await p.getByRole('button',{name:'新对话',exact:true}).click();assert.equal(await p.getByLabel('消息',{exact:true}).inputValue(),'PREVIEW-A');
});
await test('i16-unsaved-workflow-cancel-and-save-before-switch',async p=>{
 await openWorkspace(p);await p.getByLabel('工作流名称',{exact:true}).fill('需要保存的工作流');await p.getByRole('button',{name:'示例',exact:true}).click();await p.locator('.example-library .built-in-template').nth(1).click();await p.getByRole('button',{name:'取消关闭',exact:true}).click();assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'需要保存的工作流');await p.locator('.example-library .built-in-template').nth(1).click();await p.getByRole('button',{name:'保存草稿并关闭',exact:true}).click();await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='并发分析与汇总');await p.getByRole('button',{name:'工作流',exact:true}).click();assert(await p.locator('.draft-list').getByRole('button',{name:'需要保存的工作流',exact:true}).isVisible());
});
await test('i16-draft-preview-is-clearly-not-a-real-provider-receipt',async p=>{
 await openWorkspace(p);await p.getByLabel('本次任务',{exact:true}).fill('DRAFT-TASK-NOT-SENT');await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByText('输入/输出预览（草稿）',{exact:true}).click();assert((await p.getByLabel('草稿预览',{exact:true}).innerText()).includes('任务输入'));await p.getByText('本轮 Prompt 预览（草稿模板）',{exact:true}).click();const text=await p.getByLabel('草稿预览',{exact:true}).innerText();assert(text.includes('DRAFT-TASK-NOT-SENT'));assert(text.includes('尚未执行'));assert(text.includes('unknown'));assert.equal(await p.getByTestId('actual-provider-request').count(),0);
});
await test('i16-top-level-handoff-round-and-missing-activation-are-distinct',async p=>{
 await openWorkspace(p);let missing=false;await p.route('**/api/run?**',async route=>{const r=await route.fetch(),v=await r.json();if(v.run.id==='A'){
 v.nodes=[{id:'A',run:'A',node:'analyze',state:'COMPLETED',round:1,iteration:0,iteration_path:[]},...(!missing?[{id:'target-A',run:'A',node:'review',state:'RUNNING',round:1,iteration:0,iteration_path:[]}]:[])];
 v.events=[{seq:1,at:1,run:'A',edge:'edge1',source:'analyze',node:'review',kind:'handoff-sent',activation:'A',target_activation:'target-A',iteration:0,iteration_path:[],source_round:[],target_round:[],content:{answer:'TOP-LEVEL-HANDOFF'}}];}await route.fulfill({json:v});});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await settleCanvas(p);const path=p.locator('.react-flow__edge[data-id="edge1"] .react-flow__edge-path');const point=await path.evaluate(e=>{const q=e.getPointAtLength(e.getTotalLength()/2).matrixTransform(e.getScreenCTM());return{x:q.x,y:q.y}});await p.mouse.click(point.x,point.y);
 const panel=p.locator('.edge-inspector');await panel.getByText('高级：字段、时间与运行身份',{exact:true}).click();const value=label=>panel.locator('dt').filter({hasText:label}).locator('xpath=following-sibling::dd[1]').innerText();assert.equal((await value('来源轮次')).trim(),'1');assert.equal((await value('目标轮次')).trim(),'1');missing=true;await until(async()=>(await value('目标轮次')).trim()==='unknown');assert.equal((await value('来源轮次')).trim(),'1');
});
await test('design-md-default-canvas-readable-explicit-overview-and-no-selection-jump',async p=>{
 await openWorkspace(p,false);
 for(const width of [700,952,1512]){
  await p.setViewportSize({width,height:900});await example(p,0,false);
  const nodes=p.locator('.react-flow__node');await until(async()=>await nodes.count()===4&&await nodes.evaluateAll(es=>es.every(e=>getComputedStyle(e).visibility==='visible')));
  const scale=()=>p.locator('.react-flow__viewport').evaluate(e=>new DOMMatrix(getComputedStyle(e).transform).a);
  assert(await scale()>=.9,'Default editing text must remain readable at '+width);
  const title=await nodes.first().locator('header strong').evaluate(e=>parseFloat(getComputedStyle(e).fontSize));
  assert(title*await scale()>=12,'Node title cannot be reduced to a tiny overview caption');
  const before=await p.locator('.react-flow__viewport').getAttribute('style');
  await nodes.first().click();await p.keyboard.press('Escape');await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.equal(await p.locator('.react-flow__viewport').getAttribute('style'),before,'Inspecting a node must not move the current camera');
  await p.screenshot({path:out+'/design-md-readable-'+width+'.png'});
  await p.getByRole('button',{name:'适应画布',exact:true}).click();
  await until(async()=>{const canvas=await p.getByTestId('canvas').boundingBox();return nodes.evaluateAll((es,c)=>es.every(e=>{const r=e.getBoundingClientRect();return r.left>=c.x-2&&r.right<=c.x+c.width+2&&r.top>=c.y-2&&r.bottom<=c.y+c.height+2}),canvas)});
  assert(await scale()<=1,'The explicit overview remains available for the entire graph');
 }
});
await test('design-md-new-chat-error-keeps-introduction-visible',async p=>{
 await openWorkspace(p,false);
 await p.route('**/api/models',r=>r.fulfill({status:400,json:{error:'模型目录暂时不可达，请检查连接后刷新'}}));
 await p.getByRole('button',{name:'开始对话',exact:true}).click();
 await p.getByText('模型目录暂时不可达，请检查连接后刷新',{exact:true}).waitFor();
 for(const [width,height] of [[700,660],[700,756],[952,756],[1512,756]]){
  await p.setViewportSize({width,height});await p.evaluate(()=>document.fonts.ready);
  await until(async()=>p.locator('.messages').evaluate(e=>{const box=e.getBoundingClientRect();return [...e.querySelectorAll('.empty-chat h2,.empty-chat p')].every(x=>{const r=x.getBoundingClientRect();return r.top>=box.top&&r.bottom<=box.bottom&&r.left>=box.left&&r.right<=box.right})}));
  assert.equal(await p.locator('.messages').evaluate(e=>e.scrollTop),0,'The introduction is visible without scrolling past blank space');
  await p.screenshot({path:out+'/design-md-chat-error-'+width+'.png'});
 }
});
await test('i16-sim-studio-chrome-reserves-inspector-and-keeps-native-actions-explicit',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p,false);
 assert.equal(await p.locator('.react-flow').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');
 const rail=await p.getByRole('complementary',{name:'工作流工具',exact:true}).boundingBox(),sidebar=await p.locator('.sessions-sidebar').boundingBox(),canvas=await p.getByTestId('canvas').boundingBox();
 assert.equal(Math.round(sidebar.width),224);assert.equal(Math.round(rail.width),320);assert(canvas.x>=sidebar.x+sidebar.width-1);assert(canvas.x+canvas.width<=rail.x+1,'Tool rail must reserve canvas space rather than cover connection targets');
 const controls=[];p.on('request',r=>{if(/\/api\/(start|control|chat\/start|terminal\/(start|close))$/.test(r.url()))controls.push(r.url())});
 await p.locator('.studio-node-library button').filter({hasText:'原生会话与模型'}).click();await p.getByLabel('模块名称',{exact:true}).fill('Sim 布局配置测试');
 await until(async()=>{const n=await p.locator('.react-flow__node').boundingBox(),i=await p.locator('.inspector').boundingBox();return n.x>=canvas.x&&n.x+n.width<=i.x+1});
 const panel=await p.locator('.inspector').boundingBox();assert.equal(Math.round(panel.width),320);assert(Math.abs(panel.x-rail.x)<2);await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 assert(await p.locator('.studio-node-library').isVisible());assert.equal(await p.locator('.react-flow__node').count(),1);assert.equal(controls.length,0,'Navigating the editor never launches or stops an Agent');
 const transform=()=>p.locator('.react-flow__viewport').getAttribute('style'),before=await transform();await p.getByRole('button',{name:'放大画布',exact:true}).click();await until(async()=>await transform()!==before);await p.getByRole('button',{name:'适应画布',exact:true}).click();assert(await p.getByRole('toolbar',{name:'画布编辑工具',exact:true}).isVisible());
 await p.getByRole('button',{name:'开始对话',exact:true}).click();assert(await p.getByLabel('消息',{exact:true}).isVisible());assert.equal(await p.locator('.studio-tools').count(),0);assert.equal(controls.length,0);await p.screenshot({path:out+'/sim-studio-conversation.png'});
});
await test('i16-sim-studio-small-screen-can-configure-and-close-without-horizontal-overflow',async p=>{
 await p.setViewportSize({width:640,height:820});await openWorkspace(p,false);assert.equal(await p.locator('.studio-tools').isVisible(),false);
 await p.getByRole('button',{name:'创建空白工作流',exact:true}).click();await p.locator('.palette').getByRole('button',{name:'Agent',exact:true}).click();
 await p.getByLabel('模块名称',{exact:true}).fill('小屏配置保留');await p.keyboard.press('Escape');assert.equal(await p.locator('.inspector').count(),0);
 assert(await p.getByRole('button',{name:'开始运行',exact:true}).isVisible());assert(await p.getByRole('button',{name:'添加节点',exact:true}).isVisible());
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Core controls must not require horizontal page scrolling');await p.screenshot({path:out+'/sim-studio-small-screen.png'});
});
await test('i16-sim-light-dark-system-preserve-draft-and-never-control-agents',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await p.getByLabel('工作流名称',{exact:true}).fill('换主题保留草稿');const mutations=[];p.on('request',r=>{if(/\/api\/(start|control|chat\/start|terminal\/(start|close))$/.test(r.url()))mutations.push(r.url())});
 await p.getByLabel('外观主题',{exact:true}).selectOption('dark');assert.equal(await p.locator('html').getAttribute('data-theme'),'dark');assert.equal(await p.locator('.react-flow').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(27, 27, 30)');assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'换主题保留草稿');
 await p.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));await p.reload();assert.equal(await p.getByLabel('外观主题',{exact:true}).inputValue(),'dark');assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'换主题保留草稿');await p.screenshot({path:out+'/sim-dark-canvas.png'});
 await p.getByLabel('外观主题',{exact:true}).selectOption('light');assert.equal(await p.locator('.react-flow').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');await p.getByLabel('外观主题',{exact:true}).selectOption('system');await p.emulateMedia({colorScheme:'dark'});await until(async()=>await p.locator('html').getAttribute('data-theme')==='dark');await p.emulateMedia({colorScheme:'light'});await until(async()=>await p.locator('html').getAttribute('data-theme')==='light');assert.equal(mutations.length,0);
});
await test('i16-sim-panel-resize-cancel-keyboard-collapse-and-persistence',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);const sidebar=p.getByRole('separator',{name:'调整导航栏宽度',exact:true}),before=await p.locator('.sessions-sidebar').boundingBox(),r=await sidebar.boundingBox();await p.mouse.move(r.x+3,r.y+60);await p.mouse.down();await p.mouse.move(r.x+76,r.y+60,{steps:5});await p.mouse.up();await until(async()=>(await p.locator('.sessions-sidebar').boundingBox()).width>before.width+50);assert.equal(await p.evaluate(()=>document.body.style.cursor),'');
 const width=(await p.locator('.sessions-sidebar').boundingBox()).width;await sidebar.focus();await p.keyboard.press('ArrowRight');await until(async()=>Math.round((await p.locator('.sessions-sidebar').boundingBox()).width)===Math.round(width+10));await p.getByRole('button',{name:'收起导航栏',exact:true}).click();await until(async()=>Math.round((await p.locator('.sessions-sidebar').boundingBox()).width)===52);assert(await p.getByRole('button',{name:'打开文件夹',exact:true}).isVisible());await p.getByRole('button',{name:'展开导航栏',exact:true}).click();await until(async()=>Math.round((await p.locator('.sessions-sidebar').boundingBox()).width)===Math.round(width+10));await p.reload();assert.equal(Math.round((await p.locator('.sessions-sidebar').boundingBox()).width),Math.round(width+10));
 const right=p.getByRole('separator',{name:'调整检查器宽度',exact:true}),box=await right.boundingBox();await p.mouse.move(box.x+3,box.y+90);await p.mouse.down();await p.mouse.move(box.x-60,box.y+90,{steps:3});await p.evaluate(()=>window.dispatchEvent(new Event('blur')));await p.mouse.up();assert.equal(await p.evaluate(()=>document.body.style.userSelect),'');assert.equal(await p.locator('.workbench').getAttribute('data-resizing'),null);
 await p.getByRole('button',{name:'活动',exact:true}).click();const bottom=p.getByRole('separator',{name:'调整活动面板高度',exact:true});await bottom.focus();await p.keyboard.press('ArrowUp');assert.equal(await bottom.getAttribute('aria-valuenow'),'190');
});
await test('i16-sim-port-to-empty-creates-connected-node-as-one-undo',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);const source=p.locator('.react-flow__node[data-id="analyze"] .react-flow__handle.source'),box=await source.boundingBox(),canvas=await p.getByTestId('canvas').boundingBox();await p.mouse.move(box.x+box.width/2,box.y+box.height/2);await p.mouse.down();await p.mouse.move(canvas.x+canvas.width*.58,canvas.y+100,{steps:12});await p.mouse.up();await p.getByRole('dialog',{name:'在画布添加节点',exact:true}).waitFor();await p.getByLabel('搜索节点类型',{exact:true}).fill('agent');await p.keyboard.press('Enter');await until(async()=>await p.locator('.react-flow__node').count()===5);await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();assert.equal(await p.locator('.react-flow__edge').count(),4);await p.getByRole('button',{name:'撤销',exact:true}).click();assert.equal(await p.locator('.react-flow__node').count(),4);assert.equal(await p.locator('.react-flow__edge').count(),3);
});
await test('i16-sim-context-menu-duplicates-selection-and-connected-picker-escape-restores-focus',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);const node=p.locator('.react-flow__node[data-id="analyze"]');await node.click({button:'right'});await p.getByRole('menu',{name:'画布快捷操作',exact:true}).waitFor();await p.getByRole('menuitem',{name:'创建节点副本',exact:true}).click();assert.equal(await p.locator('.react-flow__node').count(),5);await p.getByRole('button',{name:'撤销',exact:true}).click();assert.equal(await p.locator('.react-flow__node').count(),4);
 const canvas=await p.getByTestId('canvas').boundingBox();await p.mouse.dblclick(canvas.x+canvas.width*.5,canvas.y+80);await p.getByRole('dialog',{name:'在画布添加节点',exact:true}).waitFor();await p.keyboard.press('Escape');assert.equal(await p.getByRole('dialog',{name:'在画布添加节点',exact:true}).count(),0);assert.equal(await p.locator('.react-flow__node').count(),4);
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await p.locator('.react-flow__node[data-id="analyze"]').click({button:'right'});assert.equal(await p.getByRole('menu',{name:'画布快捷操作',exact:true}).count(),0);
});
await test('i16-sim-edge-insert-preserves-topology-and-one-undo',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await p.getByRole('button',{name:'在连线上添加节点',exact:true}).nth(1).click();await p.getByLabel('搜索节点类型',{exact:true}).fill('agent');await p.keyboard.press('Enter');await until(async()=>await p.locator('.react-flow__node').count()===5);await displayedCanvasEdges(p,await p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph));assert.equal(await p.locator('.react-flow__edge').count(),4);await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();await p.getByRole('button',{name:'撤销',exact:true}).click();assert.equal(await p.locator('.react-flow__node').count(),4);assert.equal(await p.locator('.react-flow__edge').count(),3);
});
await test('i16-sim-edge-insert-rejects-input-and-end-and-saves-valid-graph',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await p.getByRole('button',{name:'在连线上添加节点',exact:true}).nth(1).click();const picker=p.getByRole('dialog',{name:'在画布添加节点',exact:true});assert.equal(await picker.getByRole('option',{name:'输入',exact:true}).count(),0);assert.equal(await picker.getByRole('option',{name:'结束',exact:true}).count(),0);await picker.getByRole('option',{name:'Agent',exact:true}).click();await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();const response=p.waitForResponse(r=>r.url().endsWith('/api/draft')&&r.request().method()==='POST');await p.getByRole('button',{name:'保存',exact:true}).click();const r=await response;assert(r.ok(),'Actual backend must accept the inserted graph');const g=r.request().postDataJSON().graph;assert.equal(g.nodes.filter(n=>n.kind==='start').length,1);assert(g.edges.every(e=>g.nodes.find(n=>n.id===e.source).kind!=='result'&&g.nodes.find(n=>n.id===e.target).kind!=='start'));assert.equal(g.nodes.length,5);assert.equal(g.edges.length,4);
});
await test('i16-sim-canvas-picker-cannot-survive-switch-to-another-document',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);const bounds=await p.getByTestId('canvas').boundingBox();await p.mouse.dblclick(bounds.x+bounds.width*.5,bounds.y+80);await p.getByRole('dialog',{name:'在画布添加节点',exact:true}).waitFor();await example(p,1);assert.equal(await p.getByRole('dialog',{name:'在画布添加节点',exact:true}).count(),0);assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'并发分析与汇总');assert.equal(await p.locator('.react-flow__node').count(),6);
});
await test('i16-sim-drag-library-drop-validates-kind-and-never-executes-a-node',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p,false);const calls=[];p.on('request',r=>{if(/\/api\/(start|control|chat\/start)$/.test(r.url()))calls.push(r.url())});const canvas=p.getByTestId('canvas'),box=await canvas.boundingBox();
 for(const kind of ['constructor','__proto__','unknown-provider'])await canvas.evaluate((e,{kind,x,y})=>{const data=new DataTransfer();data.setData('application/x-devflower-node',kind);e.dispatchEvent(new DragEvent('drop',{dataTransfer:data,clientX:x,clientY:y,bubbles:true}))},{kind,x:box.x+180,y:box.y+120});assert.equal(await p.locator('.react-flow__node').count(),0);
 const card=p.locator('.studio-node-library button').filter({hasText:'原生会话与模型'});await card.dragTo(canvas,{targetPosition:{x:180,y:120}});await until(async()=>await p.locator('.react-flow__node').count()===1);assert(await p.getByLabel('模块名称',{exact:true}).isVisible());assert.equal(calls.length,0);
});
await test('admission-registration-keeps-discovery-separate-and-confirm-cancel-has-no-call',async p=>{
 await openWorkspace(p);const calls=[];p.on('request',r=>{if(/\/api\/(model-admission|chat\/start|chat\/prepare)/.test(r.url()))calls.push(r.url())});
 await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');await p.locator('.model-connection-details>summary').click();
 await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());const detail=await p.locator('.model-connection-details').innerText();assert(detail.includes('目录不是调用成功证明'));assert(detail.includes('不会改变工作区权限'));assert.equal(await p.getByRole('button',{name:'验证执行',exact:true}).count(),0);
 await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await until(async()=>await p.getByLabel('模型',{exact:true}).inputValue()==='synthetic-configurable-model');
 await p.getByLabel('关闭新会话标签',{exact:true}).click();assert.deepEqual(calls,[],'Closing the unsent session must not send a task or probe');
});
await test('admission-late-preview-cannot-authorize-a-different-profile',async p=>{
 await p.route('**/api/models',async route=>{const response=await route.fetch(),value=await response.json();if(Array.isArray(value.models))for(const row of value.models)if(row.id==='synthetic-configurable-model')row.operator_registered=false;await route.fulfill({response,json:value})});
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());
 let selections=0;p.on('request',r=>{if(r.url().endsWith('/api/model-selection'))selections++});
 await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-claude');await delay(250);assert.equal(await p.getByLabel('Agent 工具',{exact:true}).inputValue(),'default-claude');assert.notEqual(await p.getByLabel('模型',{exact:true}).inputValue(),'synthetic-configurable-model');assert.equal(await p.getByLabel('本次授权确认意见').count(),0);assert.equal(selections,0)
});
await test('model-draft-effort-survives-close-and-start-without-selection-request',async p=>{
 await p.route('**/api/models',async route=>{const response=await route.fetch(),value=await response.json();for(const row of value.models||[])if(row.id==='synthetic-configurable-model')row.selectable_efforts=['xhigh','high','low'];await route.fulfill({response,json:value})});
 await openWorkspace(p);await example(p,4);await p.locator('.react-flow__node[data-id="agent"]').click();await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());
 let selections=0,prepared;p.on('request',r=>{if(r.url().endsWith('/api/model-selection'))selections++});
 await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await p.getByLabel('推理强度',{exact:true}).selectOption('xhigh');await p.getByLabel('推理强度',{exact:true}).selectOption('high');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();await p.getByLabel('本次任务',{exact:true}).fill('验证本次模型强度');
 await p.route('**/api/prepare',async route=>{const response=await route.fetch(),body=await response.json();assert.equal(response.status(),200,JSON.stringify(body));prepared=body.data;await route.fulfill({response,json:body})});
 let starts=0;await p.route('**/api/start',route=>{starts++;return route.fulfill({status:400,json:{error:'synthetic stop before model'}})});await p.getByRole('button',{name:'开始运行',exact:true}).click();await until(()=>!!prepared);const agent=prepared.graph.nodes.find(n=>n.id==='agent');assert.equal(agent.model,'synthetic-configurable-model');assert.equal(agent.effort,'high');assert.equal(selections,0);await p.getByText('synthetic stop before model',{exact:true}).waitFor();assert.equal(starts,1);
 await p.locator('.react-flow__node[data-id="agent"]').click();await until(async()=>await p.getByLabel('推理强度',{exact:true}).isEnabled());await p.getByLabel('推理强度',{exact:true}).selectOption('low');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();prepared=null;await p.getByRole('button',{name:'开始运行',exact:true}).click();await until(()=>!!prepared);assert.equal(prepared.graph.nodes.find(n=>n.id==='agent').effort,'low');await until(()=>starts===2);
});
await test('model-draft-selection-remains-bound-to-original-node',async p=>{
 await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await p.getByLabel('推理强度',{exact:true}).selectOption('low');
 await p.locator('.react-flow__node[data-id="review"]').click();await until(async()=>await p.getByLabel('执行工具',{exact:true}).inputValue()==='default-claude');assert.notEqual(await p.getByLabel('模型',{exact:true}).inputValue(),'synthetic-configurable-model');
 await p.locator('.react-flow__node[data-id="analyze"]').click();await until(async()=>await p.getByLabel('模型',{exact:true}).inputValue()==='synthetic-configurable-model');assert.equal(await p.getByLabel('推理强度',{exact:true}).inputValue(),'low');
});
await test('model-draft-selection-does-not-cross-workspace',async p=>{
 await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await p.getByLabel('推理强度',{exact:true}).selectOption('low');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 const saved=p.waitForResponse(r=>r.url().endsWith('/api/draft')&&r.request().method()==='POST');await p.getByRole('button',{name:'保存',exact:true}).click();assert.equal((await saved).status(),200);
 const path=require('node:path'),other=path.join(access.path,'synthetic-model-workspace-b');fs.mkdirSync(other,{recursive:true});fs.writeFileSync(path.join(other,'README.md'),'synthetic workspace');
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();await p.getByLabel('目录路径',{exact:true}).fill(other);await p.getByRole('button',{name:'前往',exact:true}).click();await until(async()=>(await p.locator('.folder-breadcrumb').innerText()).includes('synthetic-model-workspace-b'));await p.getByRole('button',{name:'选择此文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor({state:'hidden'});await example(p,4);await p.locator('.react-flow__node[data-id="agent"]').click();await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());assert.notEqual(await p.getByLabel('模型',{exact:true}).inputValue(),'synthetic-configurable-model');
});
await test('admission-classifier-has-its-own-exact-timeout-and-workspace-verification',async p=>{
 await openWorkspace(p);await example(p,3);await p.locator('.react-flow__node[data-id="route"]').click();await p.getByLabel('启用语义分类',{exact:true}).check();await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 let prepared,starts=0,probes=0;p.on('request',r=>{if(r.url().includes('/api/model-admission/'))probes++});
 await p.route('**/api/prepare',async route=>{const response=await route.fetch(),body=await response.json();assert.equal(response.status(),200,JSON.stringify(body));prepared=body.data;await route.fulfill({response,json:body})});
 await p.route('**/api/start',route=>{starts++;return route.fulfill({status:400,json:{error:'Synthetic classifier call failure'}})});
 await p.getByLabel('本次任务',{exact:true}).fill('Synthetic classifier binding');await p.getByRole('button',{name:'开始运行',exact:true}).click();await until(()=>starts===1);assert.equal(prepared.workspace,access.workspace);const binding=prepared.bindings.route.binding;assert.equal(binding.execution_mode,'Ask');assert.equal(binding.timeout_seconds,60);assert.equal(binding.role,'reviewer');assert.equal(probes,0);await p.locator('.alert').waitFor();assert((await p.locator('.alert').innerText()).includes('Synthetic classifier call failure'));
});
await test('admission-native-protocol-failure-is-visible-and-revokes-synthetic-certificate',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');let starts=0,probes=0;
 p.on('request',r=>{if(r.url().includes('/api/model-admission/'))probes++});
 await p.route('**/api/chat/start',route=>{starts++;assert.deepEqual(route.request().postDataJSON().answer,{action:'send'});return route.fulfill({status:400,json:{error:'Synthetic native protocol failed'}})});
 await p.getByLabel('消息',{exact:true}).fill('Synthetic actual request, no model used');await p.getByRole('button',{name:'发送',exact:true}).click();await p.locator('.alert').waitFor();assert((await p.locator('.alert').innerText()).includes('Synthetic native protocol failed'));await delay(200);assert.equal(starts,1);assert.equal(probes,0);
 // Legacy endpoint remains callable only under its explicit old contract. Its
 // synthetic CLI failure must revoke the exact old certificate, not grant one.
 const headers={Authorization:'Bearer '+access.token},scope={workspace:access.workspace,profile:'default-codex',model:'test-model',effort:'high',mode:'Ask',permission:'read',timeout:300};
 const prepared=await p.request.post(access.url+'/api/model-admission/prepare',{headers,data:scope});assert.equal(prepared.status(),200);const permit=await prepared.json();
 const verified=await p.request.post(access.url+'/api/model-admission/verify',{headers,data:{id:permit.id,sha256:permit.sha256,answer:'Synthetic legacy verification'}});assert.equal(verified.status(),400);
 const response=await p.request.post(access.url+'/api/model-admission/status',{headers,data:scope});assert.equal(response.status(),200);assert.equal((await response.json()).execution_allowed,false);
});
// New product paths. Synthetic native records are explicitly separate from real CLI acceptance.
await test('product-node-selection-does-not-recenter-or-move-nodes',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await delay(250);
 const before=await p.locator('.react-flow__viewport').getAttribute('style');const graph=await p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph);
 for(const id of ['analyze','review']){await p.locator(`.react-flow__node[data-id="${id}"]`).click();await p.getByLabel('模块名称',{exact:true}).waitFor();await delay(160);assert.equal(await p.locator('.react-flow__viewport').getAttribute('style'),before);}
 assert.deepEqual((await p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph)).nodes.map(n=>n.position),graph.nodes.map(n=>n.position));
});
await test('product-new-chat-selects-agent-and-hides-protocol-details',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();const picker=p.getByLabel('Agent 工具',{exact:true});assert((await picker.innerText()).includes('Claude Code'));assert((await picker.innerText()).includes('Codex'));
 const choices=await picker.innerText();assert(!choices.includes('test-model'));assert(!choices.includes('/ high'));assert.equal(await p.getByRole('button',{name:'验证执行',exact:true}).isVisible(),false);await p.getByRole('button',{name:'刷新模型',exact:true}).waitFor({state:'visible'});assert(await p.getByRole('button',{name:'刷新模型',exact:true}).isVisible());
 await p.setViewportSize({width:1512,height:1080});await p.screenshot({path:out+'/product-agent-picker.png'});
});
await test('product-delete-draft-confirm-cancel-and-reload-preserve-history',async p=>{
 await openWorkspace(p);await p.getByLabel('工作流名称',{exact:true}).fill('可以删除的测试草稿');await p.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));
 const old=await p.request.get(access.url+'/api/status',{headers:{Authorization:'Bearer '+access.token}});const before=await old.json();
 const remove=p.locator('.workspace-workflows').getByRole('button',{name:'删除工作流 可以删除的测试草稿',exact:true});await remove.click();const dialog=p.getByRole('dialog',{name:'删除工作流草稿',exact:true});await dialog.getByRole('button',{name:'取消',exact:true}).click();assert(await remove.isVisible());
 await remove.click();await dialog.getByRole('button',{name:'删除草稿',exact:true}).click();await until(async()=>await remove.count()===0);await p.reload();await p.getByRole('button',{name:'打开文件夹',exact:true}).waitFor();assert.equal(await remove.count(),0);
 const after=await (await p.request.get(access.url+'/api/status',{headers:{Authorization:'Bearer '+access.token}})).json();assert.equal(after.runs.length,before.runs.length);assert(!after.drafts.some(d=>d.graph.name==='可以删除的测试草稿'));
});
await test('product-run-checks-connection-after-one-confirmation-and-stops-on-failure',async p=>{
 await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();await p.getByLabel('超时（秒）',{exact:true}).fill('51');await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();
 await p.getByLabel('本次任务',{exact:true}).fill('Synthetic readonly provider failure');let starts=0,checks=0,prepared;
 p.on('request',r=>{if(r.url().includes('/api/model-admission/'))checks++});
 await p.route('**/api/prepare',async route=>{const response=await route.fetch(),v=await response.json();assert.equal(response.status(),200,JSON.stringify(v));prepared=v.data;await route.fulfill({response,json:v})});
 await p.route('**/api/start',route=>{starts++;assert.deepEqual(route.request().postDataJSON().answer,{action:'start'});return route.fulfill({status:400,json:{error:'Synthetic real request failure'}})});
 await p.getByRole('button',{name:'开始运行',exact:true}).click();await p.locator('.alert').waitFor();assert((await p.locator('.alert').innerText()).includes('Synthetic real request failure'));assert.equal(prepared.bindings.analyze.binding.timeout_seconds,51);await delay(300);assert.equal(starts,1);assert.equal(checks,0);assert.equal(await p.getByLabel('本次授权确认意见').count(),0);
});
await test('product-node-default-is-readable-conversation-original-evidence-remains',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await p.getByLabel('历史运行',{exact:true}).selectOption('dialog-plain-run');await until(async()=>await p.locator('.react-flow__node[data-id="analyze"]').count()===1);await p.locator('.react-flow__node[data-id="analyze"]').click();
 const view=p.getByLabel('Agent 运行对话',{exact:true});await until(async()=>(await view.innerText()).includes('SYNTHETIC dialog-plain final answer 1'));assert.equal(await p.locator('.advanced-run-evidence').getAttribute('open'),null);assert(!(await view.innerText()).includes('HIDDEN_RESULT_REASONING'));assert(!(await view.innerText()).includes('A. 初始化提示词'));
 await p.locator('.advanced-run-evidence>summary').click();const request=p.getByTestId('actual-provider-request');assert(await request.isVisible());assert((await request.innerText()).includes('本轮发送给原生 CLI 的可见输入'));if(await request.getAttribute('open')===null)await request.locator('summary').first().click();await request.getByRole('button',{name:'E 本轮输入',exact:true}).click();assert((await request.innerText()).includes('不是完整 Provider 上下文'));await p.screenshot({path:out+'/product-readable-conversation.png'});
});
await test('product-handoff-shows-names-and-content-once-per-real-stage',async p=>{
 await openWorkspace(p);await p.route('**/api/run?**',async route=>{const res=await route.fetch(),v=await res.json();if(v.run.id==='A'){const e={at:1,run:'A',edge:'edge1',source:'analyze',node:'review',activation:'A',target_activation:'review-round',target_round:[],iteration_path:[],attempt:1,content_sha256:'a'.repeat(64),content:{answer:'CLEAR_UPSTREAM_CONTENT'}};v.events=[{...e,seq:1,kind:'handoff-ready'},{...e,seq:2,kind:'handoff-sent'}]}await route.fulfill({json:v})});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await p.getByRole('button',{name:'活动',exact:true}).click();await p.getByRole('button',{name:'交接内容',exact:true}).click();assert.equal(await p.locator('.activity-content>details').count(),1);assert((await p.locator('.activity-content').innerText()).includes('CLEAR_UPSTREAM_CONTENT'));assert((await p.locator('.activity-content').innerText()).includes('分析 A'));assert(!(await p.locator('.activity-content').innerText()).includes('a'.repeat(64)));
});
await test('native-ordinary-terminal-label-and-close-preserve-live-pty',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-native'}).click();
 const row={id:'NATIVE-ORDINARY-PTY',handle:access.sessions['dialog-native'].handle,workspace:access.workspace,provider:'codex',execution_backend:'native-local',state:'RUNNING',process_alive:true,input_sequence:0,input_owner:'workbench',input_generation:0};let stops=0;
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output:'synthetic output',next:16,reset:false}}));await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 p.on('request',r=>{if(r.url().endsWith('/api/terminal/close'))stops++});await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await p.getByLabel('原生CLI终端',{exact:true}).getAttribute('data-connected')==='true');
 const panel=await p.locator('.native-terminal').innerText();assert(panel.includes('交互式原生 CLI · 权限由 CLI 管理'));assert(!panel.includes('该终端强制只读'));
 await p.getByRole('button',{name:'关闭终端视图',exact:true}).click();assert.equal(stops,0);
});
await test('native-local-attach-ui-observes-then-explicitly-reclaims',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();
 let row={id:'SHARED-SYNTHETIC-PTY',handle:access.sessions['dialog-A'].handle,workspace:access.workspace,state:'RUNNING',process_alive:true,input_sequence:3,input_owner:'local-terminal',input_generation:1},claims=[],inputs=[],stops=0;
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));
 await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output:'SYNTHETIC same native PTY',next:25,reset:false}}));
 await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 await p.route('**/api/terminal/claim',route=>{claims.push(route.request().postDataJSON());row={...row,input_owner:'workbench',input_generation:2};return route.fulfill({json:row})});
 await p.route('**/api/terminal/input',route=>{inputs.push(route.request().postDataJSON());return route.fulfill({json:{accepted:true}})});
 await p.route('**/api/terminal/close',route=>{stops++;return route.fulfill({json:{stopped:true}})});
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'接管到工作台',exact:true}).waitFor();
 await p.locator('.xterm-helper-textarea').focus();await p.keyboard.type('ignored');await delay(350);assert.equal(inputs.length,0);
 await p.getByRole('button',{name:'接管到工作台',exact:true}).click();await until(()=>claims.length===1);assert.equal(claims[0].generation,1);await until(async()=>await p.getByRole('button',{name:'接管到工作台',exact:true}).count()===0);
 await p.locator('.xterm-helper-textarea').focus();await p.keyboard.press('A');await until(()=>inputs.length===1);assert.equal(inputs[0].generation,2);assert.equal(inputs[0].sequence,4);
 await p.getByRole('button',{name:'关闭终端视图',exact:true}).click();assert.equal(stops,0);
});
await test('native-local-attach-is-not-presented-as-desktop-sync',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();const row={id:'SHARE-SYNTHETIC-PTY',handle:access.sessions['dialog-A'].handle,workspace:access.workspace,state:'RUNNING',process_alive:true,input_sequence:0,input_owner:'workbench',input_generation:0};
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output:'SYNTHETIC native output',next:23,reset:false}}));await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 await p.route('**/api/terminal/share',route=>route.fulfill({json:{terminal:row.id,session_id:'SYNTHETIC',command:'python3 -B /SYNTHETIC/devflower_attach.py /SYNTHETIC/private/attach.json',desktop_connected:false,desktop_reason:'当前 Codex 桌面端未连接此PTY；该命令仅连接本地原生CLI，不代表桌面同步。'}}));
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await p.getByRole('button',{name:'在本机终端连接',exact:true}).click();const view=p.getByLabel('本机原生会话连接',{exact:true});await view.waitFor();assert((await view.innerText()).includes('--takeover'));assert((await view.innerText()).includes('不代表桌面同步'));
 await p.getByRole('button',{name:'收起连接说明',exact:true}).click();assert.equal(await view.count(),0);
});
await test('product-terminal-explicit-tab-opens-once-and-reconnects-existing-pty',async p=>{
 await openWorkspace(p);await p.evaluate(()=>localStorage.removeItem('devflower-terminal-auto-open'));await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();let starts=0,active=false;const row={id:'UI-SYNTHETIC-PTY',handle:access.sessions['dialog-A'].handle,workspace:access.workspace,state:'RUNNING',process_alive:true,input_sequence:0};
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:active?[row]:[]}}));
 await p.route('**/api/terminal/prepare',async route=>{const res=await route.fetch(),v=await res.json();assert.equal(res.status(),200,'Terminal prepare HTTP failure: '+JSON.stringify(v));assert(v.data&&typeof v.data==='object','Terminal prepare response lacks data: '+JSON.stringify(v));assert.equal(v.data.handle,row.handle);assert.deepEqual(v.data.admission_checks,[],'Direct terminal must not request a nonce probe');await route.fulfill({response:res,json:v})});
 await p.route('**/api/terminal/start',route=>{starts++;active=true;return route.fulfill({json:row})});await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output:'SYNTHETIC CLI screen',next:20,reset:false}}));await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await p.getByLabel('原生CLI终端',{exact:true}).getAttribute('data-connected')==='true');assert.equal(starts,1);assert.equal(await p.locator('.terminal-confirm').count(),0);
 await p.getByRole('button',{name:'关闭终端视图',exact:true}).click();await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await p.getByLabel('原生CLI终端',{exact:true}).getAttribute('data-connected')==='true');assert.equal(starts,1);
});


await test('product-terminal-native-escape-reaches-pty-without-closing-view',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();
 const row={id:'ESCAPE-SYNTHETIC-PTY',handle:access.sessions['dialog-A'].handle,workspace:access.workspace,state:'RUNNING',process_alive:true,input_sequence:0},inputs=[],controls=[];
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));
 await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output:'SYNTHETIC native CLI screen',next:27,reset:false}}));
 await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 await p.route('**/api/terminal/input',route=>{inputs.push(route.request().postDataJSON());return route.fulfill({json:{accepted:true}})});
 p.on('request',r=>{if(/\/api\/(control|terminal\/close)$/.test(r.url()))controls.push(r.url())});
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await p.getByLabel('原生CLI终端',{exact:true}).getAttribute('data-connected')==='true');
 await p.locator('.xterm-helper-textarea').focus();await p.keyboard.press('Escape');
 assert.equal(await p.locator('.native-terminal').count(),1,'Native Escape must keep the terminal view open');await until(()=>inputs.length===1);assert.equal(inputs[0].text,'\x1b');assert.equal(inputs[0].id,row.id);assert.equal(inputs[0].sequence,1);
 assert.equal(await p.locator('.native-terminal').count(),1);assert.deepEqual(controls,[]);
 fs.writeFileSync(out+'/native-escape-evidence.json',JSON.stringify({evidence_type:'ordinary Chromium; synthetic PTY HTTP boundary',inputs,terminal_view_open:true,controls},null,2));
 await p.getByRole('button',{name:'关闭终端视图',exact:true}).click();assert.equal(await p.locator('.native-terminal').count(),0);assert.deepEqual(controls,[]);
});
await test('product-terminal-empty-live-wrapper-shows-waiting-until-output',async p=>{
 await openWorkspace(p);await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();let output='';
 const row={id:'EMPTY-SYNTHETIC-PTY',handle:access.sessions['dialog-A'].handle,workspace:access.workspace,state:'RUNNING',process_alive:true,input_sequence:0};
 await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[row]}}));
 await p.route('**/api/terminal/output?**',route=>route.fulfill({json:{...row,output,next:output.length,reset:false}}));await p.route('**/api/terminal/resize',route=>route.fulfill({json:{resized:true}}));
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(async()=>await p.getByLabel('原生CLI终端',{exact:true}).getAttribute('data-connected')==='true');
 assert((await p.locator('.terminal-connection-state').innerText()).includes('正在等待 CLI 输出'));assert((await p.locator('.native-terminal>footer').innerText()).includes('正在等待 CLI 输出'));
 output='SYNTHETIC CLI first output';await until(async()=>await p.locator('.terminal-connection-state').count()===0);assert((await p.locator('.native-terminal>footer').innerText()).includes('键盘输入'));
});
for(const kind of ['workflow','chat'])await test(`product-${kind}-revoked-connection-needs-labelled-exact-retry`,async p=>{
 await openWorkspace(p);const starts=[],permits=[];let probes=0;
 p.on('request',r=>{if(r.url().includes('/api/model-admission/'))probes++});
 await p.route(kind==='workflow'?'**/api/prepare':'**/api/chat/prepare',async route=>{const res=await route.fetch(),v=await res.json();assert.equal(res.status(),200,JSON.stringify(v));permits.push(v);await route.fulfill({response:res,json:v})});
 await p.route(kind==='workflow'?'**/api/start':'**/api/chat/start',route=>{starts.push(route.request().postDataJSON());return route.fulfill({status:starts.length===1?409:400,json:{error:starts.length===1?'Synthetic changed scope; request rejected':'Synthetic explicit retry failed'}})});
 if(kind==='workflow')await p.getByLabel('本次任务',{exact:true}).fill('Synthetic task with changed scope');
 else{await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');await p.getByLabel('消息',{exact:true}).fill('Synthetic task with changed scope')}
 const button=p.getByRole('button',{name:kind==='workflow'?'开始运行':'发送',exact:true});await button.click();await p.locator('.alert').waitFor();assert((await p.locator('.alert').innerText()).includes('Synthetic changed scope'));await delay(250);assert.equal(starts.length,1);assert.equal(probes,0);assert.deepEqual(starts[0].answer,{action:kind==='workflow'?'start':'send'});
 await button.click();await until(()=>starts.length===2);await until(async()=>(await p.locator('.alert').innerText()).includes('Synthetic explicit retry failed'));await delay(200);assert.equal(starts.length,2);assert.equal(permits.length,2);assert.notEqual(permits[0].id,permits[1].id);assert.equal(probes,0);
});
await test('product-workflow-unknown-response-does-not-grant-retry-or-replay',async p=>{
 await openWorkspace(p);let checks=0,starts=0;p.on('request',r=>{if(r.url().includes('/api/model-admission/'))checks++});
 await p.route('**/api/start',route=>{starts++;return route.abort('failed')});
 await p.getByLabel('本次任务',{exact:true}).fill('Synthetic unknown response');await p.getByRole('button',{name:'开始运行',exact:true}).click();await p.locator('.alert').waitFor();await delay(500);assert.equal(checks,0);assert.equal(starts,1);assert.equal(await p.getByRole('button',{name:'重试连接并开始运行',exact:true}).count(),0);assert.equal(await p.getByLabel('本次授权确认意见').count(),0);
});
await test('product-terminal-revoked-scope-needs-explicit-retry-open',async p=>{
 await openWorkspace(p);await p.evaluate(()=>localStorage.removeItem('devflower-terminal-auto-open'));await p.locator('.session-main').filter({hasText:'Synthetic dialog-A'}).click();const starts=[];let probes=0;
 p.on('request',r=>{if(r.url().includes('/api/model-admission/'))probes++});await p.route('**/api/terminals?**',route=>route.fulfill({json:{terminals:[]}}));
 await p.route('**/api/terminal/start',route=>{starts.push(route.request().postDataJSON());return route.fulfill({status:starts.length===1?409:400,json:{error:starts.length===1?'Synthetic terminal scope changed':'Synthetic terminal retry failed'}})});
 await p.getByRole('button',{name:'原生终端',exact:true}).click();await until(()=>starts.length===1);await until(async()=>(await p.locator('.native-terminal').innerText()).includes('Synthetic terminal scope changed'));await delay(250);assert.equal(starts.length,1);assert.equal(probes,0);assert.deepEqual(starts[0].answer,{action:'open-terminal'});
 await p.getByRole('button',{name:'重新打开',exact:true}).click();await until(()=>starts.length===2);await until(async()=>(await p.locator('.native-terminal').innerText()).includes('Synthetic terminal retry failed'));await delay(200);assert.equal(starts.length,2);assert.notEqual(starts[0].id,starts[1].id);assert.equal(probes,0);
});


// R1 ownership: persisted legacy fixture bytes plus ordinary Chromium/real HTTP.
await test('r1-two-workspaces-cannot-delete-rebind-or-own-a-legacy-draft',async p=>{
 await openWorkspace(p);const path=require('path'),{spawnSync}=require('child_process'),headers={Authorization:'Bearer '+access.token};
 const graph=JSON.parse(JSON.stringify((await (await p.request.get(access.url+'/api/status',{headers})).json()).examples[0]));graph.name='R1 未分配旧工作流';
 const dbPath=path.join(path.dirname(access.path),'session-studio','workbench.sqlite');assert(fs.existsSync(dbPath),'Only seed the known synthetic browser fixture database');
 const python=process.env.WORKBENCH_TEST_PYTHON||'python3';
 const seeded=spawnSync(python,['-B','-c',`import sys,json,sqlite3,hashlib
p=sys.argv[1];g=json.load(sys.stdin);raw=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
v={'id':'r1-legacy-unassigned','workspace':None,'revision':hashlib.sha256(raw(g).encode()).hexdigest(),'graph':g}
with sqlite3.connect(p) as d:
 d.execute("INSERT INTO records VALUES('draft',?,?)",(v['id'],raw(v)))
 d.execute('INSERT INTO drafts VALUES(?,?,?)',(v['id'],v['revision'],raw(v)))
print(raw(v))`,dbPath],{input:JSON.stringify(graph),encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 assert.ifError(seeded.error);assert.equal(seeded.status,0,seeded.stderr);const legacy=JSON.parse(seeded.stdout),bPath=path.join(access.path,'r1-workspace-B');fs.mkdirSync(bPath,{recursive:true});fs.writeFileSync(path.join(bPath,'README.md'),'Synthetic second workspace; not a model task');
 const bResponse=await p.request.post(access.url+'/api/workspace',{headers,data:{path:bPath}});assert.equal(bResponse.status(),200);const b=await bResponse.json();assert.notEqual(b.id,access.workspace);
 const outcomes=[];
 for(const workspace of [access.workspace,b.id]){
  const deleted=await p.request.post(access.url+'/api/draft/delete',{headers,data:{workspace,id:legacy.id,expected:legacy.revision}});outcomes.push({operation:'delete-legacy',workspace,status:deleted.status()});
  fs.writeFileSync(out+'/r1-workspace-http-evidence.json',JSON.stringify(outcomes,null,2));assert.equal(deleted.status(),409,'An unproved legacy owner cannot be replaced by the current workspace');
  const rebound=await p.request.post(access.url+'/api/draft',{headers,data:{workspace,id:legacy.id,expected:legacy.revision,graph}});assert.equal(rebound.status(),409);
 }
 const omitted=await p.request.post(access.url+'/api/draft',{headers,data:{id:legacy.id,expected:legacy.revision,graph}});assert.equal(omitted.status(),400,'Omitting workspace must not expose legacy mutation');
 const post=async(workspace,name)=>{const r=await p.request.post(access.url+'/api/draft',{headers,data:{workspace,graph:{...graph,name}}});assert.equal(r.status(),200);return r.json()};
 const ownedB=await post(b.id,'R1 scoped B'),ownedA=await post(access.workspace,'R1 scoped A');
 for(const operation of ['delete','save']){const r=await p.request.post(access.url+(operation==='delete'?'/api/draft/delete':'/api/draft'),{headers,data:{workspace:b.id,id:ownedA.id,expected:ownedA.revision,...(operation==='save'?{graph:ownedA.graph}:{})}});assert.equal(r.status(),409)}
 await p.reload();await p.getByRole('button',{name:'打开文件夹',exact:true}).waitFor();const navigation=p.locator('.workspace-workflows');
 assert.equal(await navigation.getByRole('button',{name:'切换至工作流 R1 scoped B',exact:true}).count(),0);assert.equal(await navigation.getByRole('button',{name:'删除工作流 R1 scoped B',exact:true}).count(),0);
 assert(await navigation.getByRole('button',{name:'切换至工作流 R1 scoped A',exact:true}).isVisible());assert.equal(await p.getByRole('button',{name:'删除工作流 R1 未分配旧工作流',exact:true}).count(),0);
 await navigation.getByRole('button',{name:'切换至工作流 R1 scoped A',exact:true}).click();if(await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='R1 scoped A');
 await navigation.locator('.legacy-drafts>summary').click();assert((await navigation.locator('.legacy-drafts').innerText()).includes('尚未证明工作区归属'));
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();await p.getByLabel('目录路径',{exact:true}).fill(bPath);await p.getByRole('button',{name:'前往',exact:true}).click();await until(async()=>(await p.locator('.folder-breadcrumb').innerText()).includes('r1-workspace-B'));await p.getByRole('button',{name:'选择此文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor({state:'hidden'});
 assert.equal(await navigation.getByRole('button',{name:'切换至工作流 R1 scoped A',exact:true}).count(),0);assert.equal(await navigation.getByRole('button',{name:'删除工作流 R1 scoped A',exact:true}).count(),0);assert(await navigation.getByRole('button',{name:'切换至工作流 R1 scoped B',exact:true}).isVisible());assert.notEqual(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'R1 scoped A','Changing workspace must not display the previous scoped draft as the new workspace canvas');
 const legacyDetails=navigation.locator('.legacy-drafts');if(await legacyDetails.getAttribute('open')===null)await legacyDetails.locator('summary').click();
 await legacyDetails.getByRole('button',{name:'在当前工作区新建副本 R1 未分配旧工作流',exact:true}).click();if(await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();
 await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()===graph.name);assert((await p.locator('.notice').innerText()).includes('新副本'));
 const saved=p.waitForResponse(r=>r.url().endsWith('/api/draft')&&r.request().method()==='POST');await p.getByRole('button',{name:'保存',exact:true}).click();const save=await saved;assert.equal(save.status(),200);const copy=await save.json();assert.notEqual(copy.id,legacy.id);assert.equal(copy.workspace,b.id);assert.equal(save.request().postDataJSON().id,undefined);
 const status=await (await p.request.get(access.url+'/api/status',{headers})).json();assert.deepEqual(status.drafts.find(d=>d.id===legacy.id),legacy);assert.deepEqual(status.drafts.find(d=>d.id===ownedA.id),ownedA,'A missing: '+JSON.stringify({project_errors:status.project_errors,drafts:status.drafts.map(d=>({id:d.id,workspace:d.workspace}))}));assert.deepEqual(status.drafts.find(d=>d.id===ownedB.id),ownedB);
 const preserved=spawnSync(python,['-B','-c',`import sys,json,sqlite3
with sqlite3.connect(sys.argv[1]) as d:
 row=d.execute("SELECT data FROM drafts WHERE id=? AND revision=?",(sys.argv[2],sys.argv[3])).fetchone()
 assert row
 print(row[0])`,dbPath,legacy.id,legacy.revision],{encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});assert.ifError(preserved.error);assert.equal(preserved.status,0,preserved.stderr);assert.deepEqual(JSON.parse(preserved.stdout),legacy);
 fs.writeFileSync(out+'/r1-workspace-closure.json',JSON.stringify({outcomes,original:legacy,scoped_A:ownedA.id,scoped_B:ownedB.id,explicit_new_copy:copy.id,copy_workspace:copy.workspace,legacy_revision_preserved:true},null,2));
});
await test('r1-handoff-trace-keeps-exact-targets-and-never-guesses-unknown',async p=>{
 await p.setViewportSize({width:1512,height:1080});await openWorkspace(p);await p.route('**/api/run?**',async route=>{const response=await route.fetch(),v=await response.json();if(v.run.id==='A'){
  const input={edge:'edge1',activation:'A',run:'A'};
  v.nodes=[...v.nodes,{id:'first-target',run:'A',node:'review',name:'第一个接收回合',kind:'agent',state:'COMPLETED',iteration:0,iteration_path:[],attempt:1,inputs:[input]},{id:'second-target',run:'A',node:'review',name:'第二个接收回合',kind:'agent',state:'COMPLETED',iteration:0,iteration_path:[],attempt:1,inputs:[input]}];
  const e={at:1,run:'A',edge:'edge1',source:'analyze',node:'review',activation:'A',target_round:[],iteration_path:[],attempt:1,content_sha256:'a'.repeat(64),content:{answer:'READABLE_R1_HANDOFF'}};
  v.events=[{...e,seq:1,kind:'handoff-ready',target_activation:'first-target'},{...e,seq:2,kind:'handoff-sent',target_activation:'first-target'},{...e,seq:3,kind:'handoff-sent',target_activation:'second-target'},{...e,seq:4,kind:'handoff-ready',target_activation:'second-target'},{...e,seq:5,kind:'handoff-sent',target_name:'未确定接收方'}];
 }await route.fulfill({json:v})});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='运行 A');await settleCanvas(p);const path=p.locator('.react-flow__edge[data-id="edge1"] .react-flow__edge-path');const point=await path.evaluate(e=>{const q=e.getPointAtLength(e.getTotalLength()/2).matrixTransform(e.getScreenCTM());return{x:q.x,y:q.y}});await p.mouse.click(point.x,point.y);
 const cards=p.getByTestId('handoff-trace').locator(':scope>details');await until(async()=>await cards.count()===3);
 const identities=[];for(let i=0;i<3;i++){const card=cards.nth(i);assert((await card.innerText()).includes('READABLE_R1_HANDOFF'));assert((await card.locator(':scope>summary').innerText()).includes('已完整发送'));await card.getByText('高级：字段、时间与运行身份',{exact:true}).click();const id=await card.locator('dt').filter({hasText:'目标Activation'}).locator('xpath=following-sibling::dd[1]').innerText();identities.push(id.trim())}
 assert.deepEqual(identities,['first-target','second-target','尚无对应激活']);assert((await cards.nth(0).locator('.handoff-route').innerText()).includes('第一个接收回合'));assert((await cards.nth(1).locator('.handoff-route').innerText()).includes('第二个接收回合'));assert((await cards.nth(2).locator('.handoff-route').innerText()).includes('未确定接收方'));
 fs.writeFileSync(out+'/r1-handoff-target-evidence.json',JSON.stringify({identities,readable_cards:3,sent_state_preserved:true},null,2));
});
await test('r1-overview-displays-provider-cost-partial-zero-and-unknown-with-provenance',async p=>{
 await openWorkspace(p);let phase=0;
 await p.route('**/api/run?**',async route=>{const response=await route.fetch(),v=await response.json();if(v.run.id==='A'){
  const row=(id,at,cost,semantics='turn',scope=id)=>({id,run:'A',node:'analyze',name:'合成费用回合',kind:'agent',state:'COMPLETED',finished_at:at,output:{receipt:{usage_scope:scope,usage:{semantics,usage:{input_tokens:10,output_tokens:5},total_cost_usd:cost}}}});
  v.nodes=phase===0?[row('old',1,.125,'session-cumulative-do-not-sum','same-session'),row('latest',2,.25,'session-cumulative-do-not-sum','same-session'),row('other',3,.5,'session-cumulative-do-not-sum','other-session'),row('unknown',4,'unknown')]:[row('single',5,phase===1?0:'unknown')];v.run.actual_calls=phase===0?4:1;v.run.connection_checks=phase===0?2:0;
 }await route.fulfill({json:v})});
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');const usage=p.getByLabel('实际调用与用量',{exact:true});await until(async()=>(await usage.innerText()).includes('USD 0.75（部分报告）'));let text=await usage.innerText();assert(text.includes('Provider 报告费用（非核验账单）'));assert(text.includes('任务模型调用 4 次 · 运行中连接检查 2 次'));assert(text.includes('输入 30 / 输出 15'));assert(!text.includes('费用：未提供可核验账单'));
 phase=1;await until(async()=>await usage.locator('span').last().innerText()==='Provider 报告费用（非核验账单）：USD 0');text=await usage.innerText();assert(!text.includes('部分报告'));assert(text.includes('任务模型调用 1 次 · 运行中连接检查 0 次'));
 phase=2;await until(async()=>(await usage.innerText()).includes('Provider 未报告费用'));assert(!(await usage.innerText()).includes('USD 0'));
 fs.writeFileSync(out+'/r1-cost-display-evidence.json',JSON.stringify({partial_reported_usd:.75,zero_reported_usd:0,unknown_not_invented:true,provenance:'Provider 报告费用（非核验账单）'},null,2));
});


await test('sc-direct-session-actions-and-escape-keep-native-history',async p=>{
 await openWorkspace(p);await p.locator('.session-menu').first().click();const modal=p.locator('.lifecycle');await modal.getByRole('heading',{name:'会话管理',exact:true}).waitFor();assert.equal(await modal.locator('select').count(),0);for(const name of ['重命名','归档到工作台','移入回收站'])assert(await modal.getByRole('button',{name:new RegExp('^'+name)}).isVisible());await modal.getByRole('button',{name:/^重命名/}).click();assert(await modal.getByLabel('会话显示名称',{exact:true}).isVisible());await p.keyboard.press('Escape');assert.equal(await p.locator('.lifecycle').count(),0);assert(await p.locator('.session-main').count()>0);
});
await test('sc-workflow-and-node-groups-collapse-persist-and-restore',async p=>{
 await openWorkspace(p);await p.getByLabel('会话分组方式',{exact:true}).selectOption('workflow');const title=p.locator('.session-group-title').first(),group=title.locator('xpath=../..');await title.waitFor();const cards=await group.locator('.session-main').count();assert(cards>0);await title.click();assert.equal(await title.getAttribute('aria-expanded'),'false');assert.equal(await group.locator('.session-main').count(),0);await p.reload();await p.locator('.session-group-title').first().waitFor();assert.equal(await p.locator('.session-group-title').first().getAttribute('aria-expanded'),'false');await p.locator('.session-group-title').first().click();const node=p.locator('.session-node-title').first();await node.click();assert.equal(await node.getAttribute('aria-expanded'),'false');await node.click();assert(await p.locator('.session-main').count()>0);
});
await test('sc-ai-external-proposal-applies-to-draft-only-with-undo',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 配置',exact:true}).click();const dialog=p.getByTestId('assistant-dock');await dialog.waitFor();const plan={version:1,name:'外部AI建议画布',summary:'Synthetic external data; no real model claim',nodes:[['start','start'],['assistant','agent'],['result','result']].map(([id,kind])=>({id,kind,name:id,profile:kind==='agent'?'default-codex':'',model:kind==='agent'?'test-model':'',effort:kind==='agent'?'high':'',mode:'Ask',initial_prompt:'理解目标',round_prompt:'简洁回答',handoff_prompt:'明确交接'})),edges:[{source:'start',target:'assistant'},{source:'assistant',target:'result'}]};let calls=0;p.on('request',r=>{if(/\/api\/(start|chat\/start|config-assistant\/start)$/.test(r.url()))calls++});await dialog.locator('input[type=file][accept="application/json,.json"]').setInputFiles({name:'proposal.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(plan))});await dialog.getByRole('button',{name:'应用到画布草稿（可撤销）',exact:true}).waitFor();await dialog.getByRole('button',{name:'应用到画布草稿（可撤销）',exact:true}).click();await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='外部AI建议画布');assert.equal(calls,0);assert.equal(await p.locator('.react-flow__node').count(),3);await p.getByRole('button',{name:'撤销',exact:true}).click();assert.notEqual(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'外部AI建议画布');
});
await test('sc-ai-invalid-proposal-is-rejected-without-calling-model',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 配置',exact:true}).click();const dialog=p.getByTestId('assistant-dock');await dialog.locator('input[type=file][accept="application/json,.json"]').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({version:1,endpoint:'https://untrusted.example',command:'unsafe'}))});await dialog.getByRole('alert').waitFor();assert.equal(await dialog.getByRole('button',{name:'应用到画布草稿（可撤销）',exact:true}).count(),0);await p.keyboard.press('Escape');assert.equal(await dialog.isVisible(),false);
});
await test('sc-discovered-model-configures-in-ui-without-authorizing-execution',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'新建会话',exact:true}).click();await p.getByLabel('Agent 工具',{exact:true}).selectOption('default-codex');await p.getByRole('button',{name:'刷新模型',exact:true}).waitFor();await p.getByRole('button',{name:'刷新模型',exact:true}).click();await until(async()=>await p.getByRole('button',{name:'刷新模型',exact:true}).isEnabled());await p.getByLabel('模型',{exact:true}).selectOption('synthetic-configurable-model');await until(async()=>await p.getByRole('button',{name:'配置此模型',exact:true}).count()===0);await until(async()=>await p.getByLabel('模型',{exact:true}).inputValue()==='synthetic-configurable-model');assert.equal(await p.getByRole('button',{name:'配置并选择',exact:true}).count(),0);await until(async()=>await p.getByLabel('模型',{exact:true}).isEnabled());const strengths=await p.getByLabel('推理强度',{exact:true}).locator('option').allTextContents();assert(strengths.includes('high')&&strengths.includes('low'),'A saved choice must not hide native supported efforts');await p.getByLabel('推理强度',{exact:true}).selectOption('low');await until(async()=>await p.getByLabel('推理强度',{exact:true}).inputValue()==='low');await p.locator('.model-connection-details>summary').click();assert((await p.locator('.model-connection-details').innerText()).includes('目录不是调用成功证明'));
});
await test('sc-workflow-batch-trash-confirm-cancel-and-preserve-native-records',async p=>{
 await openWorkspace(p);await p.getByLabel('会话分组方式',{exact:true}).selectOption('workflow');await p.locator('.session-main').first().waitFor();const count=await p.locator('.session-main').count();const target=p.locator('.group-trash').first();await target.waitFor();await target.click();const modal=p.getByRole('dialog',{name:'清理工作流对话',exact:true});await modal.waitFor();assert((await modal.innerText()).includes('不删除原生历史'));await modal.getByRole('button',{name:'取消',exact:true}).click();assert.equal(await p.locator('.session-main').count(),count);await target.click();await modal.getByRole('button',{name:'确认移入回收站',exact:true}).click();await until(async()=>await p.locator('.session-main').count()<count);await p.getByRole('button',{name:'回收站',exact:true}).click();await until(async()=>await p.locator('.session-main').count()>0);assert(await p.locator('.session-main').count()>0);
});
await test('sc-model-refresh-entry-remains-visible-during-slow-load',async p=>{
 await openWorkspace(p);let release,seen=0;const held=new Promise(r=>release=r);
 await p.route('**/api/models',async route=>{const response=await route.fetch();seen++;await held;await route.fulfill({response})});
 try{
  await p.getByRole('button',{name:'新建会话',exact:true}).click();await until(()=>seen>0);
  const refresh=p.getByRole('button',{name:'刷新模型',exact:true});
  assert.equal(await refresh.count(),1,'Refreshing must keep a stable, visible action name');assert(await refresh.isVisible());assert(await refresh.isDisabled());assert.equal(await refresh.getAttribute('aria-busy'),'true');assert(await p.getByRole('status',{name:'模型读取状态',exact:true}).isVisible());
  release();await until(async()=>await refresh.isEnabled());assert.equal(await refresh.getAttribute('aria-busy'),'false');
 }finally{release()}
});
await test('builtin-assistant-chat-updates-canvas-and-retains-dialog',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 配置',exact:true}).click();const panel=p.getByTestId('assistant-dock');await panel.getByLabel('助手模型',{exact:true}).selectOption('auto');await panel.getByLabel('助手工作方式',{exact:true}).selectOption('workflow');await panel.locator('.assistant-options>summary').click();await panel.getByRole('checkbox',{name:/设计工作流模式生成后自动应用/}).check();assert.equal(await panel.getByLabel('Agent 工具',{exact:true}).count(),0);await panel.getByRole('checkbox',{name:/允许发送.*对话/}).check();await panel.getByLabel('配置需求',{exact:true}).fill('创建只读顺序分析');let starts=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))starts++});await panel.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='Synthetic 创建只读顺序分析');assert((await panel.getByRole('log').innerText()).includes('SYNTHETIC 画布已设计'));assert.equal(starts,1);await p.getByRole('button',{name:'查看节点工具库',exact:true}).click();await p.getByRole('button',{name:'AI 配置',exact:true}).click();await until(async()=>(await p.getByRole('log').innerText()).includes('创建只读顺序分析'));assert.equal(starts,1,'Reopening must not replay model inference');await p.screenshot({path:out+'/builtin-assistant-chat.png'});await p.keyboard.press('Escape');await p.getByRole('button',{name:'撤销',exact:true}).click();assert.notEqual(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'Synthetic 创建只读顺序分析');
});
await test('builtin-assistant-user-edit-during-inference-is-not-overwritten',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 配置',exact:true}).click();const panel=p.getByTestId('assistant-dock');await panel.getByLabel('助手模型',{exact:true}).selectOption('auto');await panel.getByLabel('助手工作方式',{exact:true}).selectOption('workflow');await panel.locator('.assistant-options>summary').click();await panel.getByRole('checkbox',{name:/设计工作流模式生成后自动应用/}).check();let release;const hold=new Promise(r=>release=r);await p.route('**/api/canvas-assistant/conversation?*',async route=>{const response=await route.fetch();const body=await response.json();if(body.turn?.state==='COMPLETED')await hold;await route.fulfill({response})});try{await panel.getByRole('checkbox',{name:/允许发送.*对话/}).check();await panel.getByLabel('配置需求',{exact:true}).fill('生成过程中保留编辑');await panel.getByRole('button',{name:'发送',exact:true}).click();await p.getByLabel('工作流名称',{exact:true}).fill('用户手动保留的名称');release();await until(async()=>await panel.getByRole('alert').count()>0);assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),'用户手动保留的名称');assert((await panel.getByRole('alert').first().innerText()).includes('修改'));}finally{release()}
});

await test('polish-network-warning-clears-after-same-run-recovers-and-keeps-success-notice',async p=>{
 await openWorkspace(p);let offline=true;
 await p.route('**/api/run?*',r=>offline?r.abort('connectionfailed'):r.continue());
 await p.getByLabel('历史运行',{exact:true}).selectOption('A');await p.locator('.connection-warning').waitFor();
 assert((await p.locator('.connection-warning').innerText()).includes('正在重新连接'));
 assert(!(await p.locator('.notice').allInnerTexts()).join('').includes('Failed to fetch'));
 await p.screenshot({path:out+'/network-warning.png'});
 offline=false;await p.locator('.connection-warning').waitFor({state:'hidden'});
 assert.equal(await p.getByLabel('历史运行',{exact:true}).inputValue(),'A');
 assert(!(await p.locator('.notice').allInnerTexts()).join('').includes('Failed to fetch'));
 await p.screenshot({path:out+'/network-reconnected.png'});
});
await test('polish-focus-canvas-expands-readable-graph-without-changing-workflow',async p=>{
 await openWorkspace(p);await until(async()=>await p.locator('.react-flow__node').count()===4);
 const before=await p.getByTestId('canvas').boundingBox(),nodeBefore=await p.locator('.react-flow__node').first().boundingBox();
 const writes=[];p.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|control|canvas-assistant\/messages)$/.test(r.url()))writes.push(r.url())});
 await p.getByRole('button',{name:'专注画布',exact:true}).click();
 await until(async()=>{const c=await p.getByTestId('canvas').boundingBox(),n=await p.locator('.react-flow__node').first().boundingBox();return c.width>before.width*1.5&&n.width>nodeBefore.width*1.4});
 assert.equal(await p.locator('.react-flow__node').count(),4);assert.equal(await p.locator('.react-flow__edge-path').count(),3);
 await p.screenshot({path:out+'/focused-readable-canvas.png'});
 await p.getByRole('button',{name:'退出专注画布',exact:true}).click();await until(async()=>Math.abs((await p.getByTestId('canvas').boundingBox()).width-before.width)<3);
 assert.deepEqual(writes,[]);
});
await test('polish-small-runtime-assistant-opens-readonly-and-closes-with-draft-intact',async p=>{
 await p.setViewportSize({width:700,height:860});await openWorkspace(p);await p.getByLabel('历史运行',{exact:true}).selectOption('A');
 assert(await p.getByRole('button',{name:'AI 配置',exact:true}).isEnabled());
 const writes=[];p.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|control|canvas-assistant\/messages)$/.test(r.url()))writes.push(r.url())});
 await p.getByRole('button',{name:'AI 配置',exact:true}).click();let dock=p.getByTestId('assistant-dock');await dock.waitFor();
 assert.equal(await dock.getByLabel('助手工作方式',{exact:true}).inputValue(),'chat');
 await dock.getByLabel('配置需求',{exact:true}).fill('解释这次运行，请保留我的草稿。');
 await p.screenshot({path:out+'/small-runtime-assistant.png'});
 await p.getByRole('button',{name:'收起工作台侧栏',exact:true}).click();assert(!(await p.getByTestId('assistant-dock').isVisible()));
 await p.getByRole('button',{name:'AI 配置',exact:true}).click();dock=p.getByTestId('assistant-dock');
 assert.equal(await dock.getByLabel('配置需求',{exact:true}).inputValue(),'解释这次运行，请保留我的草稿。');
 assert.equal(await p.getByLabel('历史运行',{exact:true}).inputValue(),'A');assert.deepEqual(writes,[]);
});
await test('polish-saved-assistant-model-is-ready-before-slow-catalog-and-omitted-default',async p=>{
 let release;const hold=new Promise(r=>release=r);
 await p.route('**/api/devflower-agent/config',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({configured:true,protocol:'openai',base_url:'https://example.invalid',model:'saved-deepseek-model',effort:'high',fingerprint:'synthetic',has_credentials:true})}));
 await p.route('**/api/assistant/models*',async r=>{await hold;await r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({models:[{id:'another-model'}],default_model:'saved-deepseek-model',source:'SYNTHETIC catalog omits default'})})});
 try{
  await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');
  await until(async()=>await dock.getByLabel('助手模型',{exact:true}).inputValue()==='saved-deepseek-model');
  await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();await dock.getByLabel('配置需求',{exact:true}).fill('我可以在目录刷新完成前使用已保存的模型');
  assert(await dock.getByRole('button',{name:'发送',exact:true}).isEnabled());assert((await dock.innerText()).includes('可先使用已保存的模型'));
  release();await until(async()=>await dock.getByLabel('助手模型',{exact:true}).getByRole('option').count()===2);
  assert.equal(await dock.getByLabel('助手模型',{exact:true}).inputValue(),'saved-deepseek-model');
 }finally{release()}
});
await test('deepseek-settings-and-model-choice-survive-reopen-without-exposing-key',async p=>{
 await p.route('**/api/assistant/models*',async route=>{
  const response=await p.request.get(access.url+'/api/devflower-agent/config',{headers:{Authorization:'Bearer '+access.token}}),config=await response.json();
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({models:[{id:'synthetic-deepseek-one'},{id:'synthetic-deepseek-two'}],default_model:config.configured?config.model:'synthetic-deepseek-one',source:'SYNTHETIC catalog; no inference'})});
 });
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();
 await p.getByLabel('助手模型设置',{exact:true}).click();const settings=p.getByLabel('助手模型连接',{exact:true});
 await settings.getByLabel('助手模型ID',{exact:true}).fill('synthetic-deepseek-one');
 await settings.getByLabel('助手API协议',{exact:true}).selectOption('openai');
 await settings.getByLabel('模型服务地址',{exact:true}).fill('http://127.0.0.1:1');
 await settings.getByLabel('助手推理强度',{exact:true}).selectOption('medium');
 await settings.getByLabel('助手API密钥',{exact:true}).fill('SYNTHETIC_PERSISTENCE_SECRET');
 await settings.getByRole('button',{name:'保存连接',exact:true}).click();
 let dock=p.getByTestId('assistant-dock');await until(async()=>await dock.getByLabel('助手模型',{exact:true}).inputValue()==='synthetic-deepseek-one');
 await dock.getByLabel('助手模型',{exact:true}).selectOption('synthetic-deepseek-two');
 await until(async()=>(await dock.innerText()).includes('助手模型已保存'));
 await p.reload();await p.getByRole('button',{name:'AI 助手',exact:true}).click();dock=p.getByTestId('assistant-dock');
 await until(async()=>await dock.getByLabel('助手模型',{exact:true}).inputValue()==='synthetic-deepseek-two');
 await p.getByLabel('助手模型设置',{exact:true}).click();const reopened=p.getByLabel('助手模型连接',{exact:true});
 assert.equal(await reopened.getByLabel('助手模型ID',{exact:true}).inputValue(),'synthetic-deepseek-two');
 assert.equal(await reopened.getByLabel('助手推理强度',{exact:true}).inputValue(),'medium');
 assert.equal(await reopened.getByLabel('模型服务地址',{exact:true}).inputValue(),'http://127.0.0.1:1');
 assert.equal(await reopened.getByLabel('助手API密钥',{exact:true}).inputValue(),'');
 assert((await reopened.getByLabel('助手API密钥',{exact:true}).getAttribute('placeholder')).includes('已保存'));
 assert(!(await p.content()).includes('SYNTHETIC_PERSISTENCE_SECRET'));
 // Saving a different default must replace the previous selection even when
 // both models remain in the provider catalog.
 await reopened.getByLabel('助手模型ID',{exact:true}).fill('synthetic-deepseek-one');
 await reopened.getByRole('button',{name:'保存连接',exact:true}).click();
 await until(async()=>await p.getByTestId('assistant-dock').getByLabel('助手模型',{exact:true}).inputValue()==='synthetic-deepseek-one');

});
await test('dock-assistant-is-inline-retains-input-and-never-creates-a-modal',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.waitFor();assert.equal(await p.locator('.canvas-assistant-scrim').count(),0);assert.equal(await p.getByRole('dialog',{name:'AI配置助手',exact:true}).count(),0);assert.equal(await dock.locator('xpath=ancestor::aside[contains(@class,"studio-tools")]').count(),1);await dock.getByLabel('配置需求',{exact:true}).fill('保留这段未发送需求');await p.getByRole('button',{name:'查看节点工具库',exact:true}).click();assert.equal(await dock.isVisible(),false);await p.getByRole('button',{name:'AI 助手',exact:true}).click();assert.equal(await dock.getByLabel('配置需求',{exact:true}).inputValue(),'保留这段未发送需求');await p.screenshot({path:out+'/assistant-dock-light.png'});await p.getByLabel('外观主题',{exact:true}).selectOption('dark');await p.screenshot({path:out+'/assistant-dock-dark.png'});
});
await test('dock-assistant-two-conversations-rename-trash-and-restore',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();let calls=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))calls++});
 async function send(text){await dock.getByLabel('配置需求',{exact:true}).fill(text);await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('SYNTHETIC 画布已设计：Synthetic '+text));}
 await send('历史甲');await dock.getByLabel('配置需求',{exact:true}).fill('甲的未发送草稿');await dock.getByLabel('新建助手对话',{exact:true}).click();await send('历史乙');await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.getByRole('button',{name:'重命名助手会话 历史甲',exact:true}).waitFor();await dock.getByRole('button',{name:'重命名助手会话 历史甲',exact:true}).click();await dock.getByLabel('助手会话名称',{exact:true}).fill('我的设计');await dock.getByRole('button',{name:'保存名称',exact:true}).click();await dock.getByRole('button',{name:'我的设计 2 条消息',exact:true}).click();assert.equal(await dock.getByLabel('配置需求',{exact:true}).inputValue(),'甲的未发送草稿');await until(async()=>(await dock.getByRole('log').innerText()).includes('历史甲'));
 await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.getByRole('button',{name:'删除助手会话 我的设计',exact:true}).click();await dock.getByRole('button',{name:'移入回收站',exact:true}).click();await until(async()=>await dock.getByRole('button',{name:'删除助手会话 我的设计',exact:true}).count()===0);await dock.getByRole('button',{name:'回收站',exact:true}).click();await dock.getByRole('button',{name:'恢复助手会话 我的设计',exact:true}).click();await dock.getByRole('button',{name:'对话',exact:true}).click();await dock.getByRole('button',{name:'我的设计 2 条消息',exact:true}).waitFor();assert.equal(calls,2,'History management must never trigger inference');
});
await test('dock-assistant-connection-settings-are-a-separate-subview',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();await p.getByLabel('助手模型设置',{exact:true}).click();const settings=p.getByLabel('助手模型连接',{exact:true});await settings.getByLabel('助手模型ID',{exact:true}).waitFor();assert.equal(await p.getByRole('log',{name:'画布助手对话',exact:true}).count(),0);assert.equal(await settings.getByLabel('助手API密钥',{exact:true}).inputValue(),'');assert(await settings.getByRole('button',{name:'返回助手对话',exact:true}).evaluate(e=>e===document.activeElement));await p.keyboard.press('Escape');assert.equal(await settings.count(),0);assert(await p.getByTestId('assistant-dock').isVisible());assert(await p.getByLabel('助手模型设置',{exact:true}).evaluate(e=>e===document.activeElement));
});
await test('dock-assistant-small-screen-has-a-usable-composer',async p=>{
 await p.setViewportSize({width:700,height:860});await openWorkspace(p);await p.getByRole('button',{name:'AI 配置',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.waitFor();const box=await dock.boundingBox(),composer=await dock.locator('.assistant-composer').boundingBox();assert(box.width>=280&&box.x+box.width<=701);assert(composer.y+composer.height<=860);assert.equal(await p.locator('.canvas-assistant-scrim').count(),0);await p.screenshot({path:out+'/assistant-dock-small.png'});
});
await test('dock-completed-post-response-applies-once-without-losing-busy-result',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('workflow');await dock.locator('.assistant-options>summary').click();await dock.getByRole('checkbox',{name:/设计工作流模式生成后自动应用/}).check();await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();let calls=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))calls++});await p.route('**/api/canvas-assistant/messages',async route=>{const response=await route.fetch();let v=await response.json();await until(async()=>{const reply=await p.request.get(access.url+'/api/canvas-assistant/conversation?workspace='+access.workspace+'&id='+v.id,{headers:{Authorization:'Bearer '+access.token}});v=await reply.json();return v.turn.state==='COMPLETED'});await route.fulfill({response,json:v})});await dock.getByLabel('配置需求',{exact:true}).fill('响应已完成的快速结果');await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>await p.getByLabel('工作流名称',{exact:true}).inputValue()==='Synthetic 响应已完成的快速结果');assert.equal(calls,1);
});
await test('dock-unknown-owner-offers-stop-before-another-message',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.getByRole('button',{name:'Synthetic 请求状态待确认 1 条消息 · 状态待确认',exact:true}).click();await until(async()=>await dock.getByRole('button',{name:'停止本次生成',exact:true}).isVisible());assert(await dock.getByLabel('配置需求',{exact:true}).isDisabled());let calls=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))calls++});await dock.getByRole('button',{name:'停止本次生成',exact:true}).click();await until(async()=>await dock.getByLabel('配置需求',{exact:true}).isEnabled());assert.equal(calls,0);
});
await test('folder-common-locations-parent-and-visible-address',async p=>{
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();const dialog=p.getByRole('dialog',{name:'打开文件夹',exact:true});await dialog.getByLabel('目录路径',{exact:true}).waitFor();assert.equal(await dialog.getByText('高级：直接定位路径',{exact:true}).count(),0);await dialog.getByRole('button',{name:'此电脑',exact:true}).first().click();await until(async()=>await dialog.getByLabel('目录路径',{exact:true}).inputValue()==='/');assert((await dialog.innerText()).includes('位置'));await dialog.getByLabel('目录路径',{exact:true}).fill(access.path);await dialog.getByRole('button',{name:'前往',exact:true}).click();await until(async()=>await dialog.getByLabel('目录路径',{exact:true}).inputValue()===access.path);await dialog.getByRole('button',{name:'选择此文件夹',exact:true}).click();await until(async()=>await dialog.count()===0);assert((await p.locator('.workspace-name').innerText()).includes(access.path));
});
await test('folder-late-load-does-not-replace-new-location',async p=>{
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();const dialog=p.getByRole('dialog',{name:'打开文件夹',exact:true});let release,seen;const hold=new Promise(r=>release=r),started=new Promise(r=>seen=r);await p.route('**/api/folders?**',async route=>{const response=await route.fetch();if(new URL(route.request().url()).searchParams.get('path')==='/tmp'){seen();await hold}await route.fulfill({response})});try{await dialog.getByLabel('目录路径',{exact:true}).fill('/tmp');await dialog.getByRole('button',{name:'前往',exact:true}).click();await started;await dialog.getByRole('button',{name:'此电脑',exact:true}).first().click();await until(async()=>await dialog.getByLabel('目录路径',{exact:true}).inputValue()==='/');release();await delay(200);assert.equal(await dialog.getByLabel('目录路径',{exact:true}).inputValue(),'/')}finally{release()}
});
await test('assistant-general-chat-public-summary-does-not-change-design',async p=>{
 await openWorkspace(p);const before=await p.getByLabel('工作流名称',{exact:true}).inputValue();await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();await dock.getByLabel('配置需求',{exact:true}).fill('解释汇合，不改流程');await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),before);await dock.getByText('模型公开推理',{exact:true}).click();assert((await dock.getByRole('log').innerText()).includes('SYNTHETIC 公开依据'));assert.equal(await dock.getByText('本次画布变更',{exact:false}).count(),0);
});
await test('assistant-source-tool-failure-visible-without-undefined-path',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();await dock.getByLabel('配置需求',{exact:true}).fill('SYNTHETIC-TOOL-ERROR-UI');await dock.getByRole('button',{name:'发送',exact:true}).click();const activity=dock.locator('details.assistant-change').filter({hasText:'公开源码读取活动'});await until(async()=>await activity.count()===1);assert((await activity.locator('summary').innerText()).includes('有失败'));await activity.locator('summary').click();const text=await activity.innerText();assert(text.includes('公开源码读取 · 失败'));assert(text.includes('参数无效'));assert(!text.includes('undefined'));
});
await test('assistant-live-turn-survives-reload-without-resending',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();let dock=p.getByTestId('assistant-dock'),sends=0;
 p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))sends++});
 await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();
 await dock.getByLabel('配置需求',{exact:true}).fill('SYNTHETIC-STREAM-UI');await dock.getByRole('button',{name:'发送',exact:true}).click();
 await until(async()=>(await dock.getByRole('log').innerText()).includes('正在检查工作流配置。'));assert((await dock.getByRole('log').innerText()).includes('部分回复（未完成）'));assert.equal(sends,1);
 await p.reload();await p.getByRole('button',{name:'AI 助手',exact:true}).click();dock=p.getByTestId('assistant-dock');await until(async()=>(await dock.getByRole('log').innerText()).includes('正在检查工作流配置。'));assert.equal(sends,1);
 await until(async()=>(await dock.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));assert.equal(sends,1);
});
await test('favorite-template-reopen-copy-and-recoverable-removal',async p=>{
 await openWorkspace(p);await example(p,1);const title='收藏模板 '+Date.now();await p.getByRole('button',{name:'收藏为模板',exact:true}).click();await p.getByLabel('模板名称',{exact:true}).fill(title);await p.getByLabel('模板说明',{exact:true}).fill('我的并行汇合');await p.getByRole('button',{name:'保存模板',exact:true}).click();await p.locator('.template-card').filter({hasText:title}).waitFor();await p.getByRole('button',{name:'关闭示例库',exact:true}).click();await p.reload();await p.getByRole('button',{name:'示例',exact:true}).click();const card=p.locator('.template-card').filter({hasText:title});await card.waitFor();await card.getByRole('button',{name:/新建草稿副本/}).click();if(await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),title);await p.getByRole('button',{name:'示例',exact:true}).click();await p.getByRole('button',{name:'删除模板 '+title,exact:true}).click();await p.getByRole('button',{name:'回收站',exact:true}).last().click();await p.getByRole('button',{name:'恢复模板 '+title,exact:true}).click();await p.locator('.workflow-library').getByRole('button',{name:'收藏',exact:true}).click();await p.locator('.template-card').filter({hasText:title}).waitFor();assert.equal(await p.locator('.template-card').filter({hasText:title}).count(),1);
});
await test('whole-run-chat-history-refresh-and-next-task-without-replay',async p=>{
 await openWorkspace(p);let starts=0;p.on('request',r=>{if(r.method()==='POST'&&['/api/start','/api/chat/start'].some(x=>r.url().endsWith(x)))starts++});await p.getByRole('button',{name:'整轮对话',exact:true}).click();await p.locator('.journal-task').filter({hasText:'Synthetic structured session dialog-A'}).click();const log=p.getByRole('log',{name:'任务群聊记录',exact:true});await until(async()=>(await log.innerText()).includes('SYNTHETIC dialog-A final answer 2'));assert(!(await log.innerText()).includes('dialog-B final answer'));await p.reload();await p.getByRole('log',{name:'任务群聊记录',exact:true}).waitFor();await until(async()=>(await p.getByRole('log',{name:'任务群聊记录',exact:true}).innerText()).includes('SYNTHETIC dialog-A final answer 2'));const download=p.waitForEvent('download');await p.getByRole('button',{name:'导出记录',exact:true}).click();assert((await download).suggestedFilename().includes('dialog-A-run'));await p.getByRole('button',{name:'用此流程开始下一任务',exact:true}).click();await until(async()=>await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count()>0||await p.getByLabel('本次任务',{exact:true}).isVisible());if(await p.getByRole('dialog',{name:'未保存的草稿',exact:true}).count())await p.getByRole('button',{name:'丢弃草稿并关闭',exact:true}).click();await p.getByLabel('本次任务',{exact:true}).waitFor();assert.equal(await p.getByLabel('本次任务',{exact:true}).inputValue(),'');assert(await p.getByLabel('本次任务',{exact:true}).isEditable());assert.equal(starts,0);const r=await p.request.get(access.url+'/api/run-conversation?workspace='+access.workspace+'&run=dialog-A-run',{headers:{Authorization:'Bearer '+access.token}});assert.equal(r.status(),200);assert.equal((await r.json()).entries.filter(e=>e.kind==='assistant').length,2);
});
await test('whole-run-chat-late-read-and-network-failure-stay-scoped',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'整轮对话',exact:true}).click();let release;const hold=new Promise(r=>release=r);await p.route('**/api/run-conversation?**',async route=>{const response=await route.fetch();if(new URL(route.request().url()).searchParams.get('run')==='dialog-A-run')await hold;await route.fulfill({response})});try{await p.locator('.journal-task').filter({hasText:'Synthetic structured session dialog-A'}).click();await delay(100);await p.locator('.journal-task').filter({hasText:'Synthetic structured session dialog-B'}).click();const log=p.getByRole('log',{name:'任务群聊记录',exact:true});await until(async()=>(await log.innerText()).includes('SYNTHETIC dialog-B final answer 2'));release();await delay(200);assert(!(await log.innerText()).includes('SYNTHETIC dialog-A final answer'));await p.unroute('**/api/run-conversation?**');await p.route('**/api/run-conversation?**',route=>route.abort());await until(async()=>(await p.getByRole('alert').allTextContents()).some(s=>s.includes('状态待确认')));assert.equal(await log.locator('.journal-entry').count(),0)}finally{release()}
});
await test('assistant-catalog-failure-keeps-explicit-connection-model-usable',async p=>{
 await openWorkspace(p);await p.route('**/api/assistant/models*',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'SYNTHETIC catalog unavailable'})}));await p.route('**/api/devflower-agent/config',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({configured:true,model:'explicit-synthetic-default'})}));await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await until(async()=>await dock.getByLabel('助手模型',{exact:true}).inputValue()==='explicit-synthetic-default');await until(async()=>(await dock.getByRole('note').allTextContents()).some(text=>text.includes('不代表完整模型目录')));assert((await dock.innerText()).includes('不代表完整模型目录'));await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();await dock.getByLabel('配置需求',{exact:true}).fill('普通问题');assert(await dock.getByRole('button',{name:'发送',exact:true}).isEnabled());
});
await test('assistant-unseen-old-history-does-not-auto-apply-proposal',async p=>{
 await openWorkspace(p);const graph=await p.evaluate(()=>JSON.parse(localStorage.getItem('devflower-workbench-view')).graph),id='ee'+Date.now().toString(16).padStart(30,'0'),request='ff'+Date.now().toString(16).padStart(30,'0'),headers={Authorization:'Bearer '+access.token};const posted=await p.request.post(access.url+'/api/canvas-assistant/messages',{headers,data:{workspace:access.workspace,conversation:id,request_id:request,expected:null,message:'尚未在此浏览器应用的历史',graph,model:'auto',allow_context:true}});assert.equal(posted.status(),200);await until(async()=>{const r=await p.request.get(access.url+'/api/canvas-assistant/conversation?workspace='+access.workspace+'&id='+id,{headers});return (await r.json()).turn.state==='COMPLETED'});let applies=0;p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/config-assistant/apply'))applies++});await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');await dock.getByLabel('助手会话历史',{exact:true}).click();await dock.getByRole('button',{name:'尚未在此浏览器应用的历史 2 条消息',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('SYNTHETIC 画布已设计'));await delay(200);assert.equal(applies,0);assert.equal(await p.getByLabel('工作流名称',{exact:true}).inputValue(),graph.name);
});
await test('group-chat-hides-routine-events-without-losing-history',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'整轮对话',exact:true}).click();await p.locator('.journal-task').filter({hasText:'Synthetic structured session dialog-A'}).click();const log=p.getByRole('log',{name:'任务群聊记录',exact:true});await until(async()=>await log.locator('.journal-entry.assistant').count()===2);assert.equal(await log.locator('.journal-entry.event').count(),0);await p.getByRole('checkbox',{name:'显示运行事件',exact:true}).check();await until(async()=>await log.locator('.journal-entry.event').count()>0);assert.equal(await log.locator('.journal-entry.assistant').count(),2);await p.getByRole('checkbox',{name:'显示运行事件',exact:true}).uncheck();assert.equal(await log.locator('.journal-entry.event').count(),0);
});
await test('protected-parent-opens-with-records-without-execution',async p=>{
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();const dialog=p.getByRole('dialog',{name:'打开文件夹',exact:true});await dialog.getByLabel('目录路径',{exact:true}).fill(require('node:path').dirname(access.path));await dialog.getByRole('button',{name:'前往',exact:true}).click();await until(async()=>await dialog.getByRole('button',{name:'选择此文件夹',exact:true}).isEnabled());await dialog.getByRole('button',{name:'选择此文件夹',exact:true}).click();await dialog.waitFor({state:'hidden'});assert(!(await p.locator('body').innerText()).includes('Workspace overlaps protected'));await until(async()=>(await p.locator('.workspace-name').innerText()).includes(require('node:path').dirname(access.path)));assert(await p.getByRole('button',{name:'开始运行',exact:true}).isDisabled());
});
await test('folder-hidden-directory-toggle-does-not-grant-execution',async p=>{
 await p.getByRole('button',{name:'打开文件夹',exact:true}).click();await p.getByLabel('显示隐藏文件夹',{exact:true}).check();await p.getByLabel('显示隐藏文件夹',{exact:true}).uncheck();await p.getByRole('button',{name:'取消',exact:true}).click();assert(await p.getByRole('button',{name:'开始运行',exact:true}).isDisabled());
});
await test('agent-native-extensions-selection-is-draft-and-provider-scoped',async p=>{
 const key='a'.repeat(64);await p.route('**/api/agent-extensions?**',r=>r.fulfill({json:{capabilities:{skills:true,subagents:true},skills:[{id:key,name:'SYNTHETIC 检查清单',available:true,origin:'项目',files:2}],notice:'合成 UI 能力，不是原生模型测试'}}));await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();await p.getByRole('checkbox',{name:/SYNTHETIC 检查清单/}).check();await p.getByRole('checkbox',{name:'允许只读子 Agent 协作',exact:true}).check();await p.getByRole('button',{name:'保存',exact:true}).click();await until(async()=>(await p.locator('.notice').innerText()).includes('当前改动已保存'));await p.reload();await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();assert(await p.getByRole('checkbox',{name:'允许只读子 Agent 协作',exact:true}).isChecked());assert(await p.getByRole('checkbox',{name:/SYNTHETIC 检查清单/}).isChecked());
});
await test('assistant-flowchart-upload-is-scoped-and-removable',async p=>{
 await openWorkspace(p);await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');const z=require('node:zlib');function chunk(k,d){const kind=Buffer.from(k),n=Buffer.alloc(4),c=Buffer.alloc(4);n.writeUInt32BE(d.length);c.writeUInt32BE(z.crc32(Buffer.concat([kind,d])));return Buffer.concat([n,kind,d,c])}const header=Buffer.from([0,0,0,1,0,0,0,1,8,2,0,0,0]);const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',z.deflateSync(Buffer.from([0,255,255,255]))),chunk('IEND',Buffer.alloc(0))]);await dock.locator('input[type=file][accept="image/png,image/jpeg"]').setInputFiles({name:'流程图.png',mimeType:'image/png',buffer:png});await dock.getByRole('img',{name:'流程图.png',exact:true}).waitFor();await dock.getByRole('button',{name:'移除图片 流程图.png',exact:true}).click();assert.equal(await dock.getByRole('img',{name:'流程图.png',exact:true}).count(),0);
});
await test('assistant-rejects-non-image-without-model-call',async p=>{
 await openWorkspace(p);const headers={Authorization:'Bearer '+access.token};const r=await p.request.post(access.url+'/api/canvas-assistant/images',{headers,data:{workspace:access.workspace,name:'fake.png',data:Buffer.from('<svg/>').toString('base64')}});assert.equal(r.status(),400);assert((await r.json()).error.includes('PNG'));
});
await test('skill-loading-never-claims-selected-skill-is-unavailable',async p=>{
 const key='b'.repeat(64);let held=false,release;const hold=new Promise(r=>release=r);await p.route('**/api/agent-extensions?**',async route=>{if(held)await hold;await route.fulfill({json:{capabilities:{skills:true,subagents:true},skills:[{id:key,name:'SYNTHETIC 已选择的 Skill',available:true,origin:'项目',files:1}],notice:'合成能力，仅测试 UI'}})});
 try{await openWorkspace(p);await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();await p.getByRole('checkbox',{name:/SYNTHETIC 已选择的 Skill/}).check();await p.getByRole('button',{name:'关闭节点配置',exact:true}).click();held=true;await p.locator('.react-flow__node[data-id="analyze"]').click();await p.getByRole('button',{name:'高级',exact:true}).click();await p.getByRole('button',{name:'读取中…',exact:true}).waitFor();assert.equal(await p.getByText('原选 Skill 当前不可用',{exact:false}).count(),0);release();await p.getByRole('checkbox',{name:/SYNTHETIC 已选择的 Skill/}).waitFor();assert(await p.getByRole('checkbox',{name:/SYNTHETIC 已选择的 Skill/}).isChecked());const box=await p.getByRole('checkbox',{name:/SYNTHETIC 已选择的 Skill/}).boundingBox();assert(box.width>=12&&box.width<=24&&box.height>=12&&box.height<=24,'Skill selection must leave room for a readable label');}finally{release()}
});
await test('model-catalog-expiry-refreshes-before-restoring-effort-options',async p=>{
 await openWorkspace(p,false);let reads=0,refreshes=0;
 await p.route('**/api/models',async route=>{const data=route.request().postDataJSON();const response=await route.fetch();const value=await response.json();if(data.refresh)refreshes++;else{reads++;value.stale=true;value.attempted_at=1;value.updated_at=1;}await route.fulfill({response,json:value})});
 await p.getByRole('button',{name:'新建会话',exact:true}).click();await until(async()=>refreshes===1);await p.getByLabel('模型',{exact:true}).waitFor();assert.equal(reads,1);assert.equal(refreshes,1);assert(await p.getByRole('button',{name:'刷新模型',exact:true}).isEnabled());
});
await test('snapshot-chat-and-history-layout-readonly-assistant-keeps-run-frozen',async p=>{
 await openWorkspace(p);await p.getByLabel('历史运行',{exact:true}).selectOption('A');
 await p.getByRole('button',{name:'AI 助手',exact:true}).click();const dock=p.getByTestId('assistant-dock');
 await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');await dock.getByLabel('助手模型',{exact:true}).selectOption('auto');
 await dock.getByRole('checkbox',{name:/允许发送.*对话/}).check();const calls=[];
 p.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|control|config-assistant\/apply)$/.test(r.url()))calls.push(r.url())});
 await dock.getByLabel('配置需求',{exact:true}).fill('解释运行问题，不修改流程也不启动任务');
 await dock.getByRole('button',{name:'发送',exact:true}).click();await until(async()=>(await dock.getByRole('log').innerText()).includes('普通回答 · 未修改画布'));
 assert.equal(await p.getByLabel('历史运行',{exact:true}).inputValue(),'A');assert.deepEqual(calls,[]);
 await dock.getByLabel('助手工作方式',{exact:true}).selectOption('workflow');assert(await dock.getByLabel('配置需求',{exact:true}).isDisabled());
});
await test('snapshot-chat-and-history-layout-long-titles-stay-in-navigation',async p=>{
 const long='这是一个包含很多中文和很长无空格标识符的工作流任务标题'.repeat(20);
 await p.route('**/api/status',async route=>{const response=await route.fetch(),value=await response.json();value.runs=value.runs.map(r=>({...r,task:long}));await route.fulfill({response,json:value})});
 await openWorkspace(p);await p.getByLabel('历史运行',{exact:true}).selectOption('A');await p.getByRole('button',{name:'整轮对话',exact:true}).click();
 const nav=p.getByLabel('按工作流查看任务',{exact:true});await until(async()=>await nav.locator('.journal-task').count()>0);
 const sizes=await nav.locator('.journal-task').evaluateAll(es=>es.map(e=>({width:e.clientWidth,content:e.scrollWidth,text:e.innerText})));
 assert(sizes.some(e=>e.text.includes(long.slice(0,40))));assert(sizes.every(e=>e.content<=e.width+2),JSON.stringify(sizes));
 assert(await nav.evaluate(e=>e.scrollWidth<=e.clientWidth+2));await p.screenshot({path:out+'/journal-long-titles.png'});
});
await browser.close();writeResults(null,true);console.log(JSON.stringify({tests:results.length,failures:results.filter(r=>!r.passed).length,skipped:0}));process.exitCode=results.some(r=>!r.passed)?1:0;
})().catch(e=>{console.error(String(e));process.exitCode=1;browser?.close();});
