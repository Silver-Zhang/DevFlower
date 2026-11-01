// Only used when creating a node. Existing drafts keep their explicit timeout.
export function profileTimeout(profile){
 const value=profile?.timeout_seconds;
 return Number.isInteger(value)?Math.min(3600,Math.max(10,value)):300;
}

// A new Codex agent starts at the low-cost Luna/low choice; a node that already
// carries an explicit model/effort (templates, saved drafts) is never rewritten.
export const CODEX_NEW_AGENT={model:'gpt-6-luna',effort:'low'};
export function newAgentDefaults(profile){
 const codex=profile?.provider==='codex';
 return {model:codex?CODEX_NEW_AGENT.model:profile?.model,effort:codex?CODEX_NEW_AGENT.effort:profile?.effort};
}
