'use strict';
const fs=require('fs'),assert=require('assert/strict'),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.argv[2],access=JSON.parse(fs.readFileSync(out+'/access.json')),results=[];
let browser;
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,40))}throw Error('Condition not reached')}
async function test(name,fn){const context=await browser.newContext({viewport:{width:1512,height:900}}),p=await context.newPage();p.setDefaultTimeout(8000);const errors=[];p.on('pageerror',e=>errors.push(String(e)));
 try{await p.goto(access.url+'/#'+access.token);await p.getByRole('navigation',{name:'工作区导航'}).getByRole('button',{name:'打开工作区',exact:true}).click();await p.getByRole('button',{name:'选择此文件夹',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor({state:'hidden'});await fn(p);assert.deepEqual(errors,[]);results.push({name,passed:true})}catch(e){results.push({name,passed:false,error:e.stack});await p.screenshot({path:out+'/'+name+'-failure.png'})}finally{await context.close();fs.writeFileSync(out+'/design-results.json',JSON.stringify({evidence_type:'real Chromium/HTTP, synthetic Provider fixtures',tests:results.length,failures:results.filter(x=>!x.passed).length,errors:0,skipped:0,results},null,2));console.log(name+': '+(results.at(-1).passed?'PASS':'FAIL'))}}
async function example(p){await p.getByRole('button',{name:'示例',exact:true}).click();await p.locator('.built-in-template').first().click();await p.locator('.react-flow__node').first().waitFor();await p.getByRole('button',{name:'适应画布',exact:true}).click()}
(async()=>{browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
for(const [width,height] of [[1512,900],[1280,800],[952,756],[700,900],[560,800]])for(const theme of ['light','dark'])await test(`layout-${width}-${theme}`,async p=>{
 await p.setViewportSize({width,height});await p.getByLabel('外观主题',{exact:true}).selectOption(theme);await example(p);
 const canvas=await p.getByTestId('canvas').boundingBox();assert(canvas.width>=width-(width>=1180?224+360:width<760?52:224)-2);assert(canvas.height>=height*.37,'Canvas retains useful height');
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
 await p.getByLabel('本次任务',{exact:true}).fill('分析登录流程，修复问题，并独立验证。');const before=await p.locator('.react-flow__viewport').getAttribute('style');
 if(width<1180){await p.getByLabel('切换工作台侧栏',{exact:true}).click();await p.getByLabel('收起工作台侧栏',{exact:true}).click();assert.equal(await p.locator('.react-flow__viewport').getAttribute('style'),before,'Closing drawer does not move camera')}
 await p.screenshot({path:out+`/canvas-${width}-${theme}.png`});
 await p.getByRole('button',{name:'AI 助手',exact:true}).click();await p.getByLabel('画布助手对话',{exact:true}).waitFor();await until(async()=>await p.getByLabel('画布助手对话',{exact:true}).evaluate(e=>e.scrollTop===0));
 const welcome=await p.locator('.assistant-welcome h3').boundingBox(),log=await p.getByLabel('画布助手对话',{exact:true}).boundingBox();assert(welcome.y>=log.y-1,'Welcome heading never scrolled offscreen');
 const composer=await p.locator('.assistant-composer').boundingBox();assert(composer.y+composer.height<=height+1);assert(composer.height<height*.4,'Composer leaves room for reading');
 await p.screenshot({path:out+`/assistant-${width}-${theme}.png`});
});
await test('assistant-reading-preserved-during-stream-and-empty-reset',async p=>{
 const open=p.getByRole('button',{name:'AI 助手',exact:true});
 await open.click();
 const dock=p.getByTestId('assistant-dock'),log=dock.getByRole('log');
 await dock.getByLabel('助手工作方式',{exact:true}).selectOption('chat');
 await dock.getByLabel('配置需求',{exact:true}).fill('SYNTHETIC reading history');
 const sent=p.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'));
 await dock.getByRole('button',{name:'发送',exact:true}).click();
 const conversation=(await sent).postDataJSON().conversation;
 await log.getByText('普通回答 · 未修改画布',{exact:true}).waitFor();
 let revision=1,polls=0,replayed=0;
 p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/canvas-assistant/messages'))replayed++});
 await p.route('**/api/canvas-assistant/conversation?*',async route=>{
  if(new URL(route.request().url()).searchParams.get('id')!==conversation)return route.continue();
  const response=await route.fetch();assert.equal(response.status(),200);
  const value=await response.json();assert.equal(value.id,conversation);assert.equal(value.workspace,access.workspace);
  polls++;
  await route.fulfill({response,json:{...value,messages:Array.from({length:30},(_,i)=>({turn:'synthetic-'+i,role:i%2?'assistant':'user',text:'消息 '+i+'\n\n'+('可读内容与代码示例。'.repeat(20))})),turn:{id:'synthetic-stream',state:'RUNNING',visible_text:'新增输出 '+revision,visible_summary:null}}});
 });
 // Reopen the existing conversation, then expose controlled streaming updates.
 await p.getByLabel('收起工作台侧栏',{exact:true}).click();await open.click();
 await log.getByText('新增输出 1',{exact:true}).waitFor();
 const articles=log.locator('article.assistant-bubble');
 assert.equal(await articles.count(),31,'Keep all 30 history messages and the partial reply');
 assert(await articles.last().getByText('DevFlower · 部分回复（未完成）',{exact:true}).isVisible());
 const before=await log.evaluate(e=>{
  e.scrollTop=100;e.dispatchEvent(new Event('scroll'));
  return e.scrollTop;
 });
 assert.equal(before,100);
 const initialPolls=polls;revision++;
 await log.getByText('新增输出 2',{exact:true}).waitFor();assert(polls>initialPolls);
 const latest=p.getByRole('button',{name:'查看新消息',exact:true});
 await latest.waitFor();assert(Math.abs(await log.evaluate(e=>e.scrollTop)-before)<2,'Incoming output preserves history reading');
 await latest.click();assert(await log.evaluate(e=>e.scrollHeight-e.clientHeight-e.scrollTop<64));
 await latest.waitFor({state:'hidden'});
 await p.unrouteAll({behavior:'wait'});await dock.getByLabel('新建助手对话',{exact:true}).click();
 await until(async()=>await log.locator('.assistant-welcome').count()===1);
 assert.equal(await log.evaluate(e=>e.scrollTop),0);assert.equal(await latest.count(),0);assert.equal(replayed,0);
});
await test('compact-navigation-escape-restores-focus-and-draft',async p=>{
 await p.setViewportSize({width:700,height:900});await until(async()=>await p.locator('.workbench').evaluate(e=>e.classList.contains('sidebar-collapsed')));for(const selector of ['.sidebar-title','.session-search','.collection-tabs','.session-grouping','.session-filters','.session-list'])assert.equal(await p.locator(selector).isVisible(),false,'Collapsed rail hides '+selector);await p.getByLabel('本次任务',{exact:true}).fill('保留任务草稿');await p.getByRole('button',{name:'AI 助手',exact:true}).click();await p.getByTestId('assistant-dock').waitFor();await p.getByLabel('展开导航栏',{exact:true}).click();assert(await p.getByRole('navigation',{name:'工作区导航'}).getByRole('button',{name:'打开工作区',exact:true}).isVisible());await p.getByRole('navigation',{name:'工作区导航'}).getByRole('button',{name:'打开工作区',exact:true}).click();await p.getByRole('heading',{name:'打开文件夹',exact:true}).waitFor();await p.keyboard.press('Escape');assert.equal(await p.getByRole('heading',{name:'打开文件夹',exact:true}).count(),0);assert(await p.locator('.workbench').evaluate(e=>e.classList.contains('navigation-drawer-open')),'Modal closes before its parent navigation');assert(await p.getByTestId('assistant-dock').isVisible());await p.keyboard.press('Escape');await until(async()=>await p.getByLabel('展开导航栏',{exact:true}).evaluate(e=>e===document.activeElement));assert.equal(await p.getByLabel('本次任务',{exact:true}).inputValue(),'保留任务草稿');assert(await p.getByTestId('assistant-dock').isVisible(),'Escape dismisses navigation before the underlying assistant');await p.getByLabel('切换工作台侧栏',{exact:true}).click();assert.equal(await p.locator('.studio-tools').isVisible(),false,'Toolbar toggle closes the actual assistant drawer');
});
await browser.close();process.exitCode=results.some(x=>!x.passed)?1:0})().catch(e=>{console.error(e);process.exitCode=1});
