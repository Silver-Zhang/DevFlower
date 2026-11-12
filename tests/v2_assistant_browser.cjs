const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const out=process.argv[2];
const workspace=path.join(process.env.V2_WORKSPACES,'A');
const requests=[];
const errors=[];
let browser,context;

async function run(){
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 context=await browser.newContext({viewport:{width:1280,height:840}});
 await context.tracing.start({screenshots:true,snapshots:true});
 const page=await context.newPage();
 page.setDefaultTimeout(15000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>requests.push([request.method(),new URL(request.url()).pathname]));
 const button=name=>page.getByRole('button',{name,exact:true});
 await page.goto(process.env.V2_URL);
 await page.locator('.app-header button').click();
 await page.getByLabel('文件夹路径',{exact:true}).fill(workspace);
 await button('前往').click();
 await button('打开此文件夹').click();
 await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:'Assistant browser draft'}).click();
 const canvas=page.getByLabel('工作流画布',{exact:true});
 const original=await canvas.boundingBox();
 await button('文件').click();
 await page.getByRole('button',{name:'预览文件 note.txt',exact:true}).click();
 await button('附加到助手').click();
 const assistant=page.getByLabel('AI 助手',{exact:true});
 await assistant.waitFor();
 const dock=await assistant.boundingBox(), graph=await canvas.boundingBox();
 assert(dock.width>=400&&dock.width<=420);
 assert(graph.x+graph.width<=dock.x+1);
 assert(original.width-graph.width<=420);
 await button('助手模型设置').click();
 await assistant.getByText('当前模型目录已过期或不可用。请刷新模型目录。',{exact:true}).waitFor();
 await assistant.getByText(/上次连接测试已收到原生回执 · fixture-model/).waitFor();
 assert.equal(await button('测试真实推理').isEnabled(),false);
 assert.equal(await button('刷新模型目录').isEnabled(),true);
 await button('助手模型设置').click();
 await assistant.getByLabel('助手 Provider',{exact:true}).selectOption('claude');
 await assistant.getByLabel('助手工作方式',{exact:true}).selectOption('proposal');
 await assistant.getByLabel('助手消息',{exact:true}).fill('Rename AgentA to Reviewed Agent on the canvas.');
 await button('预览本次发送').click();
 const preview=assistant.getByLabel('本次发送预览',{exact:true});
 await preview.getByText(/SELECTED_ASSISTANT_ATTACHMENT/).waitFor();
 assert.equal(await preview.getByText(/UNSELECTED_ASSISTANT_SENTINEL/).count(),0);
 assert.equal(requests.filter(([method,route])=>method==='POST'&&route.endsWith('/assistant/send')).length,0);
 let afterDrop;
 const dropped=new Promise(resolve=>{afterDrop=resolve});
 const drop=async route=>{
  const response=await route.fetch();
  assert.equal(response.status(),200);
  await route.abort('failed');
  afterDrop();
 };
 await page.route('**/api/v2/assistant/send',drop);
 await button('确认发送').click();
 await dropped;
 await button('核对本次发送').waitFor();
 await page.unroute('**/api/v2/assistant/send',drop);
 await button('关闭 AI 助手').click();
 await button('AI 助手').click();
 // Reopen reads the stored identity without submitting again.
 const proposal=assistant.getByLabel('画布提案',{exact:true});
 await proposal.getByText('Reviewed Agent',{exact:false}).first().waitFor();
 assert.equal(await preview.count(),0);
 assert.equal(await assistant.getByLabel('助手消息',{exact:true}).inputValue(),'');
 assert.equal(await page.locator('.canvas-node').filter({hasText:'Original Agent'}).count(),1);
 await page.screenshot({path:path.join(out,'assistant-proposal.png')});
 await button('应用到画布草稿').click();
 await assistant.getByText('已应用到当前草稿，可以撤销。',{exact:true}).waitFor();
 await page.locator('.canvas-node').filter({hasText:'Reviewed Agent'}).waitFor();
 await button('关闭 AI 助手').click();
 await page.getByLabel('撤销',{exact:true}).click();
 await page.locator('.canvas-node').filter({hasText:'Original Agent'}).waitFor();
 await button('AI 助手').click();
 await page.getByRole('button',{name:'助手会话历史',exact:true}).click();
 await assistant.getByLabel('助手会话列表',{exact:true}).getByRole('button',{name:/Rename AgentA/}).click();
 await assistant.getByLabel('画布提案',{exact:true}).waitFor();
 await assistant.getByLabel('助手 Provider',{exact:true}).selectOption('claude');
 await assistant.getByLabel('助手消息',{exact:true}).fill('503 fixture question');
 await button('预览本次发送').click();
 let serviceIntent;
 await page.route('**/api/v2/assistant/send',async route=>{
  const committed=await route.fetch();
  assert.equal(committed.status(),200);
  serviceIntent=(await committed.json()).intent;
  await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture ambiguous 503'})});
 },{times:1});
 await button('确认发送').click();
 await assistant.getByRole('alert').getByText(/Fixture ambiguous 503/).waitFor();
 const readback=page.waitForResponse(response=>response.url().includes('/api/v2/assistant/conversation?'));
 await button('核对本次发送').click();
 const record=await (await readback).json();
 assert.equal(record.turns.at(-1).intent,serviceIntent);
 await assistant.getByText('Verified fixture chat response',{exact:true}).waitFor();
 assert.equal(await button('确认发送').count(),0);
 await button('关闭 AI 助手').click();
 await page.getByLabel('运行历史',{exact:true}).getByRole('button',{name:/Assistant diagnosis fixture/}).click();
 await page.getByLabel('运行状态',{exact:true}).getByText('失败',{exact:true}).waitFor();
 await button('诊断本次运行').click();
 const diagnosis=assistant.getByLabel('运行只读诊断',{exact:true});
 await diagnosis.getByText(/没有匹配规则/).waitFor();
 await diagnosis.getByText('运行检查器中的错误事件',{exact:true}).waitFor();
 await page.setViewportSize({width:560,height:760});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:path.join(out,'assistant-diagnosis-narrow.png')});
 const composer=assistant.getByLabel('助手发送',{exact:true});
 const message=composer.getByLabel('助手消息',{exact:true});
 await message.click({trial:true});
 await message.focus();
 assert(await message.evaluate(node=>node===document.activeElement));
 assert(await composer.evaluate(node=>node.scrollWidth<=node.clientWidth+1));
 await page.keyboard.press('Escape');
 await assistant.waitFor({state:'detached'});
 const navigation=button('展开导航栏');
 await page.waitForFunction(label=>document.activeElement.getAttribute('aria-label')===label,'展开导航栏');
 assert(await navigation.isVisible());
 assert.equal(requests.filter(([method,route])=>method==='POST'&&route.endsWith('/assistant/send')).length,2);
 assert(!requests.some(([method,route])=>method==='POST'&&['/launch','/drafts/save','/resume'].some(end=>route.endsWith(end))));
 // Project a prior repair result without starting a Provider, and observe
 // the real UI's new-preview request while keeping launch uncalled.
 const priorRepair='1'.repeat(32),repairAgent='2'.repeat(32),revision='3'.repeat(64);
 let repairState='RECOVERY_REQUIRED',repairSource,previewPosts=0,launchPosts=0;
 await page.route('**/api/v2/assistant/settings?*',async route=>{
  const response=await route.fetch(),value=await response.json();
  value.identities=[{id:repairAgent,revision,name:'Saved repair agent',mode:'Agent',
   permission:'workspace-write',session_kind:'new'}];
  await route.fulfill({response,json:value});
 });
 await page.route('**/api/v2/assistant/diagnosis?*',async route=>{
  const response=await route.fetch(),value=await response.json();
  repairSource=value.run;
  await route.fulfill({response,json:{...value,repair_run:priorRepair}});
 });
 await page.route('**/api/v2/assistant/repair_status?*',async route=>{
  assert.equal(new URL(route.request().url()).searchParams.get('run'),priorRepair);
  const file={path:'note.txt',sha256:'4'.repeat(64)};
  await route.fulfill({json:{run:priorRepair,source_run:repairSource,state:repairState,
   files:[{before:file,after:file,changed:false}]}});
 });
 await page.route('**/api/v2/assistant/preview_repair',async route=>{
  previewPosts++;
  const body=route.request().postDataJSON();
  assert.equal(body.run,repairSource);
  assert.equal(body.agent,repairAgent);
  assert.equal(body.revision,revision);
  assert.equal(body.authorization,undefined);
  assert.equal(body.intent,undefined);
  await route.fulfill({json:{evidence:{node:'Route',reason:'fixture'},agent:{name:'Saved repair agent',
   provider:'codex',permission:'workspace-write',revision,cwd:workspace},
   source:{branch:'fixture',head:'5'.repeat(40)},execution_task:body.task,files:[],sha256:'6'.repeat(64)}});
 });
 await page.route('**/api/v2/assistant/launch_repair',async route=>{launchPosts++;await route.abort()});
 await page.setViewportSize({width:1280,height:840});
 await button('诊断本次运行').click();
 const repair=assistant.getByLabel('授权修复',{exact:true});
 await repair.getByRole('status').filter({hasText:'RECOVERY_REQUIRED'}).waitFor();
 assert.equal(await repair.getByRole('button',{name:'新建修复',exact:true}).count(),0);
 repairState='COMPLETED';
 await repair.getByRole('button',{name:'重新读取修复与验证',exact:true}).click();
 await repair.getByText('所附文件的指纹均与修复前一致。',{exact:true}).waitFor();
 await repair.getByRole('button',{name:'新建修复',exact:true}).click();
 assert.equal(previewPosts,0);assert.equal(launchPosts,0);
 await repair.getByLabel('受管修复 Agent',{exact:true}).selectOption(repairAgent);
 await repair.getByText(/核对所选版本的每轮提示词/).waitFor();
 await repair.getByLabel('明确的修复任务',{exact:true}).fill('修复失败用例并保留原记录');
 await repair.getByRole('button',{name:'预览执行修复',exact:true}).click();
 const approve=repair.getByRole('checkbox');
 await approve.waitFor();
 assert.equal(await approve.isChecked(),false);
 assert.equal(await repair.getByRole('button',{name:'确认执行修复',exact:true}).isEnabled(),false);
 assert.equal(previewPosts,1);assert.equal(launchPosts,0);
 await page.screenshot({path:path.join(out,'assistant-new-repair-preview.png')});
 console.log('PASS completed no-op opens a new repair preview with fresh approval and no launch');
 assert.deepEqual(errors,[]);
 console.log('PASS preview and selected attachment, lost send and 503 readback, proposal review and undo, history, read-only diagnosis');
}

run().catch(error=>{fs.writeFileSync(path.join(out,'fatal.txt'),error.stack);console.error(error.message);process.exitCode=1})
.finally(async()=>{
 if(context)await context.tracing.stop({path:path.join(out,'trace.zip')}).catch(()=>{});
 if(browser)await browser.close();
});
