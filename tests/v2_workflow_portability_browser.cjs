const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const out = process.argv[2];
const address = new URL(process.env.V2_URL);
const root = path.join(process.env.V2_WORKSPACES, 'A');
const other = path.join(process.env.V2_WORKSPACES, 'B');
const outside = path.join(process.env.V2_WORKSPACES, 'private.txt');
const target = {value:'verified-tool-fixture', denied:true};
const pageErrors = [];
let browser, context;

async function api(route, data, status=200) {
 const response = await fetch(new URL('/api/v2/' + route,address), {
  method:data?'POST':'GET',
  headers:{Authorization:'Bearer '+address.hash.slice(1),...(data?{'Content-Type':'application/json'}:{})},
  ...(data?{body:JSON.stringify(data)}:{})
 });
 const value=await response.json();
 assert.equal(response.status,status,route+' '+JSON.stringify(value));
 return value;
}
const q=(route,data)=>route+'?'+new URLSearchParams(data);
const node=(id,kind,x,y,config)=>({id,name:id,kind,position:{x,y},config_version:1,config});
const edge=(id,source,target,source_port='out')=>({id,source,target,source_port,target_port:'in'});
const graph = {version:1,name:'Portable Tool Workflow',nodes:[
 node('Start','start',40,80,{}),
 node('Check','tool',260,80,{tool:'check-json',permission:'workspace-read',path:'check.py',expected_json:JSON.stringify(target)}),
 node('Validate','validation',490,80,{rules:'{"field":"result.passed","op":"equals","value":true}'}),
 node('Passed','output',740,30,{format:'json'}),node('Failed','output',740,230,{format:'json'}),
],edges:[
 edge('StartCheck','Start','Check'),edge('CheckValidate','Check','Validate'),
 edge('PassPath','Validate','Passed','pass'),edge('FailPath','Validate','Failed','fail'),
]};

async function terminal(workspace,run){
 const until=Date.now()+18000;
 while(Date.now()<until){
  const result=await api(q('run',{workspace,id:run}));
  if(!result.worker_alive&&['FAILED','COMPLETED','RECOVERY_REQUIRED'].includes(result.state))return result;
  await new Promise(resolve=>setTimeout(resolve,150));
 }
 throw Error('Expected terminal workflow did not appear');
}

