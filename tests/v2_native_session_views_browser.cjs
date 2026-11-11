const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out, stage] = process.argv.slice(2);
const cases = [], requests = [], errors = [];

async function until(test) {
 for (let i = 0; i < 100; i++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function main() {
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 1440, height: 900}});
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', req => {
  const route = new URL(req.url()).pathname;
  if (route.startsWith('/api/')) requests.push({route, method: req.method()});
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const list = pane.getByLabel('会话列表', {exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const header = page.locator('.app-header button');
 const native = page.getByRole('button', {name: '原生会话', exact: true});
 const recent = name => page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')});
 const action = name => body.getByRole('button', {name, exact: true});
 const rows = name => list.getByRole('button', {name});
 const select = name => list.getByRole('radio', {name, exact: true}).check();
 const postCount = () => requests.filter(req => req.route === '/api/v2/native-session-view' && req.method === 'POST').length;
 const visit = async name => {
  await header.click();
  await page.getByLabel('文件夹路径', {exact: true}).fill(path.join(process.env.V2_WORKSPACES, name));
  await page.getByRole('button', {name: '前往', exact: true}).click();
  await page.getByRole('button', {name: '打开此文件夹', exact: true}).click();
  await until(async () => await header.textContent() === name);
 };
 const openRow = async (name, marker = 'DEFAULT_CODEX_A') => {
  await rows(name).click();
  await body.getByText(marker, {exact: false}).waitFor();
 };
 const close = () => pane.getByRole('button', {name: '关闭原生会话', exact: true}).click();
 const confirm = async (name, submit) => {
  await action(name).click();
  const dialog = page.getByRole('dialog', {name, exact: true});
  await dialog.getByText('仅影响 DevFlower 列表。', {exact: true}).waitFor();
  await dialog.getByRole('button', {name: submit, exact: true}).click();
 };
 const rename = async alias => {
  await action('设置本地别名').click();
  const dialog = page.getByRole('dialog', {name: '设置本地别名', exact: true});
  await dialog.getByLabel('本地别名', {exact: true}).fill(alias);
  await dialog.getByRole('button', {name: '保存别名', exact: true}).click();
 };
 const scenario = async (name, run) => {
  try {
   await run();
   await page.screenshot({path: path.join(out, stage + '-' + name + '.png')});
   cases.push({name, status: 'PASS'});
   console.log('PASS ' + name);
  } catch (error) {
   cases.push({name, status: 'FAIL', error: error.stack});
   await page.screenshot({path: path.join(out, stage + '-' + name + '-FAIL.png')}).catch(() => {});
   console.error('FAIL ' + name + ' ' + error.message);
   throw error;
  }
 };
 try {
  await page.goto(process.env.V2_URL);
  if (stage === 'restart') {
   await scenario('saved-hidden-view-does-not-flash-on-restart', async () => {
    await visit('A');
    let release, seen = false;
    const held = new Promise(resolve => {release = resolve;});
    const handler = async route => {
     const response = await route.fetch();
     seen = true;
     await held;
     await route.fulfill({response});
    };
    await page.route('**/api/v2/native-session-views?*', handler);
    await page.setViewportSize({width: 600, height: 900});
    await page.getByRole('button', {name: '展开导航栏', exact: true}).focus();
    await page.keyboard.press('Enter');
    await native.focus();
    await page.keyboard.press('Enter');
    await until(() => seen);
    assert.equal(await list.getByRole('button').count(), 0);
    release();
    await list.getByRole('radio', {name: '当前', exact: true}).waitFor();
    assert.equal(await rows(/迟到跨工作空间别名/).count(), 0);
    await select('隐藏');
    await openRow(/迟到跨工作空间别名/);
    await action('取消隐藏').waitFor();
    assert.equal(postCount(), 0);
   });
   return;
  }

  await scenario('alias-hide-cancel-archive-trash-and-orthogonal-restore', async () => {
   await visit('B');
   await visit('A');
   await native.click();
   await openRow(/^Codex 会话 .*默认来源/);
   await rename('本地别名 <b>');
   await body.getByRole('heading', {name: '本地别名 <b> · 只读', exact: true}).waitFor();
   assert.equal(await body.locator('b').count(), 0);
   await action('隐藏').click();
   await rows(/本地别名/).waitFor({state: 'detached'});
   await select('隐藏');
   await openRow(/本地别名/);

   const beforeCancel = postCount();
   await confirm('归档', '取消');
   assert.equal(postCount(), beforeCancel);
   await confirm('归档', '确认归档');
   await body.waitFor({state: 'detached'});
   await select('归档');
   await openRow(/本地别名/);
   assert((await rows(/本地别名/).innerText()).includes('已隐藏'));
   await confirm('移入回收站', '确认移入回收站');
   await body.waitFor({state: 'detached'});
   await select('回收站');
   await openRow(/本地别名/);
   await action('恢复到隐藏列表').click();
   await body.waitFor({state: 'detached'});
   await select('隐藏');
   await openRow(/本地别名/);
   await action('取消隐藏').click();
   await body.waitFor({state: 'detached'});
   await select('当前');
   await openRow(/本地别名/);
   await confirm('归档', '确认归档');
   await body.waitFor({state: 'detached'});
   await select('归档');
   await openRow(/本地别名/);
   await action('恢复到当前列表').click();
   await body.waitFor({state: 'detached'});
   await select('当前');
   await openRow(/本地别名/);
  });

  await scenario('late-save-reconciles-original-row-without-replacing-selection', async () => {
   let release, seen = false, done = false;
   const held = new Promise(resolve => {release = resolve;});
   const pattern = '**/api/v2/native-session-view';
   const handler = async route => {
    const response = await route.fetch();
    seen = true;
    await held;
    await route.fulfill({response});
    done = true;
   };
   await page.route(pattern, handler);
   await rename('同工作空间迟到别名');
   await until(() => seen);
   await openRow(/^Claude Code 会话 .*默认来源/, 'DEFAULT_CLAUDE_A');
   release();
   await until(() => done);
   await rows(/同工作空间迟到别名/).waitFor();
   assert.equal(await body.getByText(/DEFAULT_CODEX_A/).count(), 0);
   await page.unroute(pattern, handler);
   await openRow(/同工作空间迟到别名/);
  });

  await scenario('uncertain-post-cues-refresh-without-replay', async () => {
   const before = postCount();
   const pattern = '**/api/v2/native-session-view';
   const uncertain = async route => {
    const response = await route.fetch();
    assert.equal(response.status(), 200);
    await route.abort('failed');
   };
   await page.route(pattern, uncertain);
   await rename('响应丢失后核对');
   await body.getByRole('alert').filter({hasText: '刷新列表核对后再操作'}).waitFor();
   assert.equal(postCount(), before + 1);
   await page.unroute(pattern, uncertain);
   await pane.getByRole('button', {name: '刷新原生会话', exact: true}).click();
   await openRow(/响应丢失后核对/);
   assert.equal(postCount(), before + 1);
  });

  await scenario('late-cross-workspace-result-does-not-leak-and-view-persists', async () => {
   let release, seen = false, done = false;
   const held = new Promise(resolve => {release = resolve;});
   const pattern = '**/api/v2/native-session-view';
   const handler = async route => {
    const response = await route.fetch();
    seen = true;
    await held;
    await route.fulfill({response});
    done = true;
   };
   await page.route(pattern, handler);
   await rename('迟到跨工作空间别名');
   await until(() => seen);
   await recent('B').click();
   await until(async () => await header.textContent() === 'B');
   await native.click();
   await openRow(/^Codex 会话 .*默认来源/, 'DEFAULT_CODEX_B');
   release();
   await until(() => done);
   assert.equal(await pane.getByText(/迟到跨工作空间别名/).count(), 0);
   assert.equal(await body.getByText(/DEFAULT_CODEX_A/).count(), 0);
   await page.unroute(pattern, handler);
   await close();
   await recent('A').click();
   await until(async () => await header.textContent() === 'A');
   await native.click();
   await openRow(/迟到跨工作空间别名/);
   await action('隐藏').click();
   await rows(/迟到跨工作空间别名/).waitFor({state: 'detached'});
  });
 } finally {
  await context.tracing.stop({path: path.join(out, stage + '-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, stage + '-results.json'), JSON.stringify({cases, requests, errors}, null, 2));
  assert.equal(errors.length, 0);
  assert(!requests.some(item => /providers\/discover|\/launch|\/cancel/.test(item.route)));
 }
}

main().catch(error => {console.error(error.message); process.exitCode = 1;});
