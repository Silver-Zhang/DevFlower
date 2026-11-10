const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
(async () => {
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 try {
  const page = await browser.newPage({viewport:{width: 1100, height: 760}});
  page.setDefaultTimeout(15000);
  const runtimeErrors = [];
  page.on('pageerror', error => runtimeErrors.push(error.message));
  await page.goto(process.env.V2_URL);
  await page.locator('.workspace-link').getByText('A', {exact:true}).click();
  await page.getByRole('button',{name:'原生会话',exact:true}).click();
  const panel = page.getByRole('complementary',{name:'原生会话'});
  const open = text => panel.getByRole('button',{name:text,exact:true}).click();
  await open('新建会话');
  await open('刷新 Codex 模型目录');
  const choose = async () => {
   const select=panel.getByLabel('请求模型',{exact:true});
   await select.locator('option').nth(1).waitFor({state:'attached'});
   const model = await select.locator('option').nth(1).getAttribute('value');
   await select.selectOption(model);
   await panel.getByLabel('请求强度',{exact:true}).selectOption('low');
  };
  await choose();
  let sends=0;
  page.on('request', request => {if (request.url().endsWith('/api/v2/session-send')) sends++;});
  await page.route('**/api/v2/session-send',async route=>{
   const reply=await route.fetch();
   assert.equal(reply.status(),200);
   await route.abort('failed');
  },{times:1});
  await panel.getByLabel('消息',{exact:true}).fill('B_BROWSER_SYNTHETIC_NEW');
  await open('新建并发送');
  await panel.getByRole('alert').filter({hasText:'请核验本次发送'}).waitFor();
  await open('核验本次发送');
  await panel.getByText('B_BROWSER_SYNTHETIC_NEW',{exact:true}).waitFor({timeout:90000});
  assert.equal(sends,1);
  await open('继续会话');
  await choose();
  await panel.getByLabel('消息',{exact:true}).fill('B_BROWSER_SYNTHETIC_CONTINUE');
  await open('发送到此会话');
  await panel.getByText('B_BROWSER_SYNTHETIC_CONTINUE',{exact:true}).waitFor({timeout:90000});
  await panel.getByText('B_BROWSER_SYNTHETIC_NEW',{exact:true}).waitFor();
  assert.equal(sends,2);
  assert.equal(await panel.getByRole('button',{name:'打开原生终端',exact:true}).isDisabled(),true);
  await open('关闭原生会话');
  await page.getByRole('button',{name:'原生会话',exact:true}).click();
  await panel.getByText('B_BROWSER_SYNTHETIC_CONTINUE',{exact:true}).waitFor();
  await page.setViewportSize({width:560,height:800});
  assert(await panel.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
  assert.deepEqual(runtimeErrors,[]);
  await page.screenshot({path: process.argv[2] + '/session-send.png'});
  console.log('PASS explicit create and continue, lost-response query without replay, two turns visible, last reopen, narrow width, gated PTY');
 } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
