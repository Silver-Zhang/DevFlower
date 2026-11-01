'use strict';
const $ = id => document.getElementById(id);
const text = (tag, value, cls) => { const e = document.createElement(tag); e.textContent = value; if(cls)e.className=cls; return e; };
const state = {workspace:null, document:null, identity:crypto.randomUUID(), version:0, saveRequest:0, saved:null, selected:null,
  mode:'design', snapshot:null, runSelected:null, profiles:[], verificationProfiles:[], owned:[], drafts:[], run:null, runEpoch:0, workspaceEpoch:0, preview:null, history:[], historyIndex:0, loadEpoch:0, statusEpoch:0, runRequest:0, pollPending:null};
const fragment = new URLSearchParams(location.hash.slice(1));
if(fragment.has('token')) { sessionStorage.setItem('devflower-stable-token',fragment.get('token')); history.replaceState(null,'',location.pathname); }
const token = sessionStorage.getItem('devflower-stable-token') || '';
$('studio').inert=true;
function notice(message) { $('notice').textContent=message; }
function confirmedPR(receipt) {
  const pr=receipt?.pr, url=typeof pr?.url==='string'?pr.url:'';
  const match=/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/([1-9][0-9]*)$/.exec(url);
  return receipt?.pushed===true&&/^[0-9a-f]{40}$/.test(receipt.commit||'')&&pr?.head===receipt.commit
    &&Number.isSafeInteger(pr.number)&&match&&String(pr.number)===match[1]?pr:null;
}
let panelOrigin=null;
function closePanels() { window.sessionDesk?.invalidate();for(const e of document.querySelectorAll('.floating-panel'))e.hidden=true; $('node-menu').hidden=true; }
function openPanel(id) {
  closePanels();panelOrigin=document.activeElement;const panel=$(id);panel.hidden=false;
  const node=canvas.svg.querySelector(`[data-node="${state.mode==='run'?state.runSelected:state.selected}"]`);
  const right=node?.getBoundingClientRect().x>innerWidth/2;
  panel.style.left=right?'14px':'';panel.style.right=right?'auto':'14px';
  if(panelOrigin?.matches('[data-node]'))[...panel.querySelectorAll('input,textarea,select,button')].find(e=>!e.matches('.close-panel')&&e.getClientRects().length&&!e.disabled)?.focus({preventScroll:true});
}
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>{
  $(button.dataset.close).hidden=true;(panelOrigin?.isConnected?panelOrigin:$('canvas')).focus();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const inside=e.target.closest('.floating-panel');closePanels();state.addContext=null;$('palette').hidden=true;$('toggle-palette').setAttribute('aria-expanded','false');if(inside)(panelOrigin?.isConnected?panelOrigin:$('canvas')).focus();}});

