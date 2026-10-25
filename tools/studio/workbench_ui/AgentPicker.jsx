import React from 'react';
const name=p=>p.provider==='claude'?'Claude Code':p.provider==='codex'?'Codex':p.provider;
export function AgentPicker({profiles,value,onChange,disabled=false}){
 const same=p=>profiles.filter(x=>x.provider===p.provider).length>1;
 return <label className="field agent-picker"><span>Agent</span><select aria-label="Agent 工具" value={value||''} disabled={disabled} onChange={e=>onChange(e.target.value)}>
  {profiles.map(p=><option key={p.id} value={p.id} disabled={!p.available}>{name(p)}{same(p)?' · '+(p.name||p.id):''}{p.available?'':'（暂不可用）'}</option>)}
 </select></label>;
}
