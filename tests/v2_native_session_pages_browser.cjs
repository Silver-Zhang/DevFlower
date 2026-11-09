const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out] = process.argv.slice(2);
const cases = [], requests = [], errors = [];
const PAGE = '**/api/v2/native-session-page?*';
const SEARCH = '**/api/v2/native-session-search';

async function until(test) {
 for (let count = 0; count < 100; count++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function holdNext(page, pattern, {abort = false} = {}) {
 let release, seen = false, done = false, captured = false;
 const held = new Promise(resolve => { release = resolve; });
 const handler = async route => {
  if (captured) return route.continue();
  captured = true;
  const response = await route.fetch();
  assert.equal(response.status(), 200);
  seen = true;
  await held;
  if (abort) await route.abort('failed');
  else await route.fulfill({response});
  done = true;
 };
 await page.route(pattern, handler);
 return {
  ready: () => until(() => seen),
  finish: async () => { release(); await until(() => done); await page.unroute(pattern, handler); },
 };
}

async function main() {
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 900, height: 900}});
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => {
  const url = new URL(request.url());
  if (url.pathname.startsWith('/api/')) {
   requests.push({route: url.pathname, method: request.method(),
    searchHasUrlQuery: url.pathname.endsWith('/native-session-search') && Boolean(url.search)});
  }
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const list = pane.getByLabel('会话列表', {exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const parts = body.getByLabel('已核验消息片段', {exact: true});
 const window = body.getByLabel('已加载消息', {exact: true});
 const native = page.getByRole('button', {name: '原生会话', exact: true});
 const back = pane.getByRole('button', {name: '返回会话列表', exact: true});
 const close = pane.getByRole('button', {name: '关闭原生会话', exact: true});
 const header = page.locator('.app-header button');
 const recent = name => page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')});
 const action = name => body.getByRole('button', {name, exact: true});
 const query = body.getByRole('searchbox', {name: '搜索整个可见会话', exact: true});
 const scopeCount = () => requests.filter(item => item.route === '/api/v2/native-sessions').length;
 const historyCount = () => requests.filter(item => /native-session-(page|search)$/.test(item.route)).length;
 const row = provider => list.getByRole('button', {name: new RegExp('^' + provider + ' 会话 .*默认来源')});
 const visit = async name => {
  await header.click();
  await page.getByLabel('文件夹路径', {exact: true}).fill(path.join(process.env.V2_WORKSPACES, name));
  await page.getByRole('button', {name: '前往', exact: true}).click();
  await page.getByRole('button', {name: '打开此文件夹', exact: true}).click();
  await until(async () => await header.textContent() === name);
 };
 const open = async (provider = 'Codex', marker = 'CODEX_VISIBLE_165') => {
  await row(provider).click();
  await window.getByText(marker, {exact: true}).waitFor();
 };
 const search = async text => {
  await query.fill(text);
  await query.press('Enter');
 };
 const verified = () => body.getByText('已完整核验当前快照的可见历史。', {exact: true}).waitFor();
 const check = async (name, test) => {
  try {
   await test();
   await page.screenshot({path: path.join(out, name + '.png')});
   cases.push({name, status: 'PASS'});
   console.log('PASS ' + name);
  } catch (error) {
   cases.push({name, status: 'FAIL', error: error.stack});
   await page.screenshot({path: path.join(out, name + '-FAIL.png')}).catch(() => {});
   console.error('FAIL ' + name + ' ' + error.message);
   throw error;
  }
 };
 try {
  await page.goto(process.env.V2_URL);
  await visit('B');
  await visit('A');
  await native.click();
  await open();

  await check('default-window-and-explicit-read-only-search', async () => {
   const old = body.getByRole('searchbox', {name: '搜索当前可见窗口', exact: true});
   const count = historyCount();
   await old.fill('STRASSE');
   await window.getByText('当前窗口没有匹配内容。', {exact: true}).waitFor();
   await old.clear();
   await query.fill('STRASSE');
   assert.equal(historyCount(), count);
   const posted = page.waitForRequest(req => new URL(req.url()).pathname === '/api/v2/native-session-search');
   await query.press('Enter');
   const request = await posted;
   assert.equal(request.method(), 'POST');
   assert.equal(new URL(request.url()).search, '');
   assert.equal(request.postDataJSON().query, 'STRASSE');
   await verified();
   await parts.getByText('EARLIEST Straße first-only <b>plain</b>', {exact: true}).waitFor();
   assert.equal(await body.locator('b').count(), 0);
   await action('返回默认窗口').click();
   await window.getByText('CODEX_VISIBLE_165', {exact: true}).waitFor();
  });

  await check('browse-earlier-and-return-newer-at-narrow-width', async () => {
   await page.setViewportSize({width: 600, height: 900});
   await action('浏览更早消息').focus();
   await page.keyboard.press('Enter');
   await verified();
   await parts.getByText('CODEX_VISIBLE_165', {exact: true}).waitFor();
   const heading = body.getByRole('heading', {name: '会话快照分页', exact: true});
   await until(() => heading.evaluate(node => node === document.activeElement));
   assert.equal(await parts.getByText(/EARLIEST Straße/).count(), 0);
   let earlier = 0;
   while (await action('更早一页').isVisible()) {
    assert(earlier++ < 12);
    await action('更早一页').click();
    await verified();
   }
   assert(earlier >= 3);
   await parts.getByText(/EARLIEST Straße/).waitFor();
   await action('较新一页').click();
   await verified();
   assert.equal(await parts.getByText(/EARLIEST Straße/).count(), 0);
   assert(await pane.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   await action('返回默认窗口').click();
   await window.getByText('CODEX_VISIBLE_165', {exact: true}).waitFor();
  });

  await check('cross-part-search-order-and-private-no-match', async () => {
   await page.setViewportSize({width: 900, height: 900});
   await search('crossboundary');
   await verified();
   await parts.getByText(/片段 1\/5/).waitFor();
   assert.equal(await parts.getByText('CrossBoundary CrossBoundary later match', {exact: true}).count(), 0);
   let later = 0;
   while (await action('下一页搜索结果').isVisible()) {
    assert(later++ < 10);
    await action('下一页搜索结果').click();
    await verified();
   }
   assert(later >= 2);
   await parts.getByText('CrossBoundary CrossBoundary later match', {exact: true}).waitFor();
   await action('上一页搜索结果').click();
   await verified();
   assert.equal(await parts.getByText('CrossBoundary CrossBoundary later match', {exact: true}).count(), 0);
   for (const text of ['ghp_SYNTHETIC_PAGE_PASSWORD', 'SYNTHETIC_PRIVATE_PAGE_MARKER']) {
    await search(text);
    await parts.getByText('此快照的可见会话没有匹配内容。', {exact: true}).waitFor();
   }
   await action('返回默认窗口').click();
  });

  await check('switching-modes-discards-late-result-and-late-error', async () => {
   const oldPage = await holdNext(page, PAGE);
   await action('浏览更早消息').click();
   await oldPage.ready();
   await search('STRASSE');
   await parts.getByText(/EARLIEST Straße/).waitFor();
   await oldPage.finish();
   await parts.getByText(/EARLIEST Straße/).waitFor();
   assert.equal(await body.getByRole('heading', {name: '会话快照分页'}).count(), 0);

   const oldSearch = await holdNext(page, SEARCH, {abort: true});
   await search('crossboundary');
   await oldSearch.ready();
   await action('浏览更早消息').click();
   await parts.getByText('CODEX_VISIBLE_165', {exact: true}).waitFor();
   await oldSearch.finish();
   assert.equal(await body.getByRole('alert').count(), 0);
   await parts.getByText('CODEX_VISIBLE_165', {exact: true}).waitFor();
  });

  await check('selection-workspace-and-close-invalidate-pending-history', async () => {
   const oldRow = await holdNext(page, PAGE);
   await action('浏览更早消息').click();
   await oldRow.ready();
   await open('Claude Code', 'CLAUDE_VISIBLE_110');
   await oldRow.finish();
   assert.equal(await parts.count(), 0);
   await window.getByText('CLAUDE_VISIBLE_110', {exact: true}).waitFor();

   await open();
   const oldWorkspace = await holdNext(page, SEARCH);
   await search('STRASSE');
   await oldWorkspace.ready();
   await recent('B').click();
   await until(async () => await header.textContent() === 'B');
   await native.click();
   await open('Codex', 'WORKSPACE_B_VISIBLE_054');
   await oldWorkspace.finish();
   assert.equal(await parts.count(), 0);
   await window.getByText('WORKSPACE_B_VISIBLE_054', {exact: true}).waitFor();

   const oldPanel = await holdNext(page, SEARCH);
   await search('WORKSPACE_B_VISIBLE');
   await oldPanel.ready();
   await close.click();
   await pane.waitFor({state: 'detached'});
   await native.click();
   await open('Codex', 'WORKSPACE_B_VISIBLE_054');
   await oldPanel.finish();
   assert.equal(await parts.count(), 0);
   assert.equal(await query.inputValue(), '');
  });

  await check('invalid-cursor-prompts-reopen-without-refresh-or-replay', async () => {
   await action('浏览更早消息').click();
   await verified();
   const count = scopeCount();
   const before = historyCount();
   const invalid = async route => {
    const url = new URL(route.request().url());
    url.searchParams.set('cursor', '0'.repeat(32));
    await route.fulfill({response: await route.fetch({url: url.href})});
   };
   await page.route(PAGE, invalid);
   await action('更早一页').click();
   await body.getByRole('alert').getByText(/翻页已失效.*重新打开会话/).waitFor();
   assert.equal(scopeCount(), count);
   assert.equal(historyCount(), before + 1);
   await page.unroute(PAGE, invalid);
   await page.keyboard.press('Escape');
   await body.waitFor({state: 'detached'});
   await until(() => row('Codex').evaluate(node => node === document.activeElement));
   await open('Codex', 'WORKSPACE_B_VISIBLE_054');
   assert.equal(await parts.count(), 0);
   assert(await back.isVisible());
  });
 } finally {
  await context.tracing.stop({path: path.join(out, 'history-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({cases, requests, errors}, null, 2));
  assert.equal(errors.length, 0);
  assert(!requests.some(item => item.searchHasUrlQuery ||
   item.method === 'POST' && /native-session-view|providers\/discover|\/launch|\/cancel/.test(item.route)));
 }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