async function api(path,data) {
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});
  const value=await response.json(); if(!response.ok)throw new Error(value.error||'请求失败'); return value;
}
function act(id, fn) { $(id).addEventListener('click',async()=>{try{await fn();}catch(e){notice(e.message);}}); }
function invalidatePreview() {
  state.preview=null;$('preview').replaceChildren();
  if(!$('task-confirm-step').hidden)taskStep(2);
  document.querySelector('[data-task-step="3"]').disabled=true;
}
function changed(record=true) {
  state.version++;invalidatePreview();$('save-state').textContent='当前改动未保存';
  $('graph-status').textContent='编辑已改变，需重新校验';
  if(record&&JSON.stringify(state.history[state.historyIndex])!==JSON.stringify(state.document)) {
    state.history=state.history.slice(0,state.historyIndex+1);state.history.push(structuredClone(state.document));
    if(state.history.length>100)state.history.shift();state.historyIndex=state.history.length-1;
  }
  $('undo').disabled=state.historyIndex<=0;$('redo').disabled=state.historyIndex>=state.history.length-1;
}
function options(select, values, selected) {
  select.replaceChildren(...values.map(([value,label])=>{ const e=text('option',label);e.value=value;return e;}));
  if(selected!==undefined)select.value=selected;
}
function loadDocument(document,saved=null) {
  state.addContext=null;closePanels();$('palette').hidden=true;$('toggle-palette').setAttribute('aria-expanded','false');
  // Migration applies to an editable copy only, never to a stored run snapshot.
  const migrated=document.version===1;
  document=structuredClone(document);if(migrated)document.version=2;
  const task=saved?.task||{};
  for(const [id,key] of [['task-title','title'],['task-body','body'],['task-scope','scope'],['task-acceptance','acceptance'],['issue-url','issue_url']])$(id).value=key==='scope'?(task.scope||[]).join('\n'):task[key]||'';
  state.document=document;state.identity=crypto.randomUUID();state.version=0;state.saved=saved;state.selected=null;
  state.history=[structuredClone(document)];state.historyIndex=0;state.loadEpoch++;invalidatePreview();$('graph-status').textContent='尚未校验当前编辑版本';$('workflow-name').value=document.name;$('max-rounds').value=document.max_rounds;
  $('save-state').textContent=migrated?'旧草稿已转为节点式，保存将创建新版本':saved?'已加载保存版本':'新草稿未保存';draw();canvas.fit();
  if(migrated)notice('已将旧模板转换为可编辑的节点式草稿；历史运行和授权不变，启动前须重新确认。');
}
async function newDraft(kind) {
  const requested=typeof kind==='string'?kind:$('template').value, identity=state.identity, version=state.version, request=++state.loadEpoch;
  const document=await api('/api/template?kind='+encodeURIComponent(requested));
  if(identity!==state.identity||version!==state.version||request!==state.loadEpoch)return; loadDocument(document);$('template-library').open=false;
}
async function status() {
  const epoch=++state.statusEpoch,workspace=state.workspace?.id,value=await api('/api/status'+(workspace?'?workspace='+encodeURIComponent(workspace):''));if(epoch!==state.statusEpoch||workspace!==state.workspace?.id)return;state.profiles=value.profiles;state.verificationProfiles=value.verification_profiles||[];state.drafts=value.drafts;
  state.sessionOnly=value.execution_context==='development-sessions';
  $('trust').textContent=state.sessionOnly?'Development 会话候选 · 无 Controller/Stable 权限':'Stable '+value.stable.slice(0,16);
  $('canvas-run').disabled=state.sessionOnly;$('prepare').disabled=state.sessionOnly;
  options($('recent'),[['','最近工作区'],...value.workspaces.map(w=>[w.id,w.path])],state.workspace?.id);
  options($('drafts'),[['','已保存流程'],...value.drafts.map(d=>[d.id,d.document.name])],state.saved?.id);
  options($('runs'),[['','选择运行'],...value.runs.filter(r=>!state.workspace||r.project===state.workspace.id).map(r=>[r.id,r.id.slice(0,12)+' · '+r.state])],state.run);
  state.workspaces=value.workspaces;
}
function setWorkspace(workspace) {
  const previous=state.workspace?.id;state.workspace=workspace;if(previous!==workspace.id&&state.document)loadDocument(state.document);state.snapshot=null;state.runSelected=null;state.workspaceEpoch++;state.owned=[];invalidatePreview();state.run=null;state.runEpoch++;
  $('workspace-path').value=workspace.path;$('workspace-info').textContent=`${workspace.path} · ${workspace.environment||'local'} · ${workspace.execution_allowed===false?'只读 · '+workspace.execution_reason:workspace.branch+' · '+workspace.head}`;
  $('sessions').replaceChildren();$('preview').replaceChildren();$('monitor').replaceChildren();if(state.mode==='run')canvas.render({nodes:[],edges:[]});$('run-title').textContent='未选择运行';sessionStorage.setItem('devflower-workspace',workspace.id);status().catch(e=>notice(e.message));sessions().catch(e=>notice(e.message));
}
async function saveDraft() {
  if(!state.workspace)throw new Error('保存前请先打开工作区');
  const identity=state.identity, version=state.version, workspace=state.workspace.id, workspaceEpoch=state.workspaceEpoch, request=++state.saveRequest, document=structuredClone(state.document), task=taskInput(), saved=state.saved?{...state.saved}:null;
  const result=await api('/api/draft',{workspace,document,id:saved?.id||null,expected:saved?.revision||null,task});
  if(identity===state.identity&&workspace===state.workspace?.id&&workspaceEpoch===state.workspaceEpoch) {
    // A concurrent response must not replace a newer confirmed revision.
    const stillExpected=state.saved?.revision===(saved?.revision);
    // Overlapping creations have distinct IDs. Only the newest request may
    // choose this draft's first identity; confirmed revisions still use CAS.
    if((saved||request===state.saveRequest)&&(stillExpected||!state.saved&&!saved)){
      state.saved={id:result.id,revision:result.revision};
      state.drafts=[result,...state.drafts.filter(d=>d.id!==result.id)];
      options($('drafts'),[['','已保存流程'],...state.drafts.map(d=>[d.id,d.document.name])],result.id);
      $('save-state').textContent=version===state.version&&JSON.stringify(task)===JSON.stringify(taskInput())?'该快照已保存':'请求发出时的快照已保存；当前仍有未保存改动';
    }
  }
  await status();
}
function field(parent,label,key,value,type='text',choices=null) {
  const nodeId=state.selected;
  const labelNode=text('label',label); const input=document.createElement(choices?'select':type==='textarea'?'textarea':'input');
  if(choices)options(input,choices,value);else if(type==='checkbox'){input.type='checkbox';input.checked=value;}else{if(input.tagName==='INPUT')input.type=type;input.value=value;}
  input.dataset.field=key;labelNode.append(input);parent.append(labelNode);
  input.addEventListener(choices||type==='checkbox'?'change':'input',()=>{
    const n=state.document.nodes.find(n=>n.id===nodeId);if(!n||state.selected!==nodeId)return;
    const result=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value;
    if(key==='label')n.label=result;else n.config[key]=result;
    if(['developer','reviewer','planner'].includes(n.type)&&['provider','profile'].includes(key)){
      const p=state.profiles.find(p=>key==='profile'?p.id===result:p.provider===result);
      if(p){Object.assign(n.config,{profile:p.id,provider:p.provider,model:'unknown',effort:'unknown',session:'new',tools:p.provider==='codex'?['native']:['Read','Glob','Grep','Bash']});}
    }
    if(n.type==='verifier'&&key==='profile')Object.assign(n.config,{target:result==='json-assertions'?(state.verificationProfiles[0]?.id||'default'):result==='javascript'?'tools/studio/static/usable.js':'tools/devflower.py',pattern:'',min_cases:1,max_skipped:0});
    changed();draw(false);if(['provider','profile'].includes(key))configure();
  });return input;
}
function configure() {
  const parent=$('node-config');
  if(parent.contains($('delivery')))$('task-delivery').append($('delivery').closest('label'));
  parent.replaceChildren();
  const n=state.document.nodes.find(n=>n.id===state.selected);if(!n){parent.append(text('p','选择节点以编辑。'));return;}
  parent.append(text('strong',n.type+' · '+n.id));field(parent,'显示名称','label',n.label);
  const c=n.config;
  if(['developer','reviewer','planner'].includes(n.type)) {
    const providers=[...new Set(state.profiles.map(p=>p.provider).filter(p=>['codex','claude'].includes(p)))];
    field(parent,'Provider','provider',c.provider,'text',providers.map(p=>[p,p==='codex'?'Codex':'Claude Code']));
    field(parent,'运行配置','profile',c.profile,'text',state.profiles.filter(p=>p.provider===c.provider).map(p=>[p.id,`${p.provider} · ${p.model} / ${p.effort}`]));
    field(parent,'原生会话','session',c.session,'text',[['new','新建独立原生会话']]);
    parent.append(text('p','新任务使用新的隔离 cwd；已有会话请在会话中心按原环境续聊，已有运行从运行记录恢复。'));
    field(parent,'系统职责','responsibility',c.responsibility,'textarea');
    field(parent,'项目背景','background',c.background,'textarea');
    const advanced=document.createElement('details');advanced.className='agent-advanced';advanced.append(text('summary','高级：模型覆盖、工具与预算'));parent.append(advanced);
    field(advanced,'请求模型（unknown 使用已批准配置）','model',c.model);
    field(advanced,'请求 effort（unknown 使用已批准配置）','effort',c.effort);
    field(advanced,'步骤预算（秒，30–3600）','timeout_seconds',c.timeout_seconds,'number');
    field(advanced,'子 Agent（当前执行器不支持）','subagents',c.subagents,'checkbox').disabled=true;
    advanced.append(text('p','允许工具'));
    for(const tool of c.provider==='codex'?['native']:['Read','Glob','Grep','Bash','Write','Edit']) {
      const label=text('label',tool),input=document.createElement('input');input.type='checkbox';input.checked=c.tools.includes(tool);label.prepend(input);advanced.append(label);
      input.addEventListener('change',()=>{c.tools=input.checked?[...new Set([...c.tools,tool])]:c.tools.filter(t=>t!==tool);changed();});
    }
    parent.append(text('p','实际模型和 effort 在运行结果中展示；服务未返回的字段为 unknown。'));
  } else if(n.type==='verifier') {
    field(parent,'固定验证配置','profile',c.profile,'text',['json-assertions','baseline','javascript'].map(x=>[x,x]));
    if(c.profile==='json-assertions')field(parent,'已注册的断言合同','target',c.target,'text',state.verificationProfiles.map(p=>[p.id,`${p.id} · ${p.cases.length} 项断言`]));
    else field(parent,'源码相对路径','target',c.target);
    parent.append(text('p','用例由可信父进程计数；不接受候选自报 unittest 计数。'));
    field(parent,'最低实际测试数','min_cases',c.min_cases,'number');field(parent,'最多 Skip','max_skipped',c.max_skipped,'number');
  } else if(n.type==='human_gate')field(parent,'实际人工确认问题（请说明需要用户确认的范围）','question',c.question,'textarea');
  else {parent.append(text('p','交付权限在当次授权预览中确认；改连线不能绕过测试与独立审核。'));parent.append($('delivery').closest('label'));}
}
const canvas = new WorkflowCanvas($('canvas'), {
  select:(id,port=false)=>{
    if(state.mode==='run'){state.runSelected=id;renderNodeEvidence();return;}
    if(port){closePanels();state.selected=id;configure();return;}
    state.selected=id;configure();
    const n=state.document.nodes.find(n=>n.id===id);
    if(n.type==='human_gate'){
      $('task-context').append($('node-config'));
      $('task-delivery').append($('delivery').closest('label'));taskStep(state.preview?3:1);
      openPanel('panel-task');
    }else{$('inspector').append($('node-config'));openPanel('inspector');}
  }, change:()=>changed(), connect:addEdge, addAfter:openNodePicker,
  drop:(kind,point)=>addNode(kind,point).catch(e=>notice(e.message)),
  menu:(id,x,y)=>{
    closePanels();const menu=$('node-menu');menu.replaceChildren();
    for(const[label,action]of [['编辑节点',()=>canvas.callbacks.select(id)],['复制节点',()=>{
      const node=state.document.nodes.find(n=>n.id===id);if(!node)return;
      const copy=structuredClone(node);copy.id=node.type+'-'+crypto.randomUUID().slice(0,8);copy.label=node.label+' 副本';
      copy.x=Math.min(10000,node.x+35);copy.y=Math.min(10000,node.y+120);state.document.nodes.push(copy);state.selected=copy.id;changed();draw();
    }],['删除节点',()=>removeSelection()]]){
      const button=text('button',label);button.setAttribute('role','menuitem');button.addEventListener('click',()=>{menu.hidden=true;action();});menu.append(button);
    }
    const bounds=$('studio').getBoundingClientRect();menu.style.left=Math.max(8,Math.min(x-bounds.x,bounds.width-168))+'px';menu.style.top=Math.max(8,Math.min(y-bounds.y,bounds.height-140))+'px';menu.hidden=false;menu.querySelector('button').focus();
  },
  remove:edge=>removeSelection(edge), message:notice, viewport:percent=>$('zoom-level').textContent=percent+'%'
});
function tab(name) {
  if(name==='task'){
    const n=state.document.nodes.find(n=>n.type==='human_gate');if(n){state.selected=n.id;configure();$('task-context').append($('node-config'));}taskStep(state.preview?3:1);openPanel('panel-task');return;
  }
  if(name==='sessions'){openPanel('panel-sessions');return;}
  canvas.cancel();closePanels();state.mode=name==='runs'?'run':'design';canvas.readonly=state.mode==='run';
  state.addContext=null;$('canvas-run').hidden=state.mode==='run';
  if(state.mode==='run')$('empty-canvas').hidden=true;
  $('panel-runs').hidden=state.mode!=='run';$('palette').hidden=true;
  for(const e of document.querySelectorAll('[data-tab]'))e.setAttribute('aria-selected',String(e.dataset.tab===name));
  for(const id of ['undo','redo','delete-node','save','new-draft','drafts','template','workflow-name','flow-options','toggle-palette'])$(id).disabled=state.mode==='run';
  if(state.mode==='run'){
    renderRunCanvas(true);if(!state.run)notice('请选择运行。运行画布使用当次固定快照，与设计草稿隔离。');
  }else{draw();canvas.fit();notice('设计草稿：双击人工确认节点填写任务，双击 Agent 节点配置，再保存并授权运行。');}
}
for(const e of document.querySelectorAll('[data-tab],[data-go]'))e.addEventListener('click',()=>tab(e.dataset.tab||e.dataset.go));
function openNodePicker(source=null,edgeIndex=null,point=null) {
  if(state.mode!=='design')return;
  const node=state.document.nodes.find(n=>n.id===source);
  if(source&&!node)return;
  if(source&&edgeIndex===null)edgeIndex=state.document.edges.findIndex(e=>e.source===source&&e.on==='next');
  const edge=edgeIndex!==null&&edgeIndex>=0?state.document.edges[edgeIndex]:null;
  state.addContext=source||point?{identity:state.identity,source,edge:edge?{...edge}:null,point}:null;
  closePanels();$('palette').hidden=false;$('toggle-palette').setAttribute('aria-expanded','true');
  $('palette-title').textContent=source?'选择下一步':'添加节点';
  $('palette-hint').textContent=source?`接在「${node.label}」之后，现有连线会自动接续。`:'选择一个角色，放入画布。';
  if(document.activeElement?.matches('[data-canvas-action]'))$('palette').querySelector('[data-kind]')?.focus();
}
act('toggle-palette',()=>{if($('palette').hidden)openNodePicker();else{$('palette').hidden=true;state.addContext=null;$('toggle-palette').setAttribute('aria-expanded','false');}});
act('flow-options',()=>openPanel('flow-panel'));
function addEdge(source,target,on) {
  if(source===target)throw new Error('不能连接到自身');
  if(on==='repair'&&(state.document.nodes.find(n=>n.id===source)?.type!=='reviewer'||state.document.nodes.find(n=>n.id===target)?.type!=='developer')){
    notice('修复回路必须从 Reviewer 连接到 Developer');return;
  }
  if(state.document.edges.some(e=>e.source===source&&e.target===target&&e.on===on)){notice('此连接已存在');return;}
  state.document.edges.push({source,target,on});changed();draw(false);
}
function removeSelection(edge=null) {
  if(edge!==null){state.document.edges.splice(edge,1);canvas.edgeSelection=null;}
  else {state.document.nodes=state.document.nodes.filter(n=>n.id!==state.selected);state.document.edges=state.document.edges.filter(e=>e.source!==state.selected&&e.target!==state.selected);state.selected=null;}
  changed();draw();
}
async function addNode(kind,point=null) {
  if(state.mode!=='design')return;
  const identity=state.identity,context=state.addContext,n=await api('/api/node?kind='+encodeURIComponent(kind));if(identity!==state.identity||state.mode!=='design')return;
  if(context!==state.addContext)return;
  const source=context&&state.document.nodes.find(x=>x.id===context.source);
  const edgeIndex=context?.edge?state.document.edges.findIndex(e=>JSON.stringify(e)===JSON.stringify(context.edge)):-1;
  if(context&&(context.source&&!source||context.edge&&edgeIndex<0)){notice('连接已改变，请重新选择添加位置。');return;}
  point=point||context?.point;
  n.id=n.type+'-'+crypto.randomUUID().slice(0,8);
  n.x=Math.round(Math.max(0,Math.min(10000,point?point.x-105:source?source.x+280:canvas.view.x+canvas.view.w/2-105)));
  n.y=Math.round(Math.max(0,Math.min(10000,point?point.y-41:source?source.y+(context.edge?140:0):canvas.view.y+canvas.view.h/2-41)));
  state.document.nodes.push(n);
  if(source){
    const connection={source:source.id,target:n.id,on:'next'};
    if(context.edge)state.document.edges.splice(edgeIndex,1,connection,{source:n.id,target:context.edge.target,on:'next'});
    else state.document.edges.push(connection);
  }
  state.addContext=null;state.selected=n.id;changed();draw();canvas.callbacks.select(n.id);$('palette').hidden=true;$('toggle-palette').setAttribute('aria-expanded','false');
}
function draw(config=true) {
  if(!state.document||state.mode==='run')return;canvas.render(state.document,state.selected);
  $('empty-canvas').hidden=state.document.nodes.length>0;
  const choices=state.document.nodes.map(n=>[n.id,n.label+' ('+n.id+')']);
  options($('edge-source'),choices);options($('edge-target'),choices);
  $('edges').replaceChildren(...state.document.edges.map((e,i)=>{const li=text('li',`${e.source} → ${e.target} · ${e.on}`),button=text('button','删除连线');button.addEventListener('click',()=>removeSelection(i));li.append(button);return li;}));
  $('undo').disabled=state.historyIndex<=0;$('redo').disabled=state.historyIndex>=state.history.length-1;
  if(config)configure();
}
function undo(delta) {
  const index=state.historyIndex+delta;if(index<0||index>=state.history.length)return;
  canvas.cancel();state.historyIndex=index;state.document=structuredClone(state.history[index]);state.selected=null;
  changed(false);$('workflow-name').value=state.document.name;$('max-rounds').value=state.document.max_rounds;draw();
}
act('undo',()=>undo(-1));act('redo',()=>undo(1));act('zoom-in',()=>canvas.zoom(1.2));act('zoom-out',()=>canvas.zoom(1/1.2));act('fit',()=>canvas.fit());
for(const button of document.querySelectorAll('[data-kind]'))button.addEventListener('click',()=>addNode(button.dataset.kind).catch(e=>notice(e.message)));
for(const button of document.querySelectorAll('#palette [data-kind]')){
  button.draggable=true;button.addEventListener('dragstart',e=>{e.dataTransfer.setData('application/x-devflower-node',button.dataset.kind);e.dataTransfer.effectAllowed='copy';$('palette').classList.add('dragging');});
  button.addEventListener('dragend',()=>$('palette').classList.remove('dragging'));
}
document.addEventListener('keydown',e=>{
  if(e.target.closest('input,textarea,select')||state.mode!=='design')return;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undo(e.shiftKey?1:-1);}
});
async function sessions() {
  if(window.sessionDesk)return window.sessionDesk.refresh();
  if(!state.workspace)throw new Error('先打开工作区');
  const epoch=state.workspaceEpoch, value=await api('/api/sessions?workspace='+state.workspace.id);
  if(epoch!==state.workspaceEpoch)return;state.owned=value.owned;$('sessions').replaceChildren();
  for(const row of value.owned) {
    const card=text('div','', 'card');card.append(text('strong',`${row.role} · ${row.id}`),text('p',`${row.state} · model ${row.resolved_model} / effort ${row.resolved_effort}`),
      text('p',`HOME ${row.home}`),text('p',`原生记录 ${row.native_records}`),text('p',row.resume?'可在对应角色节点明确选择此会话':'运行中的会话不可重复占用'));
    $('sessions').append(card);
  }
  const discovered=value.discovery.sessions||[];
  for(const row of discovered)$('sessions').append(text('div',`${row.provider||row.tool||'native'} · ${row.session_id||row.id} · 外部发现，运行未确认`,'card'));
  if(!value.owned.length&&!discovered.length)$('sessions').append(text('p','此工作区尚无可显示会话。首次运行将创建持久原生会话。'));
  if(!$('node-config').contains(document.activeElement))configure();
}
function taskInput() {
  return {title:$('task-title').value,body:$('task-body').value,scope:$('task-scope').value.split('\n').map(x=>x.trim()).filter(Boolean),
    acceptance:$('task-acceptance').value,issue_url:$('issue-url').value};
}
function taskStep(step) {
  if(step===3&&!state.preview)return;
  $('task-input-step').hidden=step!==1;$('task-scope-step').hidden=step!==2;$('task-confirm-step').hidden=step!==3;
  for(const button of document.querySelectorAll('[data-task-step]')){
    button.setAttribute('aria-current',Number(button.dataset.taskStep)===step?'step':'false');
    if(button.dataset.taskStep==='3')button.disabled=!state.preview;
  }
}
for(const button of document.querySelectorAll('[data-task-step]'))button.addEventListener('click',()=>taskStep(Number(button.dataset.taskStep)));
act('task-next',()=>{
  const missing=['task-title','task-body'].find(id=>!$(id).value.trim());
  if(missing){notice('先填写任务名称和目标，再确认范围。');$(missing).focus();return;}
  taskStep(2);
});
async function prepare() {
  if(!state.workspace)throw new Error('先打开工作区');
  const identity=state.identity,version=state.version,epoch=state.workspaceEpoch,input=taskInput(),delivery=$('delivery').checked;
  const value=await api('/api/prepare',{workspace:state.workspace.id,workflow:state.document,task:input,delivery:{commit:delivery,push:delivery,pr:delivery}});
  if(identity!==state.identity||version!==state.version||epoch!==state.workspaceEpoch||JSON.stringify(taskInput())!==JSON.stringify(input)||$('delivery').checked!==delivery)return;
  state.preview=value;taskStep(3);const parent=$('preview');parent.replaceChildren();
  parent.append(text('h3','确认本次合同与权限'),text('p',`工作区 ${value.data.source.root} · ${value.data.source.branch} · ${value.data.source.head}`),
    text('p',`源码 ${value.data.source_sha256}`),text('p',`合同预览 SHA ${value.sha256}`),
    text('p',`允许路径：${input.scope.join(', ')}；外部模型将接收这些源码。`),
    text('p',delivery?'允许受审候选 commit/push/PR；不允许 merge、force、delete、release。':'仅开发、验证与审核；不允许 Git 交付。'));
  const target=value.data.config.github_delivery;
  if(target)parent.append(text('p',`GitHub ${target.repository} · ${target.branch} → ${target.base}`),
    text('p',`绑定 PR：${target.existing_pr===null?'新建':`#${target.existing_pr}`} · 预期远端 tip：${target.expected_tip||'分支尚不存在'}`));
  parent.append(text('p',`测试隔离：${value.data.config.verification_isolation||'landlock'}`));
  for(const[role,b]of Object.entries(value.data.config.bindings))parent.append(text('p',`${role}: ${b.provider} · ${b.model}/${b.effort} · ${b.session_id||'新会话'} · HOME ${b.home} · ${b.role==='implementer'?'实施副本可写':'源码只读'} · 工具 ${b.tools.join(', ')} · ${b.timeout_seconds} 秒`));
  const tests=document.createElement('details');tests.append(text('summary','查看绑定的测试断言与执行配置'),text('pre',JSON.stringify(value.data.config.policy.verification,null,2)));parent.append(tests);
  const answers=value.data.human_questions.map(question=>{const label=text('label','必填：请说明你确认的任务范围、允许路径、外部模型使用与本次交付权限。'),answer=document.createElement('textarea');answer.rows=3;answer.required=true;answer.maxLength=4000;answer.placeholder='例如：我确认上述任务和允许路径，同意本次外部模型执行；交付权限以预览为准。';const detail=text('details',''),summary=text('summary','查看此关口的具体问题');detail.append(summary,text('p',question));label.append(detail,answer);parent.append(label);return answer;});
  const button=text('button','人工确认并运行');
  button.addEventListener('click',async()=>{
    if(state.preview!==value||button.disabled)return;
    const missing=answers.find(a=>!a.value.trim()||a.value.length>4000);
    if(missing){notice('请填写每个人工确认回复（1–4000 字）：说明确认的范围与授权后再启动。');missing.setAttribute('aria-invalid','true');missing.focus();return;}
    button.disabled=true;
    try{
      const result=await api('/api/authorize-run',{permit:value.id,sha256:value.sha256,answers:answers.map(a=>a.value)});
      state.preview=null;parent.replaceChildren(text('p','已使用本次授权，运行 '+result.run_id));tab('runs');selectRun(result.run_id);await status();
    }catch(e){notice(e.message);parent.append(text('p','授权可能已消费，请检查运行列表；不要盲目重试。'));}
  });parent.append(button);
}
function selectRun(run) {
  state.run=run||null;state.snapshot=null;state.runSelected=null;state.runEpoch++;closePanels();if(state.mode==='run')canvas.render({nodes:[],edges:[]});state.pollPending=null;if(run)sessionStorage.setItem('devflower-run',run);$('monitor').replaceChildren();$('run-title').textContent=run?'读取 '+run+'…':'未选择运行';poll();
}
function runGraph() { return state.snapshot?.workflow_graph||state.snapshot?.graph; }
function renderRunCanvas(fit=false) {
  if(state.mode!=='run')return;
  const graph=runGraph(), previous=canvas.runIdentity;
  const identity=state.run+':'+(graph?.sha256||'legacy');
  canvas.render(graph||{nodes:[],edges:[]},state.runSelected);canvas.runIdentity=identity;
  if(fit||previous!==identity)canvas.fit();
  if(graph&&!state.snapshot.workflow_graph)notice('历史运行：没有保存用户工作流身份，显示原执行图。');
  if(state.runSelected&&!$('evidence-panel').hidden)renderNodeEvidence(false);
}
function renderNodeEvidence(open=true) {
  const snapshot=state.snapshot, run=state.run, epoch=state.runEpoch;
  const n=runGraph()?.nodes.find(n=>n.id===state.runSelected);if(!snapshot||!n)return;
  const evidenceKey=JSON.stringify([run,epoch,n,snapshot.state,snapshot.candidate]);
  if(!open&&state.evidenceKey===evidenceKey)return;state.evidenceKey=evidenceKey;
  $('evidence-title').textContent=n.label+' · '+n.id;
  const parent=$('node-evidence');parent.replaceChildren();
  parent.append(text('p',`固定运行 ${run} · 当前轮次 ${snapshot.round} · ${n.status}`));
  parent.append(text('p',state.snapshot.workflow_graph?'本次授权的固定工作流快照':'历史执行图；用户画布身份未记录'));
  if(n.agent){
    const a=n.agent, rows=[['原生会话',a.session_id],['请求模型',a.requested_model],['请求强度',a.requested_effort],['实际模型',a.resolved_model],['实际强度',a.resolved_effort],['进程 PID',a.pid??a.process?.pid??a.process?.process?.pid]];
    const list=text('dl','','evidence-facts');for(const[label,value]of rows)list.append(text('dt',label),text('dd',String(value??'unknown')));parent.append(list);
    const details=document.createElement('details');details.append(text('summary','进程与会话原始证据'),text('pre',JSON.stringify(a,null,2)));parent.append(details);
  }
  if(n.type==='verifier'||n.type==='verification'){
    parent.append(text('h3','此节点的本轮测试'),text('p',n.counts?`${n.counts.tests} 项实际测试 / ${n.counts.failures} 失败 / ${n.counts.errors} 错误 / ${n.counts.skipped} Skip`:'本轮尚无已验证测试计数'));
    for(const r of n.results||[])parent.append(text('pre',JSON.stringify(r,null,2)));
  }
  if(n.type==='reviewer'){
    parent.append(text('p','本轮实际裁决：'+(n.verdict||snapshot.verdict||'未确认')));
    for(const f of n.findings||snapshot.findings||[])parent.append(text('p',`${f.id} · ${f.status} · ${f.title}`));
    parent.append(text('p','未关闭缺陷：'+(n.open_findings??(snapshot.findings||[]).filter(f=>f.status!=='CLOSED').length)));
  }
  if(n.type==='human_gate'||n.type==='authorization'){
    parent.append(text('p',n.confirmation||'人工确认状态见 Controller 证据'));
    if(snapshot.human_gate){const button=text('button','填写本轮结果节点确认');button.addEventListener('click',()=>{$('monitor').closest('details').open=true;document.querySelector('[data-result-answer]')?.focus();});parent.append(button);}
  }
  if(n.type==='delivery'){
    const receipt=n.delivery||(!snapshot.workflow_graph?snapshot.github_receipt:null);
    parent.append(text('h3','交付权限与门禁'),text('pre',JSON.stringify(receipt||snapshot.delivery,null,2)),text('p',`Controller 阶段 ${snapshot.state} · 当前审核 ${snapshot.verdict||'未确认'}；仅固定 gate 可以允许交付。`));
    const pr=confirmedPR(receipt);
    if(pr){const a=text('a','打开真实开发 PR');a.href=pr.url;a.target='_blank';a.rel='noopener noreferrer';parent.append(a);}
    else parent.append(text('p','尚无已读回确认的 PR 入口。'));
  }
  if(['developer','implementer','reviewer','planner'].includes(n.type)){
    const sessionsButton=text('button','原生会话与发现');sessionsButton.addEventListener('click',()=>{tab('sessions');sessions().catch(e=>notice(e.message));});parent.append(sessionsButton);
    if(snapshot.candidate){const button=text('button','查看本轮固定候选 diff'),area=text('pre','');parent.append(button,area);button.addEventListener('click',async()=>{try{
      const value=await api('/api/diff?id='+encodeURIComponent(run));
      if(state.run!==run||state.runEpoch!==epoch||state.snapshot?.candidate!==snapshot.candidate||value.run_id!==run||value.candidate!==snapshot.candidate||value.round!==snapshot.round||!area.isConnected)return;
      area.textContent=(value.truncated?'仅显示前 200 KB。\n':'')+value.text;
    }catch(e){if(area.isConnected&&state.run===run&&state.runEpoch===epoch)area.textContent=e.message;}});}
  }
  if(open)openPanel('evidence-panel');
}
function renderSnapshot(run,snapshot) {
  state.snapshot=snapshot;renderRunCanvas();
  $('run-title').textContent=`${run} · ${snapshot.state||'UNKNOWN'} · 当前轮次 ${snapshot.round??'未确认'}`;
  const renderKey=[run,snapshot.state,snapshot.round,snapshot.candidate].join(':');
  if(snapshot.human_gate&&state.renderKey===renderKey&&document.querySelector('[data-result-answer]'))return;
  state.renderKey=renderKey;
  const parent=$('monitor');parent.replaceChildren();
  if(snapshot.reason)parent.append(text('p',snapshot.reason));
  if(snapshot.current_agent) {
    const a=snapshot.current_agent;
    parent.append(text('p',`${a.role} · ${a.provider} · 会话 ${a.session_id} · 请求 ${a.requested_model}/${a.requested_effort} · cwd ${a.cwd||'unknown'} · ${a.permission||'unknown'} · tools ${(a.tools||[]).join(', ')} · PID ${a.process?.pid??'unknown'} · ${a.alive?'实际进程存活':'进程已结束或活性未确认'}`));
  }
  const stages=text('div','','row');
  for(const n of (snapshot.workflow_graph||snapshot.graph)?.nodes||[]) {
    const c=n.counts;
    stages.append(text('div',`${n.label}: ${n.status}${c?` · ${c.tests} tests / ${c.failures} failures / ${c.errors} errors / ${c.skipped} skipped`:''}`,'card'));
  }
  parent.append(stages);
  if(snapshot.verdict)parent.append(text('p','本轮独立审核：'+snapshot.verdict));
  for(const finding of snapshot.findings||[])parent.append(text('p',`${finding.id} · ${finding.status} · ${finding.title}`));
  const pr=confirmedPR(snapshot.github_receipt);
  if(pr) {
    const link=text('a','打开开发 PR');link.href=pr.url;link.target='_blank';link.rel='noopener noreferrer';parent.append(link);
  }
  if(snapshot.human_gate) {
    const rule=snapshot.human_gate, modern=rule.mode==='node-results';
    const gates=modern?rule.gates:[{id:'G4',question:rule.question}];
    const answers=gates.map(g=>{const label=text('label',`${g.id} · ${g.question}`),input=document.createElement('textarea');input.required=true;input.maxLength=4000;input.dataset.resultAnswer=g.id;label.append(input);parent.append(label);return {id:g.id,input};});
    const approve=text('button','确认当前候选的全部结果节点');parent.append(approve);
    approve.addEventListener('click',async()=>{
      if(state.run!==run||approve.disabled||state.snapshot?.candidate!==snapshot.candidate||state.snapshot?.round!==snapshot.round||state.snapshot?.contract_sha256!==snapshot.contract_sha256)return;
      const missing=answers.find(x=>!x.input.value.trim()||x.input.value.length>4000);
      if(missing){notice('请分别填写每个结果节点的解释与确认（1–4000 字）。');missing.input.focus();return;}
      const payload=modern?{run_id:run,answers:answers.map(x=>({id:x.id,answer:x.input.value})),expected:{candidate_sha256:snapshot.candidate,acceptance_sha256:snapshot.contract_sha256,round:snapshot.round}}:{run_id:run,answer:answers[0].input.value};
      approve.disabled=true;try{await api('/api/approve-result',payload);await poll();}
      catch(e){notice(e.message);parent.append(text('p','结果确认可能已消费，请刷新核对；不要盲目重发。'));}
    });
  }
  if(snapshot.candidate) {
    const diffButton=text('button','查看固定候选差异'),diffArea=document.createElement('pre');parent.append(diffButton,diffArea);
    diffButton.addEventListener('click',async()=>{try{
      const value=await api('/api/diff?id='+encodeURIComponent(run));
      if(state.run!==run||value.run_id!==run||value.candidate!==snapshot.candidate||!diffArea.isConnected)return;
      diffArea.textContent=(value.truncated?'差异超过显示上限，以下仅为前 200 KB。\n':'')+value.text;
    }catch(e){if(diffArea.isConnected)diffArea.textContent=e.message;}});
  }
  for(const[role,a]of Object.entries(snapshot.agents||{}))parent.append(text('div',`${role} 最近完成结果（round ${a.result_round??'unknown'}）: ${a.session_id} · ${a.resolved_model??'unknown'} / ${a.resolved_effort??'unknown'} · PID ${a.process?.process?.pid??'unknown'}`,'card'));
  // The whole monitoring panel is replaced from a single identity-checked snapshot.
  for(const[key,value]of Object.entries(snapshot)) {
    if(['agents','run_id','state','round','reason'].includes(key))continue;
    const detail=document.createElement('details');detail.append(text('summary',key),text('pre',JSON.stringify(value,null,2)));parent.append(detail);
  }
}
async function poll() {
  const run=state.run;if(!run)return;const epoch=state.runEpoch,key=run+':'+epoch;
  if(state.pollPending===key)return;state.pollPending=key;const request=++state.runRequest;
  try{const snapshot=await api('/api/run?id='+encodeURIComponent(run));if(state.run!==run||epoch!==state.runEpoch||request!==state.runRequest)return;
    if((snapshot.run_id||snapshot.id)!==run)throw new Error('运行快照身份不匹配');renderSnapshot(run,snapshot);
  }catch(e){if(state.run!==run||epoch!==state.runEpoch||request!==state.runRequest)return;$('run-title').textContent=run+' · 状态未确认';$('monitor').replaceChildren(text('p',e.message,'error'));state.snapshot=null;state.runSelected=null;if(state.mode==='run'){canvas.render({nodes:[],edges:[]});closePanels();}}
  finally{if(request===state.runRequest)state.pollPending=null;}
}
act('open-workspace',async()=>{const epoch=++state.workspaceEpoch;const w=await api('/api/workspace',{path:$('workspace-path').value});if(epoch===state.workspaceEpoch)setWorkspace(w);});
$('recent').addEventListener('change',()=>{const w=state.workspaces.find(w=>w.id===$('recent').value);if(w)setWorkspace(w);});
act('new-blank',()=>newDraft('blank'));act('new-draft',()=>newDraft());act('save',saveDraft);
$('drafts').addEventListener('change',()=>{const d=state.drafts.find(d=>d.id===$('drafts').value);if(d&&d.workspace===state.workspace?.id)loadDocument(d.document,{id:d.id,revision:d.revision,task:d.task});});
$('workflow-name').addEventListener('input',()=>{state.document.name=$('workflow-name').value;changed();});
$('max-rounds').addEventListener('change',()=>{state.document.max_rounds=Number($('max-rounds').value);changed();});
act('add-node',()=>addNode($('add-kind').value));
act('delete-node',()=>removeSelection(canvas.edgeSelection));
act('connect',()=>addEdge($('edge-source').value,$('edge-target').value,$('edge-on').value));
act('compile',async()=>{const identity=state.identity,version=state.version;
  try{const p=await api('/api/compile',{document:state.document});if(identity===state.identity&&version===state.version){$('graph-status').textContent='顺序校验通过 · '+p.order.length+' 个节点';notice('可执行顺序：'+p.order.join(' → '));}}
  catch(e){if(identity===state.identity&&version===state.version){$('graph-status').textContent='不能执行：'+e.message;throw e;}}
});
act('refresh-sessions',sessions);act('refresh-runs',status);
act('import-issue',async()=>{const epoch=state.workspaceEpoch,url=$('issue-url').value,v=await api('/api/issue',{url});if(epoch!==state.workspaceEpoch||url!==$('issue-url').value)return;$('task-title').value=v.title;$('task-body').value=v.body;$('task-acceptance').value=v.body;changed(false);notice('已导入真实 Issue，请核对允许路径与验收文本。');});
for(const id of ['task-title','task-body','task-scope','task-acceptance','issue-url'])$(id).addEventListener('input',()=>changed(false));
$('delivery').addEventListener('input',()=>invalidatePreview());
act('prepare',prepare);$('runs').addEventListener('change',()=>selectRun($('runs').value));
act('resume',async()=>{if(!state.run)throw new Error('选择运行');await api('/api/resume',{run_id:state.run});await poll();});
act('cancel',async()=>{if(!state.run)throw new Error('选择运行');await api('/api/cancel',{run_id:state.run});await poll();});
setInterval(poll,2000);
(async()=>{try{await status();await newDraft();const workspace=state.workspaces.find(w=>w.id===sessionStorage.getItem('devflower-workspace'));if(workspace){setWorkspace(await api('/api/workspace',{path:workspace.path}));const run=sessionStorage.getItem('devflower-run');if(run){await status();if([...$('runs').options].some(o=>o.value===run)){$('runs').value=run;selectRun(run);}}}notice(state.sessionOnly?'Development 会话候选已固定；可测试已授权原生会话。Controller 运行与交付仅由另行批准的 Stable 提供。':'Stable 身份已核验。画布中双击人工确认节点填写任务；双击 Agent 节点配置。运行快照与草稿独立。');}catch(e){notice(e.message);}finally{$('studio').inert=false;$('studio').dataset.ready='true';}})();
