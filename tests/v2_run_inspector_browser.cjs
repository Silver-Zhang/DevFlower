const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const output = process.argv[2];
const address = new URL(process.env.V2_URL);
const project = path.join(process.env.V2_WORKSPACES, 'A');
const calls = [];
const errors = [];
let browser, context;

async function api(route, data){
 const response=await fetch(new URL('/api/v2/'+route,address),{method:data?'POST':'GET',
  headers:{Authorization:'Bearer '+address.hash.slice(1),...(data?{'Content-Type':'application/json'}:{})},
  ...(data?{body:JSON.stringify(data)}:{})});
 assert.equal(response.status,200,route);
 return response.json();
}

async function test(){
 const workspace=(await api('workspaces/open',{path:project})).id;
 const name='Run Inspector fixture';
 const marker='DF_INSPECTOR_🟢';
 const node=(id,kind,x,config)=>({id,kind,name:id,config_version:1,config,position:{x,y:100}});
 const document={version:1,name,nodes:[
  node('Start','start',120,{initial_data:JSON.stringify({marker,payload:'🟢'.repeat(8300)})}),
  node('Finish','output',450,{format:'text'})],edges:[
  {id:'handoff',source:'Start',target:'Finish',source_port:'out',target_port:'in'}]};
 const draft=await api('drafts/save',{workspace,id:null,expected:null,document});
 const run=(await api('launch',{workspace,draft_id:draft.id,revision:draft.revision,task:'',
  intent_key:'v2-inspector-browser-one'})).run.id;
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 context=await browser.newContext({viewport:{width:1280,height:820},acceptDownloads:true});
 await context.tracing.start({screenshots:true,snapshots:true});
 const page=await context.newPage();
 page.setDefaultTimeout(15000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>calls.push([request.method(),new URL(request.url()).pathname]));
 const button=name=>page.getByRole('button',{name,exact:true});
 await page.goto(address.href);
 await page.locator('.app-header button').click();
 await page.getByLabel('文件夹路径',{exact:true}).fill(project);
 await button('前往').click();
 await button('打开此文件夹').click();
 await page.getByLabel('运行历史',{exact:true}).getByRole('button',{name:new RegExp(name)}).click();
 await page.getByLabel('运行状态',{exact:true}).getByText('已完成',{exact:true}).waitFor();
 await button('运行检查器').click();
 const inspector=page.getByLabel('运行检查器',{exact:true});
 await inspector.getByRole('button',{name:'事件',exact:true}).click();
 await inspector.getByText('授权本次运行',{exact:true}).waitFor();
 await inspector.getByText('下游已接收',{exact:true}).waitFor();
 await page.screenshot({path:path.join(output,'run-timeline-desktop.png')});
 await inspector.getByLabel('激活与轮次',{exact:true}).selectOption({label:'Finish · 第 1 轮'});
 await inspector.getByRole('button',{name:'事件',exact:true}).click();
 await inspector.getByText('下游已接收',{exact:true}).waitFor();
 assert.equal(await inspector.getByText('授权本次运行',{exact:true}).count(),0);
 const download=page.waitForEvent('download');
 await inspector.getByRole('button',{name:'导出运行详情',exact:true}).click();
 const archive=await download;
 assert.equal(archive.suggestedFilename(),'devflower-run-detail.json');
 const detail=JSON.parse(fs.readFileSync(await archive.path(),'utf8'));
 assert.equal(detail.run.id,run);
 assert.equal(detail.definition.id,detail.run.definition_id);
 assert.equal(detail.run.actual_calls,0);
 assert.equal(detail.coverage.complete_public_snapshot,false);
 assert(detail.coverage.omitted_activation_payloads>0);
 await button('关闭检查器').click();
 await page.locator('.react-flow__node').filter({hasText:'Finish'}).click();
 const nodeInspector=page.getByLabel('节点配置',{exact:true});
 await nodeInspector.getByRole('button',{name:'分段读取节点输出',exact:true}).click();
 const payload=nodeInspector.locator('.paged-evidence').getByLabel('节点输出',{exact:true});
 await payload.waitFor();
 assert((await payload.textContent()).includes(marker));
 await nodeInspector.getByRole('button',{name:'下一段',exact:true}).click();
 await nodeInspector.getByText(/显示 8001/).waitFor();
 await nodeInspector.getByRole('button',{name:'上一段',exact:true}).click();
 await nodeInspector.getByText(/显示 1–8000/).waitFor();
 await page.setViewportSize({width:560,height:760});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:path.join(output,'run-paged-output-narrow.png')});
 await button('关闭检查器').click();
 await page.reload();
 await page.getByLabel('运行状态',{exact:true}).getByText('已完成',{exact:true}).waitFor();
 assert.equal(calls.some(([method,route])=>method==='POST'&&['launch','cancel','resume'].some(key=>route.endsWith('/'+key))),false);
 assert.deepEqual(errors,[]);
 console.log('PASS chronological event filter, bounded download, Unicode paging, narrow viewport and read-only reopen');
}

test().catch(error=>{
 fs.writeFileSync(path.join(output,'fatal.txt'),error.stack);
 console.error(error.message);
 process.exitCode=1;
}).finally(async()=>{
 if(context)await context.tracing.stop({path:path.join(output,'trace.zip')}).catch(()=>{});
 if(browser)await browser.close();
});
