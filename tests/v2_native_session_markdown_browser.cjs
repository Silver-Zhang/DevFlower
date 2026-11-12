const {openReaderDisclosure} = require('./v2_native_browser_controls.cjs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const [out] = process.argv.slice(2);
const expected = JSON.parse(fs.readFileSync(path.join(out, 'synthetic-text.json'), 'utf8'));
const PAGE = '**/api/v2/native-session-page?*';
const SEARCH = '**/api/v2/native-session-search';
const TRANSCRIPT = '**/api/v2/native-session?*';
const UNSAFE = '无法安全格式化，显示纯文本。';
const TRUNCATED = '可能截断，显示纯文本。';
const INCOMPLETE = '此消息跨页或片段不完整，显示纯文本。';
const SAFE = 'p span br hr h1 h2 h3 h4 h5 h6 strong em del blockquote ul ol li pre code table thead tbody tr th td sup section div'.split(' ');
const cases = [], requests = [], errors = [], zeroTraffic = [], externalAttempts = [], injections = [];
let caughtUnsafe = 0;

async function until(test) {
 for (let n = 0; n < 100; n++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function holdNext(page, pattern) {
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
 await page.route(pattern, handler);
 return {
  ready: () => until(() => seen),
  finish: async () => { release(); await until(() => done); await page.unroute(pattern, handler); },
 };
}

async function main() {
 const origin = new URL(process.env.V2_URL).origin;
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const context = await browser.newContext({viewport: {width: 900, height: 900}});
 await context.route(url => url.origin !== origin, async route => {
  externalAttempts.push(new URL(route.request().url()).protocol);
  await route.abort('blockedbyclient');
 });
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 page.on('pageerror', error => errors.push(error.message));
 page.on('console', message => {
  if (message.type() === 'error' && message.text().includes('Unsafe transcript Markdown tree')) caughtUnsafe++;
 });
 page.on('request', request => {
  const url = new URL(request.url());
  requests.push({sameOrigin: url.origin === origin, path: url.pathname, method: request.method()});
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const pane = page.getByLabel('原生会话', {exact: true});
 const list = pane.getByLabel('会话列表', {exact: true});
 const body = pane.getByLabel('会话正文', {exact: true});
 const recent = body.getByLabel('已加载消息', {exact: true});
 const parts = body.getByLabel('已核验消息片段', {exact: true});
 const format = body.getByRole('combobox', {name: '会话正文格式', exact: true});
 const useFormat = async () => { await openReaderDisclosure(body, '会话操作'); return format; };
 const native = page.getByRole('button', {name: '原生会话', exact: true});
 const close = pane.getByRole('button', {name: '关闭原生会话', exact: true});
 const refresh = pane.getByRole('button', {name: '刷新原生会话', exact: true});
 const header = page.locator('.app-header button');
 const row = provider => list.getByRole('button', {name: new RegExp('^' + provider + ' 会话 .*默认来源')});
 const action = name => body.getByRole('button', {name, exact: true});
 const category = name => list.getByRole('radio', {name, exact: true});
 const find = body.getByRole('searchbox', {name: '搜索整个可见会话', exact: true});
 const windowFilter = body.getByRole('searchbox', {name: '搜索当前可见窗口', exact: true});
 const read = async (provider = 'Codex') => {
  await row(provider).click();
  await (await useFormat()).waitFor();
 };
 const open = async (provider = 'Codex') => {
  await native.click();
  await read(provider);
 };
 const visit = async name => {
  await page.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')}).click();
  await until(async () => await header.textContent() === name);
 };
 const search = async text => {
  await openReaderDisclosure(body, '查找消息');
  await find.fill(text);
  await find.press('Enter');
 };
 const verified = () => body.getByText('已完整核验当前快照的可见历史。', {exact: true}).waitFor();
 const plainTexts = root => root.locator('article > pre').allTextContents();
 const noRequests = async (label, test) => {
  const before = requests.length;
  try {
   await test();
   await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  } finally {
   const count = requests.length - before;
   zeroTraffic.push({label, count});
   assert.equal(count, 0, 'unexpected request in ' + label);
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
 const audit = async root => {
  const nodes = await root.locator('.native-markdown, .native-markdown *').evaluateAll(elements => elements.map(node => ({
   tag: node.localName, attributes: Object.fromEntries([...node.attributes].map(attribute => [attribute.name, attribute.value])),
  })));
  assert(nodes.length > 0);
  for (const {tag, attributes} of nodes) {
   assert(SAFE.includes(tag), 'unexpected Markdown element ' + tag);
   if (attributes.class === 'native-markdown-table') {
    assert.equal(tag, 'div');
    assert.deepEqual(attributes, {class: 'native-markdown-table', role: 'region', 'aria-label': 'Markdown 表格', tabindex: '0'});
   } else if (attributes.class) {
    const expectedTag = attributes.class === 'native-markdown' ? 'div' : 'span';
    assert.equal(tag, expectedTag);
    assert(['native-markdown', 'native-md-link', 'native-md-image', 'native-md-task'].includes(attributes.class));
    assert.deepEqual(Object.keys(attributes), ['class']);
   } else if (Object.hasOwn(attributes, 'start')) {
    assert.equal(tag, 'ol');
    assert(/^\d{1,9}$/.test(attributes.start));
    assert.deepEqual(Object.keys(attributes), ['start']);
   } else {
    assert.deepEqual(attributes, {});
   }
  }
  assert.equal(await root.locator('a,img,input,script,iframe,frame,object,embed,svg,form,style,[href],[src],[srcset],[onclick],[onerror],[onload]').count(), 0);
 };
 const modify = async (pattern, change) => {
  const handler = async route => {
   const response = await route.fetch();
   assert.equal(response.status(), 200);
   const result = await response.json();
   await route.fulfill({response, json: change(result)});
  };
  await page.route(pattern, handler);
  return () => page.unroute(pattern, handler);
 };
 try {
  await page.goto(process.env.V2_URL);
  await visit('A');
  await open();

  await check('default-exact-text-and-allowed-markdown-structures', async () => {
   assert.equal(await (await useFormat()).inputValue(), 'text');
   const baseline = await plainTexts(recent);
   for (const value of [expected.rich, expected.links, expected.raw, expected.exactLimit, expected.longWindow]) {
    assert(baseline.includes(value));
   }
   assert.equal(baseline.filter(text => text === expected.duplicate).length, 2);
   assert.equal(await recent.locator('.native-markdown').count(), 0);
   await noRequests('toggle-and-render-900', async () => {
    await (await useFormat()).focus();
    await page.keyboard.press('End');
    assert.equal(await (await useFormat()).inputValue(), 'markdown');
    await recent.locator('.native-markdown h1').getByText('Safe Markdown heading', {exact: true}).waitFor();
    await recent.locator('strong').getByText('strong fixture', {exact: true}).waitFor();
    await recent.locator('em').getByText('emphasis fixture', {exact: true}).waitFor();
    await recent.locator('del').getByText('deleted fixture', {exact: true}).waitFor();
    await recent.locator('blockquote').getByText('Quoted fixture', {exact: true}).waitFor();
    assert.equal(await recent.locator('ol[start="3"]').count(), 1);
    assert.equal(await recent.locator('.native-md-task').count(), 2);
    assert.equal(await recent.locator('table').count(), 1);
    await recent.locator('section li p').filter({hasText: 'Footnote body.'}).waitFor();
    assert.equal(await recent.locator('sup').count(), 1);
    assert.equal(await recent.locator('code').filter({hasText: '<script>inline()</script>'}).count(), 1);
    assert.equal(await recent.locator('pre code').filter({hasText: '<iframe src='}).count(), 1);
    assert.equal(await recent.locator('strong').getByText('DUPLICATE_MARKDOWN', {exact: true}).count(), 2);
    await audit(recent);
    await (await useFormat()).focus();
    await page.keyboard.press('Home');
    assert.deepEqual(await plainTexts(recent), baseline);
   });
  });

  await check('dangerous-html-isolated-fallback-and-neutralized-media', async () => {
   const startUrl = page.url();
   await noRequests('dangerous-markdown-and-fallback', async () => {
    await (await useFormat()).selectOption('markdown');
    await recent.getByText(UNSAFE, {exact: true}).waitFor();
    const raw = recent.locator('article').filter({hasText: 'Raw HTML retains this syntax'});
    assert.equal(await raw.locator('pre').textContent(), expected.raw);
    assert.equal(await raw.locator('strong').count(), 0);
    assert.equal(await raw.getByText(UNSAFE, {exact: true}).count(), 1);
    assert.equal(await recent.locator('strong').getByText('strong fixture', {exact: true}).count(), 1);
    for (const text of ['Remote label', 'JS label', 'Data label', 'VB label', 'File label', 'Blob label',
                        'Relative label', 'https://outside.invalid/autolink']) {
     assert.equal(await recent.locator('.native-md-link').getByText(text, {exact: true}).count(), 1);
    }
    await recent.locator('.native-md-image').getByText('Remote image alternative', {exact: true}).waitFor();
    await recent.getByText('JS label', {exact: true}).click();
    assert.equal(page.url(), startUrl);
    assert.equal(await page.evaluate(() => Boolean(globalThis.__markdownExecuted)), false);
    const displayed = await recent.textContent();
    for (const value of ['PRIVATE_MARKDOWN_MARKER', 'PRIVATE_TOOL_MARKER', 'ghp_SYNTHETIC_MD_CREDENTIAL']) {
     assert(!displayed.includes(value));
    }
    await audit(recent);
   });
  });

  await check('recent-truncation-codepoints-and-second-provider', async () => {
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   await noRequests('window-cutoffs-and-original-filter', async () => {
    assert.equal(await recent.getByText(TRUNCATED, {exact: true}).count(), 3);
    const baseline = await plainTexts(recent);
    assert(baseline.includes(expected.exactLimit));
    assert(baseline.includes(expected.longWindow));
    await openReaderDisclosure(body, '查找消息');
    await windowFilter.fill('**strong fixture**');
    await recent.locator('strong').getByText('strong fixture', {exact: true}).waitFor();
    assert.equal(await recent.locator('article').count(), 1);
    await windowFilter.clear();
   });
   await read('Claude Code');
   assert.equal(await (await useFormat()).inputValue(), 'text');
   assert.deepEqual(await plainTexts(recent), [expected.claude, expected.exactLimit]);
   await noRequests('second-provider-complete-16000', async () => {
    await (await useFormat()).selectOption('markdown');
    await recent.locator('h2').getByText('Claude fixture heading', {exact: true}).waitFor();
    await recent.locator('strong').getByText('EXACT_LIMIT', {exact: true}).waitFor();
    assert.equal(await recent.getByText(TRUNCATED, {exact: true}).count(), 0);
   });

   const restore = await modify(TRANSCRIPT, result => {
    injections.push('unknown-truncation-synthetic-response');
    return {...result, truncated: null};
   });
   await read('Claude Code');
   await noRequests('unknown-window-shape-falls-back', async () => {
    await (await useFormat()).selectOption('markdown');
    assert.equal(await recent.getByText(TRUNCATED, {exact: true}).count(), 2);
    assert.deepEqual(await plainTexts(recent), [expected.claude, expected.exactLimit]);
   });
   await restore();
   await read();
  });

  await check('same-page-reassembly-and-cross-part-search', async () => {
   await (await useFormat()).selectOption('markdown');
   await search('JOINTSPLIT');
   await verified();
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   await parts.locator('strong').getByText('JOINTSPLIT', {exact: true}).waitFor();
   assert.equal(await parts.locator('article').count(), 1);
   assert.equal(await parts.getByText(INCOMPLETE, {exact: true}).count(), 0);
   await noRequests('same-page-format-toggle', async () => {
    await (await useFormat()).selectOption('text');
    const split = await plainTexts(parts);
    assert.equal(split.length, 2);
    assert.equal(split.join(''), expected.same);
    assert.equal(await parts.getByText(/片段 1\/2/).count(), 1);
    assert.equal(await parts.getByText(/片段 2\/2/).count(), 1);
    await (await useFormat()).selectOption('markdown');
    await parts.locator('strong').getByText('JOINTSPLIT', {exact: true}).waitFor();
    assert.equal(await parts.locator('article').count(), 1);
   });
   await search('DUPLICATE_MARKDOWN');
   await verified();
   assert.equal(await parts.locator('strong').getByText('DUPLICATE_MARKDOWN', {exact: true}).count(), 2);
   await action('返回默认窗口').click();
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   await recent.locator('h1').getByText('Safe Markdown heading', {exact: true}).waitFor();
  });

  await check('cross-page-and-unknown-parts-stay-plain', async () => {
   await search('CROSS_PAGE_MATCH');
   await verified();
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   assert.equal(await parts.locator('.native-markdown').count(), 0);
   assert.equal(await parts.getByText(INCOMPLETE, {exact: true}).count(), 2);
   assert((await plainTexts(parts))[0].startsWith('**CROSS_PAGE_MATCH**'));
   await action('下一页搜索结果').click();
   await verified();
   assert.equal(await parts.locator('.native-markdown').count(), 0);
   assert.equal(await parts.getByText(INCOMPLETE, {exact: true}).count(), 2);
   assert.equal(await parts.getByText(/片段 3\/4/).count(), 1);
   await action('上一页搜索结果').click();
   await verified();
   assert.equal(await parts.getByText(/片段 1\/4/).count(), 1);

   const restore = await modify(SEARCH, result => {
    assert.equal(result.messages.length, 2);
    injections.push('fragment-shape-synthetic-response');
    const [a, b] = result.messages;
    const missingIndex = {role: b.role, text: b.text, part_count: b.part_count};
    return {...result, messages: [a, null, missingIndex, {role: 'tool', text: 'INJECTED_UNKNOWN_ROLE', part_index: 1, part_count: 1}]};
   });
   await search('JOINTSPLIT');
   await verified();
   assert.equal(await parts.locator('.native-markdown').count(), 0);
   assert.equal(await parts.getByText(INCOMPLETE, {exact: true}).count(), 4);
   assert.equal((await plainTexts(parts)).join(''), expected.same);
   assert.equal(await parts.getByText('INJECTED_UNKNOWN_ROLE', {exact: true}).count(), 0);
   await restore();

   const restoreArray = await modify(SEARCH, result => {
    injections.push('non-array-parts-synthetic-response');
    return {...result, messages: {role: 'assistant', text: 'INJECTED_NON_ARRAY'}};
   });
   await search('JOINTSPLIT');
   await verified();
   await parts.getByText(INCOMPLETE, {exact: true}).waitFor();
   assert.equal(await parts.getByText('INJECTED_NON_ARRAY', {exact: true}).count(), 0);
   await restoreArray();
   await action('返回默认窗口').click();
  });

  await check('narrow-keyboard-switch-and-local-table-scroll', async () => {
   await page.setViewportSize({width: 600, height: 900});
   await noRequests('keyboard-and-table-600', async () => {
    await (await useFormat()).focus();
    await page.keyboard.press('Home');
    assert.equal(await (await useFormat()).inputValue(), 'text');
    await page.keyboard.press('End');
    assert.equal(await (await useFormat()).inputValue(), 'markdown');
    const table = recent.getByRole('region', {name: 'Markdown 表格', exact: true});
    assert(await table.evaluate(node => node.scrollWidth > node.clientWidth));
    await table.focus();
    await page.keyboard.press('ArrowRight');
    await until(() => table.evaluate(node => node.scrollLeft > 0));
    for (const locator of [pane, body]) {
     assert(await locator.evaluate(node => node.scrollWidth <= node.clientWidth + 1));
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
   });
   await page.setViewportSize({width: 900, height: 900});
  });

  await check('page-query-and-session-late-results-keep-current-format', async () => {
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   const oldPage = await holdNext(page, PAGE);
   await action('浏览更早消息').click();
   await oldPage.ready();
   await read('Claude Code');
   assert.equal(await (await useFormat()).inputValue(), 'text');
   await (await useFormat()).selectOption('markdown');
   await recent.locator('h2').getByText('Claude fixture heading', {exact: true}).waitFor();
   await oldPage.finish();
   assert.equal(await parts.count(), 0);
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   await recent.locator('h2').getByText('Claude fixture heading', {exact: true}).waitFor();

   await read();
   assert.equal(await (await useFormat()).inputValue(), 'text');
   await (await useFormat()).selectOption('markdown');
   const oldSearch = await holdNext(page, SEARCH);
   await search('JOINTSPLIT');
   await oldSearch.ready();
   await action('浏览更早消息').click();
   await verified();
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
   await body.getByRole('heading', {name: '会话快照分页', exact: true}).waitFor();
   await oldSearch.finish();
   assert.equal(await body.getByRole('heading', {name: '可见会话搜索结果', exact: true}).count(), 0);
   assert.equal(await (await useFormat()).inputValue(), 'markdown');
  });

  await check('workspace-close-refresh-and-collection-reset-format', async () => {
   const oldSearch = await holdNext(page, SEARCH);
   await search('DUPLICATE_MARKDOWN');
   await oldSearch.ready();
   await visit('B');
   await open();
   assert.equal(await (await useFormat()).inputValue(), 'text');
   await oldSearch.finish();
   assert.equal(await parts.count(), 0);
   assert.deepEqual(await plainTexts(recent), ['DEFAULT_CODEX_B']);

   await (await useFormat()).selectOption('markdown');
   await close.click();
   await pane.waitFor({state: 'detached'});
   await open();
   assert.equal(await (await useFormat()).inputValue(), 'text');

   await (await useFormat()).selectOption('markdown');
   await refresh.click();
   await body.waitFor({state: 'detached'});
   await read();
   assert.equal(await (await useFormat()).inputValue(), 'text');

   await (await useFormat()).selectOption('markdown');
   await category('归档').check();
   await body.waitFor({state: 'detached'});
   await category('当前').check();
   await read();
   assert.equal(await (await useFormat()).inputValue(), 'text');
  });
 } finally {
  await context.tracing.stop({path: path.join(out, 'markdown-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({
   cases, requests, errors, zeroTraffic, externalAttempts, caughtUnsafe, injections,
  }, null, 2));
  assert.equal(errors.length, 0);
  assert.equal(externalAttempts.length, 0);
  assert(!requests.some(item => !item.sameOrigin ||
   item.method === 'POST' && !['/api/v2/workspaces/open', '/api/v2/native-session-search'].includes(item.path)));
 }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