async function main(){
 fs.writeFileSync(outside,'outside sentinel');
 fs.writeFileSync(path.join(root,'input.txt'),'verified-tool-fixture');
 fs.writeFileSync(path.join(root,'check.py'),`import json
from pathlib import Path
allowed = Path("input.txt").read_text()
denied = not Path(${JSON.stringify(outside)}).exists() and not Path(".codex/auth.json").exists()
try:
    Path("unexpected-write").write_text("invalid")
    denied = False
except OSError:
    pass
print(json.dumps({"value": allowed, "denied": denied}))
`);
 for(const args of [['init','-q'],['-c','user.email=fixture@example.invalid','-c','user.name=Fixture',
                                'commit','--allow-empty','-qm','fixture']]){
  execFileSync('/usr/bin/git',['-C',root,...args]);
 }
 const workspace=(await api('workspaces/open',{path:root})).id;
 const original=await api('drafts/save',{workspace,id:null,expected:null,document:graph});
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 context=await browser.newContext({viewport:{width:1200,height:840},acceptDownloads:true});
 const page=await context.newPage();
 page.setDefaultTimeout(12000);
 page.on('pageerror',error=>pageErrors.push(error.message));
 await context.tracing.start({screenshots:true,snapshots:true});
 await page.goto(address.href);
 const button=name=>page.getByRole('button',{name,exact:true});
 async function openWorkspace(target){
  await page.locator('.app-header button').click();
  await page.getByLabel('文件夹路径',{exact:true}).fill(target);
  await button('前往').click();
  await button('打开此文件夹').click();
 }
 await openWorkspace(root);
 await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:graph.name}).click();
 await button('工作流模板').click();
 await button('收藏当前工作流为模板').click();
 await page.getByLabel('模板名称',{exact:true}).fill('Reusable fixture');
 await page.getByLabel('模板说明',{exact:true}).fill('Fixture description to search');
 await button('保存模板').click();
 const panel=page.getByLabel('工作流模板',{exact:true});
 await panel.getByText('Reusable fixture',{exact:true}).waitFor();
 await page.getByLabel('搜索模板',{exact:true}).fill('no match');
 await panel.getByText('没有符合搜索的模板。',{exact:true}).waitFor();
 await page.getByLabel('搜索模板',{exact:true}).fill('description');
 await button('删除模板 Reusable fixture').click();
 await button('回收站').click();
 await button('恢复模板 Reusable fixture').click();
 await button('收藏').click();
 await panel.getByText('Reusable fixture',{exact:true}).waitFor();
 await page.screenshot({path:path.join(out,'templates.png')});
 await panel.getByRole('button',{name:/^Reusable fixture Fixture description/}).click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="未保存的草稿"]')||
  document.querySelector('[aria-label="工作流名称"]')?.value==='Reusable fixture');
 if(await page.getByRole('dialog',{name:'未保存的草稿'}).isVisible())await button('放弃修改').click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="工作流名称"]')?.value==='Reusable fixture');
 await page.getByLabel('工作流名称',{exact:true}).fill('Independent copy');
 await button('保存').click();
 await page.getByLabel('工作流列表',{exact:true}).getByRole('button',{name:'Independent copy'}).waitFor();
 assert.equal((await api(q('draft',{workspace,id:original.id}))).graph.name,graph.name);
 const templates=await api(q('workflow-templates',{workspace}));
 assert.equal(templates.templates[0].name,'Reusable fixture');
 assert.deepEqual((await api(q('workflow-template',{workspace,id:templates.templates[0].id,
  revision:templates.templates[0].revision}))).graph.nodes.map(item=>item.position),graph.nodes.map(item=>item.position));

 const launched=page.waitForResponse(reply=>reply.url().endsWith('/api/v2/launch')&&reply.request().method()==='POST');
 await button('启动').click();
 const launch=await (await launched).json();
 assert(launch.run,JSON.stringify(launch));
 const snapshot=await terminal(workspace,launch.run.id);
 assert.equal(snapshot.state,'COMPLETED',JSON.stringify(snapshot.nodes));
 assert.equal(snapshot.run.actual_calls,0);
 const tool=snapshot.nodes.find(item=>item.node==='Check');
 assert.equal(tool.output.result.passed,true);
 assert.equal(snapshot.nodes.find(item=>item.node==='Validate').selected_port,'pass');
 assert.equal(snapshot.nodes.find(item=>item.node==='Failed').state,'SKIPPED');
 await page.locator('.react-flow__node[data-id="Check"]').click();
 await button('查看工具 JSON 产物').click();
 await page.getByLabel('工具实际 JSON 结果',{exact:true}).filter({hasText:'verified-tool-fixture'}).waitFor();
 await page.screenshot({path:path.join(out,'tool-artifact.png')});
 const artifact=await api(q('tool-artifact',{workspace,run:launch.run.id,activation:tool.id}));
 assert.deepEqual(artifact.value,target);
 assert.equal(artifact.sha256,tool.output.result.tool_artifact.sha256);
 assert(!fs.existsSync(path.join(root,'unexpected-write')));
 assert(!fs.existsSync(path.join(root,'.devflower')));

 const download=page.waitForEvent('download');
 await button('导出运行归档').click();
 const history=await download;
 const archive=path.join(out,history.suggestedFilename());
 await history.saveAs(archive);
 await openWorkspace(other);
 const b=(await api('workspaces/open',{path:other})).id;
 await button('历史归档').click();
 await page.getByLabel('选择运行历史归档',{exact:true}).setInputFiles(archive);
 await page.getByLabel('只读归档内容',{exact:true}).getByText(launch.run.id,{exact:true}).waitFor();
 await page.getByLabel('归档激活',{exact:true}).selectOption(tool.id);
 await page.getByText('节点记录与回执',{exact:true}).click();
 await page.screenshot({path:path.join(out,'read-only-archive.png')});
 await page.reload();
 await button('历史归档').click();
 const stored=(await api(q('history-archives',{workspace:b}))).archives[0];
 await page.getByLabel('已导入的归档',{exact:true}).selectOption(stored.id);
 await page.getByLabel('只读归档内容',{exact:true}).getByText(launch.run.id,{exact:true}).waitFor();
 await page.setViewportSize({width:560,height:850});
 await page.getByLabel('只读归档内容',{exact:true}).waitFor();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
 await page.screenshot({path:path.join(out,'read-only-archive-narrow.png')});
 assert.deepEqual(await api(q('runs',{workspace:b})),[]);
 assert.equal(stored.source_workspace,workspace);
 assert.equal(stored.authority,false);
 await api(q('history-archive',{workspace,id:stored.id}),null,409);
 await api(q('tool-artifact',{workspace:b,run:launch.run.id,activation:tool.id}),null,409);
 assert(!fs.existsSync(path.join(other,'.devflower')));

 const negative=structuredClone(graph);
 negative.nodes[1].config.expected_json='{"unmatched":true}';
 const bad=await api('drafts/save',{workspace,id:null,expected:null,document:negative});
 const next=await api('launch',{workspace,draft_id:bad.id,revision:bad.revision,intent_key:'bad-json-fixture-request',task:''});
 const failed=await terminal(workspace,next.run.id);
 assert.equal(failed.state,'COMPLETED');
 assert.equal(failed.run.actual_calls,0);
 assert.equal(failed.nodes.find(item=>item.node==='Check').output.result.passed,false);
 assert.equal(failed.nodes.find(item=>item.node==='Validate').selected_port,'fail');
 assert.equal(failed.nodes.find(item=>item.node==='Passed').state,'SKIPPED');
 assert.deepEqual(pageErrors,[]);
 console.log('PASS templates, actual isolated Tool PASS/FAIL, checked artifact, cross-workspace history import/reopen, zero model calls');
}
main().catch(error=>{console.error(error);process.exitCode=1})
 .finally(async()=>{if(context)await context.tracing.stop({path:path.join(out,'portability-trace.zip')});if(browser)await browser.close()});
