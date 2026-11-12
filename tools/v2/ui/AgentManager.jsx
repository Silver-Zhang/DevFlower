import React, {useEffect, useRef, useState} from 'react';
import {Plus, X} from 'lucide-react';

const TEMPLATES = {
 planner: ['规划者', '拆解目标、约束和验收条件。'],
 developer: ['开发者', '根据任务实现并验证改动。'],
 reviewer: ['评审者', '核验结果、反例和遗漏。'],
 custom: ['', '']
};
const LABELS = {codex: 'Codex CLI', claude: 'Claude Code'};
const SOURCES = {cli_catalog: 'CLI 目录', cli_alias: 'CLI 别名', configured_alias: '原生配置别名',
 native_config: '原生配置', cli_configured: '原生配置'};
const PERMISSIONS = {'none': '仅对话（无工具）', 'workspace-read': '读取工作区', 'workspace-write': '读写工作区',
 'controlled-command': '受控命令', 'native': 'CLI 原生权限', 'manual': '人工确认'};
const fresh = workspace => ({name: '', role: '', responsibilities: '', background: '', goal: '', template: 'custom',
 provider: 'codex', model: null, model_source: 'native_default', effort: null, mode: 'Ask', permission: 'none',
 cwd: workspace.path, session: {kind: 'new'}, skills: [], subagents: 'disabled', timeout: 300, max_budget_usd: null,
 initial_prompt: '', round_prompt: '', output_prompt: ''});

function Field({label, children}) {
 return <label className="field"><span>{label}</span>{children}</label>;
}

