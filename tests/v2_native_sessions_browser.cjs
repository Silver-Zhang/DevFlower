const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out, mode] = process.argv.slice(2);
const workspaceRoot = process.env.V2_WORKSPACES;
const cases = [];
const requests = [];
const errors = [];

async function until(predicate) {
 for (let n = 0; n < 100; n++) {
  if (await predicate()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected user-visible condition did not settle');
}

async function main() {
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 1440, height: 900}});
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => {
  const route = new URL(request.url()).pathname;
  if (route.startsWith('/api/')) requests.push({route, method: request.method()});
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const sessions = pane.getByLabel('会话列表', {exact: true});
 const opener = page.getByRole('button', {name: '原生会话', exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const back = pane.getByRole('button', {name: '返回会话列表', exact: true});
 const header = page.locator('.app-header button');
 const recent = name => page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')});
 const draft = page.getByLabel('工作流名称', {exact: true});
 const close = () => pane.getByRole('button', {name: '关闭原生会话', exact: true}).click();
 const visit = async name => {
  await header.click();
  await page.getByLabel('文件夹路径', {exact: true}).fill(path.join(workspaceRoot, name));
  await page.getByRole('button', {name: '前往', exact: true}).click();
  await page.getByRole('button', {name: '打开此文件夹', exact: true}).click();
  await until(async () => await header.textContent() === name);
 };
 const panel = async () => {
  await opener.click();
  await pane.waitFor();
 };
 const caseRun = async (name, test) => {
  try {
   await test();
   await page.screenshot({path: path.join(out, mode + '-' + name + '.png')});
   cases.push({name, status: 'PASS'});
   console.log('PASS ' + name);
  } catch (error) {
   cases.push({name, status: 'FAIL', error: error.stack});
   await page.screenshot({path: path.join(out, mode + '-' + name + '-FAIL.png')}).catch(() => {});
   console.error('FAIL ' + name + ' ' + error.message);
   throw error;
  }
 };
 const defaultRow = provider => sessions.getByRole('button', {name: new RegExp('^' + provider + ' 会话 .*默认来源')});
 const read = async (button, marker) => {
  await button.click();
  await body.getByText(marker, {exact: false}).waitFor();
 };
 try {
  await page.goto(process.env.V2_URL);
  if (mode === 'disabled') {
   await caseRun('seeded-restart-disabled-no-scan', async () => {
    await visit('A');
    await page.getByLabel('工作流列表', {exact: true}).getByRole('button', {name: '合成已保存草稿', exact: true}).click();
    await until(async () => await draft.inputValue() === '合成已保存草稿');
    await panel();
    await pane.getByText('本机尚未启用原生会话只读浏览。', {exact: true}).waitFor();
    assert(await pane.getByRole('button', {name: '刷新原生会话', exact: true}).isDisabled());
    assert(!requests.some(request => request.route.startsWith('/api/v2/native-session')));
   });
   return;
  }

  await caseRun('approved-default-nested-and-isolated-visible', async () => {
   await visit('B');
   await visit('A');
   await draft.fill('合成已保存草稿');
   await page.getByRole('button', {name: '保存', exact: true}).click();
   await until(async () => await page.locator('.save-state').innerText() === '已保存');
   await panel();
   await sessions.getByText('已检查登记范围', {exact: true}).waitFor();
   assert.equal(await sessions.getByRole('button').count(), 4);
   await read(defaultRow('Codex'), 'DEFAULT_CODEX_A <b>plain</b>');
   assert.equal(await body.locator('b').count(), 0);
   const filter = body.getByLabel('搜索当前可见窗口', {exact: true});
   await filter.fill('PRIVATE_');
   await body.getByText('当前窗口没有匹配内容。', {exact: true}).waitFor();
   await filter.fill('DONT_SHOW');
   await body.getByText('当前窗口没有匹配内容。', {exact: true}).waitFor();
   await filter.fill('[REDACTED]');
   await body.getByText('DEFAULT_CODEX_FINAL', {exact: false}).waitFor();
   await back.click();
   await read(defaultRow('Claude Code'), 'DEFAULT_CLAUDE_A');
   assert.equal(await body.getByText(/PRIVATE_CLAUDE/).count(), 0);
   await back.click();
   await read(sessions.getByRole('button', {name: /项目嵌套来源/}), 'NESTED_CLAUDE_A');
   await back.click();
   await read(sessions.getByRole('button', {name: /隔离来源 reviewer/}), 'ISOLATED_CODEX_A');
   await close();
   await until(() => opener.evaluate(element => element === document.activeElement));
   assert.equal(await draft.inputValue(), '合成已保存草稿');
  });

  await caseRun('delay-selection-and-workspace-responses-stay-scoped', async () => {
   await panel();
   const pattern = '**/api/v2/native-session?*';
   let release, seen = false, finished = false;
   const delay = new Promise(resolve => {release = resolve;});
   const hold = async route => {
    if (seen) return route.continue();
    const response = await route.fetch();
    seen = true;
    await delay;
    await route.fulfill({response});
    finished = true;
   };
   await page.route(pattern, hold);
   await defaultRow('Codex').click();
   await until(() => seen);
   await read(defaultRow('Claude Code'), 'DEFAULT_CLAUDE_A');
   release();
   await until(() => finished);
   assert.equal(await body.getByText(/DEFAULT_CODEX_A/).count(), 0);
   await page.unroute(pattern, hold);
   await back.click();

   let releaseOther, seenOther = false, finishedOther = false;
   const delayOther = new Promise(resolve => {releaseOther = resolve;});
   const holdOther = async route => {
    if (seenOther) return route.continue();
    const response = await route.fetch();
    seenOther = true;
    await delayOther;
    await route.fulfill({response});
    finishedOther = true;
   };
   await page.route(pattern, holdOther);
   await defaultRow('Codex').click();
   await until(() => seenOther);
   await recent('B').click();
   await until(async () => await header.textContent() === 'B');
   assert.equal(await pane.count(), 0);
   await panel();
   await read(defaultRow('Codex'), 'DEFAULT_CODEX_B');
   releaseOther();
   await until(() => finishedOther);
   assert.equal(await body.getByText(/DEFAULT_CODEX_A/).count(), 0);
   await page.unroute(pattern, holdOther);
   await close();
  });

  await caseRun('late-list-does-not-restore-a-closed-workspace-panel', async () => {
   await recent('A').click();
   await until(async () => await header.textContent() === 'A');
   const pattern = '**/api/v2/native-sessions?*';
   let release, seen = false, done = false;
   const delay = new Promise(resolve => {release = resolve;});
   const hold = async route => {
    if (seen) return route.continue();
    const response = await route.fetch();
    seen = true;
    await delay;
    await route.fulfill({response});
    done = true;
   };
   await page.route(pattern, hold);
   await panel();
   await until(() => seen);
   await recent('B').click();
   await until(async () => await header.textContent() === 'B');
   await panel();
   await defaultRow('Codex').waitFor();
   release();
   await until(() => done);
   assert.equal(await sessions.getByRole('button').count(), 1);
   assert.equal(await sessions.getByText(/隔离来源/).count(), 0);
   await page.unroute(pattern, hold);
   await close();
  });

  await caseRun('narrow-keyboard-empty-and-no-write-entry', async () => {
   for (const width of [600, 900]) {
    await page.setViewportSize({width, height: 900});
    await opener.focus();
    await page.keyboard.press('Enter');
    await read(defaultRow('Codex'), 'DEFAULT_CODEX_B');
    await back.focus();
    await page.keyboard.press('Escape');
    assert.equal(await body.count(), 0);
    await until(() => defaultRow('Codex').evaluate(element => element === document.activeElement));
    await page.keyboard.press('Escape');
    await until(() => opener.evaluate(element => element === document.activeElement));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
   }
   await visit('Empty');
   await panel();
   await sessions.getByText('当前工作空间没有匹配的原生会话。', {exact: true}).waitFor();
   await sessions.getByText('扫描覆盖不完整', {exact: true}).waitFor();
   assert.equal(await pane.getByRole('button', {name: /发送|恢复|终端|归档|删除/}).count(), 0);
   assert(requests.filter(item => item.route.startsWith('/api/v2/native-session')).every(item => item.method === 'GET'));
  });
 } finally {
  await context.tracing.stop({path: path.join(out, mode + '-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, mode + '-results.json'), JSON.stringify({cases, errors, requests}, null, 2));
  assert.equal(errors.length, 0);
  assert(!requests.some(item => /providers\/discover|\/launch|\/cancel/.test(item.route)));
 }
}

main().catch(error => {console.error(error.message); process.exitCode = 1;});
