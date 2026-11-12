const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const out=process.argv[2];
const requests=[];
const errors=[];
let browser,context;

async function run(){
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM,args:['--no-sandbox']});
 context=await browser.newContext({viewport:{width:1280,height:840}});
 await context.tracing.start({screenshots:true,snapshots:true});
 const page=await context.newPage();
 page.setDefaultTimeout(15000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>requests.push([request.method(),new URL(request.url()).pathname]));
 const button=name=>page.getByRole('button',{name,exact:true});
 const platform=page.getByLabel('Git 平台',{exact:true});
 async function open(name){
  await page.locator('.app-header button').click();
  await page.getByLabel('文件夹路径',{exact:true}).fill(path.join(process.env.V2_WORKSPACES,name));
  await button('前往').click();
  await button('打开此文件夹').click();
  await button('Git 平台').click();
 }
 await page.goto(process.env.V2_URL);
 await open('A');
 await platform.getByRole('heading',{name:'GitHub',exact:true}).waitFor();
 await platform.getByText('example/one',{exact:true}).waitFor();
 const frame=await page.getByRole('main',{name:'Git 平台',exact:true}).boundingBox();
 const tabs=await platform.getByLabel('Git 平台视图',{exact:true}).boundingBox();
 const detail=await platform.locator('.git-platform-scroll').boundingBox();
 assert(frame.width>1050 && tabs.x+tabs.width<=detail.x+1);
 assert.equal(await button('Git 平台').getAttribute('aria-current'),'page');
 assert.equal(await page.getByRole('main',{name:'工作流设计器'}).count(),0);

 await button('分支').click();
 await platform.getByRole('button',{name:/work\/fixture/}).click();
 await platform.getByRole('heading',{name:'work/fixture',exact:true}).waitFor();
 await button('Issue').click();
 await platform.getByRole('button',{name:/Fixture Issue/}).click();
 await platform.getByText('查看远端说明',{exact:true}).click();
 await platform.getByText('Bound Issue evidence',{exact:true}).waitFor();
 await button('PR').click();
 await platform.getByRole('button',{name:/Fixture PR/}).click();
 await button('查看 CI').click();
 await platform.getByRole('button',{name:/Fixture CI/}).click();
 await platform.getByRole('button',{name:/Run tests/}).click();
 const trace=platform.getByLabel('CI 作业日志',{exact:true});
 await trace.getByText(/FAILED_PUBLIC_TEST/).waitFor();
 assert(!(await trace.innerText()).includes('PRIVATE_SYNTHETIC_ENV'));
 assert((await trace.innerText()).includes('a'.repeat(40)));
 await page.screenshot({path:path.join(out,'github-failed-ci.png')});
 await button('创建本机审核任务').click();
 const confirm=platform.getByLabel('确认本机审核',{exact:true});
 await confirm.getByText(/example\/one/).waitFor();
 assert.equal(requests.filter(([method,route])=>method==='POST'&&route.endsWith('/review')).length,0);
 await page.route('**/api/v2/git-platform/review',async route=>{
  const response=await route.fetch();
  assert.equal(response.status(),200);
  await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture lost local review reply'})});
 },{times:1});
 await button('确认创建本机审核任务').click();
 await platform.getByText(/Fixture lost local review reply/).waitFor();
 await button('核对审核任务').click();
 await platform.getByText(/已核对同一提交的本机审核任务/).waitFor();
 await button('审核与交付').click();
 await platform.getByText('等待独立审核',{exact:true}).waitFor();
 await platform.getByText(/远端发布需要已核验/).first().waitFor();
 await button('关闭 Git 平台').click();
 await open('B');
 await platform.getByRole('heading',{name:'GitLab',exact:true}).waitFor();
 await platform.getByText('group/sub/two',{exact:true}).waitFor();
 await button('MR').click();
 await platform.getByRole('button',{name:/Fixture MR/}).click();
 await button('查看 Pipeline').click();
 await platform.getByRole('button',{name:/Fixture CI/}).click();
 await platform.getByRole('button',{name:/Run tests/}).click();
 await platform.getByLabel('CI 作业日志',{exact:true}).getByText(/FAILED_PUBLIC_TEST/).waitFor();
 await page.setViewportSize({width:560,height:760});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.screenshot({path:path.join(out,'gitlab-pipeline-narrow.png')});
 assert.equal(requests.filter(([method,route])=>method==='POST'&&route.endsWith('/review')).length,1);
 assert(!requests.some(([,route])=>/\/git-platform\/(publish|merge|approve)$/.test(route)));
 assert.deepEqual(errors,[]);
 console.log('PASS GitHub and GitLab workspace binding, issue and change reads, exact failing logs, local review confirmation and lost reply readback');
}

run().catch(error=>{fs.writeFileSync(path.join(out,'fatal.txt'),error.stack);console.error(error.message);process.exitCode=1})
.finally(async()=>{
 if(context)await context.tracing.stop({path:path.join(out,'trace.zip')}).catch(()=>{});
 if(browser)await browser.close();
});
