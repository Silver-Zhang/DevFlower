const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const path = require('node:path');

async function main() {
 const browser = await chromium.launch({executablePath:process.env.CHROMIUM, args:['--no-sandbox']});
 const context = await browser.newContext({viewport:{width:1440,height:900}});
 await context.tracing.start({screenshots:true,snapshots:true});
 const page = await context.newPage();
 page.setDefaultTimeout(12000);
 const button = name => page.getByRole('button',{name,exact:true});
 const errors = [];
 page.on('pageerror',error=>errors.push(error.message));
 try {
  await page.goto(process.env.V2_URL);
  await page.locator('.app-header button').click();
  await page.getByLabel('文件夹路径',{exact:true}).fill(process.env.V2_PROJECT);
  await button('前往').click();
  await button('打开此文件夹').click();
  await button('用 AI 创建工作流').click();
  const assistant = page.getByLabel('AI 助手',{exact:true});
  const message = assistant.getByLabel('助手消息',{exact:true});
  const proposal = assistant.getByLabel('画布提案',{exact:true});
  const canvas = page.getByLabel('工作流画布',{exact:true});
  const graphNode = name => canvas.locator('.canvas-node').filter({hasText:name});
  async function send(text) {
   await message.fill(text);
   await button('预览本次发送').click();
   await button('确认发送').click();
   await proposal.getByText(/可导出独立 Python/).waitFor();
  }
  await send('创建两个使用同一配置的任务节点');
  await proposal.getByLabel('提案画布预览',{exact:true}).waitFor();
  assert.equal(await canvas.locator('.canvas-node').count(),0);
  const applyResponses=[];
  await page.route('**/api/v2/assistant/apply',async route=>{
   const response=await route.fetch();
   applyResponses.push(response.status());
   await route.abort('failed');
  },{times:1});
  await button('应用并保存草稿').click();
  await graphNode('Second').waitFor();
  assert.deepEqual(applyResponses,[200]);
  await button('预览 Python').click();
  const preview=page.getByRole('dialog',{name:'Python 脚本预览'});
  const source=await preview.locator('pre').textContent();
  assert.match(source,/'task_prompt': 'FIRST_BROWSER_TASK'/);
  assert.match(source,/'task_prompt': 'SECOND_BROWSER_TASK'/);
  await button('关闭预览').click();
  await send('把第二个改为 Claude 并在前面插入版本检查');
  await proposal.getByLabel('Version 命令参数',{exact:true}).waitFor();
  assert.equal(await graphNode('Version').count(),0);
  const title=page.getByLabel('工作流名称',{exact:true});
  const original=await title.inputValue();
  await title.fill('浏览器手动编辑');
  await button('应用并保存草稿').click();
  await assistant.getByRole('alert').getByText(/草稿自生成提案后已变化/).waitFor();
  assert.equal(await title.inputValue(),'浏览器手动编辑');
  assert.equal(await graphNode('Version').count(),0);
  await title.fill(original);
  await button('应用并保存草稿').click();
  await graphNode('Claude Reviewer').waitFor();
  await graphNode('Version').waitFor();
  await button('关闭 AI 助手').click();
  await page.getByLabel('撤销',{exact:true}).click();
  await graphNode('Second').waitFor();
  assert.equal(await graphNode('Version').count(),0);
  await button('保存').click();
  await page.reload();
  await graphNode('Second').waitFor();
  assert.equal(await graphNode('Version').count(),0);
  await button('预览 Python').click();
  await button('关闭预览').click();

  const service=new URL(process.env.V2_URL);
  const headers={Authorization:'Bearer '+service.hash.slice(1)};
  const workspaceResponse=await page.request.post(service.origin+'/api/v2/workspaces/open',{
   headers,data:{path:process.env.V2_PROJECT}
  });
  assert.equal(workspaceResponse.status(),200);
  const workspace=(await workspaceResponse.json()).id;
  const unsupportable='Unsupported saved fixture';
  const savedDesign=await page.request.post(service.origin+'/api/v2/drafts/save',{
   headers,data:{workspace,id:null,expected:null,document:{
    version:1,name:unsupportable,execution:'native-cli',
    nodes:[{id:'Join',name:'Merge stage',kind:'merge',position:{x:80,y:80},config_version:1,config:{}}],
    edges:[]
   }}
  });
  assert.equal(savedDesign.status(),200);
  await page.reload();
  await graphNode('Second').waitFor();
  let release, entered;
  const hold=new Promise(resolve=>{release=resolve});
  const sent=new Promise(resolve=>{entered=resolve});
  await page.route('**/api/v2/check',async route=>{
   const posted=route.request().postDataJSON();
   if(posted.document.name!==unsupportable){await route.continue();return}
   const response=await route.fetch();
   entered();
   await hold;
   await route.fulfill({response});
  });
  await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:unsupportable,exact:true}).click();
  await sent;
  await page.getByText('正在检查已保存的原生工作流。',{exact:true}).waitFor();
  const exportPython=button('导出 Python（原生 CLI）');
  assert(await button('启动').isDisabled());
  assert(await exportPython.isDisabled());
  await title.fill('Local change during check');
  release();
  await page.getByText('正在检查已保存的原生工作流。',{exact:true}).waitFor({state:'hidden'});
  assert.equal(await title.inputValue(),'Local change during check');
  await title.fill(unsupportable);
  await page.locator('#v2-start-hint').getByText(/暂不支持编译.*Join/).waitFor();
  assert(await button('启动').isDisabled());
  assert(await exportPython.isDisabled());
  await button('检查').click();
  await page.getByLabel('工作流检查结果',{exact:true}).getByText(/暂不支持编译/).first().waitFor();
  assert.deepEqual(errors,[]);
  console.log('PASS native proposal, save-response readback, Python preview, version conflict, undo, reload and native check admission');
 } catch(error) {
  await page.screenshot({path:path.join(process.argv[2],'failure.png')});
  throw error;
 } finally {
  await context.tracing.stop({path:path.join(process.argv[2],'trace.zip')});
  await browser.close();
 }
}

main().catch(error=>{console.error(error);process.exitCode=1;});
