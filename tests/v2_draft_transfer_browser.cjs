const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const [out, stage] = process.argv.slice(2);
const fixture = JSON.parse(fs.readFileSync(path.join(out, 'synthetic-fixture.json'), 'utf8'));
const state = process.env.V2_FIXTURE_STATE;
const VALIDATE = '/api/v2/drafts/validate';
const SAVE = '/api/v2/drafts/save';
const DRAFT = '/api/v2/draft';
const OPENED = '已打开未保存的工作流，保存后将创建新副本。';
const source = fixture.source.graph;
const copyFile = path.join(out, 'devflower-workflow.json');
const cases = [], requests = [], responses = [], errors = [], external = [], downloads = [];
const tests = {readOnlyBeforeSave: false, downloadSha: null, downloadSize: 0, createdId: null};
const encode = value => JSON.stringify(Array.isArray(value) ? value.map(item => JSON.parse(encode(item))) :
 value !== null && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, JSON.parse(encode(value[key]))])) : value);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const makeArchive = document => Buffer.from(encode({format: 'devflower.v2.workflow-draft', version: 1, document,
 document_sha256: sha(Buffer.from(encode(document)))}) + '\n');
const blank = {version: 1, name: '新工作流', nodes: [], edges: [], viewport: {x: 0, y: 0, zoom: 1}};

async function until(test) {
 for (let n = 0; n < 120; n++) {
  if (await test()) return;
  await new Promise(resolve => setTimeout(resolve, 50));
 }
 throw new Error('expected browser state did not settle');
}

async function holdNext(page, routePath) {
 let release, seen = false, done = false, captured = false;
 const pending = new Promise(resolve => { release = resolve; });
 const matches = url => url.pathname === routePath;
 const handler = async route => {
  if (captured) return route.continue();
  captured = true;
  const response = await route.fetch();
  assert.equal(response.status(), 200);
  seen = true;
  await pending;
  try { await route.fulfill({response}); }
  finally { done = true; }
 };
 await page.route(matches, handler);
 return {
  ready: () => until(() => seen),
  finish: async () => { release(); await until(() => done); await page.unroute(matches, handler); },
 };
}

