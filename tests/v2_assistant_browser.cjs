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
 await button('文件').click();
 await page.getByRole('button',{name:'预览文件 note.txt',exact:true}).click();
 await button('附加到助手').click();
 const assistant=page.getByLabel('AI 助手',{exact:true});
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
 await button('核对本次发送').click();
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
 await button('关闭 AI 助手').click();
 await page.getByLabel('运行历史',{exact:true}).getByRole('button',{name:/Assistant diagnosis fixture/}).click();
 await button('诊断本次运行').click();
 const diagnosis=assistant.getByLabel('运行只读诊断',{exact:true});
 await diagnosis.getByText(/没有匹配规则/).waitFor();
 await diagnosis.getByText('运行检查器中的错误事件',{exact:true}).waitFor();
 await page.setViewportSize({width:560,height:760});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:path.join(out,'assistant-diagnosis-narrow.png')});
 assert.equal(requests.filter(([method,route])=>method==='POST'&&route.endsWith('/assistant/send')).length,1);
 assert(!requests.some(([method,route])=>method==='POST'&&['/launch','/drafts/save','/resume'].some(end=>route.endsWith(end))));
 assert.deepEqual(errors,[]);
 console.log('PASS preview and selected attachment, lost send readback, proposal review and undo, history, read-only diagnosis');
}

run().catch(error=>{fs.writeFileSync(path.join(out,'fatal.txt'),error.stack);console.error(error.message);process.exitCode=1})
.finally(async()=>{
 if(context)await context.tracing.stop({path:path.join(out,'trace.zip')}).catch(()=>{});
 if(browser)await browser.close();
});