export function AgentManager({api, workspace, writeProviders, selectedNode, onBind, onRun, onClose, onChanged, visible}) {
 const [rows, setRows] = useState([]);
 const [agentQuery,setAgentQuery]=useState('');
 const [providers,setProviders]=useState([]),[profiles,setProfiles]=useState([]);
 const [record, setRecord] = useState(null);
 const [config, setConfig] = useState(null);
 const [sessions, setSessions] = useState(null);
 const [skills, setSkills] = useState(null);
 const [error, setError] = useState('');
 const [notice, setNotice] = useState('');
 const [busy, setBusy] = useState(false);
 const [deleteAsk, setDeleteAsk] = useState(false);
 const [directTask, setDirectTask] = useState('');
 const [pendingRun, setPendingRun] = useState(null);
 const alive = useRef(true);
 const running = useRef(false);
 const close = useRef(null), nameInput = useRef(null);
 const newId = useRef(null);

 useEffect(() => {
  alive.current = true;
  Promise.all([api('agents?workspace=' + workspace.id), api('cli-providers')]).then(([items, catalog]) => {
   if (!alive.current) return;
   setRows(items.agents);setProviders(catalog.providers);setProfiles(catalog.profiles||[]);
  }).catch(e => {if (alive.current) setError(e.message);});
  return () => {alive.current = false;};
 }, []);
 useEffect(() => {if (visible) close.current?.focus();}, [visible]);

 async function action(fn) {
  if (running.current) return;
  running.current = true; setBusy(true); setError(''); setNotice('');
  try {await fn();}
  catch (failure) {if (alive.current) setError(failure.message);}
  finally {running.current = false; if (alive.current) setBusy(false);}
 }
 function choose(row) {
  setRecord(row); setConfig(row?.config || fresh(workspace)); setSessions(null); setSkills(null);
  newId.current = row?.id || crypto.randomUUID().replaceAll('-', '');
  setDirectTask('');
  let pending = null;
  if (row) {
   try {pending = JSON.parse(sessionStorage.getItem(runKey(row.id)));}
   catch {}
  }
  setPendingRun(pending);
  setError(''); setNotice(''); setDeleteAsk(false);
  if (!row) requestAnimationFrame(() => nameInput.current?.focus());
 }
 function change(fields) {setConfig(old => ({...old, ...fields}));}
 function changeProvider(provider) {
  change({provider, model: null, model_source: 'native_default', effort: null, mode: 'Ask', permission: 'none',
   session:{kind:'new'},skills:[],subagents:'disabled',max_budget_usd:null,native_profile:null});
  setSessions(null); setSkills(null);
 }
 async function discover(provider) {
  const selected=profiles.find(row=>row.provider===provider&&row.id===config.native_profile);
  const result = await api('cli-providers/discover', {provider,
   ...(selected?.kind==='isolated-home'?{native_profile:selected.id}:{})});
  if(alive.current){setProviders(result.providers);setProfiles(result.profiles||[])}
 }
 async function loadSessions() {
  const result = await api('agent-sessions?' + new URLSearchParams({
   workspace:workspace.id,provider:config.provider,cwd:config.cwd,
   ...(config.native_profile?{native_profile:config.native_profile}:{})}));
  if (alive.current) setSessions(result);
 }
 async function loadSkills() {
  const query = new URLSearchParams({workspace: workspace.id, provider: config.provider, cwd: config.cwd});
  const result = await api('agent-skills?' + query);
  if (alive.current) setSkills(result);
 }
 async function save(event) {
  event.preventDefault();
  await action(async () => {
   const changedSession = config.session.kind === 'existing'
    && (config.session.handle !== record?.config.session.handle || config.provider !== record?.config.provider
     || config.cwd !== record?.config.cwd || config.native_profile !== record?.config.native_profile);
   const saved = await api('agents/save', {workspace: workspace.id, id: newId.current, expected: record?.revision ?? null,
    config, session_revision: changedSession ? sessions?.revision ?? null : null});
   if (!alive.current) return;
   setRows(items => [saved, ...items.filter(item => item.id !== saved.id)]);
   setRecord(saved); setConfig(saved.config); setNotice('Agent 配置已保存。'); onChanged?.();
  });
 }
 async function remove() {
  await action(async () => {
   await api('agents/remove', {workspace: workspace.id, id: record.id, expected: record.revision});
   if (!alive.current) return;
   setRows(items => items.filter(item => item.id !== record.id)); setRecord(null); setConfig(null); setDeleteAsk(false); onChanged?.();
  });
 }
 function runKey(id) {return 'df-agent-launch:' + workspace.id + ':' + id;}
 async function launch() {
  await action(async () => {
   const data = pendingRun || {workspace: workspace.id, id: record.id, revision: record.revision,
    intent_key: crypto.randomUUID(), task: directTask};
   const key = runKey(record.id);
   sessionStorage.setItem(key, JSON.stringify(data));
   setPendingRun(data);
   try {
    const result = await api('agents/launch', data);
    sessionStorage.removeItem(key);
    if (alive.current) {setPendingRun(null); onRun(result.run.id);}
   } catch (failure) {
    if (!failure.uncertain && failure.status && failure.status < 500) {
     sessionStorage.removeItem(key);
     if (alive.current) setPendingRun(null);
    }
    throw failure;
   }
  });
 }
 const nativeProfiles=profiles.filter(row=>row.provider===config?.provider);
 const nativeProfile=nativeProfiles.find(row=>row.id===config?.native_profile);
 const isolated=nativeProfile?.kind==='isolated-home';
 const profileUnavailable=Boolean(config?.native_profile&&!nativeProfile?.available);
 const provider=profileUnavailable?null:isolated?nativeProfile.catalog:providers.find(row=>row.provider===config?.provider);
 const isDirty = Boolean(config && (!record || JSON.stringify(config) !== JSON.stringify(record.config)));
 const model = provider?.models.find(item => item.id === config?.model);
 const efforts = model?.efforts?.length ? model.efforts : provider?.efforts ?? [];
 const sessionRows = sessions?.sessions ?? [];
 const modelValue = config?.model_source === 'manual' ? 'manual' : config?.model ?? '';
 const missingIdentity = Boolean(config && (!config.name.trim() || !config.role.trim()));
 const visibleRows=rows.filter(row=>[row.config.name,row.config.role,LABELS[row.config.provider]]
  .some(value=>value.toLocaleLowerCase().includes(agentQuery.trim().toLocaleLowerCase())));
 const agentSections=useRef({});
 function focusSection(id){
  const field=agentSections.current[id];
  field?.scrollIntoView({block:'start'});
  field?.focus({preventScroll:true});
 }
 const editText = (label, key, rows) => <Field label={['name','role'].includes(key)?label+'（必填）':label}>{rows ? <textarea aria-label={label} rows={rows}
  value={config[key]} onChange={event => change({[key]: event.target.value})}/> : <input aria-label={label}
  required={key==='name'||key==='role'} ref={key==='name'?nameInput:undefined}
  value={config[key]} onChange={event => change({[key]: event.target.value})}/>}</Field>;

 return <main id="v2-agents" tabIndex={-1} className="agent-manager" aria-label="Agent 管理" hidden={!visible}
  onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); onClose();}}}>
  <header><div><h2>Agent 管理</h2><small>{workspace.name}{selectedNode?' · 为当前画布节点选择配置':''}</small></div>
   {(config||rows.length>0)&&<button type="button" className={!config?'primary':undefined} onClick={() => choose(null)} disabled={busy}><Plus size={15}/>新建 Agent</button>}
   <button ref={close} aria-label="关闭 Agent 管理" onClick={onClose}><X size={17}/></button></header>
  <div className="agent-manager-layout">
   <nav aria-label="已保存 Agent">
    {rows.length>0&&<input type="search" aria-label="搜索已保存 Agent" placeholder="搜索 Agent"
     value={agentQuery} onChange={event=>setAgentQuery(event.target.value)}/>}
    {rows.length>0&&!visibleRows.length&&<p className="field-note">没有匹配的 Agent。</p>}
    {visibleRows.map(row => <button key={row.id} aria-pressed={record?.id === row.id} onClick={() => choose(row)} disabled={busy}>
     <strong>{row.config.name}</strong><small>{row.config.role} · {LABELS[row.config.provider]}</small></button>)}
   </nav>
   {!config ? <div className="agent-manager-empty"><h3>{selectedNode?'为当前节点选择 Agent':'为工作流准备 Agent'}</h3>
    <p>保存配置后，可以将同一个 Agent 用在不同的工作流节点。</p>
    {!rows.length&&<button type="button" className="primary" disabled={busy} onClick={() => choose(null)}>新建 Agent</button>}</div> :
    <form onSubmit={save} aria-label="Agent 配置表单" aria-busy={busy}>
     <div className="agent-form-heading">
      <div><h3>{config.name.trim()||'新建 Agent 配置'}</h3><p className="field-note">{isDirty?'未保存的更改':'已保存的配置'}</p></div>
      <div className="agent-form-sections" role="group" aria-label="配置章节">
       <button type="button" onClick={()=>focusSection('identity')}>身份与模型</button>
       <button type="button" onClick={()=>focusSection('prompt')}>Prompt</button>
       <button type="button" onClick={()=>focusSection('tools')}>工具与权限</button>
      </div>
     </div>
     <div className="agent-form-scroll">
     <fieldset ref={element=>{agentSections.current.identity=element}} tabIndex={-1} disabled={busy}><legend>身份与角色</legend>
      {missingIdentity&&<p id="agent-save-hint" className="field-note">填写名称与角色后即可保存。角色模板可以代填角色。</p>}
      <div className="agent-manager-pair">{editText('Agent 名称', 'name')}
       <Field label="角色模板"><select aria-label="角色模板" value={config.template} onChange={event => {
        const template = event.target.value; const [role, responsibilities] = TEMPLATES[template];
        change(template === 'custom' ? {template} : {template, role, responsibilities});
       }}>{Object.entries(TEMPLATES).map(([id, [label]]) => <option key={id} value={id}>{label || '自定义'}</option>)}</select></Field>
      </div>
      {editText('角色', 'role')}
     </fieldset>
     <fieldset disabled={busy}><legend>CLI 与模型</legend>
      <div className="agent-manager-pair">
       <Field label="Agent Provider"><select aria-label="Agent Provider" value={config.provider}
        onChange={event => changeProvider(event.target.value)}>
        {Object.entries(LABELS).map(([id, label]) => <option value={id} key={id}>{label}</option>)}
       </select></Field>
       <button type="button" onClick={() => action(() => discover(config.provider))}>发现本机 CLI</button>
      </div>
      {(nativeProfiles.length>0||config.native_profile)&&<Field label="原生 Profile">
       <select aria-label="原生 Profile" value={config.native_profile||''} onChange={event=>{
        const next=nativeProfiles.find(row=>row.id===event.target.value);
        change({native_profile:event.target.value||null,model:null,model_source:'native_default',effort:null,
         session:{kind:'new'},...(next?.kind==='isolated-home'?{mode:'Ask',permission:'none',skills:[]}: {})});
        setSessions(null);setSkills(null);
       }}>
        <option value="">默认原生配置</option>
        {config.native_profile&&!nativeProfile&&<option value={config.native_profile} disabled>{config.native_profile}（未登记）</option>}
        {nativeProfiles.map(row=><option key={row.id} value={row.id} disabled={!row.available}>
         {row.id}{row.kind==='isolated-home'?' · 独立 HOME':''}{row.available?'':'（核验未通过）'}</option>)}
       </select></Field>}
      {nativeProfile?.available&&<p className="field-note">
       模型 {nativeProfile.model||'沿用默认'} · 推理强度 {nativeProfile.effort||'沿用默认'}</p>}
      {profileUnavailable&&<p className="field-note">此 Profile 未登记或已变化。请核对原生配置，再明确选择。</p>}
      {isolated&&<p className="field-note">此 Agent 使用独立账户与历史。Ask 禁用文件、命令和扩展工具，可绑定本工作区的受管会话。</p>}
      <p className="field-note">{provider?.version || '尚未核验 CLI 版本'}
       {provider?.authenticated === true ? ' · 已观察到认证配置' : provider?.authenticated === false ? ' · CLI 未报告登录' : ''}
       {provider?.stale && provider?.refreshed_at ? ' · 发现记录待刷新' : ''}。{provider?.error || ''}</p>
      {provider?.model_note && <p className="field-note">{provider.model_note}</p>}
      <div className="agent-manager-pair">
       <Field label="Agent 模型"><select aria-label="Agent 模型" value={modelValue} onChange={event => {
        const id = event.target.value;
        const row = provider?.models.find(item => item.id === id);
        change({model: id && id !== 'manual' ? id : null, effort: null,
         model_source: id === 'manual' ? 'manual' : !id ? 'native_default' : row?.source === 'cli_catalog' ? 'catalog' : 'configured'});
       }}><option value="">使用 CLI 默认{provider?.default_model ? '（' + (nativeProfile?.model||provider.default_model.id) + '）' : nativeProfile?.model?'（'+nativeProfile.model+'）':''}</option>
        {modelValue && modelValue !== 'manual' && !model && <option value={modelValue}>{modelValue}（待核验）</option>}
        {provider?.models.map(item => <option value={item.id} key={item.id}>{item.name} · {SOURCES[item.source] || item.source}</option>)}
        <option value="manual">手动输入模型（待核验）</option></select></Field>
       <Field label="Agent 推理强度"><select aria-label="Agent 推理强度" value={config.effort ?? ''}
        onChange={event => change({effort: event.target.value || null})}>
        <option value="">使用 CLI 默认{provider?.default_effort ? '（' + provider.default_effort + '）' : ''}</option>
        {config.effort && !efforts.includes(config.effort) && <option value={config.effort}>{config.effort}（待核验）</option>}
        {efforts.map(level => <option key={level} value={level}>{level}</option>)}</select></Field>
      </div>
      {config.model_source === 'manual' && <Field label="自定义模型名"><input aria-label="自定义模型名" value={config.model ?? ''}
       onChange={event => change({model: event.target.value})}/></Field>}
     </fieldset>
     <fieldset ref={element=>{agentSections.current.prompt=element}} tabIndex={-1} disabled={busy}><legend>提示词</legend>
      {editText('初始化提示词', 'initial_prompt', 3)}{editText('每轮提示词', 'round_prompt', 4)}
      {editText('输出与交接要求', 'output_prompt', 3)}
      <details className="agent-role-context"><summary>角色职责与上下文</summary>
       {editText('职责', 'responsibilities', 2)}{editText('背景', 'background', 2)}{editText('任务目标', 'goal', 2)}
      </details>
     </fieldset>
     <fieldset ref={element=>{agentSections.current.tools=element}} tabIndex={-1} disabled={busy}><legend>工具、权限与会话</legend>
      <div className="agent-manager-pair">
       <Field label="工作模式"><select aria-label="工作模式" value={config.mode} onChange={event => change({mode: event.target.value})}>
        {['Ask', 'Plan', 'Agent'].map(mode => <option value={mode} key={mode} disabled={mode !== 'Ask' && !provider?.modes[mode]?.enabled}>
         {mode}{mode !== 'Ask' && !provider?.modes[mode]?.enabled ? '（未启用）' : ''}</option>)}
       </select></Field>
       <Field label="工具权限"><select aria-label="工具权限" value={config.permission} onChange={event => change({permission: event.target.value})}>
        {Object.entries(PERMISSIONS).map(([id, label]) => <option key={id} value={id}
         disabled={id !== 'none' && !provider?.permissions?.includes(id)}>{label}{id !== 'none' && !provider?.permissions?.includes(id) ? '（未启用）' : ''}</option>)}
       </select></Field>
      </div>
      {provider?.modes[config.mode] && <p className="field-note">{provider.modes[config.mode].detail}</p>}
      <Field label="Agent 工作目录"><input aria-label="Agent 工作目录" value={config.cwd} onChange={event => {
       change({cwd: event.target.value, session: {kind: 'new'}, skills: []}); setSessions(null); setSkills(null);
      }}/></Field>
      <div className="agent-manager-pair">
       <Field label="原生会话"><select aria-label="原生会话" value={config.session.kind === 'new' ? '' : config.session.handle}
        onChange={event => change({session: event.target.value ? {kind: 'existing', handle: event.target.value} : {kind: 'new'}})}>
        <option value="">每次新建会话</option>
        {config.session.kind === 'existing' && !sessionRows.some(row => row.handle === config.session.handle) &&
         <option value={config.session.handle}>当前绑定的已有会话</option>}
        {sessionRows.map((row, index) => <option key={row.handle} value={row.handle}>{LABELS[row.provider]} 会话 {index + 1}
         {row.updated_at ? ' · ' + new Date(row.updated_at).toLocaleString() : ''}</option>)}</select></Field>
       <button type="button" disabled={!(isolated?nativeProfile.available:writeProviders?.includes(config.provider))} onClick={() => action(loadSessions)}>读取可绑定会话</button>
      </div>
     </fieldset>
     <details className="agent-advanced"><summary>Skills、子 Agent 与预算</summary>
      <button type="button" disabled={busy||isolated} onClick={() => action(loadSkills)}>发现 Skills</button>
      {skills && <><p className="field-note">{config.provider === 'claude' && config.mode !== 'Agent' ?
       'Claude Skills 用于已授权读写的 Agent 模式。' :
       skills.selectable ? '选择此 Agent 使用的原生 Skills。' : '当前 Provider 仅展示 Skills 来源。'}
       {!skills.skills.length && ' 当前没有可显示的 Skills。'}</p>
       {skills.skills.map(item => <label className="agent-skill" key={item.id}><input type="checkbox"
        disabled={!skills.selectable || busy || config.provider === 'claude' && config.mode !== 'Agent'}
        checked={config.skills.includes(item.id)} onChange={event => change({skills: event.target.checked ?
         [...config.skills, item.id] : config.skills.filter(id => id !== item.id)})}/>
        <span><strong>{item.name}</strong> <small>{item.scope === 'user' ? '用户级' : '工作区'}</small><span>{item.description}</span></span>
       </label>)}</>}
      {config.skills.length > 0 && <p className="field-note">已选择 {config.skills.length} 个 Skill。</p>}
      <div className="agent-manager-pair">
       <Field label="子 Agent"><select aria-label="子 Agent" value={config.subagents} onChange={event => change({subagents: event.target.value})}>
        <option value="disabled">关闭</option><option value="native" disabled>CLI 原生子 Agent（未启用）</option>
       </select></Field>
       <Field label="Agent 超时（秒）"><input aria-label="Agent 超时（秒）" type="number" min="10" max="3600" value={config.timeout}
        onChange={event => change({timeout: Number(event.target.value)})}/></Field>
      </div>
      <Field label="原生预算（美元）"><input aria-label="原生预算（美元）" inputMode="decimal" disabled={!provider?.native_budget}
       value={config.max_budget_usd ?? ''} onChange={event => change({max_budget_usd: event.target.value || null})}/></Field>
     </details>
     {record && <Field label="本次 Agent 运行输入"><input aria-label="本次 Agent 运行输入" disabled={busy || !!pendingRun}
      value={pendingRun?.task ?? directTask} onChange={event => setDirectTask(event.target.value)}/></Field>}
     </div>
     <footer>
      {error && <p className="error-text" role="alert">{error}</p>}
      {notice && <p className="field-note" role="status">{notice}</p>}
      <button type="submit" className={isDirty?'primary':undefined} aria-describedby={missingIdentity?'agent-save-hint':undefined}
       disabled={busy || profileUnavailable || missingIdentity}>保存 Agent</button>
      <button type="button" disabled={busy || !record || (isDirty || profileUnavailable) && !pendingRun} onClick={launch}>
       {pendingRun ? '核验 Agent 启动' : '运行此 Agent'}</button>
      <button type="button" className={'bind-agent'+(!isDirty&&record&&!profileUnavailable?' primary':'')} disabled={busy || profileUnavailable || isDirty || !record} onClick={() => onBind(record)}>
       {selectedNode ? '用于当前节点' : '添加到画布'}</button>
      {record && (deleteAsk ? <>
       <button type="button" disabled={busy} onClick={remove}>确认删除配置</button>
       <button type="button" onClick={() => setDeleteAsk(false)}>取消删除</button></> :
       <button type="button" disabled={busy} onClick={() => setDeleteAsk(true)}>删除配置</button>)}
     </footer>
    </form>}
   {!config && error && <p className="error-text" role="alert">{error}</p>}
  </div>
 </main>;
}
