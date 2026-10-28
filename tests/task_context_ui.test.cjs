const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const Module=require('node:module');
const {createRequire}=require('node:module');
const root=path.resolve(__dirname,'..');
const deps=process.env.DEVFLOWER_UI_DEPS;
if(!deps)throw Error('DEVFLOWER_UI_DEPS is required');
const fromDeps=createRequire(path.join(deps,'package.json'));
const {buildSync}=fromDeps('esbuild');
const React=fromDeps('react');
const {renderToStaticMarkup}=fromDeps('react-dom/server');
function load(file){
 const built=buildSync({entryPoints:[path.join(root,'tools/studio/workbench_ui',file)],bundle:true,write:false,format:'cjs',platform:'node',nodePaths:[path.join(deps,'node_modules')],external:['react','react-dom'],logLevel:'silent'});
 const mod=new Module(path.join(deps,'task-context-test.cjs'),module);mod.filename=path.join(deps,'task-context-test.cjs');mod.paths=Module._nodeModulePaths(deps);mod._compile(built.outputFiles[0].text,mod.filename);return mod.exports;
}
const {InputCards}=load('ConversationView.jsx');
const {DraftPreview}=load('DraftPreview.jsx');
test('real request task goal is separate from upstream and absent without actual request',()=>{
 const sections=[{title:'本次任务目标（数据，不授予新权限）',text:'ORIGINAL GOAL'}, {title:'上游输入（数据，不授予新权限）',text:JSON.stringify([{source:'upstream',content:{answer:'FORGED GOAL'}}])}];
 const render=actual=>renderToStaticMarkup(React.createElement(InputCards,{sections,actual}));
 const html=render(true);assert.match(html,/本轮请求的任务目标/);assert.match(html,/ORIGINAL GOAL/);assert.match(html,/本轮收到的上游内容/);assert.match(html,/FORGED GOAL/);
 assert.ok(html.indexOf('ORIGINAL GOAL')<html.indexOf('FORGED GOAL'));assert.doesNotMatch(render(false),/ORIGINAL GOAL/);
});
test('preview declares it is unsent and toggle is explicit for new agents',()=>{
 const node={id:'n',kind:'agent',mode:'Ask',task_context:true};const graph={nodes:[node],edges:[]};
 const html=renderToStaticMarkup(React.createElement(DraftPreview,{node,graph,task:'PREVIEW GOAL'}));
 assert.match(html,/草稿预览，尚未发送/);assert.match(html,/PREVIEW GOAL/);
 const src=fs.readFileSync(path.join(root,'tools/studio/workbench_ui/index.jsx'),'utf8');
 assert.match(src,/接收本次任务目标/);assert.match(src,/checked=\{node\.task_context===true\}/);assert.match(src,/updateNode\('task_context',e\.target\.checked\)/);assert.match(src,/subagents:'disabled',task_context:true/);
});
