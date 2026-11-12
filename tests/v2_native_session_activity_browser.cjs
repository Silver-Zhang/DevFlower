const {openReaderDisclosure} = require('./v2_native_browser_controls.cjs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out] = process.argv.slice(2);
const ACTIVITY = '**/api/v2/native-session-activity?*';
const ACTIVITY_PATH = '/api/v2/native-session-activity';
const COMPLETE = '已核验当前快照的工具活动。';
const EMPTY = '没有可显示的原生工具活动。';
const cases = [], requests = [], external = [], errors = [];

async function until(test) {
 for (let n = 0; n < 100; n++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function holdActivity(page) {
 let release, seen = false, done = false, captured = false;
 const held = new Promise(resolve => { release = resolve; });
 const handler = async route => {
  if (captured) return route.continue();
  captured = true;
  const result = await route.fetch();
  assert.equal(result.status(), 200);
  seen = true;
  await held;
  await route.fulfill({response: result});
  done = true;
 };
 await page.route(ACTIVITY, handler);
 return {
  ready: () => until(() => seen),
  finish: async () => { release(); await until(() => done); await page.unroute(ACTIVITY, handler); },
 };
}

async function main() {
 const origin = new URL(process.env.V2_URL).origin;
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 900, height: 900}});
 await context.route(url => url.origin !== origin, async route => {
  external.push(new URL(route.request().url()).protocol);
  await route.abort('blockedbyclient');
 });
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => {
  const url = new URL(request.url());
  requests.push({path: url.pathname, method: request.method(), sameOrigin: url.origin === origin});
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const list = pane.getByLabel('会话列表', {exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const panel = body.getByRole('region', {name: '原生工具活动', exact: true});
 const rows = panel.getByRole('listitem');
 const tool = body.getByRole('button', {name: '工具活动', exact: true});
 const back = panel.getByRole('button', {name: '返回正文', exact: true});
 const opener = page.getByRole('button', {name: '原生会话', exact: true});
 const close = pane.getByRole('button', {name: '关闭原生会话', exact: true});
 const refresh = pane.getByRole('button', {name: '刷新原生会话', exact: true});
 const header = page.locator('.app-header button');
 const format = body.getByRole('combobox', {name: '会话正文格式', exact: true});
 const showActions = () => openReaderDisclosure(body, '会话操作');
 const showSearch = () => openReaderDisclosure(body, '查找消息');
 const find = body.getByRole('searchbox', {name: '搜索整个可见会话', exact: true});
 const category = name => list.getByRole('radio', {name, exact: true});
 const row = (provider, label = '默认来源') => list.getByRole('button', {name: new RegExp('^' + provider + ' 会话 .*' + label)});
 const activityGets = () => requests.filter(request => request.path === ACTIVITY_PATH && request.method === 'GET').length;
 const visit = async name => {
  await page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')}).click();
  await until(async () => await header.textContent() === name);
 };
 const read = async (provider = 'Codex', label) => {
  await row(provider, label).click();
  await tool.waitFor();
 };
 const open = async () => {
  await opener.click();
  await read();
 };
 const full = () => panel.getByText(COMPLETE, {exact: true}).waitFor();
 const returned = async () => {
  await back.click();
  await panel.waitFor({state: 'detached'});
 };
 const assertPublicOnly = async () => {
  const value = await panel.textContent();
  for (const privateText of ['RAW_ACTIVITY_ARGUMENT', 'RAW_ACTIVITY_RESULT', 'outside.invalid', 'public-pair',
                             'SYNTHETIC_VENDOR_TOOL', 'SYNTHETIC_ACTIVITY_SECRET', 'PRIVATE_ACTIVITY_REASONING',
                             'SYNTHETIC_CODEX_TRANSCRIPT', '执行成功', '正在运行']) assert(!value.includes(privateText));
  assert.equal(await panel.locator('a, img, input, pre, code').count(), 0);
  assert.equal(await panel.getByRole('button').count(), 1);
 };
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
  await visit('A');
  await open();

  await check('explicit-codex-read-and-fixed-metadata-only', async () => {
   assert.equal(activityGets(), 0);
   assert.equal(await panel.count(), 0);
   const request = page.waitForRequest(item => new URL(item.url()).pathname === ACTIVITY_PATH);
   await tool.click();
   const sent = await request;
   assert.equal(sent.method(), 'GET');
   assert.deepEqual([...new URL(sent.url()).searchParams.keys()].sort(), ['handle', 'revision', 'workspace']);
   await full();
   assert.equal(activityGets(), 1);
   assert.equal(await rows.count(), 12);
   assert.deepEqual(await rows.locator('strong').allTextContents(),
    ['读取', '编辑', '其他', '搜索', '其他', '其他', '其他', '其他', '其他', '其他', '命令', '其他']);
   assert.equal(await rows.first().getByText('观察到配对返回记录', {exact: true}).count(), 1);
   assert.equal(await panel.getByText('记录关联有歧义', {exact: true}).count(), 4);
   assert.equal(await rows.first().locator('time').first().getAttribute('datetime'), '2020-01-02T00:00:00Z');
   assert.equal(await rows.first().locator('time').last().getAttribute('datetime'), '2020-01-01T23:00:00Z');
   assert(await panel.getByText('未知', {exact: true}).count() > 0);
   assert.equal(await format.count(), 0);
   await assertPublicOnly();
   await returned();
   await showActions();
   assert.equal(await format.inputValue(), 'text');
   assert(await body.getByRole('button', {name: '设置本地别名', exact: true}).isVisible());
  });

  await check('claude-rows-and-local-only-affordances', async () => {
   const before = activityGets();
   await read('Claude Code');
   assert.equal(activityGets(), before);
   await tool.click();
   await full();
   assert.equal(await rows.count(), 9);
   assert.deepEqual((await rows.locator('strong').allTextContents()).slice(0, 4), ['读取', '编辑', '搜索', '命令']);
   assert.equal(await panel.getByText('记录未配对', {exact: true}).count(), 1);
   assert.equal(await panel.getByText('仅观察到返回记录', {exact: true}).count(), 2);
   await assertPublicOnly();
   await returned();
  });

  await check('display-limit-and-verified-empty-are-distinct', async () => {
   await read('Codex', '隔离来源 reviewer');
   await tool.click();
   await panel.getByText('只显示最近 100 项活动。', {exact: true}).waitFor();
   assert.equal(await rows.count(), 100);
   assert.deepEqual(new Set(await rows.locator('strong').allTextContents()), new Set(['命令']));
   assert.equal(await panel.getByText(EMPTY, {exact: true}).count(), 0);
   await returned();

   await visit('B');
   await open();
   await tool.click();
   await full();
   await panel.getByText(EMPTY, {exact: true}).waitFor();
   assert.equal(await rows.count(), 0);
   assert.equal(await panel.getByRole('alert').count(), 0);
   await returned();
  });

  await check('actual-stale-refusal-has-no-empty-or-automatic-retry', async () => {
   const before = activityGets();
   const beforeLists = requests.filter(item => item.path === '/api/v2/native-sessions').length;
   const stale = async route => {
    const url = new URL(route.request().url());
    url.searchParams.set('revision', '0'.repeat(32));
    const response = await route.fetch({url: url.href});
    assert.equal(response.status(), 409);
    await route.fulfill({response});
   };
   await page.route(ACTIVITY, stale);
   await tool.click();
   await panel.getByRole('alert').filter({hasText: '会话来源或列表已变化，请刷新会话列表。'}).waitFor();
   assert.equal(await rows.count(), 0);
   assert.equal(await panel.getByText(EMPTY, {exact: true}).count(), 0);
   assert.equal(activityGets(), before + 1);
   assert.equal(requests.filter(item => item.path === '/api/v2/native-sessions').length, beforeLists);
   await page.unroute(ACTIVITY, stale);
   await returned();
  });

  await check('return-before-late-result-preserves-search-format-and-page', async () => {
   await visit('A');
   await open();
   await showActions();
   await format.selectOption('markdown');
   await showSearch();
   await find.fill('SYNTHETIC_CODEX_TRANSCRIPT');
   await find.press('Enter');
   await body.getByRole('heading', {name: '可见会话搜索结果', exact: true}).waitFor();
   await body.locator('.native-markdown strong').filter({hasText: 'SYNTHETIC_CODEX_TRANSCRIPT'}).waitFor();
   const before = requests.length;
   const held = await holdActivity(page);
   await tool.click();
   await held.ready();
   await panel.getByRole('status').filter({hasText: '正在核验原生工具活动…'}).waitFor();
   await returned();
   await held.finish();
   assert.equal(requests.length, before + 1);
   await showActions();
   assert.equal(await format.inputValue(), 'markdown');
   await showSearch();
   assert.equal(await find.inputValue(), 'SYNTHETIC_CODEX_TRANSCRIPT');
   await body.locator('.native-markdown strong').filter({hasText: 'SYNTHETIC_CODEX_TRANSCRIPT'}).waitFor();

   await body.getByRole('button', {name: '浏览更早消息', exact: true}).click();
   await body.getByRole('heading', {name: '会话快照分页', exact: true}).waitFor();
   await body.getByText('已完整核验当前快照的可见历史。', {exact: true}).waitFor();
   const afterPage = requests.length;
   await tool.click();
   await full();
   await returned();
   assert.equal(requests.length, afterPage + 1);
   await showActions();
   assert.equal(await format.inputValue(), 'markdown');
   assert.equal(await body.getByRole('heading', {name: '会话快照分页', exact: true}).count(), 1);
  });

  await check('late-codex-result-cannot-replace-new-claude-activity', async () => {
   const old = await holdActivity(page);
   await tool.click();
   await old.ready();
   await read('Claude Code');
   assert.equal(await panel.count(), 0);
   await tool.click();
   await full();
   assert.equal(await rows.count(), 9);
   await old.finish();
   assert.equal(await rows.count(), 9);
   assert.equal(await panel.getByText('记录关联有歧义', {exact: true}).count(), 2);
   await returned();
  });

  await check('scope-panel-refresh-and-category-discard-late-activity', async () => {
   const oldWorkspace = await holdActivity(page);
   await tool.click();
   await oldWorkspace.ready();
   await visit('B');
   await open();
   await oldWorkspace.finish();
   assert.equal(await panel.count(), 0);

   for (const change of [
    async () => { await close.click(); await pane.waitFor({state: 'detached'}); await open(); },
    async () => { await refresh.click(); await body.waitFor({state: 'detached'}); await read(); },
    async () => { await category('归档').check(); await body.waitFor({state: 'detached'}); await category('当前').check(); await read(); },
   ]) {
    const held = await holdActivity(page);
    await tool.click();
    await held.ready();
    await change();
    await held.finish();
    assert.equal(await panel.count(), 0);
    await showActions();
   assert(await format.isVisible());
   }
  });

  await check('narrow-keyboard-focus-and-no-horizontal-overflow', async () => {
   await page.setViewportSize({width: 600, height: 900});
   const before = activityGets();
   await tool.focus();
   await page.keyboard.press('Enter');
   await full();
   await until(() => back.evaluate(node => node === document.activeElement));
   assert(await pane.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   assert(await panel.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
   await page.screenshot({path: path.join(out, 'activity-600x900.png')});
   await page.keyboard.press('Escape');
   await panel.waitFor({state: 'detached'});
   await until(() => tool.evaluate(node => node === document.activeElement));
   assert.equal(activityGets(), before + 1);
  });
 } finally {
  await context.tracing.stop({path: path.join(out, 'activity-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({
   cases, requests, external, errors, activityGets: activityGets(),
  }, null, 2));
  assert.equal(errors.length, 0);
  assert.equal(external.length, 0);
  assert(!requests.some(item => !item.sameOrigin || item.path === ACTIVITY_PATH && item.method !== 'GET' ||
   item.method === 'POST' && !['/api/v2/workspaces/open', '/api/v2/native-session-search'].includes(item.path)));
 }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
