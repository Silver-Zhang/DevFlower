const fs=require('fs'), path=require('path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const output=process.argv[2], access=JSON.parse(fs.readFileSync(path.join(output,'access.json')));
let browser, page;
const pageErrors=[];
const results=[];
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function check(name,fn){try{await fn();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:String(error),notice:await page.textContent('#notice')});console.log('FAIL '+name+': '+error+' · '+await page.textContent('#notice'));await page.screenshot({path:path.join(output,'failure-'+results.length+'.png')});}}
function assert(value,message){if(!value)throw Error(message);}
async function load(){await page.goto(access.url+'#token='+access.token);await page.waitForFunction(()=>document.querySelector('#studio').dataset.ready==='true');}
async function chooseTemplate(kind){if(!(await page.locator('#template-library').evaluate(e=>e.open)))await page.locator('#template-library summary').click();await page.selectOption('#template',kind);}
async function fresh(name){await chooseTemplate(await page.inputValue('#template'));await page.click('[data-tab="workflow"]');const response=page.waitForResponse(r=>r.url().includes('/api/template?'));await page.click('#new-draft');await response;await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='新草稿未保存');await page.fill('#workflow-name',name);}
async function interceptOnce(pattern){
  let release, seen, used=false;
  const reached=new Promise(r=>seen=r), hold=new Promise(r=>release=r);
  const route=async r=>{if(used)return r.continue();used=true;const response=await r.fetch();seen(r.request());await hold;await r.fulfill({response});await page.unroute(pattern,route);};
  await page.route(pattern,route);return {reached,release};
}
async function selectEdge(edge){
 const p=await edge.locator('.edge').evaluate(e=>{for(const f of [.25,.75,.15,.85,.5]){const p=e.getPointAtLength(e.getTotalLength()*f),q=p.matrixTransform(e.getScreenCTM());if(document.elementFromPoint(q.x,q.y)?.closest('.connection')===e.parentElement)return {x:q.x,y:q.y};}throw Error('connection has no selectable visible segment');});
 await page.mouse.click(p.x,p.y);
}
async function dragFirstNode(){
 const rect=page.locator('#canvas .node .node-body').first();await rect.scrollIntoViewIfNeeded();
 const before=await rect.evaluate(e=>{const p=e.ownerSVGElement.createSVGPoint(),b=e.getBBox();p.x=b.x+b.width/2;p.y=b.y+b.height/2;const q=p.matrixTransform(e.getScreenCTM());return {x:q.x-50,y:q.y-25};}), edge=page.locator('#canvas .edge, #canvas line').first();
 const geometry=await edge.evaluate(e=>e.outerHTML);
 await page.mouse.move(before.x+50,before.y+25);await page.mouse.down();
 try{
   await page.mouse.move(before.x+150,before.y+65,{steps:8});
   const after=await rect.evaluate(e=>{const p=e.ownerSVGElement.createSVGPoint(),b=e.getBBox();p.x=b.x+b.width/2;p.y=b.y+b.height/2;const q=p.matrixTransform(e.getScreenCTM());return {x:q.x-50,y:q.y-25};});

   assert(Math.abs(after.x-before.x-100)<3&&Math.abs(after.y-before.y-40)<3,'node does not track pointer in viewport coordinates '+JSON.stringify({before,after}));
   assert(await edge.evaluate(e=>e.outerHTML)!==geometry,'edge stays behind while node moves');
 }finally{await page.mouse.up();}
}
(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>pageErrors.push(String(e)));
 await load();
 await check('blank canvas is immediately editable without choosing a mode',async()=>{assert(await page.locator('#canvas .node').count()===0,'default is not blank');assert(await page.locator('#template').inputValue()==='blank','mandatory template entry');assert(!(await page.locator('#template-library').evaluate(e=>e.open)),'template chooser forces entry');});
 await chooseTemplate('B');await fresh('legacy interaction fixture');
 if(process.env.DRAG_BASELINE==='1'){
   await check('real pointer drag and live edge geometry',dragFirstNode);return;
 }
 await check('static response never embeds browser capability',async()=>{
   const response=await page.request.get(access.url);assert(!(await response.text()).includes(access.token),'token exposed');
   assert((await page.request.get(access.url+'api/status')).status()===403,'unauthenticated API accepted');
 });
 await check('open explicit workspace through HTTP',async()=>{
   await page.locator('.workspace-menu summary').click();await page.fill('#workspace-path',access.source);await page.click('#open-workspace');
   await page.waitForFunction(p=>document.querySelector('#workspace-info').textContent.includes(p),access.source);await page.locator('.workspace-menu summary').click();
 });
 await check('A C blank templates editable and blank execution refused',async()=>{
   for(const mode of ['A','C','blank']){
     await chooseTemplate(mode);await fresh('edited '+mode);
     await page.click('#save');await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
   }
   await page.click('#flow-options');await page.click('#compile');await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('sequential'));
   await page.keyboard.press('Escape');await chooseTemplate('B');
 });
 await check('authorization preview displays bound PR tip repository and fixed assertion contract',async()=>{
   await chooseTemplate('A');await fresh('preview boundary');
   await page.locator('#canvas [data-node="human-1"] .node-body').dblclick();
   await page.fill('#task-title','Preview fixture');await page.fill('#task-body','Change README');
   await page.click('#task-next');await page.fill('#task-scope','README.md');await page.fill('#task-acceptance','README is correct');await page.check('#delivery');
   await page.click('#prepare');await page.locator('#preview h3').waitFor();
   assert(await page.locator('[data-task-step="3"]').isEnabled(),'valid preview unavailable');
   const preview=await page.textContent('#preview');
   for(const expected of ['fixture/project','flow/usable-test','main','#42','f'.repeat(40),'landlock','json-assertion','expected_json'])assert(preview.includes(expected),'preview hides '+expected);
   assert(await page.locator('#preview textarea').count()===1,'human response missing');
   // No authorization is sent; this is a display regression only.
   await page.click('[data-task-step="2"]');await page.uncheck('#delivery');assert(await page.locator('#preview button').count()===0,'stale authorization remained');assert(!(await page.locator('[data-task-step="3"]').isEnabled()),'expired confirmation step is still enabled');
   await page.click('[data-tab="workflow"]');await page.keyboard.press('Escape');await chooseTemplate('B');
 });
 await check('late save A cannot assign identity to new B',async()=>{
   await fresh('race A');const delay=await interceptOnce('**/api/draft');await page.click('#save');await delay.reached;
   await fresh('race B');delay.release();await wait(180);
   const next=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');
   assert((await next).postDataJSON().id===null,'new B inherited A id');
   await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
 });
 await check('edits during save stay dirty',async()=>{
   await fresh('edit before');const delay=await interceptOnce('**/api/draft');await page.click('#save');await delay.reached;
   await page.fill('#workflow-name','edit after');delay.release();
   await page.waitForFunction(()=>document.querySelector('#save-state').textContent.includes('仍有未保存'));
 });
 await check('concurrent new saves reversed preserve active draft',async()=>{
   await fresh('concurrent initial');const delay=await interceptOnce('**/api/draft');await page.click('#save');await delay.reached;
   await page.fill('#workflow-name','concurrent newer');await page.click('#save');
   await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
   delay.release();await wait(200);
   assert(await page.inputValue('#workflow-name')==='concurrent newer','old response rewrote editor');
 });
 await check('node add connect delete use actual graph',async()=>{
   await page.click('#toggle-palette');await page.locator('.palette details summary').click();
   await page.selectOption('#add-kind','verifier');await page.click('#add-node');
   await page.waitForFunction(()=>document.querySelector('#node-config strong')?.textContent.startsWith('verifier · verifier-'));
   const id=(await page.textContent('#node-config strong')).split(' · ')[1],edges=await page.locator('#edges li').count();
   await page.click('#flow-options');if(await page.locator('.connections-panel').getAttribute('open')===null)await page.locator('.connections-panel summary').click();await page.selectOption('#edge-source','verifier');await page.selectOption('#edge-target',id);await page.click('#connect');
   assert(await page.locator('#edges li').count()===edges+1,'edge did not connect');await page.keyboard.press('Escape');
   const count=await page.locator('#canvas .node').count();await page.click('#delete-node');
   assert(await page.locator('#canvas .node').count()===count-1,'node did not delete');
 });
 await check('node plus inserts into the real chain with one undo and redo',async()=>{
   await chooseTemplate('A');await fresh('inline insertion');
   const old=await page.locator('#canvas .node').count();
   await page.locator('[data-node="developer"]').hover();await page.locator('[data-node="developer"] .node-add').click();
   assert((await page.textContent('#palette-hint')).includes('Developer'),'picker lost source context');
   await page.locator('[data-kind="verifier"]').click();
   await page.waitForFunction(n=>document.querySelectorAll('#canvas .node').length===n+1,old);
   const inserted=(await page.textContent('#node-config strong')).split(' · ')[1];
   const request=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');const doc=(await request).postDataJSON().document;
   assert(doc.edges.some(e=>e.source==='developer'&&e.target===inserted&&e.on==='next'),'source not connected to inserted node');
   assert(doc.edges.some(e=>e.source===inserted&&e.target==='verifier'&&e.on==='next'),'original successor lost');
   assert(!doc.edges.some(e=>e.source==='developer'&&e.target==='verifier'&&e.on==='next'),'old edge retained');
   await page.keyboard.press('Escape');await page.click('#undo');assert(await page.locator('#canvas .node').count()===old,'insertion undo not atomic');
   await page.click('#redo');assert(await page.locator('#canvas .node').count()===old+1,'insertion redo lost node');
 });
 await check('edge actions insert and delete with undo without opening forms',async()=>{
   await fresh('edge actions');
   const edge=page.locator('#canvas .connection').filter({has:page.locator('.edge[data-edge="0"]')});await selectEdge(edge);
   await edge.locator('[data-canvas-action="insert"]').click();await page.locator('[data-kind="verifier"]').click();
   await page.waitForFunction(()=>document.querySelector('#node-config strong')?.textContent.startsWith('verifier · verifier-'));
   await page.keyboard.press('Escape');const count=await page.locator('#canvas .connection').count();
   const target=page.locator('#canvas .connection').filter({has:page.locator('.edge[data-edge="0"]')});await selectEdge(target);await target.locator('[data-canvas-action="remove"]').click();
   assert(await page.locator('#canvas .connection').count()===count-1,'edge action did not remove');
   await page.click('#undo');assert(await page.locator('#canvas .connection').count()===count,'edge undo lost topology');
 });
 await check('late contextual add cannot change a newly opened draft',async()=>{
   await fresh('old insertion');await page.locator('[data-node="developer"]').hover();await page.locator('[data-node="developer"] .node-add').click();
   const delay=await interceptOnce('**/api/node?kind=verifier');await page.locator('[data-kind="verifier"]').click();await delay.reached;
   await fresh('new insertion draft');const count=await page.locator('#canvas .node').count();delay.release();await wait(150);
   assert(await page.locator('#canvas .node').count()===count,'late insertion contaminated new draft');
   assert(await page.inputValue('#workflow-name')==='new insertion draft','late insertion rewrote draft');
 });
 await check('blank canvas entry and direct port-to-empty creation preserve graph coordinates',async()=>{
   await chooseTemplate('blank');await fresh('direct creation');await page.getByRole('button',{name:'创建任务入口',exact:true}).click();
   await page.locator('#panel-task').waitFor();await page.keyboard.press('Escape');
   const human=page.locator('#canvas .node').first(),source=await human.getAttribute('data-node');
   const start=await human.locator('.out').boundingBox(),svg=await page.locator('#canvas').boundingBox();
   const x=svg.x+svg.width-320,y=svg.y+svg.height-240;
   await page.mouse.move(start.x+start.width/2,start.y+start.height/2);await page.mouse.down();await page.mouse.move(x,y,{steps:10});await page.mouse.up();
   await page.locator('#palette').waitFor();await page.locator('#palette [data-kind="developer"]').click();
   await page.waitForFunction(()=>document.querySelectorAll('#canvas .node').length===2);
   await page.keyboard.press('Escape');const request=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');const doc=(await request).postDataJSON().document;
   assert(doc.edges.length===1&&doc.edges[0].source===source&&doc.nodes.find(n=>n.id===doc.edges[0].target).type==='developer','drop did not wire new node');
   await page.click('#undo');assert(await page.locator('#canvas .node').count()===1,'direct addition undo not atomic');
 });
 await check('palette native drag drops a node and single click does not open configuration',async()=>{
   await page.keyboard.press('Escape');await page.click('#toggle-palette');
   await page.locator('#palette [data-kind="verifier"]').dragTo(page.locator('#canvas'),{targetPosition:{x:400,y:370}});
   await page.waitForFunction(()=>document.querySelectorAll('#canvas .node').length===2);await page.keyboard.press('Escape');
   const verifier=page.locator('#canvas .node.verifier');await verifier.locator('.node-body').click();
   assert(!(await page.locator('#inspector').isVisible()),'single click opened configuration');
   await verifier.locator('.node-body').dblclick();assert(await page.locator('#inspector').isVisible(),'double click failed');await page.keyboard.press('Escape');
 });
 await check('context duplicate delete and undo preserve selection and topology',async()=>{
   const verifier=page.locator('#canvas .node.verifier');await verifier.locator('.node-body').click({button:'right'});await page.getByRole('menuitem',{name:'复制节点',exact:true}).click();
   assert(await page.locator('#canvas .node.verifier').count()===2,'duplicate missing');
   await page.locator('#canvas .node.verifier').last().locator('.node-body').click({button:'right'});await page.getByRole('menuitem',{name:'删除节点',exact:true}).click();
   assert(await page.locator('#canvas .node.verifier').count()===1,'context delete removed wrong selection');await page.click('#undo');assert(await page.locator('#canvas .node.verifier').count()===2,'context undo failed');
   await chooseTemplate('A');await fresh('restore complete graph');
 });
 await check('real pointer drag and live edge geometry',async()=>{await dragFirstNode();assert(!(await page.locator('#panel-task').isVisible())&&!(await page.locator('#inspector').isVisible()),'drag opened an inspector');});
 await check('selected node textarea configuration is usable',async()=>{
   await page.locator('#canvas [data-node="human-1"] .node-body').dblclick();
   await page.click('[data-task-step="2"]');await page.locator('#task-context summary').click();await page.locator('[data-field="question"]').fill('Actual approved scope?');
   await page.locator('[data-field="label"]').click();await page.keyboard.press('Escape');
   assert(pageErrors.length===0,'selection raised '+pageErrors.join('; '));
 });
 await check('zoomed drag tracks actual pointer and undo redo restores positions',async()=>{
   await fresh('zoom and history');await page.click('#zoom-in');
   const rect=page.locator('#canvas .node .node-body').first(),before=await rect.getAttribute('x');
   const initial=await page.locator('#canvas .node').first().getAttribute('transform');
   await dragFirstNode();const moved=await page.locator('#canvas .node').first().getAttribute('transform');
   assert(initial!==moved,'no movement');await page.click('#undo');
   assert(await page.locator('#canvas .node').first().getAttribute('transform')===initial,'undo lost positions');
   await page.click('#redo');assert(await page.locator('#canvas .node').first().getAttribute('transform')===moved,'redo lost positions');
   await page.click('#fit');
 });
 await check('output port drag creates curved directional connection',async()=>{
   await chooseTemplate('A');await fresh('port connection');
   await page.click('#flow-options');if(await page.locator('.connections-panel').getAttribute('open')===null)await page.locator('.connections-panel summary').click();
   await page.locator('#edges li').first().locator('button').click();await page.keyboard.press('Escape');
   await page.locator('#canvas').scrollIntoViewIfNeeded();
   const start=await page.locator('[data-node="human-1"] .out').boundingBox(),end=await page.locator('[data-node="developer"] .in').boundingBox();
   const count=await page.locator('#edges li').count();
   await page.mouse.move(start.x+start.width/2,start.y+start.height/2);await page.mouse.down();
   await page.mouse.move(end.x+end.width/2,end.y+end.height/2,{steps:10});
   assert(await page.locator('.connection-preview').count()===1,'no connection preview');await page.mouse.up();
   assert(await page.locator('#edges li').count()===count+1,'port connection missing: '+await page.textContent('#notice'));
   assert((await page.locator('#canvas .edge.next').last().getAttribute('d')).includes(' C '),'connection not curved');
   assert((await page.locator('#canvas .edge.next').last().getAttribute('marker-end')).includes('arrow-next'),'direction missing');
 });
 await check('pointer cancellation rolls back an unfinished drag',async()=>{
   const n=page.locator('#canvas .node').first(),rect=n.locator('.node-body'),before=await n.getAttribute('transform'),box=await rect.boundingBox();
   await page.mouse.move(box.x+40,box.y+25);await page.mouse.down();await page.mouse.move(box.x+100,box.y+60,{steps:4});
   await page.keyboard.press('Escape');await page.mouse.up();
   assert(await n.getAttribute('transform')===before,'cancelled gesture left unrecorded changes');
 });
 await check('panning does not modify saved workflow and reopen preserves moved nodes',async()=>{
   const svg=page.locator('#canvas');const view=await svg.getAttribute('viewBox'),box=await svg.boundingBox();
   await page.mouse.move(box.x+30,box.y+box.height-120);await page.mouse.down();await page.mouse.move(box.x+85,box.y+box.height-75,{steps:5});await page.mouse.up();
   assert(await svg.getAttribute('viewBox')!==view,'empty canvas cannot pan');
   const request=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');
   const saved=(await request).postDataJSON().document;await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
   const id=await page.inputValue('#drafts');await fresh('temporary');await page.selectOption('#drafts',id);
   const again=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');
   assert(require('node:util').isDeepStrictEqual((await again).postDataJSON().document,saved),'reopen changed node or connection configuration');
 });
 await check('run A response cannot overwrite B',async()=>{
   await page.click('[data-tab="runs"]');
   await page.click('#refresh-runs');
   await page.waitForFunction(id=>[...document.querySelector('#runs').options].some(o=>o.value===id),access.a);
   const delay=await interceptOnce('**/api/run?id='+access.a);await page.selectOption('#runs',access.a);await delay.reached;
   await page.selectOption('#runs',access.b);await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · BLOCKED'),access.b);
   delay.release();await wait(200);
   assert((await page.textContent('#run-title')).includes(access.b+' · BLOCKED'),'A contaminated B');
   assert(!(await page.textContent('#monitor')).includes('"verdict": "ACCEPT"'),'A verdict contaminated B');
 });
 await check('A B A handles reordered snapshots',async()=>{
   const delay=await interceptOnce('**/api/run?id='+access.a);await page.selectOption('#runs',access.a);await delay.reached;
   await page.selectOption('#runs',access.b);await page.selectOption('#runs',access.a);
   await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · READY'),access.a);
   delay.release();await wait(150);
   assert((await page.textContent('#run-title')).includes(access.a+' · READY'),'selected A lost identity');
 });
 await check('network failure clears prior accepted result',async()=>{
   await page.route('**/api/run?id='+access.a,r=>r.abort('failed'));
   await page.selectOption('#runs',access.b);await page.selectOption('#runs',access.a);
   await page.waitForFunction(()=>document.querySelector('#run-title').textContent.includes('状态未确认'));
   assert(!(await page.textContent('#monitor')).includes('ACCEPT'),'network error retained ACCEPT');assert(await page.locator('#canvas .node').count()===0,'network failure retained previous canvas states');
   await page.unroute('**/api/run?id='+access.a);
 });
 await check('slow snapshot beyond polling interval is not starved',async()=>{
   await page.selectOption('#runs',access.b);const delay=await interceptOnce('**/api/run?id='+access.a);
   await page.selectOption('#runs',access.a);await delay.reached;await wait(2300);delay.release();
   await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · READY'),access.a);
 });
 await check('page reopen reads same persistent run',async()=>{
   await page.reload();await page.waitForFunction(()=>document.querySelector('#studio').dataset.ready==='true');
   await page.click('[data-tab="runs"]');
   await page.selectOption('#runs',access.b);
   await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · BLOCKED'),access.b);
 });
 await check('canvas is primary and Human Gate imports task with empty confirmation blocked before HTTP',async()=>{
   await page.click('[data-tab="workflow"]');await chooseTemplate('A');await fresh('canvas task');
   const canvasBox=await page.locator('#canvas').boundingBox();assert(canvasBox.height>800,'canvas does not occupy available workspace');
   await page.locator('#canvas [data-node="human-1"] .node-body').dblclick();
   await page.fill('#issue-url','https://github.com/fixture/project/issues/6');await page.click('#import-issue');
   await page.waitForFunction(()=>document.querySelector('#task-title').value==='真实 HTTP 导入夹具');
   await page.click('#task-next');await page.fill('#task-scope','README.md');await page.uncheck('#delivery');await page.click('#prepare');await page.locator('#preview h3').waitFor();
   let requests=0;const listener=r=>{if(r.url().endsWith('/api/authorize-run'))requests++;};page.on('request',listener);
   await page.locator('#preview button').click();await wait(150);
   assert(requests===0,'empty reply was sent to authorization server');assert(await page.locator('#preview button').isEnabled(),'empty reply disabled correction');
   assert((await page.textContent('#notice')).includes('请填写每个人工确认回复'),'missing Chinese guidance');
   await page.locator('#preview textarea').fill('浏览器夹具：确认此任务范围与路径；不允许交付。此操作不是人工可用性验收。');
   await page.route('**/api/authorize-run',r=>r.abort('failed'));
   await page.locator('#preview button').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('fetch'));
   assert(requests===1,'corrected reply did not issue exactly one request');
   assert(await page.locator('#preview button').isDisabled(),'unknown consumed permit allowed blind resend');
   await page.unroute('**/api/authorize-run');page.off('request',listener);await page.keyboard.press('Escape');
 });
 await check('keyboard node selection focuses configuration and Escape returns to canvas',async()=>{
   const node=page.locator('#canvas [data-node="developer"]');await node.focus();await page.keyboard.press('Enter');
   assert(await page.locator('#inspector').isVisible(),'keyboard cannot open node configuration');
   assert(await page.evaluate(()=>document.activeElement.closest('#inspector')!==null),'focus did not enter panel');
   await page.keyboard.press('Escape');assert(!(await page.locator('#inspector').isVisible()),'Escape did not close panel');
   assert(await page.evaluate(()=>document.activeElement.closest('#canvas')!==null),'Escape lost keyboard focus');
 });
 await check('in-focus node input immediately updates draft and invalidates authorization',async()=>{
   await page.locator('#canvas [data-node="developer"] .node-body').dblclick();
   await page.locator('[data-field="label"]').fill('输入立即生效');
   assert((await page.locator('#canvas [data-node="developer"]').textContent()).includes('输入立即生效'),'in-focus input did not update source document');
   const request=page.waitForRequest(r=>r.url().endsWith('/api/draft'));await page.click('#save');
   assert((await request).postDataJSON().document.nodes.find(n=>n.id==='developer').label==='输入立即生效','save missed latest focused input');
   await page.keyboard.press('Escape');
 });
 await check('Agent configuration saves and reopens from selected canvas node',async()=>{
   await page.locator('#canvas [data-node="developer"] .node-body').dblclick();
   await page.locator('.agent-advanced summary').click();await page.locator('[data-field="responsibility"]').fill('明确的画布实施职责');await page.locator('[data-field="timeout_seconds"]').fill('240');
   await page.locator('[data-field="label"]').fill('我的开发节点');await page.locator('[data-field="effort"]').click();await page.keyboard.press('Escape');
   await page.click('#save');await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
   const id=await page.inputValue('#drafts');await fresh('temporary agent');await page.selectOption('#drafts',id);
   await page.locator('#canvas [data-node="developer"] .node-body').dblclick();
   assert(await page.locator('[data-field="responsibility"]').inputValue()==='明确的画布实施职责','agent config was lost');
   await page.locator('.agent-advanced summary').click();assert(await page.locator('[data-field="timeout_seconds"]').inputValue()==='240','budget was lost');await page.keyboard.press('Escape');
 });
 await check('same canvas uses frozen identities own counts review diff native session and PR evidence',async()=>{
   await page.click('[data-tab="runs"]');await page.selectOption('#runs',access.canvas.failed);
   await page.waitForFunction(()=>document.querySelector('#canvas [data-node="separate-check"]')?.classList.contains('status-failed'));
   assert(await page.locator('#canvas [data-port],#canvas [data-canvas-action]').count()===0,'runtime exposed editing handles');
   assert(await page.locator('#canvas [data-node="custom-verifier"]').getAttribute('class').then(c=>c.includes('status-passed')),'first verifier lost PASS');
   await page.locator('#canvas [data-node="separate-check"] .node-body').click();
   assert((await page.textContent('#node-evidence')).includes('2 项实际测试 / 1 失败'),'second counts were borrowed');await page.keyboard.press('Escape');
   await page.locator('#canvas [data-node="custom-reviewer"] .node-body').click();
   assert((await page.textContent('#node-evidence')).includes('NEEDS_CORRECTION'),'review verdict inaccessible');
   assert((await page.textContent('#node-evidence')).includes('F-canvas'),'review findings inaccessible');await page.keyboard.press('Escape');
   await page.locator('#canvas [data-node="custom-developer"] .node-body').click();
   await page.getByRole('button',{name:'查看本轮固定候选 diff',exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#node-evidence').textContent.includes('display fixture diff only'));
   await page.getByRole('button',{name:'原生会话与发现',exact:true}).click();await page.locator('#panel-sessions').waitFor();await page.keyboard.press('Escape');
   await page.selectOption('#runs',access.canvas.delivered);await page.waitForFunction(()=>document.querySelector('#run-title').textContent.includes('COMPLETED'));
   await page.locator('#canvas [data-node="custom-delivery"] .node-body').click();
   assert(await page.getByRole('link',{name:'打开真实开发 PR',exact:true}).getAttribute('href')==='https://github.com/fixture/project/pull/42','PR not available on Delivery');
   await page.keyboard.press('Escape');await page.click('[data-tab="workflow"]');
   assert(await page.locator('#canvas [data-node="developer"]').count()===1,'run replaced design document');
   assert((await page.locator('#canvas [data-node="developer"]').textContent()).includes('我的开发节点'),'draft was overwritten by run');
 });
 await check('legacy Delivery uses matching readback and rejects mismatched or unsafe PR receipts',async()=>{
   await page.click('[data-tab="runs"]');await page.selectOption('#runs',access.canvas['legacy-delivered']);
   await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · COMPLETED'),access.canvas['legacy-delivered']);
   await page.locator('#canvas [data-node="delivery"] .node-body').click();
   assert(await page.locator('#node-evidence a').count()===1,'legacy readback missing from Delivery node');
   assert(await page.locator('#node-evidence a').getAttribute('href')==='https://github.com/fixture/project/pull/42','legacy PR identity incorrect');
   assert(!(await page.textContent('#node-evidence')).includes('尚无已读回'),'valid legacy receipt falsely shown missing');
   await page.keyboard.press('Escape');
   for(const name of ['legacy-mismatched','legacy-unsafe']){
     await page.selectOption('#runs',access.canvas[name]);
     await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · COMPLETED'),access.canvas[name]);
     await page.locator('#canvas [data-node="delivery"] .node-body').click();
     assert(await page.locator('#node-evidence a').count()===0,name+' exposed an unverified PR');
     assert(await page.locator('#monitor>a').count()===0,name+' exposed an unverified monitor link');
     await page.keyboard.press('Escape');
   }
 });
 await check('late frozen graph cannot cross run identity and stale or dead evidence cannot appear current',async()=>{
   await page.click('[data-tab="runs"]');const delay=await interceptOnce('**/api/run?id='+access.canvas.failed);
   await page.selectOption('#runs',access.canvas.failed);await delay.reached;
   await page.selectOption('#runs',access.canvas.stale);await page.waitForFunction(()=>document.querySelector('#run-title').textContent.includes('当前轮次 2'));delay.release();await wait(150);
   for(const id of ['custom-verifier','separate-check'])assert(!(await page.locator('#canvas [data-node="'+id+'"]').getAttribute('class')).includes('status-passed'),'old round showed PASS');
   await page.selectOption('#runs',access.canvas.dead);await page.waitForFunction(()=>document.querySelector('#run-title').textContent.includes('RECOVERY_REQUIRED'));
   assert(await page.locator('#canvas .status-running').count()===0,'dead worker displayed RUNNING');
   await page.selectOption('#runs',access.a);await page.waitForFunction(id=>document.querySelector('#run-title').textContent.includes(id+' · READY'),access.a);
   assert(await page.locator('#canvas [data-node="custom-developer"]').count()===0,'legacy run inherited custom identities');
 });
 await check('overlapping new draft saves oldest response first confirms latest snapshot',async()=>{
   await page.click('[data-tab="workflow"]');await chooseTemplate('A');await fresh('older pending snapshot');
   const releases=[],holds=[0,1].map(i=>new Promise(r=>releases[i]=r)),replies=[],pending=[];let serial=0;
   const handler=route=>{const i=serial++;const job=(async()=>{const response=await route.fetch();replies[i]=await response.json();await holds[i];await route.fulfill({response});})();pending.push(job);return job;};
   await page.route('**/api/draft',handler);
   try{
     const arrived=async i=>{for(let n=0;n<400&&!replies[i];n++)await wait(20);assert(replies[i],'Save request did not reach the server')};
     await page.click('#save');await arrived(0);
     await page.fill('#workflow-name','newest pending snapshot');await page.click('#save');await arrived(1);
     const refreshed=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/status'&&u.searchParams.get('workspace')===replies[0].workspace});releases[0]();await refreshed;
     assert(await page.textContent('#save-state')!=='该快照已保存','The older snapshot cannot claim all changes saved');assert(await page.inputValue('#drafts')==='','An older pending creation must not claim the current identity');
     releases[1]();await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='该快照已保存');
     assert(await page.inputValue('#drafts')===replies[1].id,'The latest save must own the current draft identity');
     await fresh('temporary after overlapping save');await page.selectOption('#drafts',replies[1].id);assert(await page.inputValue('#workflow-name')==='newest pending snapshot','Reopened draft lost the latest snapshot');
   }finally{releases.forEach(r=>r());await Promise.allSettled(pending);await page.unroute('**/api/draft',handler)}
 });
 await check('no uncaught browser exceptions',async()=>assert(pageErrors.length===0,pageErrors.join('; ')));
 await page.click('[data-tab="workflow"]');await page.screenshot({path:path.join(output,'studio.png'),fullPage:true});
})().catch(e=>{results.push({name:'harness',passed:false,error:String(e)});console.error(e);}).finally(async()=>{
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({tests:results.length,failures:results.filter(r=>!r.passed).length,skipped:0,results},null,2));
 if(browser)await browser.close();process.exitCode=results.every(r=>r.passed)?0:1;
});
