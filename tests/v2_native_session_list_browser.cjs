const {openReaderDisclosure} = require('./v2_native_browser_controls.cjs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out] = process.argv.slice(2);
const FIXED_TIME = new Date('2026-12-20T12:00:00Z');
const VIEW_ROUTE = '**/api/v2/native-session-views?*';
const cases = [], requests = [], errors = [], zeroTraffic = [];

async function until(test) {
 for (let n = 0; n < 100; n++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function holdPreferences(page) {
 let release, seen = false, done = false, captured = false;
 const held = new Promise(resolve => { release = resolve; });
 const handler = async route => {
  if (captured) return route.continue();
  captured = true;
  const response = await route.fetch();
  assert.equal(response.status(), 200);
  seen = true;
  await held;
  await route.fulfill({response});
  done = true;
 };
 await page.route(VIEW_ROUTE, handler);
 return {
  ready: () => until(() => seen),
  finish: async () => { release(); await until(() => done); await page.unroute(VIEW_ROUTE, handler); },
 };
}

async function main() {
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 900, height: 900}});
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 await page.clock.setFixedTime(new Date('2026-12-19T12:00:00Z'));
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => requests.push({path: new URL(request.url()).pathname, method: request.method()}));
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const list = pane.getByLabel('会话列表', {exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const native = page.getByRole('button', {name: '原生会话', exact: true});
 const close = pane.getByRole('button', {name: '关闭原生会话', exact: true});
 const refresh = pane.getByRole('button', {name: '刷新原生会话', exact: true});
 const back = body.getByRole('button', {name: '返回会话列表', exact: true});
 const query = list.getByRole('searchbox', {name: '筛选会话列表', exact: true});
 const grouping = list.getByRole('combobox', {name: '会话列表分组', exact: true});
 const sort = list.getByRole('combobox', {name: '会话列表排序', exact: true});
 const provider = list.getByRole('combobox', {name: '筛选会话 Provider', exact: true});
 const category = label => list.getByRole('radio', {name: label, exact: true});
 const row = name => list.getByRole('button', {name});
 const filteredEmpty = list.getByText('当前集合没有符合筛选条件的会话。', {exact: true});
 const oldEmpty = list.getByText('当前工作空间没有匹配的原生会话。', {exact: true});
 const header = page.locator('.app-header button');
 const visit = async name => {
  await page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')}).click();
  await until(async () => await header.textContent() === name);
 };
 const defaults = async () => {
  assert.equal(await query.inputValue(), '');
  assert.equal(await grouping.inputValue(), 'provider');
  assert.equal(await sort.inputValue(), 'latest');
  assert.equal(await provider.inputValue(), '');
  assert(await category('当前').isChecked());
 };
 const load = async () => {
  if (page.viewportSize().width <= 760) {
   await page.getByRole('button', {name: '展开导航栏', exact: true}).click();
  }
  await native.click();
  await query.waitFor();
 };
 const members = (section = list) => section.locator('button.native-session-item').evaluateAll(nodes => nodes.map(node => {
  const label = node.querySelector('span');
  return {name: label.firstChild.textContent, info: label.querySelector('small').textContent};
 }));
 const names = async section => (await members(section)).map(item => item.name);
 const group = label => list.getByRole('region', {name: label, exact: true});
 const groupNames = () => list.locator('section[aria-label]').evaluateAll(nodes => nodes.map(node => node.ariaLabel));
 const countRead = () => requests.filter(item => item.path === '/api/v2/native-session').length;
 const noRequests = async (label, test) => {
  const before = requests.length;
  try {
   await test();
   await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  } finally {
   const count = requests.length - before;
   zeroTraffic.push({label, count});
   assert.equal(count, 0, 'unexpected network request in ' + label);
  }
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
 const read = async (button, title) => {
  const request = page.waitForRequest(item => new URL(item.url()).pathname === '/api/v2/native-session');
  await button.click();
  const nativeRequest = await request;
  await body.getByRole('heading', {name: title + ' · 只读', exact: true}).waitFor();
  await openReaderDisclosure(body, '查找消息');
  await body.getByRole('searchbox', {name: '搜索当前可见窗口', exact: true}).waitFor();
  return new URL(nativeRequest.url()).searchParams.get('handle');
 };
 try {
  await page.goto(process.env.V2_URL);
  await visit('A');

  await check('nested-settings-only-configuration', async () => {
   await load();
   await list.getByText('项目原生配置',{exact:true}).click();
   await list.getByText('Claude Code · configured/.claude',{exact:true}).waitFor();
   await list.getByText('settings.json · 仅配置',{exact:true}).waitFor();
   assert.equal(await list.getByText('PRIVATE_SETTINGS_FIXTURE').count(),0);
   await close.click();
  });

  await check('preference-publication-and-verified-default-groups', async () => {
   const pending = await holdPreferences(page);
   await native.click();
   await pending.ready();
   assert.equal((await members()).length, 0);
   assert.equal(await query.count(), 0);
   assert.equal(await oldEmpty.count(), 0);
   assert.equal(await filteredEmpty.count(), 0);
   await page.clock.setFixedTime(FIXED_TIME);
   await pending.finish();
   await query.waitFor();
   await defaults();
   assert(await pane.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   assert(await list.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   assert.deepEqual(await groupNames(), ['Codex', 'Claude Code']);
   const defaultCodex = await members(group('Codex'));
   assert.deepEqual(defaultCodex.slice(0, 2).map(item => item.name), ['任务 2 ＱＡ <b>', '任务 10 ＱＡ <b>']);
   assert.deepEqual(defaultCodex.filter(item => item.name === '同名 10').map(item => item.info.split(' · ')[0]),
    ['默认来源', '隔离来源 qa-source']);
   assert.deepEqual(defaultCodex.slice(-2).map(item => item.info.endsWith('时间未知')), [true, true]);
   assert.equal(await row(/_ALIAS_SENTINEL/).count(), 0);
  });

  await check('epoch-ties-groups-and-snapshot-time-reference', async () => {
   await noRequests('epoch-and-groups', async () => {
    await sort.selectOption('earliest');
    const codex = await members(group('Codex'));
    assert.equal(codex[0].name, '正好七天');
    assert.deepEqual(codex.slice(-2).map(item => item.info.endsWith('时间未知')), [true, true]);
    assert.deepEqual(codex.filter(item => item.name === '同名 10').map(item => item.info.split(' · ')[0]),
     ['默认来源', '隔离来源 qa-source']);
    await sort.selectOption('name');
    const ordered = await names(group('Codex'));
    assert(ordered.indexOf('任务 2 ＱＡ <b>') < ordered.indexOf('任务 10 ＱＡ <b>'));
    const ties = (await members(group('Codex'))).filter(item => item.name === '同名 10');
    assert.deepEqual(ties.map(item => item.info.split(' · ')[0]), ['默认来源', '隔离来源 qa-source']);
    await sort.selectOption('latest');
    await grouping.selectOption('activity');
    assert.deepEqual(await groupNames(), ['近 24 小时', '24 小时至 7 天', '更早', '活动时间未知']);
    assert.deepEqual(await names(group('近 24 小时')), ['任务 2 ＱＡ <b>', '任务 10 ＱＡ <b>', '当天边界']);
    assert.deepEqual(await names(group('24 小时至 7 天')), ['同名 10', '同名 10', 'Codex 会话 7', 'Claude Code 会话 2']);
    assert.deepEqual(await names(group('更早')), ['正好七天', '协作 <b>ß']);
    assert.deepEqual(new Set(await names(group('活动时间未知'))), new Set(['未来默认', '非法默认', '无时间']));
    const oldGroups = await groupNames();
    await page.clock.setFixedTime(new Date('2026-12-29T12:00:00Z'));
    await sort.selectOption('earliest');
    assert.deepEqual(await groupNames(), oldGroups);
    assert.equal((await names(group('近 24 小时'))).length, 3);
    await page.clock.setFixedTime(FIXED_TIME);
   });
  });

  await check('unicode-literal-field-matching-and-stable-generated-names', async () => {
   await noRequests('local-text-provider-and-order', async () => {
    await query.fill('  任务 ２ qa <B>  ');
    assert.deepEqual(await names(), ['任务 2 ＱＡ <b>']);
    assert.equal(await list.locator('b').count(), 0);
    await query.fill('<b> codex');
    await filteredEmpty.waitFor();
    await query.fill('ＱＡ－ＳＯＵＲＣＥ');
    assert.deepEqual(await names(), ['同名 10']);
    assert((await members())[0].info.startsWith('隔离来源 qa-source'));
    await query.fill('ＣＯＤＥＸ 会话 ７');
    assert.deepEqual(await names(), ['Codex 会话 7']);
    await grouping.selectOption('provider');
    await sort.selectOption('name');
    assert.deepEqual(await names(), ['Codex 会话 7']);
    await provider.selectOption('claude');
    await filteredEmpty.waitFor();
    await provider.selectOption('codex');
    assert.deepEqual(await names(), ['Codex 会话 7']);
    await provider.selectOption('');
    await query.fill('ＳＳ');
    await filteredEmpty.waitFor();
    await query.fill('ß');
    assert.deepEqual(await names(), ['协作 <b>ß']);
    await query.fill('🙂'.repeat(201));
    assert.equal([...(await query.inputValue())].length, 200);
    await query.fill('SYNTHETIC_A_1');
    await filteredEmpty.waitFor();
    await query.clear();
   });
  });

  await check('collection-privacy-provider-intersection-and-empty-states', async () => {
   await noRequests('local-collection-filters', async () => {
    for (const [label, keyword] of [['隐藏', 'HIDDEN_ALIAS_SENTINEL'], ['归档', 'ARCHIVED_ALIAS_SENTINEL'],
                                   ['回收站', 'TRASH_ALIAS_SENTINEL']]) {
     await query.fill(keyword);
     await filteredEmpty.waitFor();
     assert.equal(await row(/_ALIAS_SENTINEL/).count(), 0);
     await category(label).check();
     assert.equal(await query.inputValue(), keyword);
     assert.deepEqual(await names(), [keyword]);
     await provider.selectOption('claude');
     await filteredEmpty.waitFor();
     assert.equal(await row(/_ALIAS_SENTINEL/).count(), 0);
     await provider.selectOption('');
     assert.deepEqual(await names(), [keyword]);
     await category('当前').check();
    }
    await query.clear();
    assert.equal(await row(/_ALIAS_SENTINEL/).count(), 0);
    assert.equal(await filteredEmpty.count(), 0);
   });
  });

  await check('same-native-id-distinct-rows-and-selected-read-survives-filter', async () => {
   const first = await read(row(/同名 10.*默认来源/), '同名 10');
   const second = await read(row(/同名 10.*隔离来源 qa-source/), '同名 10');
   assert.notEqual(first, second);
   const before = countRead();
   await noRequests('selected-reading-and-focus', async () => {
    await query.fill('没有符合的词');
    await filteredEmpty.waitFor();
    await body.getByRole('status').filter({hasText: '当前阅读的会话不在筛选结果中。'}).waitFor();
    await openReaderDisclosure(body, '查找消息');
    await body.getByRole('searchbox', {name: '搜索整个可见会话', exact: true}).waitFor();
    await sort.selectOption('earliest');
    await grouping.selectOption('activity');
    assert(await body.isVisible());
    await query.clear();
    assert.equal(await body.getByText('当前阅读的会话不在筛选结果中。', {exact: true}).count(), 0);
    await back.click();
    await body.waitFor({state: 'detached'});
    await until(() => row(/同名 10.*隔离来源 qa-source/).evaluate(node => node === document.activeElement));
   });
   assert.equal(countRead(), before);
   await read(row(/同名 10.*隔离来源 qa-source/), '同名 10');
   await noRequests('collection-clears-selection', async () => {
    await category('归档').check();
    await body.waitFor({state: 'detached'});
    await category('当前').check();
   });
  });

  await check('refresh-preserves-controls-and-late-scope-cannot-replace', async () => {
   await query.fill('QA');
   await provider.selectOption('codex');
   const pending = await holdPreferences(page);
   await refresh.click();
   await pending.ready();
   assert.equal((await members()).length, 0);
   assert.equal(await filteredEmpty.count(), 0);
   await pending.finish();
   await query.waitFor();
   assert.equal(await query.inputValue(), 'QA');
   assert.equal(await provider.inputValue(), 'codex');
   assert.equal(await grouping.inputValue(), 'activity');
   assert.equal(await sort.inputValue(), 'earliest');
   assert.equal((await members()).length, 3);

   const refused = async route => {
    assert.equal((await route.fetch()).status(), 200);
    await route.fulfill({status: 503, json: {error: '合成偏好读取失败'}});
   };
   await page.route(VIEW_ROUTE, refused);
   await refresh.click();
   await list.getByRole('alert').filter({hasText: '合成偏好读取失败'}).waitFor();
   assert.equal((await members()).length, 0);
   assert.equal(await filteredEmpty.count(), 0);
   assert.equal(await oldEmpty.count(), 0);
   await page.unroute(VIEW_ROUTE, refused);

   const late = await holdPreferences(page);
   await refresh.click();
   await late.ready();
   await visit('B');
   await load();
   await defaults();
   assert.deepEqual(await names(), ['乙工作区标记']);
   await late.finish();
   assert.deepEqual(await names(), ['乙工作区标记']);
   assert.equal(await row(/_ALIAS_SENTINEL|任务 10|同名 10/).count(), 0);
  });

  await check('narrow-keyboard-navigation-and-reopen-defaults', async () => {
   await page.setViewportSize({width: 600, height: 900});
   await noRequests('narrow-keyboard-controls', async () => {
    await query.focus();
    await page.keyboard.type('invalid');
    await filteredEmpty.waitFor();
    await query.clear();
    await grouping.focus();
    await page.keyboard.press('End');
    assert.equal(await grouping.inputValue(), 'activity');
    await sort.focus();
    await page.keyboard.press('End');
    assert.equal(await sort.inputValue(), 'name');
    assert(await pane.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
    assert(await list.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
   });
   await row(/乙工作区标记/).focus();
   await page.keyboard.press('Enter');
   await openReaderDisclosure(body, '查找消息');
   await body.getByRole('searchbox', {name: '搜索当前可见窗口', exact: true}).focus();
   await page.keyboard.press('Escape');
   assert(await body.isVisible());
   assert(await body.getByText('查找消息', {exact: true}).evaluate(node=>node===document.activeElement));
   await page.keyboard.press('Escape');
   await body.waitFor({state: 'detached'});
   await until(() => row(/乙工作区标记/).evaluate(node => node === document.activeElement));
   await noRequests('empty-collection-at-narrow-width', async () => {
    await category('回收站').check();
    await oldEmpty.waitFor();
    assert.equal(await filteredEmpty.count(), 0);
    await query.fill('任意');
    await filteredEmpty.waitFor();
    assert.equal(await oldEmpty.count(), 0);
   });
   await close.click();
   await pane.waitFor({state: 'detached'});
   await load();
   await defaults();
  });
 } finally {
  await context.tracing.stop({path: path.join(out, 'list-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({cases, requests, errors, zeroTraffic}, null, 2));
  assert.equal(errors.length, 0);
  assert(!requests.some(item => item.method === 'POST' && item.path !== '/api/v2/workspaces/open'));
 }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