async function main() {
 const origin = new URL(process.env.V2_URL).origin;
 const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
 const options = {viewport: {width: 900, height: 900}, acceptDownloads: true};
 if (stage === 'restart') options.storageState = path.join(out, 'initial-storage.json');
 const context = await browser.newContext(options);
 await context.route(url => url.origin !== origin, route => {
  external.push(new URL(route.request().url()).protocol);
  return route.abort('blockedbyclient');
 });
 const page = await context.newPage();
 page.setDefaultTimeout(8000);
 context.on('page', other => other.on('pageerror', error => errors.push(error.message)));
 page.on('pageerror', error => errors.push(error.message));
 page.on('download', download => downloads.push(download.suggestedFilename()));
 context.on('request', request => {
  const url = new URL(request.url());
  requests.push({path: url.pathname, method: request.method(), sameOrigin: url.origin === origin});
 });
 context.on('response', response => {
  const path = new URL(response.url()).pathname;
  if ([VALIDATE, SAVE].includes(path)) responses.push({path, status: response.status()});
 });
 await context.tracing.start({screenshots: true, snapshots: true});
 const edit = (tab = page) => tab.getByRole('textbox', {name: '工作流名称', exact: true});
 const rows = (tab = page) => tab.getByRole('navigation', {name: '工作流列表', exact: true});
 const action = (name, tab = page) => tab.getByRole('button', {name, exact: true});
 const modal = page.getByRole('dialog', {name: '未保存的草稿', exact: true});
 const choice = name => modal.getByRole('button', {name, exact: true});
 const importButton = () => action('导入工作流');
 const exportButton = () => action('导出已保存工作流');
 const notice = () => page.getByText(OPENED, {exact: true});
 const postCount = name => requests.filter(item => item.path === name && item.method === 'POST').length;
 const waitName = (name, tab = page) => until(async () => (await edit(tab).inputValue()) === name);
 const waitSaved = async (tab = page) => {
  await tab.locator('.save-state').getByText('已保存', {exact: true}).waitFor();
  await until(() => action('保存', tab).isEnabled());
 };
 const readSaved = async (name, tab = page) => {
  await rows(tab).getByRole('button', {name, exact: true}).click();
  await waitName(name, tab);
  await waitSaved(tab);
 };
 const visit = async (name, tab = page) => {
  const header = tab.locator('.app-header button');
  if (await header.textContent() === name) return;
  const runs = tab.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/runs');
  await tab.locator('.workspace-link').filter({hasText: new RegExp('^' + name + '$')}).click();
  await until(async () => await header.textContent() === name);
  const loaded = await runs;
  assert.equal(loaded.status(), 200);
  await loaded.finished();
 };
 const choose = async file => {
  const pick = page.waitForEvent('filechooser');
  await importButton().click();
  await (await pick).setFiles(file);
 };
 const file = (name, bytes) => {
  const destination = path.join(out, name);
  fs.writeFileSync(destination, bytes);
  return destination;
 };
 const discardTo = async name => {
  await rows().getByRole('button', {name, exact: true}).click();
  await choice('放弃修改').click();
  await waitName(name);
  await waitSaved();
 };
 const check = async (name, test) => {
  try {
   await test();
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
 const secondTab = async (scope, title, next) => {
  const other = await context.newPage();
  try {
   await other.goto(process.env.V2_URL);
   await until(() => other.locator('.workspace-link').count().then(count => count >= 2));
   await visit(scope, other);
   await readSaved(title, other);
   await edit(other).fill(next);
   await action('保存', other).click();
   await waitSaved(other);
  } finally { await other.close(); }
 };
 try {
  await page.goto(process.env.V2_URL);
  if (stage === 'restart') {
   await check('restart-restores-confirmed-copy-and-never-resurrects-unsaved-import', async () => {
    await waitName(source.name);
    await waitSaved();
    assert.equal(await page.locator('.app-header button').textContent(), 'B');
    assert.equal(await rows().getByRole('button', {name: source.name, exact: true}).count(), 1);
    assert.equal(await rows().getByRole('button', {name: fixture.bOriginal.graph.name, exact: true}).count(), 1);
    assert.equal(postCount(VALIDATE), 0);
    assert.equal(postCount(SAVE), 0);
    assert.equal(downloads.length, 0);
   });
   return;
  }
  await visit('A');
  await readSaved(source.name);

  await check('actual-saved-download-is-exact-canonical-archive', async () => {
   const before = postCount(SAVE);
   const next = page.waitForEvent('download');
   await exportButton().focus();
   await page.keyboard.press('Enter');
   const download = await next;
   assert.equal(download.suggestedFilename(), 'devflower-workflow.json');
   await download.saveAs(copyFile);
   const raw = fs.readFileSync(copyFile);
   const archive = JSON.parse(raw.toString('utf8'));
   assert.deepEqual(Object.keys(archive), ['document', 'document_sha256', 'format', 'version']);
   assert.equal(archive.format, 'devflower.v2.workflow-draft');
   assert.equal(archive.version, 1);
   assert.equal(archive.document_sha256, sha(Buffer.from(encode(archive.document))));
   assert.deepEqual(raw, makeArchive(source));
   assert.equal(archive.document.nodes[0].position.y, 5e-324);
   assert.equal(archive.document.nodes[1].position.y, 1e-7);
   assert.equal(archive.document.nodes[0].position.x, 0);
   assert.equal(postCount(SAVE), before);
   tests.downloadSha = sha(raw);
   tests.downloadSize = raw.length;
  });

  await check('cross-workspace-import-is-read-only-until-one-explicit-save', async () => {
   await visit('B');
   await readSaved(fixture.bOriginal.graph.name);
   const raw = fs.readFileSync(copyFile);
   const beforeDB = sha(fs.readFileSync(path.join(state, 'v2.sqlite')));
   const beforeLocal = await page.evaluate(() => JSON.stringify(localStorage));
   const beforeSave = postCount(SAVE);
   const next = page.waitForRequest(request => new URL(request.url()).pathname === VALIDATE);
   const result = page.waitForResponse(response => new URL(response.url()).pathname === VALIDATE);
   await choose(copyFile);
   const request = await next;
   assert.equal(request.method(), 'POST');
   assert.deepEqual(request.postDataBuffer(), raw);
   assert.equal(new URL(request.url()).searchParams.get('workspace'), fixture.b);
   const validated = await result;
   assert.equal(validated.status(), 200);
   assert.deepEqual(await validated.json(), {workspace: fixture.b, document_sha256: JSON.parse(raw).document_sha256, valid: true});
   await notice().waitFor();
   await waitName(source.name);
   assert.equal(await page.locator('.save-state').textContent(), '未保存');
   assert(await exportButton().isDisabled());
   assert.equal(await rows().getByRole('button', {name: source.name, exact: true}).count(), 0);
   assert.equal(postCount(SAVE), beforeSave);
   assert.equal(sha(fs.readFileSync(path.join(state, 'v2.sqlite'))), beforeDB);
   assert.equal(await page.evaluate(() => JSON.stringify(localStorage)), beforeLocal);
   tests.readOnlyBeforeSave = true;

   const saved = page.waitForRequest(request => new URL(request.url()).pathname === SAVE);
   await action('保存').click();
   const payload = (await saved).postDataJSON();
   assert.equal(payload.workspace, fixture.b);
   assert.equal(payload.expected, null);
   assert.notEqual(payload.id, fixture.source.id);
   assert.notEqual(payload.id, fixture.bOriginal.id);
   assert.deepEqual(makeArchive(payload.document), raw);
   tests.createdId = payload.id;
   await waitSaved();
   assert.equal(postCount(SAVE), beforeSave + 1);
   assert.equal(await rows().getByRole('button', {name: source.name, exact: true}).count(), 1);
  });

  await check('file-chooser-cancel-local-format-errors-and-server-structure-error', async () => {
   const before = postCount(VALIDATE), saves = postCount(SAVE);
   await choose([]);
   assert.equal(postCount(VALIDATE), before);
   const original = fs.readFileSync(copyFile);
   const badHash = JSON.parse(original);
   badHash.document_sha256 = '0'.repeat(64);
   const examples = [
    ['bad-utf8.json', Buffer.from([255]), '工作流文件格式或编码无效。'],
    ['bom.json', Buffer.concat([Buffer.from([239,187,191]), original]), '工作流文件格式或编码无效。'],
    ['oversize.json', Buffer.alloc(420001, 32), '工作流文件格式或编码无效。'],
    ['noncanonical.json', Buffer.concat([Buffer.from(' '), original]), '工作流文件格式或编码无效。'],
    ['bad-digest.json', Buffer.from(encode(badHash) + '\n'), '工作流文件内容校验失败。'],
   ];
   for (const [name, data, error] of examples) {
    await choose(file(name, data));
    await page.getByRole('alert').filter({hasText: error}).waitFor();
    await action('关闭错误').click();
   }
   assert.equal(postCount(VALIDATE), before);
   await choose(file('invalid-graph.json', makeArchive({...blank, extra: 'synthetic'})));
   await page.getByRole('alert').filter({hasText: '工作流草稿结构无效或超过限制。'}).waitFor();
   assert.equal(postCount(VALIDATE), before + 1);
   assert.equal(postCount(SAVE), saves);
   assert.equal(await edit().inputValue(), source.name);
  });

  await check('dirty-confirmation-cancel-and-discard-do-not-save', async () => {
   await readSaved(fixture.bOriginal.graph.name);
   const saves = postCount(SAVE);
   await edit().fill('保留脏编辑');
   assert(await exportButton().isDisabled());
   await page.getByText('请先保存当前工作流，再导出已保存版本。', {exact: true}).waitFor();
   await choose(copyFile);
   await modal.waitFor();
   await choice('取消切换').click();
   assert.equal(await edit().inputValue(), '保留脏编辑');
   await choose(copyFile);
   await choice('放弃修改').click();
   await notice().waitFor();
   assert.equal(await edit().inputValue(), source.name);
   assert.equal(postCount(SAVE), saves);
   await discardTo(fixture.bOriginal.graph.name);
  });

  await check('save-and-continue-saves-previous-draft-and-cas-failure-keeps-editor', async () => {
   await readSaved(fixture.bEdit.graph.name);
   const saves = postCount(SAVE);
   await edit().fill('明确保存前草稿');
   await choose(copyFile);
   const saved = page.waitForRequest(request => new URL(request.url()).pathname === SAVE);
   await choice('保存并继续').click();
   const payload = (await saved).postDataJSON();
   assert.equal(payload.id, fixture.bEdit.id);
   assert.equal(payload.expected, fixture.bEdit.revision);
   await notice().waitFor();
   assert.equal(await edit().inputValue(), source.name);
   assert.equal(postCount(SAVE), saves + 1);
   await discardTo('明确保存前草稿');

   await edit().fill('发生冲突仍保留');
   await choose(copyFile);
   await modal.waitFor();
   await secondTab('B', '明确保存前草稿', '第二标签已保存');
   const conflict = page.waitForResponse(response => new URL(response.url()).pathname === SAVE && response.status() === 409);
   await choice('保存并继续').click();
   await conflict;
   await modal.getByRole('alert').waitFor();
   assert.equal(await edit().inputValue(), '发生冲突仍保留');
   await choice('取消切换').click();
   await discardTo(fixture.bOriginal.graph.name);
  });

  await check('delayed-same-workspace-edit-and-new-file-supersede-old-results', async () => {
   const lateEdit = await holdNext(page, VALIDATE);
   await choose(copyFile);
   await lateEdit.ready();
   await edit().fill('后续编辑');
   await action('撤销').click();
   await waitName(fixture.bOriginal.graph.name);
   await lateEdit.finish();
   assert.equal(await edit().inputValue(), fixture.bOriginal.graph.name);
   assert.equal(await notice().count(), 0);

   const old = await holdNext(page, VALIDATE);
   await choose(copyFile);
   await old.ready();
   await choose(file('exact-blank-workflow.json', makeArchive(blank)));
   await notice().waitFor();
   await waitName(blank.name);
   assert.equal(await page.locator('.save-state').textContent(), '未保存');
   await old.finish();
   assert.equal(await edit().inputValue(), blank.name);
   await discardTo(fixture.bOriginal.graph.name);
  });

  await check('late-validation-and-download-invalidate-on-a-b-a-and-reload', async () => {
   await visit('A');
   await readSaved(source.name);
   const held = await holdNext(page, VALIDATE);
   await choose(copyFile);
   await held.ready();
   await visit('B');
   await visit('A');
   await readSaved(fixture.aSpare.graph.name);
   await held.finish();
   assert.equal(await edit().inputValue(), fixture.aSpare.graph.name);

   const downloadsBefore = downloads.length;
   const oldExport = await holdNext(page, DRAFT);
   await exportButton().click();
   await oldExport.ready();
   await visit('B');
   await readSaved(fixture.bOriginal.graph.name);
   await oldExport.finish();
   assert.equal(downloads.length, downloadsBefore);

   const oldReload = await holdNext(page, VALIDATE);
   await choose(copyFile);
   await oldReload.ready();
   await page.reload();
   await waitName(fixture.bOriginal.graph.name);
   await waitSaved();
   await oldReload.finish();
   assert.equal(await edit().inputValue(), fixture.bOriginal.graph.name);
   assert.equal(await notice().count(), 0);
  });

  await check('stale-other-tab-export-and-600px-native-chooser', async () => {
   await visit('A');
   await readSaved(fixture.aSpare.graph.name);
   await secondTab('A', fixture.aSpare.graph.name, '另一标签已更新');
   const before = downloads.length;
   await exportButton().click();
   await page.getByRole('alert').filter({hasText: '当前已保存版本已变化，请重新打开工作流。'}).waitFor();
   assert.equal(downloads.length, before);
   await visit('B');
   await readSaved(source.name);
   await page.setViewportSize({width: 600, height: 900});
   assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
   const pick = page.waitForEvent('filechooser');
   await importButton().focus();
   await page.keyboard.press('Enter');
   await (await pick).setFiles([]);
   assert.equal(await edit().inputValue(), source.name);
   await choose(path.join(out, 'exact-blank-workflow.json'));
   await waitName(blank.name);
   assert.equal(await page.locator('.save-state').textContent(), '未保存');
   await context.storageState({path: path.join(out, 'initial-storage.json')});
  });
 } finally {
  await context.tracing.stop({path: path.join(out, stage + '-trace.zip')});
  await browser.close();
  fs.writeFileSync(path.join(out, stage + '-results.json'), JSON.stringify({
   cases, requests, responses, errors, external, downloads, tests,
  }, null, 2));
  assert.equal(errors.length, 0);
  assert.equal(external.length, 0);
  assert(!requests.some(item => !item.sameOrigin || /providers\/discover|\/launch|\/cancel|\/check$/.test(item.path)));
 }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
