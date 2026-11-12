import React from 'react';

export const providerNames={codex:'Codex CLI',claude:'Claude Code'};
const authNames={native_login:'原生登录',native_api_key:'原生 API 认证',native_provider:'CLI 配置的服务',unknown:'来源未报告'};
const modelSources={cli_catalog:'CLI 目录',cli_alias:'原生别名',configured_alias:'本机别名配置',
 native_config:'本机配置',named_profile:'命名配置',native_default:'CLI 默认'};
export const emptySelection={provider:'codex',native_profile:null,model:null,effort:null,identity:null};

export function choicesFor(settings,selection){
 const profiles=(settings?.profiles||[]).filter(row=>row.provider===selection.provider);
 const profile=profiles.find(row=>row.id===selection.native_profile);
 const catalog=profile?.kind==='isolated-home'?profile.catalog:settings?.providers?.find(row=>row.provider===selection.provider);
 const models=[...(catalog?.models||[])];
 for(const row of [catalog?.default_model,profile?.model&&{id:profile.model,source:'named_profile'}]){
  if(row?.id&&!models.some(item=>item.id===row.id))models.push(row);
 }
 const chosen=models.find(row=>row.id===(selection.model||profile?.model||catalog?.default_model?.id));
 const defaultEffort=profile?.effort||(chosen?.id===catalog?.default_model?.id?catalog?.default_effort:null)
  ||chosen?.default_effort;
 const efforts=chosen?.efforts?.length?chosen.efforts:selection.provider==='claude'?(catalog?.efforts||[]):
  defaultEffort?[defaultEffort]:[];
 const available=!!catalog&&!catalog.stale&&catalog.modes?.Ask?.enabled&&(!selection.native_profile||profile?.available);
 const canSend=available&&(selection.provider!=='codex'||Boolean(chosen&&(
  selection.effort||defaultEffort)));
 return {profiles,profile,catalog,models,chosen,efforts,canSend};
}

export function AssistantModelSettings({settings,selection,onChange,onSave,onRefresh,onTest,busy,turn}){
 const {profiles,profile,catalog,models,efforts,canSend}=choicesFor(settings,selection);
 const identities=settings?.identities||[];
 const identity=identities.find(row=>row.id===selection.identity?.id&&row.revision===selection.identity?.revision);
 const compatible=identity&&identity.provider===selection.provider&&(identity.native_profile||null)===selection.native_profile
  &&(!identity.model||models.some(row=>row.id===identity.model));
 const compatibleEfforts=(models.find(row=>row.id===identity?.model)?.efforts||[]);
 const useAgentModel=compatible&&(!identity.effort||(compatibleEfforts.length?compatibleEfforts:catalog?.efforts||[]).includes(identity.effort));
 const matching=config=>config&&['provider','native_profile','model','effort'].every(key=>config[key]===selection[key]);
 const check=matching(turn?.config)&&turn?.mode==='probe'?turn:settings?.checks?.find(row=>matching(row.config));
 const receipt=check?.receipt;
 return <section className="assistant-preview" aria-label="助手模型配置">
  <h3>模型配置</h3>
  <fieldset disabled={busy}>
   <div className="assistant-selects">
    <label>Provider<select aria-label="助手 Provider" value={selection.provider}
     onChange={event=>onChange({...emptySelection,identity:selection.identity,provider:event.target.value})}>
     {Object.entries(providerNames).map(([key,name])=><option key={key} value={key}>{name}</option>)}
    </select></label>
    <label>原生 Profile / HOME<select aria-label="助手原生配置" value={selection.native_profile||''}
     onChange={event=>onChange({...selection,native_profile:event.target.value||null,model:null,effort:null})}>
     <option value="">本机默认 HOME</option>
     {profiles.map(row=><option key={row.id} value={row.id} disabled={!row.available}>{row.id} · {row.kind==='isolated-home'?'独立 HOME':'命名配置'}</option>)}
    </select></label>
   </div>
   <div className="assistant-selects">
    <label>模型<select aria-label="助手模型" value={selection.model||''}
     onChange={event=>onChange({...selection,model:event.target.value||null,effort:null})}>
     <option value="">原生默认{(profile?.model||catalog?.default_model?.id)&&' · '+(profile?.model||catalog.default_model.id)}</option>
     {models.map(row=><option key={row.id} value={row.id}>{row.id} · {modelSources[row.source]||row.source}</option>)}
    </select></label>
    <label>推理强度<select aria-label="助手推理强度" value={selection.effort||''}
     onChange={event=>onChange({...selection,effort:event.target.value||null})}>
     <option value="">原生默认{(profile?.effort||catalog?.default_effort)&&' · '+(profile?.effort||catalog.default_effort)}</option>
     {efforts.map(key=><option key={key}>{key}</option>)}
    </select></label>
   </div>
   {catalog&&!efforts.length&&<p className="field-note">当前 CLI 未报告可选强度，可使用原生默认设置。</p>}
   <p>{catalog?.version||'CLI 版本待检测'} · {catalog?.installed===false?'未安装':catalog?.installed?'已安装':'安装待检测'}</p>
   <p>认证 {catalog?.authenticated===true?'CLI 已报告可用':catalog?.authenticated===false?'CLI 未报告登录':'待核验'} · {authNames[catalog?.auth_source]||authNames.unknown}</p>
   {catalog?.error&&<p className="error-text">{catalog.error}</p>}
   {catalog?.stale&&<p className="field-note">{catalog.refreshed_at?'当前模型目录已过期或不可用。':''}请刷新模型目录。</p>}
   {catalog?.model_note&&<p className="field-note">{catalog.model_note}</p>}
   <p role="status">{check?.state==='COMPLETED'?
    '上次连接测试已收到原生回执 · '+(receipt?.resolved_model&&receipt.resolved_model!=='unknown'?receipt.resolved_model:'实际模型未由 CLI 报告')+
    ' · '+new Date(check.created_at*1000).toLocaleString():
    check?'上次连接测试 '+check.state+(check.reason?' · '+check.reason:''):
    catalog?.stale?'当前选择尚无实际推理回执。':'目录已读取 · 当前选择尚无实际推理回执。'}</p>
   <label>使用已保存 Agent 身份<select aria-label="助手 Agent 身份" value={selection.identity?.id||''}
    onChange={event=>{const row=identities.find(item=>item.id===event.target.value);
     onChange({...selection,identity:row?{id:row.id,revision:row.revision}:null})}}>
    <option value="">助手默认角色</option>
    {selection.identity&&!identity&&<option value={selection.identity.id}>已保存身份版本已变化</option>}
    {identities.map(row=><option key={row.id} value={row.id}>{row.name} · {row.role}</option>)}
   </select></label>
   <p className="field-note">身份只提供角色、背景和提示词。助手使用独立的无工具权限。</p>
   {identity&&<button disabled={!useAgentModel} onClick={()=>onChange({...selection,model:identity.model||null,effort:identity.effort||null})}>使用此 Agent 的模型设置</button>}
   {identity&&!useAgentModel&&<p className="field-note">当前 Provider、原生配置或模型与此 Agent 的设置不兼容。</p>}
   <div>
    <button onClick={onRefresh}>刷新模型目录</button>
    <button disabled={!canSend} onClick={onTest}>测试真实推理</button>
    <button className="primary" disabled={!canSend||(selection.identity&&!identity)} onClick={onSave}>保存助手偏好</button>
   </div>
  </fieldset>
 </section>;
}
