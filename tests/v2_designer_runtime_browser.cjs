const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const output = process.argv[2];
const address = new URL(process.env.V2_URL);
const workspacePath = path.join(process.env.V2_WORKSPACES, 'A');
const requestCount = new Map();
const pageErrors = [];
const checks = [];
const approvalPosts = [];
let browser, context, workspace, activeRun;

async function api(route, data) {
  const response = await fetch(new URL('/api/v2/' + route, address), {
    method: data ? 'POST' : 'GET',
    headers: {
      Authorization: 'Bearer ' + address.hash.slice(1),
      ...(data ? {'Content-Type': 'application/json'} : {}),
    },
    ...(data ? {body: JSON.stringify(data)} : {}),
  });
  return {status: response.status, value: await response.json()};
}

async function accepted(route, data) {
  const {status, value} = await api(route, data);
  assert.equal(status, 200, route + ' ' + JSON.stringify(value));
  return value;
}

async function until(operation) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const value = await operation();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Expected persistent state did not appear');
}

function node(id, kind, x, config) {
  return {id, kind, name: id, config_version: 1, config, position: {x, y: 80}};
}
function edge(id, source, target, source_port = 'out') {
  return {id, source, target, source_port, target_port: 'in'};
}
const graph = {
  version: 1, name: 'Approval checkpoint browser fixture',
  nodes: [
    node('Start', 'start', 30, {initial_data: '{"marker":"DF_TYPED_BROWSER_APPROVAL"}'}),
    node('Review', 'approval', 270, {instructions: 'Review the disposable fixture data.', policy: 'single-reviewer', timeout: 55}),
    node('Finish', 'output', 510, {format: 'json'}),
  ],
  edges: [edge('review', 'Start', 'Review'), edge('approved', 'Review', 'Finish', 'approved')],
};
const details = run => accepted('run?workspace=' + workspace + '&id=' + run);
const pending = snapshot => snapshot.nodes.find(row => row.node === 'Review' && row.state === 'WAITING_CONFIRMATION');

function stopOwnedWorker(owner) {
  const raw = fs.readFileSync('/proc/' + owner.pid + '/stat', 'utf8');
  const fields = raw.slice(raw.lastIndexOf(')') + 2).trim().split(/\s+/);
  assert.equal(owner.host, os.hostname());
  assert.equal(owner.boot_id, fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim());
  assert.equal(owner.start, fields[19]);
  assert.equal(owner.pgid, Number(fields[2]));
  assert.equal(owner.sid, Number(fields[3]));
  process.kill(owner.pid, 'SIGKILL');
}

