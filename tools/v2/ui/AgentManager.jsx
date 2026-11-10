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

export function AgentManager({api, workspace, writeProviders, selectedNode, onBind, onRun, onClose, visible}) {
 const [rows, setRows] = useState([]);
 const [providers, setProviders] = useState([]);
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
 const close = useRef(null);
 const newId = useRef(null);

 useEffect(() => {
  alive.current = true;
  Promise.all([api('agents?workspace=' + workspace.id), api('cli-providers')]).then(([items, catalog]) => {
   if (!alive.current) return;
   setRows(items.agents); setProviders(catalog.providers);
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
 }
 function change(fields) {setConfig(old => ({...old, ...fields}));}
 function changeProvider(provider) {
  change({provider, model: null, model_source: 'native_default', effort: null, mode: 'Ask', permission: 'none',
   session: {kind: 'new'}, skills: [], subagents: 'disabled', max_budget_usd: null});
  setSessions(null); setSkills(null);
 }
 async function discover(provider) {
  const result = await api('cli-providers/discover', {provider});
  if (alive.current) setProviders(result.providers);
 }
 async function loadSessions() {
  const result = await api('agent-sessions?' + new URLSearchParams({
   workspace: workspace.id, provider: config.provider, cwd: config.cwd}));
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
     || config.cwd !== record?.config.cwd);
   const saved = await api('agents/save', {workspace: workspace.id, id: newId.current, expected: record?.revision ?? null,
    config, session_revision: changedSession ? sessions?.revision ?? null : null});
   if (!alive.current) return;
   setRows(items => [saved, ...items.filter(item => item.id !== saved.id)]);
   setRecord(saved); setConfig(saved.config); setNotice('Agent 配置已保存。');
  });
 }
 async function remove() {
  await action(async () => {
   await api('agents/remove', {workspace: workspace.id, id: record.id, expected: record.revision});
   if (!alive.current) return;
   setRows(items => items.filter(item => item.id !== record.id)); setRecord(null); setConfig(null); setDeleteAsk(false);
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
 const provider = providers.find(row => row.provider === config?.provider);
 const isDirty = Boolean(config && (!record || JSON.stringify(config) !== JSON.stringify(record.config)));
 const model = provider?.models.find(item => item.id === config?.model);
 const efforts = model?.efforts?.length ? model.efforts : provider?.efforts ?? [];
 const sessionRows = sessions?.sessions ?? [];
 const modelValue = config?.model_source === 'manual' ? 'manual' : config?.model ?? '';
 const editText = (label, key, rows) => <Field label={label}>{rows ? <textarea aria-label={label} rows={rows}
  value={config[key]} onChange={event => change({[key]: event.target.value})}/> : <input aria-label={label}
  value={config[key]} onChange={event => change({[key]: event.target.value})}/>}</Field>;

 return <aside className="agent-manager" aria-label="Agent 管理" hidden={!visible}
  onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); onClose();}}}>
  <header><div><h2>Agent 管理</h2><small>{workspace.name}</small></div>
   <button ref={close} aria-label="关闭 Agent 管理" onClick={onClose}><X size={17}/></button></header>
  <div className="agent-manager-layout">
   <nav aria-label="已保存 Agent">
    <button onClick={() => choose(null)} disabled={busy}><Plus size={15}/>新建 Agent</button>
    {rows.map(row => <button key={row.id} aria-pressed={record?.id === row.id} onClick={() => choose(row)} disabled={busy}>
     <strong>{row.config.name}</strong><small>{row.config.role} · {LABELS[row.config.provider]}</small></button>)}
   </nav>
   {!config ? <div className="agent-manager-empty"><h3>为工作流准备 Agent</h3>
    <p>选择已保存的 Agent，或创建一份可复用配置。</p></div> :
    <form onSubmit={save} aria-label="Agent 配置表单">
     <fieldset disabled={busy}><legend>身份与角色</legend>
      <div className="agent-manager-pair">{editText('Agent 名称', 'name')}
       <Field label="角色模板"><select aria-label="角色模板" value={config.template} onChange={event => {
        const template = event.target.value; const [role, responsibilities] = TEMPLATES[template];
        change(template === 'custom' ? {template} : {template, role, responsibilities});
       }}>{Object.entries(TEMPLATES).map(([id, [label]]) => <option key={id} value={id}>{label || '自定义'}</option>)}</select></Field>
      </div>
      {editText('角色', 'role')}{editText('职责', 'responsibilities', 2)}
      {editText('背景', 'background', 2)}{editText('任务目标', 'goal', 2)}
     </fieldset>
     <fieldset disabled={busy}><legend>CLI 与会话</legend>
      <div className="agent-manager-pair">
       <Field label="Agent Provider"><select aria-label="Agent Provider" value={config.provider}
        onChange={event => changeProvider(event.target.value)}>
        {Object.entries(LABELS).map(([id, label]) => <option value={id} key={id}>{label}</option>)}
       </select></Field>
       <button type="button" onClick={() => action(() => discover(config.provider))}>发现本机 CLI</button>
      </div>
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
       }}><option value="">使用 CLI 默认{provider?.default_model ? '（' + provider.default_model.id + '）' : ''}</option>
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
       <button type="button" disabled={!writeProviders?.includes(config.provider)} onClick={() => action(loadSessions)}>读取可绑定会话</button>
      </div>
     </fieldset>
     <fieldset disabled={busy}><legend>提示词</legend>
      {editText('初始化提示词', 'initial_prompt', 3)}{editText('每轮提示词', 'round_prompt', 4)}
      {editText('输出与交接要求', 'output_prompt', 3)}
     </fieldset>
     <details className="agent-advanced"><summary>Skills、子 Agent 与预算</summary>
      <button type="button" disabled={busy} onClick={() => action(loadSkills)}>发现 Skills</button>
      {skills && <><p className="field-note">{skills.selectable ? '选择此 Agent 使用的原生 Skills。' : '当前 Provider 仅展示 Skills 来源。'}
       {!skills.skills.length && ' 当前没有可显示的 Skills。'}</p>
       {skills.skills.map(item => <label className="agent-skill" key={item.id}><input type="checkbox" disabled={!skills.selectable || busy}
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
     {error && <p className="error-text" role="alert">{error}</p>}
     {notice && <p className="field-note" role="status">{notice}</p>}
     {record && <Field label="本次 Agent 运行输入"><input aria-label="本次 Agent 运行输入" disabled={busy || !!pendingRun}
      value={pendingRun?.task ?? directTask} onChange={event => setDirectTask(event.target.value)}/></Field>}
     <footer>
      <button type="submit" className="primary" disabled={busy || !config.name.trim() || !config.role.trim()}>保存 Agent</button>
      <button type="button" disabled={busy || !record || isDirty && !pendingRun} onClick={launch}>
       {pendingRun ? '核验 Agent 启动' : '运行此 Agent'}</button>
      <button type="button" disabled={busy || isDirty || !record} onClick={() => onBind(record)}>
       {selectedNode ? '用于当前节点' : '添加到工作流'}</button>
      {record && (deleteAsk ? <>
       <button type="button" disabled={busy} onClick={remove}>确认删除配置</button>
       <button type="button" onClick={() => setDeleteAsk(false)}>取消删除</button></> :
       <button type="button" disabled={busy} onClick={() => setDeleteAsk(true)}>删除配置</button>)}
     </footer>
    </form>}
   {!config && error && <p className="error-text" role="alert">{error}</p>}
  </div>
 </aside>;
}