async function run() {
  workspace = (await accepted('workspaces/open', {path: workspacePath})).id;
  await accepted('drafts/save', {workspace, id: null, expected: null, document: graph});
  browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM, args: ['--no-sandbox']});
  context = await browser.newContext({viewport: {width: 1280, height: 850}});
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    const route = new URL(request.url()).pathname;
    requestCount.set(route, (requestCount.get(route) || 0) + 1);
    if (route === '/api/v2/approval') approvalPosts.push(request.postDataJSON());
  });
  await context.tracing.start({screenshots: true, snapshots: true});
  await page.goto(address.href);
  const action = name => page.getByRole('button', {name, exact: true});
  await page.locator('.app-header button').click();
  await page.getByLabel('文件夹路径', {exact: true}).fill(workspacePath);
  await action('前往').click();
  await action('打开此文件夹').click();
  await page.getByLabel('工作流列表', {exact: true}).getByRole('button', {name: graph.name}).click();

  async function launch() {
    const response = page.waitForResponse(reply => reply.url().endsWith('/api/v2/launch') && reply.request().method() === 'POST');
    await action('启动').click();
    const reply = await response;
    const value = await reply.json();
    assert.equal(reply.status(), 200, JSON.stringify(value));
    activeRun = value.run.id;
    return until(async () => {
      const snapshot = await details(activeRun);
      return pending(snapshot) && snapshot;
    });
  }
  async function terminal(state) {
    return until(async () => {
      const value = await details(activeRun);
      return !value.worker_alive && value.state === state && value;
    });
  }
  function assertNoEffect(value) {
    assert.equal(value.run.actual_calls, 0);
    assert(!value.nodes.some(row => row.input_sent_sha256));
  }

  const first = await launch();
  const review = pending(first);
  assertNoEffect(first);
  assert.notEqual(first.nodes.find(row => row.node === 'Finish').state, 'COMPLETED');
  await action('批准').waitFor();
  await page.screenshot({path: path.join(output, 'approval-waiting.png')});
  await page.reload();
  await action('批准').waitFor();
  assert.equal(pending(await details(activeRun)).id, review.id);
  assert.equal(requestCount.get('/api/v2/launch'), 1);
  assert.equal(requestCount.get('/api/v2/approval'), undefined);

  stopOwnedWorker(first.run.worker);
  await action('恢复运行').click();
  const restarted = await until(async () => {
    const value = await details(activeRun);
    return value.worker_alive && value.run.worker.pid !== first.run.worker.pid && pending(value) && value;
  });
  assert.equal(pending(restarted).id, review.id);
  assert.equal(restarted.definition.id, first.definition.id);
  await action('批准').click();
  const approved = await terminal('COMPLETED');
  assert.equal(approved.run.output.result.marker, 'DF_TYPED_BROWSER_APPROVAL');
  assert.equal(approvalPosts.length, 1);
  assert.equal(approvalPosts[0].activation, review.id);
  assert.equal(approvalPosts[0].decision, 'approve');
  const evidence = approved.events.filter(event => event.kind === 'human-decision');
  assert.equal(evidence.length, 1);
  assert.match(evidence[0].actor, /^local-user:[0-9]+$/);
  assert.equal(evidence[0].activation, review.id);
  assert.equal(evidence[0].definition_id, first.definition.id);
  assert.equal(evidence[0].workspace, workspace);
  await accepted('approval', approvalPosts[0]);
  assert.equal((await api('approval', {...approvalPosts[0], decision: 'reject'})).status, 409);
  assert.equal((await details(activeRun)).events.filter(event => event.kind === 'human-decision').length, 1);
  assertNoEffect(approved);
  await page.getByLabel('运行状态', {exact: true}).getByText('已完成', {exact: true}).waitFor();
  await page.screenshot({path: path.join(output, 'approval-resumed-completed.png')});
  checks.push('real browser reload, dead owner replacement, exact waiting activation, explicit approval');

  await action('编辑草稿').click();
  const second = await launch();
  assert.notEqual(second.run.id, approved.run.id);
  await action('拒绝').click();
  const rejected = await terminal('FAILED');
  assert.equal(approvalPosts.length, 2);
  assert.equal(approvalPosts[1].activation, pending(second).id);
  assert.equal(approvalPosts[1].decision, 'reject');
  assert(!rejected.nodes.some(row => row.node === 'Finish' && row.state === 'COMPLETED'));
  assertNoEffect(rejected);
  assert.equal(requestCount.get('/api/v2/launch'), 2);
  assert.equal(requestCount.get('/api/v2/resume'), 1);
  assert.equal(requestCount.get('/api/v2/approval'), 2);
  assert.deepEqual(pageErrors, []);
  await page.getByLabel('运行状态', {exact: true}).getByText('失败', {exact: true}).waitFor();
  await page.screenshot({path: path.join(output, 'approval-rejected.png')});
  checks.push('real browser rejection stops downstream, no Provider request');
}

run().catch(error => {
  fs.writeFileSync(path.join(output, 'fatal.txt'), error.stack);
  console.error(error.message);
  process.exitCode = 1;
}).finally(async () => {
  if (activeRun && process.exitCode) await api('cancel', {workspace, run_id: activeRun}).catch(() => {});
  if (context) await context.tracing.stop({path: path.join(output, 'trace.zip')}).catch(() => {});
  if (browser) await browser.close();
  fs.writeFileSync(path.join(output, 'results.json'),
    JSON.stringify({category: 'real V2 HTTP and Chromium with deterministic graph; no model', checks, pageErrors}, null, 2));
  for (const check of checks) console.log('PASS ' + check);
});
